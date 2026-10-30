"""Phase 5 installation, commissioning and handover.

A battery bank groups batteries at a site. Each bank goes planned → installed → commissioned →
handed over, and each step happens only by completing a visit of that stage: the checks SMET set for
the stage are done, required readings are inside the limits SMET confirmed, the evidence policy is
met, and the dates follow each other. Where each battery sat, and when, is kept as position rows that
are closed, never rewritten.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import any_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, Principal, require
from app.inventory import NeededReason, conflict
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, rules
from app.models import (
    BankPosition,
    BatteryBank,
    Item,
    Milestone,
    Party,
    Project,
    RolePermission,
    Serial,
    Site,
    SiteVisit,
    StageCheck,
    StoredFile,
    User,
    UserRole,
    utcnow,
)
from app.projects import _names
from app.sales import party_ref


def words(status_: str) -> str:
    return status_.replace("_", " ")


router = APIRouter(tags=["installation"], route_class=NumbersRoute)
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Position = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
SerialNo = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Stage = Literal["installation", "commissioning", "handover", "maintenance", "service"]
STAGES = ("installation", "commissioning", "handover")
CHECKED = (*STAGES, "maintenance", "service")  # stages with SMET-set checks and an evidence rule
# stage -> (bank status before, bank status after, date field)
STEP = {
    "installation": ("planned", "installed", "installed_on"),
    "commissioning": ("installed", "commissioned", "commissioned_on"),
    "handover": ("commissioned", "handed_over", "handed_over_on"),
}
ORDER = ["planned", "installed", "commissioned", "handed_over"]
# A battery in a bank takes the bank's stage as its own status.
SERIAL_STATUS = {"planned": "delivered", "installed": "installed",
                 "commissioned": "commissioned", "handed_over": "in_service"}  # fmt: skip
AT_SITE = ("delivered", "installed", "commissioned", "in_service", "removed")
BATTERY_KINDS = ("battery_model", "battery_unit")
PHOTO_RULE = {"installation": "install_photos_min", "commissioning": "commission_photos_min",
              "handover": "handover_photos_min", "maintenance": "maintenance_photos_min",
              "service": "service_photos_min"}  # fmt: skip


# ---------- who may do what ----------


def technicians_q():
    """Active users who hold installation.record through any of their roles."""
    holds = exists().where(
        UserRole.user_id == User.id,
        RolePermission.role_id == UserRole.role_id,
        RolePermission.permission == "installation.record",
    )
    return select(User.id, User.name).where(User.active, holds).order_by(User.name)


def _worker(p: require()) -> Principal:
    if not (p.can("installation.record") or p.can("installation.create")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission for this")
    return p


Worker = Annotated[Principal, Depends(_worker)]  # a technician or a coordinator


def may_work(p: Principal, v: SiteVisit) -> bool:
    """Coordinators record for anyone; a technician only on visits they are assigned to."""
    return p.can("installation.create") or (
        p.can("installation.record") and p.user.id in v.technician_ids
    )


def check_may_work(p: Principal, v: SiteVisit) -> None:
    if not may_work(p, v):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the technicians on this visit can record its work"
        )


# ---------- rows ----------


def site_ref(db: Session, site: Site) -> dict:
    pr = db.get(Project, site.project_id)
    return {"id": site.id, "name": site.name, "address": site.address,
            "project": {"id": pr.id, "number": pr.number, "name": pr.name},
            "customer": party_ref(db.get(Party, pr.customer_id))}  # fmt: skip


def active_positions(db: Session, bank_ids) -> list[tuple[BankPosition, Serial, Item]]:
    return db.execute(
        select(BankPosition, Serial, Item)
        .join(Serial, Serial.id == BankPosition.serial_id)
        .join(Item, Item.id == Serial.item_id)
        .where(BankPosition.bank_id.in_(list(bank_ids)), BankPosition.ended_at.is_(None))
        .order_by(BankPosition.position)
    ).all()


def bank_row(db: Session, b: BatteryBank, full: bool = False) -> dict:
    pos = active_positions(db, [b.id])
    row = {"id": b.id, "number": b.number, "name": b.name, "configuration": b.configuration,
           "status": b.status, "site_id": b.site_id, "installed_on": b.installed_on,
           "commissioned_on": b.commissioned_on, "handed_over_on": b.handed_over_on,
           "batteries": len(pos), "version": b.version}  # fmt: skip
    if full:
        row["site"] = site_ref(db, db.get(Site, b.site_id))
        row["positions"] = [{"id": bp.id, "position": bp.position, "serial_id": s.id,
                             "serial": s.serial, "status": s.status, "item": item_ref(i),
                             "since": bp.started_at} for bp, s, i in pos]  # fmt: skip
        hist = db.execute(
            select(BankPosition, Serial.serial)
            .join(Serial, Serial.id == BankPosition.serial_id)
            .where(BankPosition.bank_id == b.id)
            .order_by(BankPosition.started_at.desc())
            .limit(200)  # shortcut: last 200 placements; a bank holds tens of batteries
        ).all()
        names = _names(db, [x.by_id for x, _ in hist] + [x.ended_by_id for x, _ in hist])
        replaced = _serial_numbers(db, [x.replaces_id for x, _ in hist])
        row["history"] = [{"position": x.position, "serial": sn, "serial_id": x.serial_id,
                           "started_at": x.started_at, "start_reason": x.start_reason,
                           "replaces": replaced.get(x.replaces_id), "by": names.get(x.by_id),
                           "ended_at": x.ended_at, "end_reason": x.end_reason,
                           "ended_by": names.get(x.ended_by_id), "note": x.note} for x, sn in hist]  # fmt: skip
        visits = db.scalars(
            select(SiteVisit)
            .where(any_(SiteVisit.bank_ids) == b.id)
            .order_by(SiteVisit.scheduled_on)
        ).all()
        row["visits"] = [visit_brief(db, v) for v in visits]
    return row


def _serial_numbers(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return (
        dict(db.execute(select(Serial.id, Serial.serial).where(Serial.id.in_(ids))).all())
        if ids
        else {}
    )


def visit_brief(db: Session, v: SiteVisit, names: dict | None = None) -> dict:
    names = names if names is not None else _names(db, v.technician_ids)
    open_issues = sum(1 for i in v.issues if i.get("status") == "open")
    return {"id": v.id, "number": v.number, "stage": v.stage, "status": v.status,
            "scheduled_on": v.scheduled_on, "time_window": v.time_window, "done_on": v.done_on,
            "technicians": [{"id": t, "name": names.get(t)} for t in v.technician_ids],
            "bank_ids": v.bank_ids, "open_issues": open_issues, "version": v.version}  # fmt: skip


def checks_for(db: Session, stage: str) -> list[StageCheck]:
    return db.scalars(
        select(StageCheck).where(StageCheck.stage == stage, StageCheck.active)
        .order_by(StageCheck.sort, StageCheck.text)
    ).all()  # fmt: skip


def check_row(c: StageCheck) -> dict:
    return {"id": c.id, "stage": c.stage, "kind": c.kind, "text": c.text, "unit": c.unit,
            "min_value": c.min_value, "max_value": c.max_value, "confirmed": c.confirmed,
            "required": c.required, "sort": c.sort, "active": c.active, "version": c.version}  # fmt: skip


def file_row(f: StoredFile, names: dict) -> dict:
    return {"id": f.id, "kind": f.kind, "name": f.name, "content_type": f.content_type,
            "size": f.size, "caption": f.caption, "uploaded_by": names.get(f.uploaded_by),
            "uploaded_at": f.uploaded_at}  # fmt: skip


def visit_files(db: Session, v: SiteVisit) -> list[StoredFile]:
    return db.scalars(
        select(StoredFile).where(StoredFile.owner_type == "visit", StoredFile.owner_id == v.id)
        .order_by(StoredFile.uploaded_at)
    ).all()  # fmt: skip


def visit_row(db: Session, v: SiteVisit, p: Principal) -> dict:
    files = visit_files(db, v)
    names = _names(
        db, [*v.technician_ids, v.completed_by, v.created_by, *(f.uploaded_by for f in files)]
    )
    banks = db.scalars(select(BatteryBank).where(BatteryBank.id.in_(v.bank_ids))).all()
    stage = v.stage if v.stage in CHECKED else None
    return visit_brief(db, v, names) | {
        "site": site_ref(db, db.get(Site, v.site_id)),
        "banks": [
            bank_row(db, b)
            | {
                "positions": [
                    {
                        "position": bp.position,
                        "serial": s.serial,
                        "serial_id": s.id,
                        "item": item_ref(i),
                    }
                    for bp, s, i in active_positions(db, [b.id])
                ]
            }
            for b in banks
        ],  # fmt: skip
        "site_ready": v.site_ready,
        "access_notes": v.access_notes,
        "contacts": v.contacts,
        "received_serials": v.received_serials,
        "checklist": v.checklist,
        "readings": v.readings,
        "observations": v.observations,
        "issues": v.issues,
        "acknowledgement": v.acknowledgement,
        "completed_by": names.get(v.completed_by),
        "completed_at": v.completed_at,
        "cancel_reason": v.cancel_reason,
        "created_by": names.get(v.created_by),
        "checks": [check_row(c) for c in checks_for(db, stage)] if stage else [],
        "photos_needed": int(rules(db)[PHOTO_RULE[stage]]) if stage else 0,
        "files": [file_row(f, names) for f in files],
        "can_record": may_work(p, v) and v.status in ("scheduled", "started"),
        "missing": _missing(db, v) if v.status in ("scheduled", "started") else [],
    }


# ---------- stage checks (SMET's checklists and readings) ----------


class CheckIn(BaseModel):
    stage: Stage
    kind: Literal["check", "reading"]
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = ""
    min_value: Annotated[Decimal, Field(max_digits=14, decimal_places=4)] | None = None
    max_value: Annotated[Decimal, Field(max_digits=14, decimal_places=4)] | None = None
    confirmed: bool = False
    required: bool = True
    sort: int = Field(default=0, ge=0, le=9999)
    active: bool = True


class CheckPut(CheckIn):
    version: int


def _limits(body: CheckIn) -> None:
    if body.kind == "check" and (
        body.min_value is not None or body.max_value is not None or body.unit
    ):
        raise field_error("kind", "A checklist line has no unit or limits; make it a reading")
    if (
        body.min_value is not None
        and body.max_value is not None
        and body.min_value > body.max_value
    ):
        raise field_error("min_value", "The lower limit is above the upper limit")
    if body.confirmed and body.min_value is None and body.max_value is None:
        raise field_error("confirmed", "There is no limit to confirm")


@router.get("/stage-checks")
def list_checks(p: require(), db: DB, stage: Stage | None = None) -> dict:
    if not (p.can("installation.view") or p.can("installation.configure")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You don't have permission for this")
    q = select(StageCheck).order_by(StageCheck.stage, StageCheck.sort, StageCheck.text)
    if stage:
        q = q.where(StageCheck.stage == stage)
    return {"items": [check_row(c) for c in db.scalars(q)]}


@router.post("/stage-checks", status_code=status.HTTP_201_CREATED)
def add_check(body: CheckIn, p: require("installation.configure"), db: DB) -> dict:
    _limits(body)
    c = StageCheck(**body.model_dump())
    db.add(c)
    db.flush()
    record(db, p.user, "stage_check.create", "stage_check", c.id,
           f"{c.stage.capitalize()} {c.kind} added: {c.text}")  # fmt: skip
    db.commit()
    return check_row(c)


@router.put("/stage-checks/{check_id}")
def update_check(
    check_id: uuid.UUID, body: CheckPut, p: require("installation.configure"), db: DB
) -> dict:
    _limits(body)
    c = get_or_404(db, StageCheck, check_id, "Check", lock=True)
    if c.version != body.version:
        raise stale()
    before = check_row(c)
    for k, v in body.model_dump(exclude={"version"}).items():
        setattr(c, k, v)
    c.version += 1
    db.flush()
    after = check_row(c)
    record(db, p.user, "stage_check.update", "stage_check", c.id, f"{c.stage.capitalize()} {c.kind} changed: {c.text}",
           {k: [str(before[k]), str(after[k])] for k in CheckIn.model_fields if str(before[k]) != str(after[k])})  # fmt: skip
    db.commit()
    return check_row(c)


# ---------- battery banks ----------


class BankIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    configuration: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class BankPut(BankIn):
    version: int


def _bank_name_free(db: Session, site_id, name: str, not_id=None) -> None:
    q = select(BatteryBank.id).where(
        BatteryBank.site_id == site_id, func.lower(BatteryBank.name) == name.lower()
    )
    if not_id:
        q = q.where(BatteryBank.id != not_id)
    if db.scalar(q):
        raise field_error("name", "This site already has a bank with that name")


@router.get("/sites/{site_id}/banks")
def site_banks(site_id: uuid.UUID, _: require("installation.view"), db: DB) -> dict:
    get_or_404(db, Site, site_id, "Site")
    banks = db.scalars(
        select(BatteryBank).where(BatteryBank.site_id == site_id).order_by(BatteryBank.name)
    )
    return {"items": [bank_row(db, b) for b in banks]}


@router.post("/sites/{site_id}/banks", status_code=status.HTTP_201_CREATED)
def add_bank(site_id: uuid.UUID, body: BankIn, p: require("installation.create"), db: DB) -> dict:
    site = get_or_404(db, Site, site_id, "Site", lock=True)
    _bank_name_free(db, site.id, body.name)
    b = BatteryBank(number=next_number(db, "battery_bank"), site_id=site.id, **body.model_dump())
    db.add(b)
    db.flush()
    record(
        db,
        p.user,
        "bank.create",
        "battery_bank",
        b.id,
        f"{b.number} {b.name} planned at {site.name}",
    )
    db.commit()
    return bank_row(db, b, full=True)


@router.get("/banks/{bank_id}")
def get_bank(bank_id: uuid.UUID, _: require("installation.view"), db: DB) -> dict:
    return bank_row(db, get_or_404(db, BatteryBank, bank_id, "Battery bank"), full=True)


@router.put("/banks/{bank_id}")
def update_bank(
    bank_id: uuid.UUID, body: BankPut, p: require("installation.create"), db: DB
) -> dict:
    b = get_or_404(db, BatteryBank, bank_id, "Battery bank", lock=True)
    if b.version != body.version:
        raise stale()
    get_or_404(db, Site, b.site_id, "Site", lock=True)  # one rename per site at a time
    _bank_name_free(db, b.site_id, body.name, b.id)
    changes = {
        k: [getattr(b, k), v]
        for k, v in body.model_dump(exclude={"version"}).items()
        if getattr(b, k) != v
    }
    for k, (_, v) in changes.items():
        setattr(b, k, v)
    b.version += 1
    record(db, p.user, "bank.update", "battery_bank", b.id, f"{b.number} changed", changes)
    db.commit()
    return bank_row(db, b, full=True)


class Placement(BaseModel):
    serial: SerialNo
    position: Position


class AddIn(BaseModel):
    version: int
    visit_id: uuid.UUID | None = None
    batteries: list[Placement] = Field(min_length=1, max_length=200)


class RemoveIn(BaseModel):
    version: int
    visit_id: uuid.UUID | None = None
    serial: SerialNo
    reason: NeededReason


class ReplaceIn(RemoveIn):
    new_serial: SerialNo


class RelocateIn(RemoveIn):
    to_bank_id: uuid.UUID
    position: Position


def _bank_for_change(
    db: Session, p: Principal, bank_id, version: int, visit_id
) -> tuple[BatteryBank, SiteVisit | None]:
    """The bank, locked. Coordinators change any bank; a technician only during a visit they are on
    that covers this bank."""
    b = get_or_404(db, BatteryBank, bank_id, "Battery bank", lock=True)
    if b.version != version:
        raise stale()
    v = None
    if visit_id:
        v = get_or_404(db, SiteVisit, visit_id, "Visit")
        if b.id not in v.bank_ids:
            raise field_error("visit_id", "This visit doesn't cover this bank")
        if v.status not in ("scheduled", "started"):
            raise conflict(f"Visit {v.number} is {v.status}")
        check_may_work(p, v)
    elif not p.can("installation.create"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Changes to a bank are recorded on a visit you are assigned to",
        )
    return b, v


def _site_battery(db: Session, number: str, site_id, field: str) -> Serial:
    """A battery delivered to this site and not in any bank."""
    s = db.scalar(select(Serial).where(Serial.serial == number).with_for_update())
    if s is None:
        raise field_error(field, f"{number}: no such serial number")
    if db.get(Item, s.item_id).kind not in BATTERY_KINDS:
        raise field_error(field, f"{number} is not a battery")
    if s.site_id != site_id or s.status not in AT_SITE:
        raise field_error(field, f"{number} has not been delivered to this site")
    if db.scalar(
        select(BankPosition.id).where(
            BankPosition.serial_id == s.id, BankPosition.ended_at.is_(None)
        )
    ):
        raise field_error(field, f"{number} is already in a bank; relocate it instead")
    return s


def _position_free(db: Session, bank_id, position: str, field: str) -> None:
    if db.scalar(select(BankPosition.id).where(BankPosition.bank_id == bank_id, BankPosition.position == position,
                                               BankPosition.ended_at.is_(None))):  # fmt: skip
        raise field_error(field, f"Position {position} is taken")


def _current(db: Session, bank_id, number: str) -> tuple[BankPosition, Serial]:
    row = db.execute(
        select(BankPosition, Serial).join(Serial, Serial.id == BankPosition.serial_id)
        .where(BankPosition.bank_id == bank_id, Serial.serial == number, BankPosition.ended_at.is_(None))
        .with_for_update()
    ).first()  # fmt: skip
    if row is None:
        raise field_error("serial", f"{number} is not in this bank")
    return row


def _end(bp: BankPosition, p: Principal, why: str, note: str) -> None:
    bp.ended_at, bp.end_reason, bp.ended_by_id = utcnow(), why, p.user.id
    bp.note = (bp.note + " · " if bp.note else "") + note


def _place(
    db: Session, p: Principal, b: BatteryBank, s: Serial, position: str, why: str, v, replaces=None
) -> None:
    db.add(BankPosition(bank_id=b.id, serial_id=s.id, position=position, start_reason=why,
                        replaces_id=replaces, visit_id=v.id if v else None, by_id=p.user.id))  # fmt: skip
    s.status, s.version = SERIAL_STATUS[b.status], s.version + 1
    db.flush()  # the partial unique indexes are the last guard against a double placement


@router.post("/banks/{bank_id}/batteries")
def add_batteries(bank_id: uuid.UUID, body: AddIn, p: Worker, db: DB) -> dict:
    b, v = _bank_for_change(db, p, bank_id, body.version, body.visit_id)
    if b.status != "planned":
        raise conflict(
            f"{b.number} is {words(b.status)}: a battery joins it only as a replacement, recorded on a visit"
        )
    serials = [x.serial for x in body.batteries]
    positions = [x.position for x in body.batteries]
    if len(set(serials)) != len(serials) or len(set(positions)) != len(positions):
        raise field_error("batteries", "Each battery and each position only once")
    for i, x in enumerate(body.batteries):
        s = _site_battery(db, x.serial, b.site_id, f"batteries.{i}.serial")
        _position_free(db, b.id, x.position, f"batteries.{i}.position")
        _place(db, p, b, s, x.position, "added", v)
    b.version += 1
    record(db, p.user, "bank.add", "battery_bank", b.id,
           f"{b.number}: {', '.join(serials)} added" + (f" on {v.number}" if v else ""))  # fmt: skip
    db.commit()
    return bank_row(db, b, full=True)


@router.post("/banks/{bank_id}/remove")
def remove_battery(bank_id: uuid.UUID, body: RemoveIn, p: Worker, db: DB) -> dict:
    b, v = _bank_for_change(db, p, bank_id, body.version, body.visit_id)
    bp, s = _current(db, b.id, body.serial)
    _end(bp, p, "removed", body.reason)
    s.status, s.version = "removed", s.version + 1
    b.version += 1
    record(db, p.user, "bank.remove", "battery_bank", b.id,
           f"{b.number}: {s.serial} removed from {bp.position}", reason=body.reason)  # fmt: skip
    db.commit()
    return bank_row(db, b, full=True)


@router.post("/banks/{bank_id}/replace")
def replace_battery(bank_id: uuid.UUID, body: ReplaceIn, p: Worker, db: DB) -> dict:
    """The old battery leaves its position, the new one takes it; each keeps its own history and
    the new placement names the battery it replaces."""
    b, v = _bank_for_change(db, p, bank_id, body.version, body.visit_id)
    if b.status != "planned" and v is None:
        raise field_error(
            "visit_id",
            f"{b.number} is {words(b.status)}: record the replacement on a visit, with its readings and photographs",
        )
    bp, old = _current(db, b.id, body.serial)
    new = _site_battery(db, body.new_serial, b.site_id, "new_serial")
    _end(bp, p, "replaced", body.reason)
    old.status, old.version = "removed", old.version + 1
    db.flush()
    _place(db, p, b, new, bp.position, "replacement", v, replaces=old.id)
    b.version += 1
    record(db, p.user, "bank.replace", "battery_bank", b.id,
           f"{b.number}: {old.serial} replaced by {new.serial} at {bp.position}", reason=body.reason)  # fmt: skip
    db.commit()
    return bank_row(db, b, full=True)


@router.post("/banks/{bank_id}/relocate")
def relocate_battery(bank_id: uuid.UUID, body: RelocateIn, p: Worker, db: DB) -> dict:
    """To another position, or to a bank at the same stage of the same customer (another site of
    theirs included). Both banks are locked in id order, so two opposite moves can't deadlock."""
    db.execute(select(BatteryBank.id).where(BatteryBank.id.in_([bank_id, body.to_bank_id]))
               .order_by(BatteryBank.id).with_for_update())  # fmt: skip
    b, v = _bank_for_change(db, p, bank_id, body.version, body.visit_id)
    bp, s = _current(db, b.id, body.serial)
    to = (
        b
        if body.to_bank_id == b.id
        else get_or_404(db, BatteryBank, body.to_bank_id, "Battery bank", lock=True)
    )
    if to.id != b.id:
        if v is not None and to.id not in v.bank_ids:
            raise field_error("to_bank_id", "This visit doesn't cover that bank")
        if to.status != b.status:
            raise conflict(
                f"{to.number} is {words(to.status)} and {b.number} is {words(b.status)}: "
                "batteries move only between banks at the same stage"
            )
        cust = lambda bank: db.get(Project, db.get(Site, bank.site_id).project_id).customer_id  # noqa: E731
        if cust(to) != cust(b):
            raise field_error(
                "to_bank_id", "Batteries move only between banks of the same customer"
            )
    elif bp.position == body.position:
        raise field_error("position", "That is where it already is")
    _end(bp, p, "relocated", body.reason)
    db.flush()
    _position_free(db, to.id, body.position, "position")
    s.site_id = to.site_id
    _place(db, p, to, s, body.position, "relocated", v)
    b.version += 1
    if to.id != b.id:
        to.version += 1
    record(db, p.user, "bank.relocate", "battery_bank", b.id,
           f"{s.serial} moved from {b.number} {bp.position} to {to.number} {body.position}", reason=body.reason)  # fmt: skip
    db.commit()
    return bank_row(db, b, full=True)


