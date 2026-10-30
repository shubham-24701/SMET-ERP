"""Regressions for the independent review of Phases 2–3 (2026-10-30). Each test failed before its fix."""

from datetime import date

from tests import test_assembly
from tests.conftest import API
from tests.ops import (
    ALL,
    approved_po,
    item,
    key,
    ok,
    reconciled,
    stock_in,
    summary,
    supplier,
    warehouse,
)
from tests.test_assembly import build, order

plant = test_assembly.plant  # the fixture: a model, its BOM and stock for two batteries


def test_assembled_batteries_are_never_bought(plant):  # H1
    a = plant["a"]
    sup = ok(a.get(f"{API}/parties?kind=supplier"))["items"][0]
    r = a.post(f"{API}/purchase-orders", json={"supplier_id": sup["id"], "lines": [
        {"item_id": plant["model"]["id"], "qty": 1, "unit_price": 1}]})  # fmt: skip
    assert r.status_code == 422 and "assembly order" in r.text
    pr = a.post(
        f"{API}/purchase-requests",
        json={"source": "other", "lines": [{"item_id": plant["model"]["id"], "qty": 1}]},
    )
    assert pr.status_code == 422


def test_a_count_cannot_put_a_found_battery_into_available_stock(plant):  # H2
    a, b, wh = plant["a"], plant["b"], plant["wh"]
    c = ok(a.post(f"{API}/counts", json={"location_id": wh["loc"]["available"]["id"]}), 201)
    c = ok(a.put(f"{API}/counts/{c['id']}/lines", json={"version": c["version"], "lines": [
        {"item_id": plant["model"]["id"], "serials_found": ["GHOST1"]}]}))  # fmt: skip
    c = ok(a.post(f"{API}/counts/{c['id']}/submit", json={"version": c["version"]}))
    adj = ok(b.get(f"{API}/adjustments/{c['adjustment_id']}"))
    r = b.post(f"{API}/adjustments/{adj['id']}/approve", json={"version": adj["version"]})
    assert r.status_code == 409 and "quarantine" in r.text
    assert ok(a.get(f"{API}/serials", params={"q": "GHOST1"}))["total"] == 0


def test_a_repeated_line_cannot_take_another_orders_material(plant):  # M3
    ao = order(plant, qty=1)
    body = {
        "key": key(),
        "lines": [
            {"item_id": plant["cable"]["id"], "qty": 1},
            {"item_id": plant["cable"]["id"], "qty": 1},
        ],
    }
    r = plant["a"].post(f"{API}/assembly-orders/{ao['id']}/issue", json=body)
    assert r.status_code == 422 and "only once" in r.text


def test_long_serials_and_repeated_supplier_serials_are_handled(plant, user_with):  # M4, M5
    a, b = plant["a"], plant["b"]
    ao = order(plant, qty=1)
    ok(a.post(f"{API}/assembly-orders/{ao['id']}/issue", json={"key": key(), "lines": [
        {"item_id": plant["cell"]["id"], "qty": 4, "serials": ["CL1", "CL2", "CL3", "CL4"]},
        {"item_id": plant["bms"]["id"], "qty": 1, "lot": "L1"}, {"item_id": plant["cable"]["id"], "qty": 2}]}))  # fmt: skip
    long = "X" * 60
    unit = ok(build(plant, ao, ["CL1", "CL2", "CL3", "CL4"], serial=long, cable=2), 201)
    r = user_with("quality_inspector").post(
        f"{API}/serials/{unit['id']}/inspect", json={"key": key(), "result": "rework"}
    )
    assert r.status_code == 201
    sup = supplier(a, "S9")
    po = approved_po(a, b, sup, [(plant["cell"], 2, 1)])
    line = po["lines"][0]["id"]
    r = a.post(f"{API}/purchase-orders/{po['id']}/receipts", json={"key": key(), "warehouse_id": plant["wh"]["id"],
               "lines": [{"po_line_id": line, "qty": 2, "units": [{"serial": "N1", "supplier_serial": "SAME"},
                                                                  {"serial": "N2", "supplier_serial": "SAME"}]}]})  # fmt: skip
    assert r.status_code == 422 and "Supplier serial SAME" in r.text


