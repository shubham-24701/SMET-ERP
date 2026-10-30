"""Phase 6 maintenance alerts and reset codes: a separate workflow from warranty.

No supplier mechanism is confirmed (question 22), so nothing here generates, expires or clears a code
by itself. An alert is registered against a battery and its service request; the battery, customer
and installed location are verified; the required inspection is a completed visit on that request;
only then may someone holding `codes.reveal` type in the code the supplier issued. The code is
encrypted (Fernet, key in CODES_KEY), never logged or returned in lists, shown again only to that
permission with a reason (audited), and erased `code_retention_days` after the alert closes.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import record
from app.auth import DB, require
from app.config import get_settings
from app.inventory import NeededReason, conflict
from app.jobs import periodic
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, rules
from app.models import (
    Item,
    MaintenanceAlert,
    Party,
    Serial,
    ServiceRequest,
    Site,
    SiteVisit,
    utcnow,
)
from app.projects import _names
from app.service import current_bank

router = APIRouter(tags=["maintenance codes"], route_class=NumbersRoute)


def fernet() -> Fernet:
    key = get_settings().codes_key
    if not key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Reset codes can't be stored yet: CODES_KEY is not set (run python -m app.setup_db)")  # fmt: skip
    return Fernet(key)


def purge(db: Session) -> int:
    """Erases codes of alerts closed longer ago than the retention period. The record stays."""
    cutoff = utcnow() - timedelta(days=int(rules(db)["code_retention_days"]))
    r = db.execute(update(MaintenanceAlert).where(MaintenanceAlert.closed_at < cutoff, MaintenanceAlert.code_ciphertext.is_not(None))
                   .values(code_ciphertext=None, purged_at=utcnow()))  # fmt: skip
    return r.rowcount


@periodic(3600)
def purge_codes(db: Session) -> None:
    if n := purge(db):
        record(
            db,
            None,
            "code.purge",
            "maintenance_alert",
            "",
            f"{n} reset codes erased after the retention period",
        )
    db.commit()


def alert_row(db: Session, a: MaintenanceAlert, full: bool = False) -> dict:
    s = db.get(Serial, a.serial_id)
    sr = db.get(ServiceRequest, a.service_request_id)
    row = {"id": a.id, "number": a.number, "status": a.status, "serial": {"id": s.id, "serial": s.serial},
           "service_request": {"id": sr.id, "number": sr.number, "status": sr.status}, "description": a.description,
           "has_code": a.code_ciphertext is not None, "created_at": a.created_at, "version": a.version}  # fmt: skip
    if full:
        names = _names(db, [a.verified_by, a.issued_by, a.created_by])
        done = db.scalars(
            select(SiteVisit).where(
                SiteVisit.service_request_id == sr.id, SiteVisit.status == "completed"
            )
        ).all()
        row |= {"item": item_ref(db.get(Item, s.item_id)), "verification": a.verification, "verified_by": names.get(a.verified_by),
                "verified_at": a.verified_at, "recipient": a.recipient, "supplier_reference": a.supplier_reference,
                "issued_on": a.issued_on, "expires_on": a.expires_on, "issued_by": names.get(a.issued_by),
                "cleared": a.cleared, "cleared_note": a.cleared_note, "closed_at": a.closed_at, "purged_at": a.purged_at,
                "created_by": names.get(a.created_by), "facts": _facts(db, s),
                "evidence": [{"id": v.id, "number": v.number, "done_on": v.done_on} for v in done]}  # fmt: skip
    return row


def _facts(db: Session, s: Serial) -> dict:
    """What the person verifying compares with the caller's account: whose battery, where."""
    bank = current_bank(db, s.id)
    site = db.get(Site, s.site_id) if s.site_id else None
    return {"customer": db.get(Party, s.party_id).name if s.party_id else None, "site": site.name if site else None,
            "address": site.address if site else None, "bank": f"{bank.number} {bank.name}" if bank else None,
            "status": s.status}  # fmt: skip


