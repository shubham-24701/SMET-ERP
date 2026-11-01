"""Master data: units, items (battery models, units, components, accessories, materials),
suppliers and customers, warehouses and locations; document numbering and business rules.

Master data used by a transaction keeps the fields history depends on (an item's unit, tracking
and kind; a location's purpose while it holds stock). Names and descriptions stay editable.
"""

import re
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import any_, exists, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.audit import diff, record
from app.auth import DB, require
from app.models import (
    AssemblyOrder,
    Bom,
    BomLine,
    Company,
    Item,
    Location,
    NumberSequence,
    Party,
    PurchaseOrderLine,
    PurchaseRequestLine,
    Reservation,
    Serial,
    StockBalance,
    StockMove,
    SupplierItem,
    SupplierQuoteLine,
    Unit,
    Warehouse,
)


class NumbersRoute(APIRoute):
    """Quantities and prices leave as JSON numbers (9.5), not strings ("9.5000"): the response
    goes through FastAPI's encoder instead of a schema inferred from `-> dict`."""

    def __init__(self, path, endpoint, **kw):
        kw["response_model"] = None
        super().__init__(path, endpoint, **kw)


router = APIRouter(tags=["master data"], route_class=NumbersRoute)

Code = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1,
                                        max_length=40, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")]  # fmt: skip
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]
Long = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Qty = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=3)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


def page(db: Session, query, limit: int, offset: int, row) -> dict:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    return {
        "items": [row(x) for x in db.scalars(query.limit(limit).offset(offset))],
        "total": total,
    }


def not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


def get_or_404(db: Session, model, id_, what: str, lock: bool = False):
    obj = db.get(model, id_, with_for_update=lock)
    if obj is None:
        raise not_found(what)
    return obj


def apply(obj, body: BaseModel, fields: tuple[str, ...]) -> dict:
    """Copy the given fields that were sent; returns {field: [old, new]} of what changed."""
    sent = body.model_fields_set
    before = {f: getattr(obj, f) for f in fields if f in sent}
    for f in before:
        setattr(obj, f, getattr(body, f))
    return diff({k: _plain(v) for k, v in before.items()},
                {f: _plain(getattr(obj, f)) for f in before})  # fmt: skip


def _plain(v):
    return str(v) if isinstance(v, (Decimal, date, uuid.UUID)) else v


# ---------- reference data, numbering, rules ----------

UNITS = {"PCS": "Pieces", "SET": "Sets", "M": "Metres", "KG": "Kilograms", "L": "Litres"}


def seed_reference_data(db: Session) -> None:
    """Units every installation starts with; safe to run any number of times."""
    have = set(db.scalars(select(Unit.code)))
    db.add_all(Unit(code=c, name=n) for c, n in UNITS.items() if c not in have)
    db.flush()


# doc_type -> default prefix. SMET can change prefixes and padding (open question 11).
SEQUENCES = {"purchase_request": "PR", "supplier_quote": "SQ", "purchase_order": "PO",
             "goods_receipt": "GRN", "supplier_return": "SRN", "adjustment": "ADJ",
             "stock_count": "CNT", "assembly_order": "AO", "serial": "SMET", "enquiry": "ENQ",
             "quotation": "QT", "sales_order": "SO", "project": "PRJ", "dispatch": "DN",
             "battery_bank": "BNK", "site_visit": "VIS"}  # fmt: skip


def next_number(db: Session, doc_type: str) -> str:
    """The next number, e.g. PO-2026-00001. The row lock makes concurrent callers wait, so no
    number is ever given twice; a rolled-back transaction gives its number back."""
    db.execute(
        text(
            "INSERT INTO number_sequences (doc_type, prefix) VALUES (:t, :p) ON CONFLICT DO NOTHING"
        ),
        {"t": doc_type, "p": SEQUENCES[doc_type]},
    )
    seq = db.scalar(
        select(NumberSequence).where(NumberSequence.doc_type == doc_type).with_for_update()
    )
    year = date.today().year
    if seq.yearly and seq.year != year:
        seq.year, seq.next_value = year, 1
    n = seq.next_value
    seq.next_value += 1
    middle = f"-{year}" if seq.yearly else ""
    return f"{seq.prefix}{middle}-{n:0{seq.padding}d}"


