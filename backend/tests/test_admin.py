from sqlalchemy import select

from app import rbac
from app.db import SessionLocal
from app.models import AuditEvent, Role, RolePermission
from tests.conftest import API


def role_id(c, name: str) -> str:
    return next(r["id"] for r in c.get(f"{API}/roles").json()["items"] if r["name"] == name)


def role(c, name: str) -> dict:
    return next(r for r in c.get(f"{API}/roles").json()["items"] if r["name"] == name)


def test_all_role_templates_ship(admin):
    names = {r["name"] for r in admin.get(f"{API}/roles").json()["items"]}
    assert names == {t[0] for t in rbac.TEMPLATES.values()} and len(names) == 17


def test_admin_holds_no_business_permissions():
    # System Administrator configures; it never approves, adjusts stock or changes warranties.
    business = [p for p in rbac.TEMPLATES["system_admin"][3] if p.split(".")[0] not in
                {"users", "roles", "audit", "settings", "system"}
                and p.split(".")[1] not in ("configure", "view")]  # fmt: skip
    assert business == []


def test_create_user_with_roles_returns_a_one_time_password(admin):
    rid = role_id(admin, "Procurement Officer")
    r = admin.post(
        f"{API}/users", json={"name": "Ravi", "email": "Ravi@Example.test", "role_ids": [rid]}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "ravi@example.test" and len(body["temporary_password"]) >= 16
    assert [x["name"] for x in body["roles"]] == ["Procurement Officer"]
    listed = admin.get(f"{API}/users", params={"q": "ravi"}).json()["items"][0]
    assert "temporary_password" not in listed
    dup = admin.post(f"{API}/users", json={"name": "R2", "email": "ravi@example.test"})
    assert dup.status_code == 422 and "already in use" in dup.text


def test_portal_roles_cannot_go_to_staff(admin):
    rid = role_id(admin, "Distributor User")
    r = admin.post(f"{API}/users", json={"name": "D", "email": "d@example.test", "role_ids": [rid]})
    assert r.status_code == 422


def test_nobody_changes_their_own_roles_or_a_role_they_hold(admin):
    me = admin.get(f"{API}/auth/me").json()["user"]
    v = admin.get(f"{API}/users").json()["items"][0]["version"]
    assert (
        admin.patch(f"{API}/users/{me['id']}", json={"version": v, "role_ids": []}).status_code
        == 403
    )
    sa = role(admin, "System Administrator")
    r = admin.put(f"{API}/roles/{sa['id']}", json={"version": sa["version"], "name": sa["name"],
                                                   "permissions": sa["permissions"] + []})  # fmt: skip
    assert r.status_code == 403


def test_the_last_admin_can_never_be_removed(admin):
    """Defence in depth behind the escalation guard: a change that would leave nobody able to manage
    users and roles is rolled back."""
    import pytest
    from fastapi import HTTPException

    from app.admin import _ensure_an_admin_remains
    from app.models import User

    with SessionLocal() as db:
        db.query(User).update({"active": False})
        db.flush()
        with pytest.raises(HTTPException) as e:
            _ensure_an_admin_remains(db)
        assert e.value.status_code == 409
    assert admin.get(f"{API}/auth/me").status_code == 200  # rolled back: still signed in and active


def test_role_changes_are_audited_with_added_and_removed_permissions(admin):
    r = admin.post(f"{API}/roles", json={"name": "Auditor", "permissions": ["audit.view"]})
    assert r.status_code == 201
    rr = r.json()
    r = admin.put(f"{API}/roles/{rr['id']}", json={"version": rr["version"], "name": "Auditor",
                  "permissions": ["audit.view", "audit.export"], "reason": "Year-end review"})  # fmt: skip
    assert r.status_code == 200
    with SessionLocal() as db:
        ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "role.update")).one()
    assert ev.changes["permissions"] == {"added": ["audit.export"], "removed": []}
    assert ev.reason == "Year-end review" and ev.actor_name == "Test Admin"
    stale = admin.put(
        f"{API}/roles/{rr['id']}",
        json={"version": rr["version"], "name": "Auditor", "permissions": []},
    )
    assert stale.status_code == 409  # optimistic locking: someone saved first