@router.get("/maintenance-alerts")
def list_alerts(
    _: require("codes.view"),
    db: DB,
    status_: Annotated[str | None, Query(alias="status")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    purge(db)
    db.commit()
    query = select(MaintenanceAlert)
    if status_:
        query = query.where(MaintenanceAlert.status.in_(status_.split(",")))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(
        query.order_by(MaintenanceAlert.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return {"items": [alert_row(db, a) for a in rows], "total": total}


@router.get("/maintenance-alerts/{alert_id}")
def get_alert(alert_id: uuid.UUID, _: require("codes.view"), db: DB) -> dict:
    return alert_row(db, get_or_404(db, MaintenanceAlert, alert_id, "Alert"), full=True)


class AlertIn(BaseModel):
    service_request_id: uuid.UUID
    serial: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)
    ]


@router.post("/maintenance-alerts", status_code=status.HTTP_201_CREATED)
def register_alert(body: AlertIn, p: require("codes.create"), db: DB) -> dict:
    sr = get_or_404(
        db, ServiceRequest, body.service_request_id, "Service request", lock=True
    )  # not closing meanwhile
    if sr.status == "closed":
        raise conflict(f"{sr.number} is closed; open a new service request")
    s = (
        db.scalar(select(Serial).where(Serial.serial == body.serial.upper()))
        if body.serial
        else (db.get(Serial, sr.serial_id) if sr.serial_id else None)
    )
    if s is None:
        raise field_error("serial", "Name the battery showing the alert")
    bank = current_bank(db, s.id)
    if s.id != sr.serial_id and not (
        (bank and bank.id == sr.bank_id) or (sr.site_id and s.site_id == sr.site_id)
    ):
        raise field_error("serial", f"{s.serial} is not the battery, bank or site of {sr.number}")
    a = MaintenanceAlert(number=next_number(db, "maintenance_alert"), serial_id=s.id, service_request_id=sr.id,
                         description=body.description, created_by=p.user.id)  # fmt: skip
    db.add(a)
    db.flush()
    record(
        db,
        p.user,
        "code.alert",
        "maintenance_alert",
        a.id,
        f"{a.number}: alert on {s.serial} ({sr.number})",
    )
    db.commit()
    return alert_row(db, a, full=True)


class VerifyIn(BaseModel):
    version: int
    serial_confirmed: bool
    customer_confirmed: bool
    location_confirmed: bool
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


@router.post("/maintenance-alerts/{alert_id}/verify")
def verify_alert(alert_id: uuid.UUID, body: VerifyIn, p: require("codes.create"), db: DB) -> dict:
    """The serial on the battery, the customer and the installed location all match SMET's records."""
    a = _alert(db, alert_id, body.version, "open")
    if not (body.serial_confirmed and body.customer_confirmed and body.location_confirmed):
        raise field_error(
            "serial_confirmed",
            "All three must match before a code can be issued; if not, note why and close the alert",
        )
    s = db.get(Serial, a.serial_id)
    a.verification = {"checked": _facts(db, s), "note": body.note}
    a.status, a.verified_by, a.verified_at, a.version = (
        "verified",
        p.user.id,
        utcnow(),
        a.version + 1,
    )
    record(
        db,
        p.user,
        "code.verify",
        "maintenance_alert",
        a.id,
        f"{a.number}: battery, customer and location verified",
    )
    db.commit()
    return alert_row(db, a, full=True)


def _alert(db: Session, alert_id, version: int, *statuses: str) -> MaintenanceAlert:
    a = get_or_404(db, MaintenanceAlert, alert_id, "Alert", lock=True)
    if a.version != version:
        raise stale()
    if a.status not in statuses:
        raise conflict(f"{a.number} is {a.status.replace('_', ' ')}")
    return a


class CodeIn(BaseModel):
    version: int
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    recipient: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)
    ]
    issued_on: date
    supplier_reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = (
        ""
    )
    expires_on: date | None = Field(
        default=None, description="Only if the supplier states an expiry"
    )


@router.post("/maintenance-alerts/{alert_id}/code")
def record_code(alert_id: uuid.UUID, body: CodeIn, p: require("codes.reveal"), db: DB) -> dict:
    """Records the code the supplier issued, once. Needs the verification and a completed inspection
    visit on the service request."""
    a = _alert(db, alert_id, body.version, "verified")
    done = db.scalar(
        select(SiteVisit.id).where(
            SiteVisit.service_request_id == a.service_request_id, SiteVisit.status == "completed"
        )
    )
    if not done:
        raise conflict("Complete the inspection or maintenance visit on the service request first")
    if body.issued_on > date.today():
        raise field_error("issued_on", "Not in the future")
    if body.expires_on and body.expires_on < body.issued_on:
        raise field_error("expires_on", "Expires after it is issued")
    a.code_ciphertext = fernet().encrypt(body.code.encode()).decode()
    a.recipient, a.issued_on, a.supplier_reference, a.expires_on = (
        body.recipient,
        body.issued_on,
        body.supplier_reference,
        body.expires_on,
    )
    a.issued_by, a.status, a.version = p.user.id, "code_recorded", a.version + 1
    record(db, p.user, "code.record", "maintenance_alert", a.id,
           f"{a.number}: reset code recorded, given to {a.recipient}")  # fmt: skip
    # the code itself never goes into the audit log or the application log
    db.commit()
    return alert_row(db, a, full=True)


class RevealIn(BaseModel):
    reason: NeededReason


@router.post("/maintenance-alerts/{alert_id}/reveal")
def reveal_code(alert_id: uuid.UUID, body: RevealIn, p: require("codes.reveal"), db: DB) -> dict:
    """Shows the code once more, to this permission only; each look is audited with its reason."""
    a = get_or_404(db, MaintenanceAlert, alert_id, "Alert")
    if a.code_ciphertext is None:
        raise conflict(
            "No code is stored" + (" (erased after the retention period)" if a.purged_at else "")
        )
    try:
        code = fernet().decrypt(a.code_ciphertext.encode()).decode()
    except InvalidToken:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "The code can't be read with the current CODES_KEY",
        ) from None
    record(
        db,
        p.user,
        "code.view",
        "maintenance_alert",
        a.id,
        f"{a.number}: reset code viewed",
        reason=body.reason,
    )
    db.commit()
    return {"code": code}


class CloseIn(BaseModel):
    version: int
    cleared: bool | None = None
    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]


@router.post("/maintenance-alerts/{alert_id}/close")
def close_alert(alert_id: uuid.UUID, body: CloseIn, p: require("codes.create"), db: DB) -> dict:
    """Closes the alert with what was seen on site: cleared, not cleared, or unknown. The system
    never assumes a code cleared anything."""
    a = _alert(db, alert_id, body.version, "open", "verified", "code_recorded")
    a.cleared, a.cleared_note, a.status, a.closed_at, a.version = (
        body.cleared,
        body.note,
        "closed",
        utcnow(),
        a.version + 1,
    )
    word = {True: "cleared", False: "not cleared", None: "clearing not confirmed"}[body.cleared]
    record(
        db,
        p.user,
        "code.close",
        "maintenance_alert",
        a.id,
        f"{a.number} closed: alert {word}",
        reason=body.note,
    )
    db.commit()
    return alert_row(db, a, full=True)
