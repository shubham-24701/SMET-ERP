"""Regressions for the Phase 7 independent review: each test fails without its fix."""

import csv
import io
import smtplib
from decimal import Decimal
from urllib.error import URLError

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text

from app import messaging, reports
from app.config import get_settings
from app.db import SessionLocal
from app.jobs import run_due_jobs
from app.models import AuditEvent
from tests import test_installation
from tests.conftest import API
from tests.ops import approved_po, item, ok, supplier
from tests.test_notifications import (
    BUYER,
    SERVICE,
    TWILIO,
    FakeHttp,
    channel,
    deliveries,
    inbox,
    moved,
    public_sr,
)

site = test_installation.site


def test_every_report_downloads_in_both_formats(site):
    """1: Excel refused time-zoned datetimes (stock movements, inspections): a 500."""
    exporter = site["pm"]
    for name in reports.REPORTS:
        for fmt in ("csv", "xlsx"):
            r = exporter.get(f"{API}/reports/{name}/export", params={"format": fmt})
            assert r.status_code == 200, (name, fmt, r.text)
    x = exporter.get(f"{API}/reports/stock_moves/export", params={"format": "xlsx"})
    rows = list(load_workbook(io.BytesIO(x.content)).active.values)
    assert len(rows) > 1 and rows[1][0].tzinfo is None


def test_numbers_stay_numbers_and_text_cant_become_a_formula(admin, monkeypatch):
    """7: CSV turned negative numbers into text ('-2)."""
    fake = reports.Report("Fake", "inventory", "audit.view", (("x", "X"), ("y", "Y")), (),
                          lambda db, f: [{"x": Decimal("-2"), "y": "=cmd()"}])  # fmt: skip
    monkeypatch.setitem(reports.REPORTS, "fake", fake)
    monkeypatch.setattr(reports, "EXPORT_LIMIT", 10)
    with SessionLocal() as db:  # admin holds audit.view; downloads need reports.export too
        db.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission) SELECT role_id, 'reports.export' FROM user_roles"
            )
        )
        db.commit()
    r = admin.get(f"{API}/reports/fake/export")
    assert list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))[1] == ["-2", "'=cmd()"]


def test_export_refuses_too_many_rows_before_loading_them(admin, monkeypatch):
    """6: every row was loaded just to answer 'too many'."""
    loaded = []
    monkeypatch.setitem(reports.REPORTS, "fake", reports.Report("Fake", "inventory", "audit.view", (("x", "X"),), (),
                        lambda db, f: loaded.append(1) or [{"x": 1}] * 3))  # fmt: skip
    monkeypatch.setattr(reports, "EXPORT_LIMIT", 2)
    monkeypatch.setattr(reports, "run", lambda *a, **k: pytest.fail("rows loaded"))
    with SessionLocal() as db:
        db.execute(
            text(
                "INSERT INTO role_permissions (role_id, permission) SELECT role_id, 'reports.export' FROM user_roles"
            )
        )
        db.commit()
    assert admin.get(f"{API}/reports/fake/export").status_code == 409


def test_the_stored_password_never_follows_a_new_server(admin, monkeypatch):
    """2: changing the host kept the stored password, and the test sent it there."""
    seen = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            seen.append(host)

        def starttls(self, context):
            pass

        def login(self, u, pw):
            seen.append(pw)

        def noop(self):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(messaging.smtplib, "SMTP", FakeSmtp)
    good = {
        "host": "mail.example.test",
        "port": "587",
        "security": "starttls",
        "username": "smet",
        "from_address": "a@example.test",
    }
    channel(admin, "email", provider="smtp", mode="production", settings=good, secret="s3cret")
    r = admin.put(f"{API}/messages/providers/email", json={"version": 1, "provider": "smtp", "mode": "production",
                  "settings": good | {"host": "127.0.0.1", "port": "2525"}})  # fmt: skip
    assert r.status_code == 422 and "again" in r.text
    r = admin.put(f"{API}/messages/providers/email", json={"version": 1, "provider": "smtp", "mode": "production",
                  "settings": good | {"security": "none"}, "secret": "s3cret"})  # fmt: skip
    assert r.status_code == 422 and "unencrypted" in r.text
    # With the server's cap off, a connection test contacts nobody.
    assert admin.post(f"{API}/messages/providers/email/test").status_code == 409 and seen == []
    monkeypatch.setattr(get_settings(), "external_sends", "production")
    assert ok(admin.post(f"{API}/messages/providers/email/test"))["ok"] and seen == [
        "mail.example.test",
        "s3cret",
    ]


