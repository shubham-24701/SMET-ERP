"""Phase 3: BOM versions, assembly orders, serial numbers with component links, and the quality
gate that alone puts a finished battery into available stock."""

import pytest

from tests.conftest import API
from tests.ops import ALL, item, key, ok, reconciled, stock_in, summary, supplier, warehouse


@pytest.fixture
def plant(user_with):
    """A model built from 4 serialised cells, 1 BMS (lot) and 2 m of cable, with stock for 2."""
    a, b = (
        user_with(perms=ALL, email="maker@example.test"),
        user_with(perms=ALL, email="checker@example.test"),
    )
    wh, sup = warehouse(a), supplier(a)
    model = item(a, "SMET-48V-100", kind="battery_model", tracking="serial", assembled=True,
                 capacity_ah=100, voltage_v=48, chemistry="LiFePO4")  # fmt: skip
    cell = item(a, "CELL-3.2V", kind="battery_unit", tracking="serial")
    bms = item(a, "BMS-16S", tracking="lot")
    cable = a.post(
        f"{API}/items",
        json={"code": "CABLE-25", "name": "Cable", "kind": "material", "unit_code": "M"},
    ).json()
    bom = ok(a.post(f"{API}/boms", json={"item_id": model["id"], "lines": [
        {"component_id": cell["id"], "qty": 4}, {"component_id": bms["id"], "qty": 1},
        {"component_id": cable["id"], "qty": 2}]}), 201)  # fmt: skip
    ok(a.post(f"{API}/boms/{bom['id']}/activate"))
    stock_in(a, b, wh, sup, cell, 8, serials=[f"CL{n}" for n in range(1, 9)])
    stock_in(a, b, wh, sup, bms, 2, lot="L1")
    stock_in(a, b, wh, sup, cable, 5)
    return {
        "a": a,
        "b": b,
        "wh": wh,
        "model": model,
        "cell": cell,
        "bms": bms,
        "cable": cable,
        "bom": bom,
    }


def quality(c, model, confirmed=True):
    return ok(c.put(f"{API}/items/{model['id']}/quality", json={
        "parameters": [{"name": "Open-circuit voltage", "unit": "V", "min_value": 51, "max_value": 54.4, "confirmed": confirmed},
                       {"name": "Internal resistance", "unit": "mΩ", "required": False}],
        "checklist": [{"text": "Terminals torqued"}, {"text": "Label applied"}]}))  # fmt: skip


def order(p, qty=2):
    ao = ok(p["a"].post(f"{API}/assembly-orders", json={"item_id": p["model"]["id"], "qty_planned": qty,
                                                         "warehouse_id": p["wh"]["id"]}), 201)  # fmt: skip
    return ok(
        p["a"].post(f"{API}/assembly-orders/{ao['id']}/release", json={"version": ao["version"]})
    )


def issue_all(p, ao):
    ok(p["a"].post(f"{API}/assembly-orders/{ao['id']}/issue", json={"key": key(), "lines": [
        {"item_id": p["cell"]["id"], "qty": 8, "serials": [f"CL{n}" for n in range(1, 9)]},
        {"item_id": p["bms"]["id"], "qty": 2, "lot": "L1"},
        {"item_id": p["cable"]["id"], "qty": 5}]}))  # fmt: skip


def build(p, ao, cells, serial=None, cable=2.5):
    body = {"key": key(), "parts": [{"item_id": p["cell"]["id"], "qty": 4, "serials": cells},
                                    {"item_id": p["bms"]["id"], "qty": 1, "lot": "L1"},
                                    {"item_id": p["cable"]["id"], "qty": cable}]}  # fmt: skip
    if serial:
        body["serial"] = serial
    return p["a"].post(f"{API}/assembly-orders/{ao['id']}/units", json=body)


def test_an_active_bom_never_changes(plant):
    a, b = plant["a"], plant["bom"]
    r = a.put(
        f"{API}/boms/{b['id']}", json={"lines": [{"component_id": plant["cell"]["id"], "qty": 5}]}
    )
    assert r.status_code == 409 and "new version" in r.text
    v2 = ok(
        a.post(
            f"{API}/boms",
            json={
                "item_id": plant["model"]["id"],
                "lines": [{"component_id": plant["cell"]["id"], "qty": 5}],
            },
        ),
        201,
    )
    assert v2["version"] == 2 and v2["status"] == "draft"
    ok(a.post(f"{API}/boms/{v2['id']}/activate"))
    assert ok(a.get(f"{API}/boms/{b['id']}"))["status"] == "retired"
    selfish = a.post(
        f"{API}/boms",
        json={
            "item_id": plant["model"]["id"],
            "lines": [{"component_id": plant["model"]["id"], "qty": 1}],
        },
    )
    assert selfish.status_code == 422