# Business rules SMET can change without a code change. Each default is the cautious choice
# (docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md, questions 6 and 9).
RULE_DEFAULTS: dict = {
    # Purchase orders in the company currency at or below this total are approved on submission;
    # null = every purchase order needs approval. Other currencies always need approval.
    "po_auto_approve_up_to": None,
    "po_self_approval": False,  # may the person who drafted or submitted a PO approve it?
    "adjustment_self_approval": False,  # may the person who asked for a correction approve it?
    # Quotations in the company currency at or below this total, with no line discount above
    # quote_max_discount_pct, are approved on submission; null = every quotation needs approval.
    "quote_auto_approve_up_to": None,
    "quote_max_discount_pct": 0,
    "quote_self_approval": False,
    "quote_valid_days": 30,  # default validity of a new quotation (question 10b)
    # Photographs a visit needs before it can complete (assumption A20, question 31).
    "install_photos_min": 1,
    "commission_photos_min": 0,
    "handover_photos_min": 0,
    "handover_with_open_issues": False,  # may a handover complete with open issues on its visit?
}


def rules(db: Session) -> dict:
    return RULE_DEFAULTS | (db.get(Company, 1).rules or {})


class RulesPatch(BaseModel):
    version: int
    po_auto_approve_up_to: (
        Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)] | None
    ) = None
    po_self_approval: bool | None = None
    adjustment_self_approval: bool | None = None
    quote_auto_approve_up_to: (
        Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)] | None
    ) = None
    quote_max_discount_pct: (
        Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] | None
    ) = None
    quote_self_approval: bool | None = None
    quote_valid_days: int | None = Field(default=None, ge=1, le=365)
    install_photos_min: int | None = Field(default=None, ge=0, le=20)
    commission_photos_min: int | None = Field(default=None, ge=0, le=20)
    handover_photos_min: int | None = Field(default=None, ge=0, le=20)
    handover_with_open_issues: bool | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


@router.get("/rules")
def get_rules(_: require("settings.view"), db: DB) -> dict:
    return {"rules": rules(db), "version": db.get(Company, 1).version}


@router.patch("/rules")
def update_rules(body: RulesPatch, p: require("settings.configure"), db: DB) -> dict:
    c = db.get(Company, 1, with_for_update=True)
    if c.version != body.version:
        raise stale()
    before = rules(db)
    after = dict(before)
    for k in body.model_fields_set - {"version", "reason"}:
        v = getattr(body, k)
        if v is None and k not in ("po_auto_approve_up_to", "quote_auto_approve_up_to"):
            continue
        after[k] = str(v) if isinstance(v, Decimal) else v
    c.rules = {k: after[k] for k in RULE_DEFAULTS}
    c.version += 1
    record(
        db,
        p.user,
        "rules.update",
        "company",
        1,
        "Business rules changed",
        diff(before, after),
        body.reason,
    )
    db.commit()
    return {"rules": rules(db), "version": c.version}


class SequencePut(BaseModel):
    prefix: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=12,
                                             pattern=r"^[A-Za-z0-9]+$")]  # fmt: skip
    padding: int = Field(ge=3, le=9)
    yearly: bool


def _seq_row(db: Session, doc_type: str) -> dict:
    s = db.get(NumberSequence, doc_type)
    prefix, padding, yearly = (
        (s.prefix, s.padding, s.yearly) if s else (SEQUENCES[doc_type], 5, True)
    )
    year = date.today().year
    n = s.next_value if s and (not yearly or s.year == year) else 1
    return {"doc_type": doc_type, "prefix": prefix, "padding": padding, "yearly": yearly,
            "next": f"{prefix}{f'-{year}' if yearly else ''}-{n:0{padding}d}"}  # fmt: skip


@router.get("/numbering")
def list_numbering(_: require("settings.view"), db: DB) -> dict:
    return {"items": [_seq_row(db, t) for t in SEQUENCES]}


