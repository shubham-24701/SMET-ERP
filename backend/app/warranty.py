"""Phase 6 warranty: SMET's policies per battery model, registrations and their decisions.

Two routes lead to a registration (proposal p. 19): a SMET installation opens one for every battery
of a bank at handover; a distributor's or customer's sale is registered by SMET staff or through the
public form. Either way the registration is checked against the policy in force for that model:
the start date comes from the event the policy names, the expiry from its length. A policy can
approve automatically when nothing is missing; otherwise someone other than the submitter decides.
Dispatch never starts a warranty. Approved registrations change only through an authorised
correction with a reason, recorded in the audit log.
"""

import calendar
import uuid
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import any_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, Principal, require
from app.files import store
from app.inventory import NeededReason, conflict
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, rules
from app.models import (
    AuditEvent,
    BankPosition,
    BatteryBank,
    Dispatch,
    DispatchLine,
    Item,
    Party,
    Project,
    Serial,
    Site,
    StoredFile,
    User,
    WarrantyPolicy,
    WarrantyRegistration,
    utcnow,
)
from app.sales import party_ref

router = APIRouter(tags=["warranty"], route_class=NumbersRoute)
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
SerialNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
EVENTS = {"sale": "sale_date", "installation": "installation_date",
          "commissioning": "commissioning_date", "handover": "handover_date"}  # fmt: skip
OPEN = ("pending", "correction_required", "approved")
LEFT_SMET = ("delivered", "installed", "commissioned", "in_service", "removed", "in_repair")
NO_PROOF_ROUTES = ("smet_installation", "replacement")  # SMET's own order and visits are the proof


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    return d.replace(
        year=d.year + y, month=m + 1, day=min(d.day, calendar.monthrange(d.year + y, m + 1)[1])
    )


def expiry_for(start: date, months: int) -> date:
    """The last day covered: 12 months from 15 Mar 2026 ends on 14 Mar 2027. When the anniversary
    doesn't exist (29 Feb, 31 Jan + 1 month) the last day of that month is covered."""
    anniversary = add_months(start, months)
    return (
        anniversary
        if anniversary.day < start.day
        else date.fromordinal(anniversary.toordinal() - 1)
    )


def policy_for(db: Session, item_id, on: date | None) -> WarrantyPolicy | None:
    """The policy in force for this model on that day (the latest one started by then)."""
    if on is None:
        return None
    return db.scalar(select(WarrantyPolicy).where(WarrantyPolicy.item_id == item_id, WarrantyPolicy.active,
                                                  WarrantyPolicy.effective_from <= on)
                     .order_by(WarrantyPolicy.effective_from.desc()).limit(1))  # fmt: skip


def evaluate(
    db: Session, r: WarrantyRegistration
) -> tuple[WarrantyPolicy | None, date | None, date | None, list[str]]:
    """The policy, start and expiry this registration would get, and what stops it, in words."""
    s = db.get(Serial, r.serial_id)
    problems = []
    if s.status not in LEFT_SMET:
        problems.append(
            f"{s.serial} has not been sold or delivered (it is {s.status.replace('_', ' ')})"
        )
    if not (r.customer_name or r.customer_id):
        problems.append("The customer's name")
    # Which policy: the one in force on the earliest date we know for this battery's sale.
    known = [
        d for d in (r.sale_date, r.installation_date, r.commissioning_date, r.handover_date) if d
    ]
    pol = policy_for(db, s.item_id, min(known)) if known else None
    if pol is None:
        any_pol = db.scalar(
            select(func.count())
            .select_from(WarrantyPolicy)
            .where(WarrantyPolicy.item_id == s.item_id)
        )
        problems.append("No warranty policy for this model yet: SMET sets it under Warranty policies" if not any_pol
                        else "No warranty policy in force on the sale date" if known else "The sale date")  # fmt: skip
        return None, None, None, problems
    start = getattr(r, EVENTS[pol.start_event])
    if start is None:
        problems.append(f"The {pol.start_event} date (the warranty starts then)")
    elif start > date.today():
        problems.append(f"The {pol.start_event} date is in the future")
    if pol.requires_proof and r.route not in NO_PROOF_ROUTES:
        has = db.scalar(select(StoredFile.id).where(StoredFile.owner_type == "warranty", StoredFile.owner_id == r.id,
                                                    StoredFile.kind == "proof_of_purchase").limit(1))  # fmt: skip
        if not has:
            problems.append("Proof of purchase (attach the invoice or receipt)")
    return pol, start, expiry_for(start, pol.months) if start else None, problems


