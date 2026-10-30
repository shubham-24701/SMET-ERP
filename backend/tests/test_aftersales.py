"""Phase 6 warranty and after-sales, following the brief's scenarios 3 to 6: registration and
activation, maintenance and service with a reset code, battery replacement, and refused attempts."""

from datetime import date, timedelta

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import AuditEvent, MaintenanceAlert
from app.warranty import expiry_for
from tests import test_installation
from tests.conftest import API, new_client
from tests.ops import key, ok, stock_in, supplier
from tests.test_installation import JPEG, PDF, TODAY, bank, photo, visit

site = test_installation.site  # B1–B6 delivered to Tower A; pm (everything), boss, tech, other
T = date.today()


def policy(x, **kw):
    body = {"item_id": x["unit"]["id"], "months": 24, "start_event": "commissioning", "auto_approve": True,
            "requires_proof": True, "replacement_rule": "original_remaining", "effective_from": "2020-01-01"} | kw  # fmt: skip
    return ok(x["pm"].post(f"{API}/warranty-policies", json=body), 201)


def run(x, stage, b, **record):
    v = visit(x, stage, [b])
    v = ok(x["tech"].put(f"{API}/visits/{v['id']}/record", json={"version": v["version"]} | record))
    if stage == "installation":
        ok(photo(x["tech"], v), 201)
    return ok(
        x["tech"].post(
            f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
        )
    )


def handed_over(x, serials, name="Bank 1"):
    b = bank(x, name)
    b = ok(x["pm"].post(f"{API}/banks/{b['id']}/batteries", json={"version": b["version"],
           "batteries": [{"serial": s, "position": str(i)} for i, s in enumerate(serials, 1)]}))  # fmt: skip
    run(x, "installation", b, received_serials=serials)
    run(x, "commissioning", b)
    run(x, "handover", b, acknowledgement={"name": "Eng. Saeed", "on": TODAY})
    return ok(x["pm"].get(f"{API}/banks/{b['id']}"))


def regs(x, serial):
    return ok(x["pm"].get(f"{API}/warranty-registrations?q={serial}"))["items"]


def sid(x, serial):
    return ok(x["pm"].get(f"{API}/trace/scan?code={serial}"))["id"]


