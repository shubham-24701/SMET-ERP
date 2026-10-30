"""Regressions for the independent review of Phase 4 (2026-10-30). Each test failed before its fix."""

from datetime import date

import pytest

from app.projects import evaluate
from tests import test_projects, test_sales
from tests.conftest import API
from tests.ops import ALL, key, ok, reconciled, summary
from tests.test_sales import quote, to_order

shop = test_sales.shop  # fixtures: a customer, units U1–U4 and 20 lugs in stock
proj = test_projects.proj  # a project for Desert Telecom, 3 racks in stock


def lug_order(s, n):
    return to_order(
        s,
        quote(
            s, [{"line_type": "accessory", "item_id": s["lug"]["id"], "qty": n, "unit_price": 5}]
        ),
    )[1]


def test_a_dispatch_cancelled_after_its_order_closed_returns_goods_to_free_stock(
    shop,
):  # H1, the reviewer's case
    a, wh = shop["a"], shop["wh"]
    so = lug_order(shop, 15)
    ok(a.post(f"{API}/sales-orders/{so['id']}/reserve", json={"key": key()}))
    d = ok(a.post(f"{API}/dispatches", json={"order_id": so["id"], "warehouse_id": wh["id"],
              "lines": [{"order_line_id": so["lines"][0]["id"], "qty": 10}]}), 201)  # fmt: skip
    ok(a.post(f"{API}/dispatches/{d['id']}/send", json={"version": d["version"], "key": key()}))
    so = ok(a.get(f"{API}/sales-orders/{so['id']}"))
    ok(
        a.post(
            f"{API}/sales-orders/{so['id']}/close",
            json={"version": so["version"], "reason": "Rest not needed"},
        )
    )
    d = ok(a.get(f"{API}/dispatches/{d['id']}"))
    ok(
        a.post(
            f"{API}/dispatches/{d['id']}/cancel",
            json={"version": d["version"], "key": key(), "reason": "Returned at the gate"},
        )
    )
    s = summary(a, "LUG")
    assert (s["reserved"], s["free"]) == (0, 20)
    reconciled(a)


def test_a_project_reservation_is_shared_once_across_its_orders(proj, admin):  # H2, H3
    pm, eng, p = proj["pm"], proj["eng"], proj["p"]
    sheet = ok(
        pm.post(
            f"{API}/projects/{p['id']}/sheets",
            json={"lines": [{"item_id": proj["rack"]["id"], "qty": 3}]},
        ),
        201,
    )
    sheet = ok(pm.post(f"{API}/sheets/{sheet['id']}/submit", json={"version": sheet["version"]}))
    ok(eng.post(f"{API}/sheets/{sheet['id']}/approve", json={"version": sheet["version"]}))
    ok(pm.post(f"{API}/projects/{p['id']}/reserve", json={"key": key()}))
    assert summary(pm, "RACK")["reserved"] == 3
    orders = []
    for _ in range(2):
        qt = ok(pm.post(f"{API}/quotations", json={"customer_id": proj["cust"]["id"], "project_id": p["id"], "lines": [
            {"line_type": "accessory", "item_id": proj["rack"]["id"], "qty": 3, "unit_price": 100}]}), 201)  # fmt: skip
        v = lambda q: q["revisions"][0]["version"]  # noqa: E731
        qt = ok(pm.post(f"{API}/quotations/{qt['id']}/submit", json={"version": v(qt)}))
        qt = ok(eng.post(f"{API}/quotations/{qt['id']}/approve", json={"version": v(qt)}))
        qt = ok(
            pm.post(
                f"{API}/quotations/{qt['id']}/answer",
                json={"version": v(qt), "accepted": True, "by": "Buyer", "on": str(date.today())},
            )
        )
        orders.append(
            ok(pm.post(f"{API}/quotations/{qt['id']}/convert", json={"version": v(qt)}), 201)
        )
    shorts = [ok(pm.get(f"{API}/sales-orders/{o['id']}"))["lines"][0]["short"] for o in orders]
    assert shorts == [0, 3]  # the first order gets the project's three racks, the second is short
    # Reserving for an order of the project doesn't make the project reserve the same racks again.
    ok(pm.post(f"{API}/projects/{p['id']}/reserve", json={"key": key()}))
    assert summary(pm, "RACK")["reserved"] == 3


def test_a_dispatch_for_an_unknown_order_is_refused_cleanly(shop):  # M4
    r = shop["a"].post(f"{API}/dispatches", json={"order_id": "00000000-0000-0000-0000-000000000001", "warehouse_id": shop["wh"]["id"],
                       "lines": [{"order_line_id": "00000000-0000-0000-0000-000000000002", "qty": 1}]})  # fmt: skip
    assert r.status_code == 404


