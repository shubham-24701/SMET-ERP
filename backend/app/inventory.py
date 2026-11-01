"""Stock: the ledger and everything that changes it.

Every change of stock is one row in `stock_moves` (append-only) plus the matching change to
`stock_balances` in the same transaction. A balance can never go below zero (CHECK constraint
and a guarded UPDATE). Operations that touch an item's availability lock the item row first, in
id order, so two people reserving or issuing the last units are served one after the other.

Available stock = what sits in `available` locations − what active reservations still hold.
"""

import csv
import io
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import csv_cell, field_error, stale
from app.audit import record
from app.auth import DB, Principal, require
from app.masters import (
    PURPOSE_LABEL,
    Limit,
    NumbersRoute,
    Offset,
    get_or_404,
    item_ref,
    next_number,
    page,
    rules,
)
from app.models import (
    Inspection,
    Item,
    Location,
    OperationKey,
    PurchaseOrder,
    PurchaseOrderLine,
    Reservation,
    Serial,
    SerialComponent,
    StockAdjustment,
    StockAdjustmentLine,
    StockBalance,
    StockCount,
    StockCountLine,
    StockMove,
    User,
    Warehouse,
    utcnow,
)

router = APIRouter(tags=["stock"], route_class=NumbersRoute)

Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Key = Annotated[str, StringConstraints(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
Lot = Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
NeededReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)
]
SerialNo = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=60)
]
Serials = Annotated[list[SerialNo], Field(max_length=1000)]

ZERO = Decimal(0)
GONE = ("consumed", "written_off", "returned_to_supplier")  # no longer in SMET's stock
# Units a correction may write off: not those an assembly order is working with.
CORRECTABLE = ("available", "awaiting_inspection", "on_hold", "rejected")
# The status a serial takes when it lands in a location of each purpose (adjustments, counts).
STATUS_BY_PURPOSE = {"available": "available", "inspection": "awaiting_inspection",
                     "quarantine": "on_hold", "rejected": "rejected", "assembly": "issued",
                     "repair": "on_hold", "returns": "on_hold"}  # fmt: skip


def conflict(msg: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, msg)


# ---------- building blocks used by purchasing and assembly too ----------


def claim_key(db: Session, key: str, scope: str) -> dict | None:
    """First use of `key`: None, and the key is held for this transaction. A repeat (double
    click, retry after a timeout): the stored result, and nothing is posted again."""
    try:
        with db.begin_nested():
            db.add(OperationKey(key=key, scope=scope))
            db.flush()
    except IntegrityError:
        prev = db.get(OperationKey, key)
        if prev is None or prev.scope != scope:
            raise conflict(
                "This form was already used for something else. Reload and try again."
            ) from None
        return prev.result | {"duplicate": True}
    return None


def remember(db: Session, key: str, result: dict) -> dict:
    db.get(OperationKey, key).result = jsonable_encoder(result)
    return result


def lock_items(db: Session, ids) -> dict[uuid.UUID, Item]:
    """Lock items in id order (no deadlock between two multi-item operations)."""
    items = db.scalars(
        select(Item).where(Item.id.in_(set(ids))).order_by(Item.id).with_for_update()
    ).all()
    found = {i.id: i for i in items}
    if len(found) != len(set(ids)):
        raise field_error("item_id", "Unknown item")
    return found


def active_location(db: Session, location_id, what="Location") -> Location:
    loc = db.get(Location, location_id)
    if loc is None or not loc.active:
        raise field_error("location_id", f"{what}: choose an active location")
    return loc


def outside_assembly(loc: Location) -> Location:
    """The assembly floor holds what orders have issued; it changes only through those orders
    (issue, build, return), so transfers, corrections and counts stay out of it."""
    if loc.purpose == "assembly":
        raise field_error("location_id", "The assembly floor changes only through assembly orders: return "
                          "materials from the order first")  # fmt: skip
    return loc


def warehouse_location(db: Session, warehouse_id, purpose: str) -> Location:
    loc = db.scalar(select(Location).where(Location.warehouse_id == warehouse_id, Location.purpose == purpose,
                                           Location.active).order_by(Location.code).limit(1))  # fmt: skip
    if loc is None:
        raise conflict(
            f"This warehouse has no active '{PURPOSE_LABEL[purpose]}' location. Add one first."
        )
    return loc


def label(db: Session, loc: Location) -> str:
    return f"{db.get(Warehouse, loc.warehouse_id).code}/{loc.code}"


def _take(db: Session, item: Item, loc: Location, qty: Decimal, lot: str) -> None:
    done = db.execute(
        update(StockBalance)
        .where(StockBalance.item_id == item.id, StockBalance.location_id == loc.id,
               StockBalance.lot == lot, StockBalance.qty >= qty)
        .values(qty=StockBalance.qty - qty)
        .returning(StockBalance.qty)
    ).first()  # fmt: skip
    if done is None:
        have = db.scalar(select(StockBalance.qty).where(StockBalance.item_id == item.id,
                         StockBalance.location_id == loc.id, StockBalance.lot == lot)) or ZERO  # fmt: skip
        lot_txt = f" (lot {lot})" if lot else ""
        raise conflict(f"Not enough {item.code}{lot_txt} at {label(db, loc)}: {have.normalize():f} there, "
                       f"{qty.normalize():f} needed.")  # fmt: skip


def _put(db: Session, item: Item, loc: Location, qty: Decimal, lot: str) -> None:
    db.execute(
        text("INSERT INTO stock_balances (item_id, location_id, lot, qty) VALUES (:i, :l, :lot, :q) "
             "ON CONFLICT (item_id, location_id, lot) DO UPDATE SET qty = stock_balances.qty + EXCLUDED.qty"),
        {"i": item.id, "l": loc.id, "lot": lot, "q": qty},
    )  # fmt: skip


Doc = tuple[str, object, str]  # (doc_type, doc_id, number)


