"""Audit log integrity, health, security headers, CSRF, host and size limits, logging, jobs."""

import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app import jobs
from app.db import SessionLocal
from app.logs import JsonFormatter, redact
from app.main import app
from app.models import AuditEvent, Job
from tests.conftest import API, owner_engine


def test_the_app_login_cannot_change_or_delete_audit_events(admin):
    with SessionLocal() as db:
        for stmt in ("UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events",
                     "TRUNCATE audit_events"):  # fmt: skip
            with pytest.raises(DBAPIError, match="permission denied"):
                db.execute(text(stmt))
            db.rollback()


def test_not_even_the_schema_owner_can_rewrite_audit_events(admin):
    with owner_engine.connect() as conn:
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text("UPDATE audit_events SET action = 'x'"))
        conn.rollback()
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text("TRUNCATE audit_events"))


def test_audit_log_filters_and_export(admin):
    admin.post(f"{API}/roles", json={"name": '=HYPERLINK("http://evil")'})
    r = admin.get(f"{API}/audit", params={"action": "role."}).json()
    assert r["total"] == 1 and r["items"][0]["ip"]
    csv = admin.get(f"{API}/audit/export")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "'=HYPERLINK" in csv.text  # neutralised: Excel shows it as text, never runs it
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).where(AuditEvent.action == "audit.export")) == 1


def test_health(client):
    assert client.get(f"{API}/health/live").json()["status"] == "ok"
    r = client.get(f"{API}/health/ready")
    assert r.status_code == 200 and r.json()["checks"] == {"database": "ok", "migrations": "ok"}


def test_system_status_needs_permission(admin, user_with):
    assert admin.get(f"{API}/system/status").json()["jobs"]["dead"] == 0
    assert user_with().get(f"{API}/system/status").status_code == 403


def test_security_headers_and_request_id(client):
    r = client.get(f"{API}/health/live")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert len(r.headers["x-request-id"]) == 12


def test_writes_need_the_csrf_token(admin):
    no_token = TestClient(app)
    no_token.cookies = admin.cookies
    assert no_token.post(f"{API}/auth/logout").status_code == 403
    assert no_token.post(f"{API}/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_unknown_hosts_are_refused():
    assert (
        TestClient(app, base_url="http://evil.example").get(f"{API}/health/live").status_code == 400
    )


def test_oversized_bodies_are_refused_even_when_chunked(admin):
    big = b'{"name": "' + b"x" * (2 * 2**20) + b'"}'
    assert (
        admin.post(
            f"{API}/roles", content=big, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )

    def chunks():
        for _ in range(3):
            yield b"x" * 2**20

    assert admin.post(f"{API}/roles", content=chunks()).status_code == 413


def test_crashes_answer_calmly_without_internals(admin, monkeypatch):
    import app.admin as admin_module

    def boom(*a, **k):
        raise RuntimeError("secret internals at /srv/x")

    monkeypatch.setattr(admin_module, "_role_rows", boom)
    r = TestClient(app, raise_server_exceptions=False, cookies=admin.cookies).get(f"{API}/roles")
    assert r.status_code == 500 and "secret" not in r.text and "ref" in r.text


def test_logs_redact_secrets_and_personal_data():
    assert "hunter2" not in redact("login password=hunter2 ok")
    assert "[email]" in redact("mail ravi@example.test failed")
    assert "[phone]" in redact("call +971 50 123 4567")
    assert "s3cret" not in redact("postgresql+psycopg://smet_app:s3cret@127.0.0.1/smet")
    rec = logging.LogRecord("x", logging.ERROR, "", 0, "token=abc123", None, None)
    assert "abc123" not in JsonFormatter().format(rec)


def test_jobs_retry_with_backoff_then_dead_letter():
    calls = []

    @jobs.handler("test.flaky")
    def flaky(db, payload):
        calls.append(payload["n"])
        raise ValueError("provider down")

    with SessionLocal() as db:
        jobs.enqueue(db, "test.flaky", {"n": 1}, key="flaky-1", max_attempts=2)
        jobs.enqueue(db, "test.flaky", {"n": 1}, key="flaky-1")  # same key: added once
        db.commit()
    assert jobs.run_due_jobs() == 1
    with SessionLocal() as db:
        job = db.scalar(select(Job))
        assert job.status == "pending" and job.attempts == 1 and "provider down" in job.last_error
        job.run_at = job.created_at  # fast-forward the backoff
        db.commit()
    jobs.run_due_jobs()
    with SessionLocal() as db:
        assert db.scalar(select(Job.status)) == "dead"
    assert calls == [1, 1]


def test_parallel_workers_never_run_a_job_twice():
    ran = []

    @jobs.handler("test.count")
    def count(db, payload):
        ran.append(payload["i"])

    with SessionLocal() as db:
        for i in range(40):
            jobs.enqueue(db, "test.count", {"i": i})
        db.commit()
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: jobs.run_due_jobs(), range(4)))
    assert sorted(ran) == list(range(40))


def test_database_statements_time_out():
    with SessionLocal() as db:
        assert db.scalar(text("SHOW statement_timeout")) == "30s"


def test_periodic_purge_removes_expired_sessions_and_old_jobs(admin):
    from datetime import timedelta

    from app.models import AuthSession, utcnow

    with SessionLocal() as db:
        db.query(AuthSession).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.add(Job(kind="x", status="done", created_at=utcnow() - timedelta(days=8)))
        db.add(Job(kind="x", status="dead", created_at=utcnow() - timedelta(days=8)))
        db.commit()
        jobs.purge_expired(db)
        assert db.scalar(select(func.count()).select_from(AuthSession)) == 0
        assert list(db.scalars(select(Job.status))) == ["dead"]  # failed jobs stay for review


def test_no_cross_origin_access_is_granted(client):
    r = client.get(f"{API}/health/live", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_injection_payloads_are_just_text(admin):
    for payload in ("' OR 1=1 --", "%'; DROP TABLE users; --", "\\x00"):
        r = admin.get(f"{API}/users", params={"q": payload})
        assert r.status_code == 200 and r.json()["total"] == 0
        assert admin.get(f"{API}/audit", params={"q": payload}).status_code == 200
    assert admin.get(f"{API}/users").json()["total"] == 1  # the table is still there


def test_roles_list_runs_a_fixed_number_of_queries(admin):
    from sqlalchemy import event

    from app.db import engine

    count = []
    listener = lambda *a: count.append(1)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        assert admin.get(f"{API}/roles").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert len(count) <= 8  # session, user, access, roles, permissions, counts: not one per role


def test_each_sign_in_issues_a_new_session_token(admin):
    from app.auth import SESSION_COOKIE
    from tests.conftest import ADMIN, signed_in

    first = admin.cookies.get(SESSION_COOKIE)
    second = signed_in(ADMIN["email"]).cookies.get(SESSION_COOKIE)
    assert first and second and first != second
