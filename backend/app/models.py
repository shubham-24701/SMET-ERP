"""Tables. Phase 1: company, users, roles, sessions, audit log, jobs. Phase 2: master data,
purchasing, stock. Phase 3: BOMs, assembly, quality.

Conventions: UUID keys, timestamps are timezone-aware UTC (`timestamptz`), shown in the company's
time zone by the web app. Editable records carry `version` for optimistic locking.
"""

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _ts(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), **kw)


class Company(Base):
    """The one company this installation serves (single row, id = 1)."""

    __tablename__ = "company"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(64), server_default="Asia/Dubai")
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    rules: Mapped[dict] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )  # see app/rules.py
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default=false())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    # Who the role is for: internal staff, or a distributor's / customer's portal users.
    audience: Mapped[str] = mapped_column(String(12), server_default="internal")
    # Set on the role templates SMET ships (stable even if the role is renamed); null = custom.
    template_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True, index=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = _id()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # sha256 of the cookie
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only: a database trigger refuses UPDATE, DELETE and TRUNCATE (migration 0001)."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = _ts(index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)  # no FK: outlives users
    actor_name: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), server_default="")
    entity_id: Mapped[str] = mapped_column(String(64), server_default="")
    summary: Mapped[str] = mapped_column(String(300), server_default="")
    changes: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    reason: Mapped[str] = mapped_column(String(500), server_default="")
    ip: Mapped[str] = mapped_column(String(45), server_default="")
    request_id: Mapped[str] = mapped_column(String(16), server_default="")


class TemplateGrant(Base):
    """A template permission already given to a shipped role once. Never given again, so a
    permission an administrator removed stays removed when a later version adds new ones."""

    __tablename__ = "template_grants"
    template_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    permission: Mapped[str] = mapped_column(String(64), primary_key=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_due", "status", "run_at"),)
    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    key: Mapped[str | None] = mapped_column(String(200), unique=True)  # idempotency key
    status: Mapped[str] = mapped_column(
        String(10), server_default="pending"
    )  # pending|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, server_default="5")
    run_at: Mapped[datetime] = _ts()
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str] = mapped_column(String(300), server_default="")
    created_at: Mapped[datetime] = _ts()


# ======================= Phase 2: master data, purchasing, stock =======================
# Quantities NUMERIC(14,3); prices NUMERIC(16,4). Prices are reference data only: accounting is
# outside this system (proposal scope).

QTY = Numeric(14, 3)
PRICE = Numeric(16, 4)


class Unit(Base):
    __tablename__ = "units"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Item(Base):
    """Anything stocked: battery models (sold; `assembled` = built in Dubai), purchased battery
    units, components, accessories and assembly materials."""

    __tablename__ = "items"
    id: Mapped[uuid.UUID] = _id()
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(
        String(16)
    )  # battery_model|battery_unit|component|accessory|material
    category: Mapped[str] = mapped_column(String(60), server_default="")  # e.g. UPS, telecom, solar
    unit_code: Mapped[str] = mapped_column(ForeignKey("units.code"))
    tracking: Mapped[str] = mapped_column(String(6), server_default="none")  # none|lot|serial
    assembled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    capacity_ah: Mapped[float | None] = mapped_column(Numeric(10, 2))
    voltage_v: Mapped[float | None] = mapped_column(Numeric(10, 2))
    chemistry: Mapped[str] = mapped_column(String(40), server_default="")
    specs: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    min_stock: Mapped[float] = mapped_column(QTY, server_default="0")
    notes: Mapped[str] = mapped_column(Text, server_default="")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class Party(Base):
    """A supplier, customer, distributor or dealer (one company can be several)."""

    __tablename__ = "parties"
    id: Mapped[uuid.UUID] = _id()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    kinds: Mapped[list[str]] = mapped_column(ARRAY(String(12)))
    contacts: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    address: Mapped[str] = mapped_column(Text, server_default="")
    terms: Mapped[str] = mapped_column(Text, server_default="")  # purchase / commercial terms
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    notes: Mapped[str] = mapped_column(Text, server_default="")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class SupplierItem(Base):
    """What a supplier supplies, under which code, at what last price and lead time."""

    __tablename__ = "supplier_items"
    party_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("parties.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), primary_key=True)
    supplier_code: Mapped[str] = mapped_column(String(60), server_default="")
    last_price: Mapped[float | None] = mapped_column(PRICE)
    currency: Mapped[str] = mapped_column(String(3), server_default="AED")
    lead_days: Mapped[int | None] = mapped_column(Integer)


class Warehouse(Base):
    __tablename__ = "warehouses"
    id: Mapped[uuid.UUID] = _id()
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(Text, server_default="")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class Location(Base):
    """A storage area. Its purpose is the stock status of what sits in it: only `available`
    locations count as available stock."""

    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("warehouse_id", "code"),)
    id: Mapped[uuid.UUID] = _id()
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    # available|inspection|quarantine|rejected|assembly|repair|returns
    purpose: Mapped[str] = mapped_column(String(12))
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class NumberSequence(Base):
    """Document numbers such as PO-2026-00001. Locked while a number is taken, so none repeat."""

    __tablename__ = "number_sequences"
    doc_type: Mapped[str] = mapped_column(String(20), primary_key=True)
    prefix: Mapped[str] = mapped_column(String(12))
    padding: Mapped[int] = mapped_column(Integer, server_default="5")
    yearly: Mapped[bool] = mapped_column(Boolean, server_default=true())
    year: Mapped[int] = mapped_column(Integer, server_default="0")
    next_value: Mapped[int] = mapped_column(Integer, server_default="1")