def post(db: Session, user: User | None, *, item: Item, qty: Decimal, src: Location | None,
         dst: Location | None, kind: str, doc: Doc, lot: str = "", serial: Serial | None = None,
         reason: str = "") -> None:  # fmt: skip
    """One movement: the ledger row and both balances, in the caller's transaction."""
    if qty <= 0:
        raise field_error("qty", "Quantity must be more than zero")
    lot = "" if item.tracking != "lot" else lot
    if item.tracking == "lot" and not lot:
        raise field_error("lot", f"{item.code} is tracked by lot: enter the lot number")
    if src:
        _take(db, item, src, qty, lot)
    if dst:
        _put(db, item, dst, qty, lot)
    if serial is not None:
        serial.location_id = dst.id if dst else None
        serial.version += 1
    doc_type, doc_id, number = doc
    db.add(StockMove(item_id=item.id, qty=qty, from_location_id=src.id if src else None,
                     to_location_id=dst.id if dst else None, lot=lot,
                     serial_id=serial.id if serial else None, kind=kind, doc_type=doc_type,
                     doc_id=str(doc_id), doc_number=number, user_id=user.id if user else None,
                     reason=reason[:300]))  # fmt: skip


def available(db: Session, item_ids) -> dict[uuid.UUID, Decimal]:
    rows = db.execute(select(StockBalance.item_id, func.sum(StockBalance.qty))
                      .join(Location, Location.id == StockBalance.location_id)
                      .where(StockBalance.item_id.in_(item_ids), Location.purpose == "available")
                      .group_by(StockBalance.item_id))  # fmt: skip
    return defaultdict(lambda: ZERO, dict(rows.all()))


def reserved(db: Session, item_ids) -> dict[uuid.UUID, Decimal]:
    rows = db.execute(select(Reservation.item_id, func.sum(Reservation.qty - Reservation.qty_used))
                      .where(Reservation.item_id.in_(item_ids), Reservation.status == "active")
                      .group_by(Reservation.item_id))  # fmt: skip
    return defaultdict(lambda: ZERO, dict(rows.all()))


def free(db: Session, item_id) -> Decimal:
    """Available and not promised to anyone. Call with the item locked."""
    return available(db, [item_id])[item_id] - reserved(db, [item_id])[item_id]


def check_free(db: Session, item: Item, qty: Decimal) -> None:
    left = free(db, item.id)
    if left < qty:
        raise conflict(f"Only {max(left, ZERO).normalize():f} {item.unit_code} of {item.code} is free "
                       f"(the rest is reserved or not available); {qty.normalize():f} needed.")  # fmt: skip


def whole(item: Item, qty: Decimal, serials: list[str]) -> None:
    """Serial items move as whole units, each named."""
    if item.tracking != "serial":
        if serials:
            raise field_error("serials", f"{item.code} has no serial numbers")
        return
    if qty != qty.to_integral_value() or len(serials) != qty:
        raise field_error("serials", f"{item.code} is tracked by serial number: list one serial per unit "
                          f"({qty.normalize():f} needed, {len(serials)} given)")  # fmt: skip
    if len(set(serials)) != len(serials):
        raise field_error("serials", "A serial number is listed twice")


def serials_at(
    db: Session, item: Item, numbers: list[str], loc: Location, statuses: tuple[str, ...]
) -> list[Serial]:
    """The named serials, locked, checked to be this item, at this location, in one of `statuses`."""
    rows = db.scalars(
        select(Serial).where(Serial.serial.in_(numbers)).order_by(Serial.id).with_for_update()
    ).all()
    by_no = {s.serial: s for s in rows}
    for n in numbers:
        s = by_no.get(n)
        if s is None or s.item_id != item.id:
            raise field_error("serials", f"{n} is not a known {item.code} serial number")
        if s.status not in statuses:
            raise conflict(f"{n} is {s.status.replace('_', ' ')}, so it can't be used here")
        if s.location_id != loc.id:
            raise conflict(f"{n} is not at {label(db, loc)}")
    return [by_no[n] for n in numbers]


def new_serial(db: Session, item: Item, number: str, status_: str, **kw) -> Serial:
    if db.scalar(select(Serial.id).where(Serial.serial == number)):
        raise field_error("serials", f"{number} is already registered")
    if kw.get("supplier_serial") and db.scalar(select(Serial.id).where(
            Serial.item_id == item.id, Serial.supplier_serial == kw["supplier_serial"])):  # fmt: skip
        raise field_error(
            "serials",
            f"Supplier serial {kw['supplier_serial']} is already registered for {item.code}",
        )
    s = Serial(serial=number, item_id=item.id, status=status_, **kw)
    db.add(s)
    db.flush()
    return s


# ---------- reports ----------

PURPOSE_COLUMNS = (
    "available",
    "inspection",
    "quarantine",
    "rejected",
    "assembly",
    "repair",
    "returns",
)


def on_order(db: Session, item_ids) -> dict:
    rows = db.execute(
        select(PurchaseOrderLine.item_id,
               func.sum(func.greatest(PurchaseOrderLine.qty_ordered - PurchaseOrderLine.qty_accepted
                                      - PurchaseOrderLine.qty_held - PurchaseOrderLine.qty_awaiting
                                      - PurchaseOrderLine.qty_not_replaced, 0)))
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrderLine.item_id.in_(item_ids),
               PurchaseOrder.status.in_(("submitted", "approved", "partly_received")))
        .group_by(PurchaseOrderLine.item_id)
    )  # fmt: skip
    return defaultdict(lambda: ZERO, dict(rows.all()))


def summaries(db: Session, items: list[Item]) -> list[dict]:
    ids = [i.id for i in items]
    by: dict = defaultdict(lambda: defaultdict(lambda: ZERO))
    for item_id, purpose, qty in db.execute(
        select(StockBalance.item_id, Location.purpose, func.sum(StockBalance.qty))
        .join(Location, Location.id == StockBalance.location_id)
        .where(StockBalance.item_id.in_(ids)).group_by(StockBalance.item_id, Location.purpose)
    ):  # fmt: skip
        by[item_id][purpose] = qty
    res, ordered = reserved(db, ids), on_order(db, ids)
    out = []
    for i in items:
        b = by[i.id]
        free_ = b["available"] - res[i.id]
        short = i.min_stock - free_ - ordered[i.id]
        out.append(item_ref(i) | {"kind": i.kind, "min_stock": i.min_stock, "suggested": max(short, ZERO),
                   "on_hand": sum(b.values(), ZERO), "reserved": res[i.id], "free": free_,
                   "on_order": ordered[i.id], "low": i.min_stock > 0 and short > 0,
                   **{p: b[p] for p in PURPOSE_COLUMNS}})  # fmt: skip
    return out


