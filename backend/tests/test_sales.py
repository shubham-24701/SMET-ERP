"""Phase 4 sales and dispatch: enquiry → quotation revisions → approval → customer answer → one
sales order → reservations → dispatch of exact serials → delivery with discrepancies → cancellation."""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pytest
from sqlalchemy import update

from app.db import SessionLocal
from app.models import QuotationRevision
from tests.conftest import API
from tests.ops import ALL, item, key, ok, reconciled, stock_in, summary, supplier, warehouse


@pytest.fixture
def shop(user_with):
    a, b = (
        user_with(perms=ALL, email="seller@example.test"),
        user_with(perms=ALL, email="boss@example.test"),
    )
    wh, sup = warehouse(a), supplier(a)
    cust = ok(
        a.post(
            f"{API}/parties", json={"code": "CUST1", "name": "Gulf Hospital", "kinds": ["customer"]}
        ),
        201,
    )
    unit = item(a, "UNIT-12V", kind="battery_unit", tracking="serial")
    lug = item(a, "LUG")
    stock_in(a, b, wh, sup, unit, 4, serials=["U1", "U2", "U3", "U4"])
    stock_in(a, b, wh, sup, lug, 20)
    return {"a": a, "b": b, "wh": wh, "cust": cust, "unit": unit, "lug": lug}


def quote(s, lines=None):
    lines = lines or [{"line_type": "battery", "item_id": s["unit"]["id"], "qty": 3, "unit_price": 900},
                      {"line_type": "accessory", "item_id": s["lug"]["id"], "qty": 10, "unit_price": 5, "discount_pct": 10},
                      {"line_type": "installation", "description": "Installation and commissioning", "qty": 1, "unit_price": 1500}]  # fmt: skip
    return ok(
        s["a"].post(f"{API}/quotations", json={"customer_id": s["cust"]["id"], "lines": lines}), 201
    )


def to_order(s, qt=None):
    a, b = s["a"], s["b"]
    qt = qt or quote(s)
    v = lambda q: q["revisions"][0]["version"]  # noqa: E731
    qt = ok(a.post(f"{API}/quotations/{qt['id']}/submit", json={"version": v(qt)}))
    qt = ok(b.post(f"{API}/quotations/{qt['id']}/approve", json={"version": v(qt)}))
    qt = ok(a.post(f"{API}/quotations/{qt['id']}/answer", json={"version": v(qt), "accepted": True, "by": "Dr Noor",
                                                                 "ref": "LPO-88", "on": str(date.today())}))  # fmt: skip
    return qt, ok(a.post(f"{API}/quotations/{qt['id']}/convert", json={"version": v(qt)}), 201)


def test_a_quotation_goes_from_enquiry_to_exactly_one_order(shop):
    a, b = shop["a"], shop["b"]
    enq = ok(
        a.post(
            f"{API}/enquiries",
            json={
                "customer_id": shop["cust"]["id"],
                "kind": "product",
                "lines": [
                    {"item_id": shop["unit"]["id"], "qty": 3},
                    {"description": "Rack installation", "qty": 1},
                ],
            },
        ),
        201,
    )
    qt = ok(a.post(f"{API}/enquiries/{enq['id']}/quote"), 201)
    r1 = qt["revisions"][0]
    assert [ln["line_type"] for ln in r1["lines"]] == ["battery", "other"]
    assert ok(a.get(f"{API}/enquiries/{enq['id']}"))["status"] == "quoted"
    empty = a.post(f"{API}/quotations/{qt['id']}/submit", json={"version": r1["version"]})
    assert empty.status_code == 422 and "prices" in empty.text
    lines = [{"line_type": "battery", "item_id": shop["unit"]["id"], "qty": 3, "unit_price": 900, "discount_pct": 5},
             {"line_type": "installation", "description": "Rack installation", "qty": 1, "unit_price": 1500}]  # fmt: skip
    qt = ok(
        b.put(f"{API}/quotations/{qt['id']}/draft", json={"version": r1["version"], "lines": lines})
    )
    r1 = qt["revisions"][0]
    assert r1["total"] == 4065.0  # 3 × 900 less 5 %, plus 1,500
    qt = ok(a.post(f"{API}/quotations/{qt['id']}/submit", json={"version": r1["version"]}))
    r1 = qt["revisions"][0]
    # Both people who prepared it are out: b edited the draft, a submitted.
    for who in (a, b):
        assert (
            who.post(
                f"{API}/quotations/{qt['id']}/approve", json={"version": r1["version"]}
            ).status_code
            == 403
        )
    # A submitted revision never changes.
    frozen = a.put(
        f"{API}/quotations/{qt['id']}/draft", json={"version": r1["version"], "lines": lines}
    )
    assert frozen.status_code == 409