class OperationKey(Base):
    """One row per posted operation key: the same receipt, issue or transfer sent twice (a double
    click, a retry after a timeout) is recorded once."""

    __tablename__ = "operation_keys"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(40))
    result: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _ts()


# ---------- purchasing ----------


class PurchaseRequest(Base):
    __tablename__ = "purchase_requests"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(String(12), server_default="open")  # open|ordered|cancelled
    source: Mapped[str] = mapped_column(String(12))  # shortage|assembly|project|other
    ref: Mapped[str] = mapped_column(String(120), server_default="")
    needed_by: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, server_default="")
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class PurchaseRequestLine(Base):
    __tablename__ = "purchase_request_lines"
    id: Mapped[uuid.UUID] = _id()
    pr_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_requests.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)
    notes: Mapped[str] = mapped_column(String(300), server_default="")


class SupplierQuote(Base):
    __tablename__ = "supplier_quotes"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    pr_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_requests.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"))
    supplier_ref: Mapped[str] = mapped_column(String(60), server_default="")
    valid_until: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3))
    delivery_days: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, server_default="")
    status: Mapped[str] = mapped_column(String(10), server_default="open")  # open|selected|declined
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()


class SupplierQuoteLine(Base):
    __tablename__ = "supplier_quote_lines"
    id: Mapped[uuid.UUID] = _id()
    quote_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_quotes.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)
    unit_price: Mapped[float] = mapped_column(PRICE)
    delivery_date: Mapped[date | None] = mapped_column(Date)
    spec_notes: Mapped[str] = mapped_column(String(300), server_default="")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), index=True)
    # draft|submitted|approved|rejected|partly_received|received|closed|cancelled
    status: Mapped[str] = mapped_column(String(16), server_default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(3))
    supplier_ref: Mapped[str] = mapped_column(String(60), server_default="")
    pr_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_requests.id"))
    quote_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("supplier_quotes.id"))
    link_ref: Mapped[str] = mapped_column(String(120), server_default="")  # project / stock need
    expected_ship: Mapped[date | None] = mapped_column(Date)
    expected_arrival: Mapped[date | None] = mapped_column(Date)
    terms: Mapped[str] = mapped_column(Text, server_default="")
    notes: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # everyone who drafted or changed it: none of them may approve it
    editors: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(String(500), server_default="")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class PurchaseOrderLine(Base):
    """ordered ≥ accepted + held; outstanding = ordered − accepted − held (rejected goods still
    have to be replaced, so they stay outstanding)."""

    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        CheckConstraint(
            "qty_received = qty_accepted + qty_rejected + qty_held + qty_awaiting",
            name="po_line_received",
        ),
        CheckConstraint("qty_returned <= qty_rejected", name="po_line_returned"),
        CheckConstraint(
            "qty_accepted >= 0 AND qty_rejected >= 0 AND qty_held >= 0 AND qty_awaiting >= 0",
            name="po_line_positive",
        ),
    )
    id: Mapped[uuid.UUID] = _id()
    po_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty_ordered: Mapped[float] = mapped_column(QTY)
    unit_price: Mapped[float] = mapped_column(PRICE)
    qty_received: Mapped[float] = mapped_column(QTY, server_default="0")
    qty_awaiting: Mapped[float] = mapped_column(QTY, server_default="0")  # awaiting inspection
    qty_accepted: Mapped[float] = mapped_column(QTY, server_default="0")
    qty_rejected: Mapped[float] = mapped_column(QTY, server_default="0")
    qty_held: Mapped[float] = mapped_column(QTY, server_default="0")
    qty_returned: Mapped[float] = mapped_column(QTY, server_default="0")
    qty_not_replaced: Mapped[float] = mapped_column(QTY, server_default="0")  # returned, not owed


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    delivery_ref: Mapped[str] = mapped_column(String(60), server_default="")
    notes: Mapped[str] = mapped_column(Text, server_default="")
    received_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"
    id: Mapped[uuid.UUID] = _id()
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("goods_receipts.id", ondelete="CASCADE"), index=True
    )
    po_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_order_lines.id"))
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)
    lot: Mapped[str] = mapped_column(String(60), server_default="")


class SupplierReturn(Base):
    __tablename__ = "supplier_returns"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    po_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    reason: Mapped[str] = mapped_column(String(500))
    replacement_expected: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()