@router.get("/stock/summary")
def stock_summary(
    _: require("stock.view"),
    db: DB,
    q: str = Query("", max_length=100),
    kind: str = Query("", max_length=16),
    low: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    """Per item: on hand by status, reserved, free, on order, and whether it is below minimum."""
    query = select(Item).where(Item.active)
    if q:
        query = query.where(or_(Item.code.ilike(f"%{q}%"), Item.name.ilike(f"%{q}%")))
    if kind:
        query = query.where(Item.kind == kind)
    if low:  # below minimum is worked out per item; the minimum filter narrows the candidates first
        items = db.scalars(query.where(Item.min_stock > 0).order_by(Item.code)).all()
        rows = [r for r in summaries(db, items) if r["low"]]
        return {"items": rows[offset : offset + limit], "total": len(rows)}
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Item.code).limit(limit).offset(offset)).all()
    return {"items": summaries(db, items), "total": total}


@router.get("/stock/balances")
def stock_balances(
    _: require("stock.view"),
    db: DB,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    purpose: str = Query("", max_length=12),
    limit: Limit = 100,
    offset: Offset = 0,
) -> dict:
    query = (select(StockBalance, Item, Location, Warehouse)
             .join(Item, Item.id == StockBalance.item_id)
             .join(Location, Location.id == StockBalance.location_id)
             .join(Warehouse, Warehouse.id == Location.warehouse_id)
             .where(StockBalance.qty > 0))  # fmt: skip
    for col, v in ((StockBalance.item_id, item_id), (StockBalance.location_id, location_id),
                   (Location.warehouse_id, warehouse_id)):  # fmt: skip
        if v:
            query = query.where(col == v)
    if purpose:
        query = query.where(Location.purpose == purpose)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(query.order_by(Item.code, Warehouse.code, Location.code, StockBalance.lot)
                      .limit(limit).offset(offset))  # fmt: skip
    return {"items": [{"item": item_ref(i), "location_id": loc.id, "location": f"{w.code}/{loc.code}",
                       "location_name": loc.name, "purpose": loc.purpose, "lot": b.lot, "qty": b.qty}
                      for b, i, loc, w in rows], "total": total}  # fmt: skip


def _moves_query(item_id, location_id, serial_id, doc_id, kind, since, until):
    query = select(StockMove)
    if item_id:
        query = query.where(StockMove.item_id == item_id)
    if location_id:
        query = query.where(
            or_(StockMove.from_location_id == location_id, StockMove.to_location_id == location_id)
        )
    if serial_id:
        query = query.where(StockMove.serial_id == serial_id)
    if doc_id:
        query = query.where(or_(StockMove.doc_id == doc_id, StockMove.doc_number == doc_id))
    if kind:
        query = query.where(StockMove.kind == kind)
    if since:
        query = query.where(StockMove.at >= since)
    if until:
        query = query.where(StockMove.at < until)
    return query


def move_rows(db: Session, moves: list[StockMove]) -> list[dict]:
    """Moves with names, not ids: a few lookups for the whole page."""
    items = {
        i.id: i for i in db.scalars(select(Item).where(Item.id.in_({m.item_id for m in moves})))
    }
    loc_ids = {x for m in moves for x in (m.from_location_id, m.to_location_id) if x}
    locs = {
        loc.id: f"{w.code}/{loc.code}"
        for loc, w in db.execute(
            select(Location, Warehouse)
            .join(Warehouse, Warehouse.id == Location.warehouse_id)
            .where(Location.id.in_(loc_ids))
        )
    }
    serials = dict(
        db.execute(
            select(Serial.id, Serial.serial).where(
                Serial.id.in_({m.serial_id for m in moves if m.serial_id})
            )
        ).all()
    )
    users = dict(
        db.execute(
            select(User.id, User.name).where(User.id.in_({m.user_id for m in moves if m.user_id}))
        ).all()
    )
    return [{"id": m.id, "at": m.at, "item": item_ref(items[m.item_id]), "qty": m.qty,
             "from": locs.get(m.from_location_id), "to": locs.get(m.to_location_id), "lot": m.lot,
             "serial": serials.get(m.serial_id), "kind": m.kind, "doc_type": m.doc_type, "doc_id": m.doc_id,
             "doc_number": m.doc_number, "user": users.get(m.user_id, "System"), "reason": m.reason}
            for m in moves]  # fmt: skip


