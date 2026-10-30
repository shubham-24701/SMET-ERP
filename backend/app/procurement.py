"""Purchasing: purchase requests, supplier quotations and their comparison, purchase orders with
approval, goods receipts with inspection decisions, and returns to suppliers.

Quantities on a purchase order line stay separate: ordered, received (= awaiting inspection +
accepted + rejected + held), returned, and outstanding (= ordered − accepted − held − awaiting:
rejected goods are still owed). Prices are reference data; no accounting happens here.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, Principal, require
from app.inventory import (
    Key,
    Lot,
    NeededReason,
    SerialNo,
    Serials,
    claim_key,
    conflict,
    lock_items,
    new_serial,
    post,
    remember,
    serials_at,
    warehouse_location,
    whole,
)
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, page, rules
from app.models import (
    Company,
    GoodsReceipt,
    GoodsReceiptLine,
    Item,
    Location,
    Party,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequest,
    PurchaseRequestLine,
    SupplierQuote,
    SupplierQuoteLine,
    SupplierReturn,
    SupplierReturnLine,
    User,
    Warehouse,
    utcnow,
)

router = APIRouter(tags=["purchasing"], route_class=NumbersRoute)

Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Price = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=4)]
Ref = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
ZERO = Decimal(0)


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()) if ids else {}


def _items(db: Session, ids) -> dict:
    return {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(set(ids))))}


def _check_items(db: Session, ids) -> dict:
    items = _items(db, ids)
    if len(items) != len(set(ids)):
        raise field_error("item_id", "Unknown item")
    if any(not i.active for i in items.values()):
        raise field_error("item_id", "One of the items is no longer active")
    return items


def _supplier(db: Session, party_id) -> Party:
    s = db.get(Party, party_id)
    if s is None or "supplier" not in s.kinds or not s.active:
        raise field_error("supplier_id", "Choose an active supplier")
    return s


# ---------- purchase requests ----------


class PRLineIn(BaseModel):
    item_id: uuid.UUID
    qty: Qty
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class PRIn(BaseModel):
    source: Literal["shortage", "assembly", "project", "other"]
    ref: Ref = ""
    needed_by: date | None = None
    notes: Text = ""
    lines: Annotated[list[PRLineIn], Field(min_length=1, max_length=200)]


class VersionReason(BaseModel):
    version: int
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def pr_row(db: Session, r: PurchaseRequest, full: bool = False) -> dict:
    row = {"id": r.id, "number": r.number, "status": r.status, "source": r.source, "ref": r.ref,
           "needed_by": r.needed_by, "notes": r.notes, "requested_by": _names(db, [r.requested_by]).get(r.requested_by),
           "created_at": r.created_at, "version": r.version}  # fmt: skip
    lines = db.scalars(select(PurchaseRequestLine).where(PurchaseRequestLine.pr_id == r.id)).all()
    items = _items(db, [ln.item_id for ln in lines])
    row["lines"] = [
        {"id": ln.id, "item": item_ref(items[ln.item_id]), "qty": ln.qty, "notes": ln.notes}
        for ln in lines
    ]
    if full:
        quotes = db.scalars(
            select(SupplierQuote)
            .where(SupplierQuote.pr_id == r.id)
            .order_by(SupplierQuote.created_at)
        ).all()
        row["quotes"] = [quote_row(db, q) for q in quotes]
        row["comparison"] = comparison(db, lines, items, row["quotes"])
        row["orders"] = [{"id": o.id, "number": o.number, "status": o.status} for o in
                         db.scalars(select(PurchaseOrder).where(PurchaseOrder.pr_id == r.id))]  # fmt: skip
    return row


def comparison(db: Session, lines, items, quotes: list[dict]) -> list[dict]:
    """Per requested item, each supplier's offer side by side; the lowest unit price per currency
    and the earliest delivery are marked (no exchange rates: prices in different currencies are
    not ranked against each other)."""
    out = []
    for ln in lines:
        offers = [{"quote_id": q["id"], "quote": q["number"], "supplier": q["supplier"]["name"],
                   "currency": q["currency"], "qty": x["qty"], "unit_price": x["unit_price"],
                   "total": x["qty"] * x["unit_price"], "delivery_date": x["delivery_date"],
                   "delivery_days": q["delivery_days"], "spec_notes": x["spec_notes"], "status": q["status"]}
                  for q in quotes for x in q["lines"] if x["item"]["id"] == ln.item_id]  # fmt: skip
        for cur in {o["currency"] for o in offers}:
            same = [o for o in offers if o["currency"] == cur]
            low = min(o["unit_price"] for o in same)
            for o in same:
                o["lowest_price"] = o["unit_price"] == low
        dated = [o for o in offers if o["delivery_date"]]
        for o in offers:
            o["earliest"] = bool(dated) and o["delivery_date"] == min(
                d["delivery_date"] for d in dated
            )
        out.append({"item": item_ref(items[ln.item_id]), "qty": ln.qty, "offers": offers})
    return out


@router.get("/purchase-requests")
def list_prs(
    _: require("purchasing.view"),
    db: DB,
    status_: Annotated[
        Literal["open", "ordered", "cancelled"] | None, Query(alias="status")
    ] = None,
    q: str = Query("", max_length=100),
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(PurchaseRequest)
    if status_:
        query = query.where(PurchaseRequest.status == status_)
    if q:
        query = query.where(
            or_(PurchaseRequest.number.ilike(f"%{q}%"), PurchaseRequest.ref.ilike(f"%{q}%"))
        )
    return page(
        db,
        query.order_by(PurchaseRequest.created_at.desc()),
        limit,
        offset,
        lambda r: pr_row(db, r),
    )


@router.get("/purchase-requests/{pr_id}")
def get_pr(pr_id: uuid.UUID, _: require("purchasing.view"), db: DB) -> dict:
    return pr_row(db, get_or_404(db, PurchaseRequest, pr_id, "Purchase request"), full=True)


def create_pr(db: Session, user: User, body: PRIn) -> PurchaseRequest:
    _check_items(db, [ln.item_id for ln in body.lines])
    r = PurchaseRequest(number=next_number(db, "purchase_request"), source=body.source, ref=body.ref,
                        needed_by=body.needed_by, notes=body.notes, requested_by=user.id)  # fmt: skip
    db.add(r)
    db.flush()
    db.add_all(PurchaseRequestLine(pr_id=r.id, **ln.model_dump()) for ln in body.lines)
    record(db, user, "purchase_request.create", "purchase_request", r.id,
           f"{r.number} raised ({body.source}{': ' + body.ref if body.ref else ''})")  # fmt: skip
    return r


@router.post("/purchase-requests", status_code=status.HTTP_201_CREATED)
def raise_pr(body: PRIn, p: require("purchasing.create"), db: DB) -> dict:
    r = create_pr(db, p.user, body)
    db.commit()
    return pr_row(db, r, full=True)


@router.post("/purchase-requests/{pr_id}/cancel")
def cancel_pr(
    pr_id: uuid.UUID, body: VersionReason, p: require("purchasing.create"), db: DB
) -> dict:
    r = get_or_404(db, PurchaseRequest, pr_id, "Purchase request", lock=True)
    if r.version != body.version:
        raise stale()
    if r.status != "open":
        raise conflict(f"{r.number} is {r.status}")
    r.status, r.version = "cancelled", r.version + 1
    record(
        db,
        p.user,
        "purchase_request.cancel",
        "purchase_request",
        r.id,
        f"{r.number} cancelled",
        reason=body.reason,
    )
    db.commit()
    return pr_row(db, r, full=True)


# ---------- supplier quotations ----------


class QuoteLineIn(BaseModel):
    item_id: uuid.UUID
    qty: Qty
    unit_price: Price
    delivery_date: date | None = None
    spec_notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class QuoteIn(BaseModel):
    pr_id: uuid.UUID | None = None
    supplier_id: uuid.UUID
    supplier_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    valid_until: date | None = None
    currency: Currency = "AED"
    delivery_days: int | None = Field(default=None, ge=0, le=730)
    notes: Text = ""
    lines: Annotated[list[QuoteLineIn], Field(min_length=1, max_length=200)]


def quote_row(db: Session, q: SupplierQuote) -> dict:
    s = db.get(Party, q.supplier_id)
    lines = db.scalars(select(SupplierQuoteLine).where(SupplierQuoteLine.quote_id == q.id)).all()
    items = _items(db, [ln.item_id for ln in lines])
    return {"id": q.id, "number": q.number, "pr_id": q.pr_id, "supplier": {"id": s.id, "code": s.code, "name": s.name},
            "supplier_ref": q.supplier_ref, "valid_until": q.valid_until, "currency": q.currency,
            "delivery_days": q.delivery_days, "notes": q.notes, "status": q.status, "created_at": q.created_at,
            "total": sum((ln.qty * ln.unit_price for ln in lines), ZERO),
            "lines": [{"id": ln.id, "item": item_ref(items[ln.item_id]), "qty": ln.qty, "unit_price": ln.unit_price,
                       "delivery_date": ln.delivery_date, "spec_notes": ln.spec_notes} for ln in lines]}  # fmt: skip


@router.get("/supplier-quotes")
def list_quotes(
    _: require("purchasing.view"),
    db: DB,
    pr_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(SupplierQuote)
    if pr_id:
        query = query.where(SupplierQuote.pr_id == pr_id)
    if supplier_id:
        query = query.where(SupplierQuote.supplier_id == supplier_id)
    return page(
        db,
        query.order_by(SupplierQuote.created_at.desc()),
        limit,
        offset,
        lambda q: quote_row(db, q),
    )


@router.post("/supplier-quotes", status_code=status.HTTP_201_CREATED)
def record_quote(body: QuoteIn, p: require("purchasing.create"), db: DB) -> dict:
    s = _supplier(db, body.supplier_id)
    _check_items(db, [ln.item_id for ln in body.lines])
    if body.pr_id:
        pr = get_or_404(db, PurchaseRequest, body.pr_id, "Purchase request")
        if pr.status != "open":
            raise conflict(f"{pr.number} is {pr.status}")
    q = SupplierQuote(number=next_number(db, "supplier_quote"), created_by=p.user.id,
                      **body.model_dump(exclude={"lines"}))  # fmt: skip
    db.add(q)
    db.flush()
    db.add_all(SupplierQuoteLine(quote_id=q.id, **ln.model_dump()) for ln in body.lines)
    record(
        db,
        p.user,
        "supplier_quote.create",
        "supplier_quote",
        q.id,
        f"{q.number} from {s.name} recorded",
    )
    db.commit()
    return quote_row(db, q)


@router.post("/supplier-quotes/{quote_id}/order", status_code=status.HTTP_201_CREATED)
def order_from_quote(quote_id: uuid.UUID, p: require("purchasing.create"), db: DB) -> dict:
    """Choose this quotation: a draft purchase order with its lines and prices. The other
    quotations for the same request are marked declined."""
    q = get_or_404(db, SupplierQuote, quote_id, "Supplier quotation", lock=True)
    if q.status != "open":
        raise conflict(f"{q.number} is already {q.status}")
    lines = db.scalars(select(SupplierQuoteLine).where(SupplierQuoteLine.quote_id == q.id)).all()
    supplier = _supplier(db, q.supplier_id)
    po = PurchaseOrder(number=next_number(db, "purchase_order"), supplier_id=q.supplier_id, currency=q.currency,
                       supplier_ref=q.supplier_ref, pr_id=q.pr_id, quote_id=q.id, terms=supplier.terms,
                       created_by=p.user.id)  # fmt: skip
    db.add(po)
    db.flush()
    db.add_all(PurchaseOrderLine(po_id=po.id, item_id=ln.item_id, qty_ordered=ln.qty, unit_price=ln.unit_price)
               for ln in lines)  # fmt: skip
    q.status = "selected"
    if q.pr_id:
        for other in db.scalars(select(SupplierQuote).where(SupplierQuote.pr_id == q.pr_id, SupplierQuote.id != q.id,
                                                            SupplierQuote.status == "open")):  # fmt: skip
            other.status = "declined"
        pr = db.get(PurchaseRequest, q.pr_id, with_for_update=True)
        if pr.status == "open":
            pr.status, pr.version = "ordered", pr.version + 1
    record(
        db,
        p.user,
        "purchase_order.create",
        "purchase_order",
        po.id,
        f"{po.number} drafted from {q.number}",
    )
    db.commit()
    return po_row(db, po, full=True)


# ---------- purchase orders ----------


class POLineIn(BaseModel):
    item_id: uuid.UUID
    qty: Qty
    unit_price: Price


class POIn(BaseModel):
    supplier_id: uuid.UUID
    currency: Currency = "AED"
    supplier_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    pr_id: uuid.UUID | None = None
    link_ref: Ref = ""
    expected_ship: date | None = None
    expected_arrival: date | None = None
    terms: Text = ""
    notes: Text = ""
    lines: Annotated[list[POLineIn], Field(min_length=1, max_length=200)]


class POPut(POIn):
    version: int


def outstanding(ln: PurchaseOrderLine) -> Decimal:
    return max(ln.qty_ordered - ln.qty_accepted - ln.qty_held - ln.qty_awaiting, ZERO)


def po_row(db: Session, o: PurchaseOrder, full: bool = False) -> dict:
    s = db.get(Party, o.supplier_id)
    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == o.id)
                       .order_by(PurchaseOrderLine.id)).all()  # fmt: skip
    names = _names(db, [o.created_by, o.submitted_by, o.decided_by])
    row = {"id": o.id, "number": o.number, "status": o.status, "supplier": {"id": s.id, "code": s.code, "name": s.name},
           "currency": o.currency, "supplier_ref": o.supplier_ref, "pr_id": o.pr_id, "quote_id": o.quote_id,
           "link_ref": o.link_ref, "expected_ship": o.expected_ship, "expected_arrival": o.expected_arrival,
           "terms": o.terms, "notes": o.notes, "created_by": names.get(o.created_by), "created_by_id": o.created_by,
           "submitted_by": names.get(o.submitted_by), "submitted_by_id": o.submitted_by, "submitted_at": o.submitted_at,
           "decided_by": names.get(o.decided_by), "decided_at": o.decided_at, "decision_reason": o.decision_reason,
           "total": sum((ln.qty_ordered * ln.unit_price for ln in lines), ZERO),
           "created_at": o.created_at, "version": o.version}  # fmt: skip
    if full:
        items = _items(db, [ln.item_id for ln in lines])
        row["lines"] = [{"id": ln.id, "item": item_ref(items[ln.item_id]), "qty_ordered": ln.qty_ordered,
                         "unit_price": ln.unit_price, "line_total": ln.qty_ordered * ln.unit_price,
                         "qty_received": ln.qty_received, "qty_awaiting": ln.qty_awaiting,
                         "qty_accepted": ln.qty_accepted, "qty_rejected": ln.qty_rejected, "qty_held": ln.qty_held,
                         "qty_returned": ln.qty_returned, "qty_outstanding": outstanding(ln)} for ln in lines]  # fmt: skip
        row["receipts"] = [{"id": r.id, "number": r.number, "delivery_ref": r.delivery_ref, "created_at": r.created_at}
                           for r in db.scalars(select(GoodsReceipt).where(GoodsReceipt.po_id == o.id)
                                               .order_by(GoodsReceipt.created_at))]  # fmt: skip
        row["returns"] = [{"id": r.id, "number": r.number, "reason": r.reason, "created_at": r.created_at,
                           "replacement_expected": r.replacement_expected}
                          for r in db.scalars(select(SupplierReturn).where(SupplierReturn.po_id == o.id))]  # fmt: skip
        row["company_currency"] = db.get(Company, 1).currency
    return row


@router.get("/purchase-orders")
def list_pos(
    _: require("purchasing.view"),
    db: DB,
    status_: Annotated[str, Query(alias="status", max_length=40)] = "",
    supplier_id: uuid.UUID | None = None,
    q: str = Query("", max_length=100),
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    """`status` may list several, comma-separated (e.g. approved,partly_received)."""
    query = select(PurchaseOrder)
    if status_:
        query = query.where(PurchaseOrder.status.in_(status_.split(",")))
    if supplier_id:
        query = query.where(PurchaseOrder.supplier_id == supplier_id)
    if q:
        query = query.join(Party, Party.id == PurchaseOrder.supplier_id).where(
            or_(
                PurchaseOrder.number.ilike(f"%{q}%"),
                PurchaseOrder.supplier_ref.ilike(f"%{q}%"),
                Party.name.ilike(f"%{q}%"),
            )
        )
    counts = dict(
        db.execute(select(PurchaseOrder.status, func.count()).group_by(PurchaseOrder.status)).all()
    )
    return page(
        db, query.order_by(PurchaseOrder.created_at.desc()), limit, offset, lambda o: po_row(db, o)
    ) | {"counts": counts}


@router.get("/purchase-orders/{po_id}")
def get_po(po_id: uuid.UUID, _: require("purchasing.view"), db: DB) -> dict:
    return po_row(db, get_or_404(db, PurchaseOrder, po_id, "Purchase order"), full=True)


def _set_po(db: Session, o: PurchaseOrder, body: POIn) -> None:
    _supplier(db, body.supplier_id)
    _check_items(db, [ln.item_id for ln in body.lines])
    if body.pr_id:
        get_or_404(db, PurchaseRequest, body.pr_id, "Purchase request")
    for k, v in body.model_dump(exclude={"lines", "version"}).items():
        setattr(o, k, v)
    db.flush()
    db.query(PurchaseOrderLine).filter(PurchaseOrderLine.po_id == o.id).delete()
    db.add_all(PurchaseOrderLine(po_id=o.id, item_id=ln.item_id, qty_ordered=ln.qty, unit_price=ln.unit_price)
               for ln in body.lines)  # fmt: skip


@router.post("/purchase-orders", status_code=status.HTTP_201_CREATED)
def create_po(body: POIn, p: require("purchasing.create"), db: DB) -> dict:
    o = PurchaseOrder(number=next_number(db, "purchase_order"), supplier_id=body.supplier_id,
                      currency=body.currency, created_by=p.user.id)  # fmt: skip
    db.add(o)
    _set_po(db, o, body)
    record(db, p.user, "purchase_order.create", "purchase_order", o.id, f"{o.number} drafted")
    db.commit()
    return po_row(db, o, full=True)


def _po(db: Session, po_id, version: int | None = None) -> PurchaseOrder:
    o = get_or_404(db, PurchaseOrder, po_id, "Purchase order", lock=True)
    if version is not None and o.version != version:
        raise stale()
    return o


@router.put("/purchase-orders/{po_id}")
def edit_po(po_id: uuid.UUID, body: POPut, p: require("purchasing.create"), db: DB) -> dict:
    """Drafts and rejected orders only; a rejected order goes back to draft for resubmission.
    An approved order is never edited (cancel it and raise a new one)."""
    o = _po(db, po_id, body.version)
    if o.status not in ("draft", "rejected"):
        raise conflict(f"{o.number} is {o.status}: only drafts and rejected orders can be changed")
    _set_po(db, o, body)
    o.status, o.version = "draft", o.version + 1
    record(db, p.user, "purchase_order.edit", "purchase_order", o.id, f"{o.number} changed")
    db.commit()
    return po_row(db, o, full=True)


@router.post("/purchase-orders/{po_id}/submit")
def submit_po(
    po_id: uuid.UUID, body: VersionReason, p: require("purchasing.create"), db: DB
) -> dict:
    o = _po(db, po_id, body.version)
    if o.status != "draft":
        raise conflict(f"{o.number} is {o.status}")
    o.status, o.submitted_by, o.submitted_at = "submitted", p.user.id, utcnow()
    total = po_row(db, o)["total"]
    limit = rules(db)["po_auto_approve_up_to"]
    company_currency = db.get(Company, 1).currency
    if limit is not None and o.currency == company_currency and total <= Decimal(str(limit)):
        o.status, o.decided_at = "approved", utcnow()
        o.decision_reason = f"Approved automatically: {total:.2f} {o.currency} is within the {Decimal(str(limit)):.2f} limit"
    o.version += 1
    record(db, p.user, "purchase_order.submit", "purchase_order", o.id,
           f"{o.number} submitted" + (" and approved automatically" if o.status == "approved" else " for approval"))  # fmt: skip
    db.commit()
    return po_row(db, o, full=True)


def _decide(db: Session, p: Principal, po_id, body: VersionReason) -> PurchaseOrder:
    o = _po(db, po_id, body.version)
    if o.status != "submitted":
        raise conflict(f"{o.number} is {o.status}, not waiting for approval")
    if p.user.id in (o.created_by, o.submitted_by) and not rules(db)["po_self_approval"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Someone else must approve a purchase order you raised or submitted",
        )
    return o


@router.post("/purchase-orders/{po_id}/approve")
def approve_po(
    po_id: uuid.UUID, body: VersionReason, p: require("purchasing.approve"), db: DB
) -> dict:
    o = _decide(db, p, po_id, body)
    o.status, o.decided_by, o.decided_at, o.decision_reason = (
        "approved",
        p.user.id,
        utcnow(),
        body.reason,
    )
    o.version += 1
    record(
        db,
        p.user,
        "purchase_order.approve",
        "purchase_order",
        o.id,
        f"{o.number} approved",
        reason=body.reason,
    )
    db.commit()
    return po_row(db, o, full=True)


@router.post("/purchase-orders/{po_id}/reject")
def reject_po(
    po_id: uuid.UUID, body: VersionReason, p: require("purchasing.approve"), db: DB
) -> dict:
    o = _decide(db, p, po_id, body)
    if not body.reason:
        raise field_error("reason", "Say why it is rejected, so it can be fixed")
    o.status, o.decided_by, o.decided_at, o.decision_reason = (
        "rejected",
        p.user.id,
        utcnow(),
        body.reason,
    )
    o.version += 1
    record(
        db,
        p.user,
        "purchase_order.reject",
        "purchase_order",
        o.id,
        f"{o.number} rejected",
        reason=body.reason,
    )
    db.commit()
    return po_row(db, o, full=True)


@router.post("/purchase-orders/{po_id}/cancel")
def cancel_po(
    po_id: uuid.UUID, body: VersionReason, p: require("purchasing.create"), db: DB
) -> dict:
    o = _po(db, po_id, body.version)
    if o.status not in ("draft", "submitted", "approved", "rejected"):
        raise conflict(f"{o.number} is {o.status}: goods were received, so close it instead")
    if not body.reason:
        raise field_error("reason", "Say why it is cancelled")
    o.status, o.version = "cancelled", o.version + 1
    record(
        db,
        p.user,
        "purchase_order.cancel",
        "purchase_order",
        o.id,
        f"{o.number} cancelled",
        reason=body.reason,
    )
    db.commit()
    return po_row(db, o, full=True)


@router.post("/purchase-orders/{po_id}/close")
def close_po(
    po_id: uuid.UUID, body: VersionReason, p: require("purchasing.create"), db: DB
) -> dict:
    """Stop expecting what is still outstanding (a short delivery the supplier won't complete)."""
    o = _po(db, po_id, body.version)
    if o.status not in ("partly_received", "received"):
        raise conflict(f"{o.number} is {o.status}")
    if not body.reason:
        raise field_error("reason", "Say why it is closed")
    o.status, o.version = "closed", o.version + 1
    record(
        db,
        p.user,
        "purchase_order.close",
        "purchase_order",
        o.id,
        f"{o.number} closed",
        reason=body.reason,
    )
    db.commit()
    return po_row(db, o, full=True)


def _refresh_status(db: Session, o: PurchaseOrder) -> None:
    lines = db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == o.id)).all()
    done = all(outstanding(ln) == 0 and ln.qty_awaiting == 0 and ln.qty_held == 0 for ln in lines)
    o.status = "received" if done else "partly_received"