def test_unknown_permissions_and_duplicate_names_are_refused(admin):
    assert (
        admin.post(f"{API}/roles", json={"name": "X", "permissions": ["stock.steal"]}).status_code
        == 422
    )
    assert admin.post(f"{API}/roles", json={"name": "procurement officer"}).status_code == 422


def test_roles_in_use_and_templates_cannot_be_deleted(admin):
    rid = role_id(admin, "Sales Officer")
    assert admin.delete(f"{API}/roles/{rid}").status_code == 409
    custom = admin.post(f"{API}/roles", json={"name": "Temp role"}).json()
    admin.post(
        f"{API}/users", json={"name": "T", "email": "t@example.test", "role_ids": [custom["id"]]}
    )
    assert admin.delete(f"{API}/roles/{custom['id']}").status_code == 409
    unused = admin.post(f"{API}/roles", json={"name": "Unused role"}).json()
    assert admin.delete(f"{API}/roles/{unused['id']}").status_code == 204


def test_a_removed_template_permission_stays_removed_after_upgrades(admin, monkeypatch):
    mgr = role(admin, "Management / Operations Manager")
    keep = [p for p in mgr["permissions"] if p != "audit.view"]
    assert admin.put(f"{API}/roles/{mgr['id']}", json={"version": mgr["version"], "name": mgr["name"],
                     "permissions": keep}).status_code == 200  # fmt: skip
    # A later version adds a new permission to the template and syncs at startup.
    name, aud, desc, perms = rbac.TEMPLATES["operations_manager"]
    monkeypatch.setitem(rbac.PERMISSIONS, "future.view", ("Future", "x"))
    monkeypatch.setitem(
        rbac.TEMPLATES, "operations_manager", (name, aud, desc, [*perms, "future.view"])
    )
    with SessionLocal() as db:
        rbac.sync_templates(db)
        rbac.sync_templates(db)  # idempotent
        db.commit()
        rid = db.scalar(select(Role.id).where(Role.template_key == "operations_manager"))
        got = set(
            db.scalars(select(RolePermission.permission).where(RolePermission.role_id == rid))
        )
    assert "future.view" in got and "audit.view" not in got


def test_reset_password_signs_the_user_out(admin, user_with):
    bob = user_with(email="bob@example.test")
    uid = bob.get(f"{API}/auth/me").json()["user"]["id"]
    r = admin.post(f"{API}/users/{uid}/reset-password")
    assert r.status_code == 200 and r.json()["temporary_password"]
    assert bob.get(f"{API}/auth/me").status_code == 401


def test_company_settings_validate_and_lock(admin):
    c = admin.get(f"{API}/company").json()
    assert c["currency"] == "AED" and "Asia/Dubai" in c["timezones"]
    bad = admin.patch(f"{API}/company", json={"version": c["version"], "timezone": "Mars/Base"})
    assert bad.status_code == 422
    ok = admin.patch(f"{API}/company", json={"version": c["version"], "name": "SMET LLC"})
    assert ok.status_code == 200 and ok.json()["name"] == "SMET LLC"
    assert (
        admin.patch(f"{API}/company", json={"version": c["version"], "name": "Again"}).status_code
        == 409
    )


def test_user_status_counts_filter_the_list(admin, user_with):
    user_with("sales_officer")  # active, has a role
    user_with()  # active, no roles
    made = admin.post(f"{API}/users", json={"name": "New", "email": "new@example.test"}).json()
    r = admin.get(f"{API}/users").json()
    assert r["counts"] == {"active": 4, "password_pending": 1, "no_roles": 2, "deactivated": 0}
    no_roles = admin.get(f"{API}/users", params={"status": "no_roles"}).json()
    assert no_roles["total"] == 2 and made["id"] in {u["id"] for u in no_roles["items"]}
    assert admin.get(f"{API}/users", params={"status": "nonsense"}).status_code == 422


