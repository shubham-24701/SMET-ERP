"""Customization on screen: a permission per report, custom fields, built-in fields renamed, hidden or
required, approval switches, extra roles told about events, and the service-request status flow."""

from app.jobs import run_due_jobs
from app.rbac import PERMISSIONS, REPORT_PERMS, TEMPLATES
from app.reports import REPORTS
from tests.conftest import API, new_client
from tests.ops import item, ok, supplier
from tests.test_notifications import BUYER, SERVICE, inbox


def po_body(sup, it):
    return {
        "supplier_id": sup["id"],
        "currency": "AED",
        "lines": [{"item_id": it["id"], "qty": 2, "unit_price": 5}],
    }


def test_every_report_has_its_own_permission():
    assert set(REPORT_PERMS) == set(REPORTS)
    assert all(
        f"reports.{n}" in PERMISSIONS and REPORTS[n].perm == area
        for n, (_, area) in REPORT_PERMS.items()
    )
    assert "reports.purchase_orders" in TEMPLATES["procurement_officer"][3]
    assert "reports.service_requests" not in TEMPLATES["procurement_officer"][3]


def test_a_report_needs_its_own_permission_not_the_area_s(user_with):
    area_only = user_with(perms=["purchasing.view"])
    assert area_only.get(f"{API}/reports/purchase_orders").status_code == 403
    assert ok(area_only.get(f"{API}/dashboards"))["areas"] == []
    one = user_with(perms=["reports.rejected_receipts"])
    assert one.get(f"{API}/reports/purchase_orders").status_code == 403
    ok(one.get(f"{API}/reports/rejected_receipts"))
    tiles = [t["report"] for a in ok(one.get(f"{API}/dashboards"))["areas"] for t in a["tiles"]]
    assert set(tiles) == {"rejected_receipts"}


def test_custom_fields_are_defined_filled_checked_and_reported(admin, user_with):
    buyer = user_with(perms=BUYER + ["reports.purchase_orders", "reports.export"])
    assert buyer.post(f"{API}/custom-fields", json={"entity": "purchase_order", "label": "Incoterm", "kind": "choice", "options": ["FOB"]}).status_code == 403  # fmt: skip
    assert admin.post(f"{API}/custom-fields", json={"entity": "purchase_order", "label": "Incoterm", "kind": "choice", "options": []}).status_code == 422  # fmt: skip
    f = ok(admin.post(f"{API}/custom-fields", json={"entity": "purchase_order", "label": "Incoterm", "kind": "choice",
                                                     "options": ["FOB", "CIF"], "required": True}), 201)  # fmt: skip
    ok(
        admin.post(
            f"{API}/custom-fields",
            json={"entity": "purchase_order", "label": "Container count", "kind": "number"},
        ),
        201,
    )
    assert f["key"] == "incoterm"
    po = ok(
        buyer.post(f"{API}/purchase-orders", json=po_body(supplier(buyer), item(buyer, "CELL"))),
        201,
    )
    url = f"{API}/custom-values/purchase_order/{po['id']}"
    v = ok(buyer.get(url))
    assert v["missing"] == ["Incoterm"] and [x["key"] for x in v["fields"]] == [
        "container_count",
        "incoterm",
    ]
    assert (
        "Incoterm is required"
        in buyer.put(url, json={"version": 0, "values": {"container_count": 2}}).text
    )
    assert (
        "not a valid choice"
        in buyer.put(url, json={"version": 0, "values": {"incoterm": "EXW"}}).text
    )
    assert buyer.put(url, json={"version": 0, "values": {"nonsense": 1}}).status_code == 422
    v = ok(
        buyer.put(url, json={"version": 0, "values": {"incoterm": "CIF", "container_count": "3"}})
    )
    assert v["values"] == {"incoterm": "CIF", "container_count": 3} and v["missing"] == []
    assert (
        user_with(perms=["purchasing.view"]).put(url, json={"version": 1, "values": {}}).status_code
        == 403
    )
    rep = ok(buyer.get(f"{API}/reports/purchase_orders"))
    assert {"key": "cf_incoterm", "label": "Incoterm"} in rep["columns"] and rep["items"][0][
        "cf_incoterm"
    ] == "CIF"
    csv = (
        buyer.get(f"{API}/reports/purchase_orders/export").content.decode("utf-8-sig").splitlines()
    )
    assert csv[0].endswith("Container count,Incoterm") and csv[1].endswith(",3,CIF")
    # Switched off: no longer asked for or reported, but the stored value is kept.
    ok(
        admin.put(
            f"{API}/custom-fields/{f['id']}",
            json={"version": 1, "label": "Incoterm", "options": ["FOB", "CIF"], "active": False},
        )
    )
    assert ok(buyer.get(url))["missing"] == [] and "cf_incoterm" not in str(
        ok(buyer.get(f"{API}/reports/purchase_orders"))["columns"]
    )
    assert ok(buyer.get(url))["values"]["incoterm"] == "CIF"


