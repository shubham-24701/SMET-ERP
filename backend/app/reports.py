"""Phase 7 dashboards, reports and exports, read straight from the business records.

Every figure on a dashboard is the row count of a report with preset filters, so a number can
always be opened to the rows behind it; nothing is typed in, sampled or estimated. Each report needs
its area's view permission; downloading one (CSV or Excel) also needs `reports.export` and is
audited with the filters used. Global search finds batteries, customers, projects and documents the
person may see.
"""

import csv
import io
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from openpyxl import Workbook
from pydantic import BaseModel, StringConstraints
from sqlalchemy import DateTime, Select, and_, case, exists, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.admin import csv_cell
from app.audit import record
from app.auth import DB, CurrentPrincipal, Principal, require
from app.inventory import conflict
from app.masters import Limit, NumbersRoute, Offset, rules
from app.models import (
    AmcContract,
    AssemblyOrder,
    BatteryBank,
    Dispatch,
    Inspection,
    Item,
    Location,
    Party,
    Project,
    PurchaseOrder,
    PurchaseOrderLine,
    Quotation,
    QuotationLine,
    QuotationRevision,
    SalesOrder,
    SalesOrderLine,
    Serial,
    ServiceRequest,
    Site,
    SiteVisit,
    StockAdjustment,
    StockBalance,
    StockMove,
    User,
    Warehouse,
    WarrantyRegistration,
)

router = APIRouter(tags=["reports"], route_class=NumbersRoute)
EXPORT_LIMIT = 10_000
ZERO = Decimal(0)


class Filters(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    item: uuid.UUID | None = None
    serial: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] | None = None
    customer: uuid.UUID | None = None
    distributor: uuid.UUID | None = None
    supplier: uuid.UUID | None = None
    warehouse: uuid.UUID | None = None
    project: uuid.UUID | None = None
    status: Annotated[str, StringConstraints(max_length=200, pattern=r"^[a-z_,]*$")] | None = None
    flag: Annotated[str, StringConstraints(max_length=20, pattern=r"^[a-z_]*$")] | None = None


FILTER_NAMES = [k for k in Filters.model_fields if k != "flag"]


@dataclass(frozen=True)
class Report:
    title: str
    area: str
    perm: str
    columns: tuple[tuple[str, str], ...]  # (key, heading)
    filters: tuple[str, ...]
    build: Callable[[Session, Filters], Select | list[dict]]
    statuses: tuple[str, ...] = ()
    flags: dict[str, str] | None = None  # name -> what it shows
    date_label: str = "Created"
    link: str = ""  # web path of a row, with {id}


def _between(col, f: Filters):
    out = []
    if f.date_from:
        out.append(col >= f.date_from)
    if f.date_to:
        out.append(
            col < f.date_to + timedelta(days=1)
            if isinstance(col.type, DateTime)
            else col <= f.date_to
        )
    return out


def _statuses(f: Filters) -> list[str]:
    return [s for s in (f.status or "").split(",") if s]


def _today() -> date:
    return date.today()


# ---------- procurement ----------


