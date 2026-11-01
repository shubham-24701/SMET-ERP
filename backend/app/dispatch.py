"""Phase 4 dispatch and delivery.

planned → dispatched → delivered, or cancelled. Planning moves nothing. Dispatching takes stock out
of available locations only (so nothing on hold, uninspected or under assembly can leave), uses the
order's and its project's reservations first, and names every serial. Delivery records who
received it, what was refused (it comes back into the customer-returns area) and what arrived
damaged. Cancelling a dispatch that left puts the stock back with new movements and gives the
reservation back; the dispatch record stays.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, require
from app.inventory import (
    Key,
    Lot,
    Serials,
    active_location,
    check_free,
    claim_key,
    conflict,
    free,
    label,
    lock_items,
    post,
    remember,
    serials_at,
    warehouse_location,
    whole,
)
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, page
from app.models import (
    Dispatch,
    DispatchLine,
    Item,
    Location,
    Party,
    Reservation,
    SalesOrder,
    SalesOrderLine,
    Serial,
    Site,
    Warehouse,
    utcnow,
)
from app.sales import GOODS, party_ref, refresh_order_status, reservations_of

router = APIRouter(tags=["dispatch"], route_class=NumbersRoute)
ZERO = Decimal(0)
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]


def _once(rows: list) -> list:
    seen = [(r.order_line_id, r.lot) for r in rows]
    if len(set(seen)) != len(seen):
        raise ValueError("Each order line and lot only once: put the total on one line")
    return rows


class LineIn(BaseModel):
    order_line_id: uuid.UUID
    qty: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
    lot: Lot = ""
    location_id: uuid.UUID | None = None  # picked from; default: the warehouse's main store
    serials: Serials = []


class DispatchIn(BaseModel):
    order_id: uuid.UUID
    warehouse_id: uuid.UUID
    site_id: uuid.UUID | None = None
    planned_date: date | None = None
    destination: Text = ""
    delivery_contact: Short = ""
    transporter: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    vehicle: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    lines: Annotated[list[LineIn], Field(min_length=1, max_length=200), AfterValidator(_once)]


class DispatchPut(DispatchIn):
    version: int


class Go(BaseModel):
    version: int
    key: Key


class Refused(BaseModel):
    line_id: uuid.UUID
    qty_rejected: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)] = ZERO
    qty_damaged: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)] = ZERO
    rejected_serials: Serials = []
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class DeliveredIn(BaseModel):
    version: int
    key: Key
    received_by: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
    ]
    delivered_on: date
    notes: Text = ""
    lines: Annotated[list[Refused], Field(max_length=200)] = []


class CancelIn(BaseModel):
    version: int
    key: Key
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


def dispatch_row(db: Session, d: Dispatch, full: bool = False) -> dict:
    so, wh = db.get(SalesOrder, d.order_id), db.get(Warehouse, d.warehouse_id)
    site = db.get(Site, d.site_id) if d.site_id else None
    row = {"id": d.id, "number": d.number, "status": d.status, "order": {"id": so.id, "number": so.number},
           "customer": party_ref(db.get(Party, so.customer_id)), "warehouse": {"id": wh.id, "code": wh.code, "name": wh.name},
           "site": {"id": site.id, "name": site.name, "address": site.address} if site else None,
           "planned_date": d.planned_date, "destination": d.destination, "delivery_contact": d.delivery_contact,
           "transporter": d.transporter, "vehicle": d.vehicle, "dispatched_at": d.dispatched_at,
           "received_by": d.received_by, "delivered_on": d.delivered_on, "delivery_notes": d.delivery_notes,
           "cancel_reason": d.cancel_reason, "created_at": d.created_at, "version": d.version}  # fmt: skip
    if full:
        lines = db.scalars(
            select(DispatchLine).where(DispatchLine.dispatch_id == d.id).order_by(DispatchLine.id)
        ).all()
        items = {
            i.id: i
            for i in db.scalars(select(Item).where(Item.id.in_([ln.item_id for ln in lines])))
        }
        locs = {loc.id: label(db, loc) for loc in db.scalars(select(Location).where(Location.id.in_(
            [ln.location_id for ln in lines if ln.location_id])))}  # fmt: skip
        row["lines"] = [{"id": ln.id, "order_line_id": ln.order_line_id, "item": item_ref(items[ln.item_id]),
                         "qty": ln.qty, "lot": ln.lot, "location_id": ln.location_id, "location": locs.get(ln.location_id),
                         "serials": ln.serials, "qty_rejected": ln.qty_rejected, "qty_damaged": ln.qty_damaged,
                         "rejected_serials": ln.rejected_serials, "note": ln.note} for ln in lines]  # fmt: skip
    return row


@router.get("/dispatch/plan")
def dispatch_plan(
    _: require("dispatch.view"), db: DB, limit: Limit = 100, offset: Offset = 0
) -> dict:
    """Order lines still to send, by committed date, with how much is reserved and free: what can go."""
    rows = db.execute(select(SalesOrderLine, SalesOrder).join(SalesOrder, SalesOrder.id == SalesOrderLine.order_id)
                      .where(SalesOrder.status.in_(("open", "partly_dispatched")),
                             SalesOrderLine.line_type.in_(GOODS), SalesOrderLine.qty > SalesOrderLine.qty_dispatched)
                      .order_by(SalesOrder.delivery_date.nulls_last(), SalesOrder.number)
                      .limit(limit).offset(offset)).all()  # fmt: skip
    planned = dict(db.execute(select(DispatchLine.order_line_id, func.sum(DispatchLine.qty))
                              .join(Dispatch, Dispatch.id == DispatchLine.dispatch_id)
                              .where(Dispatch.status == "planned").group_by(DispatchLine.order_line_id)).all())  # fmt: skip
    out, res_cache = [], {}
    for ln, so in rows:
        if so.id not in res_cache:
            res_cache[so.id] = reservations_of(db, so)
        res = res_cache[so.id]
        item = db.get(Item, ln.item_id)
        pending = ln.qty - ln.qty_dispatched
        reserved = res.get("sales_order", {}).get(ln.item_id, ZERO) + res.get("project", {}).get(
            ln.item_id, ZERO
        )
        f = free(db, ln.item_id)
        out.append({"order": {"id": so.id, "number": so.number}, "customer": party_ref(db.get(Party, so.customer_id)),
                    "delivery_date": so.delivery_date, "line_id": ln.id, "item": item_ref(item), "pending": pending,
                    "planned": planned.get(ln.id, ZERO), "reserved": reserved, "free": f,
                    "ready": min(pending, reserved + max(f, ZERO))})  # fmt: skip
    return {"items": out}


@router.get("/dispatches")
def list_dispatches(
    _: require("dispatch.view"),
    db: DB,
    status_: Annotated[str, Query(alias="status", max_length=60)] = "",
    order_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Dispatch)
    if status_:
        query = query.where(Dispatch.status.in_(status_.split(",")))
    if order_id:
        query = query.where(Dispatch.order_id == order_id)
    counts = dict(db.execute(select(Dispatch.status, func.count()).group_by(Dispatch.status)).all())
    return page(
        db, query.order_by(Dispatch.created_at.desc()), limit, offset, lambda d: dispatch_row(db, d)
    ) | {"counts": counts}


@router.get("/dispatches/{dispatch_id}")
def get_dispatch(dispatch_id: uuid.UUID, _: require("dispatch.view"), db: DB) -> dict:
    return dispatch_row(db, get_or_404(db, Dispatch, dispatch_id, "Dispatch"), full=True)


def _set(db: Session, d: Dispatch, body: DispatchIn) -> None:
    """Validate and write the plan: lines of this order, goods only, never more than still to send
    (counting other planned dispatches)."""
    so = get_or_404(db, SalesOrder, body.order_id, "Sales order")
    if so.status not in ("open", "partly_dispatched"):
        raise conflict(f"{so.number} is {so.status.replace('_', ' ')}")
    wh = db.get(Warehouse, body.warehouse_id)
    if wh is None or not wh.active:
        raise field_error("warehouse_id", "Choose an active warehouse")
    if body.site_id:
        site = get_or_404(db, Site, body.site_id, "Site")
        if not so.project_id or site.project_id != so.project_id:
            raise field_error("site_id", "Choose a site of this order's project")
    order_lines = {
        ln.id: ln
        for ln in db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == so.id))
    }
    other = dict(db.execute(select(DispatchLine.order_line_id, func.sum(DispatchLine.qty))
                            .join(Dispatch, Dispatch.id == DispatchLine.dispatch_id)
                            .where(Dispatch.status == "planned", Dispatch.id != d.id).group_by(DispatchLine.order_line_id)).all())  # fmt: skip
    items = {
        i.id: i
        for i in db.scalars(
            select(Item).where(
                Item.id.in_([ol.item_id for ol in order_lines.values() if ol.item_id])
            )
        )
    }
    per_line: dict = {}
    for ln in body.lines:
        ol = order_lines.get(ln.order_line_id)
        if ol is None or ol.line_type not in GOODS:
            raise field_error("lines", "Each line must be a goods line of this order")
        per_line[ol.id] = per_line.get(ol.id, ZERO) + ln.qty
        whole(items[ol.item_id], ln.qty, ln.serials)
        if ln.location_id:
            loc = active_location(db, ln.location_id)
            if loc.purpose != "available" or loc.warehouse_id != wh.id:
                raise field_error(
                    "location_id", "Pick from an available-stock location in this warehouse"
                )
    for ol_id, q in per_line.items():
        ol = order_lines[ol_id]
        left = ol.qty - ol.qty_dispatched - other.get(ol_id, ZERO)
        if q > left:
            raise conflict(f"{items[ol.item_id].code}: {q.normalize():f} planned but only {max(left, ZERO).normalize():f} "
                           "left to send (other planned dispatches count)")  # fmt: skip
    for k in (
        "order_id",
        "warehouse_id",
        "site_id",
        "planned_date",
        "destination",
        "delivery_contact",
        "transporter",
        "vehicle",
    ):
        setattr(d, k, getattr(body, k))
    if not d.destination:
        d.destination = (
            db.get(Site, d.site_id).address if d.site_id else ""
        ) or so.delivery_address
    db.flush()
    db.query(DispatchLine).filter(DispatchLine.dispatch_id == d.id).delete()
    db.add_all(DispatchLine(dispatch_id=d.id, order_line_id=ln.order_line_id, item_id=order_lines[ln.order_line_id].item_id,
                            qty=ln.qty, lot=ln.lot if items[order_lines[ln.order_line_id].item_id].tracking == "lot" else "",
                            location_id=ln.location_id, serials=ln.serials) for ln in body.lines)  # fmt: skip


@router.post("/dispatches", status_code=status.HTTP_201_CREATED)
def plan_dispatch(body: DispatchIn, p: require("dispatch.create"), db: DB) -> dict:
    get_or_404(db, SalesOrder, body.order_id, "Sales order")
    if db.get(Warehouse, body.warehouse_id) is None:
        raise field_error("warehouse_id", "Choose an active warehouse")
    d = Dispatch(
        number=next_number(db, "dispatch"),
        order_id=body.order_id,
        warehouse_id=body.warehouse_id,
        created_by=p.user.id,
    )
    db.add(d)
    db.flush()
    _set(db, d, body)
    record(
        db,
        p.user,
        "dispatch.plan",
        "dispatch",
        d.id,
        f"{d.number} planned for {db.get(SalesOrder, d.order_id).number}",
    )
    db.commit()
    return dispatch_row(db, d, full=True)


def _dispatch(db: Session, dispatch_id, version: int, *statuses: str) -> Dispatch:
    d = get_or_404(db, Dispatch, dispatch_id, "Dispatch", lock=True)
    if d.version != version:
        raise stale()
    if d.status not in statuses:
        raise conflict(f"{d.number} is {d.status}")
    return d


@router.put("/dispatches/{dispatch_id}")
def edit_dispatch(
    dispatch_id: uuid.UUID, body: DispatchPut, p: require("dispatch.create"), db: DB
) -> dict:
    d = _dispatch(db, dispatch_id, body.version, "planned")
    if body.order_id != d.order_id:
        raise field_error("order_id", "A dispatch stays with its order")
    _set(db, d, body)
    d.version += 1
    record(db, p.user, "dispatch.edit", "dispatch", d.id, f"{d.number} changed")
    db.commit()
    return dispatch_row(db, d, full=True)


def _reservation(db: Session, so: SalesOrder, item_id) -> list[Reservation]:
    refs = [("sales_order", str(so.id))] + (
        [("project", str(so.project_id))] if so.project_id else []
    )
    return [r for t, i in refs for r in db.scalars(select(Reservation).where(
        Reservation.ref_type == t, Reservation.ref_id == i, Reservation.item_id == item_id,
        Reservation.status == "active").order_by(Reservation.created_at).with_for_update())]  # fmt: skip


@router.post("/dispatches/{dispatch_id}/send")
def send(dispatch_id: uuid.UUID, body: Go, p: require("dispatch.create"), db: DB) -> dict:
    """Goods leave: only available stock, reservations of the order (then its project) first."""
    if (prev := claim_key(db, body.key, "dispatch_send")) is not None:
        return prev
    d = _dispatch(db, dispatch_id, body.version, "planned")
    so = get_or_404(db, SalesOrder, d.order_id, "Sales order", lock=True)
    if so.status not in ("open", "partly_dispatched"):
        raise conflict(f"{so.number} is {so.status.replace('_', ' ')}")
    lines = db.scalars(select(DispatchLine).where(DispatchLine.dispatch_id == d.id)).all()
    items = lock_items(db, [ln.item_id for ln in lines])
    order_lines = {
        ol.id: ol
        for ol in db.scalars(
            select(SalesOrderLine).where(SalesOrderLine.order_id == so.id).with_for_update()
        )
    }
    doc = ("dispatch", d.id, d.number)
    for ln in lines:
        item, ol = items[ln.item_id], order_lines[ln.order_line_id]
        if ln.qty > ol.qty - ol.qty_dispatched:
            raise conflict(f"{item.code}: more than is left to send on {so.number}")
        src = (
            db.get(Location, ln.location_id)
            if ln.location_id
            else warehouse_location(db, d.warehouse_id, "available")
        )
        if src.purpose != "available" or not src.active:
            raise conflict(f"{label(db, src)} is not available stock")
        left, used, uses = ln.qty, ZERO, []
        for r in _reservation(db, so, item.id):
            take = min(left, r.qty - r.qty_used)
            if not take:
                continue
            r.qty_used += take
            if r.qty_used >= r.qty:
                r.status = "used"
            r.version += 1
            uses.append({"reservation_id": str(r.id), "qty": str(take)})
            left, used = left - take, used + take
            if not left:
                break
        if left:
            check_free(db, item, left)
        ln.from_reserved, ln.reservation_use = used, uses
        ln.location_id = src.id
        if item.tracking == "serial":
            for s in serials_at(db, item, ln.serials, src, ("available",)):
                post(
                    db,
                    p.user,
                    item=item,
                    qty=Decimal(1),
                    src=src,
                    dst=None,
                    kind="dispatch",
                    doc=doc,
                    serial=s,
                )
                s.status, s.party_id, s.site_id = "dispatched", so.customer_id, d.site_id
        else:
            post(
                db,
                p.user,
                item=item,
                qty=ln.qty,
                src=src,
                dst=None,
                kind="dispatch",
                doc=doc,
                lot=ln.lot,
            )
        ol.qty_dispatched += ln.qty
    d.status, d.dispatched_at, d.version = "dispatched", utcnow(), d.version + 1
    refresh_order_status(db, so)
    so.version += 1
    record(db, p.user, "dispatch.send", "dispatch", d.id, f"{d.number} dispatched ({so.number})")
    result = remember(db, body.key, {"id": d.id, "status": d.status})
    db.commit()
    return result


@router.post("/dispatches/{dispatch_id}/deliver")
def deliver(
    dispatch_id: uuid.UUID, body: DeliveredIn, p: require("dispatch.create"), db: DB
) -> dict:
    """The customer's acknowledgement. Refused goods come back to the customer-returns area and are
    owed again on the order; damaged goods stay with the customer, recorded."""
    if (prev := claim_key(db, body.key, "dispatch_deliver")) is not None:
        return prev
    d = _dispatch(db, dispatch_id, body.version, "dispatched")
    if body.delivered_on > date.today():
        raise field_error("delivered_on", "Delivery can't be dated in the future")
    so = get_or_404(db, SalesOrder, d.order_id, "Sales order", lock=True)
    lines = {
        ln.id: ln for ln in db.scalars(select(DispatchLine).where(DispatchLine.dispatch_id == d.id))
    }
    if any(x.line_id not in lines for x in body.lines):
        raise field_error("lines", "A line is not on this dispatch")
    items = lock_items(db, [ln.item_id for ln in lines.values()])
    order_lines = {
        ol.id: ol
        for ol in db.scalars(
            select(SalesOrderLine).where(SalesOrderLine.order_id == so.id).with_for_update()
        )
    }
    back = warehouse_location(db, d.warehouse_id, "returns")
    doc = ("dispatch", d.id, d.number)
    refused = {x.line_id: x for x in body.lines}
    for ln in lines.values():
        item, x = items[ln.item_id], refused.get(ln.id)
        rejected = ZERO
        if x:
            if item.tracking == "serial":
                if set(x.rejected_serials) - set(ln.serials):
                    raise field_error(
                        "rejected_serials", "Only serials on this dispatch can be refused"
                    )
                if x.qty_rejected:
                    raise field_error(
                        "qty_rejected", f"{item.code}: name the refused serial numbers instead"
                    )
                rejected = Decimal(len(set(x.rejected_serials)))
            else:
                if x.rejected_serials:
                    raise field_error("rejected_serials", f"{item.code} has no serial numbers")
                rejected = x.qty_rejected
            if rejected + x.qty_damaged > ln.qty:
                raise field_error(
                    "qty_rejected", f"{item.code}: refused and damaged add up to more than was sent"
                )
            ln.qty_rejected, ln.qty_damaged, ln.rejected_serials, ln.note = (
                rejected,
                x.qty_damaged,
                sorted(set(x.rejected_serials)),
                x.note,
            )
            if rejected:
                if item.tracking == "serial":
                    for s in db.scalars(
                        select(Serial)
                        .where(Serial.serial.in_(ln.rejected_serials))
                        .with_for_update()
                    ):
                        post(db, p.user, item=item, qty=Decimal(1), src=None, dst=back, kind="customer_return", doc=doc, serial=s,
                             reason=x.note)  # fmt: skip
                        s.status, s.party_id, s.site_id = "on_hold", None, None
                else:
                    post(db, p.user, item=item, qty=rejected, src=None, dst=back, kind="customer_return", doc=doc, lot=ln.lot,
                         reason=x.note)  # fmt: skip
        ol = order_lines[ln.order_line_id]
        ol.qty_dispatched -= rejected
        ol.qty_delivered += ln.qty - rejected
        if item.tracking == "serial":
            for s in db.scalars(
                select(Serial).where(Serial.serial.in_(set(ln.serials) - set(ln.rejected_serials)))
            ):
                s.status = "delivered"
    d.status, d.received_by, d.delivered_on, d.delivery_notes = (
        "delivered",
        body.received_by,
        body.delivered_on,
        body.notes,
    )
    d.version += 1
    refresh_order_status(db, so)
    so.version += 1
    issues = [f"{items[ln.item_id].code}: {ln.qty_rejected.normalize():f} refused, {ln.qty_damaged.normalize():f} damaged"
              for ln in lines.values() if ln.qty_rejected or ln.qty_damaged]  # fmt: skip
    record(db, p.user, "dispatch.deliver", "dispatch", d.id,
           f"{d.number} delivered to {body.received_by}" + (f" with discrepancies: {'; '.join(issues)}" if issues else ""),
           reason=body.notes[:500])  # fmt: skip
    result = remember(db, body.key, {"id": d.id, "status": d.status})
    db.commit()
    return result


@router.post("/dispatches/{dispatch_id}/cancel")
def cancel(dispatch_id: uuid.UUID, body: CancelIn, p: require("dispatch.create"), db: DB) -> dict:
    """A planned dispatch is simply cancelled. One already sent (not yet delivered) comes back where
    it was picked from, and the reservations it used are given back."""
    if (prev := claim_key(db, body.key, "dispatch_cancel")) is not None:
        return prev
    d = _dispatch(db, dispatch_id, body.version, "planned", "dispatched")
    if d.status == "dispatched":
        so = get_or_404(db, SalesOrder, d.order_id, "Sales order", lock=True)
        lines = db.scalars(select(DispatchLine).where(DispatchLine.dispatch_id == d.id)).all()
        items = lock_items(db, [ln.item_id for ln in lines])
        order_lines = {
            ol.id: ol
            for ol in db.scalars(
                select(SalesOrderLine).where(SalesOrderLine.order_id == so.id).with_for_update()
            )
        }
        doc = ("dispatch", d.id, d.number)
        for ln in lines:
            item, dst = items[ln.item_id], db.get(Location, ln.location_id)
            if item.tracking == "serial":
                for s in db.scalars(
                    select(Serial).where(Serial.serial.in_(ln.serials)).with_for_update()
                ):
                    post(db, p.user, item=item, qty=Decimal(1), src=None, dst=dst, kind="dispatch_cancel", doc=doc, serial=s,
                         reason=body.reason)  # fmt: skip
                    s.status, s.party_id, s.site_id = "available", None, None
            else:
                post(db, p.user, item=item, qty=ln.qty, src=None, dst=dst, kind="dispatch_cancel", doc=doc, lot=ln.lot,
                     reason=body.reason)  # fmt: skip
            # Exactly what this line took goes back to the same reservations, unless the order
            # (or the reservation) has been closed since: then the goods simply return to free stock.
            if so.status in ("open", "partly_dispatched", "dispatched"):
                for use in ln.reservation_use:
                    r = db.get(Reservation, uuid.UUID(use["reservation_id"]), with_for_update=True)
                    if r is not None and r.status in ("active", "used"):
                        r.qty_used -= min(Decimal(use["qty"]), r.qty_used)
                        r.status, r.version = "active", r.version + 1
            order_lines[ln.order_line_id].qty_dispatched -= ln.qty
        refresh_order_status(db, so)
        so.version += 1
    d.status, d.cancel_reason, d.version = "cancelled", body.reason, d.version + 1
    record(
        db, p.user, "dispatch.cancel", "dispatch", d.id, f"{d.number} cancelled", reason=body.reason
    )
    result = remember(db, body.key, {"id": d.id, "status": d.status})
    db.commit()
    return result
