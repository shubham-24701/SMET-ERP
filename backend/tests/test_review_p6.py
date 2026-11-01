"""Regressions for the independent review of Phase 6 (2026-10-30). Each test failed before its fix."""

import io
from datetime import date, timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.models import AmcContract, Serial, WarrantyRegistration
from tests import test_installation
from tests.conftest import API, new_client
from tests.ops import key, ok, stock_in, supplier
from tests.test_aftersales import handed_over, policy, regs
from tests.test_installation import TODAY

site = test_installation.site
T = date.today()


def replace_on_request(x, old, new, bank, stock=True):
    pm, tech = x["pm"], x["tech"]
    if stock:
        stock_in(pm, x["boss"], x["wh"], supplier(pm, f"S{new}"), x["unit"], 1, serials=[new])
    sr = ok(pm.post(f"{API}/service-requests", json={"serial": old, "issue": "Cell failed"}), 201)
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/send-replacement", json={"version": sr["version"], "key": key(),
             "serial": new, "location_id": x["wh"]["loc"]["available"]["id"]}))  # fmt: skip
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/visit", json={"version": sr["version"], "scheduled_on": TODAY,
             "technician_ids": [x["tech_id"]]}), 201)  # fmt: skip
    b = ok(pm.get(f"{API}/banks/{bank['id']}"))
    ok(tech.post(f"{API}/banks/{b['id']}/replace", json={"version": b["version"], "visit_id": sr["visits"][0]["id"],
       "serial": old, "new_serial": new, "reason": "Cell failed"}))  # fmt: skip
    v = ok(tech.get(f"{API}/visits/{sr['visits'][0]['id']}"))
    ok(
        tech.post(
            f"{API}/visits/{v['id']}/complete", json={"version": v["version"], "done_on": TODAY}
        )
    )
    sr = ok(pm.get(f"{API}/service-requests/{sr['id']}"))
    return pm.post(f"{API}/service-requests/{sr['id']}/close", json={"version": sr["version"], "resolution": "replaced",
                   "closure_notes": "Replaced"}), sr  # fmt: skip


def test_correcting_a_replacement_keeps_its_term(site):  # H1
    policy(site, start_event="installation")
    b = handed_over(site, ["B1", "B2"])
    with (
        SessionLocal() as db
    ):  # an original sold long ago, so its remaining term differs from a new one
        db.execute(
            update(WarrantyRegistration).values(
                start_date=T - timedelta(days=100), expiry_date=T + timedelta(days=600)
            )
        )
        db.commit()
    orig = regs(site, "B2")[0]
    ok(replace_on_request(site, "B2", "S1", b)[0])
    (r,) = regs(site, "S1")
    r = ok(site["pm"].post(f"{API}/warranty-registrations/{r['id']}/correct", json={"version": r["version"], "reason": "New phone",
               "customer_contact": "+971 50 999 0000"}))  # fmt: skip
    assert (r["expiry_date"], r["customer_contact"]) == (orig["expiry_date"], "+971 50 999 0000")


def test_an_expired_warranty_carries_nothing_to_its_replacement(site):  # H2
    policy(site)
    b = handed_over(site, ["B1", "B2"])
    with SessionLocal() as db:
        db.execute(
            update(WarrantyRegistration).values(
                start_date=T - timedelta(days=800), expiry_date=T - timedelta(days=70)
            )
        )
        db.commit()
    ok(replace_on_request(site, "B2", "S1", b)[0])
    assert regs(site, "S1") == []


def test_the_public_form_answers_alike_even_with_a_bad_file(site):  # H3
    anyone = new_client()
    form = {"customer_name": "Sara", "contact": "+971 50 111 2222", "purchase_date": TODAY}
    bad = ("x.jpg", io.BytesIO(b"not an image at all"), "image/jpeg")
    real = anyone.post(
        f"{API}/public/registrations", data=form | {"serial": "B4"}, files={"proof": bad}
    )
    bad = ("x.jpg", io.BytesIO(b"not an image at all"), "image/jpeg")
    fake = anyone.post(
        f"{API}/public/registrations", data=form | {"serial": "NOPE"}, files={"proof": bad}
    )
    assert real.status_code == fake.status_code == 422 and real.json() == fake.json()


def test_the_timeline_shows_after_sales_only_to_those_allowed(site):  # M4
    policy(site)
    handed_over(site, ["B1"])
    ok(
        site["pm"].post(
            f"{API}/service-requests", json={"serial": "B1", "issue": "Private complaint text"}
        ),
        201,
    )
    sid = ok(site["tech"].get(f"{API}/trace/scan?code=B1"))["id"]
    t = ok(site["tech"].get(f"{API}/serials/{sid}/timeline"))  # stock.view only
    assert (
        "Private complaint" not in str(t) and "warranty" not in t["now"] and "amc" not in t["now"]
    )
    assert "Private complaint" in str(ok(site["pm"].get(f"{API}/serials/{sid}/timeline")))