@router.get("/stock/moves")
def stock_moves(
    _: require("stock.view"),
    db: DB,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    serial_id: uuid.UUID | None = None,
    doc: str = Query("", max_length=64),
    kind: str = Query("", max_length=20),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = _moves_query(item_id, location_id, serial_id, doc, kind, since, until)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    moves = db.scalars(query.order_by(StockMove.id.desc()).limit(limit).offset(offset)).all()
    return {"items": move_rows(db, moves), "total": total}


MOVE_COLUMNS = [
    "id",
    "at",
    "item",
    "qty",
    "from",
    "to",
    "lot",
    "serial",
    "kind",
    "doc_number",
    "user",
    "reason",
]


@router.get("/stock/moves/export")
def export_moves(
    p: require("stock.export"),
    db: DB,
    item_id: uuid.UUID | None = None,
    location_id: uuid.UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    moves = db.scalars(_moves_query(item_id, location_id, None, "", "", since, until)
                       .order_by(StockMove.id).limit(50_000)).all()  # fmt: skip
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(MOVE_COLUMNS)
    for r in move_rows(db, moves):
        r["item"] = r["item"]["code"]
        w.writerow([csv_cell("" if r[c] is None else r[c]) for c in MOVE_COLUMNS])
    record(db, p.user, "stock.export", summary=f"{len(moves)} stock movements exported")
    db.commit()
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="stock-movements.csv"'})  # fmt: skip


@router.get("/stock/reconcile")
def reconcile(_: require("stock.view"), db: DB) -> dict:
    """Balances against the ledger, and serial records against balances. Both lists are empty
    when stock is consistent; anything listed is a defect to investigate, never to hide."""
    ledger = text("""
        WITH l AS (
            SELECT item_id, loc, lot, sum(q) AS q FROM (
                SELECT item_id, to_location_id AS loc, lot, qty AS q FROM stock_moves WHERE to_location_id IS NOT NULL
                UNION ALL
                SELECT item_id, from_location_id, lot, -qty FROM stock_moves WHERE from_location_id IS NOT NULL
            ) x GROUP BY item_id, loc, lot)
        SELECT coalesce(l.item_id, b.item_id), coalesce(l.loc, b.location_id), coalesce(l.lot, b.lot),
               coalesce(l.q, 0), coalesce(b.qty, 0)
        FROM l FULL JOIN stock_balances b ON b.item_id = l.item_id AND b.location_id = l.loc AND b.lot = l.lot
        WHERE coalesce(l.q, 0) <> coalesce(b.qty, 0)""")
    serials = text("""
        SELECT coalesce(x.item_id, b.item_id), coalesce(x.loc, b.location_id), coalesce(x.n, 0), coalesce(b.qty, 0)
        FROM (SELECT item_id, location_id AS loc, count(*) AS n FROM serials
              WHERE location_id IS NOT NULL GROUP BY 1, 2) x
        FULL JOIN (SELECT sb.item_id, sb.location_id, sum(sb.qty) AS qty FROM stock_balances sb
                   JOIN items i ON i.id = sb.item_id WHERE i.tracking = 'serial' GROUP BY 1, 2) b
             ON b.item_id = x.item_id AND b.location_id = x.loc
        WHERE coalesce(x.n, 0) <> coalesce(b.qty, 0)""")
    return {
        "ledger_mismatches": [{"item_id": a, "location_id": b, "lot": c, "ledger": d, "balance": e}
                              for a, b, c, d, e in db.execute(ledger)],
        "serial_mismatches": [{"item_id": a, "location_id": b, "serials": c, "balance": d}
                              for a, b, c, d in db.execute(serials)],
    }  # fmt: skip


# ---------- serial numbers ----------


def serial_row(db: Session, s: Serial, item: Item | None = None) -> dict:
    item = item or db.get(Item, s.item_id)
    loc = db.get(Location, s.location_id) if s.location_id else None
    return {"id": s.id, "serial": s.serial, "item": item_ref(item), "supplier_serial": s.supplier_serial,
            "lot": s.lot, "status": s.status, "location_id": s.location_id,
            "location": label(db, loc) if loc else None, "purpose": loc.purpose if loc else None,
            "assembly_order_id": s.assembly_order_id, "created_at": s.created_at, "version": s.version}  # fmt: skip


@router.get("/serials")
def list_serials(
    _: require("stock.view"),
    db: DB,
    q: str = Query("", max_length=80),
    item_id: uuid.UUID | None = None,
    status_: Annotated[str, Query(alias="status", max_length=20)] = "",
    location_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Serial)
    if q:
        query = query.where(
            or_(Serial.serial.ilike(f"%{q}%"), Serial.supplier_serial.ilike(f"%{q}%"))
        )
    if item_id:
        query = query.where(Serial.item_id == item_id)
    if status_:
        query = query.where(Serial.status == status_)
    if location_id:
        query = query.where(Serial.location_id == location_id)
    return page(
        db,
        query.order_by(Serial.created_at.desc(), Serial.serial),
        limit,
        offset,
        lambda s: serial_row(db, s),
    )


@router.get("/serials/{serial_id}")
def get_serial(serial_id: uuid.UUID, _: require("stock.view"), db: DB) -> dict:
    """One unit's record and its history: receipt, movements, what it contains or went into,
    inspections."""
    s = get_or_404(db, Serial, serial_id, "Serial number")
    moves = db.scalars(
        select(StockMove).where(StockMove.serial_id == s.id).order_by(StockMove.id)
    ).all()
    parts = db.execute(select(SerialComponent, Item).join(Item, Item.id == SerialComponent.component_id)
                       .where(SerialComponent.serial_id == s.id)).all()  # fmt: skip
    part_serials = dict(
        db.execute(
            select(Serial.id, Serial.serial).where(
                Serial.id.in_([c.component_serial_id for c, _ in parts if c.component_serial_id])
            )
        ).all()
    )
    inside = db.scalar(select(Serial).join(SerialComponent, SerialComponent.serial_id == Serial.id)
                       .where(SerialComponent.component_serial_id == s.id))  # fmt: skip
    inspections = db.execute(select(Inspection, User.name).join(User, User.id == Inspection.inspector_id)
                             .where(Inspection.serial_id == s.id).order_by(Inspection.at)).all()  # fmt: skip
    po = None
    if s.po_line_id:
        po = db.execute(select(PurchaseOrder.id, PurchaseOrder.number).join(PurchaseOrderLine)
                        .where(PurchaseOrderLine.id == s.po_line_id)).first()  # fmt: skip
    return serial_row(db, s) | {
        "purchase_order": {"id": po[0], "number": po[1]} if po else None,
        "moves": move_rows(db, moves),
        "components": [{"item": item_ref(i), "serial": part_serials.get(c.component_serial_id),
                        "serial_id": c.component_serial_id, "lot": c.lot, "qty": c.qty} for c, i in parts],
        "built_into": {"id": inside.id, "serial": inside.serial} if inside else None,
        "inspections": [{"id": x.id, "at": x.at, "result": x.result, "readings": x.readings,
                         "checklist": x.checklist, "observations": x.observations, "inspector": name}
                        for x, name in inspections],
    }  # fmt: skip


# ---------- transfers ----------


class TransferIn(BaseModel):
    key: Key
    item_id: uuid.UUID
    from_location_id: uuid.UUID
    to_location_id: uuid.UUID
    qty: Qty
    lot: Lot = ""
    serials: Serials = []
    reason: Reason = ""


@router.post("/stock/transfers", status_code=status.HTTP_201_CREATED)
def transfer(body: TransferIn, p: require("stock.create"), db: DB) -> dict:
    """Between locations of the same use (store to store, quarantine to quarantine). A change of
    status (accept, release, pass) goes through its own decision, never through a transfer, so no
    battery skips an inspection."""
    if (prev := claim_key(db, body.key, "transfer")) is not None:
        return prev
    item = lock_items(db, [body.item_id])[body.item_id]
    src, dst = (
        active_location(db, body.from_location_id, "From"),
        active_location(db, body.to_location_id, "To"),
    )
    if src.id == dst.id:
        raise field_error("to_location_id", "Choose a different location")
    outside_assembly(src)
    if src.purpose != dst.purpose:
        raise field_error("to_location_id", f"Transfers stay within one use ({PURPOSE_LABEL[src.purpose]}). "
                          "Status changes go through receiving, inspection or a correction.")  # fmt: skip
    whole(item, body.qty, body.serials)
    doc = ("transfer", body.key, "")
    if item.tracking == "serial":
        for s in serials_at(
            db,
            item,
            body.serials,
            src,
            tuple(STATUS_BY_PURPOSE.values()) + ("under_assembly", "rework"),
        ):
            post(
                db,
                p.user,
                item=item,
                qty=Decimal(1),
                src=src,
                dst=dst,
                kind="transfer",
                doc=doc,
                serial=s,
                reason=body.reason,
            )
    else:
        post(
            db,
            p.user,
            item=item,
            qty=body.qty,
            src=src,
            dst=dst,
            kind="transfer",
            doc=doc,
            lot=body.lot,
            reason=body.reason,
        )
    record(db, p.user, "stock.transfer", "item", item.id,
           f"{body.qty.normalize():f} {item.unit_code} {item.code}: {label(db, src)} → {label(db, dst)}",
           {"serials": body.serials} if body.serials else None, body.reason)  # fmt: skip
    result = remember(db, body.key, {"item_id": item.id, "qty": str(body.qty)})
    db.commit()
    return result


# ---------- reservations ----------


class ReservationIn(BaseModel):
    key: Key
    item_id: uuid.UUID
    qty: Qty
    ref_type: Literal["sales_order", "project", "other"]
    ref_label: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    reason: Reason = ""


class ReleaseIn(BaseModel):
    version: int
    reason: NeededReason


def reservation_row(db: Session, r: Reservation, item: Item | None = None) -> dict:
    item = item or db.get(Item, r.item_id)
    return {"id": r.id, "item": item_ref(item), "qty": r.qty, "qty_used": r.qty_used,
            "remaining": r.qty - r.qty_used if r.status == "active" else ZERO, "ref_type": r.ref_type,
            "ref_id": r.ref_id, "ref_label": r.ref_label, "status": r.status, "reason": r.reason,
            "created_at": r.created_at, "version": r.version}  # fmt: skip


def reserve(db: Session, user: User, item: Item, qty: Decimal, ref_type: str, ref_id: str, ref_label: str,
            reason: str = "") -> Reservation:  # fmt: skip
    """Item must be locked by the caller (lock_items)."""
    check_free(db, item, qty)
    r = Reservation(item_id=item.id, qty=qty, ref_type=ref_type, ref_id=ref_id, ref_label=ref_label,
                    reason=reason, created_by=user.id)  # fmt: skip
    db.add(r)
    db.flush()
    return r


@router.get("/reservations")
def list_reservations(
    _: require("stock.view"),
    db: DB,
    item_id: uuid.UUID | None = None,
    status_: Annotated[
        Literal["active", "released", "used"] | None, Query(alias="status")
    ] = "active",
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Reservation)
    if item_id:
        query = query.where(Reservation.item_id == item_id)
    if status_:
        query = query.where(Reservation.status == status_)
    return page(
        db,
        query.order_by(Reservation.created_at.desc()),
        limit,
        offset,
        lambda r: reservation_row(db, r),
    )


@router.post("/reservations", status_code=status.HTTP_201_CREATED)
def create_reservation(body: ReservationIn, p: require("stock.create"), db: DB) -> dict:
    """Sales orders and projects arrive in Phase 4; until then a reservation names what it is for."""
    if (prev := claim_key(db, body.key, "reservation")) is not None:
        return prev
    item = lock_items(db, [body.item_id])[body.item_id]
    if item.tracking == "serial" and body.qty != body.qty.to_integral_value():
        raise field_error("qty", "Serial items are reserved in whole units")
    r = reserve(db, p.user, item, body.qty, body.ref_type, "", body.ref_label, body.reason)
    record(db, p.user, "reservation.create", "reservation", r.id,
           f"{body.qty.normalize():f} {item.unit_code} {item.code} reserved for {body.ref_label}", reason=body.reason)  # fmt: skip
    row = reservation_row(db, r, item)
    remember(db, body.key, row)
    db.commit()
    return row


@router.post("/reservations/{reservation_id}/release")
def release_reservation(
    reservation_id: uuid.UUID, body: ReleaseIn, p: require("stock.create"), db: DB
) -> dict:
    r = get_or_404(db, Reservation, reservation_id, "Reservation", lock=True)
    if r.version != body.version:
        raise stale()
    if r.status != "active":
        raise conflict("This reservation is no longer active")
    if r.ref_type == "assembly_order":
        raise conflict(
            "Assembly reservations are released by completing or cancelling the assembly order"
        )
    r.status, r.version = "released", r.version + 1
    item = db.get(Item, r.item_id)
    record(db, p.user, "reservation.release", "reservation", r.id,
           f"Reservation of {item.code} for {r.ref_label} released", reason=body.reason)  # fmt: skip
    db.commit()
    return reservation_row(db, r, item)


def release_for(db: Session, ref_type: str, ref_id: str) -> None:
    db.execute(update(Reservation).where(Reservation.ref_type == ref_type, Reservation.ref_id == ref_id,
                                         Reservation.status == "active")
               .values(status=case((Reservation.qty_used >= Reservation.qty, "used"), else_="released"),
                       version=Reservation.version + 1))  # fmt: skip


# ---------- stock corrections (adjustments) ----------


class AdjustmentLineIn(BaseModel):
    item_id: uuid.UUID
    location_id: uuid.UUID
    lot: Lot = ""
    qty_change: Annotated[Decimal, Field(max_digits=14, decimal_places=3)] | None = None
    serials_in: Serials = []
    serials_out: Serials = []


class AdjustmentIn(BaseModel):
    key: Key
    reason: NeededReason
    lines: Annotated[list[AdjustmentLineIn], Field(min_length=1, max_length=200)]


class DecisionIn(BaseModel):
    version: int
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def _check_adjustment_line(db: Session, ln: AdjustmentLineIn, items: dict, loc: Location) -> None:
    item = items[ln.item_id]
    if item.tracking == "serial":
        if ln.qty_change not in (None, ZERO) or not (ln.serials_in or ln.serials_out):
            raise field_error(
                "serials_in", f"{item.code}: name the serial numbers to add or remove"
            )
        if set(ln.serials_in) & set(ln.serials_out):
            raise field_error("serials_in", "A serial can't be added and removed in one line")
        if item.assembled and ln.serials_in and loc.purpose == "available":
            raise field_error("location_id", f"A found {item.code} battery goes to quarantine and is inspected "
                              "before it counts as available")  # fmt: skip
    elif not ln.qty_change:
        raise field_error("qty_change", f"{item.code}: enter the change, e.g. -2 or 5")
    elif ln.serials_in or ln.serials_out:
        raise field_error("serials_in", f"{item.code} has no serial numbers")


def adjustment_row(db: Session, a: StockAdjustment, lines: bool = False) -> dict:
    names = dict(
        db.execute(
            select(User.id, User.name).where(User.id.in_([a.requested_by, a.decided_by]))
        ).all()
    )
    row = {"id": a.id, "number": a.number, "status": a.status, "reason": a.reason, "count_id": a.count_id,
           "requested_by": names.get(a.requested_by), "requested_by_id": a.requested_by,
           "decided_by": names.get(a.decided_by), "decided_at": a.decided_at,
           "decision_reason": a.decision_reason, "created_at": a.created_at, "version": a.version}  # fmt: skip
    if lines:
        rows = db.execute(select(StockAdjustmentLine, Item, Location)
                          .join(Item, Item.id == StockAdjustmentLine.item_id)
                          .join(Location, Location.id == StockAdjustmentLine.location_id)
                          .where(StockAdjustmentLine.adjustment_id == a.id)).all()  # fmt: skip
        row["lines"] = [{"id": ln.id, "item": item_ref(i), "location_id": loc.id, "location": label(db, loc),
                         "lot": ln.lot, "qty_change": ln.qty_change, "counted": ln.counted,
                         "serials_in": ln.serials_in, "serials_out": ln.serials_out,
                         "serials_found": ln.serials_found} for ln, i, loc in rows]  # fmt: skip
    return row


@router.get("/adjustments")
def list_adjustments(
    _: require("stock.view"),
    db: DB,
    status_: Annotated[
        Literal["pending", "approved", "rejected"] | None, Query(alias="status")
    ] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(StockAdjustment)
    if status_:
        query = query.where(StockAdjustment.status == status_)
    return page(
        db,
        query.order_by(StockAdjustment.created_at.desc()),
        limit,
        offset,
        lambda a: adjustment_row(db, a),
    )


@router.get("/adjustments/{adjustment_id}")
def get_adjustment(adjustment_id: uuid.UUID, _: require("stock.view"), db: DB) -> dict:
    return adjustment_row(
        db, get_or_404(db, StockAdjustment, adjustment_id, "Stock correction"), lines=True
    )


@router.post("/adjustments", status_code=status.HTTP_201_CREATED)
def request_adjustment(body: AdjustmentIn, p: require("adjustments.create"), db: DB) -> dict:
    if (prev := claim_key(db, body.key, "adjustment")) is not None:
        return prev
    items = lock_items(db, [ln.item_id for ln in body.lines])
    for ln in body.lines:
        _check_adjustment_line(db, ln, items, outside_assembly(active_location(db, ln.location_id)))
    a = StockAdjustment(
        number=next_number(db, "adjustment"), reason=body.reason, requested_by=p.user.id
    )
    db.add(a)
    db.flush()
    db.add_all(StockAdjustmentLine(adjustment_id=a.id, item_id=ln.item_id, location_id=ln.location_id,
                                   lot=ln.lot if items[ln.item_id].tracking == "lot" else "",
                                   qty_change=ln.qty_change, serials_in=ln.serials_in,
                                   serials_out=ln.serials_out) for ln in body.lines)  # fmt: skip
    record(db, p.user, "adjustment.request", "adjustment", a.id, f"{a.number} requested ({len(body.lines)} lines)",
           reason=body.reason)  # fmt: skip
    row = adjustment_row(db, a, lines=True)
    remember(db, body.key, row)
    db.commit()
    return row


def _decidable(db: Session, p: Principal, adjustment_id, version: int) -> StockAdjustment:
    a = get_or_404(db, StockAdjustment, adjustment_id, "Stock correction", lock=True)
    if a.version != version:
        raise stale()
    if a.status != "pending":
        raise conflict(f"{a.number} was already {a.status}")
    counted = (
        db.scalar(select(StockCount.counters).where(StockCount.id == a.count_id))
        if a.count_id
        else []
    )
    if (
        p.user.id in {a.requested_by, *(counted or [])}
        and not rules(db)["adjustment_self_approval"]
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Someone else must decide on a correction you asked for"
        )
    return a


def _apply_line(
    db: Session, p: Principal, a: StockAdjustment, ln: StockAdjustmentLine, item: Item
) -> str:
    loc = db.get(Location, ln.location_id)
    doc = ("adjustment", a.id, a.number)
    if item.tracking != "serial":
        if ln.counted is not None:
            have = db.scalar(select(StockBalance.qty).where(StockBalance.item_id == item.id,
                             StockBalance.location_id == loc.id, StockBalance.lot == ln.lot)) or ZERO  # fmt: skip
            delta = ln.counted - have
        else:
            delta = ln.qty_change
        if delta < 0 and loc.purpose == "available":
            check_free(db, item, -delta)  # a correction can't take stock someone has been promised
        if delta:
            post(db, p.user, item=item, qty=abs(delta), src=loc if delta < 0 else None,
                 dst=loc if delta > 0 else None, kind="adjust_out" if delta < 0 else "adjust_in", doc=doc, lot=ln.lot)  # fmt: skip
        return f"{item.code} {delta:+f}"
    serials_out, serials_in = list(ln.serials_out), list(ln.serials_in)
    if ln.serials_found is not None:  # from a count: what's missing goes out, what's new comes in
        found = set(ln.serials_found)
        here = set(
            db.scalars(
                select(Serial.serial).where(Serial.item_id == item.id, Serial.location_id == loc.id)
            )
        )
        known_elsewhere = db.scalars(
            select(Serial.serial).where(Serial.serial.in_(found - here))
        ).all()
        if known_elsewhere:
            raise conflict(
                f"{known_elsewhere[0]} is registered elsewhere: transfer it, then count again"
            )
        serials_out, serials_in = sorted(here - found), sorted(found - here)
    if serials_out:
        if loc.purpose == "available":
            check_free(db, item, Decimal(len(serials_out)))
        for s in serials_at(
            db,
            item,
            serials_out,
            loc,
            CORRECTABLE,
        ):
            post(
                db,
                p.user,
                item=item,
                qty=Decimal(1),
                src=loc,
                dst=None,
                kind="adjust_out",
                doc=doc,
                serial=s,
            )
            s.status = "written_off"
    if serials_in and item.assembled and loc.purpose == "available":
        raise conflict(f"A found {item.code} battery can't go straight into available stock: reject this, then "
                       "ask for a correction into quarantine so it is inspected")  # fmt: skip
    for n in serials_in:
        status_ = STATUS_BY_PURPOSE[loc.purpose]
        s = new_serial(db, item, n, status_)
        post(
            db,
            p.user,
            item=item,
            qty=Decimal(1),
            src=None,
            dst=loc,
            kind="adjust_in",
            doc=doc,
            serial=s,
        )
    return f"{item.code} +{len(serials_in)} −{len(serials_out)} serials"


@router.post("/adjustments/{adjustment_id}/approve")
def approve_adjustment(
    adjustment_id: uuid.UUID, body: DecisionIn, p: require("adjustments.approve"), db: DB
) -> dict:
    a = _decidable(db, p, adjustment_id, body.version)
    lines = db.scalars(
        select(StockAdjustmentLine).where(StockAdjustmentLine.adjustment_id == a.id)
    ).all()
    items = lock_items(db, [ln.item_id for ln in lines])
    done = [_apply_line(db, p, a, ln, items[ln.item_id]) for ln in lines]
    a.status, a.decided_by, a.decided_at, a.decision_reason = (
        "approved",
        p.user.id,
        utcnow(),
        body.reason,
    )
    a.version += 1
    record(db, p.user, "adjustment.approve", "adjustment", a.id, f"{a.number} approved: {'; '.join(done)}"[:300],
           reason=body.reason)  # fmt: skip
    db.commit()
    return adjustment_row(db, a, lines=True)


@router.post("/adjustments/{adjustment_id}/reject")
def reject_adjustment(
    adjustment_id: uuid.UUID, body: DecisionIn, p: require("adjustments.approve"), db: DB
) -> dict:
    a = _decidable(db, p, adjustment_id, body.version)
    if not body.reason:
        raise field_error("reason", "Say why it is rejected")
    a.status, a.decided_by, a.decided_at, a.decision_reason = (
        "rejected",
        p.user.id,
        utcnow(),
        body.reason,
    )
    a.version += 1
    record(
        db,
        p.user,
        "adjustment.reject",
        "adjustment",
        a.id,
        f"{a.number} rejected",
        reason=body.reason,
    )
    db.commit()
    return adjustment_row(db, a, lines=True)


# ---------- stock counts ----------


class CountIn(BaseModel):
    location_id: uuid.UUID
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)] = ""


class CountLineIn(BaseModel):
    item_id: uuid.UUID
    lot: Lot = ""
    counted: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)] | None = None
    serials_found: Serials | None = None


