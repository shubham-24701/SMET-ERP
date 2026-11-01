"""Phase 6 after-sales: maintenance contracts (AMC) and service requests.

Warranty and AMC are separate: each has its own check (`warranty_of`, `amc_of`) and neither is
inferred from the other. An AMC's planned visits are ordinary site visits of stage "maintenance";
a service request's visits are of stage "service", so technicians record both the way they record
an installation. Service requests move only along the allowed transitions; every change keeps who,
when and why. A replaced battery's warranty follows the original's policy when the request closes.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import any_, func, or_, select
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, require
from app.files import store
from app.installation import _check_plan, technicians_q, visit_brief
from app.inventory import (
    Key,
    Lot,
    NeededReason,
    Serials,
    active_location,
    check_free,
    claim_key,
    conflict,
    label,
    lock_items,
    post,
    remember,
    serials_at,
    whole,
)
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, rules
from app.models import (
    AmcContract,
    BankPosition,
    BatteryBank,
    Item,
    Location,
    MaintenanceAlert,
    Party,
    Project,
    Serial,
    ServiceRequest,
    Site,
    SiteVisit,
    StoredFile,
    User,
    utcnow,
)
from app.projects import _names
from app.sales import party_ref
from app.warranty import add_months, carry_to_replacement, warranty_of

router = APIRouter(tags=["after-sales"], route_class=NumbersRoute)
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
SerialNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
AT_CUSTOMER = ("delivered", "installed", "commissioned", "in_service", "removed")


def _sites_of_customer(db: Session, customer_id):
    return (
        select(Site.id)
        .join(Project, Project.id == Site.project_id)
        .where(Project.customer_id == customer_id)
    )


def current_bank(db: Session, serial_id) -> BatteryBank | None:
    return db.scalar(select(BatteryBank).join(BankPosition, BankPosition.bank_id == BatteryBank.id)
                     .where(BankPosition.serial_id == serial_id, BankPosition.ended_at.is_(None)))  # fmt: skip


# ======================= AMC =======================


def amc_of(db: Session, s: Serial, on: date | None = None) -> list[dict]:
    """Contracts covering this battery on a day, worked out from each contract's scope. A renewed
    contract still covers until its own end."""
    on = on or date.today()
    if (
        s.status not in AT_CUSTOMER
    ):  # in the workshop, in stock, scrapped: no contract covers it there
        return []
    bank = current_bank(db, s.id)
    conds = [(AmcContract.scope == "batteries") & (any_(AmcContract.serial_ids) == s.id)]
    if s.party_id:
        conds.append((AmcContract.scope == "customer") & (AmcContract.customer_id == s.party_id))
    if s.site_id:
        conds.append((AmcContract.scope == "site") & (AmcContract.site_id == s.site_id))
    if bank:
        conds.append((AmcContract.scope == "bank") & (any_(AmcContract.bank_ids) == bank.id))
    rows = db.scalars(select(AmcContract).where(AmcContract.status.in_(("active", "renewed")), AmcContract.starts_on <= on,
                                                AmcContract.ends_on >= on, or_(*conds)).order_by(AmcContract.ends_on)).all()  # fmt: skip
    return [{"id": c.id, "number": c.number, "starts_on": c.starts_on, "ends_on": c.ends_on, "scope": c.scope,
             "covered": c.covered, "excluded": c.excluded} for c in rows]  # fmt: skip


def coverage(db: Session, c: AmcContract) -> list[tuple[Serial, Item, BatteryBank | None]]:
    """The batteries the contract covers now (up to 500 shown)."""
    q = (
        select(Serial, Item)
        .join(Item, Item.id == Serial.item_id)
        .where(Item.kind.in_(("battery_model", "battery_unit")))
    )
    if c.scope == "customer":
        q = q.where(Serial.party_id == c.customer_id, Serial.status.in_(AT_CUSTOMER))
    elif c.scope == "site":
        q = q.where(Serial.site_id == c.site_id, Serial.status.in_(AT_CUSTOMER))
    elif c.scope == "bank":
        q = q.join(BankPosition, BankPosition.serial_id == Serial.id).where(BankPosition.bank_id.in_(c.bank_ids),
                                                                          BankPosition.ended_at.is_(None))  # fmt: skip
    else:
        q = q.where(Serial.id.in_(c.serial_ids))
    rows = db.execute(q.order_by(Serial.serial).limit(500)).all()
    return [(s, i, current_bank(db, s.id)) for s, i in rows]


class AmcIn(BaseModel):
    customer_id: uuid.UUID
    scope: Literal["customer", "site", "bank", "batteries"]
    site_id: uuid.UUID | None = None
    bank_ids: list[uuid.UUID] = Field(default=[], max_length=50)
    serials: list[SerialNo] = Field(default=[], max_length=500)
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    starts_on: date
    ends_on: date
    visit_every_months: int = Field(ge=1, le=24)
    service_scope: Text = ""
    covered: list[Line] = Field(default=[], max_length=30)
    excluded: list[Line] = Field(default=[], max_length=30)


class AmcPut(AmcIn):
    version: int


def _amc_targets(db: Session, body: AmcIn) -> dict:
    cust = db.get(Party, body.customer_id)
    if cust is None or "customer" not in cust.kinds:
        raise field_error("customer_id", "Choose a customer")
    if body.ends_on <= body.starts_on:
        raise field_error("ends_on", "The contract ends after it starts")
    theirs = set(db.scalars(_sites_of_customer(db, cust.id)))
    out = {
        "customer_id": cust.id,
        "scope": body.scope,
        "site_id": None,
        "bank_ids": [],
        "serial_ids": [],
    }
    if body.scope == "site":
        if body.site_id not in theirs:
            raise field_error("site_id", "Choose a site of this customer's projects")
        out["site_id"] = body.site_id
    elif body.scope == "bank":
        banks = db.scalars(select(BatteryBank).where(BatteryBank.id.in_(body.bank_ids))).all()
        if (
            not body.bank_ids
            or len(banks) != len(set(body.bank_ids))
            or any(b.site_id not in theirs for b in banks)
        ):
            raise field_error("bank_ids", "Choose banks at this customer's sites")
        out["bank_ids"] = list(dict.fromkeys(body.bank_ids))
    elif body.scope == "batteries":
        names = list(dict.fromkeys(x.upper() for x in body.serials))
        found = db.scalars(
            select(Serial).where(Serial.serial.in_(names), Serial.party_id == cust.id)
        ).all()
        if not names or len(found) != len(names):
            missing = sorted(set(names) - {s.serial for s in found})
            raise field_error(
                "serials",
                f"Not this customer's batteries: {', '.join(missing[:10])}"
                if names
                else "List the batteries",
            )
        out["serial_ids"] = [s.id for s in found]
    return out


def amc_row(db: Session, c: AmcContract, full: bool = False) -> dict:
    today = date.today()
    shown = "expired" if c.status == "active" and c.ends_on < today else c.status
    row = {"id": c.id, "number": c.number, "customer": party_ref(db.get(Party, c.customer_id)), "scope": c.scope,
           "reference": c.reference, "starts_on": c.starts_on, "ends_on": c.ends_on, "status": shown,
           "visit_every_months": c.visit_every_months, "version": c.version}  # fmt: skip
    if full:
        site = db.get(Site, c.site_id) if c.site_id else None
        banks = db.scalars(select(BatteryBank).where(BatteryBank.id.in_(c.bank_ids))).all()
        visits = db.scalars(
            select(SiteVisit).where(SiteVisit.amc_id == c.id).order_by(SiteVisit.scheduled_on)
        ).all()
        names = _names(db, [t for v in visits for t in v.technician_ids])
        sites = {
            x.id: x.name
            for x in db.scalars(select(Site).where(Site.id.in_({v.site_id for v in visits})))
        }
        nxt = db.scalar(select(AmcContract).where(AmcContract.renewed_from_id == c.id))
        prev = db.get(AmcContract, c.renewed_from_id) if c.renewed_from_id else None
        row |= {"site": {"id": site.id, "name": site.name} if site else None,
                "banks": [{"id": b.id, "number": b.number, "name": b.name} for b in banks],
                "service_scope": c.service_scope, "covered": c.covered, "excluded": c.excluded,
                "cancel_reason": c.cancel_reason,
                "batteries": [{"id": s.id, "serial": s.serial, "item": item_ref(i), "status": s.status,
                               "bank": {"id": b.id, "number": b.number} if b else None} for s, i, b in coverage(db, c)],
                "visits": [visit_brief(db, v, names) | {"site": {"id": v.site_id, "name": sites.get(v.site_id)}} for v in visits],
                "renewed_from": {"id": prev.id, "number": prev.number} if prev else None,
                "renewed_by": {"id": nxt.id, "number": nxt.number} if nxt else None}  # fmt: skip
    return row


@router.get("/amc")
def list_amc(
    _: require("amc.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status")] = None,
    expiring: bool = False,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(AmcContract).join(Party, Party.id == AmcContract.customer_id)
    if status_:
        query = query.where(AmcContract.status.in_(status_.split(",")))
    if expiring:  # active and ending within the renewal notice, not yet renewed
        query = query.where(AmcContract.status == "active", AmcContract.ends_on >= date.today(),
                            AmcContract.ends_on <= date.today() + timedelta(days=int(rules(db)["amc_renewal_notice_days"])))  # fmt: skip
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                AmcContract.number.ilike(like),
                AmcContract.reference.ilike(like),
                Party.name.ilike(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(AmcContract.ends_on).limit(limit).offset(offset)).all()
    return {"items": [amc_row(db, c) for c in rows], "total": total}


@router.get("/amc/reminders")
def amc_reminders(_: require("amc.view"), db: DB) -> dict:
    """What needs chasing: maintenance visits in the next 14 days, overdue ones, and contracts to renew."""
    today = date.today()
    open_ = SiteVisit.status.in_(("scheduled", "started"))
    up = db.scalars(select(SiteVisit).where(SiteVisit.amc_id.is_not(None), open_, SiteVisit.scheduled_on >= today,
                                            SiteVisit.scheduled_on <= today + timedelta(days=14)).order_by(SiteVisit.scheduled_on)).all()  # fmt: skip
    late = db.scalars(select(SiteVisit).where(SiteVisit.amc_id.is_not(None), open_, SiteVisit.scheduled_on < today)
                      .order_by(SiteVisit.scheduled_on)).all()  # fmt: skip
    days = int(rules(db)["amc_renewal_notice_days"])
    ending = db.scalars(select(AmcContract).where(AmcContract.status == "active", AmcContract.ends_on >= today,
                                                  AmcContract.ends_on <= today + timedelta(days=days)).order_by(AmcContract.ends_on)).all()  # fmt: skip
    names = _names(db, [t for v in [*up, *late] for t in v.technician_ids])
    return {"upcoming": [visit_brief(db, v, names) for v in up], "overdue": [visit_brief(db, v, names) for v in late],
            "expiring": [amc_row(db, c) for c in ending], "renewal_notice_days": days}  # fmt: skip


@router.get("/amc/{amc_id}")
def get_amc(amc_id: uuid.UUID, _: require("amc.view"), db: DB) -> dict:
    return amc_row(db, get_or_404(db, AmcContract, amc_id, "Contract"), full=True)


@router.post("/amc", status_code=status.HTTP_201_CREATED)
def create_amc(body: AmcIn, p: require("amc.create"), db: DB) -> dict:
    c = AmcContract(number=next_number(db, "amc"), created_by=p.user.id, **_amc_targets(db, body),
                    **body.model_dump(include={"reference", "starts_on", "ends_on", "visit_every_months",
                                               "service_scope", "covered", "excluded"}))  # fmt: skip
    db.add(c)
    db.flush()
    record(
        db,
        p.user,
        "amc.create",
        "amc_contract",
        c.id,
        f"{c.number}: draft for {db.get(Party, c.customer_id).name}",
    )
    db.commit()
    return amc_row(db, c, full=True)


def _amc(db: Session, amc_id, version: int, *statuses: str) -> AmcContract:
    c = get_or_404(db, AmcContract, amc_id, "Contract", lock=True)
    if c.version != version:
        raise stale()
    if c.status not in statuses:
        raise conflict(f"{c.number} is {c.status}")
    return c


@router.put("/amc/{amc_id}")
def update_amc(amc_id: uuid.UUID, body: AmcPut, p: require("amc.create"), db: DB) -> dict:
    c = _amc(db, amc_id, body.version, "draft")
    data = _amc_targets(db, body) | body.model_dump(include={"reference", "starts_on", "ends_on", "visit_every_months",
                                                             "service_scope", "covered", "excluded"})  # fmt: skip
    changes = {k: [str(getattr(c, k)), str(v)] for k, v in data.items() if getattr(c, k) != v}
    for k, v in data.items():
        setattr(c, k, v)
    c.version += 1
    record(db, p.user, "amc.update", "amc_contract", c.id, f"{c.number} changed", changes)
    db.commit()
    return amc_row(db, c, full=True)


class Ver(BaseModel):
    version: int


@router.post("/amc/{amc_id}/activate")
def activate_amc(amc_id: uuid.UUID, body: Ver, p: require("amc.create"), db: DB) -> dict:
    """Plans one maintenance visit per covered site every `visit_every_months` from the start
    (the first one interval after it) up to the end. Technicians are assigned on each visit."""
    c = _amc(db, amc_id, body.version, "draft")
    by_site: dict = {}
    for _, _, b in coverage(db, c):
        if b:
            by_site.setdefault(b.site_id, set()).add(b.id)
    if c.scope == "site":
        by_site.setdefault(c.site_id, set())
    if not by_site:
        raise conflict(
            "No battery of this contract is in a bank at a site yet, so no visit can be planned"
        )
    dates = []
    k = 1
    while (d := add_months(c.starts_on, k * c.visit_every_months)) <= c.ends_on and k <= 60:
        dates.append(d)
        k += 1
    for site_id, banks in by_site.items():
        for d in dates:
            db.add(SiteVisit(number=next_number(db, "site_visit"), site_id=site_id, stage="maintenance", amc_id=c.id,
                             bank_ids=sorted(banks), scheduled_on=d, created_by=p.user.id))  # fmt: skip
    c.status, c.version = "active", c.version + 1
    record(db, p.user, "amc.activate", "amc_contract", c.id,
           f"{c.number} active {c.starts_on} to {c.ends_on}: {len(dates) * len(by_site)} maintenance visits planned")  # fmt: skip
    db.commit()
    return amc_row(db, c, full=True)


class CancelIn(BaseModel):
    version: int
    reason: NeededReason


@router.post("/amc/{amc_id}/cancel")
def cancel_amc(amc_id: uuid.UUID, body: CancelIn, p: require("amc.create"), db: DB) -> dict:
    """Ends the contract now: visits still to come are cancelled; done ones stay."""
    c = _amc(db, amc_id, body.version, "draft", "active")
    n = 0
    for v in db.scalars(
        select(SiteVisit).where(
            SiteVisit.amc_id == c.id, SiteVisit.status.in_(("scheduled", "started"))
        )
    ):
        v.status, v.cancel_reason, v.version = (
            "cancelled",
            f"{c.number} cancelled: {body.reason}"[:500],
            v.version + 1,
        )
        n += 1
    c.status, c.cancel_reason, c.version = "cancelled", body.reason, c.version + 1
    record(
        db,
        p.user,
        "amc.cancel",
        "amc_contract",
        c.id,
        f"{c.number} cancelled; {n} planned visits cancelled",
        reason=body.reason,
    )
    prev = (
        db.get(AmcContract, c.renewed_from_id, with_for_update=True) if c.renewed_from_id else None
    )
    if (
        prev is not None and prev.status == "renewed"
    ):  # the renewal didn't happen: the old one runs on
        prev.status, prev.version = "active", prev.version + 1
        record(
            db,
            p.user,
            "amc.renewal_cancelled",
            "amc_contract",
            prev.id,
            f"{prev.number} active again: renewal {c.number} cancelled",
        )
    db.commit()
    return amc_row(db, c, full=True)


class RenewIn(BaseModel):
    version: int
    starts_on: date
    ends_on: date
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] | None = None
    visit_every_months: int | None = Field(default=None, ge=1, le=24)


@router.post("/amc/{amc_id}/renew", status_code=status.HTTP_201_CREATED)
def renew_amc(amc_id: uuid.UUID, body: RenewIn, p: require("amc.create"), db: DB) -> dict:
    """A new draft contract with the same scope and terms; the old one keeps its visits and covers
    until its own end."""
    c = _amc(db, amc_id, body.version, "active")
    if body.ends_on <= body.starts_on:
        raise field_error("ends_on", "The contract ends after it starts")
    n = AmcContract(number=next_number(db, "amc"), customer_id=c.customer_id, scope=c.scope, site_id=c.site_id,
                    bank_ids=c.bank_ids, serial_ids=c.serial_ids, reference=body.reference if body.reference is not None else c.reference,
                    starts_on=body.starts_on, ends_on=body.ends_on, visit_every_months=body.visit_every_months or c.visit_every_months,
                    service_scope=c.service_scope, covered=c.covered, excluded=c.excluded, renewed_from_id=c.id,
                    created_by=p.user.id)  # fmt: skip
    db.add(n)
    c.status, c.version = "renewed", c.version + 1
    db.flush()
    record(db, p.user, "amc.renew", "amc_contract", c.id, f"{c.number} renewed as {n.number}")
    db.commit()
    return amc_row(db, n, full=True)


# ======================= service requests =======================

OPEN_SR = ("new", "under_review", "scheduled", "in_progress", "pending_action")
ALLOWED = {
    "new": {"under_review", "scheduled"},
    "under_review": {"scheduled", "in_progress", "pending_action"},
    "scheduled": {"in_progress", "pending_action", "under_review"},
    "in_progress": {"pending_action", "scheduled"},
    "pending_action": {"under_review", "scheduled", "in_progress"},
}  # closing has its own action, from any open status


def _move(sr: ServiceRequest, user: User | None, to: str, reason: str = "") -> None:
    sr.history = [*sr.history, {"from": sr.status, "to": to, "by": user.name if user else "Public form",
                                "at": utcnow().isoformat(), "reason": reason}]  # fmt: skip
    sr.status = to


def sr_row(db: Session, sr: ServiceRequest, full: bool = False) -> dict:
    s = db.get(Serial, sr.serial_id) if sr.serial_id else None
    site = db.get(Site, sr.site_id) if sr.site_id else None
    names = _names(db, [sr.coordinator_id])
    row = {"id": sr.id, "number": sr.number, "source": sr.source, "status": sr.status, "priority": sr.priority,
           "serial": {"id": s.id, "serial": s.serial} if s else None, "reported_serial": sr.reported_serial,
           "site": {"id": site.id, "name": site.name} if site else None,
           "customer": party_ref(db.get(Party, sr.customer_id)) if sr.customer_id else None,
           "contact_name": sr.contact_name, "issue": sr.issue, "coordinator_id": sr.coordinator_id,
           "coordinator": names.get(sr.coordinator_id), "created_at": sr.created_at, "version": sr.version}  # fmt: skip
    if full:
        bank = db.get(BatteryBank, sr.bank_id) if sr.bank_id else None
        visits = db.scalars(
            select(SiteVisit)
            .where(SiteVisit.service_request_id == sr.id)
            .order_by(SiteVisit.scheduled_on)
        ).all()
        vnames = _names(db, [t for v in visits for t in v.technician_ids])
        files = db.scalars(select(StoredFile).where(StoredFile.owner_type == "service", StoredFile.owner_id == sr.id)
                           .order_by(StoredFile.uploaded_at)).all()  # fmt: skip
        fnames = _names(db, [f.uploaded_by for f in files])
        alerts = db.scalars(
            select(MaintenanceAlert).where(MaintenanceAlert.service_request_id == sr.id)
        ).all()
        repl = db.get(Serial, sr.replacement_serial_id) if sr.replacement_serial_id else None
        loc = db.get(Location, s.location_id) if s and s.location_id else None
        row |= {"contact_phone": sr.contact_phone, "preferred_visit": sr.preferred_visit,
                "bank": {"id": bank.id, "number": bank.number, "name": bank.name, "status": bank.status} if bank else None,
                "battery": {"status": s.status, "item": item_ref(db.get(Item, s.item_id)), "location": label(db, loc) if loc else None,
                            "bank": (lambda b: {"id": b.id, "number": b.number} if b else None)(current_bank(db, s.id))} if s else None,
                # Two separate answers: warranty and AMC are never inferred from each other.
                "warranty": warranty_of(db, s.id) if s else {"state": "unknown"},
                "amc": amc_of(db, s) if s else [],
                "history": sr.history, "diagnosis": sr.diagnosis, "work_done": sr.work_done, "parts": sr.parts,
                "resolution": sr.resolution, "replacement": {"id": repl.id, "serial": repl.serial, "status": repl.status} if repl else None,
                "acknowledgement": sr.acknowledgement, "closure_notes": sr.closure_notes,
                "visits": [visit_brief(db, v, vnames) for v in visits],
                "alerts": [{"id": a.id, "number": a.number, "status": a.status} for a in alerts],
                "files": [{"id": f.id, "kind": f.kind, "name": f.name, "content_type": f.content_type, "caption": f.caption,
                           "uploaded_by": fnames.get(f.uploaded_by) or "Public form", "uploaded_at": f.uploaded_at} for f in files]}  # fmt: skip
    return row


@router.get("/service-requests")
def list_srs(
    p: require("service.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status")] = None,
    priority: str | None = None,
    mine: bool = False,
    serial_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(ServiceRequest).outerjoin(Serial, Serial.id == ServiceRequest.serial_id)
    if status_:
        query = query.where(ServiceRequest.status.in_(status_.split(",")))
    if priority:
        query = query.where(ServiceRequest.priority.in_(priority.split(",")))
    if mine:
        query = query.where(ServiceRequest.coordinator_id == p.user.id)
    if serial_id:
        query = query.where(
            or_(
                ServiceRequest.serial_id == serial_id,
                ServiceRequest.replacement_serial_id == serial_id,
            )
        )
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(ServiceRequest.number.ilike(like), Serial.serial.ilike(like), ServiceRequest.reported_serial.ilike(like),
                                ServiceRequest.contact_name.ilike(like), ServiceRequest.issue.ilike(like)))  # fmt: skip
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    rows = db.scalars(
        query.order_by(ServiceRequest.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {
        "items": [
            sr_row(db, x)
            for x in sorted(rows, key=lambda x: (x.status == "closed", order[x.priority]))
        ],
        "total": total,
    }


class SrIn(BaseModel):
    serial: SerialNo | None = None
    bank_id: uuid.UUID | None = None
    site_id: uuid.UUID | None = None
    contact_name: Short = ""
    contact_phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    issue: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=4000)]
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    preferred_visit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


def _target(db: Session, serial: str | None, bank_id, site_id) -> dict:
    """Serial, bank, site and customer, each filled in from the most specific one given."""
    s = bank = site = None
    if serial:
        s = db.scalar(select(Serial).where(Serial.serial == serial.upper()))
        if s is None:
            raise field_error("serial", "No battery with that serial number")
        bank = current_bank(db, s.id)
    if bank_id:
        bank = get_or_404(db, BatteryBank, bank_id, "Battery bank")
    if site_id:
        site = get_or_404(db, Site, site_id, "Site")
    site = site or (
        db.get(Site, bank.site_id) if bank else db.get(Site, s.site_id) if s and s.site_id else None
    )
    if not (s or bank or site):
        raise field_error("serial", "Name the battery, the bank or the site")
    customer = db.get(Project, site.project_id).customer_id if site else (s.party_id if s else None)
    return {"serial_id": s.id if s else None, "bank_id": bank.id if bank else None, "site_id": site.id if site else None,
            "customer_id": customer}  # fmt: skip


@router.post("/service-requests", status_code=status.HTTP_201_CREATED)
def open_sr(body: SrIn, p: require("service.create"), db: DB) -> dict:
    sr = ServiceRequest(number=next_number(db, "service_request"), source="internal", created_by=p.user.id,
                        **_target(db, body.serial, body.bank_id, body.site_id),
                        **body.model_dump(exclude={"serial", "bank_id", "site_id"}))  # fmt: skip
    sr.reported_serial = (body.serial or "").upper()
    db.add(sr)
    db.flush()
    record(
        db, p.user, "service.open", "service_request", sr.id, f"{sr.number} opened ({sr.priority})"
    )
    db.commit()
    return sr_row(db, sr, full=True)


@router.get("/service-requests/{sr_id}")
def get_sr(sr_id: uuid.UUID, _: require("service.view"), db: DB) -> dict:
    return sr_row(db, get_or_404(db, ServiceRequest, sr_id, "Service request"), full=True)


def _open_sr(db: Session, sr_id, version: int) -> ServiceRequest:
    sr = get_or_404(db, ServiceRequest, sr_id, "Service request", lock=True)
    if sr.version != version:
        raise stale()
    if sr.status == "closed":
        raise conflict(f"{sr.number} is closed")
    return sr


class SrPatch(BaseModel):
    version: int
    serial: SerialNo | None = None  # fills in a request opened without a known battery
    coordinator_id: uuid.UUID | None = None
    priority: Literal["low", "normal", "high", "urgent"] | None = None
    contact_name: Short | None = None
    contact_phone: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None
    ) = None
    preferred_visit: (
        Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None
    ) = None
    diagnosis: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = (
        None
    )
    work_done: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)] | None = (
        None
    )


@router.patch("/service-requests/{sr_id}")
def update_sr(sr_id: uuid.UUID, body: SrPatch, p: require("service.create"), db: DB) -> dict:
    sr = _open_sr(db, sr_id, body.version)
    data = {
        k: getattr(body, k)
        for k in body.model_fields_set - {"version", "serial"}
        if getattr(body, k) is not None
    }
    if body.serial:
        if sr.serial_id:
            raise field_error("serial", "This request already names its battery")
        data |= {k: v for k, v in _target(db, body.serial, None, None).items() if v is not None}
    if "coordinator_id" in data:
        u = db.get(User, data["coordinator_id"])
        if u is None or not u.active:
            raise field_error("coordinator_id", "Choose an active person")
    changes = {k: [str(getattr(sr, k)), str(v)] for k, v in data.items() if getattr(sr, k) != v}
    for k, v in data.items():
        setattr(sr, k, v)
    sr.version += 1
    record(db, p.user, "service.update", "service_request", sr.id, f"{sr.number} updated", changes)
    db.commit()
    return sr_row(db, sr, full=True)


class StatusIn(BaseModel):
    version: int
    to: Literal["under_review", "scheduled", "in_progress", "pending_action"]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


@router.post("/service-requests/{sr_id}/status")
def move_sr(sr_id: uuid.UUID, body: StatusIn, p: require("service.create"), db: DB) -> dict:
    sr = _open_sr(db, sr_id, body.version)
    if body.to not in ALLOWED[sr.status]:
        raise conflict(
            f"A request that is {sr.status.replace('_', ' ')} can't go to {body.to.replace('_', ' ')}"
        )
    if body.to == "pending_action" and len(body.reason) < 3:
        raise field_error("reason", "Say what it is waiting for")
    _move(sr, p.user, body.to, body.reason)
    sr.version += 1
    record(
        db,
        p.user,
        "service.status",
        "service_request",
        sr.id,
        f"{sr.number} now {body.to.replace('_', ' ')}",
        reason=body.reason,
    )
    db.commit()
    return sr_row(db, sr, full=True)


class SrVisitIn(BaseModel):
    version: int
    scheduled_on: date
    time_window: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    technician_ids: list[uuid.UUID] = Field(default=[], max_length=20)


@router.post("/service-requests/{sr_id}/visit", status_code=status.HTTP_201_CREATED)
def schedule_sr_visit(
    sr_id: uuid.UUID, body: SrVisitIn, p: require("service.create", "installation.create"), db: DB
) -> dict:
    sr = _open_sr(db, sr_id, body.version)
    if not sr.site_id:
        raise conflict(
            "This request has no site: name the battery or site first, or receive the battery at the workshop"
        )
    banks = [sr.bank_id] if sr.bank_id else []
    _check_plan(db, sr.site_id, "service", banks, body.technician_ids)
    v = SiteVisit(number=next_number(db, "site_visit"), site_id=sr.site_id, stage="service", service_request_id=sr.id,
                  bank_ids=banks, scheduled_on=body.scheduled_on, time_window=body.time_window,
                  technician_ids=body.technician_ids, contacts=[{"name": sr.contact_name, "role": "", "phone": sr.contact_phone}] if sr.contact_name else [],
                  access_notes=sr.preferred_visit, created_by=p.user.id)  # fmt: skip
    db.add(v)
    db.flush()
    if sr.status in ("new", "under_review", "pending_action"):
        _move(sr, p.user, "scheduled", f"Visit {v.number} on {v.scheduled_on}")
    sr.version += 1
    record(
        db,
        p.user,
        "service.visit",
        "service_request",
        sr.id,
        f"{sr.number}: visit {v.number} on {v.scheduled_on}",
    )
    db.commit()
    return sr_row(db, sr, full=True)


class KeyIn(BaseModel):
    version: int
    key: Key


class ReceiveIn(KeyIn):
    location_id: uuid.UUID


@router.post("/service-requests/{sr_id}/receive")
def receive_at_workshop(
    sr_id: uuid.UUID, body: ReceiveIn, p: require("service.create", "stock.create"), db: DB
) -> dict:
    """The battery comes back to SMET's workshop: into a repair location, out of the customer's
    hands for now. It must be out of its bank first (removed or replaced on a visit)."""
    if (prev := claim_key(db, body.key, "service_receive")) is not None:
        return prev
    sr = _open_sr(db, sr_id, body.version)
    if not sr.serial_id:
        raise conflict("Name the battery first")
    s = db.get(Serial, sr.serial_id, with_for_update=True)
    if current_bank(db, s.id):
        raise conflict(
            f"{s.serial} is still in a bank: remove or replace it on the service visit first"
        )
    if s.status not in AT_CUSTOMER:
        raise conflict(f"{s.serial} is {s.status.replace('_', ' ')}, not at the customer")
    loc = active_location(db, body.location_id)
    if loc.purpose != "repair":
        raise field_error("location_id", "Receive it into a repair location")
    item = lock_items(db, [s.item_id])[s.item_id]
    post(
        db,
        p.user,
        item=item,
        qty=Decimal(1),
        src=None,
        dst=loc,
        kind="service_receipt",
        doc=("service_request", sr.id, sr.number),
        serial=s,
    )
    s.status = "in_repair"
    sr.version += 1
    record(
        db,
        p.user,
        "service.receive",
        "service_request",
        sr.id,
        f"{sr.number}: {s.serial} received at {label(db, loc)}",
    )
    out = remember(db, body.key, sr_row(db, sr, full=True))
    db.commit()
    return out


@router.post("/service-requests/{sr_id}/return-battery")
def return_to_customer(
    sr_id: uuid.UUID, body: KeyIn, p: require("service.create", "stock.create"), db: DB
) -> dict:
    """The repaired (or checked) battery goes back to the customer's site, out of the bank until a
    technician places it on a visit."""
    if (prev := claim_key(db, body.key, "service_return")) is not None:
        return prev
    sr = _open_sr(db, sr_id, body.version)
    s = db.get(Serial, sr.serial_id, with_for_update=True) if sr.serial_id else None
    if s is None or s.status != "in_repair" or not s.location_id:
        raise conflict("The battery is not at the workshop")
    item = lock_items(db, [s.item_id])[s.item_id]
    post(db, p.user, item=item, qty=Decimal(1), src=db.get(Location, s.location_id), dst=None, kind="service_return",
         doc=("service_request", sr.id, sr.number), serial=s)  # fmt: skip
    s.status = "delivered"
    sr.version += 1
    record(
        db,
        p.user,
        "service.return",
        "service_request",
        sr.id,
        f"{sr.number}: {s.serial} returned to the customer",
    )
    out = remember(db, body.key, sr_row(db, sr, full=True))
    db.commit()
    return out


class PartIn(KeyIn):
    item_id: uuid.UUID
    qty: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)]
    location_id: uuid.UUID
    lot: Lot = ""
    serials: Serials = []


@router.post("/service-requests/{sr_id}/parts")
def use_part(
    sr_id: uuid.UUID, body: PartIn, p: require("service.create", "stock.create"), db: DB
) -> dict:
    """A part used on the job leaves available stock against the request."""
    if (prev := claim_key(db, body.key, "service_part")) is not None:
        return prev
    sr = _open_sr(db, sr_id, body.version)
    item = lock_items(db, [body.item_id])[body.item_id]
    loc = active_location(db, body.location_id)
    if loc.purpose != "available":
        raise field_error("location_id", "Parts come from available stock")
    whole(item, body.qty, body.serials)
    check_free(db, item, body.qty)
    doc = ("service_request", sr.id, sr.number)
    if item.tracking == "serial":
        for s in serials_at(db, item, body.serials, loc, ("available",)):
            post(
                db,
                p.user,
                item=item,
                qty=Decimal(1),
                src=loc,
                dst=None,
                kind="service_use",
                doc=doc,
                serial=s,
            )
            s.status = "consumed"
    else:
        post(
            db,
            p.user,
            item=item,
            qty=body.qty,
            src=loc,
            dst=None,
            kind="service_use",
            doc=doc,
            lot=body.lot,
        )
    sr.parts = [*sr.parts, {"item": item.code, "name": item.name, "qty": str(body.qty), "unit": item.unit_code,
                            "lot": body.lot, "serials": body.serials, "by": p.user.name, "on": date.today().isoformat()}]  # fmt: skip
    sr.version += 1
    record(
        db,
        p.user,
        "service.part",
        "service_request",
        sr.id,
        f"{sr.number}: {body.qty.normalize():f} {item.code} used",
    )
    out = remember(db, body.key, sr_row(db, sr, full=True))
    db.commit()
    return out


class ReplacementIn(KeyIn):
    serial: SerialNo
    location_id: uuid.UUID


@router.post("/service-requests/{sr_id}/send-replacement")
def send_replacement(
    sr_id: uuid.UUID, body: ReplacementIn, p: require("service.create", "stock.create"), db: DB
) -> dict:
    """A replacement battery leaves available stock for the original's site; a technician then puts
    it in the original's place on the service visit."""
    if (prev := claim_key(db, body.key, "service_replacement")) is not None:
        return prev
    sr = _open_sr(db, sr_id, body.version)
    orig = db.get(Serial, sr.serial_id) if sr.serial_id else None
    if orig is None:
        raise conflict("Name the battery being replaced first")
    if sr.replacement_serial_id:
        raise conflict("A replacement has already been sent for this request")
    loc = active_location(db, body.location_id)
    new = db.scalar(select(Serial).where(Serial.serial == body.serial.upper()))
    if new is None:
        raise field_error("serial", "No battery with that serial number")
    item = lock_items(db, [new.item_id])[new.item_id]
    if item.kind not in ("battery_model", "battery_unit"):
        raise field_error("serial", f"{new.serial} is not a battery")
    check_free(db, item, Decimal(1))
    (new,) = serials_at(db, item, [new.serial], loc, ("available",))
    post(db, p.user, item=item, qty=Decimal(1), src=loc, dst=None, kind="service_replacement",
         doc=("service_request", sr.id, sr.number), serial=new)  # fmt: skip
    new.status, new.party_id, new.site_id = (
        "delivered",
        orig.party_id or sr.customer_id,
        orig.site_id or sr.site_id,
    )
    sr.replacement_serial_id = new.id
    sr.version += 1
    record(
        db,
        p.user,
        "service.replacement",
        "service_request",
        sr.id,
        f"{sr.number}: {new.serial} sent to replace {orig.serial}",
    )
    out = remember(db, body.key, sr_row(db, sr, full=True))
    db.commit()
    return out