def test_a_send_that_may_have_arrived_is_never_repeated(admin, user_with, monkeypatch):
    """3: a timeout after the request went out was retried, so the customer could get it twice."""

    def timeout(*a, **k):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(messaging, "urlopen", timeout)
    with pytest.raises(messaging.Unsure):
        messaging.http("POST", "https://api.twilio.com/x")

    def refused(*a, **k):
        raise URLError(ConnectionRefusedError())

    monkeypatch.setattr(messaging, "urlopen", refused)
    with pytest.raises(messaging.Retry):
        messaging.http("POST", "https://api.twilio.com/x")
    # An accepted request with no reference: unknown, not failed (and not offered as a plain retry).
    coord = user_with(perms=SERVICE)
    monkeypatch.setattr(get_settings(), "external_sends", "production")
    channel(admin, "sms", mode="production", **TWILIO)
    monkeypatch.setattr(messaging, "HTTP", FakeHttp((201, {"status": "queued"})))
    public_sr("0501234567")
    moved(coord)
    (d,) = deliveries()
    assert d.status == "unknown"
    # SMTP cut off while sending: the same.
    s = messaging.Smtp(
        {
            "host": "h.example.test",
            "port": "25",
            "security": "none",
            "username": "",
            "from_address": "a@example.test",
        },
        "",
    )

    class Drops:
        def __init__(self, *a, **k):
            pass

        def send_message(self, m):
            raise smtplib.SMTPServerDisconnected("gone")

        def quit(self):
            pass

    monkeypatch.setattr(messaging.smtplib, "SMTP", Drops)
    with pytest.raises(messaging.Unsure):
        s.send(messaging.Outgoing("b@example.test", "S", "B", [], "", "en"))


def test_the_public_form_cant_text_numbers_abroad_or_on_opening(admin, user_with):
    """4: anyone could make SMET text any number, at SMET's cost."""
    coord = user_with(perms=SERVICE)
    channel(admin, "sms", mode="mock")
    public_sr("0501234567")
    run_due_jobs()
    assert deliveries() == []  # opening on the public form: nothing sent
    public_sr("+44 7700 900123")
    moved(coord)
    assert deliveries() == []  # not a number in SMET's country
    moved(coord, "scheduled")
    sr = ok(coord.get(f"{API}/service-requests"))["items"][1]
    ok(
        coord.post(
            f"{API}/service-requests/{sr['id']}/status",
            json={"version": sr["version"], "to": "under_review"},
        )
    )
    run_due_jobs()
    assert [d.recipient for d in deliveries()] == ["+971501234567"]


def test_status_checks_stop_when_the_server_cap_is_off(admin, user_with, monkeypatch):
    """5: the worker kept calling Twilio after sends were switched off."""
    coord = user_with(perms=SERVICE)
    monkeypatch.setattr(get_settings(), "external_sends", "production")
    channel(admin, "sms", mode="production", **TWILIO)
    monkeypatch.setattr(messaging, "HTTP", FakeHttp((201, {"sid": "SM" + "b" * 32})))
    public_sr("0501234567")
    moved(coord)
    monkeypatch.setattr(get_settings(), "external_sends", "off")
    monkeypatch.setattr(messaging, "HTTP", lambda *a, **k: pytest.fail("Twilio called"))
    with SessionLocal() as db:
        messaging.refresh_status(db)
    assert deliveries()[0].status == "sent"