class CountLinesIn(BaseModel):
    version: int
    lines: Annotated[list[CountLineIn], Field(max_length=2000)]


class CountSubmit(BaseModel):
    version: int
    reason: NeededReason = "Stock count"


def count_row(db: Session, c: StockCount, lines: bool = False) -> dict:
    loc = db.get(Location, c.location_id)
    row = {"id": c.id, "number": c.number, "location_id": loc.id, "location": label(db, loc),
           "purpose": loc.purpose, "status": c.status, "notes": c.notes, "adjustment_id": c.adjustment_id,
           "created_at": c.created_at, "submitted_at": c.submitted_at, "version": c.version}  # fmt: skip
    if lines:
        rows = db.execute(select(StockCountLine, Item).join(Item, Item.id == StockCountLine.item_id)
                          .where(StockCountLine.count_id == c.id).order_by(Item.code, StockCountLine.lot)).all()  # fmt: skip
        expected_serials: dict = defaultdict(list)
        for item_id, n in db.execute(select(Serial.item_id, Serial.serial).where(Serial.location_id == loc.id)
                                     .order_by(Serial.serial)):  # fmt: skip
            expected_serials[item_id].append(n)
        row["lines"] = [{"id": ln.id, "item": item_ref(i), "lot": ln.lot, "expected": ln.expected,
                         "counted": ln.counted, "serials_found": ln.serials_found,
                         "serials_expected": expected_serials.get(i.id, []) if i.tracking == "serial" else None}
                        for ln, i in rows]  # fmt: skip
    return row


