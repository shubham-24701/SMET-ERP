"""Master data and purchasing: request → quotations → order → approval → receipt → decisions →
return → replacement."""

import pytest

from tests.conftest import API
from tests.ops import ALL, approved_po, item, key, ok, reconciled, summary, supplier, warehouse


@pytest.fixture
def team(user_with):
    """Two people with every business permission: work done by one is approved by the other."""
    return user_with(perms=ALL, email="maker@example.test"), user_with(
        perms=ALL, email="checker@example.test"
    )


def test_items_keep_what_history_depends_on(team):
    a, _ = team
    units = [u["code"] for u in ok(a.get(f"{API}/units"))["items"]]
    assert "PCS" in units
    x = item(a, "cell-100ah", kind="battery_unit", tracking="serial")
    assert x["code"] == "CELL-100AH"
    dup = a.post(
        f"{API}/items",
        json={"code": "Cell-100Ah", "name": "x", "kind": "component", "unit_code": "PCS"},
    )
    assert dup.status_code == 422 and "already in use" in dup.text
    bad = a.post(
        f"{API}/items",
        json={
            "code": "B1",
            "name": "x",
            "kind": "component",
            "unit_code": "PCS",
            "assembled": True,
        },
    )
    assert bad.status_code == 422
    model = a.post(f"{API}/items", json={"code": "B2", "name": "x", "kind": "battery_model", "unit_code": "PCS",
                                         "assembled": True})  # fmt: skip
    assert (
        model.status_code == 422 and "serial" in model.text
    )  # each assembled battery is serialised
    # Unused: the tracking can still change. Once used in an order: it can't.
    x = ok(a.patch(f"{API}/items/{x['id']}", json={"version": x["version"], "tracking": "lot"}))
    sup = supplier(a)
    a.post(
        f"{API}/purchase-orders",
        json={"supplier_id": sup["id"], "lines": [{"item_id": x["id"], "qty": 1, "unit_price": 1}]},
    )
    r = a.patch(f"{API}/items/{x['id']}", json={"version": x["version"], "tracking": "none"})
    assert r.status_code == 422 and "already used" in r.text
    assert (
        ok(a.patch(f"{API}/items/{x['id']}", json={"version": x["version"], "name": "Renamed"}))[
            "name"
        ]
        == "Renamed"
    )


def test_new_warehouses_get_every_area_the_workflows_need(team):
    a, _ = team
    w = warehouse(a)
    assert set(w["loc"]) == {"available", "inspection", "quarantine", "rejected", "assembly", "returns"}


def test_document_numbers_follow_on_and_can_be_reformatted(team, admin):
    a, _ = team
    sup, x = supplier(a), item(a, "C1")
    body = {"supplier_id": sup["id"], "lines": [{"item_id": x["id"], "qty": 1, "unit_price": 1}]}
    n1 = ok(a.post(f"{API}/purchase-orders", json=body), 201)["number"]
    n2 = ok(a.post(f"{API}/purchase-orders", json=body), 201)["number"]
    assert n1.endswith("-00001") and n2.endswith("-00002") and n1.startswith("PO-20")
    ok(
        admin.put(
            f"{API}/numbering/purchase_order",
            json={"prefix": "smpo", "padding": 4, "yearly": False},
        )
    )
    assert ok(a.post(f"{API}/purchase-orders", json=body), 201)["number"] == "SMPO-0003"


