"""Phase 3: bills of materials, assembly orders and the quality gate.

An assembly order: choose the battery model and its active BOM → release (components reserved,
or a shortage list that can become a purchase request) → issue materials to the assembly floor →
build batteries one by one (serial number, components consumed and linked) → inspect each
(pass / hold / rework) → return unused materials → complete.

The gate: a finished battery reaches available stock only through a passing inspection, which
needs every required checklist item done, every required reading taken, and every reading within
the limits SMET has confirmed. Limits not yet confirmed are recorded, never applied or invented.
"""

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, require
from app.inventory import (
    Key,
    Lot,
    SerialNo,
    Serials,
    active_location,
    check_free,
    claim_key,
    conflict,
    free,
    label,
    lock_items,
    new_serial,
    post,
    release_for,
    remember,
    reserve,
    serial_row,
    serials_at,
    warehouse_location,
    whole,
)
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, page
from app.models import (
    AssemblyOrder,
    Bom,
    BomLine,
    ChecklistItem,
    Inspection,
    Item,
    Location,
    Reservation,
    RolePermission,
    Serial,
    SerialComponent,
    StockMove,
    TestParameter,
    User,
    UserRole,
    Warehouse,
    utcnow,
)
from app.procurement import PRIn, PRLineIn, create_pr, pr_row

router = APIRouter(tags=["assembly"], route_class=NumbersRoute)

Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
ZERO = Decimal(0)


def _items(db: Session, ids) -> dict:
    return {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(set(ids))))}


# ---------- bills of materials ----------


class BomLineIn(BaseModel):
    component_id: uuid.UUID
    qty: Qty
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class BomIn(BaseModel):
    item_id: uuid.UUID
    notes: Notes = ""
    lines: Annotated[list[BomLineIn], Field(min_length=1, max_length=200)]


class BomPut(BaseModel):
    notes: Notes = ""
    lines: Annotated[list[BomLineIn], Field(min_length=1, max_length=200)]


def bom_row(db: Session, b: Bom, full: bool = True) -> dict:
    item = db.get(Item, b.item_id)
    row = {"id": b.id, "item": item_ref(item), "version": b.version, "status": b.status, "notes": b.notes,
           "created_at": b.created_at, "activated_at": b.activated_at}  # fmt: skip
    lines = db.scalars(select(BomLine).where(BomLine.bom_id == b.id).order_by(BomLine.id)).all()
    if full:
        comps = _items(db, [ln.component_id for ln in lines])
        row["lines"] = [{"id": ln.id, "component": item_ref(comps[ln.component_id]), "qty": ln.qty, "notes": ln.notes}
                        for ln in lines]  # fmt: skip
    else:
        row["line_count"] = len(lines)
    return row


def _check_bom(db: Session, item: Item, lines: list[BomLineIn]) -> None:
    if item.kind != "battery_model" or not item.assembled:
        raise field_error("item_id", f"{item.code} is not a battery model assembled by SMET")
    ids = [ln.component_id for ln in lines]
    if len(set(ids)) != len(ids):
        raise field_error("lines", "Each component only once; put the total quantity on one line")
    comps = _items(db, ids)
    if len(comps) != len(set(ids)) or any(not c.active for c in comps.values()):
        raise field_error("lines", "Unknown or inactive component")
    if item.id in comps:
        raise field_error("lines", "A battery can't be a component of itself")
    for ln in lines:
        c = comps[ln.component_id]
        if c.tracking == "serial" and ln.qty != ln.qty.to_integral_value():
            raise field_error("lines", f"{c.code} is counted in whole units")


@router.get("/boms")
def list_boms(_: require("bom.view"), db: DB, item_id: uuid.UUID | None = None, limit: Limit = 50,
              offset: Offset = 0) -> dict:  # fmt: skip
    query = select(Bom)
    if item_id:
        query = query.where(Bom.item_id == item_id)
    order = case((Bom.status == "active", 0), (Bom.status == "draft", 1), else_=2)
    return page(
        db,
        query.order_by(order, Bom.version.desc()),
        limit,
        offset,
        lambda b: bom_row(db, b, full=False),
    )


@router.get("/boms/{bom_id}")
def get_bom(bom_id: uuid.UUID, _: require("bom.view"), db: DB) -> dict:
    return bom_row(db, get_or_404(db, Bom, bom_id, "Bill of materials"))


@router.post("/boms", status_code=status.HTTP_201_CREATED)
def create_bom(body: BomIn, p: require("bom.configure"), db: DB) -> dict:
    """A new draft version. Copy the lines of the current version to change it."""
    item = lock_items(db, [body.item_id])[body.item_id]
    _check_bom(db, item, body.lines)
    version = (db.scalar(select(func.max(Bom.version)).where(Bom.item_id == item.id)) or 0) + 1
    b = Bom(item_id=item.id, version=version, notes=body.notes, created_by=p.user.id)
    db.add(b)
    db.flush()
    db.add_all(BomLine(bom_id=b.id, **ln.model_dump()) for ln in body.lines)
    record(db, p.user, "bom.create", "bom", b.id, f"{item.code} BOM v{version} drafted")
    db.commit()
    return bom_row(db, b)


