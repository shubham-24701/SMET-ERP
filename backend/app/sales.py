"""Phase 4 sales: enquiries, quotations with immutable revisions and approval, the customer's
answer, conversion to one sales order, and what the order needs (stock, purchase, assembly).

A quotation's content lives in revisions. A draft can be edited; from submission on a revision never
changes, and a change is a new revision (the old one becomes "revised"). An accepted revision
converts to exactly one sales order (unique key in the database).
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.assembly import OrderIn, plan_order
from app.audit import record
from app.auth import DB, Principal, require
from app.inventory import Key, claim_key, conflict, free, lock_items, release_for, remember, reserve
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, page, rules
from app.models import (
    Company,
    Dispatch,
    Enquiry,
    Item,
    Party,
    Project,
    Quotation,
    QuotationLine,
    QuotationRevision,
    Reservation,
    SalesOrder,
    SalesOrderLine,
    User,
    utcnow,
)
from app.procurement import PRIn, PRLineIn, create_pr, pr_row

router = APIRouter(tags=["sales"], route_class=NumbersRoute)

ZERO = Decimal(0)
Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
Price = Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=4)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
LineType = Literal["battery", "accessory", "installation", "commissioning", "amc", "other"]
GOODS = ("battery", "accessory")  # dispatched from stock; the rest are services (Phases 5–6)


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()) if ids else {}


def _party(db: Session, party_id, *kinds: str) -> Party:
    x = db.get(Party, party_id)
    if x is None or not x.active or not set(kinds) & set(x.kinds):
        raise field_error("customer_id", "Choose an active customer, distributor or dealer")
    return x


def party_ref(x: Party) -> dict:
    return {"id": x.id, "code": x.code, "name": x.name}


CUSTOMER_KINDS = ("customer", "distributor", "dealer")


# ---------- enquiries ----------


class EnquiryLine(BaseModel):
    item_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    qty: Qty


class EnquiryIn(BaseModel):
    customer_id: uuid.UUID
    kind: Literal["product", "project"]
    contact: Short = ""
    needed_by: date | None = None
    lines: Annotated[list[EnquiryLine], Field(min_length=1, max_length=100)]
    notes: Text = ""
    project_id: uuid.UUID | None = None


class EnquiryPatch(BaseModel):
    version: int
    status: Literal["open", "lost", "closed"] | None = None
    notes: Text | None = None
    reason: Reason = ""


def enquiry_row(db: Session, e: Enquiry) -> dict:
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(
        [uuid.UUID(x["item_id"]) for x in e.lines if x.get("item_id")])))}  # fmt: skip
    quotes = db.execute(
        select(Quotation.id, Quotation.number).where(Quotation.enquiry_id == e.id)
    ).all()
    return {"id": e.id, "number": e.number, "customer": party_ref(db.get(Party, e.customer_id)), "kind": e.kind,
            "status": e.status, "contact": e.contact, "needed_by": e.needed_by, "notes": e.notes,
            "project_id": e.project_id, "created_at": e.created_at, "version": e.version,
            "lines": [x | {"item": item_ref(items[uuid.UUID(x["item_id"])]) if x.get("item_id") else None} for x in e.lines],
            "quotations": [{"id": i, "number": n} for i, n in quotes]}  # fmt: skip


def _check_lines_items(db: Session, ids) -> dict:
    ids = [i for i in ids if i]
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(ids)))}
    if len(items) != len(set(ids)) or any(not i.active for i in items.values()):
        raise field_error("lines", "Unknown or inactive item")
    return items


@router.get("/enquiries")
def list_enquiries(
    _: require("sales.view"),
    db: DB,
    status_: Annotated[
        Literal["open", "quoted", "lost", "closed"] | None, Query(alias="status")
    ] = None,
    q: str = Query("", max_length=100),
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Enquiry)
    if status_:
        query = query.where(Enquiry.status == status_)
    if q:
        query = query.join(Party, Party.id == Enquiry.customer_id).where(
            or_(Enquiry.number.ilike(f"%{q}%"), Party.name.ilike(f"%{q}%"))
        )
    return page(
        db, query.order_by(Enquiry.created_at.desc()), limit, offset, lambda e: enquiry_row(db, e)
    )


@router.get("/enquiries/{enquiry_id}")
def get_enquiry(enquiry_id: uuid.UUID, _: require("sales.view"), db: DB) -> dict:
    return enquiry_row(db, get_or_404(db, Enquiry, enquiry_id, "Enquiry"))


@router.post("/enquiries", status_code=status.HTTP_201_CREATED)
def create_enquiry(body: EnquiryIn, p: require("sales.create"), db: DB) -> dict:
    _party(db, body.customer_id, *CUSTOMER_KINDS)
    _check_lines_items(db, [ln.item_id for ln in body.lines])
    if any(not ln.item_id and not ln.description for ln in body.lines):
        raise field_error("lines", "Each line needs an item or a description")
    if body.project_id:
        get_or_404(db, Project, body.project_id, "Project")
    e = Enquiry(number=next_number(db, "enquiry"), created_by=p.user.id,
                lines=[{"item_id": str(ln.item_id) if ln.item_id else None, "description": ln.description,
                        "qty": str(ln.qty)} for ln in body.lines],
                **body.model_dump(exclude={"lines"}))  # fmt: skip
    db.add(e)
    db.flush()
    record(db, p.user, "enquiry.create", "enquiry", e.id, f"{e.number} recorded")
    db.commit()
    return enquiry_row(db, e)


@router.patch("/enquiries/{enquiry_id}")
def update_enquiry(
    enquiry_id: uuid.UUID, body: EnquiryPatch, p: require("sales.create"), db: DB
) -> dict:
    e = get_or_404(db, Enquiry, enquiry_id, "Enquiry", lock=True)
    if e.version != body.version:
        raise stale()
    before = {"status": e.status, "notes": e.notes}
    if body.status:
        e.status = body.status
    if body.notes is not None:
        e.notes = body.notes
    e.version += 1
    record(db, p.user, "enquiry.update", "enquiry", e.id, f"{e.number}: {e.status}",
           {k: [before[k], getattr(e, k)] for k in before if before[k] != getattr(e, k)}, body.reason)  # fmt: skip
    db.commit()
    return enquiry_row(db, e)


# ---------- quotations ----------


class QuoteLineIn(BaseModel):
    line_type: LineType
    item_id: uuid.UUID | None = None
    description: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    qty: Qty
    unit_price: Price
    discount_pct: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] = ZERO


class RevisionIn(BaseModel):
    currency: Currency = "AED"
    valid_until: date | None = None
    terms: Text = ""
    scope_notes: Text = ""
    installation_date: date | None = None
    commissioning_date: date | None = None
    handover_date: date | None = None
    lines: Annotated[list[QuoteLineIn], Field(min_length=1, max_length=200)]


class QuotationIn(RevisionIn):
    customer_id: uuid.UUID
    enquiry_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None


class RevisionPut(RevisionIn):
    version: int


class Decision(BaseModel):
    version: int
    reason: Reason = ""


class Answer(BaseModel):
    version: int
    accepted: bool
    by: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
    ref: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    on: date
    reason: Reason = ""


def line_total(ln) -> Decimal:
    return (ln.qty * ln.unit_price * (100 - ln.discount_pct) / 100).quantize(Decimal("0.01"))


def _lines(db: Session, rev_id) -> list[QuotationLine]:
    return db.scalars(
        select(QuotationLine)
        .where(QuotationLine.revision_id == rev_id)
        .order_by(QuotationLine.sort)
    ).all()


def effective(r: QuotationRevision) -> str:
    """Approved or submitted revisions past their validity date read as expired."""
    if r.status in ("submitted", "approved") and r.valid_until and r.valid_until < date.today():
        return "expired"
    return r.status


def revision_row(db: Session, r: QuotationRevision) -> dict:
    lines = _lines(db, r.id)
    items = {
        i.id: i
        for i in db.scalars(
            select(Item).where(Item.id.in_([ln.item_id for ln in lines if ln.item_id]))
        )
    }
    names = _names(db, [r.submitted_by, r.decided_by, r.answer_recorded_by])
    return {"id": r.id, "rev": r.rev, "status": effective(r), "stored_status": r.status, "currency": r.currency,
            "valid_until": r.valid_until, "terms": r.terms, "scope_notes": r.scope_notes,
            "installation_date": r.installation_date, "commissioning_date": r.commissioning_date,
            "handover_date": r.handover_date, "editor_ids": r.editors,
            "submitted_by": names.get(r.submitted_by), "submitted_by_id": r.submitted_by, "submitted_at": r.submitted_at,
            "decided_by": names.get(r.decided_by), "decided_at": r.decided_at, "decision_reason": r.decision_reason,
            "customer_answer_by": r.customer_answer_by, "customer_ref": r.customer_ref,
            "customer_answer_at": r.customer_answer_at, "answer_recorded_by": names.get(r.answer_recorded_by),
            "total": sum((line_total(ln) for ln in lines), ZERO),
            "max_discount": max((ln.discount_pct for ln in lines), default=ZERO),
            "created_at": r.created_at, "version": r.version,
            "lines": [{"id": ln.id, "line_type": ln.line_type, "item": item_ref(items[ln.item_id]) if ln.item_id else None,
                       "description": ln.description, "qty": ln.qty, "unit_price": ln.unit_price,
                       "discount_pct": ln.discount_pct, "total": line_total(ln)} for ln in lines]}  # fmt: skip


def latest(db: Session, quotation_id, lock: bool = False) -> QuotationRevision:
    q = (
        select(QuotationRevision)
        .where(QuotationRevision.quotation_id == quotation_id)
        .order_by(QuotationRevision.rev.desc())
        .limit(1)
    )
    return db.scalar(q.with_for_update() if lock else q)


def quotation_row(db: Session, qt: Quotation, full: bool = False) -> dict:
    cur = latest(db, qt.id)
    row = {"id": qt.id, "number": qt.number, "customer": party_ref(db.get(Party, qt.customer_id)),
           "enquiry_id": qt.enquiry_id, "project_id": qt.project_id, "created_at": qt.created_at,
           "rev": cur.rev, "status": effective(cur), "currency": cur.currency, "valid_until": cur.valid_until,
           "total": sum((line_total(ln) for ln in _lines(db, cur.id)), ZERO)}  # fmt: skip
    if full:
        revs = db.scalars(select(QuotationRevision).where(QuotationRevision.quotation_id == qt.id)
                          .order_by(QuotationRevision.rev.desc())).all()  # fmt: skip
        row["revisions"] = [revision_row(db, r) for r in revs]
        so = db.execute(
            select(SalesOrder.id, SalesOrder.number).where(
                SalesOrder.quotation_revision_id.in_([r.id for r in revs])
            )
        ).first()
        row["sales_order"] = {"id": so[0], "number": so[1]} if so else None
        if qt.project_id:
            pr = db.get(Project, qt.project_id)
            row["project"] = {"id": pr.id, "number": pr.number, "name": pr.name}
    return row


def _set_lines(db: Session, r: QuotationRevision, lines: list[QuoteLineIn]) -> None:
    items = _check_lines_items(db, [ln.item_id for ln in lines])
    for ln in lines:
        if ln.line_type in GOODS and not ln.item_id:
            raise field_error("lines", f"A {ln.line_type} line needs an item")
        if ln.item_id and ln.line_type not in GOODS:
            raise field_error(
                "lines",
                "Services (installation, commissioning, AMC) are described, not picked as items",
            )
        if (
            ln.item_id
            and items[ln.item_id].tracking == "serial"
            and ln.qty != ln.qty.to_integral_value()
        ):
            raise field_error("lines", f"{items[ln.item_id].code} is sold in whole units")
        if not ln.item_id and not ln.description:
            raise field_error("lines", "Describe each service line")
    db.query(QuotationLine).filter(QuotationLine.revision_id == r.id).delete()
    db.add_all(
        QuotationLine(revision_id=r.id, sort=n, **ln.model_dump()) for n, ln in enumerate(lines)
    )


def _fill(r: QuotationRevision, body: RevisionIn, db: Session) -> None:
    for k in (
        "currency",
        "valid_until",
        "terms",
        "scope_notes",
        "installation_date",
        "commissioning_date",
        "handover_date",
    ):
        setattr(r, k, getattr(body, k))
    if r.valid_until is None:
        r.valid_until = date.today() + timedelta(days=int(rules(db)["quote_valid_days"]))


def create_quotation(db: Session, user: User, body: QuotationIn) -> Quotation:
    customer = _party(db, body.customer_id, *CUSTOMER_KINDS)
    if body.enquiry_id:
        e = get_or_404(db, Enquiry, body.enquiry_id, "Enquiry", lock=True)
        if e.customer_id != customer.id:
            raise field_error("enquiry_id", "The enquiry is from another customer")
        if e.status == "open":
            e.status, e.version = "quoted", e.version + 1
    if (
        body.project_id
        and get_or_404(db, Project, body.project_id, "Project").customer_id != customer.id
    ):
        raise field_error("project_id", "The project is for another customer")
    qt = Quotation(number=next_number(db, "quotation"), customer_id=customer.id, enquiry_id=body.enquiry_id,
                   project_id=body.project_id, created_by=user.id)  # fmt: skip
    db.add(qt)
    db.flush()
    r = QuotationRevision(quotation_id=qt.id, rev=1, editors=[user.id], currency=body.currency)
    _fill(r, body, db)
    db.add(r)
    db.flush()
    _set_lines(db, r, body.lines)
    record(
        db, user, "quotation.create", "quotation", qt.id, f"{qt.number} drafted for {customer.name}"
    )
    return qt


@router.get("/quotations")
def list_quotations(
    _: require("sales.view"),
    db: DB,
    q: str = Query("", max_length=100),
    customer_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Quotation)
    if q:
        query = query.join(Party, Party.id == Quotation.customer_id).where(
            or_(Quotation.number.ilike(f"%{q}%"), Party.name.ilike(f"%{q}%"))
        )
    if customer_id:
        query = query.where(Quotation.customer_id == customer_id)
    if project_id:
        query = query.where(Quotation.project_id == project_id)
    return page(
        db,
        query.order_by(Quotation.created_at.desc()),
        limit,
        offset,
        lambda x: quotation_row(db, x),
    )


@router.get("/quotations/{quotation_id}")
def get_quotation(quotation_id: uuid.UUID, _: require("sales.view"), db: DB) -> dict:
    return quotation_row(db, get_or_404(db, Quotation, quotation_id, "Quotation"), full=True)


@router.post("/quotations", status_code=status.HTTP_201_CREATED)
def new_quotation(body: QuotationIn, p: require("sales.create"), db: DB) -> dict:
    qt = create_quotation(db, p.user, body)
    db.commit()
    return quotation_row(db, qt, full=True)


@router.post("/enquiries/{enquiry_id}/quote", status_code=status.HTTP_201_CREATED)
def quote_enquiry(enquiry_id: uuid.UUID, p: require("sales.create"), db: DB) -> dict:
    """A draft quotation with the enquiry's lines; prices are filled in on the draft."""
    e = get_or_404(db, Enquiry, enquiry_id, "Enquiry")
    items = _check_lines_items(db, [uuid.UUID(x["item_id"]) for x in e.lines if x.get("item_id")])
    lines = []
    for x in e.lines:
        item = items.get(uuid.UUID(x["item_id"])) if x.get("item_id") else None
        kind = (
            "battery"
            if item and item.kind in ("battery_model", "battery_unit")
            else "accessory"
            if item
            else "other"
        )
        lines.append(QuoteLineIn(line_type=kind, item_id=item.id if item else None, description=x["description"],
                                 qty=Decimal(x["qty"]), unit_price=ZERO))  # fmt: skip
    qt = create_quotation(db, p.user, QuotationIn(customer_id=e.customer_id, enquiry_id=e.id, project_id=e.project_id,
                                                  currency=db.get(Party, e.customer_id).currency, lines=lines))  # fmt: skip
    db.commit()
    return quotation_row(db, qt, full=True)