def _approve(db: Session, r: WarrantyRegistration, user: User | None, reason: str) -> None:
    pol, start, expiry, problems = evaluate(db, r)
    if problems:
        raise HTTPException(
            422, {"message": "Not ready to approve: " + "; ".join(problems), "missing": problems}
        )
    r.status, r.policy_id, r.start_date, r.expiry_date = "approved", pol.id, start, expiry
    r.decided_by, r.decided_at, r.decision_reason = (user.id if user else None), utcnow(), reason
    r.version += 1
    s = db.get(Serial, r.serial_id)
    record(db, user, "warranty.approve", "warranty_registration", r.id,
           f"{r.number}: warranty for {s.serial} active {start} to {expiry}" + ("" if user else " (approved by policy)"), reason=reason)  # fmt: skip


def auto_check(db: Session, r: WarrantyRegistration) -> None:
    """Approves by itself only where SMET's policy says so and nothing is missing."""
    pol, _, _, problems = evaluate(db, r)
    if pol and pol.auto_approve and not problems and r.status == "pending":
        _approve(db, r, None, "Every requirement of the policy was met")


def _new(db: Session, **kw) -> WarrantyRegistration:
    r = WarrantyRegistration(number=next_number(db, "warranty"), **kw)
    try:
        with db.begin_nested():
            db.add(r)
            db.flush()
    except IntegrityError:
        other = db.scalar(select(WarrantyRegistration.number).where(WarrantyRegistration.serial_id == kw["serial_id"],
                                                                    WarrantyRegistration.status.in_(OPEN)))  # fmt: skip
        raise conflict(f"This battery already has warranty registration {other}") from None
    return r


# ---------- policies ----------


class PolicyIn(BaseModel):
    item_id: uuid.UUID
    months: int = Field(ge=1, le=600)
    start_event: Literal["sale", "installation", "commissioning", "handover"]
    auto_approve: bool = False
    requires_proof: bool = True
    replacement_rule: Literal["original_remaining", "new_full", "longer"]
    effective_from: date
    notes: Text = ""
    active: bool = True


class PolicyPut(PolicyIn):
    version: int


TERMS = (
    "item_id",
    "months",
    "start_event",
    "auto_approve",
    "requires_proof",
    "replacement_rule",
    "effective_from",
)


def policy_row(db: Session, x: WarrantyPolicy) -> dict:
    used = db.scalar(
        select(func.count())
        .select_from(WarrantyRegistration)
        .where(WarrantyRegistration.policy_id == x.id)
    )
    return {"id": x.id, "item": item_ref(db.get(Item, x.item_id)), "months": x.months, "start_event": x.start_event,
            "auto_approve": x.auto_approve, "requires_proof": x.requires_proof, "replacement_rule": x.replacement_rule,
            "effective_from": x.effective_from, "notes": x.notes, "active": x.active, "used": used, "version": x.version}  # fmt: skip


@router.get("/warranty-policies")
def list_policies(_: require("warranty.view"), db: DB) -> dict:
    rows = db.scalars(select(WarrantyPolicy).join(Item, Item.id == WarrantyPolicy.item_id)
                      .order_by(Item.code, WarrantyPolicy.effective_from.desc())).all()  # fmt: skip
    return {"items": [policy_row(db, x) for x in rows]}


def _battery_model(db: Session, item_id) -> Item:
    it = get_or_404(db, Item, item_id, "Item")
    if it.kind not in ("battery_model", "battery_unit"):
        raise field_error("item_id", "Warranty policies are for battery models and units")
    return it