@router.put("/boms/{bom_id}")
def edit_bom(bom_id: uuid.UUID, body: BomPut, p: require("bom.configure"), db: DB) -> dict:
    b = get_or_404(db, Bom, bom_id, "Bill of materials", lock=True)
    if b.status != "draft":
        raise conflict(f"Version {b.version} is {b.status} and can't change: draft a new version")
    item = db.get(Item, b.item_id)
    _check_bom(db, item, body.lines)
    b.notes = body.notes
    db.query(BomLine).filter(BomLine.bom_id == b.id).delete()
    db.add_all(BomLine(bom_id=b.id, **ln.model_dump()) for ln in body.lines)
    record(db, p.user, "bom.edit", "bom", b.id, f"{item.code} BOM v{b.version} changed")
    db.commit()
    return bom_row(db, b)


@router.post("/boms/{bom_id}/activate")
def activate_bom(bom_id: uuid.UUID, p: require("bom.configure"), db: DB) -> dict:
    """This version becomes the one new assembly orders use; the previous one is retired. Orders
    already planned keep the version they were planned with."""
    b = get_or_404(db, Bom, bom_id, "Bill of materials", lock=True)
    if b.status != "draft":
        raise conflict(f"Version {b.version} is {b.status}")
    item = lock_items(db, [b.item_id])[b.item_id]
    for old in db.scalars(
        select(Bom).where(Bom.item_id == item.id, Bom.status == "active").with_for_update()
    ):
        old.status = "retired"
    db.flush()
    b.status, b.activated_by, b.activated_at = "active", p.user.id, utcnow()
    record(db, p.user, "bom.activate", "bom", b.id, f"{item.code} BOM v{b.version} made active")
    db.commit()
    return bom_row(db, b)


@router.post("/boms/{bom_id}/retire")
def retire_bom(bom_id: uuid.UUID, p: require("bom.configure"), db: DB) -> dict:
    b = get_or_404(db, Bom, bom_id, "Bill of materials", lock=True)
    if b.status == "retired":
        raise conflict("Already retired")
    b.status = "retired"
    record(db, p.user, "bom.retire", "bom", b.id, f"BOM v{b.version} retired")
    db.commit()
    return bom_row(db, b)


# ---------- test parameters and checklists ----------


class ParameterIn(BaseModel):
    id: uuid.UUID | None = None
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = ""
    min_value: Annotated[Decimal, Field(max_digits=14, decimal_places=4)] | None = None
    max_value: Annotated[Decimal, Field(max_digits=14, decimal_places=4)] | None = None
    required: bool = True
    confirmed: bool = False
    active: bool = True


class CheckIn(BaseModel):
    id: uuid.UUID | None = None
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    required: bool = True
    active: bool = True


class QualityPut(BaseModel):
    parameters: Annotated[list[ParameterIn], Field(max_length=100)]
    checklist: Annotated[list[CheckIn], Field(max_length=100)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def _quality(db: Session, item_id) -> dict:
    params = db.scalars(select(TestParameter).where(TestParameter.item_id == item_id)
                        .order_by(TestParameter.active.desc(), TestParameter.sort)).all()  # fmt: skip
    checks = db.scalars(select(ChecklistItem).where(ChecklistItem.item_id == item_id)
                        .order_by(ChecklistItem.active.desc(), ChecklistItem.sort)).all()  # fmt: skip
    return {
        "parameters": [{"id": x.id, "name": x.name, "unit": x.unit, "min_value": x.min_value, "max_value": x.max_value,
                        "required": x.required, "confirmed": x.confirmed, "active": x.active, "version": x.version}
                       for x in params],
        "checklist": [{"id": x.id, "text": x.text, "required": x.required, "active": x.active} for x in checks],
    }  # fmt: skip


@router.get("/items/{item_id}/quality")
def get_quality(item_id: uuid.UUID, _: require("items.view"), db: DB) -> dict:
    item = get_or_404(db, Item, item_id, "Item")
    return {"item": item_ref(item)} | _quality(db, item.id)


@router.put("/items/{item_id}/quality")
def set_quality(
    item_id: uuid.UUID, body: QualityPut, p: require("quality.configure"), db: DB
) -> dict:
    """Rows left out are switched off, never deleted: past inspections keep the limits they used."""
    item = lock_items(db, [item_id])[item_id]
    if not item.assembled:
        raise field_error(
            "item_id", "Final inspection is set up for battery models assembled by SMET"
        )
    for x in body.parameters:
        if x.min_value is not None and x.max_value is not None and x.min_value > x.max_value:
            raise field_error("parameters", f"{x.name}: the lowest value is above the highest")
        if x.confirmed and x.min_value is None and x.max_value is None:
            raise field_error(
                "parameters", f"{x.name}: a confirmed limit needs a lowest or highest value"
            )
    before = _quality(db, item.id)
    for model, rows, fields in ((TestParameter, body.parameters, ("name", "unit", "min_value", "max_value", "required",
                                                                  "confirmed", "active")),
                                (ChecklistItem, body.checklist, ("text", "required", "active"))):  # fmt: skip
        existing = {x.id: x for x in db.scalars(select(model).where(model.item_id == item.id))}
        if any(r.id and r.id not in existing for r in rows):
            raise field_error("parameters", "A row belongs to another item")
        kept = set()
        for n, r in enumerate(rows):
            x = existing.get(r.id) if r.id else None
            if x is None:
                x = model(item_id=item.id)
                db.add(x)
            for f in fields:
                setattr(x, f, getattr(r, f))
            x.sort = n
            if model is TestParameter:
                x.version = (x.version or 0) + 1
            kept.add(r.id)
        for x in existing.values():
            if x.id not in kept:
                x.active = False
    db.flush()
    after = _quality(db, item.id)
    record(db, p.user, "quality.configure", "item", item.id, f"{item.code}: test parameters and checklist changed",
           {"before": _plain(before), "after": _plain(after)}, body.reason)  # fmt: skip
    db.commit()
    return {"item": item_ref(item)} | after


def _plain(v):
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items() if k != "version"}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return str(v) if isinstance(v, (Decimal, uuid.UUID)) else v