@router.get("/counts")
def list_counts(_: require("stock.view"), db: DB, limit: Limit = 50, offset: Offset = 0) -> dict:
    return page(
        db,
        select(StockCount).order_by(StockCount.created_at.desc()),
        limit,
        offset,
        lambda c: count_row(db, c),
    )


@router.get("/counts/{count_id}")
def get_count(count_id: uuid.UUID, _: require("stock.view"), db: DB) -> dict:
    return count_row(db, get_or_404(db, StockCount, count_id, "Stock count"), lines=True)


@router.post("/counts", status_code=status.HTTP_201_CREATED)
def start_count(body: CountIn, p: require("stock.create"), db: DB) -> dict:
    """Lists what the books say is in the location now; counters fill in what they find."""
    loc = outside_assembly(active_location(db, body.location_id))
    c = StockCount(
        number=next_number(db, "stock_count"),
        location_id=loc.id,
        notes=body.notes,
        created_by=p.user.id,
    )
    db.add(c)
    db.flush()
    for b in db.scalars(
        select(StockBalance).where(StockBalance.location_id == loc.id, StockBalance.qty > 0)
    ):
        db.add(StockCountLine(count_id=c.id, item_id=b.item_id, lot=b.lot, expected=b.qty))
    record(
        db, p.user, "count.start", "stock_count", c.id, f"{c.number} started at {label(db, loc)}"
    )
    db.commit()
    return count_row(db, c, lines=True)


