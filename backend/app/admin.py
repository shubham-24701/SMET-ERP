"""Administration: users, roles & permissions, the audit log and company settings.

Guards against privilege escalation: nobody changes their own roles or a role they hold, and at
least one active user always keeps the power to manage users and roles (no lock-out).
"""

import csv
import io
import secrets
import uuid
from datetime import datetime
from typing import Annotated, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import Select, delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError

from app.audit import diff, record
from app.auth import DB, EMAIL_PATTERN, Principal, hasher, load_access, require
from app.models import AuditEvent, AuthSession, Company, Role, RolePermission, User, UserRole
from app.rbac import PERMISSIONS, is_guarded

router = APIRouter(tags=["admin"])
ADMIN_POWERS = ("users.edit", "roles.configure")


def field_error(field: str, msg: str) -> HTTPException:
    return HTTPException(422, [{"loc": ["body", field], "msg": msg, "type": "x"}])


def stale() -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT, "Someone else changed this just now. Reload and try again."
    )


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
RoleName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]
Text500 = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


def serialize_access_changes(db: DB) -> None:
    """One change to users or roles at a time (a transaction-scoped lock), so two administrators
    removing each other at the same moment can't both pass the "an admin remains" check."""
    db.execute(text("SELECT pg_advisory_xact_lock(74610001)"))


def guard(p: Principal, perms, what: str) -> None:
    """Refuse when `perms` include a guarded permission the caller doesn't hold (no escalation)."""
    missing = sorted(x for x in perms if is_guarded(x) and x not in p.permissions)
    if missing:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"You can't {what}: it involves {missing[0]}, which you don't hold yourself.",
        )


def _perms_of_roles(db: DB, role_ids) -> set[str]:
    if not role_ids:
        return set()
    return set(
        db.scalars(select(RolePermission.permission).where(RolePermission.role_id.in_(role_ids)))
    )


def temporary_password() -> str:
    return secrets.token_urlsafe(12)  # 16 characters, shown once to the administrator


def _ensure_an_admin_remains(db: DB) -> None:
    """Called after a change, inside its transaction: refuses it if nobody active could still
    manage users and roles."""
    holders = (
        select(UserRole.user_id)
        .join(RolePermission, RolePermission.role_id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .where(User.active, RolePermission.permission.in_(ADMIN_POWERS))
        .group_by(UserRole.user_id)
        .having(func.count(func.distinct(RolePermission.permission)) == len(ADMIN_POWERS))
    )
    if not db.scalar(select(func.count()).select_from(holders.subquery())):
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "At least one active user must keep the permission to manage users and roles.",
        )


# ---------- users ----------


class UserIn(BaseModel):
    name: Name
    email: str = Field(max_length=254, pattern=EMAIL_PATTERN)
    role_ids: list[uuid.UUID] = Field(default=[], max_length=20)


class UserPatch(BaseModel):
    version: int
    name: Name | None = None
    role_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    active: bool | None = None
    reason: Text500 = ""


def _roles_of(db: DB, user_ids) -> dict:
    out: dict = {}
    for uid, rid, name in db.execute(
        select(UserRole.user_id, Role.id, Role.name)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id.in_(user_ids))
        .order_by(Role.name)
    ):
        out.setdefault(uid, []).append({"id": rid, "name": name})
    return out


def _user_row(u: User, roles: list) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "active": u.active,
            "must_change_password": u.must_change_password, "last_login_at": u.last_login_at,
            "created_at": u.created_at, "version": u.version, "roles": roles}  # fmt: skip


def _checked_roles(db: DB, role_ids: list[uuid.UUID]) -> list[Role]:
    roles = db.scalars(select(Role).where(Role.id.in_(role_ids))).all() if role_ids else []
    if len(roles) != len(set(role_ids)):
        raise field_error("role_ids", "Unknown role")
    if any(r.audience != "internal" for r in roles):
        # Portal roles go to distributor / customer accounts, which arrive with the portals.
        raise field_error("role_ids", "Portal roles can't be given to staff accounts")
    return roles


UserStatus = Literal["active", "password_pending", "no_roles", "deactivated"]
_has_role = select(UserRole.user_id).where(UserRole.user_id == User.id).exists()
STATUS_FILTERS = {
    "active": User.active,
    "password_pending": User.active & User.must_change_password,
    "no_roles": User.active & ~_has_role,
    "deactivated": ~User.active,
}