# ---------- assembly orders ----------


class OrderIn(BaseModel):
    item_id: uuid.UUID
    qty_planned: int = Field(ge=1, le=10_000)
    warehouse_id: uuid.UUID
    source: Literal["replenishment", "sales_order", "project"] = "replenishment"
    source_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    responsible_id: uuid.UUID | None = None
    target_date: date | None = None
    notes: Notes = ""


class Versioned(BaseModel):
    version: int


class Closing(BaseModel):
    version: int
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class MaterialLine(BaseModel):
    item_id: uuid.UUID
    qty: Qty
    location_id: uuid.UUID | None = (
        None  # where from (issue) or where to (return); default: main store
    )
    lot: Lot = ""
    serials: Serials = []


def once_each(rows: list) -> list:
    """Each item (and lot) once per request: quantities are checked per line against what the order
    holds, so a repeated line could take another order's material."""
    seen = [(r.item_id, r.lot) for r in rows]
    if len(set(seen)) != len(seen):
        raise ValueError("Each item and lot only once: put the total on one line")
    return rows


class MaterialsIn(BaseModel):
    key: Key
    lines: Annotated[
        list[MaterialLine], Field(min_length=1, max_length=200), AfterValidator(once_each)
    ]


class Part(BaseModel):
    item_id: uuid.UUID
    qty: Qty
    lot: Lot = ""
    serials: Serials = []


class UnitIn(BaseModel):
    key: Key
    serial: SerialNo | None = None  # none: the next SMET number
    parts: Annotated[list[Part], Field(min_length=1, max_length=200), AfterValidator(once_each)]
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


def wip(db: Session, ao: AssemblyOrder) -> dict[tuple, Decimal]:
    """What this order has on the assembly floor, per (item, lot): issued − returned − consumed."""
    sign = case((StockMove.kind == "issue", StockMove.qty), else_=-StockMove.qty)
    rows = db.execute(select(StockMove.item_id, StockMove.lot, func.sum(sign))
                      .where(StockMove.doc_type == "assembly_order", StockMove.doc_id == str(ao.id),
                             StockMove.kind.in_(("issue", "return", "consume")))
                      .group_by(StockMove.item_id, StockMove.lot))  # fmt: skip
    return {(i, lot): q for i, lot, q in rows if q}


def _moved(db: Session, ao: AssemblyOrder) -> dict:
    out: dict = defaultdict(lambda: defaultdict(lambda: ZERO))
    for item_id, kind, q in db.execute(select(StockMove.item_id, StockMove.kind, func.sum(StockMove.qty))
                                       .where(StockMove.doc_type == "assembly_order", StockMove.doc_id == str(ao.id))
                                       .group_by(StockMove.item_id, StockMove.kind)):  # fmt: skip
        out[item_id][kind] = q
    return out