def _current(
    db: Session, quotation_id, version: int, statuses: tuple[str, ...]
) -> tuple[Quotation, QuotationRevision]:
    qt = get_or_404(db, Quotation, quotation_id, "Quotation")
    r = latest(db, qt.id, lock=True)
    if r.version != version:
        raise stale()
    if effective(r) not in statuses:
        raise conflict(f"{qt.number} R{r.rev} is {effective(r)}")
    return qt, r


@router.put("/quotations/{quotation_id}/draft")
def edit_draft(
    quotation_id: uuid.UUID, body: RevisionPut, p: require("sales.create"), db: DB
) -> dict:
    qt, r = _current(db, quotation_id, body.version, ("draft",))
    _fill(r, body, db)
    _set_lines(db, r, body.lines)
    if p.user.id not in r.editors:
        r.editors = [*r.editors, p.user.id]
    r.version += 1
    record(db, p.user, "quotation.edit", "quotation", qt.id, f"{qt.number} R{r.rev} changed")
    db.commit()
    return quotation_row(db, qt, full=True)


@router.post("/quotations/{quotation_id}/submit")
def submit_quotation(
    quotation_id: uuid.UUID, body: Decision, p: require("sales.create"), db: DB
) -> dict:
    qt, r = _current(db, quotation_id, body.version, ("draft",))
    if r.valid_until and r.valid_until < date.today():
        raise field_error("valid_until", "The validity date has passed: change it first")
    row = revision_row(db, r)
    if row["total"] <= 0 and not any(ln["unit_price"] for ln in row["lines"]):
        raise field_error("lines", "Fill in the prices before submitting")
    rl, company = rules(db), db.get(Company, 1)
    r.status, r.submitted_by, r.submitted_at = "submitted", p.user.id, utcnow()
    limit = rl["quote_auto_approve_up_to"]
    if (limit is not None and r.currency == company.currency and row["total"] <= Decimal(str(limit))
            and row["max_discount"] <= Decimal(str(rl["quote_max_discount_pct"]))):  # fmt: skip
        r.status, r.decided_at = "approved", utcnow()
        r.decision_reason = "Approved automatically: within the limits in Rules & numbering"
    r.version += 1
    record(db, p.user, "quotation.submit", "quotation", qt.id,
           f"{qt.number} R{r.rev} submitted" + (" and approved automatically" if r.status == "approved" else ""))  # fmt: skip
    db.commit()
    return quotation_row(db, qt, full=True)