def test_a_rejected_sheet_reaches_the_person_who_wrote_it(site, user_with):
    """10: only the project manager and creator heard about sheet and change decisions."""
    pm = site["pm"]
    writer = user_with(perms=["projects.view", "projects.create", "items.view"])
    eng = user_with(perms=["projects.view", "projects.approve"])
    pr = ok(pm.get(f"{API}/projects"))["items"][0]
    sheet = ok(writer.post(f"{API}/projects/{pr['id']}/sheets", json={"lines": [{"item_id": site["unit"]["id"], "qty": 2}]}), 201)  # fmt: skip
    sheet = ok(
        writer.post(f"{API}/sheets/{sheet['id']}/submit", json={"version": sheet["version"]})
    )
    ok(
        eng.post(
            f"{API}/sheets/{sheet['id']}/reject",
            json={"version": sheet["version"], "reason": "Say which racks"},
        )
    )
    run_due_jobs()
    assert [n["event"] for n in inbox(writer)["items"]] == ["approval.decided"]


def test_staff_email_keeps_the_link_and_customers_get_their_own_wording(admin, user_with):
    """11: staff copies of customer events had the customer wording and no link."""
    coord = user_with(perms=SERVICE)
    ok(
        coord.put(
            f"{API}/notifications/preferences", json={"notify_email": True, "muted_events": []}
        )
    )
    channel(admin, "email", provider="smtp", mode="mock")
    public_sr("ali@example.test")
    run_due_jobs()  # coord hears of the new request by email
    moved(coord)  # the customer, who asked for updates by email
    staff, customer = sorted(deliveries(), key=lambda d: d.user_id is None)
    assert "Open in SMET: http" in staff.body
    assert (
        customer.body.startswith("Demo Trading LLC: your service request")
        and "Open in SMET" not in customer.body
    )


def test_report_dates_are_smet_dates(user_with):
    """12: dates were UTC: an order made at 01:30 in Dubai on 1 Nov counted as 31 Oct."""
    buyer = user_with(perms=BUYER + ["reports.export"])
    po = approved_po(
        buyer,
        user_with(perms=["purchasing.view", "purchasing.approve"]),
        supplier(buyer),
        [(item(buyer, "CELL"), 1, 1)],
    )
    with SessionLocal() as db:
        db.execute(text("UPDATE purchase_orders SET created_at = '2026-10-31 21:30+00'"))
        db.commit()
    r = ok(
        buyer.get(
            f"{API}/reports/purchase_orders",
            params={"date_from": "2026-11-01", "date_to": "2026-11-01"},
        )
    )
    assert [x["number"] for x in r["items"]] == [po["number"]] and r["items"][0][
        "created"
    ] == "2026-11-01"


def test_wording_only_for_real_channels_and_opt_outs_checked_at_send(admin, user_with):
    """18 and 15: junk channels were stored; an opt-out after queueing didn't stop the send."""
    assert (
        admin.put(
            f"{API}/messages/templates/service.status/xyz", json={"version": 0, "body": "x"}
        ).status_code
        == 404
    )
    coord = user_with(perms=SERVICE)
    channel(admin, "sms", mode="mock")
    public_sr("0501234567")
    sr = ok(coord.get(f"{API}/service-requests"))["items"][0]
    ok(
        coord.post(
            f"{API}/service-requests/{sr['id']}/status",
            json={"version": sr["version"], "to": "under_review"},
        )
    )
    with SessionLocal() as db:  # build the message, then opt out before the worker sends it
        from app.notify import audit_job  # noqa: PLC0415

        job = db.execute(
            text(
                "SELECT payload FROM jobs WHERE kind = 'notify.audit' AND payload->>'action' = 'service.status'"
            )
        ).scalar()
        audit_job(db, job)
    ok(
        admin.post(f"{API}/messages/opt-outs", json={"channel": "sms", "address": "0501234567"}),
        201,
    )
    run_due_jobs()
    assert [d.status for d in deliveries()] == ["skipped"]


def test_the_sandbox_recipient_is_masked_in_the_audit_log(admin):
    """17: the audit log kept the test number in full."""
    channel(admin, "sms", mode="mock", test_recipient="+971 55 000 0000")
    with SessionLocal() as db:
        e = db.scalar(select(AuditEvent).where(AuditEvent.action == "messages.provider"))
    assert "550000000" not in str(e.changes) and e.changes["test_recipient"][1].startswith("+9715")
