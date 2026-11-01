"""Fields SMET shapes on screen, without a software change.

- Custom fields: extra fields on a kind of record (text, long text, number, date, choice, yes/no),
  required or not. Their values sit beside the record (custom_values), show on its page, and become
  extra columns in its reports and downloads.
- Built-in fields: the optional fields listed in BUILTIN can be renamed, hidden from forms, or made
  required. "Required" is checked by the server whenever someone creates the record or empties the
  field, through every route; records the system creates by itself (a registration opened at
  handover, an order converted from a quotation) are left to the people who complete them.

Defining fields needs `fields.configure`; reading or filling values needs the record's own view or
edit permission.
"""

import math
import re
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, CurrentPrincipal, Principal, require
from app.models import (
    AmcContract,
    AssemblyOrder,
    BatteryBank,
    CustomField,
    CustomValues,
    Dispatch,
    FieldSetting,
    Item,
    Party,
    Project,
    PurchaseOrder,
    Quotation,
    SalesOrder,
    ServiceRequest,
    Site,
    SiteVisit,
    WarrantyRegistration,
    utcnow,
)

router = APIRouter(tags=["custom fields"])

# key -> (model, label, view permission, edit permission(s))
ENTITIES: dict[str, tuple[type, str, str, tuple[str, ...]]] = {
    "item": (Item, "Items", "items.view", ("items.edit",)),
    "party": (Party, "Suppliers & customers", "parties.view", ("parties.edit",)),
    "purchase_order": (PurchaseOrder, "Purchase orders", "purchasing.view", ("purchasing.create",)),
    "assembly_order": (AssemblyOrder, "Assembly orders", "assembly.view", ("assembly.create",)),
    "quotation": (Quotation, "Quotations", "sales.view", ("sales.create",)),
    "sales_order": (SalesOrder, "Sales orders", "sales.view", ("sales.create",)),
    "project": (Project, "Projects", "projects.view", ("projects.create",)),
    "site": (Site, "Sites", "projects.view", ("projects.create",)),
    "dispatch": (Dispatch, "Dispatches", "dispatch.view", ("dispatch.create",)),
    "site_visit": (SiteVisit, "Site visits", "installation.view", ("installation.create", "installation.record")),
    "bank": (BatteryBank, "Battery banks", "installation.view", ("installation.create",)),
    "warranty_registration": (WarrantyRegistration, "Warranty registrations", "warranty.view", ("warranty.create",)),
    "amc_contract": (AmcContract, "Maintenance contracts", "amc.view", ("amc.create",)),
    "service_request": (ServiceRequest, "Service requests", "service.view", ("service.create",)),
}  # fmt: skip

# Optional built-in fields SMET may rename, hide or require (the form field has the same name).
BUILTIN: dict[str, dict[str, str]] = {
    "item": {"category": "Category", "capacity_ah": "Capacity (Ah)", "voltage_v": "Voltage (V)", "chemistry": "Chemistry", "notes": "Notes"},
    "party": {"address": "Address", "terms": "Terms", "notes": "Notes"},
    "purchase_order": {"supplier_ref": "Supplier reference", "expected_ship": "Expected shipping", "expected_arrival": "Expected arrival",
                       "link_ref": "For (project or need)", "terms": "Terms", "notes": "Notes"},
    "sales_order": {"delivery_date": "Delivery date", "delivery_address": "Delivery address", "notes": "Notes"},
    "project": {"scope": "Scope", "start_date": "Start", "end_date": "End"},
    "site": {"address": "Address", "contact": "Contact", "survey_date": "Survey date", "surveyed_by": "Surveyed by", "survey_notes": "Survey notes"},
    "dispatch": {"planned_date": "Planned date", "destination": "Destination", "delivery_contact": "Delivery contact", "transporter": "Transporter", "vehicle": "Vehicle"},
    "service_request": {"contact_name": "Contact", "contact_phone": "Phone", "preferred_visit": "Preferred visit"},
    "warranty_registration": {"customer_name": "End customer's name", "customer_contact": "Phone or email", "address": "Installation address",
                              "distributor_name": "Distributor or dealer name", "sale_date": "Date of sale"},
    "amc_contract": {"reference": "Reference", "service_scope": "Service scope"},
}  # fmt: skip
MODEL_ENTITY = {m: k for k, (m, *_) in ENTITIES.items()}
KINDS = ("text", "long_text", "number", "date", "choice", "yes_no")


def _entity(name: str) -> tuple[type, str, str, tuple[str, ...]]:
    if name not in ENTITIES:
        raise HTTPException(404, "There is no such kind of record")
    return ENTITIES[name]


