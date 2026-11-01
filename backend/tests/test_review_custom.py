"""Regressions for the independent review of the customization work: each fails without its fix."""

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Role, RolePermission
from app.rbac import sync_templates
from tests import test_installation
from tests.conftest import API
from tests.ops import item, ok, supplier
from tests.test_customization import po_body
from tests.test_installation import bank, visit
from tests.test_notifications import BUYER

site = test_installation.site


def field(admin, entity, label, kind="text", **kw):
    return ok(
        admin.post(
            f"{API}/custom-fields", json={"entity": entity, "label": label, "kind": kind} | kw
        ),
        201,
    )


def test_a_required_field_filled_in_the_same_request_is_accepted(admin, user_with):
    """Must 1–2: the check ran before the route had copied the fields (PO, dispatch)."""
    buyer = user_with(perms=BUYER)
    ok(
        admin.put(
            f"{API}/field-settings/purchase_order/supplier_ref",
            json={"version": 0, "required": True},
        )
    )
    body = po_body(supplier(buyer), item(buyer, "CELL"))
    assert "Supplier reference is required" in buyer.post(f"{API}/purchase-orders", json=body).text
    po = ok(buyer.post(f"{API}/purchase-orders", json=body | {"supplier_ref": "SQ-1"}), 201)
    assert po["supplier_ref"] == "SQ-1"


def test_startup_sync_respects_a_report_taken_away(admin):
    """Must 3: a report permission removed from a shipped role came back at the next start."""
    with SessionLocal() as db:
        role = db.scalar(select(Role).where(Role.template_key == "warehouse_officer"))
        for perm in ("purchasing.view", "reports.purchase_orders"):
            db.delete(db.get(RolePermission, (role.id, perm)))
        db.commit()
        sync_templates(db)
        db.commit()
        assert db.get(RolePermission, (role.id, "reports.purchase_orders")) is None


def test_technicians_fill_details_only_on_their_own_visits(site, admin):
    """Must 4: any technician could change any visit's details."""
    field(admin, "site_visit", "Access card")
    v = visit(site, "installation", [bank(site)])
    url = f"{API}/custom-values/site_visit/{v['id']}"
    assert (
        site["other"].put(url, json={"version": 0, "values": {"access_card": "A1"}}).status_code
        == 403
    )
    ok(site["tech"].put(url, json={"version": 0, "values": {"access_card": "A1"}}))


def test_bad_numbers_and_wrong_types_are_refused_not_crashes(admin, user_with):
    """Must 5 and Should 6: odd numbers gave 500s; objects were stored as text."""
    buyer = user_with(perms=BUYER)
    field(admin, "purchase_order", "Weight", "number")
    field(admin, "purchase_order", "Tag")
    po = ok(
        buyer.post(f"{API}/purchase-orders", json=po_body(supplier(buyer), item(buyer, "CELL"))),
        201,
    )
    url = f"{API}/custom-values/purchase_order/{po['id']}"
    for bad in (
        {"weight": "1.e999"},
        {"weight": [1]},
        {"weight": {"a": 1}},
        {"tag": {"x": [1]}},
        {"tag": True},
    ):
        assert buyer.put(url, json={"version": 0, "values": bad}).status_code == 422, bad
    assert ok(buyer.put(url, json={"version": 0, "values": {"weight": "2.5", "tag": 7}}))[
        "values"
    ] == {"weight": 2.5, "tag": "7"}