@router.post("/warranty-policies", status_code=status.HTTP_201_CREATED)
def add_policy(body: PolicyIn, p: require("warranty.configure"), db: DB) -> dict:
    it = _battery_model(db, body.item_id)
    x = WarrantyPolicy(**body.model_dump())
    try:
        with db.begin_nested():
            db.add(x)
            db.flush()
    except IntegrityError:
        raise field_error(
            "effective_from", f"{it.code} already has a policy from that date"
        ) from None
    record(db, p.user, "warranty_policy.create", "warranty_policy", x.id,
           f"{it.code}: {x.months} months from {x.start_event}, from {x.effective_from}")  # fmt: skip
    db.commit()
    return policy_row(db, x)


@router.put("/warranty-policies/{policy_id}")
def update_policy(
    policy_id: uuid.UUID, body: PolicyPut, p: require("warranty.configure"), db: DB
) -> dict:
    """Terms of a policy that warranties already use can't change: make a new one from a later date.
    Notes and switching it off are always allowed."""
    x = get_or_404(db, WarrantyPolicy, policy_id, "Policy", lock=True)
    if x.version != body.version:
        raise stale()
    new = body.model_dump(exclude={"version"})
    changed = {k: [str(getattr(x, k)), str(v)] for k, v in new.items() if getattr(x, k) != v}
    if any(k in TERMS for k in changed) and policy_row(db, x)["used"]:
        raise conflict(
            "Warranties already use this policy, so its terms stay. Add a new policy from the date the new terms apply."
        )
    if "item_id" in changed:
        _battery_model(db, body.item_id)
    for k, v in new.items():
        setattr(x, k, v)
    x.version += 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error(
            "effective_from", "This model already has a policy from that date"
        ) from None
    record(
        db,
        p.user,
        "warranty_policy.update",
        "warranty_policy",
        x.id,
        "Warranty policy changed",
        changed,
    )
    db.commit()
    return policy_row(db, x)


# ---------- registrations ----------


def reg_row(db: Session, r: WarrantyRegistration, full: bool = False) -> dict:
    s = db.get(Serial, r.serial_id)
    item = db.get(Item, s.item_id)
    expired = r.status == "approved" and r.expiry_date is not None and r.expiry_date < date.today()
    row = {"id": r.id, "number": r.number, "serial": {"id": s.id, "serial": s.serial}, "item": item_ref(item),
           "route": r.route, "status": r.status, "expired": expired, "customer_name": r.customer_name,
           "start_date": r.start_date, "expiry_date": r.expiry_date, "created_at": r.created_at, "version": r.version}  # fmt: skip
    if full:
        files = db.scalars(select(StoredFile).where(StoredFile.owner_type == "warranty", StoredFile.owner_id == r.id)
                           .order_by(StoredFile.uploaded_at)).all()  # fmt: skip
        ids = [r.submitted_by, r.decided_by, *(f.uploaded_by for f in files)]
        names = dict(
            db.execute(select(User.id, User.name).where(User.id.in_([i for i in ids if i]))).all()
        )
        pol, start, expiry, problems = (
            evaluate(db, r)
            if r.status in ("pending", "correction_required")
            else (None, None, None, [])
        )
        history = db.scalars(select(AuditEvent).where(AuditEvent.entity_type == "warranty_registration",
                                                      AuditEvent.entity_id == str(r.id)).order_by(AuditEvent.id)).all()  # fmt: skip
        replaces = db.get(WarrantyRegistration, r.replaces_id) if r.replaces_id else None
        row |= {
            "customer": party_ref(db.get(Party, r.customer_id)) if r.customer_id else None,
            "customer_contact": r.customer_contact, "address": r.address,
            "distributor": party_ref(db.get(Party, r.distributor_id)) if r.distributor_id else None,
            "distributor_name": r.distributor_name, "sale_date": r.sale_date, "installation_date": r.installation_date,
            "commissioning_date": r.commissioning_date, "handover_date": r.handover_date, "notes": r.notes,
            "policy": policy_row(db, db.get(WarrantyPolicy, r.policy_id or (pol.id if pol else None))) if (r.policy_id or pol) else None,
            "would_start": start, "would_expire": expiry, "missing": problems,
            "ended_on": r.ended_on, "end_reason": r.end_reason,
            "replaces": {"id": replaces.id, "number": replaces.number} if replaces else None,
            "submitted_by": names.get(r.submitted_by) if r.submitted_by else ("Public form" if r.route == "public" else "System"),
            "submitted_by_id": r.submitted_by, "decided_by": names.get(r.decided_by) if r.decided_by else ("Policy" if r.decided_at else None),
            "decided_at": r.decided_at, "decision_reason": r.decision_reason,
            "files": [{"id": f.id, "kind": f.kind, "name": f.name, "content_type": f.content_type, "size": f.size,
                       "caption": f.caption, "uploaded_by": names.get(f.uploaded_by) or "Public form", "uploaded_at": f.uploaded_at} for f in files],
            "history": [{"at": e.at, "who": e.actor_name, "action": e.action, "summary": e.summary, "reason": e.reason,
                         "changes": e.changes} for e in history],
        }  # fmt: skip
    return row


