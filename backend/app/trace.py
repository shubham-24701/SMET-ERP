"""Phase 5 traceability: find a battery by scan or number, its whole life on one timeline, QR labels,
and the public verification a label leads to.

The timeline is read from the records that own each event (stock movements, inspections, dispatches,
bank positions, visits), so it can never disagree with them. A QR label carries only
`<public address>/v/<random token>`: no customer, no internal id, nothing secret. The public check
shows the serial number, the product and whether it is in service; nothing else.
"""

import re
import uuid
from datetime import datetime, time
from typing import Annotated

import segno
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import any_, select
from sqlalchemy.orm import Session

from app.auth import DB, Principal, require
from app.config import get_settings
from app.installation import site_ref
from app.inventory import label, serial_row
from app.masters import get_or_404, item_ref
from app.models import (
    BankPosition,
    BatteryBank,
    Dispatch,
    DispatchLine,
    Inspection,
    Item,
    Location,
    MaintenanceAlert,
    Party,
    Serial,
    SerialComponent,
    ServiceRequest,
    Site,
    SiteVisit,
    StockMove,
    User,
    WarrantyRegistration,
)
from app.projects import _names
from app.sales import party_ref
from app.service import amc_of
from app.warranty import warranty_of

router = APIRouter(tags=["traceability"])
TOKEN = re.compile(r"(?:/v/)?([0-9a-f]{32})/?$")
MOVE_WORDS = {
    "receipt": "Received from the supplier", "transfer": "Moved", "issue": "Issued to assembly",
    "consume": "Used in assembly", "build": "Assembled", "return": "Returned to stock from assembly",
    "supplier_return": "Returned to the supplier", "dispatch": "Dispatched to the customer",
    "dispatch_cancel": "Dispatch cancelled; back in stock", "customer_return": "Refused at delivery; back in returns",
    "adjust_in": "Stock correction (in)", "adjust_out": "Stock correction (out)",
    "accept": "Accepted at receiving inspection", "reject": "Rejected at receiving inspection",
    "hold": "Put on hold at receiving inspection", "passed": "Passed inspection",
    "on_hold": "Put on hold at final inspection", "rework": "Sent to rework",
}  # fmt: skip
STAGE_WORDS = {
    "installation": "Installed",
    "commissioning": "Commissioned",
    "handover": "Handed over to the customer",
}
PUBLIC_STATUS = {"installed": "installed", "commissioned": "in_service", "in_service": "in_service",
                 "removed": "removed", "written_off": "withdrawn", "rejected": "withdrawn",
                 "returned_to_supplier": "withdrawn"}  # fmt: skip


def qr_url(s: Serial) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/v/{s.qr_token}"


@router.get("/trace/scan")
def scan(
    code: Annotated[str, Query(min_length=1, max_length=300)], _: require("stock.view"), db: DB
) -> dict:
    """A scanned label (its address or token), our serial number or the supplier's."""
    code = code.strip()
    s = None
    if m := TOKEN.search(code.lower()):
        s = db.scalar(select(Serial).where(Serial.qr_token == m.group(1)))
    s = s or db.scalar(select(Serial).where(Serial.serial == code))
    if s is None:
        found = db.scalars(select(Serial).where(Serial.supplier_serial == code).limit(2)).all()
        s = found[0] if len(found) == 1 else None
    if s is None:
        raise HTTPException(404, "No battery or unit with that label or serial number")
    return {"id": s.id, "serial": s.serial}


def _when(x) -> str:
    return x.isoformat() if isinstance(x, datetime) else datetime.combine(x, time.max).isoformat()