def _decide(
    db: Session, p: Principal, quotation_id, body: Decision
) -> tuple[Quotation, QuotationRevision]:
    qt, r = _current(db, quotation_id, body.version, ("submitted",))
    if p.user.id in {r.submitted_by, *r.editors} and not rules(db)["quote_self_approval"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Someone else must approve a quotation you prepared or submitted",
        )
    return qt, r


@router.post("/quotations/{quotation_id}/approve")
def approve_quotation(
    quotation_id: uuid.UUID, body: Decision, p: require("sales.approve"), db: DB
) -> dict:
    qt, r = _decide(db, p, quotation_id, body)
    r.status, r.decided_by, r.decided_at, r.decision_reason = (
        "approved",
        p.user.id,
        utcnow(),
        body.reason,
    )
    r.version += 1
    record(
        db,
        p.user,
        "quotation.approve",
        "quotation",
        qt.id,
        f"{qt.number} R{r.rev} approved",
        reason=body.reason,
    )
    db.commit()
    return quotation_row(db, qt, full=True)


@router.post("/quotations/{quotation_id}/reject")
def reject_quotation(
    quotation_id: uuid.UUID, body: Decision, p: require("sales.approve"), db: DB
) -> dict:
    qt, r = _decide(db, p, quotation_id, body)
    if not body.reason:
        raise field_error("reason", "Say why, so it can be revised")
    r.status, r.decided_by, r.decided_at, r.decision_reason = (
        "rejected",
        p.user.id,
        utcnow(),
        body.reason,
    )
    r.version += 1
    record(
        db,
        p.user,
        "quotation.reject",
        "quotation",
        qt.id,
        f"{qt.number} R{r.rev} rejected",
        reason=body.reason,
    )
    db.commit()
    return quotation_row(db, qt, full=True)


