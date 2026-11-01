"""Phase 7 notifications: what happened, told to the people who need to act on it.

Events come from two places:
- the audit log: every recorded action passes `on_audit`, and those in ACTIONS queue a job (in the
  same transaction, so a rolled-back action notifies nobody). The worker turns the job into
  notifications after the commit, so building them can never break or slow down the action itself.
- a check of what is late or due, run hourly by the worker: stock below minimum, late supplier
  deliveries and assembly orders, maintenance visits coming up or overdue, contracts ending. A
  reminder repeats every `reminder_repeat_days` while the cause lasts; once something is
  `escalate_after_days` late it also goes to the members of the escalation role, if one is set.

Each event becomes an in-system notification for the people whose permissions (or assignment)
cover it, never the person who did it. Staff who ask for it get an email too. A customer who agreed
to updates gets a message about their own service request or warranty registration only.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import audit
from app.auth import DB, CurrentPrincipal
from app.config import get_settings
from app.jobs import enqueue, handler, periodic
from app.masters import Limit, Offset, rules
from app.messaging import EVENTS, queue, render, wording
from app.models import (
    AmcContract,
    AssemblyOrder,
    Company,
    Dispatch,
    Item,
    MessageProvider,
    Notification,
    Party,
    Project,
    PurchaseOrder,
    Quotation,
    QuotationRevision,
    RolePermission,
    SalesOrder,
    Serial,
    ServiceRequest,
    Site,
    SiteVisit,
    StockAdjustment,
    User,
    UserRole,
    WarrantyRegistration,
    utcnow,
)

router = APIRouter(tags=["notifications"])


def holders(db: Session, *perms: str) -> set[uuid.UUID]:
    """Active people holding any of these permissions."""
    return set(db.scalars(select(UserRole.user_id).join(RolePermission, RolePermission.role_id == UserRole.role_id)
                          .join(User, User.id == UserRole.user_id).where(RolePermission.permission.in_(perms), User.active)))  # fmt: skip


def members(db: Session, role_id) -> set[uuid.UUID]:
    if not role_id:
        return set()
    return set(db.scalars(select(UserRole.user_id).join(User, User.id == UserRole.user_id)
                          .where(UserRole.role_id == uuid.UUID(str(role_id)), User.active)))  # fmt: skip


def emit(db: Session, event: str, key: str, values: dict, link: str, users, *,
         entity: tuple[str, object] = ("", ""), actor_id=None, customer: tuple[str, bool] | None = None) -> None:  # fmt: skip
    """Notify `users` (minus the actor) and, with consent, the customer at `customer[0]`."""
    company = db.get(Company, 1)
    v = {k: "" for k in EVENTS[event].vars} | {
        k: "" if x is None else str(x) for k, x in values.items()
    }
    v["company"] = company.name if company else "SMET"
    ent = (entity[0], str(entity[1]))
    staff = wording(db, event, "in_app")
    ids = {u for u in users if u} - {actor_id}
    for u in db.scalars(select(User).where(User.id.in_(ids), User.active)) if ids else []:
        if event in (u.muted_events or []):
            continue
        if staff and staff.active:
            db.execute(insert(Notification).values(id=uuid.uuid4(), user_id=u.id, event=event, key=key[:200],
                                                   title=render(staff.subject, v)[:200], body=render(staff.body, v), link=link[:200])
                       .on_conflict_do_nothing())  # fmt: skip
        if u.notify_email:
            queue(db, event=event, channel="email", to=u.email, key=f"{key}:{u.id}",
                  values=v | {"url": get_settings().public_base_url.rstrip("/") + link}, user_id=u.id, entity=ent)  # fmt: skip
    if customer and customer[1] and customer[0] and EVENTS[event].customer:
        to = customer[0]
        if "@" in to:
            queue(
                db, event=event, channel="email", to=to, key=f"{key}:customer", values=v, entity=ent
            )
        else:  # WhatsApp when it is set up with an approved template, else SMS
            wa = wording(db, event, "whatsapp")
            p = db.get(MessageProvider, "whatsapp")
            channel = (
                "whatsapp"
                if p and p.mode != "off" and wa and wa.active and wa.provider_template
                else "sms"
            )
            queue(
                db, event=event, channel=channel, to=to, key=f"{key}:customer", values=v, entity=ent
            )


# ---------- events from the audit log ----------


def _po(db, a):
    o = db.get(PurchaseOrder, a["id"])
    link = f"/purchasing/orders/{o.id}"
    if a["action"] == "purchase_order.submit":
        if o.status == "submitted":
            emit(db, "approval.requested", a["key"], {"number": o.number, "summary": a["summary"], "by": a["by"]},
                 link, holders(db, "purchasing.approve"), entity=("purchase_order", o.id), actor_id=a["actor"])  # fmt: skip
        return
    emit(db, "approval.decided", a["key"], {"number": o.number, "summary": a["summary"], "by": a["by"], "reason": a["reason"]},
         link, {o.created_by, o.submitted_by}, entity=("purchase_order", o.id), actor_id=a["actor"])  # fmt: skip


def _adjustment(db, a):
    x = db.get(StockAdjustment, a["id"])
    if a["action"] == "adjustment.request":
        emit(db, "approval.requested", a["key"], {"number": x.number, "summary": a["summary"], "by": a["by"]},
             "/stock/corrections", holders(db, "adjustments.approve"), entity=("adjustment", x.id), actor_id=a["actor"])  # fmt: skip
    else:
        emit(db, "approval.decided", a["key"], {"number": x.number, "summary": a["summary"], "by": a["by"], "reason": a["reason"]},
             "/stock/corrections", {x.requested_by}, entity=("adjustment", x.id), actor_id=a["actor"])  # fmt: skip


def _quotation(db, a):
    q = db.get(Quotation, a["id"])
    r = db.scalar(select(QuotationRevision).where(QuotationRevision.quotation_id == q.id).order_by(QuotationRevision.rev.desc()).limit(1))  # fmt: skip
    link = f"/sales/quotations/{q.id}"
    if a["action"] == "quotation.submit":
        if r and r.status == "submitted":
            emit(db, "approval.requested", a["key"], {"number": q.number, "summary": a["summary"], "by": a["by"]},
                 link, holders(db, "sales.approve"), entity=("quotation", q.id), actor_id=a["actor"])  # fmt: skip
        return
    emit(db, "approval.decided", a["key"], {"number": q.number, "summary": a["summary"], "by": a["by"], "reason": a["reason"]},
         link, {q.created_by, r.submitted_by if r else None}, entity=("quotation", q.id), actor_id=a["actor"])  # fmt: skip


def _project(db, a):
    pr = db.get(Project, a["id"])
    link = f"/projects/{pr.id}"
    if a["action"] in ("sheet.submit", "project_change.request"):
        emit(db, "approval.requested", a["key"], {"number": pr.number, "summary": a["summary"], "by": a["by"]},
             link, holders(db, "projects.approve"), entity=("project", pr.id), actor_id=a["actor"])  # fmt: skip
    else:
        emit(db, "approval.decided", a["key"], {"number": pr.number, "summary": a["summary"], "by": a["by"], "reason": a["reason"]},
             link, {pr.manager_id, pr.created_by}, entity=("project", pr.id), actor_id=a["actor"])  # fmt: skip


def _warranty(db, a):
    r = db.get(WarrantyRegistration, a["id"])
    s = db.get(Serial, r.serial_id)
    link, ent = f"/warranty/{r.id}", ("warranty_registration", r.id)
    contact = (r.customer_contact, r.updates_consent)
    if a["action"] == "warranty.register":
        if r.status == "pending":
            emit(db, "approval.requested", a["key"], {"number": r.number, "summary": a["summary"], "by": a["by"]},
                 link, holders(db, "warranty.approve"), entity=ent, actor_id=a["actor"])  # fmt: skip
    elif a["action"] == "warranty.correction_requested":
        emit(db, "warranty.missing_info", a["key"], {"number": r.number, "serial": s.serial, "reason": a["reason"]},
             link, {r.submitted_by}, entity=ent, actor_id=a["actor"], customer=contact)  # fmt: skip
    else:
        outcome = "approved" if a["action"] == "warranty.approve" else "not approved"
        emit(db, "warranty.outcome", a["key"], {"number": r.number, "serial": s.serial, "outcome": outcome,
                                                "expiry": r.expiry_date if outcome == "approved" else "", "reason": a["reason"]},
             link, {r.submitted_by}, entity=ent, actor_id=a["actor"], customer=contact)  # fmt: skip


def _service(db, a):
    sr = db.get(ServiceRequest, a["id"])
    link, ent = f"/service/{sr.id}", ("service_request", sr.id)
    if a["action"] == "service.open":
        emit(db, "service.status", a["key"], {"number": sr.number, "status": "received", "summary": a["summary"]},
             link, holders(db, "service.create"), entity=ent, actor_id=a["actor"],
             customer=(sr.contact_phone, sr.updates_consent))  # fmt: skip
        return
    to = (a["changes"].get("status") or [None, sr.status])[1]
    emit(db, "service.status", a["key"], {"number": sr.number, "status": str(to).replace("_", " "), "summary": a["summary"]},
         link, {sr.coordinator_id, sr.created_by}, entity=ent, actor_id=a["actor"],
         customer=(sr.contact_phone, sr.updates_consent))  # fmt: skip


def _dispatch(db, a):
    d = db.get(Dispatch, a["id"])
    so = db.get(SalesOrder, d.order_id)
    pr = db.get(Project, so.project_id) if so.project_id else None
    emit(db, "readiness.changed", a["key"], {"number": d.number, "summary": a["summary"]}, f"/dispatch/{d.id}",
         {so.created_by, pr.manager_id if pr else None}, entity=("dispatch", d.id), actor_id=a["actor"])  # fmt: skip


def _visit(db, a):
    v = db.get(SiteVisit, a["id"])
    if v.stage not in ("installation", "commissioning", "handover"):
        return
    pr = db.get(Project, db.get(Site, v.site_id).project_id)
    emit(db, "readiness.changed", a["key"], {"number": v.number, "summary": a["summary"]}, f"/installation/visits/{v.id}",
         {pr.manager_id, v.created_by}, entity=("site_visit", v.id), actor_id=a["actor"])  # fmt: skip


ACTIONS = {
    "purchase_order.submit": _po, "purchase_order.approve": _po, "purchase_order.reject": _po,
    "adjustment.request": _adjustment, "adjustment.approve": _adjustment, "adjustment.reject": _adjustment,
    "quotation.submit": _quotation, "quotation.approve": _quotation, "quotation.reject": _quotation,
    "sheet.submit": _project, "sheet.approved": _project, "sheet.rejected": _project,
    "project_change.request": _project, "project_change.approved": _project, "project_change.rejected": _project,
    "warranty.register": _warranty, "warranty.approve": _warranty, "warranty.reject": _warranty,
    "warranty.correction_requested": _warranty,
    "service.open": _service, "service.status": _service, "service.close": _service,
    "dispatch.send": _dispatch, "dispatch.deliver": _dispatch, "visit.complete": _visit,
}  # fmt: skip


def on_audit(db: Session, actor, action: str, entity_type: str, entity_id, summary: str,
             changes: dict, reason: str) -> None:  # fmt: skip
    if action in ACTIONS:
        enqueue(db, "notify.audit", {"action": action, "id": str(entity_id), "summary": summary, "reason": reason,
                                     "changes": changes, "actor": str(actor.id) if actor else None,
                                     "by": actor.name if actor else "SMET", "key": f"{action}:{uuid.uuid4().hex}"})  # fmt: skip


audit.LISTENERS.append(on_audit)


@handler("notify.audit")
def audit_job(db: Session, a: dict) -> None:
    a = a | {"id": uuid.UUID(a["id"]), "actor": uuid.UUID(a["actor"]) if a["actor"] else None}
    ACTIONS[a["action"]](db, a)
    db.commit()


# ---------- what is late or due ----------


def _bucket(db: Session, today: date) -> int:
    """Same number for `reminder_repeat_days` days: the reminder's key, so it repeats on schedule."""
    return today.toordinal() // max(int(rules(db)["reminder_repeat_days"]), 1)