def test_revisions_are_kept_and_conversion_happens_once(shop, user_with):
    a, b = shop["a"], shop["b"]
    qt = quote(shop)
    r1 = qt["revisions"][0]
    qt = ok(a.post(f"{API}/quotations/{qt['id']}/submit", json={"version": r1["version"]}))
    qt = ok(
        b.post(
            f"{API}/quotations/{qt['id']}/reject",
            json={"version": qt["revisions"][0]["version"], "reason": "Discount too high"},
        )
    )
    qt = ok(
        a.post(
            f"{API}/quotations/{qt['id']}/revise", json={"version": qt["revisions"][0]["version"]}
        ),
        201,
    )
    assert [(r["rev"], r["status"]) for r in qt["revisions"]] == [(2, "draft"), (1, "revised")]
    assert qt["revisions"][1]["decision_reason"] == "Discount too high"  # R1 kept as it was
    qt, so = to_order(shop, qt)
    assert so["quotation"]["rev"] == 2 and so["status"] == "open"
    assert [ln["line_type"] for ln in so["lines"]] == ["battery", "accessory", "installation"]
    again = a.post(
        f"{API}/quotations/{qt['id']}/convert", json={"version": qt["revisions"][0]["version"]}
    )
    assert again.status_code == 409 and so["number"] in again.text
    # Two people converting at the same moment still make one order.
    qt2 = quote(shop)
    qt2 = ok(
        a.post(
            f"{API}/quotations/{qt2['id']}/submit", json={"version": qt2["revisions"][0]["version"]}
        )
    )
    qt2 = ok(
        b.post(
            f"{API}/quotations/{qt2['id']}/approve",
            json={"version": qt2["revisions"][0]["version"]},
        )
    )
    qt2 = ok(a.post(f"{API}/quotations/{qt2['id']}/answer", json={"version": qt2["revisions"][0]["version"], "accepted": True,
                                                                   "by": "Buyer", "on": str(date.today())}))  # fmt: skip
    people = [user_with(perms=ALL) for _ in range(3)]
    with ThreadPoolExecutor(3) as pool:
        codes = sorted(pool.map(lambda c: c.post(f"{API}/quotations/{qt2['id']}/convert",
                                                 json={"version": qt2["revisions"][0]["version"]}).status_code, people))  # fmt: skip
    assert codes == [201, 409, 409]


def test_expired_quotations_cannot_be_accepted(shop):
    a, b = shop["a"], shop["b"]
    qt = quote(shop)
    qt = ok(
        a.post(
            f"{API}/quotations/{qt['id']}/submit", json={"version": qt["revisions"][0]["version"]}
        )
    )
    qt = ok(
        b.post(
            f"{API}/quotations/{qt['id']}/approve", json={"version": qt["revisions"][0]["version"]}
        )
    )
    with SessionLocal() as db:
        db.execute(update(QuotationRevision).where(QuotationRevision.id == qt["revisions"][0]["id"])
                   .values(valid_until=date.today() - timedelta(days=1)))  # fmt: skip
        db.commit()
    qt = ok(a.get(f"{API}/quotations/{qt['id']}"))
    assert qt["status"] == "expired"
    r = a.post(f"{API}/quotations/{qt['id']}/answer", json={"version": qt["revisions"][0]["version"], "accepted": True,
                                                            "by": "Late buyer", "on": str(date.today())})  # fmt: skip
    assert r.status_code == 409 and "expired" in r.text
    ok(
        a.post(
            f"{API}/quotations/{qt['id']}/revise", json={"version": qt["revisions"][0]["version"]}
        ),
        201,
    )  # renew by revising