class SupplierReturnLine(Base):
    __tablename__ = "supplier_return_lines"
    id: Mapped[uuid.UUID] = _id()
    return_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("supplier_returns.id", ondelete="CASCADE"), index=True
    )
    po_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchase_order_lines.id"))
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)


# ---------- stock ----------


class Serial(Base):
    """One individually tracked unit: a purchased battery unit, a serialised component, or a
    finished battery. The serial number never changes; status and location follow its moves."""

    __tablename__ = "serials"
    __table_args__ = (UniqueConstraint("item_id", "supplier_serial"),)
    id: Mapped[uuid.UUID] = _id()
    serial: Mapped[str] = mapped_column(String(60), unique=True)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), index=True)
    supplier_serial: Mapped[str | None] = mapped_column(String(80))
    lot: Mapped[str] = mapped_column(String(60), server_default="")
    # awaiting_inspection|available|on_hold|rejected|returned_to_supplier|issued|consumed|
    # under_assembly|rework|written_off
    status: Mapped[str] = mapped_column(String(20), index=True)
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"), index=True)
    receipt_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("goods_receipt_lines.id"))
    po_line_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("purchase_order_lines.id"))
    assembly_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assembly_orders.id"), index=True
    )
    party_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("parties.id"), index=True
    )  # holder after dispatch
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"), index=True)
    # What the QR label carries: random, so labels can't be guessed or counted, and no internal id.
    qr_token: Mapped[str] = mapped_column(
        String(32), unique=True, server_default=text("replace(gen_random_uuid()::text, '-', '')")
    )
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class StockBalance(Base):
    """Derived from the ledger, kept in the same transaction; never negative (CHECK)."""

    __tablename__ = "stock_balances"
    __table_args__ = (CheckConstraint("qty >= 0", name="stock_never_negative"),)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), primary_key=True)
    location_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("locations.id"), primary_key=True, index=True
    )
    lot: Mapped[str] = mapped_column(String(60), primary_key=True, server_default="")
    qty: Mapped[float] = mapped_column(QTY, server_default="0")


class StockMove(Base):
    """The stock ledger, the source of truth. Append-only (grants + trigger, migration 0003):
    a correction is a new move."""

    __tablename__ = "stock_moves"
    __table_args__ = (
        CheckConstraint("qty > 0", name="move_qty_positive"),
        CheckConstraint(
            "from_location_id IS NOT NULL OR to_location_id IS NOT NULL", name="move_has_side"
        ),
        Index("ix_moves_item_at", "item_id", "at"),
        Index("ix_moves_doc", "doc_type", "doc_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = _ts()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)
    from_location_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("locations.id"), index=True
    )
    to_location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"), index=True)
    lot: Mapped[str] = mapped_column(String(60), server_default="")
    serial_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("serials.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    doc_type: Mapped[str] = mapped_column(String(20), server_default="")
    doc_id: Mapped[str] = mapped_column(String(64), server_default="")
    doc_number: Mapped[str] = mapped_column(String(60), server_default="")
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(300), server_default="")


class Reservation(Base):
    """Stock promised to an assembly order, sales order or project. Available = on hand in
    available locations − what reservations still hold (qty − qty_used)."""

    __tablename__ = "reservations"
    __table_args__ = (
        CheckConstraint("qty_used >= 0 AND qty_used <= qty", name="reservation_used"),
    )
    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), index=True)
    qty: Mapped[float] = mapped_column(QTY)
    qty_used: Mapped[float] = mapped_column(QTY, server_default="0")
    ref_type: Mapped[str] = mapped_column(String(16))  # assembly_order|sales_order|project|other
    ref_id: Mapped[str] = mapped_column(String(64), server_default="")
    ref_label: Mapped[str] = mapped_column(String(120), server_default="")
    status: Mapped[str] = mapped_column(
        String(10), server_default="active", index=True
    )  # active|released|used
    reason: Mapped[str] = mapped_column(String(300), server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class StockAdjustment(Base):
    """A requested correction; stock changes only when someone else approves it."""

    __tablename__ = "stock_adjustments"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    status: Mapped[str] = mapped_column(
        String(10), server_default="pending", index=True
    )  # pending|approved|rejected
    reason: Mapped[str] = mapped_column(String(500))
    count_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(String(500), server_default="")
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class StockAdjustmentLine(Base):
    """Either `qty_change` (signed) or, from a count, `counted` (the difference is worked out at
    approval, against the stock then). Serial items name their serials."""

    __tablename__ = "stock_adjustment_lines"
    id: Mapped[uuid.UUID] = _id()
    adjustment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stock_adjustments.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    lot: Mapped[str] = mapped_column(String(60), server_default="")
    qty_change: Mapped[float | None] = mapped_column(QTY)
    counted: Mapped[float | None] = mapped_column(QTY)
    serials_in: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")
    serials_out: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")
    serials_found: Mapped[list[str] | None] = mapped_column(ARRAY(String(60)))


class StockCount(Base):
    __tablename__ = "stock_counts"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    location_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("locations.id"))
    status: Mapped[str] = mapped_column(
        String(10), server_default="open"
    )  # open|submitted|cancelled
    notes: Mapped[str] = mapped_column(Text, server_default="")
    adjustment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("stock_adjustments.id"))
    # everyone who entered counts: none of them may approve the resulting correction
    counters: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class StockCountLine(Base):
    __tablename__ = "stock_count_lines"
    __table_args__ = (UniqueConstraint("count_id", "item_id", "lot"),)
    id: Mapped[uuid.UUID] = _id()
    count_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stock_counts.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    lot: Mapped[str] = mapped_column(String(60), server_default="")
    expected: Mapped[float] = mapped_column(QTY)  # on the books when the count started
    counted: Mapped[float | None] = mapped_column(QTY)
    serials_found: Mapped[list[str] | None] = mapped_column(ARRAY(String(60)))