def test_whoever_edited_an_order_or_counted_cannot_approve_the_result(user_with):  # M6
    a, b = user_with(perms=ALL), user_with(perms=ALL)
    c3 = user_with(perms=ALL)
    wh, sup, x = warehouse(a), supplier(a), item(a, "WIRE")
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
    po = ok(b.put(f"{API}/purchase-orders/{po['id']}", json={"version": po["version"], "supplier_id": sup["id"],
                                                             "lines": [{"item_id": x["id"], "qty": 999, "unit_price": 999}]}))  # fmt: skip
    po = ok(a.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    assert (
        b.post(
            f"{API}/purchase-orders/{po['id']}/approve", json={"version": po["version"]}
        ).status_code
        == 403
    )
    ok(c3.post(f"{API}/purchase-orders/{po['id']}/approve", json={"version": po["version"]}))
    stock_in(a, c3, wh, sup, x, 5)
    count = ok(b.post(f"{API}/counts", json={"location_id": wh["loc"]["available"]["id"]}), 201)
    count = ok(
        a.put(
            f"{API}/counts/{count['id']}/lines",
            json={"version": count["version"], "lines": [{"item_id": x["id"], "counted": 50}]},
        )
    )
    count = ok(b.post(f"{API}/counts/{count['id']}/submit", json={"version": count["version"]}))
    adj = ok(a.get(f"{API}/adjustments/{count['adjustment_id']}"))
    assert (
        a.post(
            f"{API}/adjustments/{adj['id']}/approve", json={"version": adj["version"]}
        ).status_code
        == 403
    )
    saved = ok(c3.get(f"{API}/audit", params={"action": "count.save"}))["items"]
    assert (
        saved and saved[0]["changes"]["lines"][0]["counted"] == "50"
    )  # what was counted is audited


def test_a_correction_sent_twice_is_recorded_once(user_with):  # M7
    a = user_with(perms=ALL)
    wh, x = warehouse(a), item(a, "TIE")
    body = {
        "key": key(),
        "reason": "Found two",
        "lines": [
            {"item_id": x["id"], "location_id": wh["loc"]["available"]["id"], "qty_change": 2}
        ],
    }
    first = ok(a.post(f"{API}/adjustments", json=body), 201)
    again = ok(a.post(f"{API}/adjustments", json=body), 201)
    assert again["duplicate"] and again["number"] == first["number"]
    assert ok(a.get(f"{API}/adjustments", params={"status": "pending"}))["total"] == 1


def test_the_assembly_floor_changes_only_through_orders(plant):  # M8
    a = plant["a"]
    ao = order(plant, qty=1)
    ok(a.post(f"{API}/assembly-orders/{ao['id']}/issue", json={"key": key(), "lines": [
        {"item_id": plant["cell"]["id"], "qty": 4, "serials": ["CL1", "CL2", "CL3", "CL4"]}]}))  # fmt: skip
    floor = plant["wh"]["loc"]["assembly"]["id"]
    r = a.post(f"{API}/adjustments", json={"key": key(), "reason": "Lost one", "lines": [
        {"item_id": plant["cell"]["id"], "location_id": floor, "serials_out": ["CL1"]}]})  # fmt: skip
    assert r.status_code == 422 and "assembly orders" in r.text
    assert a.post(f"{API}/counts", json={"location_id": floor}).status_code == 422
    # A unit issued to an order isn't written off through a count of the store either.
    cells = [
        m
        for m in ok(a.get(f"{API}/assembly-orders/{ao['id']}"))["materials"]
        if m["item"]["code"] == "CELL-3.2V"
    ]
    assert cells[0]["on_floor"] == 4
    reconciled(a)


def test_goods_not_replaced_are_no_longer_outstanding(user_with):  # L15
    a, b = user_with(perms=ALL), user_with(perms=ALL)
    wh, sup, x = warehouse(a), supplier(a), item(a, "GLAND")
    po = approved_po(a, b, sup, [(x, 10, 1)])
    line = po["lines"][0]["id"]
    ok(a.post(f"{API}/purchase-orders/{po['id']}/receipts", json={"key": key(), "warehouse_id": wh["id"],
              "lines": [{"po_line_id": line, "qty": 10, "accept": 8, "reject": 2}]}), 201)  # fmt: skip
    ok(a.post(f"{API}/purchase-orders/{po['id']}/returns", json={"key": key(), "warehouse_id": wh["id"], "reason": "Cracked",
              "replacement_expected": False, "lines": [{"po_line_id": line, "qty": 2}]}), 201)  # fmt: skip
    po = ok(a.get(f"{API}/purchase-orders/{po['id']}"))
    assert po["lines"][0]["qty_outstanding"] == 0 and po["status"] == "received"
    assert summary(a, "GLAND")["on_order"] == 0


def test_repeats_return_the_same_answer_and_numbering_skips_taken_serials(plant):  # L13, L17
    a = plant["a"]
    body = {
        "key": key(),
        "item_id": plant["cable"]["id"],
        "qty": 1,
        "ref_type": "project",
        "ref_label": "Mall",
    }
    first = ok(a.post(f"{API}/reservations", json=body), 201)
    again = ok(a.post(f"{API}/reservations", json=body), 201)
    assert again["item"]["code"] == first["item"]["code"] and again["duplicate"]
    ok(
        a.post(
            f"{API}/reservations/{first['id']}/release",
            json={"version": first["version"], "reason": "test"},
        )
    )
    ao = order(plant, qty=2)
    ok(a.post(f"{API}/assembly-orders/{ao['id']}/issue", json={"key": key(), "lines": [
        {"item_id": plant["cell"]["id"], "qty": 8, "serials": [f"CL{n}" for n in range(1, 9)]},
        {"item_id": plant["bms"]["id"], "qty": 2, "lot": "L1"}, {"item_id": plant["cable"]["id"], "qty": 4}]}))  # fmt: skip
    taken = f"SMET-{date.today().year}-00001"
    ok(build(plant, ao, ["CL1", "CL2", "CL3", "CL4"], serial=taken, cable=2), 201)
    auto = ok(build(plant, ao, ["CL5", "CL6", "CL7", "CL8"], cable=2), 201)
    assert auto["serial"] == f"SMET-{date.today().year}-00002"


def test_a_model_with_a_bill_of_materials_keeps_its_kind(plant):  # L16
    a, m = plant["a"], plant["model"]
    r = a.patch(
        f"{API}/items/{m['id']}",
        json={"version": m["version"], "assembled": False, "tracking": "none"},
    )
    assert r.status_code == 422 and "already used" in r.text