def test_expiry_dates_count_whole_months():
    assert expiry_for(date(2026, 3, 15), 12) == date(2027, 3, 14)
    assert expiry_for(date(2024, 2, 29), 12) == date(2025, 2, 28)
    assert expiry_for(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_smet_installation_activates_by_policy_at_handover_and_never_at_dispatch(site):
    pm = site["pm"]
    # No policy yet: handover opens a registration that waits, saying why.
    handed_over(site, ["B1"])
    (r,) = regs(site, "B1")
    r = ok(pm.get(f"{API}/warranty-registrations/{r['id']}"))
    assert r["status"] == "pending" and "No warranty policy for this model" in r["missing"][0]
    policy(site)
    r = ok(
        site["boss"].post(
            f"{API}/warranty-registrations/{r['id']}/approve", json={"version": r["version"]}
        )
    )
    assert (r["status"], r["start_date"], r["expiry_date"]) == (
        "approved",
        TODAY,
        str(expiry_for(T, 24)),
    )
    # With the policy in place, the next handover approves by itself (the rule allows it).
    handed_over(site, ["B2"], "Bank 2")
    (r2,) = regs(site, "B2")
    assert (r2["status"], r2["route"]) == ("approved", "smet_installation")
    # B3 was delivered (dispatched to the customer) but never installed: no warranty.
    anyone = new_client()
    assert ok(anyone.get(f"{API}/public/lookup?serial=B3"))["warranty"] == {"state": "none"}
    pub = ok(anyone.get(f"{API}/public/lookup?serial=b2"))
    assert (
        pub["warranty"] == {"state": "active"}
        and "Desert Telecom" not in str(pub)
        and "not proof" in pub["note"]
    )
    ok(
        pm.patch(
            f"{API}/rules",
            json={"version": ok(pm.get(f"{API}/rules"))["version"], "public_warranty_dates": True},
        )
    )
    assert ok(anyone.get(f"{API}/public/lookup?serial=B2"))["warranty"]["expiry_date"] == str(
        expiry_for(T, 24)
    )
    assert anyone.get(f"{API}/public/lookup?serial=NOPE").status_code == 404


def test_a_distributor_sale_is_checked_against_policy_and_decided_by_someone_else(site, user_with):
    pm, boss = site["pm"], site["boss"]
    policy(site, start_event="sale", auto_approve=False)
    dist = ok(
        pm.post(
            f"{API}/parties",
            json={"code": "DIST", "name": "Gulf Distributors", "kinds": ["distributor"]},
        ),
        201,
    )
    sale = str(T - timedelta(days=10))
    r = ok(pm.post(f"{API}/warranty-registrations", json={"serial": "b3", "route": "distributor", "customer_name": "Al Noor Clinic",
              "customer_contact": "+971 4 000 0000", "distributor_id": dist["id"], "sale_date": sale}), 201)  # fmt: skip
    assert r["status"] == "pending" and r["missing"] == [
        "Proof of purchase (attach the invoice or receipt)"
    ]
    assert (
        pm.post(
            f"{API}/warranty-registrations/{r['id']}/approve", json={"version": r["version"]}
        ).status_code
        == 403
    )  # submitter
    assert (
        boss.post(
            f"{API}/warranty-registrations/{r['id']}/approve", json={"version": r["version"]}
        ).status_code
        == 422
    )
    r = ok(pm.post(f"{API}/warranty-registrations/{r['id']}/files", files={"file": ("invoice.pdf", PDF, "application/pdf")},
                   data={"kind": "proof_of_purchase"}), 201)  # fmt: skip
    assert r["status"] == "pending" and r["missing"] == []  # this policy wants a person to decide
    r = ok(
        boss.post(
            f"{API}/warranty-registrations/{r['id']}/approve",
            json={"version": r["version"], "reason": "Invoice checked"},
        )
    )
    assert (r["start_date"], r["expiry_date"]) == (
        sale,
        str(expiry_for(T - timedelta(days=10), 24)),
    )
    dup = pm.post(
        f"{API}/warranty-registrations",
        json={"serial": "B3", "route": "direct", "customer_name": "X", "sale_date": sale},
    )
    assert dup.status_code == 409 and r["number"] in dup.text
    # Someone who may approve but not correct is refused, and the attempt is audited.
    clerk = user_with(perms=["warranty.view", "warranty.approve"], email="clerk@example.test")
    assert clerk.post(f"{API}/warranty-registrations/{r['id']}/correct", json={"version": r["version"], "reason": "Typo",
                      "sale_date": str(T)}).status_code == 403  # fmt: skip
    with SessionLocal() as db:
        assert db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "access.denied", AuditEvent.actor_name == "clerk"
            )
        )
    # An authorised correction recomputes the dates and is kept with its reason.
    newsale = str(T - timedelta(days=3))
    r = ok(pm.post(f"{API}/warranty-registrations/{r['id']}/correct", json={"version": r["version"], "reason": "Invoice date misread",
               "sale_date": newsale}))  # fmt: skip
    assert r["start_date"] == newsale and r["history"][-1]["reason"] == "Invoice date misread"
    assert r["history"][-1]["changes"]["expiry_date"] == [
        str(expiry_for(T - timedelta(days=10), 24)),
        str(expiry_for(T - timedelta(days=3), 24)),
    ]
    # Policy terms in use can't change; a new policy from a later date can.
    pol = ok(pm.get(f"{API}/warranty-policies"))["items"][0]
    assert (
        pm.put(
            f"{API}/warranty-policies/{pol['id']}",
            json=pol | {"item_id": pol["item"]["id"], "months": 36},
        ).status_code
        == 409
    )