def _open_count(db: Session, count_id, version: int) -> StockCount:
    c = get_or_404(db, StockCount, count_id, "Stock count", lock=True)
    if c.version != version:
        raise stale()
    if c.status != "open":
        raise conflict(f"{c.number} is {c.status}")
    return c


@router.put("/counts/{count_id}/lines")
def save_count_lines(
    count_id: uuid.UUID, body: CountLinesIn, p: require("stock.create"), db: DB
) -> dict:
    """Save what was counted; lines for items found but not on the books are added."""
    c = _open_count(db, count_id, body.version)
    items = lock_items(db, [ln.item_id for ln in body.lines]) if body.lines else {}
    existing = {(ln.item_id, ln.lot): ln for ln in
                db.scalars(select(StockCountLine).where(StockCountLine.count_id == c.id))}  # fmt: skip
    for ln in body.lines:
        item = items[ln.item_id]
        lot = ln.lot if item.tracking == "lot" else ""
        if item.tracking == "serial":
            if ln.serials_found is None:
                raise field_error("serials_found", f"{item.code}: list the serial numbers found")
            counted, serials = Decimal(len(set(ln.serials_found))), sorted(set(ln.serials_found))
        else:
            if ln.serials_found:
                raise field_error("serials_found", f"{item.code} has no serial numbers")
            counted, serials = ln.counted, None
        row = existing.get((item.id, lot))
        if row is None:
            row = StockCountLine(count_id=c.id, item_id=item.id, lot=lot, expected=ZERO)
            db.add(row)
        row.counted, row.serials_found = counted, serials
    c.version += 1
    if p.user.id not in c.counters:
        c.counters = [*c.counters, p.user.id]
    record(db, p.user, "count.save", "stock_count", c.id, f"{c.number}: {len(body.lines)} lines counted",
           {"lines": [{"item_id": str(ln.item_id), "lot": ln.lot,
                       "counted": None if ln.counted is None else str(ln.counted),
                       "serials_found": ln.serials_found} for ln in body.lines]})  # fmt: skip
    db.commit()
    return count_row(db, c, lines=True)