@router.get("/serials/{serial_id}/timeline")
def timeline(serial_id: uuid.UUID, p: require("stock.view"), db: DB) -> dict:
    s = get_or_404(db, Serial, serial_id, "Serial number")
    events: list[dict] = []
    moves = db.scalars(
        select(StockMove).where(StockMove.serial_id == s.id).order_by(StockMove.id)
    ).all()
    users = _names(db, [m.user_id for m in moves])
    locs = {loc.id: loc for loc in db.scalars(select(Location).where(
        Location.id.in_({x for m in moves for x in (m.from_location_id, m.to_location_id) if x})))}  # fmt: skip
    for m in moves:
        where = " → ".join(label(db, locs[x]) for x in (m.from_location_id, m.to_location_id) if x)
        events.append({"at": m.at, "kind": "stock", "title": MOVE_WORDS.get(m.kind, m.kind.replace("_", " ").capitalize()),
                       "detail": where, "doc": {"type": m.doc_type, "id": m.doc_id, "number": m.doc_number},
                       "by": users.get(m.user_id)})  # fmt: skip
    for x, name in db.execute(select(Inspection, User.name).join(User, User.id == Inspection.inspector_id)
                              .where(Inspection.serial_id == s.id)):  # fmt: skip
        events.append({"at": x.at, "kind": "inspection", "title": f"Final inspection: {x.result.replace('_', ' ')}",
                       "detail": x.observations, "by": name})  # fmt: skip
    for parent in db.scalars(select(Serial).join(SerialComponent, SerialComponent.serial_id == Serial.id)
                             .where(SerialComponent.component_serial_id == s.id)):  # fmt: skip
        events.append({"at": parent.created_at, "kind": "assembly", "title": f"Built into {parent.serial}",
                       "link": {"type": "serial", "id": parent.id}})  # fmt: skip
    for d, ln in db.execute(select(Dispatch, DispatchLine).join(DispatchLine, DispatchLine.dispatch_id == Dispatch.id)
                            .where(any_(DispatchLine.serials) == s.serial, Dispatch.status == "delivered")):  # fmt: skip
        refused = s.serial in ln.rejected_serials
        events.append({"at": d.delivered_at or d.delivered_on, "kind": "delivery",
                       "title": "Refused at delivery" if refused else "Delivered",
                       "detail": f"Received by {d.received_by}", "doc": {"type": "dispatch", "id": str(d.id), "number": d.number}})  # fmt: skip
    places = db.execute(select(BankPosition, BatteryBank).join(BatteryBank, BatteryBank.id == BankPosition.bank_id)
                        .where(BankPosition.serial_id == s.id).order_by(BankPosition.started_at)).all()  # fmt: skip
    sites = {}
    replaced = dict(db.execute(select(Serial.id, Serial.serial).where(
        Serial.id.in_({bp.replaces_id for bp, _ in places if bp.replaces_id}))).all())  # fmt: skip
    for bp, b in places:
        site = sites.setdefault(b.site_id, db.get(Site, b.site_id))
        where = f"{b.number} {b.name}, {site.name}, position {bp.position}"
        start = {"added": "Placed in a battery bank", "replacement": f"Replaced {replaced.get(bp.replaces_id, 'a battery')}",
                 "relocated": "Moved to a new position"}[bp.start_reason]  # fmt: skip
        events.append(
            {
                "at": bp.started_at,
                "kind": "bank",
                "title": start,
                "detail": where,
                "link": {"type": "bank", "id": b.id},
            }
        )
        if bp.ended_at:
            end = {
                "removed": "Removed from the bank",
                "replaced": "Replaced in the bank",
                "relocated": "Moved out of",
            }[bp.end_reason]
            events.append({"at": bp.ended_at, "kind": "bank", "title": end, "detail": f"{where}. {bp.note}".strip(),
                           "link": {"type": "bank", "id": b.id}})  # fmt: skip
        for v in db.scalars(select(SiteVisit).where(any_(SiteVisit.bank_ids) == b.id, SiteVisit.status == "completed",
                                                    SiteVisit.stage.in_(STAGE_WORDS))):  # fmt: skip
            if bp.started_at <= v.completed_at and (
                bp.ended_at is None or bp.ended_at > v.completed_at
            ):
                events.append({"at": v.completed_at, "kind": "stage", "title": STAGE_WORDS[v.stage],
                               "detail": f"On {v.done_on}, {where}", "doc": {"type": "visit", "id": str(v.id), "number": v.number}})  # fmt: skip
    events += after_sales(db, s, p)
    events.sort(key=lambda e: _when(e["at"]))
    return {"serial": serial_row(db, s), "now": where_now(db, s, p), "events": events}