# ---------- visits ----------


class Contact(BaseModel):
    name: Short
    role: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""


class VisitIn(BaseModel):
    site_id: uuid.UUID
    stage: Literal["installation", "commissioning", "handover", "other"]
    bank_ids: list[uuid.UUID] = Field(default=[], max_length=50)
    scheduled_on: date
    time_window: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    technician_ids: list[uuid.UUID] = Field(default=[], max_length=20)
    site_ready: bool | None = None
    access_notes: Text = ""
    contacts: list[Contact] = Field(default=[], max_length=10)


class VisitPatch(BaseModel):
    version: int
    bank_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    scheduled_on: date | None = None
    time_window: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = (
        None
    )
    technician_ids: list[uuid.UUID] | None = Field(default=None, max_length=20)
    site_ready: bool | None = None
    access_notes: Text | None = None
    contacts: list[Contact] | None = Field(default=None, max_length=10)


def _check_plan(db: Session, site_id, stage: str, bank_ids: list, technician_ids: list) -> None:
    if len(set(bank_ids)) != len(bank_ids) or len(set(technician_ids)) != len(technician_ids):
        raise field_error("bank_ids", "Each bank and each technician only once")
    if stage in STAGES and not bank_ids:
        raise field_error("bank_ids", "Choose the battery banks this visit works on")
    found = set(
        db.scalars(
            select(BatteryBank.id).where(
                BatteryBank.id.in_(bank_ids), BatteryBank.site_id == site_id
            )
        )
    )
    if found != set(bank_ids):
        raise field_error("bank_ids", "Choose banks of this site")
    techs = {i for i, _ in db.execute(technicians_q().where(User.id.in_(technician_ids)))}
    if techs != set(technician_ids):
        raise field_error("technician_ids", "Choose active people who can record installation work")