# ---------- goods receipt and inspection decisions ----------


class Unit(BaseModel):
    serial: SerialNo
    supplier_serial: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""


class Decision(BaseModel):
    """How much of what is waiting is accepted, rejected or held; serial items name the units."""

    accept: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)] = ZERO
    reject: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)] = ZERO
    hold: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)] = ZERO
    accept_serials: Serials = []
    reject_serials: Serials = []
    hold_serials: Serials = []
    accept_location_id: uuid.UUID | None = None


class ReceiptLineIn(Decision):
    po_line_id: uuid.UUID
    qty: Qty
    lot: Lot = ""
    units: Annotated[list[Unit], Field(max_length=1000)] = []


class ReceiptIn(BaseModel):
    key: Key
    warehouse_id: uuid.UUID
    delivery_ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    notes: Text = ""
    lines: Annotated[list[ReceiptLineIn], Field(min_length=1, max_length=200)]


class DecideIn(Decision):
    key: Key
    warehouse_id: uuid.UUID
    lot: Lot = ""
    source: Literal["awaiting", "held"] = "awaiting"
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def _has_decision(d: Decision) -> bool:
    return bool(
        d.accept or d.reject or d.hold or d.accept_serials or d.reject_serials or d.hold_serials
    )


def decide(db: Session, user: User, o: PurchaseOrder, ln: PurchaseOrderLine, item: Item, wh: Warehouse,
           lot: str, d: Decision, source: str, doc: tuple) -> str:  # fmt: skip
    """Move what was waiting (or held) to available stock, rejected goods or quarantine, and keep
    the order line's counts in step. Item must be locked."""
    is_serial = item.tracking == "serial"
    if is_serial:
        d.accept, d.reject, d.hold = (
            Decimal(len(x)) for x in (d.accept_serials, d.reject_serials, d.hold_serials)
        )
        named = d.accept_serials + d.reject_serials + d.hold_serials
        if len(set(named)) != len(named):
            raise field_error("accept_serials", "A serial number is in two decisions")
    elif d.accept_serials or d.reject_serials or d.hold_serials:
        raise field_error("accept_serials", f"{item.code} has no serial numbers")
    total = d.accept + d.reject + d.hold
    pool = ln.qty_awaiting if source == "awaiting" else ln.qty_held
    if total > pool:
        raise conflict(f"{item.code}: {total.normalize():f} decided but only {pool.normalize():f} "
                       f"{'awaiting inspection' if source == 'awaiting' else 'on hold'}")  # fmt: skip
    if source == "held" and d.hold:
        raise field_error("hold", "These are already on hold")
    src = warehouse_location(db, wh.id, "inspection" if source == "awaiting" else "quarantine")
    accept_to = None
    if d.accept:
        accept_to = (
            db.get(Location, d.accept_location_id)
            if d.accept_location_id
            else warehouse_location(db, wh.id, "available")
        )
        if (
            accept_to is None
            or accept_to.purpose != "available"
            or not accept_to.active
            or accept_to.warehouse_id != wh.id
        ):
            raise field_error(
                "accept_location_id",
                "Accepted goods go to an active available-stock location in this warehouse",
            )
    dest = {"accept": accept_to, "reject": warehouse_location(db, wh.id, "rejected") if d.reject else None,
            "hold": warehouse_location(db, wh.id, "quarantine") if d.hold else None}  # fmt: skip
    new_status = {"accept": "available", "reject": "rejected", "hold": "on_hold"}
    from_status = ("awaiting_inspection",) if source == "awaiting" else ("on_hold",)
    for what in ("accept", "reject", "hold"):
        qty = getattr(d, what)
        if not qty:
            continue
        if is_serial:
            for s in serials_at(db, item, getattr(d, f"{what}_serials"), src, from_status):
                post(
                    db,
                    user,
                    item=item,
                    qty=Decimal(1),
                    src=src,
                    dst=dest[what],
                    kind=what,
                    doc=doc,
                    serial=s,
                )
                s.status = new_status[what]
        else:
            post(db, user, item=item, qty=qty, src=src, dst=dest[what], kind=what, doc=doc, lot=lot)
    if source == "awaiting":
        ln.qty_awaiting -= total
    else:
        ln.qty_held -= total
    ln.qty_accepted += d.accept
    ln.qty_rejected += d.reject
    ln.qty_held += d.hold
    return f"{item.code}: {d.accept.normalize():f} accepted, {d.reject.normalize():f} rejected, {d.hold.normalize():f} held"