def _def(f: CustomField) -> dict:
    return {"id": f.id, "entity": f.entity, "key": f.key, "label": f.label, "kind": f.kind, "options": f.options,
            "required": f.required, "help": f.help, "sort": f.sort, "active": f.active, "version": f.version}  # fmt: skip


def active_fields(db: Session, entity: str) -> list[CustomField]:
    return list(db.scalars(select(CustomField).where(CustomField.entity == entity, CustomField.active)
                           .order_by(CustomField.sort, CustomField.label)))  # fmt: skip


@router.get("/fields/config")
def fields_config(_: CurrentPrincipal, db: DB) -> dict:
    """What every form needs: renamed, hidden and required built-in fields, and the custom fields."""
    out: dict = {e: {"builtin": {}, "custom": []} for e in ENTITIES}
    for s in db.scalars(select(FieldSetting)):
        if s.entity in out and s.field in BUILTIN.get(s.entity, {}):
            out[s.entity]["builtin"][s.field] = {
                "label": s.label,
                "hidden": s.hidden,
                "required": s.required,
            }
    for f in db.scalars(
        select(CustomField).where(CustomField.active).order_by(CustomField.sort, CustomField.label)
    ):
        out[f.entity]["custom"].append(_def(f))
    return out


@router.get("/fields")
def fields_admin(_: require("fields.configure"), db: DB) -> dict:
    settings = {(s.entity, s.field): s for s in db.scalars(select(FieldSetting))}
    defs = db.scalars(
        select(CustomField).order_by(CustomField.entity, CustomField.sort, CustomField.label)
    ).all()
    return {"entities": [{
        "key": e, "label": label,
        "builtin": [{"field": f, "default_label": d, "label": (s := settings.get((e, f))) and s.label or "",
                     "hidden": bool(s and s.hidden), "required": bool(s and s.required), "version": s.version if s else 0}
                    for f, d in BUILTIN.get(e, {}).items()],
        "custom": [_def(f) for f in defs if f.entity == e]} for e, (_, label, *_) in ENTITIES.items()], "kinds": KINDS}  # fmt: skip


Label = Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)]


class SettingIn(BaseModel):
    version: int
    label: Label = ""
    hidden: bool = False
    required: bool = False


@router.put("/field-settings/{entity}/{field}")
def save_setting(
    entity: str, field: str, body: SettingIn, p: require("fields.configure"), db: DB
) -> dict:
    _entity(entity)
    if field not in BUILTIN.get(entity, {}):
        raise HTTPException(404, "This field can't be changed")
    if body.hidden and body.required:
        raise field_error("hidden", "A hidden field can't be required: nobody could fill it in")
    s = db.get(FieldSetting, (entity, field), with_for_update=True)
    if (s.version if s else 0) != body.version:
        raise stale()
    if s is None:
        s = FieldSetting(entity=entity, field=field, version=0)
        db.add(s)
    before = {"label": s.label or "", "hidden": bool(s.hidden), "required": bool(s.required)}
    s.label, s.hidden, s.required, s.version = (
        body.label,
        body.hidden,
        body.required,
        (s.version or 0) + 1,
    )
    after = {"label": s.label, "hidden": s.hidden, "required": s.required}
    record(db, p.user, "fields.builtin", "field_setting", f"{entity}.{field}", f"{ENTITIES[entity][1]}: {BUILTIN[entity][field]} adjusted",
           {k: [before[k], after[k]] for k in after if before[k] != after[k]})  # fmt: skip
    try:
        db.commit()
    except IntegrityError:
        raise stale() from None
    return {"version": s.version}


class FieldIn(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    options: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    ] = Field(default=[], max_length=50)
    required: bool = False
    help: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    sort: int = Field(default=0, ge=0, le=999)
    active: bool = True


class NewField(FieldIn):
    entity: str
    kind: Literal["text", "long_text", "number", "date", "choice", "yes_no"]


def _checked_options(kind: str, options: list[str]) -> list[str]:
    if kind == "choice" and not options:
        raise field_error("options", "A choice field needs at least one option")
    if len(set(options)) != len(options):
        raise field_error("options", "Each option once")
    return options if kind == "choice" else []


@router.post("/custom-fields", status_code=status.HTTP_201_CREATED)
def add_field(body: NewField, p: require("fields.configure"), db: DB) -> dict:
    _entity(body.entity)
    key = re.sub(r"[^a-z0-9]+", "_", body.label.lower()).strip("_")[:40] or "field"
    taken = set(db.scalars(select(CustomField.key).where(CustomField.entity == body.entity)))
    base, n = key, 2
    while key in taken:
        key, n = f"{base[:36]}_{n}", n + 1
    f = CustomField(entity=body.entity, key=key, kind=body.kind, label=body.label, options=_checked_options(body.kind, body.options),
                    required=body.required, help=body.help, sort=body.sort, active=body.active)  # fmt: skip
    db.add(f)
    try:
        db.flush()
    except IntegrityError:  # the same field added twice at once
        raise stale() from None
    record(
        db,
        p.user,
        "fields.create",
        "custom_field",
        f.id,
        f"{ENTITIES[body.entity][1]}: field '{f.label}' ({f.kind}) added",
    )
    db.commit()
    return _def(f)