@router.put("/numbering/{doc_type}")
def set_numbering(
    doc_type: str, body: SequencePut, p: require("settings.configure"), db: DB
) -> dict:
    if doc_type not in SEQUENCES:
        raise not_found("Document type")
    db.execute(
        text(
            "INSERT INTO number_sequences (doc_type, prefix) VALUES (:t, :p) ON CONFLICT DO NOTHING"
        ),
        {"t": doc_type, "p": SEQUENCES[doc_type]},
    )
    s = db.get(NumberSequence, doc_type, with_for_update=True)
    before = {"prefix": s.prefix, "padding": s.padding, "yearly": s.yearly}
    s.prefix, s.padding, s.yearly = body.prefix.upper(), body.padding, body.yearly
    # Numbers already issued stay as they are; only new documents use the new format.
    record(db, p.user, "numbering.update", "numbering", doc_type, f"Numbering for {doc_type} changed",
           diff(before, {"prefix": s.prefix, "padding": s.padding, "yearly": s.yearly}))  # fmt: skip
    db.commit()
    return _seq_row(db, doc_type)


# ---------- units ----------


class UnitIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1,
                                           max_length=12, pattern=r"^[A-Za-z0-9]+$")]  # fmt: skip
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]


class UnitPatch(BaseModel):
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None
    ) = None
    active: bool | None = None


def _unit(u: Unit) -> dict:
    return {"code": u.code, "name": u.name, "active": u.active}


@router.get("/units")
def list_units(_: require("items.view"), db: DB) -> dict:
    return {"items": [_unit(u) for u in db.scalars(select(Unit).order_by(Unit.code))]}


@router.post("/units", status_code=status.HTTP_201_CREATED)
def create_unit(body: UnitIn, p: require("items.configure"), db: DB) -> dict:
    if db.get(Unit, body.code):
        raise field_error("code", "This unit already exists")
    u = Unit(code=body.code, name=body.name)
    db.add(u)
    record(db, p.user, "unit.create", "unit", u.code, f"Unit {u.code} added")
    db.commit()
    return _unit(u)


@router.patch("/units/{code}")
def update_unit(code: str, body: UnitPatch, p: require("items.configure"), db: DB) -> dict:
    u = get_or_404(db, Unit, code.upper(), "Unit", lock=True)
    changes = apply(u, body, tuple(f for f in ("name", "active") if getattr(body, f) is not None))
    record(db, p.user, "unit.update", "unit", u.code, f"Unit {u.code} changed", changes)
    db.commit()
    return _unit(u)


# ---------- items ----------

ItemKind = Literal["battery_model", "battery_unit", "component", "accessory", "material"]
Tracking = Literal["none", "lot", "serial"]
KIND_LABEL = {"battery_model": "Battery model", "battery_unit": "Battery unit (purchased)",
              "component": "Component", "accessory": "Accessory", "material": "Assembly material"}  # fmt: skip
Specs = Annotated[dict[Annotated[str, StringConstraints(max_length=60)],
                       Annotated[str, StringConstraints(max_length=200)]], Field(max_length=30)]  # fmt: skip
Measure = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]


class ItemIn(BaseModel):
    code: Code
    name: Name
    kind: ItemKind
    category: Short = ""
    unit_code: str = Field(max_length=12)
    tracking: Tracking = "none"
    assembled: bool = False
    capacity_ah: Measure | None = None
    voltage_v: Measure | None = None
    chemistry: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    specs: Specs = {}
    min_stock: Qty = Decimal(0)
    notes: Long = ""


class ItemPatch(BaseModel):
    version: int
    name: Name | None = None
    kind: ItemKind | None = None
    category: Short | None = None
    unit_code: str | None = Field(default=None, max_length=12)
    tracking: Tracking | None = None
    assembled: bool | None = None
    capacity_ah: Measure | None = None
    voltage_v: Measure | None = None
    chemistry: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] | None = None
    specs: Specs | None = None
    min_stock: Qty | None = None
    notes: Long | None = None
    active: bool | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


