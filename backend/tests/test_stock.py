"""Stock integrity: never negative, reservations respected under concurrency, corrections need a
second person, counts, the append-only ledger, reconciliation."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.db import SessionLocal
from tests.conftest import API
from tests.ops import ALL, item, key, ok, reconciled, stock_in, summary, supplier, warehouse


@pytest.fixture
def setup(user_with):
    a, b = (
        user_with(perms=ALL, email="maker@example.test"),
        user_with(perms=ALL, email="checker@example.test"),
    )
    wh, sup = warehouse(a), supplier(a)
    return a, b, wh, sup


def test_stock_never_goes_negative(setup):
    a, b, wh, sup = setup
    x = item(a, "BOLT")
    stock_in(a, b, wh, sup, x, 5)
    rack = ok(
        a.post(
            f"{API}/warehouses/{wh['id']}/locations",
            json={"code": "R1", "name": "Rack 1", "purpose": "available"},
        ),
        201,
    )
    move = {
        "key": key(),
        "item_id": x["id"],
        "from_location_id": wh["loc"]["available"]["id"],
        "to_location_id": rack["id"],
    }
    r = a.post(f"{API}/stock/transfers", json=move | {"qty": 6})
    assert r.status_code == 409 and "Not enough BOLT" in r.text
    ok(a.post(f"{API}/stock/transfers", json=move | {"qty": 5}), 201)
    again = a.post(f"{API}/stock/transfers", json=move | {"key": key(), "qty": 1})
    assert again.status_code == 409
    s = summary(a, "BOLT")
    assert (s["available"], s["on_hand"]) == (5, 5)
    reconciled(a)


def test_transfers_never_change_a_status(setup):
    a, b, wh, sup = setup
    x = item(a, "NUT")
    stock_in(a, b, wh, sup, x, 3)
    r = a.post(f"{API}/stock/transfers", json={"key": key(), "item_id": x["id"], "qty": 1,
               "from_location_id": wh["loc"]["available"]["id"], "to_location_id": wh["loc"]["quarantine"]["id"]})  # fmt: skip
    assert r.status_code == 422 and "within one use" in r.text


def test_reservations_hold_stock_and_the_last_units_go_to_one_person(setup, user_with):
    a, b, wh, sup = setup
    x = item(a, "INVERTER")
    stock_in(a, b, wh, sup, x, 3)
    r = ok(a.post(f"{API}/reservations", json={"key": key(), "item_id": x["id"], "qty": 2, "ref_type": "project",
                                               "ref_label": "Hospital UPS"}), 201)  # fmt: skip
    s = summary(a, "INVERTER")
    assert (s["available"], s["reserved"], s["free"]) == (3, 2, 1)
    # Two people reserve the last free unit at the same moment: one gets it.
    people = [user_with("warehouse_officer") for _ in range(4)]

    def grab(c):
        return c.post(f"{API}/reservations", json={"key": key(), "item_id": x["id"], "qty": 1, "ref_type": "other",
                                                   "ref_label": "rush"}).status_code  # fmt: skip

    with ThreadPoolExecutor(4) as pool:
        codes = sorted(pool.map(grab, people))
    assert codes == [201, 409, 409, 409]
    # A correction can't take promised stock either.
    adj = ok(
        a.post(
            f"{API}/adjustments",
            json={
                "reason": "Damaged in storage",
                "lines": [
                    {
                        "item_id": x["id"],
                        "location_id": wh["loc"]["available"]["id"],
                        "qty_change": -1,
                    }
                ],
            },
        ),
        201,
    )
    blocked = b.post(f"{API}/adjustments/{adj['id']}/approve", json={"version": adj["version"]})
    assert blocked.status_code == 409 and "free" in blocked.text
    ok(
        a.post(
            f"{API}/reservations/{r['id']}/release",
            json={"version": r["version"], "reason": "Project moved"},
        )
    )
    assert summary(a, "INVERTER")["free"] == 2


def test_corrections_need_a_second_person_and_a_reason(setup):
    a, b, wh, sup = setup
    x = item(a, "TAPE")
    stock_in(a, b, wh, sup, x, 10)
    store = wh["loc"]["available"]["id"]
    no_reason = a.post(
        f"{API}/adjustments",
        json={
            "reason": "",
            "lines": [{"item_id": x["id"], "location_id": store, "qty_change": -2}],
        },
    )
    assert no_reason.status_code == 422
    adj = ok(
        a.post(
            f"{API}/adjustments",
            json={
                "reason": "Two rolls damaged",
                "lines": [{"item_id": x["id"], "location_id": store, "qty_change": -2}],
            },
        ),
        201,
    )
    assert summary(a, "TAPE")["available"] == 10  # nothing changes until approved
    assert (
        a.post(
            f"{API}/adjustments/{adj['id']}/approve", json={"version": adj["version"]}
        ).status_code
        == 403
    )
    done = ok(
        b.post(
            f"{API}/adjustments/{adj['id']}/approve",
            json={"version": adj["version"], "reason": "Seen"},
        )
    )
    assert done["status"] == "approved" and summary(a, "TAPE")["available"] == 8
    twice = b.post(f"{API}/adjustments/{adj['id']}/approve", json={"version": done["version"]})
    assert twice.status_code == 409 and summary(a, "TAPE")["available"] == 8
    reconciled(a)


def test_a_count_corrects_against_the_stock_at_approval(setup):
    a, b, wh, sup = setup
    x = item(a, "SCREW")
    cell = item(a, "CELL", kind="battery_unit", tracking="serial")
    stock_in(a, b, wh, sup, x, 20)
    stock_in(a, b, wh, sup, cell, 3, serials=["S1", "S2", "S3"])
    c = ok(a.post(f"{API}/counts", json={"location_id": wh["loc"]["available"]["id"]}), 201)
    lines = {ln["item"]["code"]: ln for ln in c["lines"]}
    assert lines["SCREW"]["expected"] == 20 and lines["CELL"]["serials_expected"] == [
        "S1",
        "S2",
        "S3",
    ]
    c = ok(
        a.put(
            f"{API}/counts/{c['id']}/lines",
            json={
                "version": c["version"],
                "lines": [
                    {"item_id": x["id"], "counted": 18},
                    {"item_id": cell["id"], "serials_found": ["S1", "S3", "S9"]},
                ],
            },
        )
    )
    c = ok(a.post(f"{API}/counts/{c['id']}/submit", json={"version": c["version"]}))
    assert c["status"] == "submitted" and c["adjustment_id"]
    # Five screws go out before the count's correction is approved: the count still wins (18 found).
    used = ok(
        a.post(
            f"{API}/adjustments",
            json={
                "reason": "Scrapped",
                "lines": [
                    {
                        "item_id": x["id"],
                        "location_id": wh["loc"]["available"]["id"],
                        "qty_change": -5,
                    }
                ],
            },
        ),
        201,
    )
    ok(b.post(f"{API}/adjustments/{used['id']}/approve", json={"version": used["version"]}))
    assert summary(a, "SCREW")["available"] == 15
    adj = ok(a.get(f"{API}/adjustments/{c['adjustment_id']}"))
    ok(b.post(f"{API}/adjustments/{adj['id']}/approve", json={"version": adj["version"]}))
    assert summary(a, "SCREW")["available"] == 18
    serials = {
        s["serial"]: s["status"]
        for s in ok(a.get(f"{API}/serials", params={"item_id": cell["id"]}))["items"]
    }
    assert serials == {"S1": "available", "S2": "written_off", "S3": "available", "S9": "available"}
    reconciled(a)


def test_the_ledger_cannot_be_rewritten_by_the_app():
    with SessionLocal() as db:
        for stmt in (
            "UPDATE stock_moves SET qty = 0",
            "DELETE FROM stock_moves",
            "TRUNCATE stock_moves",
        ):
            with pytest.raises(Exception, match="permission denied|append-only"):
                db.execute(text(stmt))
            db.rollback()


def test_movements_export_and_low_stock(setup):
    a, b, wh, sup = setup
    x = item(a, "RELAY", min_stock=10)
    stock_in(a, b, wh, sup, x, 4)
    low = ok(a.get(f"{API}/stock/summary", params={"low": True}))["items"]
    assert [(r["code"], r["suggested"]) for r in low] == [("RELAY", 6)]
    csv = a.get(f"{API}/stock/moves/export")
    assert csv.status_code == 200 and "RELAY" in csv.text and "receipt" in csv.text
    moves = ok(a.get(f"{API}/stock/moves", params={"item_id": x["id"]}))
    assert [m["kind"] for m in moves["items"]] == ["accept", "receipt"]