def order_row(db: Session, ao: AssemblyOrder, full: bool = False) -> dict:
    item, wh, b = (
        db.get(Item, ao.item_id),
        db.get(Warehouse, ao.warehouse_id),
        db.get(Bom, ao.bom_id),
    )
    units = dict(db.execute(select(Serial.status, func.count()).where(Serial.assembly_order_id == ao.id,
                            Serial.item_id == ao.item_id).group_by(Serial.status)).all())  # fmt: skip
    names = dict(
        db.execute(
            select(User.id, User.name).where(User.id.in_([ao.responsible_id, ao.created_by]))
        ).all()
    )
    built = sum(units.values())
    row = {"id": ao.id, "number": ao.number, "item": item_ref(item), "bom": {"id": b.id, "version": b.version},
           "warehouse": {"id": wh.id, "code": wh.code}, "qty_planned": ao.qty_planned, "qty_built": built,
           "qty_passed": units.get("available", 0), "units_by_status": units, "status": ao.status,
           "source": ao.source, "source_ref": ao.source_ref, "responsible_id": ao.responsible_id,
           "responsible": names.get(ao.responsible_id), "target_date": ao.target_date, "notes": ao.notes,
           "created_by": names.get(ao.created_by), "created_at": ao.created_at, "version": ao.version}  # fmt: skip
    if full:
        lines = db.scalars(select(BomLine).where(BomLine.bom_id == b.id).order_by(BomLine.id)).all()
        comps = _items(db, [ln.component_id for ln in lines])
        res = {
            r.item_id: r
            for r in db.scalars(
                select(Reservation).where(
                    Reservation.ref_type == "assembly_order", Reservation.ref_id == str(ao.id)
                )
            )
        }
        moved, floor = _moved(db, ao), wip(db, ao)
        row["materials"] = [{
            "item": item_ref(comps[ln.component_id]), "per_unit": ln.qty, "needed": ln.qty * ao.qty_planned,
            "reserved_left": (res[ln.component_id].qty - res[ln.component_id].qty_used)
                             if ln.component_id in res and res[ln.component_id].status == "active" else ZERO,
            "issued": moved[ln.component_id]["issue"], "consumed": moved[ln.component_id]["consume"],
            "returned": moved[ln.component_id]["return"],
            "on_floor": sum((q for (i, _), q in floor.items() if i == ln.component_id), ZERO),
            "on_floor_lots": [{"lot": lot, "qty": q} for (i, lot), q in floor.items() if i == ln.component_id and lot],
            "free": free(db, ln.component_id),
        } for ln in lines]  # fmt: skip
        row["units"] = [{"id": s.id, "serial": s.serial, "status": s.status, "created_at": s.created_at}
                        for s in db.scalars(select(Serial).where(Serial.assembly_order_id == ao.id,
                                                                 Serial.item_id == ao.item_id).order_by(Serial.created_at))]  # fmt: skip
        floor_serials = db.execute(
            select(Serial.serial, Serial.item_id).where(
                Serial.assembly_order_id == ao.id,
                Serial.item_id != ao.item_id,
                Serial.status == "issued",
            )
        ).all()
        row["serials_on_floor"] = [{"serial": s, "item_id": i} for s, i in floor_serials]
        row["assembly_location"] = label(db, warehouse_location(db, wh.id, "assembly"))
    return row


@router.get("/assembly-orders/people")
def assembly_people(_: require("assembly.view"), db: DB) -> dict:
    """Who can be responsible for an order: active users allowed to work on assembly orders."""
    rows = db.execute(select(User.id, User.name).join(UserRole, UserRole.user_id == User.id)
                      .join(RolePermission, RolePermission.role_id == UserRole.role_id)
                      .where(User.active, RolePermission.permission == "assembly.edit")
                      .distinct().order_by(User.name)).all()  # fmt: skip
    return {"items": [{"id": i, "name": n} for i, n in rows]}


