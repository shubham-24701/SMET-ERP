"""The public portal: no sign-in, so it shows and accepts as little as possible.

Anyone may check a battery by its label (QR) or serial number and see the product, whether it is in
service and its warranty state; dates only if SMET allows (question 20). No customer, site, document
or internal id is ever returned. A valid serial confirms SMET has a record of it, not that the
product is authentic.

Anyone may also ask for a warranty registration or a service visit. Both land as pending work for
SMET staff; nothing is approved or scheduled by the public. Answers are the same whether or not the
battery exists or already has a registration, so the forms can't be used to probe records.
Everything is rate-limited per address; when a limit is hit, a hashed address is logged (no
personal data).
"""

import hashlib
import logging
import re
from datetime import date
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import record, request_info
from app.auth import DB, rate_limit
from app.files import read_checked, store
from app.masters import next_number, rules
from app.models import Item, Serial, ServiceRequest, WarrantyRegistration
from app.service import _target
from app.trace import PUBLIC_STATUS
from app.warranty import LEFT_SMET, OPEN, warranty_of

router = APIRouter(prefix="/public", tags=["public"])
log = logging.getLogger("smet.public")
NOTE = "A valid serial number confirms that SMET has a record of this battery. It is not proof that the product is genuine."
RECEIVED = {"message": "Thank you. SMET has received it and will contact you."}


def limit(name: str, n: int, window_s: int) -> None:
    ip = request_info.get().get("ip", "")
    try:
        rate_limit(
            f"{name}:{ip}",
            n,
            window_s,
            "Too many requests from this address; please try again later",
        )
    except HTTPException:
        log.warning(
            "Public %s limit reached",
            name,
            extra={"client": hashlib.sha256(ip.encode()).hexdigest()[:12]},
        )
        raise


def public_view(db: DB, s: Serial) -> dict:
    w = warranty_of(db, s.id)
    shown = {"state": w["state"]}
    if rules(db)["public_warranty_dates"] and w["state"] in ("active", "expired", "not_started"):
        shown |= {"start_date": w["start_date"], "expiry_date": w["expiry_date"]}
    return {"serial": s.serial, "product": db.get(Item, s.item_id).name,
            "status": PUBLIC_STATUS.get(s.status, "not_in_service"), "warranty": shown, "note": NOTE}  # fmt: skip


@router.get("/verify/{token}")
def verify(token: str, db: DB) -> dict:
    """From a scanned label."""
    limit("verify", 30, 60)
    s = (
        db.scalar(select(Serial).where(Serial.qr_token == token))
        if re.fullmatch(r"[0-9a-f]{32}", token)
        else None
    )
    if s is None:
        raise HTTPException(404, "We can't find this label")
    return public_view(db, s)


@router.get("/lookup")
def lookup(
    serial: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)], db: DB
) -> dict:
    """From a serial number typed in. Batteries only; anything else answers like an unknown number."""
    limit("verify", 30, 60)
    s = db.scalar(select(Serial).join(Item, Item.id == Serial.item_id)
                  .where(Serial.serial == serial.upper(), Item.kind.in_(("battery_model", "battery_unit"))))  # fmt: skip
    if s is None:
        raise HTTPException(404, "We can't find that serial number")
    return public_view(db, s)


Short = Annotated[str, Form(max_length=160)]


@router.post("/registrations", status_code=status.HTTP_202_ACCEPTED)
def public_registration(
    db: DB,
    serial: Annotated[str, Form(min_length=1, max_length=60)],
    customer_name: Annotated[str, Form(min_length=2, max_length=160)],
    contact: Annotated[str, Form(min_length=5, max_length=160)],
    purchase_date: Annotated[date, Form()],
    address: Annotated[str, Form(max_length=2000)] = "",
    seller: Short = "",
    website: Short = "",  # left empty by people; filled by bots
    proof: Annotated[UploadFile | None, File()] = None,
) -> dict:
    limit("register", 5, 3600)
    if (
        proof is not None and proof.filename
    ):  # judged before the serial, so the answer can't tell them apart
        read_checked(proof, "proof_of_purchase")
        proof.file.seek(0)
    s = db.scalar(select(Serial).join(Item, Item.id == Serial.item_id)
                  .where(Serial.serial == serial.strip().upper(), Item.kind.in_(("battery_model", "battery_unit"))))  # fmt: skip
    taken = s and db.scalar(select(WarrantyRegistration.id).where(WarrantyRegistration.serial_id == s.id,
                                                                  WarrantyRegistration.status.in_(OPEN)))  # fmt: skip
    if website or s is None or taken or s.status not in LEFT_SMET or purchase_date > date.today():
        log.info(
            "Public registration not recorded (unknown, already registered, not sold, or filtered)"
        )
        return RECEIVED
    r = WarrantyRegistration(number=next_number(db, "warranty"), serial_id=s.id, route="public",
                             customer_name=customer_name.strip(), customer_contact=contact.strip(), address=address.strip(),
                             distributor_name=seller.strip(), sale_date=purchase_date, notes="Submitted on the public form")  # fmt: skip
    try:
        with db.begin_nested():
            db.add(r)
            db.flush()
    except IntegrityError:  # someone registered it a moment ago
        return RECEIVED
    record(
        db,
        None,
        "warranty.register",
        "warranty_registration",
        r.id,
        f"{r.number}: {s.serial} registered on the public form",
    )
    if proof is not None and proof.filename:
        store(
            db,
            None,
            "warranty",
            r.id,
            proof,
            "proof_of_purchase",
            "",
            ("warranty_registration", r.id, r.number),
        )
    else:
        db.commit()
    return RECEIVED


class PublicSr(BaseModel):
    serial: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=160)]
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=60)]
    issue: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=4000)]
    preferred_visit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    website: Annotated[str, StringConstraints(max_length=160)] = ""


@router.post("/service-requests", status_code=status.HTTP_202_ACCEPTED)
def public_service_request(body: PublicSr, db: DB) -> dict:
    """Kept even when the serial is mistyped: SMET staff call back and fill it in."""
    limit("service", 5, 3600)
    if body.website:
        return RECEIVED
    s = db.scalar(select(Serial.serial).join(Item, Item.id == Serial.item_id)
                  .where(Serial.serial == body.serial.upper(), Item.kind.in_(("battery_model", "battery_unit")))) if body.serial else None  # fmt: skip
    target = _target(db, s, None, None) if s else {}
    sr = ServiceRequest(number=next_number(db, "service_request"), source="public", reported_serial=body.serial.upper(),
                        contact_name=body.name, contact_phone=body.phone, issue=body.issue,
                        preferred_visit=body.preferred_visit, **target)  # fmt: skip
    db.add(sr)
    db.flush()
    record(
        db, None, "service.open", "service_request", sr.id, f"{sr.number} opened on the public form"
    )
    db.commit()
    return RECEIVED