@router.post("/quotations/{quotation_id}/revise", status_code=status.HTTP_201_CREATED)
def revise_quotation(
    quotation_id: uuid.UUID, body: Decision, p: require("sales.create"), db: DB
) -> dict:
    """A new draft revision copied from the current one, which is kept unchanged as "revised"."""
    qt, r = _current(
        db, quotation_id, body.version, ("submitted", "approved", "rejected", "declined", "expired")
    )
    shapers = list(
        dict.fromkeys([*r.editors, *([r.submitted_by] if r.submitted_by else []), p.user.id])
    )
    new = QuotationRevision(quotation_id=qt.id, rev=r.rev + 1, editors=shapers, currency=r.currency,
                            valid_until=None, terms=r.terms, scope_notes=r.scope_notes,
                            installation_date=r.installation_date, commissioning_date=r.commissioning_date,
                            handover_date=r.handover_date)  # fmt: skip
    new.valid_until = date.today() + timedelta(days=int(rules(db)["quote_valid_days"]))
    db.add(new)
    db.flush()
    db.add_all(QuotationLine(revision_id=new.id, line_type=ln.line_type, item_id=ln.item_id, description=ln.description,
                             qty=ln.qty, unit_price=ln.unit_price, discount_pct=ln.discount_pct, sort=ln.sort)
               for ln in _lines(db, r.id))  # fmt: skip
    r.status, r.version = "revised", r.version + 1
    record(
        db,
        p.user,
        "quotation.revise",
        "quotation",
        qt.id,
        f"{qt.number} R{new.rev} drafted from R{r.rev}",
        reason=body.reason,
    )
    db.commit()
    return quotation_row(db, qt, full=True)