# ======================= Phase 3: BOMs, assembly, quality =======================


class Bom(Base):
    """A bill of materials version. Active versions never change: a change is a new version, so
    batteries already built keep the recipe they were built to."""

    __tablename__ = "boms"
    __table_args__ = (
        UniqueConstraint("item_id", "version"),
        Index("one_active_bom", "item_id", unique=True, postgresql_where=text("status = 'active'")),
    )
    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), server_default="draft")  # draft|active|retired
    notes: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    activated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BomLine(Base):
    __tablename__ = "bom_lines"
    __table_args__ = (CheckConstraint("qty > 0", name="bom_qty_positive"),)
    id: Mapped[uuid.UUID] = _id()
    bom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boms.id", ondelete="CASCADE"), index=True)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)  # per finished battery
    notes: Mapped[str] = mapped_column(String(300), server_default="")


class AssemblyOrder(Base):
    __tablename__ = "assembly_orders"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    bom_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boms.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    qty_planned: Mapped[int] = mapped_column(Integer)
    # draft|released|in_progress|completed|cancelled
    status: Mapped[str] = mapped_column(String(12), server_default="draft", index=True)
    source: Mapped[str] = mapped_column(String(14))  # replenishment|sales_order|project
    source_ref: Mapped[str] = mapped_column(String(120), server_default="")
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    target_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class SerialComponent(Base):
    """What went into a finished battery: a component serial, or a quantity from a lot."""

    __tablename__ = "serial_components"
    id: Mapped[uuid.UUID] = _id()
    serial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("serials.id"), index=True)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    component_serial_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("serials.id"), unique=True
    )
    lot: Mapped[str] = mapped_column(String(60), server_default="")
    qty: Mapped[float] = mapped_column(QTY)


class TestParameter(Base):
    """A reading taken at final inspection. Limits are applied only once `confirmed` (by SMET):
    no test limit is invented."""

    __tablename__ = "test_parameters"
    __test__ = False  # not a pytest test class
    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    unit: Mapped[str] = mapped_column(String(20), server_default="")
    min_value: Mapped[float | None] = mapped_column(Numeric(14, 4))
    max_value: Mapped[float | None] = mapped_column(Numeric(14, 4))
    required: Mapped[bool] = mapped_column(Boolean, server_default=true())
    confirmed: Mapped[bool] = mapped_column(Boolean, server_default=false())
    sort: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class ChecklistItem(Base):
    __tablename__ = "checklist_items"
    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), index=True)
    text: Mapped[str] = mapped_column(String(200))
    required: Mapped[bool] = mapped_column(Boolean, server_default=true())
    sort: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Inspection(Base):
    """A final inspection of one finished battery. Readings and checklist are copied with the
    limits that applied, so later changes to parameters never rewrite the record."""

    __tablename__ = "inspections"
    id: Mapped[uuid.UUID] = _id()
    serial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("serials.id"), index=True)
    assembly_order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("assembly_orders.id"), index=True
    )
    result: Mapped[str] = mapped_column(String(10))  # passed|on_hold|rework
    readings: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    checklist: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    observations: Mapped[str] = mapped_column(Text, server_default="")
    inspector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = _ts()


# ======================= Phase 4: sales, projects, dispatch =======================


class Enquiry(Base):
    """What a customer asked for, before any price. Lines are informal (an item or free text)."""

    __tablename__ = "enquiries"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # product|project
    status: Mapped[str] = mapped_column(
        String(10), server_default="open"
    )  # open|quoted|lost|closed
    contact: Mapped[str] = mapped_column(String(160), server_default="")
    needed_by: Mapped[date | None] = mapped_column(Date)
    lines: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )  # [{item_id?, description, qty}]
    notes: Mapped[str] = mapped_column(Text, server_default="")
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class Quotation(Base):
    """A customer quotation; its content lives in revisions. Only the latest revision is live."""

    __tablename__ = "quotations"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), index=True)
    enquiry_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enquiries.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()