def _escalated(db: Session, days_late: int) -> set[uuid.UUID]:
    r = rules(db)
    after = r["escalate_after_days"]
    return (
        members(db, r["escalation_role_id"])
        if after is not None and days_late >= int(after)
        else set()
    )


def check_due(db: Session, today: date | None = None) -> None:
    from app.inventory import summaries  # noqa: PLC0415  (inventory imports a lot)
    from app.warranty import add_months  # noqa: PLC0415

    today = today or date.today()
    b = _bucket(db, today)
    r = rules(db)
    buyers = holders(db, "purchasing.create")
    items = db.scalars(select(Item).where(Item.min_stock > 0, Item.active)).all()
    for s in summaries(db, items) if buyers and items else []:
        if s["low"]:
            emit(db, "stock.low", f"stock.low:{s['id']}:{b}", {"item": f"{s['code']} {s['name']}", "free": s["free"],
                                                                "minimum": s["min_stock"]}, "/stock", buyers, entity=("item", s["id"]))  # fmt: skip
    for o in db.scalars(select(PurchaseOrder).where(PurchaseOrder.status.in_(("approved", "partly_received")),
                                                    PurchaseOrder.expected_arrival < today)):  # fmt: skip
        late = (today - o.expected_arrival).days
        emit(db, "procurement.delayed", f"po.late:{o.id}:{b}", {"number": o.number, "supplier": db.get(Party, o.supplier_id).name,
                                                                 "expected": o.expected_arrival, "days_late": late},
             f"/purchasing/orders/{o.id}", {o.created_by} | holders(db, "receipts.create") | _escalated(db, late),
             entity=("purchase_order", o.id))  # fmt: skip
    for ao in db.scalars(select(AssemblyOrder).where(AssemblyOrder.status.in_(("released", "in_progress")),
                                                     AssemblyOrder.target_date < today)):  # fmt: skip
        late = (today - ao.target_date).days
        emit(db, "assembly.delayed", f"ao.late:{ao.id}:{b}", {"number": ao.number, "item": db.get(Item, ao.item_id).name,
                                                               "target": ao.target_date, "days_late": late},
             f"/assembly/orders/{ao.id}", {ao.responsible_id} | holders(db, "assembly.create") | _escalated(db, late),
             entity=("assembly_order", ao.id))  # fmt: skip
    planners = holders(db, "amc.create")
    notice = int(r["amc_visit_notice_days"])
    open_ = SiteVisit.status.in_(("scheduled", "started"))
    for v in db.scalars(select(SiteVisit).where(SiteVisit.amc_id.is_not(None), open_)):
        c = db.get(AmcContract, v.amc_id)
        vals = {
            "number": v.number,
            "contract": c.number,
            "customer": db.get(Party, c.customer_id).name,
            "date": v.scheduled_on,
        }
        if v.scheduled_on < today:
            late = (today - v.scheduled_on).days
            emit(db, "maintenance.overdue", f"visit.late:{v.id}:{b}", vals | {"days_late": late}, f"/installation/visits/{v.id}",
                 set(v.technician_ids) | planners | _escalated(db, late), entity=("site_visit", v.id))  # fmt: skip
        elif (v.scheduled_on - today).days <= notice:
            emit(db, "amc.visit_upcoming", f"visit.soon:{v.id}:{v.scheduled_on}", vals, f"/installation/visits/{v.id}",
                 set(v.technician_ids) | planners, entity=("site_visit", v.id))  # fmt: skip
    # A contract whose next visit is due and none is planned: overdue maintenance too.
    last_done = dict(db.execute(select(SiteVisit.amc_id, func.max(SiteVisit.done_on))
                                .where(SiteVisit.amc_id.is_not(None), SiteVisit.status == "completed").group_by(SiteVisit.amc_id)).all())  # fmt: skip
    planned = set(db.scalars(select(SiteVisit.amc_id).where(SiteVisit.amc_id.is_not(None), open_)))
    ending = int(r["amc_renewal_notice_days"])
    for c in db.scalars(select(AmcContract).where(AmcContract.status == "active")):
        customer = db.get(Party, c.customer_id).name
        due = add_months(last_done.get(c.id) or c.starts_on, c.visit_every_months)
        if c.id not in planned and due < today <= c.ends_on:
            late = (today - due).days
            emit(db, "maintenance.overdue", f"amc.due:{c.id}:{due}:{b}", {"number": c.number, "contract": c.number, "customer": customer,
                                                                          "date": due, "days_late": late},
                 f"/amc/{c.id}", planners | _escalated(db, late), entity=("amc_contract", c.id))  # fmt: skip
        if today <= c.ends_on and (c.ends_on - today).days <= ending:
            emit(db, "amc.expiring", f"amc.ends:{c.id}:{b}", {"number": c.number, "customer": customer, "ends": c.ends_on},
                 f"/amc/{c.id}", planners, entity=("amc_contract", c.id))  # fmt: skip
    db.commit()