@router.post("/quotations/{quotation_id}/answer")
def record_answer(
    quotation_id: uuid.UUID, body: Answer, p: require("sales.create"), db: DB
) -> dict:
    """The customer's acceptance or refusal of the approved revision, as SMET received it."""
    qt, r = _current(db, quotation_id, body.version, ("approved",))
    if body.on > date.today():
        raise field_error("on", "The answer can't be dated in the future")
    r.status = "accepted" if body.accepted else "declined"
    r.customer_answer_by, r.customer_ref, r.customer_answer_at = body.by, body.ref, body.on
    r.answer_recorded_by, r.version = p.user.id, r.version + 1
    record(db, p.user, f"quotation.{r.status}", "quotation", qt.id,
           f"{qt.number} R{r.rev} {r.status} by {body.by}" + (f" (ref {body.ref})" if body.ref else ""), reason=body.reason)  # fmt: skip
    db.commit()
    return quotation_row(db, qt, full=True)


class ConvertIn(BaseModel):
    version: int
    delivery_date: date | None = None
    delivery_address: Text = ""
    notes: Text = ""


@router.post("/quotations/{quotation_id}/convert", status_code=status.HTTP_201_CREATED)
def convert(quotation_id: uuid.UUID, body: ConvertIn, p: require("sales.create"), db: DB) -> dict:
    """The accepted revision becomes one sales order. A second attempt finds the first order."""
    qt, r = _current(db, quotation_id, body.version, ("accepted",))
    existing = db.scalar(select(SalesOrder).where(SalesOrder.quotation_revision_id == r.id))
    if existing:
        raise conflict(f"{qt.number} R{r.rev} is already {existing.number}")
    so = SalesOrder(number=next_number(db, "sales_order"), customer_id=qt.customer_id, quotation_revision_id=r.id,
                    project_id=qt.project_id, currency=r.currency, customer_ref=r.customer_ref,
                    delivery_date=body.delivery_date, delivery_address=body.delivery_address, notes=body.notes,
                    created_by=p.user.id)  # fmt: skip
    db.add(so)
    try:
        db.flush()
    except IntegrityError:  # a simultaneous conversion won
        db.rollback()
        raise conflict(f"{qt.number} R{r.rev} was just converted by someone else") from None
    db.add_all(SalesOrderLine(order_id=so.id, line_type=ln.line_type, item_id=ln.item_id, description=ln.description,
                              qty=ln.qty, unit_price=ln.unit_price, discount_pct=ln.discount_pct, sort=ln.sort)
               for ln in _lines(db, r.id))  # fmt: skip
    record(
        db,
        p.user,
        "sales_order.create",
        "sales_order",
        so.id,
        f"{so.number} from {qt.number} R{r.rev}",
    )
    db.commit()
    return order_row(db, so, full=True)