def test_finished_records_keep_their_details_and_removed_options_survive(admin, user_with):
    """Should 5 and 2: closed records could be changed; a removed option made saving erase it."""
    buyer = user_with(perms=BUYER)
    f = field(admin, "purchase_order", "Incoterm", "choice", options=["FOB", "CIF"])
    po = ok(
        buyer.post(f"{API}/purchase-orders", json=po_body(supplier(buyer), item(buyer, "CELL"))),
        201,
    )
    url = f"{API}/custom-values/purchase_order/{po['id']}"
    ok(buyer.put(url, json={"version": 0, "values": {"incoterm": "CIF"}}))
    ok(
        admin.put(
            f"{API}/custom-fields/{f['id']}",
            json={"version": 1, "label": "Incoterm", "options": ["FOB"]},
        )
    )
    assert ok(buyer.put(url, json={"version": 1, "values": {"incoterm": "CIF"}}))["values"] == {
        "incoterm": "CIF"
    }
    assert buyer.put(url, json={"version": 2, "values": {"incoterm": "EXW"}}).status_code == 422
    ok(
        buyer.post(
            f"{API}/purchase-orders/{po['id']}/cancel",
            json={"version": po["version"], "reason": "Not needed"},
        )
    )
    assert buyer.put(url, json={"version": 2, "values": {"incoterm": "FOB"}}).status_code == 409


def test_editing_without_view_permission_answers_with_the_saved_values(admin, user_with):
    """Should 8: the save went through, then the answer was a 403."""
    field(admin, "item", "Shelf life")
    it = item(user_with(perms=["items.view", "items.edit"]), "CELL")
    editor = user_with(perms=["items.edit"])
    assert (
        ok(
            editor.put(
                f"{API}/custom-values/item/{it['id']}",
                json={"version": 0, "values": {"shelf_life": "2y"}},
            )
        )["version"]
        == 1
    )


def test_a_deleted_role_doesn_t_block_other_rule_changes(admin, user_with):
    """Should 4: a role deleted after being added to an event blocked every later rule change."""
    role = ok(admin.post(f"{API}/roles", json={"name": "Night shift", "permissions": []}), 201)
    rules = ok(admin.get(f"{API}/rules"))
    ok(
        admin.patch(
            f"{API}/rules",
            json={"version": rules["version"], "event_roles": {"stock.low": [role["id"]]}},
        )
    )
    admin.delete(f"{API}/roles/{role['id']}")
    rules = ok(admin.get(f"{API}/rules"))
    ok(admin.patch(f"{API}/rules", json={"version": rules["version"], "reminder_repeat_days": 3}))


def test_switched_off_fields_leave_the_report_rows(admin, user_with):
    """Nice 1: hidden columns still carried their values in the report data."""
    buyer = user_with(perms=BUYER + ["reports.purchase_orders"])
    f = field(admin, "purchase_order", "Secret note")
    po = ok(
        buyer.post(f"{API}/purchase-orders", json=po_body(supplier(buyer), item(buyer, "CELL"))),
        201,
    )
    ok(
        buyer.put(
            f"{API}/custom-values/purchase_order/{po['id']}",
            json={"version": 0, "values": {"secret_note": "x"}},
        )
    )
    ok(
        admin.put(
            f"{API}/custom-fields/{f['id']}",
            json={"version": 1, "label": "Secret note", "active": False},
        )
    )
    assert "cf_secret_note" not in ok(buyer.get(f"{API}/reports/purchase_orders"))["items"][0]


def test_open_is_not_a_status_name(admin):
    """Nice 5: a status called "open" would clash with the list's "open" filter."""
    rules = ok(admin.get(f"{API}/rules"))
    moves = rules["rules"]["sr_moves"] | {
        "new": [*rules["rules"]["sr_moves"]["new"], "open"],
        "open": ["scheduled"],
    }
    r = admin.patch(
        f"{API}/rules",
        json={
            "version": rules["version"],
            "sr_statuses": [{"key": "open", "label": "Open"}],
            "sr_moves": moves,
        },
    )
    assert r.status_code == 422


def test_a_field_label_can_t_become_a_spreadsheet_formula(admin, user_with):
    """Security L1: export headings are admin-typed labels, so they are neutralised like cells."""
    buyer = user_with(perms=BUYER + ["reports.purchase_orders", "reports.export"])
    field(admin, "purchase_order", '=HYPERLINK("http://evil")')
    buyer.post(f"{API}/purchase-orders", json=po_body(supplier(buyer), item(buyer, "CELL")))
    head = (
        buyer.get(f"{API}/reports/purchase_orders/export")
        .content.decode("utf-8-sig")
        .splitlines()[0]
    )
    assert "'=HYPERLINK" in head