ITEM_FIELDS = ("name", "kind", "category", "unit_code", "tracking", "assembled", "capacity_ah",
               "voltage_v", "chemistry", "specs", "min_stock", "notes", "active")  # fmt: skip
LOCKED_ITEM_FIELDS = ("kind", "unit_code", "tracking", "assembled")


def item_row(i: Item) -> dict:
    return {"id": i.id, "code": i.code, "name": i.name, "kind": i.kind, "category": i.category,
            "unit_code": i.unit_code, "tracking": i.tracking, "assembled": i.assembled,
            "capacity_ah": i.capacity_ah, "voltage_v": i.voltage_v, "chemistry": i.chemistry,
            "specs": i.specs, "min_stock": i.min_stock, "notes": i.notes, "active": i.active,
            "version": i.version, "created_at": i.created_at}  # fmt: skip


def item_ref(i: Item) -> dict:
    return {
        "id": i.id,
        "code": i.code,
        "name": i.name,
        "unit_code": i.unit_code,
        "tracking": i.tracking,
    }


def _check_item(db: Session, i: Item) -> None:
    unit = db.get(Unit, i.unit_code)
    if unit is None or not unit.active:
        raise field_error("unit_code", "Choose an active unit")
    if i.assembled and i.kind != "battery_model":
        raise field_error("assembled", "Only battery models are assembled")
    if i.assembled and i.tracking != "serial":
        raise field_error("tracking", "Assembled batteries get a serial number each: choose serial")


def item_in_use(db: Session, item_id) -> bool:
    return bool(db.scalar(select(
        exists().where(StockMove.item_id == item_id)
        | exists().where(PurchaseOrderLine.item_id == item_id)
        | exists().where(PurchaseRequestLine.item_id == item_id)
        | exists().where(SupplierQuoteLine.item_id == item_id)
        | exists().where(BomLine.component_id == item_id)
        | exists().where(Bom.item_id == item_id)
        | exists().where(AssemblyOrder.item_id == item_id)
        | exists().where(Serial.item_id == item_id)
        | exists().where(Reservation.item_id == item_id)
    )))  # fmt: skip


@router.get("/items")
def list_items(
    _: require("items.view"),
    db: DB,
    q: str = Query("", max_length=100),
    kind: ItemKind | None = None,
    active: bool | None = True,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Item)
    if q:
        query = query.where(or_(Item.code.ilike(f"%{q}%"), Item.name.ilike(f"%{q}%"),
                                Item.category.ilike(f"%{q}%")))  # fmt: skip
    if kind:
        query = query.where(Item.kind == kind)
    if active is not None:
        query = query.where(Item.active == active)
    return page(db, query.order_by(Item.code), limit, offset, item_row)


@router.get("/items/options")
def item_options(_: require("items.view"), db: DB, kind: ItemKind | None = None) -> dict:
    """Every active item, a few fields each, for pickers (a few thousand rows stay small)."""
    query = select(
        Item.id, Item.code, Item.name, Item.unit_code, Item.tracking, Item.kind, Item.assembled
    ).where(Item.active)
    if kind:
        query = query.where(Item.kind == kind)
    return {"items": [dict(r._mapping) for r in db.execute(query.order_by(Item.code))]}


@router.get("/items/{item_id}")
def get_item(item_id: uuid.UUID, _: require("items.view"), db: DB) -> dict:
    i = get_or_404(db, Item, item_id, "Item")
    return item_row(i) | {"in_use": item_in_use(db, i.id)}


@router.post("/items", status_code=status.HTTP_201_CREATED)
def create_item(body: ItemIn, p: require("items.edit"), db: DB) -> dict:
    i = Item(**body.model_dump())
    _check_item(db, i)
    db.add(i)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("code", "This code is already in use") from None
    record(db, p.user, "item.create", "item", i.id, f"Item {i.code} added")
    db.commit()
    return item_row(i)