# ---------- sales orders ----------


def _own(db: Session, ref_type: str, ref_id: str) -> dict:
    return dict(db.execute(select(Reservation.item_id, func.sum(Reservation.qty - Reservation.qty_used))
                           .where(Reservation.ref_type == ref_type, Reservation.ref_id == ref_id,
                                  Reservation.status == "active").group_by(Reservation.item_id)).all())  # fmt: skip


def _pending(db: Session, so: SalesOrder) -> dict:
    out: dict = {}
    for ln in db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == so.id)):
        if ln.line_type in GOODS:
            out[ln.item_id] = out.get(ln.item_id, ZERO) + ln.qty - ln.qty_dispatched
    return out


def reservations_of(db: Session, so: SalesOrder) -> dict:
    """What is still reserved for this order: its own reservations, and its share of its project's.
    A project's reservation is shared once across the project's open orders, oldest order first,
    so two orders never both count the same reserved units."""
    out = {"sales_order": _own(db, "sales_order", str(so.id))}
    if so.project_id:
        pool = _own(db, "project", str(so.project_id))
        earlier = db.scalars(select(SalesOrder).where(
            SalesOrder.project_id == so.project_id, SalesOrder.id != so.id,
            SalesOrder.status.in_(("open", "partly_dispatched")),
            or_(SalesOrder.created_at < so.created_at, and_(SalesOrder.created_at == so.created_at, SalesOrder.id < so.id)))
            .order_by(SalesOrder.created_at, SalesOrder.id))  # fmt: skip
        for other in earlier:
            own = _own(db, "sales_order", str(other.id))
            for item_id, need in _pending(db, other).items():
                pool[item_id] = pool.get(item_id, ZERO) - min(
                    max(need - own.get(item_id, ZERO), ZERO), pool.get(item_id, ZERO)
                )
        mine = _pending(db, so)
        out["project"] = {
            i: min(q, max(mine.get(i, ZERO) - out["sales_order"].get(i, ZERO), ZERO))
            for i, q in pool.items()
            if q > 0
        }
    return out


