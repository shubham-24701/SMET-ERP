"""Phase 4 projects: sites and surveys, sizing rules that SMET supplies (never invented), requirement
sheets approved by an engineer, the material list, changes, and links to stock, purchase and sales."""

import pytest

from app.projects import evaluate
from tests.conftest import API
from tests.ops import ALL, item, key, ok, stock_in, summary, supplier, warehouse


@pytest.fixture
def proj(user_with):
    pm, eng = (
        user_with(perms=ALL, email="pm@example.test"),
        user_with(perms=ALL, email="engineer@example.test"),
    )
    wh, sup = warehouse(pm), supplier(pm)
    cust = ok(
        pm.post(
            f"{API}/parties", json={"code": "TEL", "name": "Desert Telecom", "kinds": ["customer"]}
        ),
        201,
    )
    unit = item(pm, "UNIT-2V", kind="battery_unit", tracking="serial")
    rack = item(pm, "RACK", kind="accessory")
    stock_in(pm, eng, wh, sup, rack, 3)
    p = ok(pm.post(f"{API}/projects", json={"name": "Tower power backup", "customer_id": cust["id"],
                                            "scope": "48 V backup for 2 towers"}), 201)  # fmt: skip
    return {"pm": pm, "eng": eng, "wh": wh, "cust": cust, "unit": unit, "rack": rack, "p": p}


def test_formulas_are_arithmetic_only():
    assert (
        evaluate(
            "ceil(load_w * hours / (volts * ah * dod))",
            {"load_w": 2400, "hours": 4, "volts": 2, "ah": 600, "dod": 0.8},
        )
        == 10
    )
    for bad in (
        "__import__('os').system('x')",
        "load_w.real",
        "[1][0]",
        "'a' * 3",
        "open('f')",
        "lambda: 1",
        "max()",
    ):
        with pytest.raises(
            Exception,
            match="(Not allowed|Only numbers|Allowed functions|Unknown input|needs other|Not a valid)",
        ):
            evaluate(bad, {"load_w": 1})
    with pytest.raises(Exception, match="divides by zero"):
        evaluate("load_w / (load_w - load_w)", {"load_w": 1})