class QuotationRevision(Base):
    """Editable only as a draft. Once submitted it never changes: a change is a new revision, and
    the old one is marked revised."""

    __tablename__ = "quotation_revisions"
    __table_args__ = (UniqueConstraint("quotation_id", "rev"),)
    id: Mapped[uuid.UUID] = _id()
    quotation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quotations.id"), index=True)
    rev: Mapped[int] = mapped_column(Integer)
    # draft|submitted|approved|rejected|accepted|declined|revised
    status: Mapped[str] = mapped_column(String(10), server_default="draft", index=True)
    currency: Mapped[str] = mapped_column(String(3))
    valid_until: Mapped[date | None] = mapped_column(Date)
    terms: Mapped[str] = mapped_column(Text, server_default="")
    scope_notes: Mapped[str] = mapped_column(Text, server_default="")
    installation_date: Mapped[date | None] = mapped_column(Date)
    commissioning_date: Mapped[date | None] = mapped_column(Date)
    handover_date: Mapped[date | None] = mapped_column(Date)
    editors: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(String(500), server_default="")
    # the customer's answer, as recorded by SMET
    customer_answer_by: Mapped[str] = mapped_column(String(160), server_default="")
    customer_ref: Mapped[str] = mapped_column(String(60), server_default="")
    customer_answer_at: Mapped[date | None] = mapped_column(Date)
    answer_recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class QuotationLine(Base):
    __tablename__ = "quotation_lines"
    __table_args__ = (
        CheckConstraint("discount_pct >= 0 AND discount_pct <= 100", name="quote_discount_range"),
    )
    id: Mapped[uuid.UUID] = _id()
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quotation_revisions.id", ondelete="CASCADE"), index=True
    )
    line_type: Mapped[str] = mapped_column(
        String(14)
    )  # battery|accessory|installation|commissioning|amc|other
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(300), server_default="")
    qty: Mapped[float] = mapped_column(QTY)
    unit_price: Mapped[float] = mapped_column(PRICE)
    discount_pct: Mapped[float] = mapped_column(Numeric(5, 2), server_default="0")
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class SalesOrder(Base):
    __tablename__ = "sales_orders"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), index=True)
    # one order per accepted revision: converting twice is refused by the database
    quotation_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quotation_revisions.id"), unique=True
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(18), server_default="open", index=True
    )  # open|partly_dispatched|dispatched|closed|cancelled
    currency: Mapped[str] = mapped_column(String(3))
    customer_ref: Mapped[str] = mapped_column(String(60), server_default="")
    delivery_date: Mapped[date | None] = mapped_column(Date)
    delivery_address: Mapped[str] = mapped_column(Text, server_default="")
    notes: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class SalesOrderLine(Base):
    """Goods lines (battery, accessory) are dispatched; service lines are delivered in Phases 5–6."""

    __tablename__ = "sales_order_lines"
    __table_args__ = (
        CheckConstraint("qty_dispatched >= 0 AND qty_dispatched <= qty", name="so_line_dispatched"),
    )
    id: Mapped[uuid.UUID] = _id()
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sales_orders.id", ondelete="CASCADE"), index=True
    )
    line_type: Mapped[str] = mapped_column(String(14))
    item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("items.id"))
    description: Mapped[str] = mapped_column(String(300), server_default="")
    qty: Mapped[float] = mapped_column(QTY)
    unit_price: Mapped[float] = mapped_column(PRICE)
    discount_pct: Mapped[float] = mapped_column(Numeric(5, 2), server_default="0")
    qty_dispatched: Mapped[float] = mapped_column(
        QTY, server_default="0"
    )  # on the road or delivered
    qty_delivered: Mapped[float] = mapped_column(
        QTY, server_default="0"
    )  # accepted by the customer
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), index=True)
    status: Mapped[str] = mapped_column(
        String(10), server_default="planning"
    )  # planning|active|on_hold|completed|cancelled
    scope: Mapped[str] = mapped_column(Text, server_default="")
    contacts: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    manager_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class Site(Base):
    """A place where batteries go. The survey is kept on the site: date, who, findings and the
    technical requirements found (key/value)."""

    __tablename__ = "sites"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    address: Mapped[str] = mapped_column(Text, server_default="")
    contact: Mapped[str] = mapped_column(String(160), server_default="")
    survey_date: Mapped[date | None] = mapped_column(Date)
    surveyed_by: Mapped[str] = mapped_column(String(120), server_default="")
    survey_notes: Mapped[str] = mapped_column(Text, server_default="")
    requirements: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class Milestone(Base):
    __tablename__ = "project_milestones"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    kind: Mapped[str] = mapped_column(
        String(16)
    )  # material_ready|delivery|installation|commissioning|handover|other
    title: Mapped[str] = mapped_column(String(160))
    due_date: Mapped[date | None] = mapped_column(Date)
    done_at: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class ProjectTask(Base):
    __tablename__ = "project_tasks"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    title: Mapped[str] = mapped_column(String(200))
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), server_default="open")  # open|done
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class ProjectChange(Base):
    """A change to scope, quantities or schedule, decided by someone with projects.approve."""

    __tablename__ = "project_changes"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # scope|quantity|schedule
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(10), server_default="pending"
    )  # pending|approved|rejected
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(String(500), server_default="")
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class ProjectNote(Base):
    """Progress notes and pending actions (append-only in use: notes are never edited)."""

    __tablename__ = "project_notes"
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    text: Mapped[str] = mapped_column(Text)
    pending_action: Mapped[bool] = mapped_column(Boolean, server_default=false())
    author_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()