def order_row(db: Session, so: SalesOrder, full: bool = False) -> dict:
    lines = db.scalars(
        select(SalesOrderLine).where(SalesOrderLine.order_id == so.id).order_by(SalesOrderLine.sort)
    ).all()
    row = {"id": so.id, "number": so.number, "customer": party_ref(db.get(Party, so.customer_id)), "status": so.status,
           "currency": so.currency, "customer_ref": so.customer_ref, "delivery_date": so.delivery_date,
           "delivery_address": so.delivery_address, "notes": so.notes, "project_id": so.project_id,
           "quotation_revision_id": so.quotation_revision_id, "created_at": so.created_at, "version": so.version,
           "total": sum((line_total(ln) for ln in lines), ZERO),
           "pending_goods": sum((ln.qty - ln.qty_dispatched for ln in lines if ln.line_type in GOODS), ZERO)}  # fmt: skip
    if full:
        items = {
            i.id: i
            for i in db.scalars(
                select(Item).where(Item.id.in_([ln.item_id for ln in lines if ln.item_id]))
            )
        }
        res = reservations_of(db, so)
        row["lines"] = []
        for ln in lines:
            item = items.get(ln.item_id)
            r_so = res.get("sales_order", {}).get(ln.item_id, ZERO)
            r_pr = res.get("project", {}).get(ln.item_id, ZERO)
            pending = ln.qty - ln.qty_dispatched
            free_ = free(db, ln.item_id) if item else ZERO
            short = (
                max(pending - r_so - r_pr - max(free_, ZERO), ZERO)
                if ln.line_type in GOODS
                else ZERO
            )
            row["lines"].append({"id": ln.id, "line_type": ln.line_type, "item": item_ref(item) if item else None,
                                 "assembled": bool(item and item.assembled), "description": ln.description,
                                 "qty": ln.qty, "unit_price": ln.unit_price, "discount_pct": ln.discount_pct,
                                 "total": line_total(ln), "qty_dispatched": ln.qty_dispatched,
                                 "qty_delivered": ln.qty_delivered, "pending": pending, "reserved": r_so,
                                 "reserved_for_project": r_pr, "free": free_, "short": short})  # fmt: skip
        row["dispatches"] = [{"id": d.id, "number": d.number, "status": d.status, "planned_date": d.planned_date}
                             for d in db.scalars(select(Dispatch).where(Dispatch.order_id == so.id).order_by(Dispatch.created_at))]  # fmt: skip
        if so.quotation_revision_id:
            qr = db.get(QuotationRevision, so.quotation_revision_id)
            qt = db.get(Quotation, qr.quotation_id)
            row["quotation"] = {"id": qt.id, "number": qt.number, "rev": qr.rev}
        if so.project_id:
            pr = db.get(Project, so.project_id)
            row["project"] = {"id": pr.id, "number": pr.number, "name": pr.name}
    return row


@router.get("/sales-orders")
def list_orders(
    _: require("sales.view"),
    db: DB,
    status_: Annotated[str, Query(alias="status", max_length=60)] = "",
    q: str = Query("", max_length=100),
    project_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(SalesOrder)
    if status_:
        query = query.where(SalesOrder.status.in_(status_.split(",")))
    if project_id:
        query = query.where(SalesOrder.project_id == project_id)
    if q:
        query = query.join(Party, Party.id == SalesOrder.customer_id).where(
            or_(
                SalesOrder.number.ilike(f"%{q}%"),
                SalesOrder.customer_ref.ilike(f"%{q}%"),
                Party.name.ilike(f"%{q}%"),
            )
        )
    counts = dict(
        db.execute(select(SalesOrder.status, func.count()).group_by(SalesOrder.status)).all()
    )
    return page(
        db, query.order_by(SalesOrder.created_at.desc()), limit, offset, lambda s: order_row(db, s)
    ) | {"counts": counts}


@router.get("/sales-orders/{order_id}")
def get_order(order_id: uuid.UUID, _: require("sales.view"), db: DB) -> dict:
    return order_row(db, get_or_404(db, SalesOrder, order_id, "Sales order"), full=True)


def _open_order(db: Session, order_id) -> SalesOrder:
    so = get_or_404(db, SalesOrder, order_id, "Sales order", lock=True)
    if so.status not in ("open", "partly_dispatched"):
        raise conflict(f"{so.number} is {so.status.replace('_', ' ')}")
    return so


class KeyIn(BaseModel):
    key: Key


@router.post("/sales-orders/{order_id}/reserve")
def reserve_for_order(order_id: uuid.UUID, body: KeyIn, p: require("sales.create"), db: DB) -> dict:
    """Reserve free stock for what is still to be dispatched (as much as is free, line by line)."""
    if (prev := claim_key(db, body.key, "so_reserve")) is not None:
        return prev
    so = _open_order(db, order_id)
    lines = [ln for ln in db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == so.id))
             if ln.line_type in GOODS]  # fmt: skip
    items = lock_items(db, [ln.item_id for ln in lines]) if lines else {}
    have = reservations_of(db, so)
    want: dict = {}
    for ln in lines:
        want[ln.item_id] = want.get(ln.item_id, ZERO) + ln.qty - ln.qty_dispatched
    done = []
    for item_id, need in want.items():
        need -= have.get("sales_order", {}).get(item_id, ZERO) + have.get("project", {}).get(
            item_id, ZERO
        )
        take = min(need, max(free(db, item_id), ZERO))
        if items[item_id].tracking == "serial":
            take = take.to_integral_value(rounding="ROUND_FLOOR")
        if take > 0:
            reserve(db, p.user, items[item_id], take, "sales_order", str(so.id), so.number)
            done.append(f"{items[item_id].code} {take.normalize():f}")
    record(db, p.user, "sales_order.reserve", "sales_order", so.id,
           f"{so.number}: reserved {', '.join(done) or 'nothing (no free stock)'}")  # fmt: skip
    result = remember(db, body.key, {"id": so.id, "reserved": done})
    db.commit()
    return result