def _receivable(db: Session, po_id) -> PurchaseOrder:
    o = _po(db, po_id)
    if o.status not in ("approved", "partly_received"):
        raise conflict(
            f"{o.number} is {o.status}: goods can be received only against an approved order"
        )
    return o


def _active_warehouse(db: Session, warehouse_id) -> Warehouse:
    wh = db.get(Warehouse, warehouse_id)
    if wh is None or not wh.active:
        raise field_error("warehouse_id", "Choose an active warehouse")
    return wh


@router.post("/purchase-orders/{po_id}/receipts", status_code=status.HTTP_201_CREATED)
def receive(po_id: uuid.UUID, body: ReceiptIn, p: require("receipts.create"), db: DB) -> dict:
    """Record a delivery. Everything lands in receiving inspection; a person who may also decide
    on quality (quality.create) can accept, reject or hold it in the same step."""
    if (prev := claim_key(db, body.key, "goods_receipt")) is not None:
        return prev
    if any(_has_decision(ln) for ln in body.lines) and not p.can("quality.create"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Accepting, rejecting or holding goods needs quality.create"
        )
    o = _receivable(db, po_id)
    wh = _active_warehouse(db, body.warehouse_id)
    po_lines = {ln.id: ln for ln in db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == o.id)
                                                .with_for_update())}  # fmt: skip
    if any(ln.po_line_id not in po_lines for ln in body.lines):
        raise field_error("po_line_id", "A line is not on this purchase order")
    if len({ln.po_line_id for ln in body.lines}) != len(body.lines):
        raise field_error("po_line_id", "Each order line only once per delivery")
    items = lock_items(db, [po_lines[ln.po_line_id].item_id for ln in body.lines])
    inspection = warehouse_location(db, wh.id, "inspection")
    gr = GoodsReceipt(number=next_number(db, "goods_receipt"), po_id=o.id, warehouse_id=wh.id,
                      delivery_ref=body.delivery_ref, notes=body.notes, received_by=p.user.id)  # fmt: skip
    db.add(gr)
    db.flush()
    doc = ("goods_receipt", gr.id, gr.number)
    done = []
    for ln in body.lines:
        pol, item = po_lines[ln.po_line_id], items[po_lines[ln.po_line_id].item_id]
        due = outstanding(pol)
        if ln.qty > due:
            raise conflict(f"{item.code}: {ln.qty.normalize():f} delivered but only {due.normalize():f} outstanding "
                           "on the order. Receive the ordered quantity and agree the rest with the supplier.")  # fmt: skip
        whole(item, ln.qty, [u.serial for u in ln.units])
        grl = GoodsReceiptLine(receipt_id=gr.id, po_line_id=pol.id, item_id=item.id, qty=ln.qty,
                               lot=ln.lot if item.tracking == "lot" else "")  # fmt: skip
        db.add(grl)
        db.flush()
        if item.tracking == "serial":
            for u in ln.units:
                s = new_serial(db, item, u.serial, "awaiting_inspection", supplier_serial=u.supplier_serial or None,
                               receipt_line_id=grl.id, po_line_id=pol.id, lot=ln.lot)  # fmt: skip
                post(
                    db,
                    p.user,
                    item=item,
                    qty=Decimal(1),
                    src=None,
                    dst=inspection,
                    kind="receipt",
                    doc=doc,
                    serial=s,
                )
        else:
            post(
                db,
                p.user,
                item=item,
                qty=ln.qty,
                src=None,
                dst=inspection,
                kind="receipt",
                doc=doc,
                lot=ln.lot,
            )
        pol.qty_received += ln.qty
        pol.qty_awaiting += ln.qty
        if _has_decision(ln):
            done.append(decide(db, p.user, o, pol, item, wh, grl.lot, ln, "awaiting", doc))
        else:
            done.append(f"{item.code}: {ln.qty.normalize():f} awaiting inspection")
    _refresh_status(db, o)
    o.version += 1
    record(db, p.user, "goods_receipt.create", "goods_receipt", gr.id,
           f"{gr.number} against {o.number}: {'; '.join(done)}"[:300])  # fmt: skip
    result = remember(db, body.key, {"id": gr.id, "number": gr.number, "po_id": o.id})
    db.commit()
    return result