class SizingRule(Base):
    """A battery-sizing formula SMET supplies (inputs, an expression, a result). Until SMET marks
    one confirmed, its results are suggestions an engineer must accept; none is shipped."""

    __tablename__ = "sizing_rules"
    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, server_default="")
    inputs: Mapped[list] = mapped_column(
        JSONB, server_default=text("'[]'::jsonb")
    )  # [{name, label, unit}]
    expression: Mapped[str] = mapped_column(String(500))
    result_unit: Mapped[str] = mapped_column(String(20), server_default="batteries")
    confirmed: Mapped[bool] = mapped_column(Boolean, server_default=false())
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class RequirementSheet(Base):
    """Battery requirements for a project (or one site). Approved sheets never change: a change is
    a new version."""

    __tablename__ = "requirement_sheets"
    __table_args__ = (UniqueConstraint("project_id", "version_no"),)
    id: Mapped[uuid.UUID] = _id()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    version_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(
        String(10), server_default="draft"
    )  # draft|submitted|approved|rejected|superseded
    notes: Mapped[str] = mapped_column(Text, server_default="")
    prepared_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    editors: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(String(500), server_default="")
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class RequirementLine(Base):
    """One requirement. `calculation` keeps the rule, inputs and result shown to the engineer, so
    the approved quantity can always be traced back."""

    __tablename__ = "requirement_lines"
    id: Mapped[uuid.UUID] = _id()
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement_sheets.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    configuration: Mapped[str] = mapped_column(String(300), server_default="")
    qty: Mapped[float] = mapped_column(QTY)
    calculation: Mapped[dict | None] = mapped_column(JSONB)
    notes: Mapped[str] = mapped_column(String(300), server_default="")
    sort: Mapped[int] = mapped_column(Integer, server_default="0")


class Dispatch(Base):
    """planned → dispatched → delivered, or cancelled. Cancelling after dispatch returns the stock
    with new movements; the record stays."""

    __tablename__ = "dispatches"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_orders.id"), index=True)
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    warehouse_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("warehouses.id"))
    status: Mapped[str] = mapped_column(String(10), server_default="planned", index=True)
    planned_date: Mapped[date | None] = mapped_column(Date)
    destination: Mapped[str] = mapped_column(Text, server_default="")
    delivery_contact: Mapped[str] = mapped_column(String(160), server_default="")
    transporter: Mapped[str] = mapped_column(String(120), server_default="")
    vehicle: Mapped[str] = mapped_column(String(60), server_default="")
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by: Mapped[str] = mapped_column(String(160), server_default="")
    delivered_on: Mapped[date | None] = mapped_column(Date)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when recorded
    delivery_notes: Mapped[str] = mapped_column(Text, server_default="")
    cancel_reason: Mapped[str] = mapped_column(String(500), server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class DispatchLine(Base):
    __tablename__ = "dispatch_lines"
    __table_args__ = (
        CheckConstraint("qty_rejected + qty_damaged <= qty", name="dispatch_line_discrepancy"),
    )
    id: Mapped[uuid.UUID] = _id()
    dispatch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dispatches.id", ondelete="CASCADE"), index=True
    )
    order_line_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales_order_lines.id"))
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[float] = mapped_column(QTY)
    lot: Mapped[str] = mapped_column(String(60), server_default="")
    location_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("locations.id"))  # picked from
    serials: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")
    qty_rejected: Mapped[float] = mapped_column(QTY, server_default="0")  # refused, came back
    qty_damaged: Mapped[float] = mapped_column(QTY, server_default="0")  # accepted but damaged
    rejected_serials: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")
    from_reserved: Mapped[float] = mapped_column(QTY, server_default="0")  # given back if cancelled
    # [{reservation_id, qty}] taken when sent: exactly what a cancellation gives back
    reservation_use: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    note: Mapped[str] = mapped_column(String(300), server_default="")


# ======================= Phase 5: installation and traceability =======================