def test_the_whole_purchase_journey_keeps_quantities_apart(team, user_with):
    maker, checker = team
    wh, x = warehouse(maker), item(maker, "CABLE")
    s1, s2 = supplier(maker, "S1"), supplier(maker, "S2")
    pr = ok(
        maker.post(
            f"{API}/purchase-requests",
            json={"source": "shortage", "lines": [{"item_id": x["id"], "qty": 10}]},
        ),
        201,
    )
    for s, price in ((s1, 12), (s2, 9.5)):
        ok(
            maker.post(
                f"{API}/supplier-quotes",
                json={
                    "pr_id": pr["id"],
                    "supplier_id": s["id"],
                    "lines": [
                        {
                            "item_id": x["id"],
                            "qty": 10,
                            "unit_price": price,
                            "delivery_date": "2026-12-01",
                        }
                    ],
                },
            ),
            201,
        )
    full = ok(maker.get(f"{API}/purchase-requests/{pr['id']}"))
    offers = full["comparison"][0]["offers"]
    assert [o["supplier"] for o in offers if o["lowest_price"]] == ["Supplier S2"]
    cheapest = next(q for q in full["quotes"] if q["supplier"]["code"] == "S2")
    po = ok(maker.post(f"{API}/supplier-quotes/{cheapest['id']}/order"), 201)
    assert po["status"] == "draft" and po["lines"][0]["unit_price"] == 9.5
    assert ok(maker.get(f"{API}/purchase-requests/{pr['id']}"))["status"] == "ordered"
    po = ok(maker.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    # The person who raised it can't approve it, even holding the permission.
    own = maker.post(f"{API}/purchase-orders/{po['id']}/approve", json={"version": po["version"]})
    assert own.status_code == 403
    po = ok(
        checker.post(
            f"{API}/purchase-orders/{po['id']}/approve",
            json={"version": po["version"], "reason": "ok"},
        )
    )
    line = po["lines"][0]["id"]
    # A storekeeper records the delivery; quality decides.
    store = user_with("warehouse_officer")
    decided = store.post(f"{API}/purchase-orders/{po['id']}/receipts", json={"key": key(), "warehouse_id": wh["id"],
                         "lines": [{"po_line_id": line, "qty": 8, "accept": 8}]})  # fmt: skip
    assert decided.status_code == 403
    k = key()
    receipt = {
        "key": k,
        "warehouse_id": wh["id"],
        "delivery_ref": "DN-77",
        "lines": [{"po_line_id": line, "qty": 8}],
    }
    first = ok(store.post(f"{API}/purchase-orders/{po['id']}/receipts", json=receipt), 201)
    again = ok(
        store.post(f"{API}/purchase-orders/{po['id']}/receipts", json=receipt), 201
    )  # a double click
    assert again["duplicate"] and again["number"] == first["number"]
    po = ok(maker.get(f"{API}/purchase-orders/{po['id']}"))
    ln = po["lines"][0]
    assert (ln["qty_received"], ln["qty_awaiting"], ln["qty_outstanding"]) == (8, 8, 2)
    assert summary(maker, "CABLE")["inspection"] == 8 and summary(maker, "CABLE")["free"] == 0
    inspector = user_with("quality_inspector")
    ok(inspector.post(f"{API}/purchase-orders/{po['id']}/lines/{line}/decide",
                      json={"key": key(), "warehouse_id": wh["id"], "accept": 5, "reject": 2, "hold": 1}))  # fmt: skip
    ln = ok(maker.get(f"{API}/purchase-orders/{po['id']}"))["lines"][0]
    assert (ln["qty_accepted"], ln["qty_rejected"], ln["qty_held"], ln["qty_awaiting"]) == (
        5,
        2,
        1,
        0,
    )
    assert (
        ln["qty_outstanding"] == 4
    )  # 10 ordered − 5 accepted − 1 held: rejected goods are still owed
    over = inspector.post(f"{API}/purchase-orders/{po['id']}/lines/{line}/decide",
                          json={"key": key(), "warehouse_id": wh["id"], "accept": 2, "source": "held"})  # fmt: skip
    assert over.status_code == 409  # only 1 is on hold
    ok(inspector.post(f"{API}/purchase-orders/{po['id']}/lines/{line}/decide",
                      json={"key": key(), "warehouse_id": wh["id"], "accept": 1, "source": "held"}))  # fmt: skip
    ok(maker.post(f"{API}/purchase-orders/{po['id']}/returns", json={"key": key(), "warehouse_id": wh["id"],
                  "reason": "Damaged insulation", "lines": [{"po_line_id": line, "qty": 2}]}), 201)  # fmt: skip
    too_many = maker.post(f"{API}/purchase-orders/{po['id']}/returns", json={"key": key(), "warehouse_id": wh["id"],
                          "reason": "again", "lines": [{"po_line_id": line, "qty": 1}]})  # fmt: skip
    assert too_many.status_code == 409
    over_delivery = store.post(f"{API}/purchase-orders/{po['id']}/receipts", json={"key": key(), "warehouse_id": wh["id"],
                               "lines": [{"po_line_id": line, "qty": 5}]})  # fmt: skip
    assert over_delivery.status_code == 409 and "outstanding" in over_delivery.text
    replacement = {
        "key": key(),
        "warehouse_id": wh["id"],
        "lines": [{"po_line_id": line, "qty": 4, "accept": 4}],
    }
    ok(
        inspector.post(f"{API}/purchase-orders/{po['id']}/receipts", json=replacement), 403
    )  # inspectors don't receive
    ok(maker.post(f"{API}/purchase-orders/{po['id']}/receipts", json=replacement), 201)
    po = ok(maker.get(f"{API}/purchase-orders/{po['id']}"))
    ln = po["lines"][0]
    assert po["status"] == "received"
    assert (ln["qty_ordered"], ln["qty_received"], ln["qty_accepted"], ln["qty_rejected"], ln["qty_returned"],
            ln["qty_outstanding"]) == (10, 12, 10, 2, 2, 0)  # fmt: skip
    s = summary(maker, "CABLE")
    assert (s["available"], s["rejected"], s["on_hand"]) == (10, 0, 10)
    reconciled(maker)


def test_small_orders_in_the_company_currency_can_approve_themselves(team, admin):
    a, _ = team
    rules = ok(admin.get(f"{API}/rules"))
    ok(
        admin.patch(
            f"{API}/rules", json={"version": rules["version"], "po_auto_approve_up_to": 500}
        )
    )
    sup, x = supplier(a), item(a, "FUSE")
    small = approved_po(a, a, sup, [(x, 10, 20)])  # 200 AED: no approver needed
    assert small["status"] == "approved" and "automatically" in small["decision_reason"]
    # 600 AED is over the limit; 1 USD is under it but in another currency: both wait for approval.
    for qty, price, currency in ((10, 60, "AED"), (1, 1, "USD")):
        body = {"supplier_id": sup["id"], "currency": currency,
                "lines": [{"item_id": x["id"], "qty": qty, "unit_price": price}]}  # fmt: skip
        po = ok(a.post(f"{API}/purchase-orders", json=body), 201)
        po = ok(a.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
        assert po["status"] == "submitted"


def test_goods_are_received_only_against_approved_orders(team):
    a, _ = team
    wh, sup, x = warehouse(a), supplier(a), item(a, "LUG")
    po = ok(
        a.post(
            f"{API}/purchase-orders",
            json={
                "supplier_id": sup["id"],
                "lines": [{"item_id": x["id"], "qty": 1, "unit_price": 1}],
            },
        ),
        201,
    )
    r = a.post(f"{API}/purchase-orders/{po['id']}/receipts", json={"key": key(), "warehouse_id": wh["id"],
               "lines": [{"po_line_id": po["lines"][0]["id"], "qty": 1}]})  # fmt: skip
    assert r.status_code == 409 and "approved" in r.text


def test_received_serials_are_registered_once(team):
    a, b = team
    wh, sup = warehouse(a), supplier(a)
    cell = item(a, "CELL", kind="battery_unit", tracking="serial")
    po = approved_po(a, b, sup, [(cell, 2, 300)])
    line = po["lines"][0]["id"]
    body = {"key": key(), "warehouse_id": wh["id"], "lines": [{"po_line_id": line, "qty": 2,
            "units": [{"serial": "c-1", "supplier_serial": "XY001"}, {"serial": "c-2"}], "hold_serials": ["C-2"]}]}  # fmt: skip
    ok(a.post(f"{API}/purchase-orders/{po['id']}/receipts", json=body), 201)
    found = {
        s["serial"]: s for s in ok(a.get(f"{API}/serials", params={"item_id": cell["id"]}))["items"]
    }
    assert (
        found["C-1"]["status"] == "awaiting_inspection"
        and found["C-1"]["supplier_serial"] == "XY001"
    )
    assert found["C-2"]["status"] == "on_hold"
    po2 = approved_po(a, b, sup, [(cell, 1, 300)])
    dup = a.post(f"{API}/purchase-orders/{po2['id']}/receipts", json={"key": key(), "warehouse_id": wh["id"],
                 "lines": [{"po_line_id": po2["lines"][0]["id"], "qty": 1, "units": [{"serial": "C-1"}]}]})  # fmt: skip
    assert dup.status_code == 422 and "already registered" in dup.text
    wrong_count = a.post(f"{API}/purchase-orders/{po2['id']}/receipts", json={"key": key(), "warehouse_id": wh["id"],
                         "lines": [{"po_line_id": po2["lines"][0]["id"], "qty": 1}]})  # fmt: skip
    assert wrong_count.status_code == 422 and "one serial per unit" in wrong_count.text
    reconciled(a)