def test_cancelling_a_renewal_gives_the_contract_back(site):  # M5
    pm = site["pm"]
    c = ok(pm.post(f"{API}/amc", json={"customer_id": site["cust"]["id"], "scope": "site", "site_id": site["site"]["id"],
              "starts_on": TODAY, "ends_on": str(T + timedelta(days=200)), "visit_every_months": 12}), 201)  # fmt: skip
    with (
        SessionLocal() as db
    ):  # active without planned visits (no bank yet), as a draft can't be renewed
        db.execute(update(AmcContract).values(status="active"))
        db.commit()
    c = ok(pm.get(f"{API}/amc/{c['id']}"))
    n = ok(pm.post(f"{API}/amc/{c['id']}/renew", json={"version": c["version"], "starts_on": str(T + timedelta(days=201)),
           "ends_on": str(T + timedelta(days=500))}), 201)  # fmt: skip
    ok(
        pm.post(
            f"{API}/amc/{n['id']}/cancel",
            json={"version": n["version"], "reason": "Customer declined"},
        )
    )
    assert ok(pm.get(f"{API}/amc/{c['id']}"))["status"] == "active"


def test_a_request_doesnt_close_with_its_battery_at_the_workshop(site):  # M6
    pm = site["pm"]
    sr = ok(
        pm.post(f"{API}/service-requests", json={"serial": "B5", "issue": "Check at workshop"}), 201
    )
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/receive", json={"version": sr["version"], "key": key(),
             "location_id": site["wh"]["loc"]["repair"]["id"]}))  # fmt: skip
    r = pm.post(
        f"{API}/service-requests/{sr['id']}/close",
        json={"version": sr["version"], "resolution": "repaired", "closure_notes": "Done"},
    )
    assert r.status_code == 409 and "workshop" in r.text


def test_staff_can_register_under_a_handover_policy(site):  # M7
    policy(site, start_event="handover", requires_proof=False)
    r = ok(site["pm"].post(f"{API}/warranty-registrations", json={"serial": "B3", "route": "direct", "customer_name": "Clinic",
              "sale_date": TODAY, "handover_date": TODAY}), 201)  # fmt: skip
    assert (r["status"], r["start_date"]) == ("approved", TODAY)


def test_a_battery_in_the_workshop_is_not_under_contract(site):  # M9
    pm = site["pm"]
    c = ok(pm.post(f"{API}/amc", json={"customer_id": site["cust"]["id"], "scope": "customer", "starts_on": TODAY,
              "ends_on": str(T + timedelta(days=90)), "visit_every_months": 12}), 201)  # fmt: skip
    with SessionLocal() as db:
        db.execute(update(AmcContract).where(AmcContract.id == c["id"]).values(status="active"))
        db.commit()
    sr = ok(pm.post(f"{API}/service-requests", json={"serial": "B5", "issue": "Check"}), 201)
    assert [a["number"] for a in sr["amc"]] == [c["number"]]
    sr = ok(pm.post(f"{API}/service-requests/{sr['id']}/receive", json={"version": sr["version"], "key": key(),
             "location_id": site["wh"]["loc"]["repair"]["id"]}))  # fmt: skip
    assert sr["amc"] == []


def test_a_refurbished_replacement_with_an_old_warranty_still_closes(site):  # L10
    policy(site, auto_approve=True, requires_proof=False, start_event="sale")
    b = handed_over(site, ["B1", "B2"])
    # S1 once had its own (approved) registration, as if it had been sold and come back.
    stock_in(
        site["pm"],
        site["boss"],
        site["wh"],
        supplier(site["pm"], "SOLD"),
        site["unit"],
        1,
        serials=["S9"],
    )
    with SessionLocal() as db:
        s9 = db.query(Serial).filter(Serial.serial == "S9").one()
        db.add(WarrantyRegistration(number="WR-OLD-1", serial_id=s9.id, route="direct", status="approved",
                                    customer_name="Earlier buyer", start_date=T - timedelta(days=10), expiry_date=T + timedelta(days=300)))  # fmt: skip
        db.commit()
    r, _ = replace_on_request(site, "B2", "S9", b, stock=False)
    ok(r)
    assert {x["number"] for x in regs(site, "S9") if x["status"] == "approved"} != {"WR-OLD-1"}