class BatteryBank(Base):
    """A group of batteries working as one system at a site. Its three stages have separate dates:
    installed, commissioned, handed over, each set only by a completed visit."""

    __tablename__ = "battery_banks"
    __table_args__ = (UniqueConstraint("site_id", "name"),)
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    configuration: Mapped[str] = mapped_column(String(300), server_default="")
    # planned|installed|commissioned|handed_over
    status: Mapped[str] = mapped_column(String(12), server_default="planned", index=True)
    installed_on: Mapped[date | None] = mapped_column(Date)
    commissioned_on: Mapped[date | None] = mapped_column(Date)
    handed_over_on: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class BankPosition(Base):
    """Which battery sat where, and when. Rows are never rewritten except to close them: a removal,
    replacement or relocation ends one row and (except a removal) starts another."""

    __tablename__ = "bank_positions"
    __table_args__ = (
        Index("one_place_per_battery", "serial_id", unique=True, postgresql_where=text("ended_at IS NULL")),
        Index("one_battery_per_position", "bank_id", "position", unique=True,
              postgresql_where=text("ended_at IS NULL")),
    )  # fmt: skip
    id: Mapped[uuid.UUID] = _id()
    bank_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("battery_banks.id"), index=True)
    serial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("serials.id"), index=True)
    position: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = _ts()
    start_reason: Mapped[str] = mapped_column(String(12))  # added|replacement|relocated
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str] = mapped_column(
        String(12), server_default=""
    )  # removed|replaced|relocated
    note: Mapped[str] = mapped_column(String(500), server_default="")
    replaces_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("serials.id"))
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("site_visits.id"))
    by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    ended_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class StageCheck(Base):
    """What SMET wants checked at each stage: a checklist line or a reading. Limits on a reading
    apply only once confirmed, as at final inspection."""

    __tablename__ = "stage_checks"
    id: Mapped[uuid.UUID] = _id()
    stage: Mapped[str] = mapped_column(
        String(14), index=True
    )  # installation|commissioning|handover
    kind: Mapped[str] = mapped_column(String(8))  # check|reading
    text: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20), server_default="")
    min_value: Mapped[float | None] = mapped_column(Numeric(14, 4))
    max_value: Mapped[float | None] = mapped_column(Numeric(14, 4))
    confirmed: Mapped[bool] = mapped_column(Boolean, server_default=false())
    required: Mapped[bool] = mapped_column(Boolean, server_default=true())
    sort: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class SiteVisit(Base):
    """A planned trip to a site for one stage (or other work), with its technicians. The work is
    recorded on the visit; completing it moves the banks it covers to the next stage."""

    __tablename__ = "site_visits"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), index=True)
    stage: Mapped[str] = mapped_column(String(14))  # installation|commissioning|handover|other
    bank_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    scheduled_on: Mapped[date] = mapped_column(Date, index=True)
    time_window: Mapped[str] = mapped_column(String(60), server_default="")
    technician_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    # scheduled|started|completed|cancelled
    status: Mapped[str] = mapped_column(String(10), server_default="scheduled", index=True)
    site_ready: Mapped[bool | None] = mapped_column(Boolean)
    access_notes: Mapped[str] = mapped_column(Text, server_default="")
    contacts: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    received_serials: Mapped[list[str]] = mapped_column(ARRAY(String(60)), server_default="{}")
    checklist: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    readings: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    observations: Mapped[str] = mapped_column(Text, server_default="")
    issues: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    acknowledgement: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    done_on: Mapped[date | None] = mapped_column(Date)
    completed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str] = mapped_column(String(500), server_default="")
    amc_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("amc_contracts.id"), index=True)
    service_request_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("service_requests.id"), index=True
    )
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class StoredFile(Base):
    """A photograph, test report or signed acknowledgement. The bytes live on disk under a random
    name; the hash proves they haven't changed. Files are never edited, only added."""

    __tablename__ = "files"
    __table_args__ = (Index("files_owner", "owner_type", "owner_id"),)
    id: Mapped[uuid.UUID] = _id()
    owner_type: Mapped[str] = mapped_column(String(20))  # visit|warranty|service
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(20))  # photo|test_report|acknowledgement|proof_of_purchase|…
    name: Mapped[str] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(40))
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    caption: Mapped[str] = mapped_column(String(300), server_default="")
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id")
    )  # None: public form
    uploaded_at: Mapped[datetime] = _ts()


# ======================= Phase 6: warranty and after-sales =======================


class WarrantyPolicy(Base):
    """SMET's warranty terms for one battery model, from a date. Nothing is assumed: a model with no
    policy can't have a warranty activated. Terms of a policy in use never change; a new policy
    with a later start date replaces it."""

    __tablename__ = "warranty_policies"
    __table_args__ = (UniqueConstraint("item_id", "effective_from"),)
    id: Mapped[uuid.UUID] = _id()
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("items.id"), index=True)
    months: Mapped[int] = mapped_column(Integer)
    start_event: Mapped[str] = mapped_column(String(14))  # sale|installation|commissioning|handover
    auto_approve: Mapped[bool] = mapped_column(Boolean, server_default=false())
    requires_proof: Mapped[bool] = mapped_column(Boolean, server_default=true())
    replacement_rule: Mapped[str] = mapped_column(String(20))  # original_remaining|new_full|longer
    effective_from: Mapped[date] = mapped_column(Date)
    notes: Mapped[str] = mapped_column(Text, server_default="")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = _ts()