def test_a_project_runs_from_survey_to_approved_requirements(proj):
    pm, eng, p = proj["pm"], proj["eng"], proj["p"]
    assert [m["kind"] for m in p["milestones"]] == [
        "material_ready",
        "delivery",
        "installation",
        "commissioning",
        "handover",
    ]
    site = ok(pm.post(f"{API}/projects/{p['id']}/sites", json={"name": "Tower A", "address": "Al Quoz, Dubai",
               "survey_date": "2026-10-20", "surveyed_by": "Omar", "requirements": {"Load": "2.4 kW", "Backup": "4 h"}}), 201)  # fmt: skip
    # A sizing rule exists only if SMET enters it; this one is not confirmed.
    no = pm.post(
        f"{API}/sizing-rules",
        json={"name": "Bad", "inputs": [{"name": "x", "label": "X"}], "expression": "x.__class__"},
    )
    assert no.status_code == 422
    rule = ok(pm.post(f"{API}/sizing-rules", json={"name": "Cells for backup time", "inputs": [
        {"name": "load_w", "label": "Load", "unit": "W"}, {"name": "hours", "label": "Backup", "unit": "h"},
        {"name": "cell_wh", "label": "Usable energy per cell", "unit": "Wh"}],
        "expression": "load_w * hours / cell_wh", "result_unit": "cells"}), 201)  # fmt: skip
    sheet = ok(pm.post(f"{API}/projects/{p['id']}/sheets", json={"site_id": site["id"], "lines": [
        {"item_id": proj["unit"]["id"], "configuration": "24 × 2 V in series", "rule_id": rule["id"],
         "inputs": {"load_w": 2400, "hours": 4, "cell_wh": 1000}},
        {"item_id": proj["rack"]["id"], "qty": 2}]}), 201)  # fmt: skip
    calc = sheet["lines"][0]["calculation"]
    assert (sheet["lines"][0]["qty"], calc["result"], calc["confirmed"]) == (
        10,
        9.6,
        False,
    )  # inputs and result shown
    sheet = ok(pm.post(f"{API}/sheets/{sheet['id']}/submit", json={"version": sheet["version"]}))
    assert (
        pm.post(
            f"{API}/sheets/{sheet['id']}/approve", json={"version": sheet["version"]}
        ).status_code
        == 403
    )
    sheet = ok(
        eng.post(
            f"{API}/sheets/{sheet['id']}/approve",
            json={"version": sheet["version"], "reason": "Checked with load study"},
        )
    )
    frozen = pm.put(
        f"{API}/sheets/{sheet['id']}",
        json={"version": sheet["version"], "lines": [{"item_id": proj["rack"]["id"], "qty": 9}]},
    )
    assert frozen.status_code == 409 and "new version" in frozen.text
    p = ok(pm.get(f"{API}/projects/{p['id']}"))
    mats = {m["item"]["code"]: m for m in p["materials"]}
    assert (mats["UNIT-2V"]["required"], mats["UNIT-2V"]["short"], mats["RACK"]["short"]) == (
        10,
        10,
        0,
    )
    ok(pm.post(f"{API}/projects/{p['id']}/reserve", json={"key": key()}))
    assert summary(pm, "RACK")["reserved"] == 2
    pr = ok(pm.post(f"{API}/projects/{p['id']}/purchase-request"), 201)
    assert [(ln["item"]["code"], ln["qty"]) for ln in pr["lines"]] == [("UNIT-2V", 10)] and pr[
        "ref"
    ] == p["number"]
    qt = ok(pm.post(f"{API}/projects/{p['id']}/quotation"), 201)
    assert qt["project"]["number"] == p["number"] and len(qt["revisions"][0]["lines"]) == 2
    p = ok(pm.get(f"{API}/projects/{p['id']}"))
    assert [r["number"] for r in p["purchase_requests"]] == [pr["number"]] and p["quotations"][0][
        "number"
    ] == qt["number"]


def test_changes_need_someone_else_and_progress_is_kept(proj):
    pm, eng, p = proj["pm"], proj["eng"], proj["p"]
    ok(
        pm.post(
            f"{API}/projects/{p['id']}/changes",
            json={"kind": "quantity", "description": "Tower B added: +12 cells"},
        ),
        201,
    )
    ok(
        pm.post(
            f"{API}/projects/{p['id']}/notes",
            json={"text": "Access permit pending from landlord", "pending_action": True},
        ),
        201,
    )
    people = ok(pm.get(f"{API}/projects/people"))["items"]
    ok(
        pm.post(
            f"{API}/projects/{p['id']}/tasks",
            json={"title": "Book crane", "assignee_id": people[0]["id"], "due_date": "2026-11-05"},
        ),
        201,
    )
    p = ok(pm.get(f"{API}/projects/{p['id']}"))
    c = p["changes"][0]
    assert (
        pm.post(f"{API}/changes/{c['id']}/approve", json={"version": c["version"]}).status_code
        == 403
    )
    assert (
        eng.post(f"{API}/changes/{c['id']}/reject", json={"version": c["version"]}).status_code
        == 422
    )  # needs a reason
    ok(
        eng.post(
            f"{API}/changes/{c['id']}/approve",
            json={"version": c["version"], "reason": "Agreed in site meeting"},
        )
    )
    m = p["milestones"][0]
    ok(pm.patch(f"{API}/milestones/{m['id']}", json={"version": m["version"], "done": True}))
    t = p["tasks"][0]
    ok(pm.patch(f"{API}/tasks/{t['id']}", json={"version": t["version"], "status": "done"}))
    p = ok(pm.get(f"{API}/projects/{p['id']}"))
    assert (
        p["changes"][0]["status"] == "approved"
        and p["milestones"][0]["done_at"]
        and p["tasks"][0]["status"] == "done"
    )
    assert p["notes"][0]["pending_action"] is True