@router.post("/purchase-orders/{po_id}/lines/{line_id}/decide")
def decide_line(
    po_id: uuid.UUID, line_id: uuid.UUID, body: DecideIn, p: require("quality.create"), db: DB
) -> dict:
    """Inspection decision on goods waiting in receiving inspection, or released from hold."""
    if (prev := claim_key(db, body.key, "receipt_decision")) is not None:
        return prev
    if not _has_decision(body):
        raise field_error("accept", "Enter what is accepted, rejected or held")
    o = _po(db, po_id)
    if o.status not in ("partly_received", "received", "closed"):
        raise conflict(f"{o.number} has nothing waiting for a decision")
    ln = db.get(PurchaseOrderLine, line_id, with_for_update=True)
    if ln is None or ln.po_id != o.id:
        raise field_error("line_id", "This line is not on the order")
    item = lock_items(db, [ln.item_id])[ln.item_id]
    wh = _active_warehouse(db, body.warehouse_id)
    gr_doc = ("purchase_order", o.id, o.number)
    summary = decide(
        db,
        p.user,
        o,
        ln,
        item,
        wh,
        body.lot if item.tracking == "lot" else "",
        body,
        body.source,
        gr_doc,
    )
    if o.status != "closed":
        _refresh_status(db, o)
    o.version += 1
    record(
        db,
        p.user,
        "goods_receipt.decide",
        "purchase_order",
        o.id,
        f"{o.number} {summary}",
        reason=body.notes,
    )
    result = remember(db, body.key, {"po_id": o.id})
    db.commit()
    return result