def test_release_reserves_components_or_lists_what_is_short(plant):
    a = plant["a"]
    big = ok(a.post(f"{API}/assembly-orders", json={"item_id": plant["model"]["id"], "qty_planned": 3,
                                                   "warehouse_id": plant["wh"]["id"]}), 201)  # fmt: skip
    r = a.post(f"{API}/assembly-orders/{big['id']}/release", json={"version": big["version"]})
    assert r.status_code == 409 and "CELL-3.2V short by 4" in r.text
    pr = ok(a.post(f"{API}/assembly-orders/{big['id']}/shortage-request"), 201)
    assert pr["source"] == "assembly" and pr["ref"] == big["number"]
    assert {(ln["item"]["code"], ln["qty"]) for ln in pr["lines"]} == {
        ("CELL-3.2V", 4),
        ("BMS-16S", 1),
        ("CABLE-25", 1),
    }
    order(plant)
    s = summary(a, "CELL-3.2V")
    assert (s["available"], s["reserved"], s["free"]) == (8, 8, 0)


def test_from_components_to_a_passed_battery(plant, user_with):
    a, model = plant["a"], plant["model"]
    ao = order(plant)
    issue_all(plant, ao)
    assert summary(a, "CELL-3.2V")["assembly"] == 8 and summary(a, "CELL-3.2V")["reserved"] == 0
    tech = user_with("assembly_technician")
    unit = ok(tech.post(f"{API}/assembly-orders/{ao['id']}/units", json={"key": key(), "parts": [
        {"item_id": plant["cell"]["id"], "qty": 4, "serials": ["CL1", "CL2", "CL3", "CL4"]},
        {"item_id": plant["bms"]["id"], "qty": 1, "lot": "L1"},
        {"item_id": plant["cable"]["id"], "qty": 2.5}]}), 201)  # fmt: skip
    assert unit["serial"].startswith("SMET-20")
    reused = build(plant, ao, ["CL1", "CL5", "CL6", "CL7"])
    assert reused.status_code == 409 and "CL1 is consumed" in reused.text
    record = ok(a.get(f"{API}/serials/{unit['id']}"))
    assert record["status"] == "under_assembly"
    assert sorted(c["serial"] for c in record["components"] if c["serial"]) == [
        "CL1",
        "CL2",
        "CL3",
        "CL4",
    ]
    parts = {c["item"]["code"]: c for c in record["components"] if c["item"]["code"] != "CELL-3.2V"}
    assert (
        parts["CABLE-25"]["qty"] == 2.5 and parts["BMS-16S"]["lot"] == "L1"
    )  # actual use, not the BOM's 2 m
    cell_record = ok(a.get(f"{API}/serials", params={"q": "CL1"}))["items"][0]
    assert ok(a.get(f"{API}/serials/{cell_record['id']}"))["built_into"]["serial"] == unit["serial"]
    # No transfer, correction or shortcut puts an uninspected battery into the store.
    floor = plant["wh"]["loc"]["assembly"]["id"]
    sneak = a.post(f"{API}/stock/transfers", json={"key": key(), "item_id": model["id"], "qty": 1, "serials": [unit["serial"]],
                   "from_location_id": floor, "to_location_id": plant["wh"]["loc"]["available"]["id"]})  # fmt: skip
    assert sneak.status_code == 422
    inspector = user_with("quality_inspector")
    url = f"{API}/serials/{unit['id']}/inspect"
    no_checks = inspector.post(url, json={"key": key(), "result": "passed"})
    assert (
        no_checks.status_code == 422 and "set up" in no_checks.text
    )  # nothing to inspect against yet
    q = quality(inspector, model)
    volt, ir = q["parameters"][0]["id"], q["parameters"][1]["id"]
    checks = [{"item_id": c["id"], "done": True} for c in q["checklist"]]
    low = inspector.post(url, json={"key": key(), "result": "passed", "checklist": checks,
                                    "readings": [{"parameter_id": volt, "value": 49.9}]})  # fmt: skip
    assert low.status_code == 422 and "outside the confirmed limits" in low.text
    half = inspector.post(url, json={"key": key(), "result": "passed", "checklist": checks[:1],
                                     "readings": [{"parameter_id": volt, "value": 53.1}]})  # fmt: skip
    assert half.status_code == 422 and "Not done: Label applied" in half.text
    ok(inspector.post(url, json={"key": key(), "result": "rework", "observations": "Cell 3 low",
                                 "readings": [{"parameter_id": volt, "value": 49.9}]}), 201)  # fmt: skip
    passed = ok(inspector.post(url, json={"key": key(), "result": "passed", "checklist": checks,
                                          "readings": [{"parameter_id": volt, "value": 53.1}, {"parameter_id": ir, "value": 0.8}]}), 201)  # fmt: skip
    assert passed["status"] == "available"
    record = ok(a.get(f"{API}/serials/{unit['id']}"))
    assert record["purpose"] == "available" and [x["result"] for x in record["inspections"]] == [
        "rework",
        "passed",
    ]
    reading = record["inspections"][1]["readings"][0]
    assert (reading["min"], reading["max"], reading["within"]) == (
        "51.0000",
        "54.4000",
        True,
    )  # limits as they were
    again = inspector.post(url, json={"key": key(), "result": "passed", "checklist": checks})
    assert again.status_code == 409, again.text
    # Second battery goes on hold; the order can't complete while materials are on the floor.
    second = ok(build(plant, ao, ["CL5", "CL6", "CL7", "CL8"], serial="smet-test-2", cable=2), 201)
    assert second["serial"] == "SMET-TEST-2"
    assert (
        build(plant, ao, ["CL5", "CL6", "CL7", "CL8"]).status_code == 409
    )  # both planned batteries exist
    ok(
        inspector.post(
            f"{API}/serials/{second['id']}/inspect",
            json={"key": key(), "result": "on_hold", "observations": "Await BMS firmware"},
        ),
        201,
    )
    full = ok(a.get(f"{API}/assembly-orders/{ao['id']}"))
    assert full["units_by_status"] == {"available": 1, "on_hold": 1}
    stuck = a.post(f"{API}/assembly-orders/{ao['id']}/complete", json={"version": full["version"]})
    assert stuck.status_code == 409 and "on the assembly floor" in stuck.text
    ok(
        a.post(
            f"{API}/assembly-orders/{ao['id']}/return",
            json={"key": key(), "lines": [{"item_id": plant["cable"]["id"], "qty": 0.5}]},
        )
    )
    too_much = a.post(
        f"{API}/assembly-orders/{ao['id']}/return",
        json={"key": key(), "lines": [{"item_id": plant["cable"]["id"], "qty": 1}]},
    )
    assert too_much.status_code == 409
    full = ok(a.get(f"{API}/assembly-orders/{ao['id']}"))
    done = ok(
        a.post(f"{API}/assembly-orders/{ao['id']}/complete", json={"version": full["version"]})
    )
    assert done["status"] == "completed"
    s = summary(a, "SMET-48V-100")
    assert (s["available"], s["quarantine"], s["assembly"]) == (1, 1, 0)
    c = summary(a, "CABLE-25")
    assert (c["available"], c["assembly"], c["reserved"]) == (0.5, 0, 0)
    assert summary(a, "CELL-3.2V")["on_hand"] == 0
    reconciled(a)