class Ack(BaseModel):
    name: Short = ""
    on: date | None = None


class CloseIn(BaseModel):
    version: int
    resolution: Literal["repaired", "replaced", "returned", "no_fault", "other"]
    closure_notes: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=4000)
    ]
    acknowledgement: Ack = Ack()


@router.post("/service-requests/{sr_id}/close")
def close_sr(sr_id: uuid.UUID, body: CloseIn, p: require("service.create"), db: DB) -> dict:
    sr = _open_sr(db, sr_id, body.version)
    busy = db.scalar(
        select(SiteVisit.number).where(
            SiteVisit.service_request_id == sr.id, SiteVisit.status.in_(("scheduled", "started"))
        )
    )
    if busy:
        raise conflict(f"Visit {busy} is still open: complete or cancel it first")
    alert = db.scalar(
        select(MaintenanceAlert.number).where(
            MaintenanceAlert.service_request_id == sr.id, MaintenanceAlert.status != "closed"
        )
    )
    if alert:
        raise conflict(f"Maintenance alert {alert} is still open")
    here = db.get(Serial, sr.serial_id) if sr.serial_id else None
    if here and here.status == "in_repair" and body.resolution != "replaced":
        raise conflict(f"{here.serial} is still at the workshop: return it to the customer first")
    if sr.replacement_serial_id and body.resolution != "replaced":
        raise conflict("A replacement was sent for this request, so it closes as replaced")
    if body.resolution == "replaced":
        orig = db.get(Serial, sr.serial_id) if sr.serial_id else None
        new = db.get(Serial, sr.replacement_serial_id) if sr.replacement_serial_id else None
        placed = orig and db.scalar(
            select(BankPosition).where(
                BankPosition.replaces_id == orig.id, BankPosition.ended_at.is_(None)
            )
        )
        if new is None and placed:
            new = db.get(Serial, placed.serial_id)
            sr.replacement_serial_id = new.id
        if orig is None or new is None:
            raise conflict(
                "Send the replacement, or replace the battery in its bank on the service visit, first"
            )
        carry_to_replacement(
            db, p.user, orig, new, date.today(), f"{sr.number}: {body.closure_notes}"
        )
        # Contracts listing the original battery cover its replacement from now on.
        for c in db.scalars(select(AmcContract).where(AmcContract.scope == "batteries", any_(AmcContract.serial_ids) == orig.id,
                                                      AmcContract.status.in_(("draft", "active"))).with_for_update()):  # fmt: skip
            if new.id not in c.serial_ids:
                c.serial_ids, c.version = [*c.serial_ids, new.id], c.version + 1
    sr.resolution, sr.closure_notes, sr.acknowledgement = (
        body.resolution,
        body.closure_notes,
        body.acknowledgement.model_dump(mode="json"),
    )
    _move(sr, p.user, "closed", f"{body.resolution.replace('_', ' ')}: {body.closure_notes}"[:500])
    sr.version += 1
    record(db, p.user, "service.close", "service_request", sr.id, f"{sr.number} closed: {body.resolution.replace('_', ' ')}",
           reason=body.closure_notes[:500])  # fmt: skip
    db.commit()
    return sr_row(db, sr, full=True)


@router.post("/service-requests/{sr_id}/files", status_code=status.HTTP_201_CREATED)
def upload_sr_file(
    sr_id: uuid.UUID,
    p: require("service.create"),
    db: DB,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["photo", "test_report", "acknowledgement", "other"], Form()] = "photo",
    caption: Annotated[str, Form(max_length=300)] = "",
) -> dict:
    sr = get_or_404(db, ServiceRequest, sr_id, "Service request")
    if sr.status == "closed":
        raise conflict(f"{sr.number} is closed")
    store(db, p.user, "service", sr.id, file, kind, caption, ("service_request", sr.id, sr.number))
    return sr_row(db, sr, full=True)


@router.get("/service/people")
def service_people(_: require("service.view"), db: DB) -> dict:
    """Coordinators (anyone active) and technicians (those who can record visits)."""
    return {"coordinators": [{"id": i, "name": n} for i, n in db.execute(select(User.id, User.name).where(User.active).order_by(User.name))],
            "technicians": [{"id": i, "name": n} for i, n in db.execute(technicians_q())]}  # fmt: skip