@router.get("/warranty-registrations")
def list_registrations(
    _: require("warranty.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(WarrantyRegistration).join(Serial, Serial.id == WarrantyRegistration.serial_id)
    if status_:
        query = query.where(WarrantyRegistration.status.in_(status_.split(",")))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(WarrantyRegistration.number.ilike(like), Serial.serial.ilike(like),
                                WarrantyRegistration.customer_name.ilike(like)))  # fmt: skip
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(WarrantyRegistration.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {"items": [reg_row(db, r) for r in rows], "total": total}


@router.get("/warranty-registrations/{reg_id}")
def get_registration(reg_id: uuid.UUID, _: require("warranty.view"), db: DB) -> dict:
    return reg_row(db, get_or_404(db, WarrantyRegistration, reg_id, "Registration"), full=True)


class RegIn(BaseModel):
    serial: SerialNo
    route: Literal["distributor", "direct"]
    customer_id: uuid.UUID | None = None
    customer_name: Short = ""
    customer_contact: Short = ""
    address: Text = ""
    distributor_id: uuid.UUID | None = None
    distributor_name: Short = ""
    sale_date: date | None = None
    installation_date: date | None = None
    commissioning_date: date | None = None
    notes: Text = ""


class RegPut(BaseModel):
    version: int
    customer_id: uuid.UUID | None = None
    customer_name: Short = ""
    customer_contact: Short = ""
    address: Text = ""
    distributor_id: uuid.UUID | None = None
    distributor_name: Short = ""
    sale_date: date | None = None
    installation_date: date | None = None
    commissioning_date: date | None = None
    notes: Text = ""


def _parties(db: Session, body) -> None:
    for field, kind in (
        ("customer_id", ("customer",)),
        ("distributor_id", ("distributor", "dealer")),
    ):
        pid = getattr(body, field)
        if pid:
            party = db.get(Party, pid)
            if party is None or not any(k in party.kinds for k in kind):
                raise field_error(field, f"Choose a {' or '.join(kind)}")


@router.post("/warranty-registrations", status_code=status.HTTP_201_CREATED)
def register(body: RegIn, p: require("warranty.create"), db: DB) -> dict:
    """For a battery sold through a distributor or directly, registered by SMET staff. Attach the
    proof of purchase next; the policy is checked each time something changes."""
    s = db.scalar(select(Serial).where(Serial.serial == body.serial.upper()))
    if s is None:
        raise field_error("serial", "No battery with that serial number")
    if db.get(Item, s.item_id).kind not in ("battery_model", "battery_unit"):
        raise field_error("serial", f"{s.serial} is not a battery")
    _parties(db, body)
    data = body.model_dump(exclude={"serial"})
    if body.customer_id and not body.customer_name:
        data["customer_name"] = db.get(Party, body.customer_id).name
    r = _new(db, serial_id=s.id, submitted_by=p.user.id, **data)
    record(
        db,
        p.user,
        "warranty.register",
        "warranty_registration",
        r.id,
        f"{r.number}: {s.serial} registered ({r.route})",
    )
    auto_check(db, r)
    db.commit()
    return reg_row(db, r, full=True)


def _editable(db: Session, reg_id, version: int) -> WarrantyRegistration:
    r = get_or_404(db, WarrantyRegistration, reg_id, "Registration", lock=True)
    if r.version != version:
        raise stale()
    if r.status not in ("pending", "correction_required"):
        raise conflict(
            f"{r.number} is {r.status.replace('_', ' ')}; an approved warranty changes only by a correction"
        )
    return r


@router.put("/warranty-registrations/{reg_id}")
def update_registration(
    reg_id: uuid.UUID, body: RegPut, p: require("warranty.create"), db: DB
) -> dict:
    """Completing or fixing a registration before the decision. A registration sent back for
    correction is pending again once changed."""
    r = _editable(db, reg_id, body.version)
    _parties(db, body)
    data = body.model_dump(exclude={"version"})
    changes = {k: [str(getattr(r, k)), str(v)] for k, v in data.items() if getattr(r, k) != v}
    for k, v in data.items():
        setattr(r, k, v)
    r.status, r.version = "pending", r.version + 1
    record(
        db,
        p.user,
        "warranty.update",
        "warranty_registration",
        r.id,
        f"{r.number}: details completed",
        changes,
    )
    auto_check(db, r)
    db.commit()
    return reg_row(db, r, full=True)


@router.post("/warranty-registrations/{reg_id}/files", status_code=status.HTTP_201_CREATED)
def upload_registration_file(
    reg_id: uuid.UUID,
    p: require("warranty.create"),
    db: DB,
    file: Annotated[UploadFile, File()],
    kind: Annotated[
        Literal["proof_of_purchase", "installation_photo", "other"], Form()
    ] = "proof_of_purchase",
    caption: Annotated[str, Form(max_length=300)] = "",
) -> dict:
    r = get_or_404(db, WarrantyRegistration, reg_id, "Registration", lock=True)
    if r.status in ("rejected", "ended"):
        raise conflict(f"{r.number} is {r.status}")
    store(
        db, p.user, "warranty", r.id, file, kind, caption, ("warranty_registration", r.id, r.number)
    )
    r = db.get(WarrantyRegistration, reg_id, with_for_update=True)
    if r.status == "pending":
        auto_check(db, r)
        db.commit()
    return reg_row(db, r, full=True)


class DecideIn(BaseModel):
    version: int
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def _decider(db: Session, p: Principal, r: WarrantyRegistration) -> None:
    if r.submitted_by == p.user.id and not rules(db)["warranty_self_approval"]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You submitted this registration, so someone else decides it"
        )


@router.post("/warranty-registrations/{reg_id}/approve")
def approve(reg_id: uuid.UUID, body: DecideIn, p: require("warranty.approve"), db: DB) -> dict:
    r = _editable(db, reg_id, body.version)
    _decider(db, p, r)
    if r.status != "pending":
        raise conflict("Waiting for the correction asked for; it is pending again once changed")
    _approve(db, r, p.user, body.reason)
    db.commit()
    return reg_row(db, r, full=True)


class ReasonIn(BaseModel):
    version: int
    reason: NeededReason


@router.post("/warranty-registrations/{reg_id}/reject")
def reject(reg_id: uuid.UUID, body: ReasonIn, p: require("warranty.approve"), db: DB) -> dict:
    r = _editable(db, reg_id, body.version)
    _decider(db, p, r)
    r.status, r.decided_by, r.decided_at, r.decision_reason, r.version = (
        "rejected",
        p.user.id,
        utcnow(),
        body.reason,
        r.version + 1,
    )
    record(
        db,
        p.user,
        "warranty.reject",
        "warranty_registration",
        r.id,
        f"{r.number} rejected",
        reason=body.reason,
    )
    db.commit()
    return reg_row(db, r, full=True)


@router.post("/warranty-registrations/{reg_id}/request-correction")
def request_correction(
    reg_id: uuid.UUID, body: ReasonIn, p: require("warranty.approve"), db: DB
) -> dict:
    r = _editable(db, reg_id, body.version)
    _decider(db, p, r)
    r.status, r.decision_reason, r.version = "correction_required", body.reason, r.version + 1
    record(db, p.user, "warranty.correction_requested", "warranty_registration", r.id,
           f"{r.number}: correction asked for", reason=body.reason)  # fmt: skip
    db.commit()
    return reg_row(db, r, full=True)


class CorrectIn(BaseModel):
    version: int
    reason: NeededReason
    customer_name: Short | None = None
    customer_contact: Short | None = None
    address: Text | None = None
    sale_date: date | None = None
    installation_date: date | None = None
    commissioning_date: date | None = None
    handover_date: date | None = None


@router.post("/warranty-registrations/{reg_id}/correct")
def correct(reg_id: uuid.UUID, body: CorrectIn, p: require("warranty.correct"), db: DB) -> dict:
    """An authorised correction of an approved warranty. The dates are worked out again from its
    policy; every change is kept in the audit log with the reason."""
    r = get_or_404(db, WarrantyRegistration, reg_id, "Registration", lock=True)
    if r.version != body.version:
        raise stale()
    if r.status != "approved":
        raise conflict("Only an approved warranty is corrected; a pending one is simply edited")
    data = {
        k: v
        for k, v in body.model_dump(exclude={"version", "reason"}).items()
        if k in body.model_fields_set and v is not None
    }
    before = {k: str(getattr(r, k)) for k in data}
    for k, v in data.items():
        setattr(r, k, v)
    pol = db.get(WarrantyPolicy, r.policy_id)
    start = getattr(r, EVENTS[pol.start_event])
    if start is None or start > date.today():
        raise field_error(
            EVENTS[pol.start_event], f"The {pol.start_event} date starts this warranty"
        )
    old_dates = (str(r.start_date), str(r.expiry_date))
    r.start_date, r.expiry_date, r.version = start, expiry_for(start, pol.months), r.version + 1
    changes = {k: [before[k], str(getattr(r, k))] for k in data if before[k] != str(getattr(r, k))}
    if old_dates != (str(r.start_date), str(r.expiry_date)):
        changes |= {
            "start_date": [old_dates[0], str(r.start_date)],
            "expiry_date": [old_dates[1], str(r.expiry_date)],
        }
    if not changes:
        raise field_error("reason", "Nothing would change")
    record(
        db,
        p.user,
        "warranty.correct",
        "warranty_registration",
        r.id,
        f"{r.number} corrected",
        changes,
        reason=body.reason,
    )
    db.commit()
    return reg_row(db, r, full=True)


# ---------- registrations opened by SMET's own installation, and replacements ----------


def open_at_handover(
    db: Session, user: User, banks: list[BatteryBank]
) -> list[WarrantyRegistration]:
    """At handover each battery of the banks gets a registration with its real dates; the policy then
    decides whether it is approved at once or waits for someone. Batteries that already have an
    open registration (e.g. registered at sale) keep it."""
    out = []
    for b in banks:
        site = db.get(Site, b.site_id)
        pr = db.get(Project, site.project_id)
        for (s,) in db.execute(select(Serial).join(BankPosition, BankPosition.serial_id == Serial.id)
                               .where(BankPosition.bank_id == b.id, BankPosition.ended_at.is_(None))):  # fmt: skip
            if db.scalar(select(WarrantyRegistration.id).where(WarrantyRegistration.serial_id == s.id,
                                                               WarrantyRegistration.status.in_(OPEN))):  # fmt: skip
                continue
            sold = _delivered_on(db, s)
            r = _new(db, serial_id=s.id, route="smet_installation", customer_id=pr.customer_id,
                     customer_name=db.get(Party, pr.customer_id).name, address=site.address or site.name,
                     sale_date=sold, installation_date=b.installed_on, commissioning_date=b.commissioned_on,
                     handover_date=b.handed_over_on, submitted_by=user.id, notes=f"Opened at handover of {b.number}")  # fmt: skip
            record(
                db,
                user,
                "warranty.register",
                "warranty_registration",
                r.id,
                f"{r.number}: {s.serial} registered at handover of {b.number}",
            )
            auto_check(db, r)
            out.append(r)
    return out


def _delivered_on(db: Session, s: Serial) -> date | None:
    """When it was delivered to the customer (and not refused): the sale date."""
    return db.scalar(select(func.max(Dispatch.delivered_on)).join(DispatchLine, DispatchLine.dispatch_id == Dispatch.id)
                     .where(Dispatch.status == "delivered", any_(DispatchLine.serials) == s.serial,
                            ~(any_(DispatchLine.rejected_serials) == s.serial)))  # fmt: skip


def carry_to_replacement(
    db: Session, user: User, old: Serial, new: Serial, on: date, why: str
) -> WarrantyRegistration | None:
    """The replacement's warranty under the original battery's policy (replacement_rule): the rest
    of the original's term, a full new term, or whichever is longer. The original's warranty ends.
    Nothing is created if the original had no approved warranty."""
    orig = db.scalar(select(WarrantyRegistration).where(WarrantyRegistration.serial_id == old.id,
                                                        WarrantyRegistration.status == "approved").with_for_update())  # fmt: skip
    if orig is None:
        return None
    rule = db.get(WarrantyPolicy, orig.policy_id).replacement_rule
    new_pol = policy_for(db, new.item_id, on)
    expiries = {"original_remaining": [orig.expiry_date]}
    if rule in ("new_full", "longer"):
        if new_pol is None:
            raise conflict(
                f"No warranty policy for {db.get(Item, new.item_id).code} on {on}: SMET sets it before this replacement closes"
            )
        expiries["new_full"] = [expiry_for(on, new_pol.months)]
        expiries["longer"] = [orig.expiry_date, expiry_for(on, new_pol.months)]
    orig.status, orig.ended_on, orig.end_reason, orig.version = (
        "ended",
        on,
        f"Replaced by {new.serial}: {why}"[:300],
        orig.version + 1,
    )
    db.flush()
    r = _new(db, serial_id=new.id, route="replacement", status="approved", customer_id=orig.customer_id,
             customer_name=orig.customer_name, customer_contact=orig.customer_contact, address=orig.address,
             distributor_id=orig.distributor_id, distributor_name=orig.distributor_name, sale_date=on,
             installation_date=on, policy_id=(new_pol.id if rule != "original_remaining" and new_pol else orig.policy_id),
             start_date=on, expiry_date=max(expiries[rule]), replaces_id=orig.id, submitted_by=user.id,
             decided_by=user.id, decided_at=utcnow(), decision_reason=f"Replacement ({rule.replace('_', ' ')})")  # fmt: skip
    record(db, user, "warranty.replace", "warranty_registration", r.id,
           f"{r.number}: {new.serial} replaces {old.serial}, covered to {r.expiry_date} ({rule.replace('_', ' ')})")  # fmt: skip
    record(
        db,
        user,
        "warranty.end",
        "warranty_registration",
        orig.id,
        f"{orig.number} ended: {old.serial} replaced by {new.serial}",
    )
    return r


# ---------- what a battery is covered by ----------


def warranty_of(db: Session, serial_id) -> dict:
    """The battery's current warranty, in plain states: none, pending, active, expired."""
    r = db.scalar(select(WarrantyRegistration).where(WarrantyRegistration.serial_id == serial_id,
                                                     WarrantyRegistration.status != "rejected")
                  .order_by(WarrantyRegistration.created_at.desc()).limit(1))  # fmt: skip
    if r is None:
        return {"state": "none"}
    today = date.today()
    state = {"pending": "pending", "correction_required": "pending", "ended": "ended"}.get(r.status)
    if r.status == "approved":
        state = (
            "expired"
            if r.expiry_date < today
            else "not_started"
            if r.start_date > today
            else "active"
        )
    return {"state": state, "registration": {"id": r.id, "number": r.number}, "start_date": r.start_date,
            "expiry_date": r.expiry_date}  # fmt: skip