def test_the_public_forms_land_as_pending_work_and_say_the_same_thing_either_way(site):
    anyone = new_client()
    got = anyone.post(f"{API}/public/registrations", data={"serial": "B4", "customer_name": "Sara", "contact": "+971 50 111 2222",
                      "purchase_date": TODAY}, files={"proof": ("r.jpg", JPEG, "image/jpeg")})  # fmt: skip
    same = anyone.post(f"{API}/public/registrations", data={"serial": "NOPE", "customer_name": "Sara", "contact": "+971 50 111 2222",
                       "purchase_date": TODAY})  # fmt: skip
    assert got.status_code == same.status_code == 202 and got.json() == same.json()
    (r,) = regs(site, "B4")
    r = ok(site["pm"].get(f"{API}/warranty-registrations/{r['id']}"))
    assert (r["status"], r["route"], len(r["files"])) == ("pending", "public", 1)
    sr = anyone.post(f"{API}/public/service-requests", json={"serial": "B9X", "name": "Sara", "phone": "+971 50 111 2222",
                     "issue": "Alarm beeping since this morning"})  # fmt: skip
    assert sr.status_code == 202
    (x,) = ok(site["pm"].get(f"{API}/service-requests?q=B9X"))["items"]
    assert (x["source"], x["status"], x["serial"]) == ("public", "new", None)


def test_amc_plans_visits_and_its_coverage_is_never_inferred_from_warranty(site):
    pm = site["pm"]
    policy(site)
    b = handed_over(site, ["B1", "B2"])
    end = str(T + timedelta(days=364))
    c = ok(pm.post(f"{API}/amc", json={"customer_id": site["cust"]["id"], "scope": "bank", "bank_ids": [b["id"]], "reference": "AMC-TEL-01",
              "starts_on": TODAY, "ends_on": end, "visit_every_months": 3, "covered": ["Quarterly inspection"],
              "excluded": ["Physical damage"]}), 201)  # fmt: skip
    c = ok(pm.post(f"{API}/amc/{c['id']}/activate", json={"version": c["version"]}))
    assert [v["stage"] for v in c["visits"]] == ["maintenance"] * 3 and {
        x["serial"] for x in c["batteries"]
    } == {"B1", "B2"}
    # Two separate answers: B1 has warranty and AMC; B3 (delivered, not installed) has neither.
    sr1 = ok(
        pm.post(
            f"{API}/service-requests", json={"serial": "B1", "issue": "Low backup time reported"}
        ),
        201,
    )
    assert sr1["warranty"]["state"] == "active" and [a["number"] for a in sr1["amc"]] == [
        c["number"]
    ]
    sr3 = ok(
        pm.post(
            f"{API}/service-requests", json={"serial": "B3", "issue": "Customer asks for a check"}
        ),
        201,
    )
    assert sr3["warranty"]["state"] == "none" and sr3["amc"] == []
    # A contract ending soon is listed for renewal; renewing keeps the old one and its visits.
    short = ok(pm.post(f"{API}/amc", json={"customer_id": site["cust"]["id"], "scope": "site", "site_id": site["site"]["id"],
                  "starts_on": str(T - timedelta(days=300)), "ends_on": str(T + timedelta(days=30)), "visit_every_months": 6}), 201)  # fmt: skip
    short = ok(pm.post(f"{API}/amc/{short['id']}/activate", json={"version": short["version"]}))
    rem = ok(pm.get(f"{API}/amc/reminders"))
    assert [x["number"] for x in rem["expiring"]] == [short["number"]] and len(
        rem["overdue"]
    ) == 1  # the visit 6 months in
    new = ok(pm.post(f"{API}/amc/{short['id']}/renew", json={"version": short["version"], "starts_on": str(T + timedelta(days=31)),
             "ends_on": str(T + timedelta(days=395))}), 201)  # fmt: skip
    old = ok(pm.get(f"{API}/amc/{short['id']}"))
    assert (
        old["status"],
        old["renewed_by"]["number"],
        new["renewed_from"]["number"],
        new["status"],
    ) == ("renewed", new["number"], short["number"], "draft")
    assert len(old["visits"]) == 1