@router.get("/goods-receipts")
def list_receipts(
    _: require("purchasing.view"),
    db: DB,
    po_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(GoodsReceipt)
    if po_id:
        query = query.where(GoodsReceipt.po_id == po_id)
    return page(
        db,
        query.order_by(GoodsReceipt.created_at.desc()),
        limit,
        offset,
        lambda r: receipt_row(db, r),
    )


def receipt_row(db: Session, r: GoodsReceipt) -> dict:
    o, wh = db.get(PurchaseOrder, r.po_id), db.get(Warehouse, r.warehouse_id)
    lines = db.scalars(select(GoodsReceiptLine).where(GoodsReceiptLine.receipt_id == r.id)).all()
    items = _items(db, [ln.item_id for ln in lines])
    return {"id": r.id, "number": r.number, "po": {"id": o.id, "number": o.number}, "warehouse": wh.code,
            "delivery_ref": r.delivery_ref, "notes": r.notes, "received_by": _names(db, [r.received_by]).get(r.received_by),
            "created_at": r.created_at,
            "lines": [{"item": item_ref(items[ln.item_id]), "qty": ln.qty, "lot": ln.lot} for ln in lines]}  # fmt: skip


@router.get("/goods-receipts/{receipt_id}")
def get_receipt(receipt_id: uuid.UUID, _: require("purchasing.view"), db: DB) -> dict:
    return receipt_row(db, get_or_404(db, GoodsReceipt, receipt_id, "Goods receipt"))


# ---------- returns to supplier ----------


class ReturnLineIn(BaseModel):
    po_line_id: uuid.UUID
    qty: Qty
    lot: Lot = ""
    serials: Serials = []


class ReturnIn(BaseModel):
    key: Key
    warehouse_id: uuid.UUID
    reason: NeededReason
    replacement_expected: bool = True
    lines: Annotated[list[ReturnLineIn], Field(min_length=1, max_length=200)]


@router.post("/purchase-orders/{po_id}/returns", status_code=status.HTTP_201_CREATED)
def return_to_supplier(
    po_id: uuid.UUID, body: ReturnIn, p: require("purchasing.create"), db: DB
) -> dict:
    """Send rejected goods back. A replacement is expected unless said otherwise: the rejected
    quantity stays outstanding on the order until it arrives or the order is closed."""
    if (prev := claim_key(db, body.key, "supplier_return")) is not None:
        return prev
    o = _po(db, po_id)
    if o.status in ("draft", "submitted", "approved", "rejected", "cancelled"):
        raise conflict(f"{o.number} has no received goods to return")
    wh = _active_warehouse(db, body.warehouse_id)
    po_lines = {ln.id: ln for ln in db.scalars(select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == o.id)
                                                .with_for_update())}  # fmt: skip
    if any(ln.po_line_id not in po_lines for ln in body.lines):
        raise field_error("po_line_id", "A line is not on this purchase order")
    items = lock_items(db, [po_lines[ln.po_line_id].item_id for ln in body.lines])
    rejected_loc = warehouse_location(db, wh.id, "rejected")
    sr = SupplierReturn(number=next_number(db, "supplier_return"), po_id=o.id, reason=body.reason,
                        replacement_expected=body.replacement_expected, created_by=p.user.id)  # fmt: skip
    db.add(sr)
    db.flush()
    doc = ("supplier_return", sr.id, sr.number)
    for ln in body.lines:
        pol, item = po_lines[ln.po_line_id], items[po_lines[ln.po_line_id].item_id]
        left = pol.qty_rejected - pol.qty_returned
        if ln.qty > left:
            raise conflict(f"{item.code}: only {left.normalize():f} rejected and not yet returned")
        whole(item, ln.qty, ln.serials)
        if item.tracking == "serial":
            for s in serials_at(db, item, ln.serials, rejected_loc, ("rejected",)):
                post(db, p.user, item=item, qty=Decimal(1), src=rejected_loc, dst=None, kind="supplier_return",
                     doc=doc, serial=s, reason=body.reason)  # fmt: skip
                s.status = "returned_to_supplier"
        else:
            post(db, p.user, item=item, qty=ln.qty, src=rejected_loc, dst=None, kind="supplier_return", doc=doc,
                 lot=ln.lot, reason=body.reason)  # fmt: skip
        pol.qty_returned += ln.qty
        db.add(SupplierReturnLine(return_id=sr.id, po_line_id=pol.id, item_id=item.id, qty=ln.qty))
    o.version += 1
    record(
        db,
        p.user,
        "supplier_return.create",
        "supplier_return",
        sr.id,
        f"{sr.number} for {o.number}",
        reason=body.reason,
    )
    result = remember(db, body.key, {"id": sr.id, "number": sr.number, "po_id": o.id})
    db.commit()
    return result