def test_small_quotations_without_discount_can_approve_themselves(shop, admin):
    a = shop["a"]
    rules = ok(admin.get(f"{API}/rules"))
    ok(
        admin.patch(
            f"{API}/rules",
            json={
                "version": rules["version"],
                "quote_auto_approve_up_to": 5000,
                "quote_max_discount_pct": 5,
            },
        )
    )
    small = quote(
        shop,
        [
            {
                "line_type": "accessory",
                "item_id": shop["lug"]["id"],
                "qty": 10,
                "unit_price": 5,
                "discount_pct": 5,
            }
        ],
    )
    assert (
        ok(
            a.post(
                f"{API}/quotations/{small['id']}/submit",
                json={"version": small["revisions"][0]["version"]},
            )
        )["status"]
        == "approved"
    )
    deep = quote(
        shop,
        [
            {
                "line_type": "accessory",
                "item_id": shop["lug"]["id"],
                "qty": 10,
                "unit_price": 5,
                "discount_pct": 20,
            }
        ],
    )
    assert (
        ok(
            a.post(
                f"{API}/quotations/{deep['id']}/submit",
                json={"version": deep["revisions"][0]["version"]},
            )
        )["status"]
        == "submitted"
    )


def test_dispatch_takes_reserved_available_serials_and_delivery_records_refusals(shop):
    a, wh = shop["a"], shop["wh"]
    _, so = to_order(shop)
    ok(a.post(f"{API}/sales-orders/{so['id']}/reserve", json={"key": key()}))
    so = ok(a.get(f"{API}/sales-orders/{so['id']}"))
    unit_line = next(ln for ln in so["lines"] if ln["line_type"] == "battery")
    lug_line = next(ln for ln in so["lines"] if ln["line_type"] == "accessory")
    assert (unit_line["reserved"], lug_line["reserved"]) == (3, 10)
    assert summary(a, "UNIT-12V")["free"] == 1
    too_much = a.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"], "lines": [
        {"order_line_id": unit_line["id"], "qty": 4, "serials": ["U1", "U2", "U3", "U4"]}]})  # fmt: skip
    assert too_much.status_code == 409 and "left to send" in too_much.text
    service = a.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"], "lines": [
        {"order_line_id": next(ln for ln in so["lines"] if ln["line_type"] == "installation")["id"], "qty": 1}]})  # fmt: skip
    assert service.status_code == 422  # services aren't dispatched
    d = ok(a.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"], "transporter": "Al Noor Transport",
              "vehicle": "D 12345", "lines": [{"order_line_id": unit_line["id"], "qty": 2, "serials": ["U1", "U2"]},
                                              {"order_line_id": lug_line["id"], "qty": 10}]}), 201)  # fmt: skip
    assert summary(a, "UNIT-12V")["available"] == 4  # planning moves nothing
    k = key()
    ok(a.post(f"{API}/dispatches/{d['id']}/send", json={"version": d["version"], "key": k}))
    again = ok(a.post(f"{API}/dispatches/{d['id']}/send", json={"version": d["version"], "key": k}))
    assert again["duplicate"]
    s = summary(a, "UNIT-12V")
    assert (s["available"], s["reserved"], s["free"]) == (
        2,
        1,
        1,
    )  # the reservation was used, not free stock
    u1 = ok(a.get(f"{API}/serials", params={"q": "U1"}))["items"][0]
    assert u1["status"] == "dispatched" and u1["location"] is None
    so = ok(a.get(f"{API}/sales-orders/{so['id']}"))
    assert so["status"] == "partly_dispatched"
    d = ok(a.get(f"{API}/dispatches/{d['id']}"))
    lines = {ln["item"]["code"]: ln for ln in d["lines"]}
    ok(a.post(f"{API}/dispatches/{d['id']}/deliver", json={"version": d["version"], "key": key(), "received_by": "Store keeper Ali",
              "delivered_on": str(date.today()), "lines": [{"line_id": lines["UNIT-12V"]["id"], "rejected_serials": ["U2"], "note": "Dented case"},
                                                           {"line_id": lines["LUG"]["id"], "qty_damaged": 1}]}))  # fmt: skip
    so = ok(a.get(f"{API}/sales-orders/{so['id']}"))
    ul = next(ln for ln in so["lines"] if ln["line_type"] == "battery")
    assert (ul["qty_dispatched"], ul["qty_delivered"], ul["pending"]) == (
        1,
        1,
        2,
    )  # the refused one is owed again
    u2 = ok(a.get(f"{API}/serials", params={"q": "U2"}))["items"][0]
    assert (u2["status"], u2["purpose"]) == ("on_hold", "returns")
    assert ok(a.get(f"{API}/serials", params={"q": "U1"}))["items"][0]["status"] == "delivered"
    reconciled(a)