class FieldPut(FieldIn):
    version: int


@router.put("/custom-fields/{field_id}")
def edit_field(field_id: uuid.UUID, body: FieldPut, p: require("fields.configure"), db: DB) -> dict:
    """Label, options, required, help, order and on/off. The kind and key stay, so values keep their meaning."""
    f = db.get(CustomField, field_id, with_for_update=True)
    if f is None:
        raise HTTPException(404, "Field not found")
    if f.version != body.version:
        raise stale()
    before = _def(f)
    f.label, f.options, f.required = (
        body.label,
        _checked_options(f.kind, body.options),
        body.required,
    )
    f.help, f.sort, f.active, f.version = body.help, body.sort, body.active, f.version + 1
    after = _def(f)
    record(db, p.user, "fields.update", "custom_field", f.id, f"{ENTITIES[f.entity][1]}: field '{f.label}' changed",
           {k: [before[k], after[k]] for k in ("label", "options", "required", "help", "sort", "active") if before[k] != after[k]})  # fmt: skip
    db.commit()
    return _def(f)


# Finished records keep their extra details as they were, like the record itself.
LOCKED: dict[str, set[str]] = {
    "purchase_order": {"closed", "cancelled", "rejected"},
    "assembly_order": {"completed", "cancelled"},
    "sales_order": {"closed", "cancelled"},
    "dispatch": {"delivered", "cancelled"},
    "site_visit": {"completed", "cancelled"},
    "warranty_registration": {"approved", "rejected", "ended"},
    "amc_contract": {"renewed", "cancelled"},
    "service_request": {"closed"},
}


def _record(db: Session, p: Principal, entity: str, record_id: uuid.UUID, write: bool):
    model, label, view, edit = _entity(entity)
    if not (any(x in p.permissions for x in edit) if write else view in p.permissions):
        raise HTTPException(403, "You don't have permission for this record")
    obj = db.get(model, record_id)
    if obj is None:
        raise HTTPException(404, "Record not found")
    if write:
        if entity == "site_visit":  # technicians: only visits they are on, as for the visit record
            from app.installation import check_may_work  # noqa: PLC0415

            check_may_work(p, obj)
        locked = getattr(obj, "status", "") in LOCKED.get(entity, set())
        if locked and not (
            entity == "warranty_registration" and "warranty.correct" in p.permissions
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"This record is {obj.status.replace('_', ' ')}: its details can't change",
            )


@router.get("/custom-values/{entity}/{record_id}")
def get_values(entity: str, record_id: uuid.UUID, p: CurrentPrincipal, db: DB) -> dict:
    _record(db, p, entity, record_id, write=False)
    return _values_row(db, entity, record_id)


def _values_row(db: Session, entity: str, record_id: uuid.UUID) -> dict:
    v = db.get(CustomValues, (entity, record_id))
    defs = active_fields(db, entity)
    return {"fields": [_def(f) for f in defs], "values": (v.values if v else {}), "version": v.version if v else 0,
            "missing": [f.label for f in defs if f.required and (v is None or v.values.get(f.key) in (None, ""))]}  # fmt: skip


def _value(f: CustomField, v, stored=None):
    """The value in its stored form, or a field error. A value already stored is kept as it is (an
    option removed later doesn't make the record unsaveable)."""
    if v is None or v == "":
        return None
    if v == stored and type(v) is type(stored):
        return v
    try:
        if f.kind in ("text", "long_text", "choice") and not isinstance(v, str):
            if f.kind == "choice" or isinstance(v, bool) or not isinstance(v, int | float):
                raise ValueError
        if f.kind in ("text", "long_text"):
            s = str(v).strip()
            if len(s) > (4000 if f.kind == "long_text" else 300):
                raise ValueError("too long")
            return s or None
        if f.kind == "number":
            if isinstance(v, bool):
                raise ValueError
            if not isinstance(v, int | float | str):
                raise ValueError
            n = float(v) if "." in str(v) or "e" in str(v).lower() else int(v)
            if not math.isfinite(n) or abs(n) > 1e15:
                raise ValueError
            return n
        if f.kind == "date":
            return date.fromisoformat(str(v)).isoformat()
        if f.kind == "yes_no":
            if not isinstance(v, bool):
                raise ValueError
            return v
        if f.kind == "choice":
            if v not in f.options:
                raise ValueError
            return v
    except (ValueError, TypeError, OverflowError):
        raise field_error(
            f"values.{f.key}", f"{f.label}: not a valid {f.kind.replace('_', ' ')}"
        ) from None
    return None