def test_unconfirmed_limits_are_recorded_not_applied(plant, user_with):
    ao = order(plant, qty=1)
    ok(plant["a"].post(f"{API}/assembly-orders/{ao['id']}/issue", json={"key": key(), "lines": [
        {"item_id": plant["cell"]["id"], "qty": 4, "serials": ["CL1", "CL2", "CL3", "CL4"]},
        {"item_id": plant["bms"]["id"], "qty": 1, "lot": "L1"}, {"item_id": plant["cable"]["id"], "qty": 2}]}))  # fmt: skip
    unit = ok(plant["a"].post(f"{API}/assembly-orders/{ao['id']}/units", json={"key": key(), "parts": [
        {"item_id": plant["cell"]["id"], "qty": 4, "serials": ["CL1", "CL2", "CL3", "CL4"]},
        {"item_id": plant["bms"]["id"], "qty": 1, "lot": "L1"}, {"item_id": plant["cable"]["id"], "qty": 2}]}), 201)  # fmt: skip
    inspector = user_with("quality_inspector")
    q = quality(inspector, plant["model"], confirmed=False)
    checks = [{"item_id": c["id"], "done": True} for c in q["checklist"]]
    r = ok(inspector.post(f"{API}/serials/{unit['id']}/inspect", json={"key": key(), "result": "passed", "checklist": checks,
           "readings": [{"parameter_id": q["parameters"][0]["id"], "value": 12}]}), 201)  # fmt: skip
    reading = ok(plant["a"].get(f"{API}/serials/{unit['id']}"))["inspections"][0]["readings"][0]
    assert (
        r["status"] == "available"
        and reading["limits_confirmed"] is False
        and reading["within"] is None
    )


def test_only_issued_material_can_be_consumed(plant):
    ao = order(plant, qty=1)
    r = build(plant, {"id": ao["id"]}, ["CL1", "CL2", "CL3", "CL4"])
    assert r.status_code == 409  # released, nothing issued yet
    ok(plant["a"].post(f"{API}/assembly-orders/{ao['id']}/issue", json={"key": key(), "lines": [
        {"item_id": plant["cell"]["id"], "qty": 4, "serials": ["CL1", "CL2", "CL3", "CL4"]},
        {"item_id": plant["bms"]["id"], "qty": 1, "lot": "L1"}, {"item_id": plant["cable"]["id"], "qty": 2}]}))  # fmt: skip
    r = build(plant, ao, ["CL1", "CL2", "CL3", "CL4"])  # wants 2.5 m of cable, 2 m issued
    assert r.status_code == 409 and "issue more first" in r.text
    reconciled(plant["a"])