def after_sales(db: Session, s: Serial, p: Principal) -> list[dict]:
    """Warranty decisions, service requests and maintenance alerts (never a code's value)."""
    out = []
    # Each group only for those allowed to see it: stock.view alone opens the timeline.
    regs = select(WarrantyRegistration).where(WarrantyRegistration.serial_id == s.id)
    for r in db.scalars(regs) if p.can("warranty.view") else []:
        doc = {"type": "warranty", "id": str(r.id), "number": r.number}
        out.append(
            {"at": r.created_at, "kind": "warranty", "title": "Warranty registered", "doc": doc}
        )
        if r.decided_at and r.status in ("approved", "ended"):
            out.append(
                {
                    "at": r.decided_at,
                    "kind": "warranty",
                    "title": "Warranty approved",
                    "detail": f"{r.start_date} to {r.expiry_date}",
                    "doc": doc,
                }
            )
        elif r.decided_at and r.status == "rejected":
            out.append(
                {
                    "at": r.decided_at,
                    "kind": "warranty",
                    "title": "Warranty registration rejected",
                    "detail": r.decision_reason,
                    "doc": doc,
                }
            )
        if r.ended_on:
            out.append(
                {
                    "at": r.ended_on,
                    "kind": "warranty",
                    "title": "Warranty ended",
                    "detail": r.end_reason,
                    "doc": doc,
                }
            )
    for sr in (
        []
        if not p.can("service.view")
        else db.scalars(
            select(ServiceRequest).where(
                (ServiceRequest.serial_id == s.id) | (ServiceRequest.replacement_serial_id == s.id)
            )
        )
    ):
        doc = {"type": "service", "id": str(sr.id), "number": sr.number}
        role = "" if sr.serial_id == s.id else " (as the replacement)"
        out.append(
            {
                "at": sr.created_at,
                "kind": "service",
                "title": f"Service request opened{role}",
                "detail": sr.issue[:160],
                "doc": doc,
            }
        )
        closed = next((h for h in reversed(sr.history) if h["to"] == "closed"), None)
        if closed:
            out.append(
                {
                    "at": datetime.fromisoformat(closed["at"]),
                    "kind": "service",
                    "title": f"Service request closed: {sr.resolution.replace('_', ' ')}",
                    "detail": sr.closure_notes[:160],
                    "doc": doc,
                }
            )
    alerts = select(MaintenanceAlert).where(MaintenanceAlert.serial_id == s.id)
    for a in db.scalars(alerts) if p.can("codes.view") else []:
        doc = {"type": "alert", "id": str(a.id), "number": a.number}
        out.append(
            {
                "at": a.created_at,
                "kind": "service",
                "title": "Maintenance alert registered",
                "detail": a.description[:160],
                "doc": doc,
            }
        )
        if a.issued_on:
            out.append(
                {
                    "at": a.issued_on,
                    "kind": "service",
                    "title": "Reset code recorded",
                    "detail": f"Given to {a.recipient}",
                    "doc": doc,
                }
            )
    return out


def where_now(db: Session, s: Serial, p: Principal) -> dict:
    """Where the battery is: a bank position, a site, or a stock location."""
    out = {
        "status": s.status,
        "customer": party_ref(db.get(Party, s.party_id)) if s.party_id else None,
    }
    row = db.execute(select(BankPosition, BatteryBank).join(BatteryBank, BatteryBank.id == BankPosition.bank_id)
                     .where(BankPosition.serial_id == s.id, BankPosition.ended_at.is_(None))).first()  # fmt: skip
    if row:
        bp, b = row
        out |= {
            "bank": {"id": b.id, "number": b.number, "name": b.name, "status": b.status},
            "position": bp.position,
        }
    if s.site_id:
        out["site"] = site_ref(db, db.get(Site, s.site_id))
    if s.location_id:
        out["location"] = label(db, db.get(Location, s.location_id))
    if p.can("warranty.view"):
        out["warranty"] = warranty_of(db, s.id)
    if p.can("amc.view"):
        out["amc"] = [
            {"id": c["id"], "number": c["number"], "ends_on": c["ends_on"]} for c in amc_of(db, s)
        ]
    return out


@router.get("/serial-labels")
def labels(ids: Annotated[str, Query(max_length=4000)], _: require("stock.view"), db: DB) -> dict:
    """Printable labels: up to 100 at a time, each with its QR code as an SVG image."""
    try:
        wanted = list(dict.fromkeys(uuid.UUID(x) for x in ids.split(",") if x))
    except ValueError:
        raise HTTPException(422, "ids: serial ids separated by commas") from None
    if not 1 <= len(wanted) <= 100:
        raise HTTPException(422, "ids: between 1 and 100 serials")
    rows = {s.id: (s, i) for s, i in db.execute(select(Serial, Item).join(Item, Item.id == Serial.item_id)
                                                .where(Serial.id.in_(wanted)))}  # fmt: skip
    out = []
    for sid in wanted:
        if sid in rows:
            s, i = rows[sid]
            url = qr_url(s)
            qr = segno.make_qr(url, error="m").svg_data_uri(scale=4, border=2)
            out.append({"id": s.id, "serial": s.serial, "item": item_ref(i), "supplier_serial": s.supplier_serial,
                        "url": url, "qr": qr})  # fmt: skip
    return {"items": out, "public_base_url": get_settings().public_base_url}