@router.patch("/items/{item_id}")
def update_item(item_id: uuid.UUID, body: ItemPatch, p: require("items.edit"), db: DB) -> dict:
    i = get_or_404(db, Item, item_id, "Item", lock=True)
    if i.version != body.version:
        raise stale()
    sent = {f for f in ITEM_FIELDS if f in body.model_fields_set}
    nullable = {"capacity_ah", "voltage_v"}
    sent = {f for f in sent if getattr(body, f) is not None or f in nullable}
    locked = [f for f in LOCKED_ITEM_FIELDS if f in sent and getattr(body, f) != getattr(i, f)]
    if locked and item_in_use(db, i.id):
        raise field_error(locked[0], "This item is already used in stock, orders or a bill of materials, "
                          "so this can't change. Create a new item instead.")  # fmt: skip
    changes = apply(i, body, tuple(sent))
    _check_item(db, i)
    i.version += 1
    record(db, p.user, "item.update", "item", i.id, f"Item {i.code} changed", changes, body.reason)
    db.commit()
    return item_row(i)


# ---------- suppliers, customers, distributors, dealers ----------

PartyKind = Literal["supplier", "customer", "distributor", "dealer"]


class Contact(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    role: Short = ""
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] = ""


Kinds = Annotated[list[PartyKind], Field(min_length=1, max_length=4)]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class PartyIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1,
                                           max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]  # fmt: skip
    name: Name
    kinds: Kinds
    contacts: list[Contact] = Field(default=[], max_length=20)
    address: Long = ""
    terms: Long = ""
    currency: Currency = "AED"
    notes: Long = ""


class PartyPatch(BaseModel):
    version: int
    name: Name | None = None
    kinds: Kinds | None = None
    contacts: list[Contact] | None = Field(default=None, max_length=20)
    address: Long | None = None
    terms: Long | None = None
    currency: Currency | None = None
    notes: Long | None = None
    active: bool | None = None
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = ""


def party_row(x: Party) -> dict:
    return {"id": x.id, "code": x.code, "name": x.name, "kinds": x.kinds, "contacts": x.contacts,
            "address": x.address, "terms": x.terms, "currency": x.currency, "notes": x.notes,
            "active": x.active, "version": x.version}  # fmt: skip


@router.get("/parties")
def list_parties(
    _: require("parties.view"),
    db: DB,
    q: str = Query("", max_length=100),
    kind: PartyKind | None = None,
    active: bool | None = True,
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Party)
    if q:
        query = query.where(or_(Party.code.ilike(f"%{q}%"), Party.name.ilike(f"%{q}%")))
    if kind:
        query = query.where(any_(Party.kinds) == kind)
    if active is not None:
        query = query.where(Party.active == active)
    return page(db, query.order_by(Party.name, Party.id), limit, offset, party_row)


def _supplied(db: Session, party_id) -> list[dict]:
    rows = db.execute(select(SupplierItem, Item).join(Item, Item.id == SupplierItem.item_id)
                      .where(SupplierItem.party_id == party_id).order_by(Item.code))  # fmt: skip
    return [{"item": item_ref(i), "supplier_code": s.supplier_code, "last_price": s.last_price,
             "currency": s.currency, "lead_days": s.lead_days} for s, i in rows]  # fmt: skip


@router.get("/parties/{party_id}")
def get_party(party_id: uuid.UUID, _: require("parties.view"), db: DB) -> dict:
    x = get_or_404(db, Party, party_id, "Supplier or customer")
    return party_row(x) | {"supplied_items": _supplied(db, x.id)}


def _contacts(contacts: list[Contact]) -> list[dict]:
    bad = [
        c.email
        for c in contacts
        if c.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", c.email)
    ]
    if bad:
        raise field_error("contacts", f"Not an email address: {bad[0][:60]}")
    return [c.model_dump() for c in contacts]


@router.post("/parties", status_code=status.HTTP_201_CREATED)
def create_party(body: PartyIn, p: require("parties.edit"), db: DB) -> dict:
    x = Party(**body.model_dump(exclude={"contacts", "kinds"}), kinds=sorted(set(body.kinds)),
              contacts=_contacts(body.contacts))  # fmt: skip
    db.add(x)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("code", "This code is already in use") from None
    record(db, p.user, "party.create", "party", x.id, f"{x.name} added ({', '.join(x.kinds)})")
    db.commit()
    return party_row(x)


