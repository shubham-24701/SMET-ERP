from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import select, update

from app.auth import SESSION_COOKIE
from app.db import SessionLocal
from app.models import AuditEvent, AuthSession, User, utcnow
from tests.conftest import ADMIN, API, PASSWORD, new_client, signed_in


def test_first_run_setup_creates_the_admin_and_signs_in(client):
    assert client.get(f"{API}/auth/setup-status").json() == {"needs_setup": True}
    me = client.post(f"{API}/auth/setup", json=ADMIN).json()
    assert me["roles"] == ["System Administrator"]
    assert "users.edit" in me["permissions"] and me["company"]["timezone"] == "Asia/Dubai"
    assert client.get(f"{API}/auth/me").status_code == 200
    assert client.get(f"{API}/auth/setup-status").json() == {"needs_setup": False}


def test_setup_runs_once_even_when_two_arrive_together(client):
    def attempt(i):
        c = new_client()
        return c.post(
            f"{API}/auth/setup", json={**ADMIN, "email": f"a{i}@example.test"}
        ).status_code

    with ThreadPoolExecutor(4) as pool:
        codes = sorted(pool.map(attempt, range(4)))
    assert codes == [201, 409, 409, 409]


def test_weak_passwords_are_refused(client):
    r = client.post(f"{API}/auth/setup", json={**ADMIN, "password": "short"})
    assert r.status_code == 422
    r = client.post(f"{API}/auth/setup", json={**ADMIN, "password": "aaaaaaaaaaaaaaaa"})
    assert r.status_code == 422


def test_wrong_password_is_generic_and_audited(admin):
    c = new_client()
    unknown = c.post(
        f"{API}/auth/login", json={"email": "nobody@example.test", "password": "x" * 12}
    )
    wrong = c.post(f"{API}/auth/login", json={"email": ADMIN["email"], "password": "x" * 12})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()  # no hint whether the account exists
    with SessionLocal() as db:
        assert db.scalar(select(AuditEvent).where(AuditEvent.action == "auth.login_failed"))


def test_login_is_rate_limited_per_account(admin):
    c = new_client()
    codes = [c.post(f"{API}/auth/login", json={"email": ADMIN["email"], "password": "wrong-password"}).status_code
             for _ in range(6)]  # fmt: skip
    assert codes == [401] * 5 + [429]
    # Even the right password waits now: guessing can't continue in the same minute.
    assert (
        c.post(
            f"{API}/auth/login", json={"email": ADMIN["email"], "password": PASSWORD}
        ).status_code
        == 429
    )


def test_forged_and_missing_sessions_are_rejected(admin):
    assert new_client().get(f"{API}/auth/me").status_code == 401
    forged = new_client()
    forged.cookies.set(SESSION_COOKIE, "made-up")
    assert forged.get(f"{API}/auth/me").status_code == 401


def test_logout_ends_the_session(admin):
    assert admin.post(f"{API}/auth/logout").status_code == 204
    assert admin.get(f"{API}/auth/me").status_code == 401


def test_idle_and_expired_sessions_end(admin):
    with SessionLocal() as db:
        db.execute(update(AuthSession).values(last_seen_at=utcnow() - timedelta(minutes=61)))
        db.commit()
    assert admin.get(f"{API}/auth/me").status_code == 401
    c = signed_in(ADMIN["email"])
    with SessionLocal() as db:
        db.execute(update(AuthSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert c.get(f"{API}/auth/me").status_code == 401


def test_new_user_must_change_the_temporary_password_first(admin):
    made = admin.post(
        f"{API}/users", json={"name": "Asha", "email": "asha@example.test", "role_ids": []}
    ).json()
    asha = signed_in("asha@example.test", made["temporary_password"])
    assert asha.get(f"{API}/auth/me").json()["user"]["must_change_password"] is True
    assert asha.get(f"{API}/users").status_code == 403  # nothing else until the password changes
    r = asha.post(f"{API}/auth/password", json={"current_password": made["temporary_password"],
                                                "new_password": "a-new-long-password"})  # fmt: skip
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is False


def test_changing_password_signs_out_other_sessions(admin):
    other = signed_in(ADMIN["email"])
    r = admin.post(
        f"{API}/auth/password",
        json={"current_password": PASSWORD, "new_password": "another-long-password"},
    )
    assert r.status_code == 200
    assert admin.get(f"{API}/auth/me").status_code == 200  # this session stays
    assert other.get(f"{API}/auth/me").status_code == 401
    bad = admin.post(
        f"{API}/auth/password", json={"current_password": "nope", "new_password": "x1y2z3w4v5u6t7"}
    )
    assert bad.status_code == 422 and "current_password" in bad.text


def test_deactivated_user_is_signed_out_and_cannot_sign_in(admin, user_with):
    bob = user_with(email="bob@example.test")
    uid = bob.get(f"{API}/auth/me").json()["user"]["id"]
    v = admin.get(f"{API}/users", params={"q": "bob"}).json()["items"][0]["version"]
    assert (
        admin.patch(f"{API}/users/{uid}", json={"version": v, "active": False}).status_code == 200
    )
    assert bob.get(f"{API}/auth/me").status_code == 401
    r = new_client().post(
        f"{API}/auth/login", json={"email": "bob@example.test", "password": PASSWORD}
    )
    assert r.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(User.active).where(User.email == "bob@example.test")) is False