@router.post("/sales-orders/{order_id}/purchase-request", status_code=status.HTTP_201_CREATED)
def order_shortage_request(order_id: uuid.UUID, p: require("sales.create"), db: DB) -> dict:
    """A purchase request for bought goods this order is short of (assembled models need an
    assembly order instead)."""
    so = _open_order(db, order_id)
    short = [
        x for x in order_row(db, so, full=True)["lines"] if x["short"] > 0 and not x["assembled"]
    ]
    if not short:
        raise conflict("Nothing bought is short for this order")
    r = create_pr(db, p.user, PRIn(source="project" if so.project_id else "other", ref=so.number,
                                   lines=[PRLineIn(item_id=x["item"]["id"], qty=x["short"], notes=f"For {so.number}") for x in short]))  # fmt: skip
    db.commit()
    return pr_row(db, r, full=True)


class AssemblyFor(BaseModel):
    line_id: uuid.UUID
    warehouse_id: uuid.UUID
    qty: int | None = Field(default=None, ge=1, le=10_000)


@router.post("/sales-orders/{order_id}/assembly-order", status_code=status.HTTP_201_CREATED)
def order_assembly(
    order_id: uuid.UUID, body: AssemblyFor, p: require("assembly.create", "sales.view"), db: DB
) -> dict:
    """A planned assembly order for an assembled battery this order is short of."""
    so = _open_order(db, order_id)
    line = next((x for x in order_row(db, so, full=True)["lines"] if x["id"] == body.line_id), None)
    if line is None or not line["assembled"]:
        raise field_error("line_id", "Choose a line for a battery SMET assembles")
    qty = body.qty or int(line["short"])
    if qty < 1:
        raise conflict("Nothing is short on this line")
    return plan_order(OrderIn(item_id=line["item"]["id"], qty_planned=qty, warehouse_id=body.warehouse_id,
                              source="sales_order", source_ref=so.number), p, db)  # fmt: skip


@router.post("/sales-orders/{order_id}/close")
def close_order(order_id: uuid.UUID, body: Decision, p: require("sales.create"), db: DB) -> dict:
    """Close what remains (or cancel an order nothing was dispatched for); reservations go back."""
    so = _open_order(db, order_id)
    if so.version != body.version:
        raise stale()
    if not body.reason:
        raise field_error("reason", "Say why")
    open_dispatch = db.scalar(
        select(Dispatch.number).where(Dispatch.order_id == so.id, Dispatch.status == "planned")
    )
    if open_dispatch:
        raise conflict(f"{open_dispatch} is still planned: cancel or send it first")
    sent = (
        db.scalar(
            select(func.sum(SalesOrderLine.qty_dispatched)).where(SalesOrderLine.order_id == so.id)
        )
        or ZERO
    )
    so.status = "closed" if sent > 0 else "cancelled"
    release_for(db, "sales_order", str(so.id))
    so.version += 1
    record(
        db,
        p.user,
        f"sales_order.{so.status}",
        "sales_order",
        so.id,
        f"{so.number} {so.status}",
        reason=body.reason,
    )
    db.commit()
    return order_row(db, so, full=True)


def refresh_order_status(db: Session, so: SalesOrder) -> None:
    lines = [
        ln
        for ln in db.scalars(select(SalesOrderLine).where(SalesOrderLine.order_id == so.id))
        if ln.line_type in GOODS
    ]
    if not lines or so.status in ("closed", "cancelled"):
        return
    sent = sum((ln.qty_dispatched for ln in lines), ZERO)
    so.status = (
        "dispatched"
        if all(ln.qty_dispatched >= ln.qty for ln in lines)
        else "partly_dispatched"
        if sent
        else "open"
    )