class ValuesIn(BaseModel):
    version: int
    values: dict[str, object] = Field(max_length=200)


@router.put("/custom-values/{entity}/{record_id}")
def save_values(
    entity: str, record_id: uuid.UUID, body: ValuesIn, p: CurrentPrincipal, db: DB
) -> dict:
    _record(db, p, entity, record_id, write=True)
    defs = {f.key: f for f in active_fields(db, entity)}
    if extra := set(body.values) - set(defs):
        raise field_error("values", f"Unknown field {', '.join(sorted(extra))}")
    row = db.get(CustomValues, (entity, record_id), with_for_update=True)
    if (row.version if row else 0) != body.version:
        raise stale()
    old = dict(row.values) if row else {}
    new = dict(old)  # values of fields switched off since are kept, not erased
    for k, f in defs.items():
        if k in body.values:
            new[k] = _value(f, body.values[k], old.get(k))
        if f.required and new.get(k) in (None, ""):
            raise field_error(f"values.{k}", f"{f.label} is required")
    new = {k: v for k, v in new.items() if v is not None}
    if row is None:
        row = CustomValues(entity=entity, record_id=record_id, values=new, version=1)
        db.add(row)
    else:
        row.values, row.version, row.updated_at = new, row.version + 1, utcnow()
    changes = {
        defs[k].label if k in defs else k: [old.get(k), new.get(k)]
        for k in old.keys() | new.keys()
        if old.get(k) != new.get(k)
    }
    record(db, p.user, "fields.values", entity, record_id, f"{ENTITIES[entity][1][:-1]}: extra fields updated", changes)  # fmt: skip
    try:
        db.commit()
    except IntegrityError:
        raise stale() from None
    return _values_row(db, entity, record_id)


def values_for(db: Session, entity: str, ids: list) -> dict:
    """{record id: values} for a page of report rows."""
    ids = [i if isinstance(i, uuid.UUID) else uuid.UUID(str(i)) for i in ids if i]
    if not ids:
        return {}
    rows = db.execute(select(CustomValues.record_id, CustomValues.values).where(CustomValues.entity == entity,
                                                                                CustomValues.record_id.in_(ids)))  # fmt: skip
    return {str(r): v for r, v in rows}


# ---------- built-in fields made required: checked on every write ----------


# Records the system makes by itself carry `skip_required = True` (set before they are added).
# Each flush notes what was created or which required fields changed; the commit then checks the
# final values, so routes that fill a record over several steps aren't stopped half-way.
def _required(session: Session) -> set:
    if "required_fields" not in session.info:
        with session.no_autoflush:
            session.info["required_fields"] = {
                (s.entity, s.field) for s in session.scalars(select(FieldSetting).where(FieldSetting.required))
                if s.field in BUILTIN.get(s.entity, {})}  # fmt: skip
    return session.info["required_fields"]


@event.listens_for(Session, "after_flush")
def _note_required(session: Session, _ctx) -> None:
    objs = [
        o
        for o in (*session.new, *session.dirty)
        if type(o) in MODEL_ENTITY and not getattr(o, "skip_required", False)
    ]
    if not objs or not (required := _required(session)):
        return
    pending = session.info.setdefault("check_required", {})
    for o in objs:
        entity = MODEL_ENTITY[type(o)]
        fields = {f for f in BUILTIN.get(entity, {}) if (entity, f) in required}
        if o not in session.new:
            state = inspect(o)
            fields = {f for f in fields if state.attrs[f].history.has_changes()}
        if fields:
            pending.setdefault(id(o), (o, set()))[1].update(fields)


@event.listens_for(Session, "before_commit")
def _check_required(session: Session) -> None:
    if session.new or session.dirty:  # commit flushes after this event: flush now so it is noted
        session.flush()
    for o, fields in session.info.pop("check_required", {}).values():
        entity = MODEL_ENTITY[type(o)]
        for field in sorted(fields):
            v = getattr(o, field)
            if v is None or (isinstance(v, str) and not v.strip()):
                with session.no_autoflush:
                    label = session.get(FieldSetting, (entity, field))
                raise field_error(
                    field, f"{(label and label.label) or BUILTIN[entity][field]} is required"
                )


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _forget(session: Session) -> None:
    session.info.pop("required_fields", None)
    session.info.pop("check_required", None)