def test_a_service_request_moves_by_the_rules_and_a_reset_code_stays_secret(site, user_with):
    pm, tech = site["pm"], site["tech"]
    handed_over(site, ["B1"])
    sr = ok(
        pm.post(
            f"{API}/service-requests",
            json={"serial": "B1", "issue": "Maintenance alarm on the BMS", "priority": "urgent"},
        ),
        201,
    )

    def mv(to, reason=""):
        return pm.post(
            f"{API}/service-requests/{sr['id']}/status",
            json={"version": sr["version"], "to": to, "reason": reason},
        )

    assert mv("in_progress").status_code == 409
    sr = ok(mv("under_review"))
    assert mv("pending_action").status_code == 422  # needs a reason
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/visit", json={"version": sr["version"], "scheduled_on": TODAY,
             "technician_ids": [site["tech_id"]]}), 201)  # fmt: skip
    assert sr["status"] == "scheduled" and [h["to"] for h in sr["history"]] == [
        "under_review",
        "scheduled",
    ]
    a = ok(
        pm.post(
            f"{API}/maintenance-alerts",
            json={
                "service_request_id": sr["id"],
                "description": "Reset requested after maintenance",
            },
        ),
        201,
    )
    bad = pm.post(f"{API}/maintenance-alerts/{a['id']}/verify", json={"version": a["version"], "serial_confirmed": True,
                  "customer_confirmed": False, "location_confirmed": True})  # fmt: skip
    assert bad.status_code == 422
    a = ok(pm.post(f"{API}/maintenance-alerts/{a['id']}/verify", json={"version": a["version"], "serial_confirmed": True,
               "customer_confirmed": True, "location_confirmed": True}))  # fmt: skip
    code = {
        "version": a["version"],
        "code": "RST-7731-XQ",
        "recipient": "Eng. Saeed (site)",
        "issued_on": TODAY,
    }
    assert (
        pm.post(f"{API}/maintenance-alerts/{a['id']}/code", json=code).status_code == 409
    )  # no completed inspection yet
    v = ok(tech.get(f"{API}/visits/{sr['visits'][0]['id']}"))
    ok(
        tech.post(
            f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
        )
    )
    viewer = user_with(perms=["codes.view", "codes.create"], email="desk@example.test")
    assert viewer.post(f"{API}/maintenance-alerts/{a['id']}/code", json=code).status_code == 403
    a = ok(pm.post(f"{API}/maintenance-alerts/{a['id']}/code", json=code))
    assert (
        a["has_code"]
        and "RST-7731" not in str(a)
        and "RST-7731" not in str(ok(viewer.get(f"{API}/maintenance-alerts")))
    )
    assert (
        viewer.post(
            f"{API}/maintenance-alerts/{a['id']}/reveal", json={"reason": "Customer lost it"}
        ).status_code
        == 403
    )
    assert (
        ok(
            pm.post(
                f"{API}/maintenance-alerts/{a['id']}/reveal", json={"reason": "Customer lost it"}
            )
        )["code"]
        == "RST-7731-XQ"
    )
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.entity_id == a["id"])).all()
        assert "code.view" in [e.action for e in events] and not any(
            "RST-7731" in f"{e.summary}{e.changes}{e.reason}" for e in events
        )
        assert "RST-7731" not in db.get(MaintenanceAlert, a["id"]).code_ciphertext
    sr = ok(pm.get(f"{API}/service-requests/{sr['id']}"))
    close = {
        "version": sr["version"],
        "resolution": "repaired",
        "closure_notes": "Alarm cleared after maintenance",
    }
    assert (
        pm.post(f"{API}/service-requests/{sr['id']}/close", json=close).status_code == 409
    )  # the alert is open
    ok(
        pm.post(
            f"{API}/maintenance-alerts/{a['id']}/close",
            json={"version": a["version"], "cleared": True, "note": "Cleared on site"},
        )
    )
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/close", json=close))
    assert sr["status"] == "closed" and mv("under_review").status_code == 409
    # After the retention period the code is erased; the record stays.
    with SessionLocal() as db:
        db.execute(
            update(MaintenanceAlert).values(
                closed_at=MaintenanceAlert.closed_at - timedelta(days=400)
            )
        )
        db.commit()
    assert ok(pm.get(f"{API}/maintenance-alerts"))["items"][0]["has_code"] is False
    assert (
        pm.post(f"{API}/maintenance-alerts/{a['id']}/reveal", json={"reason": "Audit"}).status_code
        == 409
    )