@router.patch("/parties/{party_id}")
def update_party(party_id: uuid.UUID, body: PartyPatch, p: require("parties.edit"), db: DB) -> dict:
    x = get_or_404(db, Party, party_id, "Supplier or customer", lock=True)
    if x.version != body.version:
        raise stale()
    if body.contacts is not None:
        body.contacts = _contacts(body.contacts)
    if body.kinds is not None:
        body.kinds = sorted(set(body.kinds))
    fields = ("name", "kinds", "contacts", "address", "terms", "currency", "notes", "active")
    changes = apply(x, body, tuple(f for f in fields if getattr(body, f) is not None))
    x.version += 1
    record(db, p.user, "party.update", "party", x.id, f"{x.name} changed", changes, body.reason)
    db.commit()
    return party_row(x)


class SuppliedItem(BaseModel):
    item_id: uuid.UUID
    supplier_code: Annotated[str, StringConstraints(strip_whitespace=True, max_length=60)] = ""
    last_price: Annotated[Decimal, Field(ge=0, max_digits=16, decimal_places=4)] | None = None
    currency: Currency = "AED"
    lead_days: int | None = Field(default=None, ge=0, le=730)


@router.put("/parties/{party_id}/items")
def set_supplied_items(party_id: uuid.UUID, body: Annotated[list[SuppliedItem], Field(max_length=500)],
                       p: require("parties.edit"), db: DB) -> dict:  # fmt: skip
    x = get_or_404(db, Party, party_id, "Supplier", lock=True)
    if "supplier" not in x.kinds:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only suppliers have supplied items")
    ids = [s.item_id for s in body]
    if len(set(ids)) != len(ids):
        raise field_error("item_id", "Each item only once")
    if ids and db.scalar(select(func.count()).where(Item.id.in_(ids))) != len(ids):
        raise field_error("item_id", "Unknown item")
    db.query(SupplierItem).filter(SupplierItem.party_id == x.id).delete()
    db.add_all(SupplierItem(party_id=x.id, **s.model_dump()) for s in body)
    record(
        db, p.user, "party.supplied_items", "party", x.id, f"{x.name}: {len(body)} supplied items"
    )
    db.commit()
    return {"supplied_items": _supplied(db, x.id)}


# ---------- warehouses and locations ----------

Purpose = Literal[
    "available", "inspection", "quarantine", "rejected", "assembly", "repair", "returns"
]
PURPOSE_LABEL = {"available": "Available stock", "inspection": "Awaiting inspection",
                 "quarantine": "On hold (quarantine)", "rejected": "Rejected, to return",
                 "assembly": "Under assembly", "repair": "Under repair", "returns": "Returned"}  # fmt: skip
# Every new warehouse gets one area of each kind the workflows need; SMET adds racks and bays.
STANDARD_LOCATIONS = [("STORE", "Main store", "available"), ("INSP", "Receiving inspection", "inspection"),
                      ("QUAR", "Quarantine", "quarantine"), ("REJ", "Rejected goods", "rejected"),
                      ("ASSY", "Assembly floor", "assembly"), ("RET", "Customer returns", "returns")]  # fmt: skip


class WarehouseIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1,
                                           max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]  # fmt: skip
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    address: Long = ""


class WarehousePatch(BaseModel):
    version: int
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
        | None
    ) = None
    address: Long | None = None
    active: bool | None = None


class LocationIn(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1,
                                           max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")]  # fmt: skip
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    purpose: Purpose


class LocationPatch(BaseModel):
    version: int
    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
        | None
    ) = None
    purpose: Purpose | None = None
    active: bool | None = None


def location_row(loc: Location, wh: Warehouse | None = None) -> dict:
    return {"id": loc.id, "warehouse_id": loc.warehouse_id, "code": loc.code, "name": loc.name,
            "purpose": loc.purpose, "active": loc.active, "version": loc.version,
            **({"warehouse": wh.code, "label": f"{wh.code} / {loc.code}"} if wh else {})}  # fmt: skip