@router.get("/users")
def list_users(
    _: require("users.view"),
    db: DB,
    q: str = Query("", max_length=100),
    status_: Annotated[UserStatus | None, Query(alias="status")] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Users, plus a count per status (the count cards above the list filter by them)."""
    query = select(User)
    if q:
        query = query.where(or_(User.name.ilike(f"%{q}%"), User.email.ilike(f"%{q}%")))
    counts = {k: db.scalar(select(func.count()).select_from(query.where(f).subquery()))
              for k, f in STATUS_FILTERS.items()}  # fmt: skip
    if status_:
        query = query.where(STATUS_FILTERS[status_])
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    users = db.scalars(query.order_by(User.name, User.id).limit(limit).offset(offset)).all()
    roles = _roles_of(db, [u.id for u in users])
    return {
        "items": [_user_row(u, roles.get(u.id, [])) for u in users],
        "total": total,
        "counts": counts,
    }


@router.post("/users", status_code=status.HTTP_201_CREATED)
def create_user(body: UserIn, p: require("users.create"), db: DB) -> dict:
    roles = _checked_roles(db, body.role_ids)
    if roles and not p.can("users.edit"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Giving roles needs the users.edit permission"
        )
    guard(p, _perms_of_roles(db, [r.id for r in roles]), "give these roles")
    password = temporary_password()
    user = User(email=body.email.strip().lower(), name=body.name,
                password_hash=hasher.hash(password), must_change_password=True)  # fmt: skip
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("email", "This email is already in use") from None
    db.add_all([UserRole(user_id=user.id, role_id=r.id) for r in roles])
    record(db, p.user, "user.create", "user", user.id, f"{user.name} added",
           {"roles": [None, sorted(r.name for r in roles)]})  # fmt: skip
    db.commit()
    row = _user_row(user, [{"id": r.id, "name": r.name} for r in roles])
    return row | {"temporary_password": password}  # shown once; they must change it at sign-in


def _managed_user(db: DB, p: Principal, user_id: uuid.UUID, what: str) -> User:
    """The user, locked for this transaction, if the caller holds every guarded permission they do."""
    serialize_access_changes(db)
    user = db.get(User, user_id, with_for_update=True)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    guard(p, load_access(db, user.id)[1], what)
    return user


@router.patch("/users/{user_id}")
def update_user(user_id: uuid.UUID, body: UserPatch, p: require("users.edit"), db: DB) -> dict:
    if user_id == p.user.id and (body.role_ids is not None or body.active is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You can't change your own roles or access")
    user = _managed_user(db, p, user_id, "change this user")
    if user.version != body.version:
        raise stale()
    old_roles = sorted(r["name"] for r in _roles_of(db, [user.id]).get(user.id, []))
    before = {"name": user.name, "active": user.active, "roles": old_roles}
    if body.name is not None:
        user.name = body.name
    if body.role_ids is not None:
        roles = _checked_roles(db, body.role_ids)
        guard(p, _perms_of_roles(db, [r.id for r in roles]), "give these roles")
        db.execute(delete(UserRole).where(UserRole.user_id == user.id))
        db.add_all([UserRole(user_id=user.id, role_id=r.id) for r in roles])
    if body.active is not None:
        user.active = body.active
        if not body.active:  # signed out everywhere, now
            db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    user.version += 1
    db.flush()
    new_roles = sorted(r["name"] for r in _roles_of(db, [user.id]).get(user.id, []))
    changes = diff(before, {"name": user.name, "active": user.active, "roles": new_roles})
    _ensure_an_admin_remains(db)
    what = [w for k, w in (("active", "deactivated" if not user.active else "reactivated"),
                           ("roles", "roles changed"), ("name", "renamed")) if k in changes]  # fmt: skip
    summary = f"{user.name}: {', '.join(what)}" if what else f"{user.name}: no change"
    record(db, p.user, "user.update", "user", user.id, summary, changes, body.reason)
    db.commit()
    return _user_row(user, _roles_of(db, [user.id]).get(user.id, []))


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: uuid.UUID, p: require("users.edit"), db: DB) -> dict:
    if user_id == p.user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Change your own password from your profile"
        )
    user = _managed_user(db, p, user_id, "reset this user's password")
    password = temporary_password()
    user.password_hash = hasher.hash(password)
    user.must_change_password = True
    user.version += 1
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    record(
        db,
        p.user,
        "user.reset_password",
        "user",
        user.id,
        f"{user.name}: temporary password issued",
    )
    db.commit()
    return {"temporary_password": password}


# ---------- roles & permissions ----------

Audience = Literal["internal", "distributor", "customer"]


class RoleIn(BaseModel):
    name: RoleName
    description: Text500 = ""
    audience: Audience = "internal"
    permissions: list[str] = Field(default=[], max_length=500)


class RolePut(BaseModel):
    version: int
    name: RoleName
    description: Text500 = ""
    permissions: list[str] = Field(max_length=500)
    reason: Text500 = ""


def _checked_perms(perms: list[str]) -> list[str]:
    unknown = [x for x in perms if x not in PERMISSIONS]
    if unknown:
        raise field_error("permissions", f"Unknown permission: {unknown[0][:64]}")
    return sorted(set(perms))


def _role_rows(db: DB, roles: list[Role]) -> list[dict]:
    """Three queries for any number of roles (no query per row)."""
    ids = [r.id for r in roles]
    perms: dict = {}
    for rid, perm in db.execute(
        select(RolePermission.role_id, RolePermission.permission)
        .where(RolePermission.role_id.in_(ids))
        .order_by(RolePermission.permission)
    ):
        perms.setdefault(rid, []).append(perm)
    users = dict(
        db.execute(
            select(UserRole.role_id, func.count())
            .where(UserRole.role_id.in_(ids))
            .group_by(UserRole.role_id)
        ).all()
    )
    return [{"id": r.id, "name": r.name, "description": r.description, "audience": r.audience,
             "template": r.template_key is not None, "permissions": perms.get(r.id, []),
             "users": users.get(r.id, 0), "version": r.version} for r in roles]  # fmt: skip


def _role_row(db: DB, r: Role) -> dict:
    return _role_rows(db, [r])[0]


@router.get("/permissions")
def permission_catalogue(_: require("roles.view")) -> dict:
    return {
        "items": [{"code": c, "module": m, "description": d} for c, (m, d) in PERMISSIONS.items()]
    }


@router.get("/roles")
def list_roles(_: require("roles.view"), db: DB) -> dict:
    return {"items": _role_rows(db, db.scalars(select(Role).order_by(Role.name)).all())}


def _name_taken(db: DB, name: str, but=None) -> bool:
    q = select(Role.id).where(func.lower(Role.name) == name.lower())
    return db.scalar(q.where(Role.id != but) if but else q) is not None


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(body: RoleIn, p: require("roles.configure"), db: DB) -> dict:
    perms = _checked_perms(body.permissions)
    guard(p, perms, "create a role with these permissions")
    name = body.name
    if _name_taken(db, name):
        raise field_error("name", "A role with this name already exists")
    role = Role(name=name, description=body.description, audience=body.audience)
    db.add(role)
    db.flush()
    db.add_all([RolePermission(role_id=role.id, permission=x) for x in perms])
    record(
        db,
        p.user,
        "role.create",
        "role",
        role.id,
        f"{name} created",
        {"permissions": [None, perms]},
    )
    db.commit()
    return _role_row(db, role)


def _editable_role(db: DB, p: Principal, role_id: uuid.UUID) -> Role:
    serialize_access_changes(db)
    role = db.get(Role, role_id, with_for_update=True)
    if role is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Role not found")
    if role.name in p.roles:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can't change a role you hold. Ask another administrator.",
        )
    guard(p, _perms_of_roles(db, [role.id]), "change this role")
    return role


@router.put("/roles/{role_id}")
def update_role(role_id: uuid.UUID, body: RolePut, p: require("roles.configure"), db: DB) -> dict:
    role = _editable_role(db, p, role_id)
    if role.version != body.version:
        raise stale()
    perms = _checked_perms(body.permissions)
    guard(p, perms, "give a role these permissions")
    name = body.name
    if _name_taken(db, name, but=role.id):
        raise field_error("name", "A role with this name already exists")
    old = sorted(
        db.scalars(select(RolePermission.permission).where(RolePermission.role_id == role.id))
    )
    before = {"name": role.name, "description": role.description}
    role.name, role.description = name, body.description
    role.version += 1
    db.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
    db.add_all([RolePermission(role_id=role.id, permission=x) for x in perms])
    db.flush()
    _ensure_an_admin_remains(db)
    changes = diff(before, {"name": role.name, "description": role.description})
    added, removed = sorted(set(perms) - set(old)), sorted(set(old) - set(perms))
    if added or removed:
        changes["permissions"] = {"added": added, "removed": removed}
    record(db, p.user, "role.update", "role", role.id, f"{role.name} changed", changes, body.reason)
    db.commit()
    return _role_row(db, role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(role_id: uuid.UUID, p: require("roles.configure"), db: DB) -> None:
    role = _editable_role(db, p, role_id)
    if role.template_key:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Shipped role templates can't be deleted; remove their permissions instead",
        )
    if db.scalar(select(func.count()).select_from(UserRole).where(UserRole.role_id == role.id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Take this role away from its users first")
    record(db, p.user, "role.delete", "role", role.id, f"{role.name} deleted")
    db.delete(role)
    try:
        db.commit()
    except IntegrityError:  # someone was given this role a moment ago
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Take this role away from its users first"
        ) from None


# ---------- audit log ----------


def _audit_filters(
    action: str = Query("", max_length=64),
    actor: str = Query("", max_length=120),
    entity_type: str = Query("", max_length=40),
    entity_id: str = Query("", max_length=64),
    since: datetime | None = None,
    until: datetime | None = None,
    q: str = Query("", max_length=100),
):
    query = select(AuditEvent)
    if action:
        query = query.where(AuditEvent.action.startswith(action))
    if actor:
        query = query.where(AuditEvent.actor_name.ilike(f"%{actor}%"))
    if entity_type:
        query = query.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.where(AuditEvent.entity_id == entity_id)
    if since:
        query = query.where(AuditEvent.at >= since)
    if until:
        query = query.where(AuditEvent.at < until)
    if q:
        query = query.where(
            or_(AuditEvent.summary.ilike(f"%{q}%"), AuditEvent.reason.ilike(f"%{q}%"))
        )
    return query


AuditQuery = Annotated[Select, Depends(_audit_filters)]


def _event(e: AuditEvent) -> dict:
    return {"id": e.id, "at": e.at, "actor": e.actor_name, "actor_id": e.actor_id, "action": e.action,
            "entity_type": e.entity_type, "entity_id": e.entity_id, "summary": e.summary,
            "changes": e.changes, "reason": e.reason, "ip": e.ip, "request_id": e.request_id}  # fmt: skip


@router.get("/audit")
def list_audit(
    _: require("audit.view"),
    db: DB,
    query: AuditQuery,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
) -> dict:
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(AuditEvent.id.desc()).limit(limit).offset(offset))
    return {"items": [_event(e) for e in rows], "total": total}


EXPORT_LIMIT = 50_000
EXPORT_COLUMNS = [
    "id",
    "at",
    "actor",
    "action",
    "entity_type",
    "entity_id",
    "summary",
    "reason",
    "ip",
]


def csv_cell(v) -> str:
    """Spreadsheet-safe: a value starting with = + - @ would run as a formula in Excel."""
    s = v.isoformat() if isinstance(v, datetime) else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/audit/export")
def export_audit(p: require("audit.export"), db: DB, query: AuditQuery) -> Response:
    rows = db.scalars(query.order_by(AuditEvent.id.desc()).limit(EXPORT_LIMIT)).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(EXPORT_COLUMNS)
    for e in rows:
        ev = _event(e)
        w.writerow([csv_cell(ev[c]) for c in EXPORT_COLUMNS])
    record(db, p.user, "audit.export", summary=f"{len(rows)} events exported")
    db.commit()
    return Response(
        "﻿" + out.getvalue(),  # byte-order mark: Excel then reads UTF-8 correctly
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )


# ---------- company settings ----------

# Region/City zones only: Python also lists aliases such as "Factory" and "posix/…" that browsers
# reject, and one of those saved would break every date on screen.
TIMEZONES = sorted(
    {"UTC"}
    | {
        z
        for z in available_timezones()
        if "/" in z and not z.startswith(("posix/", "right/", "Etc/"))
    }
)
_TZ_SET = set(TIMEZONES)


class CompanyPatch(BaseModel):
    version: int
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)]
        | None
    ) = None
    timezone: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")


def _company(c: Company) -> dict:
    return {"name": c.name, "timezone": c.timezone, "currency": c.currency, "version": c.version}


@router.get("/company")
def get_company(_: require("settings.view"), db: DB) -> dict:
    return _company(db.get(Company, 1)) | {"timezones": TIMEZONES}


@router.patch("/company")
def update_company(body: CompanyPatch, p: require("settings.configure"), db: DB) -> dict:
    c = db.get(Company, 1, with_for_update=True)
    if c.version != body.version:
        raise stale()
    if body.timezone is not None and body.timezone not in _TZ_SET:
        raise field_error("timezone", "Unknown time zone")
    before = _company(c)
    for k in ("name", "timezone", "currency"):
        if (v := getattr(body, k)) is not None:
            setattr(c, k, v)
    c.version += 1
    changes = diff({k: before[k] for k in ("name", "timezone", "currency")},
                   {k: getattr(c, k) for k in ("name", "timezone", "currency")})  # fmt: skip
    record(db, p.user, "company.update", "company", 1, "Company settings changed", changes)
    db.commit()
    return _company(c)