@router.get("/installation/technicians")
def technicians(_: require("installation.view"), db: DB) -> dict:
    return {"items": [{"id": i, "name": n} for i, n in db.execute(technicians_q())]}


@router.get("/visits")
def list_visits(
    p: require("installation.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status")] = None,
    stage: str | None = None,
    project_id: uuid.UUID | None = None,
    site_id: uuid.UUID | None = None,
    mine: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(SiteVisit, Site).join(Site, Site.id == SiteVisit.site_id)
    if status_:
        query = query.where(SiteVisit.status.in_(status_.split(",")))
    if stage:
        query = query.where(SiteVisit.stage.in_(stage.split(",")))
    if project_id:
        query = query.where(Site.project_id == project_id)
    if site_id:
        query = query.where(SiteVisit.site_id == site_id)
    if mine:
        query = query.where(any_(SiteVisit.technician_ids) == p.user.id)
    if date_from:
        query = query.where(SiteVisit.scheduled_on >= date_from)
    if date_to:
        query = query.where(SiteVisit.scheduled_on <= date_to)
    if q:
        like = f"%{q.strip()}%"
        query = query.join(Project, Project.id == Site.project_id).where(
            or_(
                SiteVisit.number.ilike(like),
                Site.name.ilike(like),
                Project.number.ilike(like),
                Project.name.ilike(like),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.execute(
        query.order_by(SiteVisit.scheduled_on, SiteVisit.number).limit(limit).offset(offset)
    ).all()
    names = _names(db, [t for v, _ in rows for t in v.technician_ids])
    projects = {
        pr.id: pr
        for pr in db.scalars(select(Project).where(Project.id.in_({s.project_id for _, s in rows})))
    }
    return {"items": [visit_brief(db, v, names) | {
        "site": {"id": s.id, "name": s.name},
        "project": {"id": s.project_id, "number": projects[s.project_id].number, "name": projects[s.project_id].name}}
        for v, s in rows], "total": total}  # fmt: skip


@router.post("/visits", status_code=status.HTTP_201_CREATED)
def schedule_visit(body: VisitIn, p: require("installation.create"), db: DB) -> dict:
    site = get_or_404(db, Site, body.site_id, "Site")
    _check_plan(db, site.id, body.stage, body.bank_ids, body.technician_ids)
    data = body.model_dump()
    data["contacts"] = [c.model_dump() for c in body.contacts]
    v = SiteVisit(number=next_number(db, "site_visit"), created_by=p.user.id, **data)
    db.add(v)
    db.flush()
    record(db, p.user, "visit.create", "site_visit", v.id,
           f"{v.number}: {v.stage} visit to {site.name} on {v.scheduled_on}")  # fmt: skip
    db.commit()
    return visit_row(db, v, p)


@router.get("/visits/{visit_id}")
def get_visit(visit_id: uuid.UUID, p: require("installation.view"), db: DB) -> dict:
    return visit_row(db, get_or_404(db, SiteVisit, visit_id, "Visit"), p)


def _open_visit(db: Session, visit_id, version: int) -> SiteVisit:
    v = get_or_404(db, SiteVisit, visit_id, "Visit", lock=True)
    if v.version != version:
        raise stale()
    if v.status not in ("scheduled", "started"):
        raise conflict(f"Visit {v.number} is {v.status}; its record can't change")
    return v


@router.patch("/visits/{visit_id}")
def update_visit(
    visit_id: uuid.UUID, body: VisitPatch, p: require("installation.create"), db: DB
) -> dict:
    v = _open_visit(db, visit_id, body.version)
    data = {k: getattr(body, k) for k in body.model_fields_set - {"version"}
            if getattr(body, k) is not None or k == "site_ready"}  # fmt: skip
    if "contacts" in data:
        data["contacts"] = [c.model_dump() for c in body.contacts]
    _check_plan(
        db,
        v.site_id,
        v.stage,
        data.get("bank_ids", v.bank_ids),
        data.get("technician_ids", v.technician_ids),
    )
    changes = {k: [str(getattr(v, k)), str(x)] for k, x in data.items() if getattr(v, k) != x}
    for k, x in data.items():
        setattr(v, k, x)
    v.version += 1
    record(
        db,
        p.user,
        "visit.update",
        "site_visit",
        v.id,
        f"{v.number} rescheduled or reassigned",
        changes,
    )
    db.commit()
    return visit_row(db, v, p)


class CheckDone(BaseModel):
    check_id: uuid.UUID
    done: bool = False
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class Reading(BaseModel):
    check_id: uuid.UUID
    value: Annotated[Decimal, Field(max_digits=14, decimal_places=4)] | None = None


class Issue(BaseModel):
    """Issues are listed in their saved order; new ones go at the end. Saving can reword an open
    issue, never drop or close one: closing is its own action, with what was done."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    action: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class Ack(BaseModel):
    name: Short = ""
    role: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    on: date | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


class RecordIn(BaseModel):
    version: int
    site_ready: bool | None = None
    access_notes: Text | None = None
    received_serials: list[SerialNo] = Field(default=[], max_length=500)
    checklist: list[CheckDone] = Field(default=[], max_length=200)
    readings: list[Reading] = Field(default=[], max_length=200)
    observations: Text = ""
    issues: list[Issue] = Field(default=[], max_length=100)
    acknowledgement: Ack = Ack()


def _within(c: StageCheck, value) -> bool | None:
    """None when no confirmed limit applies (recorded, not judged)."""
    if value is None or not c.confirmed:
        return None
    v = Decimal(str(value))
    return (c.min_value is None or v >= Decimal(str(c.min_value))) and (
        c.max_value is None or v <= Decimal(str(c.max_value))
    )


@router.put("/visits/{visit_id}/record")
def record_work(visit_id: uuid.UUID, body: RecordIn, p: Worker, db: DB) -> dict:
    """Saves what was found and done so far. Checklist lines and readings are stored with the text
    and limits that applied, so later changes to the checks never rewrite a record."""
    v = _open_visit(db, visit_id, body.version)
    check_may_work(p, v)
    received = list(dict.fromkeys(body.received_serials))
    if received:
        here = set(db.scalars(select(Serial.serial).where(Serial.serial.in_(received), Serial.site_id == v.site_id,
                                                          Serial.status.in_(AT_SITE))))  # fmt: skip
        if missing := [x for x in received if x not in here]:
            raise field_error(
                "received_serials", f"Not delivered to this site: {', '.join(missing[:10])}"
            )
    checks = {c.id: c for c in checks_for(db, v.stage)} if v.stage in CHECKED else {}
    for field, rows, kind in (
        ("checklist", body.checklist, "check"),
        ("readings", body.readings, "reading"),
    ):
        ids = [r.check_id for r in rows]
        if len(set(ids)) != len(ids) or any(i not in checks or checks[i].kind != kind for i in ids):
            raise field_error(field, f"Use each {kind} of this stage once")
    v.received_serials = received
    v.checklist = [{"check_id": str(r.check_id), "text": checks[r.check_id].text, "required": checks[r.check_id].required,
                    "done": r.done, "note": r.note} for r in body.checklist]  # fmt: skip
    v.readings = [{"check_id": str(r.check_id), "text": (c := checks[r.check_id]).text, "unit": c.unit,
                   "required": c.required, "value": str(r.value) if r.value is not None else None,
                   "min": str(c.min_value) if c.min_value is not None else None,
                   "max": str(c.max_value) if c.max_value is not None else None,
                   "confirmed": c.confirmed, "within": _within(c, r.value)} for r in body.readings]  # fmt: skip
    if len(body.issues) < len(v.issues):
        raise field_error("issues", "Issues can't be deleted; close them with what was done")
    v.issues = [dict(old) | (i.model_dump() if old["status"] == "open" else {}) for old, i in zip(v.issues, body.issues, strict=False)] + [
        i.model_dump() | {"status": "open"} for i in body.issues[len(v.issues):]]  # fmt: skip
    v.observations = body.observations
    v.acknowledgement = body.acknowledgement.model_dump(mode="json")
    if "site_ready" in body.model_fields_set:
        v.site_ready = body.site_ready
    if body.access_notes is not None:
        v.access_notes = body.access_notes
    if v.status == "scheduled":
        v.status = "started"
    v.version += 1
    record(db, p.user, "visit.record", "site_visit", v.id, f"{v.number}: work recorded")
    db.commit()
    return visit_row(db, v, p)


class CompleteIn(BaseModel):
    version: int
    done_on: date


def _missing(db: Session, v: SiteVisit) -> list[str]:
    """Everything that stops this visit from completing, in words."""
    out = []
    if v.stage not in CHECKED:
        return out
    done = {r["check_id"] for r in v.checklist if r["done"]}
    taken = {r["check_id"]: r for r in v.readings if r["value"] is not None}
    for c in checks_for(db, v.stage):
        if c.kind == "check" and c.required and str(c.id) not in done:
            out.append(f"Checklist: {c.text}")
        if c.kind == "reading":
            r = taken.get(str(c.id))
            if r is None and c.required:
                out.append(f"Reading missing: {c.text}")
            elif r is not None and _within(c, r["value"]) is False:
                out.append(
                    f"Reading outside the confirmed limits: {c.text} {r['value']} {c.unit}".rstrip()
                )
    photos = sum(1 for f in visit_files(db, v) if f.kind == "photo")
    need = int(rules(db)[PHOTO_RULE[v.stage]])
    if photos < need:
        out.append(f"Photographs: {photos} of {need}")
    if v.stage == "handover" and not (
        v.acknowledgement.get("name") and v.acknowledgement.get("on")
    ):
        out.append("The customer's acknowledgement: who accepted the handover, and when")
    if any(i["status"] == "open" for i in v.issues) and v.stage == "handover":
        if not rules(db)["handover_with_open_issues"]:
            out.append("Open issues on this visit")
    return out


@router.post("/visits/{visit_id}/complete")
def complete_visit(visit_id: uuid.UUID, body: CompleteIn, p: Worker, db: DB) -> dict:
    v = _open_visit(db, visit_id, body.version)
    check_may_work(p, v)
    if body.done_on > date.today():
        raise field_error("done_on", "The work can't be done in the future")
    if missing := _missing(db, v):
        raise HTTPException(
            422, {"message": "Not ready to complete: " + "; ".join(missing), "missing": missing}
        )
    banks = db.scalars(
        select(BatteryBank)
        .where(BatteryBank.id.in_(v.bank_ids))
        .order_by(BatteryBank.id)
        .with_for_update()
    ).all()
    if v.stage in STAGES:
        before, after, field = STEP[v.stage]
        for b in banks:
            if b.status != before:
                raise conflict(
                    f"{b.number} is {b.status.replace('_', ' ')}; {v.stage} needs it {before}"
                )
            prev = {"commissioning": b.installed_on, "handover": b.commissioned_on}.get(v.stage)
            if prev and body.done_on < prev:
                raise field_error(
                    "done_on", f"{b.number}: earlier than the previous stage ({prev})"
                )
            pos = active_positions(db, [b.id])
            if not pos:
                raise conflict(f"{b.number} has no batteries")
            if v.stage == "installation":
                if unconfirmed := [
                    s.serial for _, s, _ in pos if s.serial not in v.received_serials
                ]:
                    raise field_error(
                        "received_serials", f"Confirm receipt of {', '.join(unconfirmed[:10])}"
                    )
            b.status, b.version = after, b.version + 1
            setattr(b, field, body.done_on)
            for _, s, _ in pos:
                s.status, s.version = SERIAL_STATUS[after], s.version + 1
        _milestone(db, v, body.done_on)
        if v.stage == "handover":
            from app.warranty import (
                open_at_handover,  # here, not at the top: warranty imports this module's neighbours
            )

            open_at_handover(db, p.user, banks)
    v.status, v.done_on, v.completed_by, v.completed_at = (
        "completed",
        body.done_on,
        p.user.id,
        utcnow(),
    )
    v.version += 1
    record(db, p.user, "visit.complete", "site_visit", v.id,
           f"{v.number}: {v.stage} completed on {v.done_on}" + "".join(f", {b.number} {b.status.replace('_', ' ')}" for b in banks))  # fmt: skip
    db.commit()
    return visit_row(db, v, p)


def _milestone(db: Session, v: SiteVisit, on: date) -> None:
    """When every bank of the project has reached this stage, its milestone of that kind is done."""
    site = db.get(Site, v.site_id)
    reached = ORDER.index(STEP[v.stage][1])
    statuses = db.scalars(select(BatteryBank.status).join(Site, Site.id == BatteryBank.site_id)
                          .where(Site.project_id == site.project_id)).all()  # fmt: skip
    if all(ORDER.index(s) >= reached for s in statuses):
        for m in db.scalars(select(Milestone).where(Milestone.project_id == site.project_id, Milestone.kind == v.stage,
                                                    Milestone.done_at.is_(None))):  # fmt: skip
            m.done_at, m.version = on, m.version + 1


class CancelIn(BaseModel):
    version: int
    reason: NeededReason


@router.post("/visits/{visit_id}/cancel")
def cancel_visit(
    visit_id: uuid.UUID, body: CancelIn, p: require("installation.create"), db: DB
) -> dict:
    """Not done, or not finished: the record of what was found stays."""
    v = _open_visit(db, visit_id, body.version)
    v.status, v.cancel_reason, v.version = "cancelled", body.reason, v.version + 1
    record(
        db, p.user, "visit.cancel", "site_visit", v.id, f"{v.number} cancelled", reason=body.reason
    )
    db.commit()
    return visit_row(db, v, p)


class CloseIssueIn(BaseModel):
    version: int
    action: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


@router.post("/visits/{visit_id}/issues/{index}/close")
def close_issue(visit_id: uuid.UUID, index: int, body: CloseIssueIn, p: Worker, db: DB) -> dict:
    """Outstanding work found on a visit is closed when it is done, even after the visit ended."""
    v = get_or_404(db, SiteVisit, visit_id, "Visit", lock=True)
    if v.version != body.version:
        raise stale()
    check_may_work(p, v)
    if not 0 <= index < len(v.issues) or v.issues[index]["status"] != "open":
        raise conflict("That issue is not open")
    issues = [dict(i) for i in v.issues]
    issues[index] |= {"status": "closed", "action": body.action, "closed_on": date.today().isoformat(),
                      "closed_by": p.user.name}  # fmt: skip
    v.issues, v.version = issues, v.version + 1
    record(
        db,
        p.user,
        "visit.issue_close",
        "site_visit",
        v.id,
        f"{v.number}: issue closed: {issues[index]['text']}",
    )
    db.commit()
    return visit_row(db, v, p)


@router.get("/projects/{project_id}/installation")
def project_installation(project_id: uuid.UUID, _: require("installation.view"), db: DB) -> dict:
    """Banks and visits of a project's sites, and the outstanding work found on them."""
    get_or_404(db, Project, project_id, "Project")
    site_ids = select(Site.id).where(Site.project_id == project_id)
    banks = db.scalars(
        select(BatteryBank).where(BatteryBank.site_id.in_(site_ids)).order_by(BatteryBank.number)
    ).all()
    visits = db.scalars(select(SiteVisit).where(SiteVisit.site_id.in_(site_ids))
                        .order_by(SiteVisit.scheduled_on, SiteVisit.number)).all()  # fmt: skip
    names = _names(db, [t for v in visits for t in v.technician_ids])
    outstanding = [{"visit_id": v.id, "visit": v.number, "index": i, "text": x["text"], "action": x["action"]}
                   for v in visits for i, x in enumerate(v.issues) if x["status"] == "open"]  # fmt: skip
    return {"banks": [bank_row(db, b) for b in banks], "visits": [visit_brief(db, v, names) for v in visits],
            "outstanding": outstanding}  # fmt: skip


@router.get("/banks")
def list_banks(
    _: require("installation.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[str | None, Query(max_length=80)] = None,
    customer_id: uuid.UUID | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(BatteryBank).join(Site, Site.id == BatteryBank.site_id)
    if customer_id:
        query = query.join(Project, Project.id == Site.project_id).where(
            Project.customer_id == customer_id
        )
    if status_:
        query = query.where(BatteryBank.status.in_(status_.split(",")))
    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(BatteryBank.number.ilike(like), BatteryBank.name.ilike(like), Site.name.ilike(like))
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    banks = db.scalars(query.order_by(BatteryBank.number).limit(limit).offset(offset)).all()
    sites = {
        x.id: x for x in db.scalars(select(Site).where(Site.id.in_({b.site_id for b in banks})))
    }
    return {"items": [bank_row(db, b) | {"site": {"id": b.site_id, "name": sites[b.site_id].name,
                                                  "project_id": sites[b.site_id].project_id}} for b in banks],
            "total": total}  # fmt: skip


@router.get("/sites/{site_id}/batteries")
def site_batteries(site_id: uuid.UUID, _: require("installation.view"), db: DB) -> dict:
    """Batteries delivered to this site that are in no bank: what can be placed."""
    get_or_404(db, Site, site_id, "Site")
    placed = select(BankPosition.serial_id).where(BankPosition.ended_at.is_(None))
    rows = db.execute(select(Serial, Item).join(Item, Item.id == Serial.item_id)
                      .where(Serial.site_id == site_id, Serial.status.in_(AT_SITE), Item.kind.in_(BATTERY_KINDS),
                             Serial.id.not_in(placed)).order_by(Serial.serial).limit(500)).all()  # fmt: skip
    return {
        "items": [
            {"id": x.id, "serial": x.serial, "status": x.status, "item": item_ref(i)}
            for x, i in rows
        ]
    }