def _warehouse_rows(db: Session, whs: list[Warehouse]) -> list[dict]:
    locs: dict = {}
    for loc in db.scalars(select(Location).where(Location.warehouse_id.in_([w.id for w in whs]))
                          .order_by(Location.code)):  # fmt: skip
        locs.setdefault(loc.warehouse_id, []).append(loc)
    return [{"id": w.id, "code": w.code, "name": w.name, "address": w.address, "active": w.active,
             "version": w.version, "locations": [location_row(x, w) for x in locs.get(w.id, [])]}
            for w in whs]  # fmt: skip


@router.get("/warehouses")
def list_warehouses(_: require("warehouses.view"), db: DB) -> dict:
    return {"items": _warehouse_rows(db, db.scalars(select(Warehouse).order_by(Warehouse.code)).all()),
            "purposes": PURPOSE_LABEL}  # fmt: skip


@router.post("/warehouses", status_code=status.HTTP_201_CREATED)
def create_warehouse(body: WarehouseIn, p: require("warehouses.configure"), db: DB) -> dict:
    w = Warehouse(**body.model_dump())
    db.add(w)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("code", "This code is already in use") from None
    db.add_all(
        Location(warehouse_id=w.id, code=c, name=n, purpose=pu) for c, n, pu in STANDARD_LOCATIONS
    )
    record(db, p.user, "warehouse.create", "warehouse", w.id, f"Warehouse {w.code} added")
    db.commit()
    return _warehouse_rows(db, [w])[0]


@router.patch("/warehouses/{warehouse_id}")
def update_warehouse(warehouse_id: uuid.UUID, body: WarehousePatch, p: require("warehouses.configure"),
                     db: DB) -> dict:  # fmt: skip
    w = get_or_404(db, Warehouse, warehouse_id, "Warehouse", lock=True)
    if w.version != body.version:
        raise stale()
    if body.active is False and _holds_stock(db, Location.warehouse_id == w.id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This warehouse still holds stock. Move it out first."
        )
    changes = apply(
        w, body, tuple(f for f in ("name", "address", "active") if getattr(body, f) is not None)
    )
    w.version += 1
    record(
        db, p.user, "warehouse.update", "warehouse", w.id, f"Warehouse {w.code} changed", changes
    )
    db.commit()
    return _warehouse_rows(db, [w])[0]


def _holds_stock(db: Session, where) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(StockBalance.qty > 0, StockBalance.location_id == Location.id, where)
            )
        )
    )


@router.post("/warehouses/{warehouse_id}/locations", status_code=status.HTTP_201_CREATED)
def create_location(warehouse_id: uuid.UUID, body: LocationIn, p: require("warehouses.configure"),
                    db: DB) -> dict:  # fmt: skip
    w = get_or_404(db, Warehouse, warehouse_id, "Warehouse")
    loc = Location(warehouse_id=w.id, **body.model_dump())
    db.add(loc)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("code", "This warehouse already has a location with this code") from None
    record(db, p.user, "location.create", "location", loc.id, f"Location {w.code}/{loc.code} added")
    db.commit()
    return location_row(loc, w)


@router.patch("/locations/{location_id}")
def update_location(location_id: uuid.UUID, body: LocationPatch, p: require("warehouses.configure"),
                    db: DB) -> dict:  # fmt: skip
    loc = get_or_404(db, Location, location_id, "Location", lock=True)
    if loc.version != body.version:
        raise stale()
    changing_use = (
        body.purpose is not None and body.purpose != loc.purpose
    ) or body.active is False
    if changing_use and _holds_stock(db, Location.id == loc.id):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "This location holds stock, so its use can't change. Move the stock first.")  # fmt: skip
    changes = apply(
        loc, body, tuple(f for f in ("name", "purpose", "active") if getattr(body, f) is not None)
    )
    loc.version += 1
    w = db.get(Warehouse, loc.warehouse_id)
    record(
        db,
        p.user,
        "location.update",
        "location",
        loc.id,
        f"Location {w.code}/{loc.code} changed",
        changes,
    )
    db.commit()
    return location_row(loc, w)