def test_odd_but_valid_input_answers_clearly_not_with_500(proj):  # M5, M6, M7, M8
    pm, p = proj["pm"], proj["p"]
    for bad in ("ceil + x", "x * 9e999", "1" * 400):
        with pytest.raises(Exception, match="(Unknown input|below 10|Not a valid)"):
            evaluate(bad, {"x": 1})
    with pytest.raises(Exception, match="too large"):
        evaluate(
            "ceil(x * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12 * 1e12)",
            {"x": 1},
        )
    rule = {"inputs": [{"name": "x", "label": "X"}], "expression": "x"}
    ok(pm.post(f"{API}/sizing-rules", json={"name": "Rule A"} | rule), 201)
    b = ok(pm.post(f"{API}/sizing-rules", json={"name": "Rule B"} | rule), 201)
    r = pm.put(
        f"{API}/sizing-rules/{b['id']}", json={"name": "Rule A", "version": b["version"]} | rule
    )
    assert r.status_code == 422 and "exists" in r.text
    assert (
        pm.post(
            f"{API}/sizing-rules",
            json={"name": "C", "inputs": [{"name": "ceil", "label": "C"}], "expression": "ceil"},
        ).status_code
        == 422
    )
    assert (
        pm.patch(
            f"{API}/projects/{p['id']}", json={"version": p["version"], "name": None}
        ).status_code
        == 200
    )
    ok(pm.post(f"{API}/projects/{p['id']}/tasks", json={"title": "Survey"}), 201)
    t = ok(pm.get(f"{API}/projects/{p['id']}"))["tasks"][0]
    assert (
        pm.patch(
            f"{API}/tasks/{t['id']}", json={"version": t["version"], "status": None}
        ).status_code
        == 200
    )


def test_whoever_shaped_a_quotation_cannot_approve_its_next_revision(shop, user_with):  # L10
    a, b = shop["a"], shop["b"]
    c = user_with(perms=ALL)
    qt = quote(shop)  # a drafts
    qt = ok(
        c.post(
            f"{API}/quotations/{qt['id']}/submit", json={"version": qt["revisions"][0]["version"]}
        )
    )
    qt = ok(
        b.post(
            f"{API}/quotations/{qt['id']}/reject",
            json={"version": qt["revisions"][0]["version"], "reason": "Price"},
        )
    )
    qt = ok(
        c.post(
            f"{API}/quotations/{qt['id']}/revise", json={"version": qt["revisions"][0]["version"]}
        ),
        201,
    )
    qt = ok(
        c.post(
            f"{API}/quotations/{qt['id']}/submit", json={"version": qt["revisions"][0]["version"]}
        )
    )
    assert (
        a.post(
            f"{API}/quotations/{qt['id']}/approve", json={"version": qt["revisions"][0]["version"]}
        ).status_code
        == 403
    )
    ok(
        b.post(
            f"{API}/quotations/{qt['id']}/approve", json={"version": qt["revisions"][0]["version"]}
        )
    )


def test_an_older_sheet_cannot_replace_a_newer_approved_one(proj):  # L11
    pm, eng, p = proj["pm"], proj["eng"], proj["p"]
    sheets = []
    for n in (2, 3):
        s = ok(
            pm.post(
                f"{API}/projects/{p['id']}/sheets",
                json={"lines": [{"item_id": proj["rack"]["id"], "qty": n}]},
            ),
            201,
        )
        sheets.append(ok(pm.post(f"{API}/sheets/{s['id']}/submit", json={"version": s["version"]})))
    ok(eng.post(f"{API}/sheets/{sheets[1]['id']}/approve", json={"version": sheets[1]["version"]}))
    r = eng.post(f"{API}/sheets/{sheets[0]['id']}/approve", json={"version": sheets[0]["version"]})
    assert r.status_code == 409 and "already approved" in r.text


def test_refused_serials_are_named_not_counted(shop):  # L14
    a, wh = shop["a"], shop["wh"]
    _, so = to_order(shop)
    line = next(ln for ln in so["lines"] if ln["line_type"] == "battery")["id"]
    d = ok(
        a.post(
            f"{API}/dispatches",
            json={
                "order_id": so["id"],
                "warehouse_id": wh["id"],
                "lines": [{"order_line_id": line, "qty": 1, "serials": ["U1"]}],
            },
        ),
        201,
    )
    ok(a.post(f"{API}/dispatches/{d['id']}/send", json={"version": d["version"], "key": key()}))
    d = ok(a.get(f"{API}/dispatches/{d['id']}"))
    r = a.post(f"{API}/dispatches/{d['id']}/deliver", json={"version": d["version"], "key": key(), "received_by": "X",
               "delivered_on": str(date.today()), "lines": [{"line_id": d["lines"][0]["id"], "qty_rejected": 1}]})  # fmt: skip
    assert r.status_code == 422 and "name the refused serial" in r.text