@router.post("/counts/{count_id}/submit")
def submit_count(
    count_id: uuid.UUID, body: CountSubmit, p: require("stock.create"), db: DB
) -> dict:
    """Counted lines that differ from the books become one correction request, decided like any
    other. The difference is worked out again at approval, against the stock then."""
    c = _open_count(db, count_id, body.version)
    loc = db.get(Location, c.location_id)
    lines = db.execute(select(StockCountLine, Item).join(Item, Item.id == StockCountLine.item_id)
                       .where(StockCountLine.count_id == c.id, StockCountLine.counted.is_not(None))).all()  # fmt: skip
    diffs = []
    for ln, item in lines:
        if item.tracking == "serial":
            here = set(
                db.scalars(
                    select(Serial.serial).where(
                        Serial.item_id == item.id, Serial.location_id == loc.id
                    )
                )
            )
            if set(ln.serials_found or []) != here:
                diffs.append((ln, item))
        else:
            have = db.scalar(select(StockBalance.qty).where(StockBalance.item_id == item.id,
                             StockBalance.location_id == loc.id, StockBalance.lot == ln.lot)) or ZERO  # fmt: skip
            if ln.counted != have:
                diffs.append((ln, item))
    if diffs:
        a = StockAdjustment(number=next_number(db, "adjustment"), reason=f"{c.number}: {body.reason}",
                            count_id=c.id, requested_by=p.user.id)  # fmt: skip
        db.add(a)
        db.flush()
        db.add_all(StockAdjustmentLine(adjustment_id=a.id, item_id=item.id, location_id=loc.id, lot=ln.lot,
                                       counted=ln.counted if item.tracking != "serial" else None,
                                       serials_found=ln.serials_found if item.tracking == "serial" else None)
                   for ln, item in diffs)  # fmt: skip
        c.adjustment_id = a.id
    c.status, c.submitted_at, c.version = "submitted", utcnow(), c.version + 1
    record(db, p.user, "count.submit", "stock_count", c.id,
           f"{c.number} submitted: {len(diffs)} differences" + (" (correction requested)" if diffs else ""))  # fmt: skip
    db.commit()
    return count_row(db, c, lines=True)


@router.post("/counts/{count_id}/cancel")
def cancel_count(
    count_id: uuid.UUID, body: CountSubmit, p: require("stock.create"), db: DB
) -> dict:
    c = _open_count(db, count_id, body.version)
    c.status, c.version = "cancelled", c.version + 1
    record(
        db, p.user, "count.cancel", "stock_count", c.id, f"{c.number} cancelled", reason=body.reason
    )
    db.commit()
    return count_row(db, c)