@periodic(3600)
def due_job(db: Session) -> None:
    check_due(db)


# ---------- the inbox ----------


def _row(n: Notification) -> dict:
    return {"id": n.id, "event": n.event, "title": n.title, "body": n.body, "link": n.link,
            "read": n.read_at is not None, "created_at": n.created_at}  # fmt: skip


@router.get("/notifications")
def my_notifications(p: CurrentPrincipal, db: DB, unread: bool = False,
                     limit: Limit = 30, offset: Offset = 0) -> dict:  # fmt: skip
    mine = select(Notification).where(Notification.user_id == p.user.id)
    q = mine.where(Notification.read_at.is_(None)) if unread else mine
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    count_unread = db.scalar(select(func.count()).where(Notification.user_id == p.user.id, Notification.read_at.is_(None)))  # fmt: skip
    rows = db.scalars(q.order_by(Notification.created_at.desc()).limit(limit).offset(offset))
    return {"items": [_row(n) for n in rows], "total": total, "unread": count_unread}


class ReadIn(BaseModel):
    ids: list[uuid.UUID] = Field(default=[], max_length=200)
    all: bool = False


@router.post("/notifications/read")
def mark_read(body: ReadIn, p: CurrentPrincipal, db: DB) -> dict:
    q = update(Notification).where(
        Notification.user_id == p.user.id, Notification.read_at.is_(None)
    )
    if not body.all:
        q = q.where(Notification.id.in_(body.ids))
    db.execute(q.values(read_at=utcnow()))
    db.commit()
    return {"unread": db.scalar(select(func.count()).where(Notification.user_id == p.user.id, Notification.read_at.is_(None)))}  # fmt: skip


@router.get("/notifications/preferences")
def my_preferences(p: CurrentPrincipal) -> dict:
    return {"notify_email": p.user.notify_email, "muted_events": p.user.muted_events or [],
            "events": [{"key": k, "label": e.label} for k, e in EVENTS.items()]}  # fmt: skip


class PrefsIn(BaseModel):
    notify_email: bool
    muted_events: Annotated[list[str], Field(max_length=len(EVENTS))] = []


@router.put("/notifications/preferences")
def save_preferences(body: PrefsIn, p: CurrentPrincipal, db: DB) -> dict:
    u = db.get(User, p.user.id, with_for_update=True)
    u.notify_email, u.muted_events = body.notify_email, sorted(set(body.muted_events) & set(EVENTS))
    db.commit()
    return {"notify_email": u.notify_email, "muted_events": u.muted_events}