class WarrantyRegistration(Base):
    """One battery's warranty: who, where, which dates, and SMET's decision. At most one open
    registration per battery (pending, correction required or approved)."""

    __tablename__ = "warranty_registrations"
    __table_args__ = (
        Index("one_open_warranty_per_battery", "serial_id", unique=True,
              postgresql_where=text("status IN ('pending', 'correction_required', 'approved')")),
    )  # fmt: skip
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    serial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("serials.id"), index=True)
    route: Mapped[str] = mapped_column(
        String(20)
    )  # smet_installation|distributor|direct|public|replacement
    # pending|correction_required|approved|rejected|ended
    status: Mapped[str] = mapped_column(String(20), server_default="pending", index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parties.id"))
    customer_name: Mapped[str] = mapped_column(String(160), server_default="")
    customer_contact: Mapped[str] = mapped_column(String(160), server_default="")
    address: Mapped[str] = mapped_column(Text, server_default="")
    distributor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parties.id"))
    distributor_name: Mapped[str] = mapped_column(String(160), server_default="")
    sale_date: Mapped[date | None] = mapped_column(Date)
    installation_date: Mapped[date | None] = mapped_column(Date)
    commissioning_date: Mapped[date | None] = mapped_column(Date)
    handover_date: Mapped[date | None] = mapped_column(Date)
    policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warranty_policies.id"))
    start_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    replaces_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("warranty_registrations.id"))
    ended_on: Mapped[date | None] = mapped_column(Date)
    end_reason: Mapped[str] = mapped_column(String(300), server_default="")
    notes: Mapped[str] = mapped_column(Text, server_default="")
    submitted_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str] = mapped_column(String(500), server_default="")
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class AmcContract(Base):
    """An annual maintenance contract. What it covers is worked out when asked (a battery placed
    later in a covered bank is covered), so replacements need no editing. Renewal makes a new
    contract and keeps the old one with its visits."""

    __tablename__ = "amc_contracts"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("parties.id"), index=True)
    scope: Mapped[str] = mapped_column(String(10))  # customer|site|bank|batteries
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"))
    bank_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    serial_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default="{}")
    reference: Mapped[str] = mapped_column(String(80), server_default="")
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)
    visit_every_months: Mapped[int] = mapped_column(Integer)
    service_scope: Mapped[str] = mapped_column(Text, server_default="")
    covered: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    excluded: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(
        String(10), server_default="draft", index=True
    )  # draft|active|renewed|cancelled
    renewed_from_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("amc_contracts.id"))
    cancel_reason: Mapped[str] = mapped_column(String(500), server_default="")
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class ServiceRequest(Base):
    """A complaint or maintenance request. Status changes follow the allowed transitions and are
    kept with who, when and why."""

    __tablename__ = "service_requests"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    source: Mapped[str] = mapped_column(String(10))  # internal|public
    serial_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("serials.id"), index=True)
    reported_serial: Mapped[str] = mapped_column(String(60), server_default="")
    bank_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("battery_banks.id"))
    site_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sites.id"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("parties.id"), index=True)
    contact_name: Mapped[str] = mapped_column(String(160), server_default="")
    contact_phone: Mapped[str] = mapped_column(String(60), server_default="")
    issue: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(
        String(8), server_default="normal"
    )  # low|normal|high|urgent
    preferred_visit: Mapped[str] = mapped_column(String(200), server_default="")
    # new|under_review|scheduled|in_progress|pending_action|closed
    status: Mapped[str] = mapped_column(String(16), server_default="new", index=True)
    history: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    coordinator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    diagnosis: Mapped[str] = mapped_column(Text, server_default="")
    work_done: Mapped[str] = mapped_column(Text, server_default="")
    parts: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    resolution: Mapped[str] = mapped_column(
        String(12), server_default=""
    )  # repaired|replaced|returned|no_fault|other
    replacement_serial_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("serials.id"))
    acknowledgement: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    closure_notes: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")


class MaintenanceAlert(Base):
    """A battery asking for a maintenance reset code. The code comes from the supplier and is typed
    in by an authorised person; it is stored encrypted, shown only to that permission, never
    logged, and erased after the retention period."""

    __tablename__ = "maintenance_alerts"
    id: Mapped[uuid.UUID] = _id()
    number: Mapped[str] = mapped_column(String(30), unique=True)
    serial_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("serials.id"), index=True)
    service_request_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("service_requests.id"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(14), server_default="open", index=True
    )  # open|verified|code_recorded|closed
    verification: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    code_ciphertext: Mapped[str | None] = mapped_column(Text)
    recipient: Mapped[str] = mapped_column(String(160), server_default="")
    supplier_reference: Mapped[str] = mapped_column(String(120), server_default="")
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)  # only if the supplier states one
    issued_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    cleared: Mapped[bool | None] = mapped_column(Boolean)  # as confirmed on site, not by any system
    cleared_note: Mapped[str] = mapped_column(String(500), server_default="")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _ts()
    version: Mapped[int] = mapped_column(Integer, server_default="1")