def test_built_in_fields_can_be_renamed_hidden_or_required(admin, user_with):
    clerk = user_with(perms=["parties.view", "parties.edit"])
    s = admin.put(
        f"{API}/field-settings/party/address", json={"version": 0, "hidden": True, "required": True}
    )
    assert s.status_code == 422
    assert (
        admin.put(f"{API}/field-settings/party/code", json={"version": 0, "label": "x"}).status_code
        == 404
    )
    ok(
        admin.put(
            f"{API}/field-settings/party/address",
            json={"version": 0, "label": "Office address", "required": True},
        )
    )
    ok(admin.put(f"{API}/field-settings/party/notes", json={"version": 0, "hidden": True}))
    cfg = ok(clerk.get(f"{API}/fields/config"))["party"]["builtin"]
    assert cfg == {"address": {"label": "Office address", "hidden": False, "required": True},
                   "notes": {"label": "", "hidden": True, "required": False}}  # fmt: skip
    body = {"code": "C1", "name": "Gulf Telecom", "kinds": ["customer"]}
    r = clerk.post(f"{API}/parties", json=body)
    assert r.status_code == 422 and "Office address is required" in r.text
    party = ok(clerk.post(f"{API}/parties", json=body | {"address": "Al Quoz, Dubai"}), 201)
    r = clerk.patch(
        f"{API}/parties/{party['id']}", json={"address": " ", "version": party["version"]}
    )
    assert r.status_code == 422


def test_approval_switches_and_extra_roles_told(admin, user_with):
    buyer = user_with(perms=BUYER)
    rules = ok(admin.get(f"{API}/rules"))
    ops = next(
        r
        for r in ok(admin.get(f"{API}/roles"))["items"]
        if r["name"] == "Management / Operations Manager"
    )
    boss = user_with("operations_manager")  # sees purchasing, can't approve it
    assert (
        admin.patch(
            f"{API}/rules",
            json={"version": rules["version"], "event_roles": {"nonsense": [ops["id"]]}},
        ).status_code
        == 422
    )
    ok(
        admin.patch(
            f"{API}/rules",
            json={"version": rules["version"], "event_roles": {"approval.requested": [ops["id"]]}},
        )
    )
    sup, it = supplier(buyer), item(buyer, "CELL")
    po = ok(buyer.post(f"{API}/purchase-orders", json=po_body(sup, it)), 201)
    po = ok(buyer.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    run_due_jobs()
    assert po["status"] == "submitted" and [n["event"] for n in inbox(boss)["items"]] == [
        "approval.requested"
    ]
    rules = ok(admin.get(f"{API}/rules"))
    ok(
        admin.patch(
            f"{API}/rules", json={"version": rules["version"], "po_approval_required": False}
        )
    )
    po = ok(buyer.post(f"{API}/purchase-orders", json=po_body(sup, it)), 201)
    po = ok(buyer.post(f"{API}/purchase-orders/{po['id']}/submit", json={"version": po["version"]}))
    assert po["status"] == "approved" and "switched off" in po["decision_reason"]


def test_service_statuses_and_moves_are_set_on_screen(admin, user_with):
    coord = user_with(perms=SERVICE + ["reports.service_requests"])
    ok(new_client().post(f"{API}/public/service-requests", json={"name": "Ali", "phone": "0501234567", "issue": "Alarm keeps sounding"}), 202)  # fmt: skip
    sr = ok(coord.get(f"{API}/service-requests"))["items"][0]
    rules = ok(admin.get(f"{API}/rules"))
    flow = ok(coord.get(f"{API}/service/flow"))
    moves = flow["moves"] | {
        "in_progress": [*flow["moves"]["in_progress"], "waiting_parts"],
        "waiting_parts": ["in_progress"],
    }
    bad = admin.patch(
        f"{API}/rules",
        json={
            "version": rules["version"],
            "sr_statuses": [{"key": "waiting_parts", "label": "Waiting for parts"}],
        },
    )
    assert bad.status_code == 422 and "No move" in bad.text  # a status nothing reaches
    ok(admin.patch(f"{API}/rules", json={"version": rules["version"], "sr_statuses": [{"key": "waiting_parts", "label": "Waiting for parts"}],
                                         "sr_moves": moves}))  # fmt: skip
    assert ok(coord.get(f"{API}/service/flow"))["statuses"]["waiting_parts"] == "Waiting for parts"

    def mv(to):
        version = ok(coord.get(f"{API}/service-requests/{sr['id']}"))["version"]
        return coord.post(
            f"{API}/service-requests/{sr['id']}/status", json={"version": version, "to": to}
        )

    assert mv("waiting_parts").status_code == 409  # not from "new"
    ok(mv("under_review"))
    ok(mv("in_progress"))
    ok(mv("waiting_parts"))
    assert (
        ok(coord.get(f"{API}/reports/service_requests", params={"status": "waiting_parts"}))[
            "total"
        ]
        == 1
    )
    assert ok(coord.get(f"{API}/service-requests", params={"status": "open"}))["total"] == 1
    rules = ok(admin.get(f"{API}/rules"))
    r = admin.patch(
        f"{API}/rules",
        json={"version": rules["version"], "sr_statuses": [], "sr_moves": flow["moves"]},
    )
    assert r.status_code == 422 and "still in use" in r.text