# ---- regressions from the independent review (2026-10-28) ----


def test_a_helpdesk_user_cannot_take_over_an_admin(admin, user_with):
    helpdesk = user_with(perms=["users.view", "users.edit", "users.create", "roles.view"])
    admin_id = admin.get(f"{API}/auth/me").json()["user"]["id"]
    assert helpdesk.post(f"{API}/users/{admin_id}/reset-password").status_code == 403
    v = admin.get(f"{API}/users", params={"q": "Test Admin"}).json()["items"][0]["version"]
    off = helpdesk.patch(f"{API}/users/{admin_id}", json={"version": v, "active": False})
    assert off.status_code == 403


def test_nobody_hands_out_powers_they_dont_hold(admin, user_with):
    helpdesk = user_with(perms=["users.view", "users.edit", "users.create", "roles.view"])
    sa = role_id(helpdesk, "System Administrator")
    r = helpdesk.post(
        f"{API}/users", json={"name": "Puppet", "email": "p@example.test", "role_ids": [sa]}
    )
    assert r.status_code == 403 and "configure, which you don't hold" in r.text
    # Business roles are fine: giving them is what user administration is for.
    sales = role_id(helpdesk, "Sales Officer")
    ok = helpdesk.post(
        f"{API}/users", json={"name": "Sam", "email": "s@example.test", "role_ids": [sales]}
    )
    assert ok.status_code == 201
    # A role editor can't create or widen a role beyond their own powers either.
    editor = user_with(perms=["roles.view", "roles.configure"])
    assert (
        editor.post(
            f"{API}/roles", json={"name": "Backdoor", "permissions": ["users.edit"]}
        ).status_code
        == 403
    )


def test_two_admins_removing_each_other_at_once_leave_one(admin, user_with):
    from concurrent.futures import ThreadPoolExecutor

    second = user_with("system_admin", email="second@example.test")
    ids = {c: c.get(f"{API}/auth/me").json()["user"]["id"] for c in (admin, second)}
    rows = {u["id"]: u["version"] for u in admin.get(f"{API}/users").json()["items"]}

    def strip(pair):
        actor, target = pair
        return actor.patch(
            f"{API}/users/{ids[target]}", json={"version": rows[ids[target]], "role_ids": []}
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(strip, [(admin, second), (second, admin)]))
    assert codes[0] == 200 and codes[1] in (403, 409)  # the second sees the first's change
    with SessionLocal() as db:
        from app.admin import ADMIN_POWERS
        from app.auth import load_access
        from app.models import User

        holders = [
            u for u in db.scalars(select(User)) if set(ADMIN_POWERS) <= load_access(db, u.id)[1]
        ]
    assert len(holders) == 1


def test_blank_names_are_refused(admin):
    c = admin.get(f"{API}/company").json()
    assert (
        admin.patch(f"{API}/company", json={"version": c["version"], "name": "     "}).status_code
        == 422
    )
    assert admin.post(f"{API}/roles", json={"name": "   "}).status_code == 422
    assert (
        admin.post(f"{API}/users", json={"name": "  ", "email": "x@example.test"}).status_code
        == 422
    )


def test_only_browser_safe_time_zones_are_offered(admin):
    zones = admin.get(f"{API}/company").json()["timezones"]
    assert "Asia/Dubai" in zones and "UTC" in zones
    assert not [
        z for z in zones if z in ("Factory", "localtime") or z.startswith(("posix/", "right/"))
    ]
    c = admin.get(f"{API}/company").json()
    assert (
        admin.patch(
            f"{API}/company", json={"version": c["version"], "timezone": "Factory"}
        ).status_code
        == 422
    )


def test_audit_trigger_functions_pin_their_search_path():
    from sqlalchemy import text

    with SessionLocal() as db:
        configs = db.execute(text(
            "SELECT proconfig FROM pg_proc WHERE proname IN ('audit_events_guard', 'audit_events_append_only')"
        )).scalars().all()  # fmt: skip
    assert configs and all(c and "search_path=pg_catalog" in c for c in configs)