def test_cancelling_a_sent_dispatch_puts_stock_and_reservations_back(shop):
    a, wh = shop["a"], shop["wh"]
    _, so = to_order(shop)
    ok(a.post(f"{API}/sales-orders/{so['id']}/reserve", json={"key": key()}))
    unit_line = next(ln for ln in so["lines"] if ln["line_type"] == "battery")
    # A unit on hold can't leave.
    ok(
        a.post(
            f"{API}/adjustments",
            json={
                "key": key(),
                "reason": "Found",
                "lines": [
                    {
                        "item_id": shop["unit"]["id"],
                        "location_id": wh["loc"]["quarantine"]["id"],
                        "serials_in": ["HELD1"],
                    }
                ],
            },
        ),
        201,
    )
    held = ok(a.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"], "lines": [
        {"order_line_id": unit_line["id"], "qty": 1, "serials": ["U3"]}]}), 201)  # fmt: skip
    plan = ok(a.put(f"{API}/dispatches/{held['id']}", json={"version": held["version"], "order_id": so["id"], "warehouse_id": wh["id"],
              "lines": [{"order_line_id": unit_line["id"], "qty": 1, "serials": ["HELD1"]}]}))  # fmt: skip
    r = a.post(
        f"{API}/dispatches/{plan['id']}/send", json={"version": plan["version"], "key": key()}
    )
    assert r.status_code in (409, 422)  # not approved, not in available stock
    ok(
        a.post(
            f"{API}/dispatches/{plan['id']}/cancel",
            json={"version": plan["version"], "key": key(), "reason": "Wrong unit"},
        )
    )
    d = ok(a.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"], "lines": [
        {"order_line_id": unit_line["id"], "qty": 3, "serials": ["U1", "U2", "U3"]}]}), 201)  # fmt: skip
    ok(a.post(f"{API}/dispatches/{d['id']}/send", json={"version": d["version"], "key": key()}))
    before = summary(a, "UNIT-12V")
    assert (before["available"], before["reserved"]) == (1, 0)
    d = ok(a.get(f"{API}/dispatches/{d['id']}"))
    ok(
        a.post(
            f"{API}/dispatches/{d['id']}/cancel",
            json={"version": d["version"], "key": key(), "reason": "Customer postponed"},
        )
    )
    after = summary(a, "UNIT-12V")
    assert (after["available"], after["reserved"], after["free"]) == (4, 3, 1)
    d = ok(a.get(f"{API}/dispatches/{d['id']}"))
    assert (
        d["status"] == "cancelled" and d["cancel_reason"] == "Customer postponed"
    )  # the record stays
    moves = ok(a.get(f"{API}/stock/moves", params={"doc": d["number"]}))["items"]
    assert sorted(m["kind"] for m in moves) == ["dispatch"] * 3 + ["dispatch_cancel"] * 3
    assert ok(a.get(f"{API}/sales-orders/{so['id']}"))["status"] == "open"
    reconciled(a)


def test_what_an_order_is_short_of_becomes_a_purchase_request(shop):
    a = shop["a"]
    lines = [{"line_type": "accessory", "item_id": shop["lug"]["id"], "qty": 35, "unit_price": 5}]
    _, so = to_order(shop, quote(shop, lines))
    pr = ok(a.post(f"{API}/sales-orders/{so['id']}/purchase-request"), 201)
    assert [(ln["item"]["code"], ln["qty"]) for ln in pr["lines"]] == [("LUG", 15)] and pr[
        "ref"
    ] == so["number"]
    ok(a.post(f"{API}/sales-orders/{so['id']}/reserve", json={"key": key()}))
    so = ok(a.post(f"{API}/sales-orders/{so['id']}/close", json={"version": ok(a.get(f"{API}/sales-orders/{so['id']}"))["version"],
                                                                "reason": "Customer cancelled"}))  # fmt: skip
    assert so["status"] == "cancelled" and summary(a, "LUG")["reserved"] == 0


def test_supplier_and_assembled_items_are_checked_on_quotations(shop):
    a = shop["a"]
    r = a.post(f"{API}/quotations", json={"customer_id": shop["cust"]["id"], "lines": [
        {"line_type": "installation", "item_id": shop["lug"]["id"], "qty": 1, "unit_price": 1}]})  # fmt: skip
    assert r.status_code == 422 and "Services" in r.text
    sup = ok(a.get(f"{API}/parties", params={"kind": "supplier"}))["items"][0]
    r = a.post(f"{API}/quotations", json={"customer_id": sup["id"], "lines": [
        {"line_type": "accessory", "item_id": shop["lug"]["id"], "qty": 1, "unit_price": 1}]})  # fmt: skip
    assert r.status_code == 422 and "customer" in r.text