def test_a_replacement_is_linked_to_the_original_and_takes_its_warranty(site):
    pm, tech, boss = site["pm"], site["tech"], site["boss"]
    policy(site, replacement_rule="original_remaining")
    b = handed_over(site, ["B1", "B2"])
    orig = regs(site, "B2")[0]
    stock_in(pm, boss, site["wh"], supplier(pm, "SUP2"), site["unit"], 1, serials=["S1"])
    sr = ok(pm.post(f"{API}/service-requests", json={"serial": "B2", "issue": "Cell swollen"}), 201)
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/send-replacement", json={"version": sr["version"], "key": key(),
             "serial": "S1", "location_id": site["wh"]["loc"]["available"]["id"]}))  # fmt: skip
    assert sr["replacement"]["serial"] == "S1"
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/visit", json={"version": sr["version"], "scheduled_on": TODAY,
             "technician_ids": [site["tech_id"]]}), 201)  # fmt: skip
    v = sr["visits"][0]
    b = ok(tech.post(f"{API}/banks/{b['id']}/replace", json={"version": b["version"], "visit_id": v["id"], "serial": "B2",
           "new_serial": "S1", "reason": "Cell swollen"}))  # fmt: skip
    assert {p["serial"]: p["status"] for p in b["positions"]} == {
        "B1": "in_service",
        "S1": "in_service",
    }
    v = ok(tech.get(f"{API}/visits/{v['id']}"))
    ok(
        tech.post(
            f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
        )
    )
    sr = ok(pm.get(f"{API}/service-requests/{sr['id']}"))
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/receive", json={"version": sr["version"], "key": key(),
             "location_id": site["wh"]["loc"]["repair"]["id"]}))  # fmt: skip
    assert sr["battery"]["status"] == "in_repair" and sr["battery"]["location"].endswith("REP")
    ok(pm.post(f"{API}/service-requests/{sr['id']}/close", json={"version": sr["version"], "resolution": "replaced",
       "closure_notes": "Replaced under warranty", "acknowledgement": {"name": "Eng. Saeed", "on": TODAY}}))  # fmt: skip
    (new,) = regs(site, "S1")
    old = ok(pm.get(f"{API}/warranty-registrations/{orig['id']}"))
    assert (new["status"], new["expiry_date"], new["route"]) == (
        "approved",
        orig["expiry_date"],
        "replacement",
    )
    assert (old["status"], old["end_reason"].startswith("Replaced by S1")) == ("ended", True)
    titles = [e["title"] for e in ok(pm.get(f"{API}/serials/{sid(site, 'S1')}/timeline"))["events"]]
    assert (
        "Replaced B2" in titles
        and ok(new_client().get(f"{API}/public/lookup?serial=B2"))["warranty"]["state"] == "ended"
    )


def test_refused_attempts_leak_nothing(site, user_with):
    tech = site["tech"]
    handed_over(site, ["B1"])
    sr = ok(
        site["pm"].post(f"{API}/service-requests", json={"serial": "B1", "issue": "Check"}), 201
    )
    for path, body in ((f"/service-requests/{sr['id']}/close", {"version": 1, "resolution": "other", "closure_notes": "x x x"}),
                       ("/warranty-policies", {"item_id": site["unit"]["id"], "months": 1, "start_event": "sale",
                                               "replacement_rule": "longer", "effective_from": TODAY})):  # fmt: skip
        r = tech.post(f"{API}{path}", json=body)
        assert r.status_code == 403 and "B1" not in r.text
    # An installation technician sees no alerts, contracts or warranties.
    assert {
        tech.get(f"{API}{p}").status_code
        for p in ("/maintenance-alerts", "/amc", "/warranty-registrations")
    } == {403}
    anyone = new_client()
    for path in (f"/service-requests/{sr['id']}", "/warranty-registrations", "/maintenance-alerts"):
        assert anyone.get(f"{API}{path}").status_code == 401