def _purchase_orders(db, f):
    lines = (select(PurchaseOrderLine.po_id, func.sum(PurchaseOrderLine.qty_ordered).label("ordered"),
                    func.sum(PurchaseOrderLine.qty_received).label("received"),
                    func.sum(PurchaseOrderLine.qty_accepted).label("accepted"),
                    func.sum(PurchaseOrderLine.qty_rejected).label("rejected"))
             .group_by(PurchaseOrderLine.po_id).subquery())  # fmt: skip
    late = case((and_(PurchaseOrder.status.in_(("approved", "partly_received")), PurchaseOrder.expected_arrival < _today()),
                 _today() - PurchaseOrder.expected_arrival), else_=None)  # fmt: skip
    q = (select(PurchaseOrder.id, PurchaseOrder.number, Party.name.label("supplier"), PurchaseOrder.status,
                PurchaseOrder.expected_arrival, late.label("days_late"), lines.c.ordered, lines.c.received,
                lines.c.accepted, lines.c.rejected, func.date(PurchaseOrder.created_at).label("created"))
         .join(Party, Party.id == PurchaseOrder.supplier_id).outerjoin(lines, lines.c.po_id == PurchaseOrder.id)
         .where(*_between(PurchaseOrder.created_at, f)).order_by(PurchaseOrder.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(PurchaseOrder.status.in_(st))
    if f.supplier:
        q = q.where(PurchaseOrder.supplier_id == f.supplier)
    if f.item:
        q = q.where(
            exists().where(
                PurchaseOrderLine.po_id == PurchaseOrder.id, PurchaseOrderLine.item_id == f.item
            )
        )
    if f.flag == "late":
        q = q.where(
            PurchaseOrder.status.in_(("approved", "partly_received")),
            PurchaseOrder.expected_arrival < _today(),
        )
    return q


def _rejected(db, f):
    q = (select(PurchaseOrder.id, PurchaseOrder.number, Party.name.label("supplier"), Item.code.label("item_code"),
                Item.name.label("item"), PurchaseOrderLine.qty_received.label("received"),
                PurchaseOrderLine.qty_rejected.label("rejected"), PurchaseOrderLine.qty_held.label("held"),
                PurchaseOrderLine.qty_returned.label("returned"), func.date(PurchaseOrder.created_at).label("created"))
         .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id).join(Party, Party.id == PurchaseOrder.supplier_id)
         .join(Item, Item.id == PurchaseOrderLine.item_id)
         .where(or_(PurchaseOrderLine.qty_rejected > 0, PurchaseOrderLine.qty_held > 0), *_between(PurchaseOrder.created_at, f))
         .order_by(PurchaseOrder.created_at.desc()))  # fmt: skip
    if f.supplier:
        q = q.where(PurchaseOrder.supplier_id == f.supplier)
    if f.item:
        q = q.where(PurchaseOrderLine.item_id == f.item)
    return q


# ---------- stock ----------


def _stock(db, f):
    from app.inventory import summaries  # noqa: PLC0415  (avoids a circular import at startup)

    q = select(Item).where(Item.active).order_by(Item.code)
    if f.item:
        q = q.where(Item.id == f.item)
    if f.flag == "low":
        q = q.where(Item.min_stock > 0)
    rows = summaries(db, db.scalars(q).all())
    if f.flag == "low":
        rows = [r for r in rows if r["low"]]
    return [{"id": r["id"], "item_code": r["code"], "item": r["name"], "on_hand": r["on_hand"], "available": r["available"],
             "reserved": r["reserved"], "free": r["free"], "on_order": r["on_order"], "minimum": r["min_stock"],
             "held": r["quarantine"] + r["inspection"], "repair": r["repair"], "returns": r["returns"],
             "low": "yes" if r["low"] else ""} for r in rows]  # fmt: skip


def _repair_returns(db, f):
    q = (select(Item.id, Item.code.label("item_code"), Item.name.label("item"), Warehouse.code.label("warehouse"),
                Location.code.label("location"), Location.purpose, func.sum(StockBalance.qty).label("qty"))
         .join(Location, Location.id == StockBalance.location_id).join(Warehouse, Warehouse.id == Location.warehouse_id)
         .join(Item, Item.id == StockBalance.item_id)
         .where(Location.purpose.in_(("repair", "returns", "quarantine", "rejected")), StockBalance.qty > 0)
         .group_by(Item.id, Item.code, Item.name, Warehouse.code, Location.code, Location.purpose)
         .order_by(Item.code, Warehouse.code, Location.code))  # fmt: skip
    if f.item:
        q = q.where(Item.id == f.item)
    if f.warehouse:
        q = q.where(Warehouse.id == f.warehouse)
    if st := _statuses(f):
        q = q.where(Location.purpose.in_(st))
    return q


def _moves(db, f):
    src, dst = aliased(Location), aliased(Location)
    q = (select(StockMove.id, StockMove.at, Item.code.label("item_code"), Item.name.label("item"), StockMove.qty,
                src.code.label("from_location"), dst.code.label("to_location"), Serial.serial, StockMove.lot,
                StockMove.kind.label("status"), StockMove.doc_number.label("document"), User.name.label("by"), StockMove.reason)
         .join(Item, Item.id == StockMove.item_id).outerjoin(src, src.id == StockMove.from_location_id)
         .outerjoin(dst, dst.id == StockMove.to_location_id).outerjoin(Serial, Serial.id == StockMove.serial_id)
         .outerjoin(User, User.id == StockMove.user_id).where(*_between(StockMove.at, f)).order_by(StockMove.id.desc()))  # fmt: skip
    if f.item:
        q = q.where(StockMove.item_id == f.item)
    if f.serial:
        q = q.where(Serial.serial == f.serial.upper())
    if f.warehouse:
        q = q.where(or_(src.warehouse_id == f.warehouse, dst.warehouse_id == f.warehouse))
    if st := _statuses(f):
        q = q.where(StockMove.kind.in_(st))
    return q


def _adjustments(db, f):
    req, dec = aliased(User), aliased(User)
    q = (select(StockAdjustment.id, StockAdjustment.number, StockAdjustment.status, StockAdjustment.reason,
                req.name.label("requested_by"), func.date(StockAdjustment.created_at).label("created"),
                dec.name.label("decided_by"), func.date(StockAdjustment.decided_at).label("decided"))
         .join(req, req.id == StockAdjustment.requested_by).outerjoin(dec, dec.id == StockAdjustment.decided_by)
         .where(*_between(StockAdjustment.created_at, f)).order_by(StockAdjustment.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(StockAdjustment.status.in_(st))
    return q


# ---------- assembly ----------


def _assembly(db, f):
    built = (select(Serial.assembly_order_id, func.count().label("built"),
                    func.count().filter(Serial.status.in_(("available", "delivered", "installed", "commissioned", "in_service"))).label("passed"),
                    func.count().filter(Serial.status.in_(("on_hold", "rework"))).label("held"))
             .where(Serial.assembly_order_id.is_not(None)).group_by(Serial.assembly_order_id).subquery())  # fmt: skip
    late = case((and_(AssemblyOrder.status.in_(("released", "in_progress")), AssemblyOrder.target_date < _today()),
                 _today() - AssemblyOrder.target_date), else_=None)  # fmt: skip
    q = (select(AssemblyOrder.id, AssemblyOrder.number, Item.name.label("item"), AssemblyOrder.status,
                AssemblyOrder.qty_planned.label("planned"), func.coalesce(built.c.built, 0).label("built"),
                func.coalesce(built.c.passed, 0).label("passed"), func.coalesce(built.c.held, 0).label("on_hold_or_rework"),
                AssemblyOrder.target_date, late.label("days_late"), Warehouse.code.label("warehouse"),
                User.name.label("responsible"))
         .join(Item, Item.id == AssemblyOrder.item_id).join(Warehouse, Warehouse.id == AssemblyOrder.warehouse_id)
         .outerjoin(User, User.id == AssemblyOrder.responsible_id).outerjoin(built, built.c.assembly_order_id == AssemblyOrder.id)
         .where(*_between(AssemblyOrder.created_at, f)).order_by(AssemblyOrder.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(AssemblyOrder.status.in_(st))
    if f.item:
        q = q.where(AssemblyOrder.item_id == f.item)
    if f.warehouse:
        q = q.where(AssemblyOrder.warehouse_id == f.warehouse)
    if f.flag == "late":
        q = q.where(
            AssemblyOrder.status.in_(("released", "in_progress")),
            AssemblyOrder.target_date < _today(),
        )
    return q


def _holds(db, f):
    last = (
        select(Inspection.serial_id, func.max(Inspection.at).label("at"))
        .group_by(Inspection.serial_id)
        .subquery()
    )
    q = (select(Serial.id, Serial.serial, Item.name.label("item"), Inspection.result.label("status"), Inspection.at,
                AssemblyOrder.number.label("assembly_order"), User.name.label("inspector"), Inspection.observations)
         .join(last, last.c.serial_id == Serial.id).join(Inspection, and_(Inspection.serial_id == Serial.id, Inspection.at == last.c.at))
         .join(Item, Item.id == Serial.item_id).join(User, User.id == Inspection.inspector_id)
         .outerjoin(AssemblyOrder, AssemblyOrder.id == Inspection.assembly_order_id)
         .where(Inspection.result.in_(("on_hold", "rework")), Serial.status.in_(("on_hold", "rework")), *_between(Inspection.at, f))
         .order_by(Inspection.at))  # fmt: skip
    if f.item:
        q = q.where(Serial.item_id == f.item)
    if f.serial:
        q = q.where(Serial.serial == f.serial.upper())
    if st := _statuses(f):
        q = q.where(Inspection.result.in_(st))
    return q


# ---------- sales ----------


def _quotations(db, f):
    cur = (select(QuotationRevision.quotation_id, func.max(QuotationRevision.rev).label("rev"))
           .group_by(QuotationRevision.quotation_id).subquery())  # fmt: skip
    total = (select(QuotationLine.revision_id, func.sum(QuotationLine.qty * QuotationLine.unit_price * (100 - QuotationLine.discount_pct) / 100).label("total"))
             .group_by(QuotationLine.revision_id).subquery())  # fmt: skip
    q = (select(Quotation.id, Quotation.number, Party.name.label("customer"), Project.number.label("project"),
                QuotationRevision.rev, QuotationRevision.status, QuotationRevision.currency, total.c.total,
                QuotationRevision.valid_until, func.date(Quotation.created_at).label("created"))
         .join(cur, cur.c.quotation_id == Quotation.id)
         .join(QuotationRevision, and_(QuotationRevision.quotation_id == Quotation.id, QuotationRevision.rev == cur.c.rev))
         .join(Party, Party.id == Quotation.customer_id).outerjoin(Project, Project.id == Quotation.project_id)
         .outerjoin(total, total.c.revision_id == QuotationRevision.id)
         .where(*_between(Quotation.created_at, f)).order_by(Quotation.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(QuotationRevision.status.in_(st))
    if f.customer:
        q = q.where(Quotation.customer_id == f.customer)
    if f.project:
        q = q.where(Quotation.project_id == f.project)
    return q


def _sales_orders(db, f):
    lines = (select(SalesOrderLine.order_id, func.sum(SalesOrderLine.qty).label("ordered"),
                    func.sum(SalesOrderLine.qty_dispatched).label("dispatched"), func.sum(SalesOrderLine.qty_delivered).label("delivered"))
             .group_by(SalesOrderLine.order_id).subquery())  # fmt: skip
    q = (select(SalesOrder.id, SalesOrder.number, Party.name.label("customer"), Project.number.label("project"), SalesOrder.status,
                SalesOrder.delivery_date, lines.c.ordered, lines.c.dispatched, lines.c.delivered,
                (func.coalesce(lines.c.ordered, 0) - func.coalesce(lines.c.dispatched, 0)).label("pending"),
                func.date(SalesOrder.created_at).label("created"))
         .join(Party, Party.id == SalesOrder.customer_id).outerjoin(Project, Project.id == SalesOrder.project_id)
         .outerjoin(lines, lines.c.order_id == SalesOrder.id)
         .where(*_between(SalesOrder.created_at, f)).order_by(SalesOrder.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(SalesOrder.status.in_(st))
    if f.customer:
        q = q.where(SalesOrder.customer_id == f.customer)
    if f.project:
        q = q.where(SalesOrder.project_id == f.project)
    if f.item:
        q = q.where(
            exists().where(
                SalesOrderLine.order_id == SalesOrder.id, SalesOrderLine.item_id == f.item
            )
        )
    return q


def _dispatches(db, f):
    q = (select(Dispatch.id, Dispatch.number, SalesOrder.number.label("order"), Party.name.label("customer"), Dispatch.status,
                Dispatch.planned_date, Warehouse.code.label("warehouse"), Dispatch.destination, Dispatch.delivered_on)
         .join(SalesOrder, SalesOrder.id == Dispatch.order_id).join(Party, Party.id == SalesOrder.customer_id)
         .join(Warehouse, Warehouse.id == Dispatch.warehouse_id)
         .where(*_between(Dispatch.planned_date, f)).order_by(Dispatch.planned_date.desc().nulls_last()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(Dispatch.status.in_(st))
    if f.customer:
        q = q.where(SalesOrder.customer_id == f.customer)
    if f.warehouse:
        q = q.where(Dispatch.warehouse_id == f.warehouse)
    if f.project:
        q = q.where(SalesOrder.project_id == f.project)
    return q


# ---------- installation ----------


def _visits(stages):
    def build(db, f):
        late = case((and_(SiteVisit.status.in_(("scheduled", "started")), SiteVisit.scheduled_on < _today()),
                     _today() - SiteVisit.scheduled_on), else_=None)  # fmt: skip
        q = (select(SiteVisit.id, SiteVisit.number, SiteVisit.stage, Site.name.label("site"), Project.number.label("project"),
                    Party.name.label("customer"), SiteVisit.scheduled_on, SiteVisit.status, late.label("days_late"),
                    SiteVisit.done_on, AmcContract.number.label("contract"))
             .join(Site, Site.id == SiteVisit.site_id).join(Project, Project.id == Site.project_id)
             .join(Party, Party.id == Project.customer_id).outerjoin(AmcContract, AmcContract.id == SiteVisit.amc_id)
             .where(SiteVisit.stage.in_(stages), *_between(SiteVisit.scheduled_on, f)).order_by(SiteVisit.scheduled_on.desc()))  # fmt: skip
        if st := _statuses(f):
            q = q.where(SiteVisit.status.in_(st))
        if f.project:
            q = q.where(Project.id == f.project)
        if f.customer:
            q = q.where(Project.customer_id == f.customer)
        if f.flag == "late":
            q = q.where(
                SiteVisit.status.in_(("scheduled", "started")), SiteVisit.scheduled_on < _today()
            )
        if f.flag == "upcoming":
            days = int(rules(db)["amc_visit_notice_days"])
            q = q.where(SiteVisit.status.in_(("scheduled", "started")),
                        SiteVisit.scheduled_on.between(_today(), _today() + timedelta(days=days)))  # fmt: skip
        return q

    return build


def _banks(db, f):
    q = (select(BatteryBank.id, BatteryBank.number, BatteryBank.name, Site.name.label("site"), Project.number.label("project"),
                Party.name.label("customer"), BatteryBank.status, BatteryBank.installed_on, BatteryBank.commissioned_on,
                BatteryBank.handed_over_on)
         .join(Site, Site.id == BatteryBank.site_id).join(Project, Project.id == Site.project_id)
         .join(Party, Party.id == Project.customer_id).order_by(BatteryBank.number))  # fmt: skip
    if st := _statuses(f):
        q = q.where(BatteryBank.status.in_(st))
    if f.project:
        q = q.where(Project.id == f.project)
    if f.customer:
        q = q.where(Project.customer_id == f.customer)
    return q


# ---------- warranty ----------

REG_STATUS = case((and_(WarrantyRegistration.status == "approved", WarrantyRegistration.expiry_date < func.current_date()), "expired"),
                  (WarrantyRegistration.status == "approved", "active"), else_=WarrantyRegistration.status)  # fmt: skip


def _warranties(db, f):
    dist = aliased(Party)
    q = (select(WarrantyRegistration.id, WarrantyRegistration.number, Serial.serial, Item.name.label("item"),
                WarrantyRegistration.customer_name.label("customer"),
                func.coalesce(dist.name, WarrantyRegistration.distributor_name).label("distributor"),
                REG_STATUS.label("status"), WarrantyRegistration.route, WarrantyRegistration.start_date,
                WarrantyRegistration.expiry_date, func.date(WarrantyRegistration.created_at).label("created"))
         .join(Serial, Serial.id == WarrantyRegistration.serial_id).join(Item, Item.id == Serial.item_id)
         .outerjoin(dist, dist.id == WarrantyRegistration.distributor_id)
         .where(*_between(WarrantyRegistration.created_at, f)).order_by(WarrantyRegistration.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(REG_STATUS.in_(st))
    if f.item:
        q = q.where(Serial.item_id == f.item)
    if f.serial:
        q = q.where(Serial.serial == f.serial.upper())
    if f.customer:
        q = q.where(WarrantyRegistration.customer_id == f.customer)
    if f.distributor:
        q = q.where(WarrantyRegistration.distributor_id == f.distributor)
    if f.flag == "expiring":  # active and ending within 90 days
        q = q.where(WarrantyRegistration.status == "approved",
                    WarrantyRegistration.expiry_date.between(_today(), _today() + timedelta(days=90)))  # fmt: skip
    return q


# ---------- AMC and service ----------


def _amc(db, f):
    q = (select(AmcContract.id, AmcContract.number, Party.name.label("customer"), AmcContract.status, AmcContract.scope,
                AmcContract.starts_on, AmcContract.ends_on, AmcContract.visit_every_months, AmcContract.reference)
         .join(Party, Party.id == AmcContract.customer_id).where(*_between(AmcContract.ends_on, f))
         .order_by(AmcContract.ends_on))  # fmt: skip
    if st := _statuses(f):
        q = q.where(AmcContract.status.in_(st))
    if f.customer:
        q = q.where(AmcContract.customer_id == f.customer)
    if f.flag == "ending":
        days = int(rules(db)["amc_renewal_notice_days"])
        q = q.where(
            AmcContract.status == "active",
            AmcContract.ends_on.between(_today(), _today() + timedelta(days=days)),
        )
    return q


def _service(db, f):
    rep = aliased(Serial)
    q = (select(ServiceRequest.id, ServiceRequest.number, ServiceRequest.source, ServiceRequest.status, ServiceRequest.priority,
                Party.name.label("customer"), func.coalesce(Serial.serial, ServiceRequest.reported_serial).label("serial"),
                func.date(ServiceRequest.created_at).label("opened"),
                case((ServiceRequest.status != "closed", func.current_date() - func.date(ServiceRequest.created_at)), else_=None).label("open_days"),
                ServiceRequest.resolution, rep.serial.label("replacement"))
         .outerjoin(Party, Party.id == ServiceRequest.customer_id).outerjoin(Serial, Serial.id == ServiceRequest.serial_id)
         .outerjoin(rep, rep.id == ServiceRequest.replacement_serial_id)
         .where(*_between(ServiceRequest.created_at, f)).order_by(ServiceRequest.created_at.desc()))  # fmt: skip
    if st := _statuses(f):
        q = q.where(ServiceRequest.status.in_(st))
    if f.customer:
        q = q.where(ServiceRequest.customer_id == f.customer)
    if f.serial:
        q = q.where(
            or_(
                Serial.serial == f.serial.upper(),
                ServiceRequest.reported_serial == f.serial.upper(),
            )
        )
    if f.item:
        q = q.where(Serial.item_id == f.item)
    if f.project:
        q = q.where(ServiceRequest.site_id.in_(select(Site.id).where(Site.project_id == f.project)))
    if f.flag == "replaced":
        q = q.where(ServiceRequest.replacement_serial_id.is_not(None))
    return q


# ---------- traceability ----------


def _batteries(db, f):
    reg = (select(WarrantyRegistration.serial_id, WarrantyRegistration.distributor_id, REG_STATUS.label("warranty"),
                  WarrantyRegistration.expiry_date)
           .where(WarrantyRegistration.status.in_(("pending", "correction_required", "approved"))).subquery())  # fmt: skip
    q = (select(Serial.id, Serial.serial, Item.name.label("item"), Serial.status, Party.name.label("customer"),
                Site.name.label("site"), Project.number.label("project"), Warehouse.code.label("warehouse"),
                Location.code.label("location"), reg.c.warranty, reg.c.expiry_date.label("warranty_expiry"))
         .join(Item, Item.id == Serial.item_id).outerjoin(Party, Party.id == Serial.party_id)
         .outerjoin(Site, Site.id == Serial.site_id).outerjoin(Project, Project.id == Site.project_id)
         .outerjoin(Location, Location.id == Serial.location_id).outerjoin(Warehouse, Warehouse.id == Location.warehouse_id)
         .outerjoin(reg, reg.c.serial_id == Serial.id)
         .where(Item.kind.in_(("battery_model", "battery_unit"))).order_by(Serial.serial))  # fmt: skip
    if st := _statuses(f):
        q = q.where(Serial.status.in_(st))
    if f.item:
        q = q.where(Serial.item_id == f.item)
    if f.serial:
        q = q.where(Serial.serial.ilike(f"{f.serial.upper()}%"))
    if f.customer:
        q = q.where(Serial.party_id == f.customer)
    if f.distributor:
        q = q.where(reg.c.distributor_id == f.distributor)
    if f.project:
        q = q.where(Site.project_id == f.project)
    if f.warehouse:
        q = q.where(Location.warehouse_id == f.warehouse)
    return q


D, IT, S, C, W, P, ST = "date", "item", "serial", "customer", "warehouse", "project", "status"
REPORTS: dict[str, Report] = {
    "purchase_orders": Report("Purchase orders", "procurement", "purchasing.view",
        (("number", "Order"), ("supplier", "Supplier"), ("status", "Status"), ("expected_arrival", "Expected"), ("days_late", "Days late"),
         ("ordered", "Ordered"), ("received", "Received"), ("accepted", "Accepted"), ("rejected", "Rejected"), ("created", "Created")),
        (D, IT, "supplier", ST), _purchase_orders, ("draft", "submitted", "approved", "partly_received", "received", "closed", "rejected", "cancelled"),
        {"late": "Late deliveries only"}, link="/purchasing/orders/{id}"),
    "rejected_receipts": Report("Rejected and held quantities", "procurement", "purchasing.view",
        (("number", "Order"), ("supplier", "Supplier"), ("item_code", "Item"), ("item", "Name"), ("received", "Received"),
         ("rejected", "Rejected"), ("held", "On hold"), ("returned", "Returned"), ("created", "Ordered")),
        (D, IT, "supplier"), _rejected, link="/purchasing/orders/{id}"),
    "stock_position": Report("Stock position", "inventory", "stock.view",
        (("item_code", "Item"), ("item", "Name"), ("on_hand", "On hand"), ("available", "Available"), ("reserved", "Reserved"),
         ("free", "Free"), ("on_order", "On order"), ("minimum", "Minimum"), ("held", "Held / inspecting"), ("repair", "Repair"),
         ("returns", "Returned"), ("low", "Below minimum")),
        (IT,), _stock, flags={"low": "Below minimum only"}),
    "repair_returns": Report("Returned, repair and held stock", "inventory", "stock.view",
        (("item_code", "Item"), ("item", "Name"), ("warehouse", "Warehouse"), ("location", "Location"), ("purpose", "Kind"), ("qty", "Quantity")),
        (IT, W, ST), _repair_returns, ("repair", "returns", "quarantine", "rejected")),
    "stock_moves": Report("Stock movements", "inventory", "stock.view",
        (("at", "When"), ("item_code", "Item"), ("item", "Name"), ("qty", "Quantity"), ("from_location", "From"), ("to_location", "To"),
         ("serial", "Serial"), ("lot", "Lot"), ("status", "Kind"), ("document", "Document"), ("by", "By"), ("reason", "Reason")),
        (D, IT, S, W, ST), _moves, date_label="When"),
    "adjustments": Report("Stock corrections", "inventory", "stock.view",
        (("number", "Correction"), ("status", "Status"), ("reason", "Reason"), ("requested_by", "Asked by"), ("created", "Asked on"),
         ("decided_by", "Decided by"), ("decided", "Decided on")),
        (D, ST), _adjustments, ("pending", "approved", "rejected")),
    "assembly_orders": Report("Assembly orders", "assembly", "assembly.view",
        (("number", "Order"), ("item", "Battery"), ("status", "Status"), ("planned", "Planned"), ("built", "Built"), ("passed", "Passed"),
         ("on_hold_or_rework", "Hold / rework"), ("target_date", "Target"), ("days_late", "Days late"), ("warehouse", "Warehouse"),
         ("responsible", "Responsible")),
        (D, IT, W, ST), _assembly, ("draft", "released", "in_progress", "completed", "cancelled"), {"late": "Late only"},
        link="/assembly/orders/{id}"),
    "quality_holds": Report("Quality holds and rework", "assembly", "assembly.view",
        (("serial", "Serial"), ("item", "Battery"), ("status", "Result"), ("at", "Inspected"), ("assembly_order", "Assembly order"),
         ("inspector", "Inspector"), ("observations", "Observations")),
        (D, IT, S, ST), _holds, ("on_hold", "rework"), date_label="Inspected", link="/stock/serials/{id}"),
    "quotations": Report("Quotations", "sales", "sales.view",
        (("number", "Quotation"), ("customer", "Customer"), ("project", "Project"), ("rev", "Revision"), ("status", "Status"),
         ("currency", "Currency"), ("total", "Total"), ("valid_until", "Valid until"), ("created", "Created")),
        (D, C, P, ST), _quotations, ("draft", "submitted", "approved", "rejected", "revised", "accepted", "declined"),
        link="/sales/quotations/{id}"),
    "sales_orders": Report("Sales orders", "sales", "sales.view",
        (("number", "Order"), ("customer", "Customer"), ("project", "Project"), ("status", "Status"), ("delivery_date", "Delivery"),
         ("ordered", "Ordered"), ("dispatched", "Dispatched"), ("delivered", "Delivered"), ("pending", "Still to send"), ("created", "Created")),
        (D, IT, C, P, ST), _sales_orders, ("open", "partly_dispatched", "dispatched", "closed", "cancelled"), link="/sales/orders/{id}"),
    "dispatches": Report("Dispatches", "sales", "dispatch.view",
        (("number", "Dispatch"), ("order", "Order"), ("customer", "Customer"), ("status", "Status"), ("planned_date", "Planned"),
         ("warehouse", "Warehouse"), ("destination", "Destination"), ("delivered_on", "Delivered")),
        (D, C, W, P, ST), _dispatches, ("planned", "dispatched", "delivered", "cancelled"), date_label="Planned", link="/dispatch/{id}"),
    "site_visits": Report("Site visits", "installation", "installation.view",
        (("number", "Visit"), ("stage", "Stage"), ("site", "Site"), ("project", "Project"), ("customer", "Customer"),
         ("scheduled_on", "Scheduled"), ("status", "Status"), ("days_late", "Days late"), ("done_on", "Done")),
        (D, C, P, ST), _visits(("installation", "commissioning", "handover", "other")), ("scheduled", "started", "completed", "cancelled"),
        {"late": "Overdue only"}, date_label="Scheduled", link="/installation/visits/{id}"),
    "banks": Report("Battery banks", "installation", "installation.view",
        (("number", "Bank"), ("name", "Name"), ("site", "Site"), ("project", "Project"), ("customer", "Customer"), ("status", "Status"),
         ("installed_on", "Installed"), ("commissioned_on", "Commissioned"), ("handed_over_on", "Handed over")),
        (C, P, ST), _banks, ("planned", "installed", "commissioned", "handed_over"),
        link="/installation/banks/{id}"),
    "warranties": Report("Warranties", "warranty", "warranty.view",
        (("number", "Registration"), ("serial", "Serial"), ("item", "Battery"), ("customer", "Customer"), ("distributor", "Distributor"),
         ("status", "Status"), ("route", "Route"), ("start_date", "Starts"), ("expiry_date", "Expires"), ("created", "Registered")),
        (D, IT, S, C, "distributor", ST), _warranties, ("pending", "correction_required", "active", "expired", "rejected", "ended"),
        {"expiring": "Ending within 90 days"}, date_label="Registered", link="/warranty/{id}"),
    "maintenance_visits": Report("Maintenance visits", "service", "amc.view",
        (("number", "Visit"), ("contract", "Contract"), ("customer", "Customer"), ("site", "Site"), ("scheduled_on", "Scheduled"),
         ("status", "Status"), ("days_late", "Days late"), ("done_on", "Done")),
        (D, C, P, ST), _visits(("maintenance",)), ("scheduled", "started", "completed", "cancelled"),
        {"late": "Overdue only", "upcoming": "Coming up"}, date_label="Scheduled", link="/installation/visits/{id}"),
    "amc_contracts": Report("Maintenance contracts", "service", "amc.view",
        (("number", "Contract"), ("customer", "Customer"), ("status", "Status"), ("scope", "Covers"), ("starts_on", "Starts"),
         ("ends_on", "Ends"), ("visit_every_months", "Visit every (months)"), ("reference", "Reference")),
        (D, C, ST), _amc, ("draft", "active", "renewed", "cancelled"), {"ending": "Ending soon, to renew"}, date_label="Ends",
        link="/amc/{id}"),
    "service_requests": Report("Service requests", "service", "service.view",
        (("number", "Request"), ("source", "From"), ("status", "Status"), ("priority", "Priority"), ("customer", "Customer"),
         ("serial", "Serial"), ("opened", "Opened"), ("open_days", "Open for (days)"), ("resolution", "Resolution"),
         ("replacement", "Replacement")),
        (D, IT, S, C, P, ST), _service, ("new", "under_review", "scheduled", "in_progress", "pending_action", "closed"),
        {"replaced": "Replacements only"}, date_label="Opened", link="/service/{id}"),
    "batteries": Report("Battery traceability", "traceability", "stock.view",
        (("serial", "Serial"), ("item", "Battery"), ("status", "Status"), ("customer", "Customer"), ("site", "Site"),
         ("project", "Project"), ("warehouse", "Warehouse"), ("location", "Location"), ("warranty", "Warranty"),
         ("warranty_expiry", "Warranty ends")),
        (IT, S, C, "distributor", P, W, ST), _batteries,
        ("available", "on_hold", "rework", "awaiting_inspection", "delivered", "installed", "commissioned", "in_service", "removed",
         "in_repair", "written_off"), link="/stock/serials/{id}"),
}  # fmt: skip

OPEN_SR = "new,under_review,scheduled,in_progress,pending_action"
# Each dashboard figure: (label, report, filters). Its number is that report's row count.
AREAS: dict[str, tuple[str, list[tuple[str, str, dict]]]] = {
    "procurement": ("Procurement", [
        ("Open purchase orders", "purchase_orders", {"status": "submitted,approved,partly_received"}),
        ("Waiting for approval", "purchase_orders", {"status": "submitted"}),
        ("Expected receipts", "purchase_orders", {"status": "approved,partly_received"}),
        ("Late deliveries", "purchase_orders", {"flag": "late"}),
        ("Lines with rejected or held goods", "rejected_receipts", {})]),
    "inventory": ("Inventory", [
        ("Items below minimum", "stock_position", {"flag": "low"}),
        ("Returned, repair and held stock", "repair_returns", {}),
        ("Corrections waiting for approval", "adjustments", {"status": "pending"})]),
    "assembly": ("Assembly", [
        ("Planned orders", "assembly_orders", {"status": "draft,released"}),
        ("Work in progress", "assembly_orders", {"status": "in_progress"}),
        ("Late orders", "assembly_orders", {"flag": "late"}),
        ("Completed orders", "assembly_orders", {"status": "completed"}),
        ("Batteries on hold or in rework", "quality_holds", {})]),
    "sales": ("Sales", [
        ("Open quotations", "quotations", {"status": "draft,submitted,approved"}),
        ("Quotations waiting for approval", "quotations", {"status": "submitted"}),
        ("Orders with quantities to send", "sales_orders", {"status": "open,partly_dispatched"}),
        ("Dispatches planned", "dispatches", {"status": "planned"}),
        ("Dispatches on the way", "dispatches", {"status": "dispatched"})]),
    "installation": ("Installation", [
        ("Visits scheduled", "site_visits", {"status": "scheduled,started"}),
        ("Overdue visits", "site_visits", {"flag": "late"}),
        ("Visits completed", "site_visits", {"status": "completed"}),
        ("Banks to commission", "banks", {"status": "installed"}),
        ("Banks to hand over", "banks", {"status": "commissioned"})]),
    "warranty": ("Warranty", [
        ("Registrations to decide", "warranties", {"status": "pending"}),
        ("Waiting for corrections", "warranties", {"status": "correction_required"}),
        ("Active warranties", "warranties", {"status": "active"}),
        ("Ending within 90 days", "warranties", {"flag": "expiring"}),
        ("Expired", "warranties", {"status": "expired"}),
        ("Rejected", "warranties", {"status": "rejected"})]),
    "service": ("Maintenance and service", [
        ("Maintenance visits coming up", "maintenance_visits", {"flag": "upcoming"}),
        ("Overdue maintenance visits", "maintenance_visits", {"flag": "late"}),
        ("Open service requests", "service_requests", {"status": OPEN_SR}),
        ("Replacements", "service_requests", {"flag": "replaced"}),
        ("Contracts to renew", "amc_contracts", {"flag": "ending"})]),
    "traceability": ("Battery traceability", [
        ("Batteries in stock", "batteries", {"status": "available"}),
        ("Batteries at customers", "batteries", {"status": "delivered,installed,commissioned,in_service"}),
        ("Batteries in repair", "batteries", {"status": "in_repair"})]),
    "exceptions": ("Exceptions", [
        ("Late supplier deliveries", "purchase_orders", {"flag": "late"}),
        ("Items below minimum", "stock_position", {"flag": "low"}),
        ("Late assembly orders", "assembly_orders", {"flag": "late"}),
        ("Quality holds and rework", "quality_holds", {}),
        ("Overdue site visits", "site_visits", {"flag": "late"}),
        ("Installed, not commissioned", "banks", {"status": "installed"}),
        ("Commissioned, not handed over", "banks", {"status": "commissioned"}),
        ("Overdue maintenance visits", "maintenance_visits", {"flag": "late"}),
        ("Registrations waiting for corrections", "warranties", {"status": "correction_required"}),
        ("Service requests waiting on something", "service_requests", {"status": "pending_action"})]),
}  # fmt: skip


def _report(name: str, p: Principal) -> Report:
    r = REPORTS.get(name)
    if r is None:
        raise HTTPException(404, "There is no such report")
    if r.perm not in p.permissions:
        raise HTTPException(403, "You don't have permission to see this report")
    return r


def _checked(r: Report, f: Filters) -> None:
    allowed = {"date_from" if x == "date" else x for x in r.filters} | (
        {"date_to"} if "date" in r.filters else set()
    )
    given = {k for k in FILTER_NAMES if getattr(f, k) not in (None, "")}
    if extra := given - allowed:
        raise HTTPException(422, f"This report can't be filtered by {', '.join(sorted(extra))}")
    if f.flag and f.flag not in (r.flags or {}):
        raise HTTPException(422, "Unknown option for this report")
    if bad := set(_statuses(f)) - set(r.statuses):
        raise HTTPException(422, f"Unknown status {', '.join(sorted(bad))}")


def run(
    db: Session, r: Report, f: Filters, limit: int | None, offset: int = 0
) -> tuple[list[dict], int]:
    q = r.build(db, f)
    if isinstance(q, list):
        return q[offset : offset + limit if limit else None], len(q)
    total = db.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    if limit:
        q = q.limit(limit).offset(offset)
    return [dict(m) for m in db.execute(q).mappings()], total


def count(db: Session, r: Report, f: Filters) -> int:
    q = r.build(db, f)
    return (
        len(q)
        if isinstance(q, list)
        else db.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    )


def _describe(name: str, r: Report) -> dict:
    return {"name": name, "title": r.title, "area": r.area, "columns": [{"key": k, "label": lab} for k, lab in r.columns],
            "filters": list(r.filters), "statuses": list(r.statuses), "flags": r.flags or {}, "date_label": r.date_label,
            "link": r.link}  # fmt: skip


@router.get("/reports")
def list_reports(p: CurrentPrincipal) -> dict:
    return {"items": [_describe(n, r) for n, r in REPORTS.items() if r.perm in p.permissions],
            "areas": {a: label for a, (label, _) in AREAS.items()}, "can_export": "reports.export" in p.permissions}  # fmt: skip


@router.get("/dashboards")
def dashboards(p: CurrentPrincipal, db: DB) -> dict:
    """Every figure the person may see, area by area. An area without any is left out."""
    out = []
    for area, (label, tiles) in AREAS.items():
        shown = [{"label": t, "report": rep, "filters": f, "count": count(db, REPORTS[rep], Filters(**f))}
                 for t, rep, f in tiles if REPORTS[rep].perm in p.permissions]  # fmt: skip
        if shown:
            out.append({"area": area, "label": label, "tiles": shown})
    return {"areas": out}


@router.get("/reports/{name}")
def report_rows(name: str, p: CurrentPrincipal, db: DB, f: Annotated[Filters, Query()],
                limit: Limit = 50, offset: Offset = 0) -> dict:  # fmt: skip
    r = _report(name, p)
    _checked(r, f)
    rows, total = run(db, r, f, limit, offset)
    return _describe(name, r) | {"items": rows, "total": total}


def _cell(v):
    if isinstance(v, Decimal):
        return float(v) if v != v.to_integral() else int(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


@router.get("/reports/{name}/export")
def export_report(name: str, p: require("reports.export"), db: DB, f: Annotated[Filters, Query()],
                  format: Literal["csv", "xlsx"] = "csv") -> Response:  # fmt: skip
    r = _report(name, p)
    _checked(r, f)
    rows, total = run(db, r, f, None)
    if total > EXPORT_LIMIT:
        raise conflict(
            f"{total} rows is more than {EXPORT_LIMIT} for one download: narrow the filters"
        )
    keys, heads = [k for k, _ in r.columns], [lab for _, lab in r.columns]
    stamp = date.today().isoformat()
    if format == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(heads)
        for row in rows:
            w.writerow(["" if row.get(k) is None else csv_cell(_cell(row.get(k))) for k in keys])
        body, media = ("﻿" + out.getvalue()).encode(), "text/csv; charset=utf-8"
    else:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(r.title[:31])
        ws.append(heads)
        for row in rows:
            ws.append([csv_cell(v) if isinstance(v := _cell(row.get(k)), str) else v for k in keys])
        buf = io.BytesIO()
        wb.save(buf)
        body, media = (
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    used = {k: str(v) for k, v in f.model_dump().items() if v not in (None, "")}
    record(db, p.user, "report.export", "report", name, f"{r.title}: {len(rows)} rows as {format.upper()}", {"filters": [None, used]})  # fmt: skip
    db.commit()
    return Response(body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.{format}"'})  # fmt: skip


# ---------- global search ----------


@router.get("/search")
def search(
    p: CurrentPrincipal, db: DB, q: Annotated[str, Query(min_length=2, max_length=60)]
) -> dict:
    """Batteries, customers and suppliers, projects and documents, each only with its permission."""
    term = q.strip()
    like, pre = f"%{term}%", f"{term.upper()}%"
    can = p.permissions.__contains__
    groups = []

    def add(kind, label, perm, query, row):
        if can(perm):
            items = [row(x) for x in db.execute(query.limit(5)).all()]
            if items:
                groups.append({"kind": kind, "label": label, "items": items})

    add(
        "battery",
        "Batteries",
        "stock.view",
        select(Serial.id, Serial.serial, Item.name, Serial.status)
        .join(Item, Item.id == Serial.item_id)
        .where(Serial.serial.like(pre))
        .order_by(Serial.serial),
        lambda x: {
            "id": x[0],
            "title": x[1],
            "detail": f"{x[2]} · {x[3].replace('_', ' ')}",
            "link": f"/stock/serials/{x[0]}",
        },
    )
    add(
        "party",
        "Customers and suppliers",
        "parties.view",
        select(Party.id, Party.name, Party.code, Party.kinds)
        .where(or_(Party.name.ilike(like), Party.code.ilike(like)))
        .order_by(Party.name),
        lambda x: {
            "id": x[0],
            "title": x[1],
            "detail": f"{x[2]} · {', '.join(x[3])}",
            "link": f"/masters/parties?q={x[2]}",
        },
    )
    add(
        "project",
        "Projects",
        "projects.view",
        select(Project.id, Project.number, Project.name)
        .where(or_(Project.number.ilike(like), Project.name.ilike(like)))
        .order_by(Project.created_at.desc()),
        lambda x: {
            "id": x[0],
            "title": f"{x[1]} {x[2]}",
            "detail": "",
            "link": f"/projects/{x[0]}",
        },
    )
    add(
        "service",
        "Service requests",
        "service.view",
        select(
            ServiceRequest.id,
            ServiceRequest.number,
            ServiceRequest.status,
            ServiceRequest.contact_name,
        )
        .where(
            or_(
                ServiceRequest.number.ilike(like),
                ServiceRequest.reported_serial.like(pre),
                ServiceRequest.contact_name.ilike(like),
            )
        )
        .order_by(ServiceRequest.created_at.desc()),
        lambda x: {
            "id": x[0],
            "title": x[1],
            "detail": f"{x[2].replace('_', ' ')} · {x[3]}".strip(" ·"),
            "link": f"/service/{x[0]}",
        },
    )
    add(
        "warranty",
        "Warranty registrations",
        "warranty.view",
        select(
            WarrantyRegistration.id, WarrantyRegistration.number, WarrantyRegistration.customer_name
        )
        .where(
            or_(
                WarrantyRegistration.number.ilike(like),
                WarrantyRegistration.customer_name.ilike(like),
            )
        )
        .order_by(WarrantyRegistration.created_at.desc()),
        lambda x: {"id": x[0], "title": x[1], "detail": x[2], "link": f"/warranty/{x[0]}"},
    )
    add(
        "purchase_order",
        "Purchase orders",
        "purchasing.view",
        select(PurchaseOrder.id, PurchaseOrder.number, PurchaseOrder.status)
        .where(PurchaseOrder.number.ilike(like))
        .order_by(PurchaseOrder.created_at.desc()),
        lambda x: {
            "id": x[0],
            "title": x[1],
            "detail": x[2].replace("_", " "),
            "link": f"/purchasing/orders/{x[0]}",
        },
    )
    add(
        "sales_order",
        "Sales orders",
        "sales.view",
        select(SalesOrder.id, SalesOrder.number, SalesOrder.status)
        .where(SalesOrder.number.ilike(like))
        .order_by(SalesOrder.created_at.desc()),
        lambda x: {
            "id": x[0],
            "title": x[1],
            "detail": x[2].replace("_", " "),
            "link": f"/sales/orders/{x[0]}",
        },
    )
    add(
        "quotation",
        "Quotations",
        "sales.view",
        select(Quotation.id, Quotation.number, literal(""))
        .where(Quotation.number.ilike(like))
        .order_by(Quotation.created_at.desc()),
        lambda x: {"id": x[0], "title": x[1], "detail": "", "link": f"/sales/quotations/{x[0]}"},
    )
    return {"groups": groups}  # fmt: skip
