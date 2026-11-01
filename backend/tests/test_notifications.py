"""Phase 7 notifications and messages: who hears about what, and that nothing leaves the server
until it is switched on, configured and allowed."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select, update

from app import messaging
from app.config import get_settings
from app.db import SessionLocal
from app.jobs import run_due_jobs
from app.messaging import mask, normalise
from app.models import AuditEvent, MessageDelivery
from app.notify import check_due
from tests.conftest import API, new_client
from tests.ops import approved_po, item, ok, supplier

BUYER = [
    "purchasing.view",
    "purchasing.create",
    "items.view",
    "items.edit",
    "parties.view",
    "parties.edit",
]
SERVICE = ["service.view", "service.create", "stock.view"]


class FakeHttp:
    """Stands in for the network: records each call and answers from a list."""

    def __init__(self, *answers):
        self.calls, self.answers = [], list(answers)

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.answers.pop(0) if self.answers else (200, {})


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*a, **kw):
        raise AssertionError("a provider was called")

    monkeypatch.setattr(messaging, "HTTP", refuse)


def public_sr(phone, updates=True):
    """The customer's own request, from the public form (no battery needed)."""
    ok(new_client().post(f"{API}/public/service-requests", json={"name": "Ali", "phone": phone, "updates": updates,
                         "issue": "Battery alarm keeps beeping"}), 202)  # fmt: skip


def inbox(c, **q):
    return ok(c.get(f"{API}/notifications", params=q))


def deliveries():
    with SessionLocal() as db:
        return db.scalars(select(MessageDelivery).order_by(MessageDelivery.created_at)).all()


def channel(admin, ch, **body):
    cur = next(x for x in ok(admin.get(f"{API}/messages/providers"))["items"] if x["channel"] == ch)
    return ok(admin.put(f"{API}/messages/providers/{ch}", json={"version": cur["version"],
              "provider": cur["provider"]} | body))  # fmt: skip


TWILIO = {"provider": "twilio", "settings": {"account_sid": "AC" + "0" * 32, "from_number": "+15005550006"},
          "secret": "not-a-real-token"}  # fmt: skip