@router.get("/assembly-orders")
def list_orders(
    _: require("assembly.view"),
    db: DB,
    status_: Annotated[str, Query(alias="status", max_length=60)] = "",
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(AssemblyOrder)
    if status_:
        query = query.where(AssemblyOrder.status.in_(status_.split(",")))
    counts = dict(
        db.execute(select(AssemblyOrder.status, func.count()).group_by(AssemblyOrder.status)).all()
    )
    return page(
        db,
        query.order_by(AssemblyOrder.created_at.desc()),
        limit,
        offset,
        lambda a: order_row(db, a),
    ) | {"counts": counts}


@router.get("/assembly-orders/{order_id}")
def get_order(order_id: uuid.UUID, _: require("assembly.view"), db: DB) -> dict:
    return order_row(db, get_or_404(db, AssemblyOrder, order_id, "Assembly order"), full=True)


@router.post("/assembly-orders", status_code=status.HTTP_201_CREATED)
def plan_order(body: OrderIn, p: require("assembly.create"), db: DB) -> dict:
    item = db.get(Item, body.item_id)
    if item is None or not item.assembled or not item.active:
        raise field_error("item_id", "Choose an active battery model assembled by SMET")
    b = db.scalar(select(Bom).where(Bom.item_id == item.id, Bom.status == "active"))
    if b is None:
        raise field_error("item_id", f"{item.code} has no active bill of materials yet")
    wh = db.get(Warehouse, body.warehouse_id)
    if wh is None or not wh.active:
        raise field_error("warehouse_id", "Choose an active warehouse")
    warehouse_location(db, wh.id, "assembly")
    if body.responsible_id:
        u = db.get(User, body.responsible_id)
        if u is None or not u.active:
            raise field_error("responsible_id", "Choose an active user")
    ao = AssemblyOrder(
        number=next_number(db, "assembly_order"),
        bom_id=b.id,
        created_by=p.user.id,
        **body.model_dump(),
    )
    db.add(ao)
    db.flush()
    record(db, p.user, "assembly_order.create", "assembly_order", ao.id,
           f"{ao.number}: {ao.qty_planned} × {item.code} planned (BOM v{b.version})")  # fmt: skip
    db.commit()
    return order_row(db, ao, full=True)


def _order(db: Session, order_id, version: int | None = None, statuses=None) -> AssemblyOrder:
    ao = get_or_404(db, AssemblyOrder, order_id, "Assembly order", lock=True)
    if version is not None and ao.version != version:
        raise stale()
    if statuses and ao.status not in statuses:
        raise conflict(f"{ao.number} is {ao.status.replace('_', ' ')}")
    return ao


def _bom_lines(db: Session, ao: AssemblyOrder) -> list[BomLine]:
    return db.scalars(select(BomLine).where(BomLine.bom_id == ao.bom_id)).all()


def shortages(db: Session, ao: AssemblyOrder) -> list[dict]:
    lines = _bom_lines(db, ao)
    items = _items(db, [ln.component_id for ln in lines])
    out = []
    for ln in lines:
        need, have = ln.qty * ao.qty_planned, free(db, ln.component_id)
        if have < need:
            out.append({"item": item_ref(items[ln.component_id]), "needed": need, "free": max(have, ZERO),
                        "short": need - max(have, ZERO)})  # fmt: skip
    return out


@router.post("/assembly-orders/{order_id}/release")
def release_order(
    order_id: uuid.UUID, body: Versioned, p: require("assembly.create"), db: DB
) -> dict:
    """Reserve every component for the whole order, or refuse with what is short."""
    ao = _order(db, order_id, body.version, ("draft",))
    lines = _bom_lines(db, ao)
    items = lock_items(db, [ln.component_id for ln in lines])
    short = shortages(db, ao)
    if short:
        raise HTTPException(status.HTTP_409_CONFLICT, jsonable_encoder({
            "message": "Not enough components to release: " + ", ".join(
                f"{s['item']['code']} short by {s['short'].normalize():f}" for s in short),
            "shortages": [s | {"item": s["item"]["code"]} for s in short]}))  # fmt: skip
    for ln in lines:
        reserve(
            db,
            p.user,
            items[ln.component_id],
            ln.qty * ao.qty_planned,
            "assembly_order",
            str(ao.id),
            ao.number,
        )
    ao.status, ao.version = "released", ao.version + 1
    record(
        db,
        p.user,
        "assembly_order.release",
        "assembly_order",
        ao.id,
        f"{ao.number} released, components reserved",
    )
    db.commit()
    return order_row(db, ao, full=True)


@router.post("/assembly-orders/{order_id}/shortage-request", status_code=status.HTTP_201_CREATED)
def request_shortage(
    order_id: uuid.UUID, p: require("purchasing.create", "assembly.view"), db: DB
) -> dict:
    """A purchase request for what this order is short of."""
    ao = _order(db, order_id, statuses=("draft",))
    short = shortages(db, ao)
    if not short:
        raise conflict("Nothing is short for this order")
    r = create_pr(
        db,
        p.user,
        PRIn(
            source="assembly",
            ref=ao.number,
            lines=[
                PRLineIn(item_id=s["item"]["id"], qty=s["short"], notes=f"For {ao.number}")
                for s in short
            ],
        ),
    )
    db.commit()
    return pr_row(db, r, full=True)


def _assembly_loc(db: Session, ao: AssemblyOrder) -> Location:
    return warehouse_location(db, ao.warehouse_id, "assembly")


def _store(db: Session, ao: AssemblyOrder, location_id) -> Location:
    loc = (
        active_location(db, location_id)
        if location_id
        else warehouse_location(db, ao.warehouse_id, "available")
    )
    if loc.purpose != "available":
        raise field_error("location_id", "Materials come from and go back to available stock")
    return loc


@router.post("/assembly-orders/{order_id}/issue")
def issue(order_id: uuid.UUID, body: MaterialsIn, p: require("assembly.edit"), db: DB) -> dict:
    """Materials to the assembly floor. Reserved quantities are used first; more than reserved is
    possible only from free stock (an extra need, e.g. a damaged part)."""
    if (prev := claim_key(db, body.key, "assembly_issue")) is not None:
        return prev
    ao = _order(db, order_id, statuses=("released", "in_progress"))
    on_bom = {ln.component_id for ln in _bom_lines(db, ao)}
    if any(ln.item_id not in on_bom for ln in body.lines):
        raise field_error(
            "item_id", "Only components on this order's bill of materials can be issued"
        )
    items = lock_items(db, [ln.item_id for ln in body.lines])
    floor, doc = _assembly_loc(db, ao), ("assembly_order", ao.id, ao.number)
    for ln in body.lines:
        item, src = items[ln.item_id], _store(db, ao, ln.location_id)
        whole(item, ln.qty, ln.serials)
        r = db.scalar(select(Reservation).where(Reservation.ref_type == "assembly_order", Reservation.ref_id == str(ao.id),
                                                Reservation.item_id == item.id, Reservation.status == "active")
                      .with_for_update())  # fmt: skip
        from_reserve = min(ln.qty, r.qty - r.qty_used) if r else ZERO
        if ln.qty > from_reserve:
            check_free(db, item, ln.qty - from_reserve)
        if r:
            r.qty_used += from_reserve
        if item.tracking == "serial":
            for s in serials_at(db, item, ln.serials, src, ("available",)):
                post(
                    db,
                    p.user,
                    item=item,
                    qty=Decimal(1),
                    src=src,
                    dst=floor,
                    kind="issue",
                    doc=doc,
                    serial=s,
                )
                s.status, s.assembly_order_id = "issued", ao.id
        else:
            post(
                db,
                p.user,
                item=item,
                qty=ln.qty,
                src=src,
                dst=floor,
                kind="issue",
                doc=doc,
                lot=ln.lot,
            )
    ao.status, ao.version = "in_progress", ao.version + 1
    record(db, p.user, "assembly_order.issue", "assembly_order", ao.id, f"{ao.number}: " + ", ".join(
        f"{items[ln.item_id].code} {ln.qty.normalize():f}" for ln in body.lines)[:250])  # fmt: skip
    result = remember(db, body.key, {"id": ao.id})
    db.commit()
    return result


@router.post("/assembly-orders/{order_id}/return")
def return_materials(
    order_id: uuid.UUID, body: MaterialsIn, p: require("assembly.edit"), db: DB
) -> dict:
    """Unused materials back to available stock."""
    if (prev := claim_key(db, body.key, "assembly_return")) is not None:
        return prev
    ao = _order(db, order_id, statuses=("released", "in_progress"))
    items = lock_items(db, [ln.item_id for ln in body.lines])
    floor, doc, have = _assembly_loc(db, ao), ("assembly_order", ao.id, ao.number), wip(db, ao)
    for ln in body.lines:
        item, dst = items[ln.item_id], _store(db, ao, ln.location_id)
        lot = ln.lot if item.tracking == "lot" else ""
        whole(item, ln.qty, ln.serials)
        if ln.qty > have.get((item.id, lot), ZERO):
            raise conflict(
                f"{ao.number} has only {have.get((item.id, lot), ZERO).normalize():f} {item.code} on the floor"
            )
        if item.tracking == "serial":
            for s in serials_at(db, item, ln.serials, floor, ("issued",)):
                if s.assembly_order_id != ao.id:
                    raise conflict(f"{s.serial} was issued to another order")
                post(
                    db,
                    p.user,
                    item=item,
                    qty=Decimal(1),
                    src=floor,
                    dst=dst,
                    kind="return",
                    doc=doc,
                    serial=s,
                )
                s.status, s.assembly_order_id = "available", None
        else:
            post(
                db,
                p.user,
                item=item,
                qty=ln.qty,
                src=floor,
                dst=dst,
                kind="return",
                doc=doc,
                lot=lot,
            )
    ao.version += 1
    record(
        db,
        p.user,
        "assembly_order.return",
        "assembly_order",
        ao.id,
        f"{ao.number}: unused materials returned",
    )
    result = remember(db, body.key, {"id": ao.id})
    db.commit()
    return result


@router.post("/assembly-orders/{order_id}/units", status_code=status.HTTP_201_CREATED)
def build_unit(order_id: uuid.UUID, body: UnitIn, p: require("assembly.edit"), db: DB) -> dict:
    """One finished battery: its serial number, the components it consumed (actual quantities,
    which may differ from the BOM) and links to each component serial. It waits for inspection."""
    if (prev := claim_key(db, body.key, "assembly_unit")) is not None:
        return prev
    ao = _order(db, order_id, statuses=("in_progress",))
    battery = lock_items(db, [ao.item_id])[ao.item_id]
    built = db.scalar(
        select(func.count()).where(Serial.assembly_order_id == ao.id, Serial.item_id == ao.item_id)
    )
    if built >= ao.qty_planned:
        raise conflict(f"{ao.number} already has all {ao.qty_planned} planned batteries")
    lines = {ln.component_id: ln for ln in _bom_lines(db, ao)}
    sent = {pt.item_id for pt in body.parts}
    if missing := [lines[i] for i in lines if i not in sent]:
        names = _items(db, [ln.component_id for ln in missing])
        raise field_error(
            "parts", "Missing from this battery: " + ", ".join(i.code for i in names.values())
        )
    if extra := sent - set(lines):
        raise field_error("parts", f"Not on the bill of materials: {len(extra)} item(s)")
    comps = lock_items(db, list(sent))
    floor, doc, have = _assembly_loc(db, ao), ("assembly_order", ao.id, ao.number), wip(db, ao)
    number = body.serial or next_number(db, "serial")
    while not body.serial and db.scalar(select(Serial.id).where(Serial.serial == number)):
        number = next_number(db, "serial")  # a typed-in or received serial already took this one
    unit = new_serial(db, battery, number, "under_assembly", assembly_order_id=ao.id)
    for pt in body.parts:
        item = comps[pt.item_id]
        lot = pt.lot if item.tracking == "lot" else ""
        whole(item, pt.qty, pt.serials)
        if pt.qty > have.get((item.id, lot), ZERO):
            raise conflict(f"Only {have.get((item.id, lot), ZERO).normalize():f} {item.code}{' lot ' + lot if lot else ''} "
                           f"issued to {ao.number} and not yet used: issue more first")  # fmt: skip
        if item.tracking == "serial":
            for s in serials_at(db, item, pt.serials, floor, ("issued",)):
                if s.assembly_order_id != ao.id:
                    raise conflict(f"{s.serial} was issued to another order")
                post(
                    db,
                    p.user,
                    item=item,
                    qty=Decimal(1),
                    src=floor,
                    dst=None,
                    kind="consume",
                    doc=doc,
                    serial=s,
                )
                s.status = "consumed"
                db.add(
                    SerialComponent(
                        serial_id=unit.id, component_id=item.id, component_serial_id=s.id, qty=1
                    )
                )
        else:
            post(
                db,
                p.user,
                item=item,
                qty=pt.qty,
                src=floor,
                dst=None,
                kind="consume",
                doc=doc,
                lot=lot,
            )
            db.add(SerialComponent(serial_id=unit.id, component_id=item.id, lot=lot, qty=pt.qty))
    post(
        db,
        p.user,
        item=battery,
        qty=Decimal(1),
        src=None,
        dst=floor,
        kind="build",
        doc=doc,
        serial=unit,
        reason=body.notes,
    )
    ao.version += 1
    record(
        db,
        p.user,
        "assembly_order.build",
        "serial",
        unit.id,
        f"{ao.number}: battery {unit.serial} built",
    )
    result = remember(db, body.key, {"id": unit.id, "serial": unit.serial})
    db.commit()
    return result


@router.post("/assembly-orders/{order_id}/complete")
def complete_order(
    order_id: uuid.UUID, body: Closing, p: require("assembly.create"), db: DB
) -> dict:
    ao = _order(db, order_id, body.version, ("in_progress", "released"))
    left = wip(db, ao)
    if left:
        raise conflict(
            f"{ao.number} still has materials on the assembly floor: return or use them first"
        )
    waiting = db.scalar(select(func.count()).where(Serial.assembly_order_id == ao.id, Serial.item_id == ao.item_id,
                                                   Serial.status.in_(("under_assembly", "rework"))))  # fmt: skip
    if waiting:
        raise conflict(
            f"{waiting} batteries of {ao.number} are still waiting for inspection or in rework"
        )
    built = db.scalar(
        select(func.count()).where(Serial.assembly_order_id == ao.id, Serial.item_id == ao.item_id)
    )
    if built < ao.qty_planned and not body.reason:
        raise field_error(
            "reason",
            f"Only {built} of {ao.qty_planned} built: say why the order is completed short",
        )
    release_for(db, "assembly_order", str(ao.id))
    ao.status, ao.version = "completed", ao.version + 1
    record(db, p.user, "assembly_order.complete", "assembly_order", ao.id,
           f"{ao.number} completed: {built} of {ao.qty_planned} built", reason=body.reason)  # fmt: skip
    db.commit()
    return order_row(db, ao, full=True)


@router.post("/assembly-orders/{order_id}/cancel")
def cancel_order(order_id: uuid.UUID, body: Closing, p: require("assembly.create"), db: DB) -> dict:
    ao = _order(db, order_id, body.version, ("draft", "released", "in_progress"))
    if not body.reason:
        raise field_error("reason", "Say why the order is cancelled")
    if db.scalar(
        select(func.count()).where(Serial.assembly_order_id == ao.id, Serial.item_id == ao.item_id)
    ):
        raise conflict(f"{ao.number} has built batteries: complete it instead")
    if wip(db, ao):
        raise conflict(f"{ao.number} still has materials on the assembly floor: return them first")
    release_for(db, "assembly_order", str(ao.id))
    ao.status, ao.version = "cancelled", ao.version + 1
    record(
        db,
        p.user,
        "assembly_order.cancel",
        "assembly_order",
        ao.id,
        f"{ao.number} cancelled",
        reason=body.reason,
    )
    db.commit()
    return order_row(db, ao, full=True)


# ---------- final inspection ----------


class Reading(BaseModel):
    parameter_id: uuid.UUID
    value: Annotated[Decimal, Field(max_digits=14, decimal_places=4)]


class Check(BaseModel):
    item_id: uuid.UUID
    done: bool


class InspectionIn(BaseModel):
    key: Key
    result: Literal["passed", "on_hold", "rework"]
    readings: Annotated[list[Reading], Field(max_length=100)] = []
    checklist: Annotated[list[Check], Field(max_length=100)] = []
    observations: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] = ""
    location_id: uuid.UUID | None = None  # where a passed battery goes; default: the main store