def test_an_approval_request_reaches_the_approvers_and_the_decision_reaches_the_buyer(user_with):
    buyer, approver = (
        user_with(perms=BUYER),
        user_with(perms=["purchasing.view", "purchasing.approve"]),
    )
    bystander = user_with(perms=["purchasing.view"])
    po = ok(buyer.post(f"{API}/purchase-orders", json={"supplier_id": supplier(buyer)["id"], "currency": "AED",
            "lines": [{"item_id": item(buyer, "CELL")["id"], "qty": 4, "unit_price": 10}]}), 201)  # fmt: skip
    po = ok(buyer.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    assert inbox(approver)["total"] == 0  # nothing until the worker has run, after the commit
    run_due_jobs()
    (n,) = inbox(approver)["items"]
    assert (
        n["title"] == f"{po['number']} waits for your approval"
        and n["link"] == f"/purchasing/orders/{po['id']}"
    )
    assert (
        inbox(bystander)["total"] == inbox(buyer)["total"] == 0
    )  # not the doer, not without the permission
    ok(approver.post(f"{API}/purchase-orders/{po['id']}/approve", json={"version": po["version"]}))
    run_due_jobs()
    mine = inbox(buyer, unread=True)
    assert mine["unread"] == 1 and mine["items"][0]["event"] == "approval.decided"
    assert ok(buyer.post(f"{API}/notifications/read", json={"all": True}))["unread"] == 0
    # Someone else's notification can't be marked read by this person.
    other = inbox(approver)["items"][0]["id"]
    ok(buyer.post(f"{API}/notifications/read", json={"ids": [other]}))
    assert inbox(approver)["unread"] == 1


def test_muted_events_stay_quiet(user_with):
    buyer, approver = (
        user_with(perms=BUYER),
        user_with(perms=["purchasing.view", "purchasing.approve"]),
    )
    prefs = ok(approver.get(f"{API}/notifications/preferences"))
    assert {e["key"] for e in prefs["events"]} >= {
        "approval.requested",
        "stock.low",
        "service.status",
    }
    ok(approver.put(f"{API}/notifications/preferences", json={"notify_email": False, "muted_events": ["approval.requested", "nonsense"]}))  # fmt: skip
    assert ok(approver.get(f"{API}/notifications/preferences"))["muted_events"] == [
        "approval.requested"
    ]
    approved_po(buyer, approver, supplier(buyer), [(item(buyer, "CELL"), 1, 1)])
    run_due_jobs()
    assert [n["event"] for n in inbox(approver)["items"]] == []


def test_nothing_leaves_the_server_until_switched_on_and_allowed(
    admin, user_with, monkeypatch, no_network
):
    coord = user_with(perms=SERVICE)
    # Off: nothing is queued at all.
    public_sr("050 123 4567")
    run_due_jobs()
    assert deliveries() == []
    sr = ok(coord.get(f"{API}/service-requests"))["items"][0]
    # Production on the screen, but the server allows nothing: recorded as blocked, never sent.
    channel(admin, "sms", mode="production", **TWILIO)
    shown = ok(admin.get(f"{API}/messages/providers"))
    sms = next(x for x in shown["items"] if x["channel"] == "sms")
    assert (
        shown["external_sends"] == "off"
        and sms["secret_set"]
        and "not-a-real-token" not in str(shown)
    )
    assert "EXTERNAL_SENDS" in sms["blocked"]
    sr = ok(coord.post(f"{API}/service-requests/{sr['id']}/status", json={"version": sr["version"], "to": "under_review"}))  # fmt: skip
    run_due_jobs()
    (d,) = deliveries()
    assert (d.status, d.recipient, d.channel) == ("blocked", "+971501234567", "sms")
    assert d.body == "Demo Trading LLC: your service request {} is now under review.".format(
        sr["number"]
    )
    log = ok(admin.get(f"{API}/messages/deliveries"))["items"][0]
    assert log["to"] == "+9715••••••67" and "501234567" not in str(log)
    with SessionLocal() as db:  # the credential change is audited without the credential
        e = db.scalar(select(AuditEvent).where(AuditEvent.action == "messages.provider"))
        assert e.changes["secret"] == ["[hidden]", "[hidden]"] and "not-a-real-token" not in str(
            e.changes
        )
    # Allowed on the server: the retry goes to Twilio once, and its status is followed up.
    monkeypatch.setattr(get_settings(), "external_sends", "production")
    fake = FakeHttp(
        (201, {"sid": "SM" + "a" * 32, "status": "queued"}), (200, {"status": "delivered"})
    )
    monkeypatch.setattr(messaging, "HTTP", fake)
    ok(admin.post(f"{API}/messages/deliveries/{d.id}/retry"))
    run_due_jobs()
    ((method, url, kw),) = fake.calls
    assert method == "POST" and url.endswith(f"/Accounts/AC{'0' * 32}/Messages.json")
    assert kw["form"] == {"To": "+971501234567", "From": "+15005550006", "Body": d.body}
    with SessionLocal() as db:
        messaging.refresh_status(db)
    (d,) = deliveries()
    assert (d.status, d.provider_ref, d.attempts) == ("delivered", "SM" + "a" * 32, 1)


def test_a_message_is_never_sent_twice(admin, user_with, monkeypatch):
    user_with(perms=SERVICE)
    monkeypatch.setattr(get_settings(), "external_sends", "production")
    channel(admin, "sms", mode="production", **TWILIO)
    fake = FakeHttp((503, {}))
    monkeypatch.setattr(messaging, "HTTP", fake)
    public_sr("+971 50 123 4567")
    run_due_jobs()
    (d,) = deliveries()  # the provider was down: queued again for later, with the reason
    assert (d.status, d.attempts, len(fake.calls)) == ("queued", 1, 1) and "503" in d.error
    with SessionLocal() as db:  # as if a worker died during the provider call
        db.execute(update(MessageDelivery).values(status="sending"))
        db.commit()
        messaging.send_job(db, {"id": str(d.id)})
    (d,) = deliveries()
    assert d.status == "unknown" and len(fake.calls) == 1
    # The same event can't queue the same message again (same key).
    with SessionLocal() as db:
        for _ in range(2):
            messaging.queue(
                db, event="service.status", channel="sms", to="0501234567", key="same", values={}
            )
        db.commit()
    assert len(deliveries()) == 2


def test_sandbox_sends_only_to_the_test_recipient(admin, user_with, monkeypatch):
    user_with(perms=SERVICE)
    monkeypatch.setattr(get_settings(), "external_sends", "sandbox")
    wa = {"provider": "meta_cloud", "settings": {"phone_number_id": "1234567890", "api_version": "v21.0"},
          "secret": "not-a-real-token"}  # fmt: skip
    r = admin.put(f"{API}/messages/providers/whatsapp", json={"version": 0, "mode": "sandbox"} | wa)
    assert r.status_code == 422 and "test_recipient" in r.text
    channel(admin, "whatsapp", mode="sandbox", test_recipient="+971 55 000 0000", **wa)
    t = next(x for x in ok(admin.get(f"{API}/messages/templates"))["items"]
             if (x["event"], x["channel"]) == ("service.status", "whatsapp"))  # fmt: skip
    ok(admin.put(f"{API}/messages/templates/service.status/whatsapp",
                 json={"version": t["version"], "body": t["body"], "provider_template": "sr_status", "language": "en"}))  # fmt: skip
    fake = FakeHttp((200, {"messages": [{"id": "wamid.1"}]}))
    monkeypatch.setattr(messaging, "HTTP", fake)
    public_sr("0501234567")
    run_due_jobs()
    ((_, url, kw),) = fake.calls
    assert url == "https://graph.facebook.com/v21.0/1234567890/messages"
    assert kw["body"]["to"] == "971550000000" and kw["body"]["template"]["name"] == "sr_status"
    params = [p["text"] for p in kw["body"]["template"]["components"][0]["parameters"]]
    assert (
        params[0] == "Demo Trading LLC" and params[2] == "received"
    )  # {company} {number} {status}
    (d,) = deliveries()
    assert (d.recipient, d.sent_to, d.status) == ("+971501234567", "+971550000000", "sent")


def test_no_consent_or_an_opt_out_means_no_message(admin, user_with, no_network):
    user_with(perms=SERVICE)
    channel(admin, "sms", mode="mock")
    public_sr("0501234567", updates=False)
    run_due_jobs()
    assert deliveries() == []  # no consent asked for: nothing
    ok(admin.post(f"{API}/messages/opt-outs", json={"channel": "sms", "address": "+971501234567", "reason": "Asked by phone"}), 201)  # fmt: skip
    public_sr("0501234567")
    run_due_jobs()
    (d,) = deliveries()
    assert d.status == "skipped" and "opted out" in d.error
    r = admin.post(f"{API}/messages/deliveries/{d.id}/retry")
    assert r.status_code == 409


def test_staff_email_in_mock_mode_is_recorded_and_never_sent(admin, user_with, no_network):
    buyer, approver = (
        user_with(perms=BUYER),
        user_with(perms=["purchasing.view", "purchasing.approve"]),
    )
    ok(
        approver.put(
            f"{API}/notifications/preferences", json={"notify_email": True, "muted_events": []}
        )
    )
    channel(admin, "email", provider="smtp", mode="mock")
    assert ok(admin.post(f"{API}/messages/providers/email/test"))["ok"]
    po = ok(buyer.post(f"{API}/purchase-orders", json={"supplier_id": supplier(buyer)["id"], "currency": "AED",
            "lines": [{"item_id": item(buyer, "CELL")["id"], "qty": 4, "unit_price": 10}]}), 201)  # fmt: skip
    ok(buyer.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    run_due_jobs()
    (d,) = deliveries()
    assert (d.channel, d.mode, d.status) == ("email", "mock", "delivered")
    assert (
        d.subject == f"{po['number']} waits for your approval"
        and f"/purchasing/orders/{po['id']}" in d.body
    )


def test_smtp_sends_one_message_with_its_subject(monkeypatch):
    sent = []

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("mail.example.test", 587)

        def starttls(self, context):
            pass

        def login(self, user, password):
            assert (user, password) == ("smet", "pw")

        def send_message(self, msg):
            sent.append(msg)

        def quit(self):
            pass

    monkeypatch.setattr(messaging.smtplib, "SMTP", FakeSmtp)
    s = messaging.Smtp({"host": "mail.example.test", "port": "587", "security": "starttls", "username": "smet",
                        "from_address": "noreply@example.test"}, "pw")  # fmt: skip
    ref = s.send(messaging.Outgoing("a@example.test", "Hello", "Body", [], "", "en"))
    (m,) = sent
    assert (m["To"], m["Subject"], m["Message-ID"]) == ("a@example.test", "Hello", ref)


def test_wording_is_checked_before_it_is_saved(admin, user_with):
    t = lambda **kw: admin.put(
        f"{API}/messages/templates/stock.low/in_app",
        json={"version": 0, "subject": "{item} low", "body": "x"} | kw,
    )  # noqa: E731
    assert "Unknown field {price}" in t(body="Costs {price}").text
    assert "Braces only" in t(body="Odd { brace").text
    p = ok(admin.post(f"{API}/messages/templates/preview", json={"event": "stock.low", "channel": "sms", "body": "Low: {item} {nope}"}))  # fmt: skip
    assert p["body"].startswith("Low: 12V 100Ah battery") and "nope" in p["errors"]["body"]
    ok(t(subject="Order more {item}", body="Only {free} left"))
    buyer = user_with(perms=BUYER)
    it = item(buyer, "CELL", min_stock=5)
    with SessionLocal() as db:
        check_due(db)
    (n,) = inbox(buyer)["items"]
    assert (n["title"], n["body"]) == (f"Order more {it['code']} {it['name']}", "Only 0 left")
    viewer = user_with(perms=["notifications.view"])
    assert viewer.put(f"{API}/messages/templates/stock.low/in_app", json={"version": 1, "subject": "a", "body": "b"}).status_code == 403  # fmt: skip


def test_reminders_repeat_on_schedule_and_escalate(admin, user_with):
    buyer, approver = (
        user_with(perms=BUYER),
        user_with(perms=["purchasing.view", "purchasing.approve"]),
    )
    boss = user_with("operations_manager")
    po = ok(buyer.post(f"{API}/purchase-orders", json={"supplier_id": supplier(buyer)["id"], "currency": "AED",
            "expected_arrival": str(date.today() - timedelta(days=2)),
            "lines": [{"item_id": item(buyer, "CELL")["id"], "qty": 4, "unit_price": 10}]}), 201)  # fmt: skip
    po = ok(buyer.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    ok(approver.post(f"{API}/purchase-orders/{po['id']}/approve", json={"version": po["version"]}))
    roles = ok(admin.get(f"{API}/roles"))["items"]
    ops = next(r for r in roles if r["name"] == "Management / Operations Manager")
    rules = ok(admin.get(f"{API}/rules"))
    ok(admin.patch(f"{API}/rules", json={"version": rules["version"], "escalate_after_days": 5, "escalation_role_id": ops["id"]}))  # fmt: skip

    def late(day):
        with SessionLocal() as db:
            check_due(db, day)
        return [n for n in inbox(buyer)["items"] if n["event"] == "procurement.delayed"]

    today = date.today()
    assert len(late(today)) == 1 and len(late(today)) == 1  # once per period, however often it runs
    assert not [
        n for n in inbox(boss)["items"] if n["event"] == "procurement.delayed"
    ]  # 2 days: not yet
    assert len(late(today + timedelta(days=7))) == 2  # still late a week on: reminded again
    assert [n["event"] for n in inbox(boss)["items"]].count(
        "procurement.delayed"
    ) == 1  # 9 days: escalated


def test_addresses_are_normalised_and_masked():
    assert normalise("sms", "050 123 4567", "971") == "+971501234567"
    assert normalise("sms", "00971501234567", "971") == "+971501234567"
    assert normalise("sms", "501234567", "971") == "+971501234567"
    assert (
        normalise("sms", "ali@example.test", "971") is None
        and normalise("sms", "12", "971") is None
    )
    assert normalise("email", " Ali@Example.TEST ", "971") == "ali@example.test"
    assert (
        mask("ali@example.test") == "a•••@example.test" and mask("+971501234567") == "+9715••••••67"
    )