def judge(
    params: list[TestParameter], checks: list[ChecklistItem], body: InspectionIn
) -> tuple[list, list, list[str]]:
    """The readings and checklist as recorded, and every reason the battery can't pass."""
    got = {r.parameter_id: r.value for r in body.readings}
    done = {c.item_id: c.done for c in body.checklist}
    if unknown := (set(got) - {x.id for x in params}) | (set(done) - {x.id for x in checks}):
        raise field_error(
            "readings", f"{len(unknown)} reading(s) or check(s) don't belong to this model"
        )
    readings, problems = [], []
    for x in params:
        v = got.get(x.id)
        within = None
        if v is not None and x.confirmed:
            within = (x.min_value is None or v >= x.min_value) and (
                x.max_value is None or v <= x.max_value
            )
            if not within:
                problems.append(
                    f"{x.name} {v.normalize():f}{x.unit} is outside the confirmed limits"
                )
        if v is None and x.required:
            problems.append(f"{x.name} was not measured")
        readings.append({"parameter_id": str(x.id), "name": x.name, "unit": x.unit,
                         "value": None if v is None else str(v), "min": None if x.min_value is None else str(x.min_value),
                         "max": None if x.max_value is None else str(x.max_value), "limits_confirmed": x.confirmed,
                         "within": within})  # fmt: skip
    checklist = []
    for c in checks:
        ok = done.get(c.id, False)
        if c.required and not ok:
            problems.append(f"Not done: {c.text}")
        checklist.append({"item_id": str(c.id), "text": c.text, "required": c.required, "done": ok})
    if not params and not checks:
        problems.append("No inspection checklist or test parameters are set up for this model yet")
    return readings, checklist, problems


@router.post("/serials/{serial_id}/inspect", status_code=status.HTTP_201_CREATED)
def inspect(serial_id: uuid.UUID, body: InspectionIn, p: require("quality.create"), db: DB) -> dict:
    """Pass: to available stock (the only way an assembled battery gets there). Hold: to
    quarantine. Rework: back to the assembly floor, then inspected again."""
    if (prev := claim_key(db, body.key, "inspection")) is not None:
        return prev
    s = db.get(Serial, serial_id, with_for_update=True)
    if s is None:
        raise field_error("serial_id", "Unknown serial number")
    item = lock_items(db, [s.item_id])[s.item_id]
    if not item.assembled:
        raise conflict(
            "Final inspection is for batteries assembled by SMET; received goods are decided at receiving"
        )
    if s.status not in ("under_assembly", "rework", "on_hold"):
        raise conflict(
            f"{s.serial} is {s.status.replace('_', ' ')} and isn't waiting for inspection"
        )
    here = db.get(Location, s.location_id)
    params = db.scalars(select(TestParameter).where(TestParameter.item_id == item.id, TestParameter.active)
                        .order_by(TestParameter.sort)).all()  # fmt: skip
    checks = db.scalars(select(ChecklistItem).where(ChecklistItem.item_id == item.id, ChecklistItem.active)
                        .order_by(ChecklistItem.sort)).all()  # fmt: skip
    readings, checklist, problems = judge(params, checks, body)
    if body.result == "passed" and problems:
        raise HTTPException(
            422, {"message": f"{s.serial} can't pass: " + "; ".join(problems), "problems": problems}
        )
    wh_id = here.warehouse_id
    target = {"passed": active_location(db, body.location_id) if body.location_id else warehouse_location(db, wh_id, "available"),
              "on_hold": warehouse_location(db, wh_id, "quarantine"),
              "rework": warehouse_location(db, wh_id, "assembly")}[body.result]  # fmt: skip
    if (
        target.purpose
        != {"passed": "available", "on_hold": "quarantine", "rework": "assembly"}[body.result]
    ):
        raise field_error("location_id", "A passed battery goes to an available-stock location")
    if target.id != here.id:
        post(db, p.user, item=item, qty=Decimal(1), src=here, dst=target, kind=body.result, doc=("inspection", s.id, s.serial),
             serial=s, reason=body.observations)  # fmt: skip
    s.status = {"passed": "available", "on_hold": "on_hold", "rework": "rework"}[body.result]
    s.version += 1
    x = Inspection(serial_id=s.id, assembly_order_id=s.assembly_order_id, result=body.result, readings=readings,
                   checklist=checklist, observations=body.observations, inspector_id=p.user.id)  # fmt: skip
    db.add(x)
    db.flush()
    record(db, p.user, f"inspection.{body.result}", "serial", s.id,
           f"{s.serial}: {body.result.replace('_', ' ')}" + (f" ({len(problems)} open points)" if problems else ""),
           reason=body.observations[:500])  # fmt: skip
    result = remember(
        db, body.key, {"id": x.id, "serial_id": s.id, "status": s.status, "problems": problems}
    )
    db.commit()
    return result


@router.get("/inspections/queue")
def inspection_queue(
    _: require("assembly.view"), db: DB, limit: Limit = 100, offset: Offset = 0
) -> dict:
    """Finished batteries waiting for a decision: built, in rework, or on hold."""
    query = (select(Serial).join(Item, Item.id == Serial.item_id)
             .where(Item.assembled, Serial.status.in_(("under_assembly", "rework", "on_hold")))
             .order_by(Serial.created_at))  # fmt: skip
    return page(db, query, limit, offset, lambda s: serial_row(db, s))
