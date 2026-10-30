"""Phase 4 projects: projects and sites (with surveys), milestones, tasks, notes, changes that need
approval, battery requirement sheets with engineering approval, and the sizing-rule framework.

No sizing formula is shipped or invented. SMET enters its own rules (inputs and an arithmetic
expression); the result is shown with its inputs on the requirement line, and the quantity is
always approved by an engineer (projects.approve) before stock, purchases or assembly follow.
"""

import ast
import math
import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import AfterValidator, BaseModel, Field, StringConstraints
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.admin import field_error, stale
from app.assembly import OrderIn, plan_order
from app.audit import diff, record
from app.auth import DB, require
from app.inventory import Key, claim_key, conflict, free, lock_items, remember, reserve
from app.masters import Limit, NumbersRoute, Offset, get_or_404, item_ref, next_number, page
from app.models import (
    AssemblyOrder,
    Item,
    Milestone,
    Party,
    Project,
    ProjectChange,
    ProjectNote,
    ProjectTask,
    PurchaseRequest,
    Quotation,
    RequirementLine,
    RequirementSheet,
    Reservation,
    SalesOrder,
    Site,
    SizingRule,
    User,
    utcnow,
)
from app.procurement import PRIn, PRLineIn, create_pr, pr_row
from app.sales import (
    CUSTOMER_KINDS,
    QuotationIn,
    QuoteLineIn,
    create_quotation,
    party_ref,
    quotation_row,
)

router = APIRouter(tags=["projects"], route_class=NumbersRoute)
ZERO = Decimal(0)
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return dict(db.execute(select(User.id, User.name).where(User.id.in_(ids))).all()) if ids else {}


def _active_user(db: Session, user_id, field: str) -> None:
    if user_id:
        u = db.get(User, user_id)
        if u is None or not u.active:
            raise field_error(field, "Choose an active user")


# ---------- the sizing-rule evaluator ----------

FUNCS = {"ceil": math.ceil, "floor": math.floor, "round": round, "max": max, "min": min}
_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call,
          ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)  # fmt: skip


def check_expression(expr: str, names: set[str]) -> ast.Expression:
    """Arithmetic only: numbers, the rule's inputs, + − × ÷, brackets and ceil/floor/round/max/min.
    Anything else (attributes, indexing, strings, other calls) is refused before it can run."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise field_error("expression", "Not a valid formula") from None
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    for node in ast.walk(tree):
        if not isinstance(node, _NODES):
            raise field_error("expression", f"Not allowed in a formula: {type(node).__name__}")
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise field_error("expression", "Only numbers are allowed")
            if abs(node.value) > 1e12:
                raise field_error("expression", "Numbers in a formula stay below 10^12")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords
        ):
            raise field_error("expression", "Allowed functions: ceil, floor, round, max, min")
        # a function name only as a call, an input name only as a value
        if isinstance(node, ast.Name) and (
            node.id not in FUNCS if id(node) in called else node.id not in names
        ):
            raise field_error("expression", f"Unknown input: {node.id}")
    return tree


def evaluate(expr: str, values: dict[str, float]) -> float:
    tree = check_expression(expr, set(values))

    def run(n):
        match n:
            case ast.Expression():
                return run(n.body)
            case ast.Constant():
                return n.value
            case ast.Name():
                return values[n.id]
            case ast.UnaryOp():
                return -run(n.operand) if isinstance(n.op, ast.USub) else run(n.operand)
            case ast.BinOp():
                a, b = run(n.left), run(n.right)
                if isinstance(n.op, ast.Add):
                    return a + b
                if isinstance(n.op, ast.Sub):
                    return a - b
                if isinstance(n.op, ast.Mult):
                    return a * b
                if b == 0:
                    raise field_error("inputs", "The formula divides by zero with these inputs")
                return a / b
            case ast.Call():
                try:
                    return FUNCS[n.func.id](*[run(x) for x in n.args])
                except (TypeError, ValueError):
                    raise field_error(
                        "expression", f"{n.func.id}() needs other arguments"
                    ) from None

    try:
        result = run(tree)
    except OverflowError:
        raise field_error(
            "inputs", "The formula gives a number too large with these inputs"
        ) from None
    if (
        not isinstance(result, (int, float))
        or math.isnan(result)
        or math.isinf(result)
        or abs(result) > 1e9
    ):
        raise field_error("inputs", "The formula gives no usable number with these inputs")
    return float(result)


def _unique_inputs(rows: list) -> list:
    names = [r.name for r in rows]
    if len(set(names)) != len(names):
        raise ValueError("Each input name only once")
    if clash := set(names) & set(FUNCS):
        raise ValueError(f"{sorted(clash)[0]} is a function name; choose another input name")
    return rows


class RuleInput(BaseModel):
    name: Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]{0,39}$")]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = ""


class RuleIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
    description: Text = ""
    inputs: Annotated[
        list[RuleInput], Field(min_length=1, max_length=20), AfterValidator(_unique_inputs)
    ]
    expression: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]
    result_unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = (
        "batteries"
    )
    confirmed: bool = False
    active: bool = True


class RulePut(RuleIn):
    version: int
    reason: Reason = ""


Values = Annotated[dict[str, Annotated[float, Field(ge=-1e9, le=1e9)]], Field(max_length=20)]


def rule_row(r: SizingRule) -> dict:
    return {"id": r.id, "name": r.name, "description": r.description, "inputs": r.inputs, "expression": r.expression,
            "result_unit": r.result_unit, "confirmed": r.confirmed, "active": r.active, "version": r.version}  # fmt: skip


@router.get("/sizing-rules")
def list_rules(_: require("projects.view"), db: DB) -> dict:
    return {
        "items": [
            rule_row(r)
            for r in db.scalars(
                select(SizingRule).order_by(SizingRule.active.desc(), SizingRule.name)
            )
        ]
    }


@router.post("/sizing-rules", status_code=status.HTTP_201_CREATED)
def create_rule(body: RuleIn, p: require("projects.configure"), db: DB) -> dict:
    check_expression(body.expression, {i.name for i in body.inputs})
    r = SizingRule(
        **body.model_dump(exclude={"inputs"}), inputs=[i.model_dump() for i in body.inputs]
    )
    db.add(r)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("name", "A rule with this name exists") from None
    record(db, p.user, "sizing_rule.create", "sizing_rule", r.id, f"Sizing rule {r.name} added",
           {"expression": [None, r.expression], "confirmed": [None, r.confirmed]})  # fmt: skip
    db.commit()
    return rule_row(r)


@router.put("/sizing-rules/{rule_id}")
def update_rule(
    rule_id: uuid.UUID, body: RulePut, p: require("projects.configure"), db: DB
) -> dict:
    r = get_or_404(db, SizingRule, rule_id, "Sizing rule", lock=True)
    if r.version != body.version:
        raise stale()
    check_expression(body.expression, {i.name for i in body.inputs})
    before = rule_row(r)
    for k, v in body.model_dump(exclude={"version", "reason", "inputs"}).items():
        setattr(r, k, v)
    r.inputs, r.version = [i.model_dump() for i in body.inputs], r.version + 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise field_error("name", "A rule with this name exists") from None
    record(db, p.user, "sizing_rule.update", "sizing_rule", r.id, f"Sizing rule {r.name} changed",
           diff({k: before[k] for k in ("expression", "inputs", "confirmed", "active")},
                {k: getattr(r, k) for k in ("expression", "inputs", "confirmed", "active")}), body.reason)  # fmt: skip
    db.commit()
    return rule_row(r)


def calculate(rule: SizingRule, inputs: dict[str, float]) -> dict:
    names = [i["name"] for i in rule.inputs]
    if missing := [n for n in names if n not in inputs]:
        raise field_error("inputs", f"Enter {', '.join(missing)}")
    values = {n: inputs[n] for n in names}
    result = evaluate(rule.expression, values)
    return {"rule_id": str(rule.id), "rule": rule.name, "expression": rule.expression, "inputs": values,
            "result": round(result, 4), "result_unit": rule.result_unit, "confirmed": rule.confirmed,
            "suggested_qty": math.ceil(result - 1e-9)}  # fmt: skip


class EvaluateIn(BaseModel):
    inputs: Values


@router.post("/sizing-rules/{rule_id}/evaluate")
def try_rule(rule_id: uuid.UUID, body: EvaluateIn, _: require("projects.view"), db: DB) -> dict:
    return calculate(get_or_404(db, SizingRule, rule_id, "Sizing rule"), body.inputs)


# ---------- projects and sites ----------

ProjectStatus = Literal["planning", "active", "on_hold", "completed", "cancelled"]


class Contact(BaseModel):
    name: Name
    role: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""
    phone: Annotated[str, StringConstraints(strip_whitespace=True, max_length=40)] = ""
    email: Annotated[str, StringConstraints(strip_whitespace=True, max_length=254)] = ""


class ProjectIn(BaseModel):
    name: Name
    customer_id: uuid.UUID
    scope: Text = ""
    contacts: Annotated[list[Contact], Field(max_length=20)] = []
    manager_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None


class ProjectPatch(BaseModel):
    version: int
    name: Name | None = None
    status: ProjectStatus | None = None
    scope: Text | None = None
    contacts: Annotated[list[Contact], Field(max_length=20)] | None = None
    manager_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    reason: Reason = ""


class SiteIn(BaseModel):
    name: Name
    address: Text = ""
    contact: Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)] = ""
    survey_date: date | None = None
    surveyed_by: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    survey_notes: Text = ""
    requirements: Annotated[dict[Annotated[str, StringConstraints(max_length=60)], Annotated[str, StringConstraints(max_length=300)]],
                            Field(max_length=40)] = {}  # fmt: skip


class SitePut(SiteIn):
    version: int


def site_row(s: Site) -> dict:
    return {"id": s.id, "project_id": s.project_id, "name": s.name, "address": s.address, "contact": s.contact,
            "survey_date": s.survey_date, "surveyed_by": s.surveyed_by, "survey_notes": s.survey_notes,
            "requirements": s.requirements, "version": s.version}  # fmt: skip


def project_row(db: Session, pr: Project, full: bool = False) -> dict:
    names = _names(db, [pr.manager_id])
    row = {"id": pr.id, "number": pr.number, "name": pr.name, "customer": party_ref(db.get(Party, pr.customer_id)),
           "status": pr.status, "scope": pr.scope, "contacts": pr.contacts, "manager_id": pr.manager_id,
           "manager": names.get(pr.manager_id), "start_date": pr.start_date, "end_date": pr.end_date,
           "created_at": pr.created_at, "version": pr.version}  # fmt: skip
    if not full:
        nxt = db.execute(select(Milestone.title, Milestone.due_date).where(Milestone.project_id == pr.id, Milestone.done_at.is_(None))
                         .order_by(Milestone.due_date.nulls_last()).limit(1)).first()  # fmt: skip
        row["next_milestone"] = {"title": nxt[0], "due_date": nxt[1]} if nxt else None
        return row
    sites = db.scalars(select(Site).where(Site.project_id == pr.id).order_by(Site.created_at)).all()
    tasks = db.scalars(
        select(ProjectTask)
        .where(ProjectTask.project_id == pr.id)
        .order_by(ProjectTask.status, ProjectTask.due_date.nulls_last())
    ).all()
    changes = db.scalars(
        select(ProjectChange)
        .where(ProjectChange.project_id == pr.id)
        .order_by(ProjectChange.created_at.desc())
    ).all()
    notes = db.scalars(
        select(ProjectNote)
        .where(ProjectNote.project_id == pr.id)
        .order_by(ProjectNote.created_at.desc())
        .limit(50)
    ).all()
    people = _names(db, [t.assignee_id for t in tasks] + [c.requested_by for c in changes] + [c.decided_by for c in changes]
                    + [n.author_id for n in notes])  # fmt: skip
    pid = str(pr.id)
    row |= {
        "sites": [site_row(s) for s in sites],
        "milestones": [{"id": m.id, "site_id": m.site_id, "kind": m.kind, "title": m.title, "due_date": m.due_date,
                        "done_at": m.done_at, "version": m.version}
                       for m in db.scalars(select(Milestone).where(Milestone.project_id == pr.id).order_by(Milestone.due_date.nulls_last()))],
        "tasks": [{"id": t.id, "site_id": t.site_id, "title": t.title, "assignee_id": t.assignee_id,
                   "assignee": people.get(t.assignee_id), "due_date": t.due_date, "status": t.status, "version": t.version} for t in tasks],
        "changes": [{"id": c.id, "kind": c.kind, "description": c.description, "status": c.status,
                     "requested_by": people.get(c.requested_by), "requested_by_id": c.requested_by,
                     "decided_by": people.get(c.decided_by), "decision_reason": c.decision_reason,
                     "created_at": c.created_at, "version": c.version} for c in changes],
        "notes": [{"id": n.id, "site_id": n.site_id, "text": n.text, "pending_action": n.pending_action,
                   "author": people.get(n.author_id), "created_at": n.created_at} for n in notes],
        "sheets": [sheet_row(db, s) for s in db.scalars(select(RequirementSheet).where(RequirementSheet.project_id == pr.id)
                                                         .order_by(RequirementSheet.version_no.desc()))],
        "materials": materials(db, pr),
        "quotations": [{"id": q.id, "number": q.number} for q in db.scalars(select(Quotation).where(Quotation.project_id == pr.id))],
        "sales_orders": [{"id": s.id, "number": s.number, "status": s.status}
                         for s in db.scalars(select(SalesOrder).where(SalesOrder.project_id == pr.id))],
        "purchase_requests": [{"id": r.id, "number": r.number, "status": r.status}
                              for r in db.scalars(select(PurchaseRequest).where(PurchaseRequest.ref == pr.number))],
        "assembly_orders": [{"id": a.id, "number": a.number, "status": a.status}
                            for a in db.scalars(select(AssemblyOrder).where(AssemblyOrder.source_ref == pr.number))],
        "reservations": db.scalar(select(func.count()).where(Reservation.ref_type == "project", Reservation.ref_id == pid,
                                                             Reservation.status == "active")),
    }  # fmt: skip
    return row


@router.get("/projects")
def list_projects(
    _: require("projects.view"),
    db: DB,
    status_: Annotated[ProjectStatus | None, Query(alias="status")] = None,
    q: str = Query("", max_length=100),
    limit: Limit = 50,
    offset: Offset = 0,
) -> dict:
    query = select(Project)
    if status_:
        query = query.where(Project.status == status_)
    if q:
        query = query.join(Party, Party.id == Project.customer_id).where(
            or_(
                Project.number.ilike(f"%{q}%"),
                Project.name.ilike(f"%{q}%"),
                Party.name.ilike(f"%{q}%"),
            )
        )
    counts = dict(db.execute(select(Project.status, func.count()).group_by(Project.status)).all())
    return page(
        db, query.order_by(Project.created_at.desc()), limit, offset, lambda x: project_row(db, x)
    ) | {"counts": counts}


@router.get("/projects/people")
def project_people(_: require("projects.view"), db: DB) -> dict:
    """Active staff for assigning tasks and managers (names only)."""
    return {
        "items": [
            {"id": i, "name": n}
            for i, n in db.execute(
                select(User.id, User.name).where(User.active).order_by(User.name)
            )
        ]
    }


@router.get("/projects/{project_id}")
def get_project(project_id: uuid.UUID, _: require("projects.view"), db: DB) -> dict:
    return project_row(db, get_or_404(db, Project, project_id, "Project"), full=True)


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectIn, p: require("projects.create"), db: DB) -> dict:
    c = db.get(Party, body.customer_id)
    if c is None or not c.active or not set(CUSTOMER_KINDS) & set(c.kinds):
        raise field_error("customer_id", "Choose an active customer")
    _active_user(db, body.manager_id, "manager_id")
    pr = Project(number=next_number(db, "project"), created_by=p.user.id, contacts=[x.model_dump() for x in body.contacts],
                 **body.model_dump(exclude={"contacts"}))  # fmt: skip
    db.add(pr)
    db.flush()
    db.add_all(Milestone(project_id=pr.id, kind=k, title=t) for k, t in MILESTONES)
    record(db, p.user, "project.create", "project", pr.id, f"{pr.number} {pr.name} created")
    db.commit()
    return project_row(db, pr, full=True)


MILESTONES = [("material_ready", "Material ready"), ("delivery", "Delivery"), ("installation", "Installation"),
              ("commissioning", "Commissioning"), ("handover", "Handover")]  # fmt: skip


def _project(db: Session, project_id, lock: bool = False) -> Project:
    pr = get_or_404(db, Project, project_id, "Project", lock=lock)
    if pr.status in ("completed", "cancelled") and lock:
        raise conflict(f"{pr.number} is {pr.status}")
    return pr


@router.patch("/projects/{project_id}")
def update_project(
    project_id: uuid.UUID, body: ProjectPatch, p: require("projects.create"), db: DB
) -> dict:
    pr = get_or_404(db, Project, project_id, "Project", lock=True)
    if pr.version != body.version:
        raise stale()
    _active_user(db, body.manager_id, "manager_id")
    fields = [
        f
        for f in ("name", "status", "scope", "contacts", "manager_id", "start_date", "end_date")
        if f in body.model_fields_set
        and (
            getattr(body, f) is not None or f in ("manager_id", "start_date", "end_date")
        )  # required fields: null = unchanged
    ]
    before = {f: getattr(pr, f) for f in fields}
    for f in fields:
        v = getattr(body, f)
        setattr(pr, f, [x.model_dump() for x in v] if f == "contacts" else v)
    pr.version += 1
    record(db, p.user, "project.update", "project", pr.id, f"{pr.number} changed",
           diff({k: str(v) for k, v in before.items()}, {f: str(getattr(pr, f)) for f in fields}), body.reason)  # fmt: skip
    db.commit()
    return project_row(db, pr, full=True)


@router.post("/projects/{project_id}/sites", status_code=status.HTTP_201_CREATED)
def add_site(project_id: uuid.UUID, body: SiteIn, p: require("projects.create"), db: DB) -> dict:
    pr = _project(db, project_id, lock=True)
    s = Site(project_id=pr.id, **body.model_dump())
    db.add(s)
    db.flush()
    record(db, p.user, "site.create", "site", s.id, f"{pr.number}: site {s.name} added")
    db.commit()
    return site_row(s)


@router.put("/sites/{site_id}")
def update_site(site_id: uuid.UUID, body: SitePut, p: require("projects.create"), db: DB) -> dict:
    s = get_or_404(db, Site, site_id, "Site", lock=True)
    if s.version != body.version:
        raise stale()
    before = site_row(s)
    for k, v in body.model_dump(exclude={"version"}).items():
        setattr(s, k, v)
    s.version += 1
    record(db, p.user, "site.update", "site", s.id, f"Site {s.name} changed (survey and requirements)",
           diff({k: str(before[k]) for k in SiteIn.model_fields}, {k: str(getattr(s, k)) for k in SiteIn.model_fields}))  # fmt: skip
    db.commit()
    return site_row(s)


# ---------- milestones, tasks, notes, changes ----------


class MilestoneIn(BaseModel):
    kind: Literal[
        "material_ready", "delivery", "installation", "commissioning", "handover", "other"
    ] = "other"
    title: Name
    site_id: uuid.UUID | None = None
    due_date: date | None = None


class MilestonePatch(BaseModel):
    version: int
    title: Name | None = None
    due_date: date | None = None
    done_at: date | None = None
    done: bool | None = None


class TaskIn(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    site_id: uuid.UUID | None = None
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None


class TaskPatch(BaseModel):
    version: int
    status: Literal["open", "done"] | None = None
    assignee_id: uuid.UUID | None = None
    due_date: date | None = None


class NoteIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    site_id: uuid.UUID | None = None
    pending_action: bool = False


class ChangeIn(BaseModel):
    kind: Literal["scope", "quantity", "schedule"]
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=4000)
    ]


class DecideIn(BaseModel):
    version: int
    reason: Reason = ""


def _site_of(db: Session, pr: Project, site_id) -> None:
    if site_id and get_or_404(db, Site, site_id, "Site").project_id != pr.id:
        raise field_error("site_id", "That site belongs to another project")


@router.post("/projects/{project_id}/milestones", status_code=status.HTTP_201_CREATED)
def add_milestone(
    project_id: uuid.UUID, body: MilestoneIn, p: require("projects.create"), db: DB
) -> dict:
    pr = _project(db, project_id, lock=True)
    _site_of(db, pr, body.site_id)
    m = Milestone(project_id=pr.id, **body.model_dump())
    db.add(m)
    record(db, p.user, "milestone.create", "project", pr.id, f"{pr.number}: milestone {m.title}")
    db.commit()
    return {"id": m.id}


@router.patch("/milestones/{milestone_id}")
def update_milestone(
    milestone_id: uuid.UUID, body: MilestonePatch, p: require("projects.create"), db: DB
) -> dict:
    m = get_or_404(db, Milestone, milestone_id, "Milestone", lock=True)
    if m.version != body.version:
        raise stale()
    before = {"title": m.title, "due_date": str(m.due_date), "done_at": str(m.done_at)}
    if body.title:
        m.title = body.title
    if "due_date" in body.model_fields_set:
        m.due_date = body.due_date
    if body.done is not None:
        m.done_at = (body.done_at or date.today()) if body.done else None
    m.version += 1
    record(db, p.user, "milestone.update", "project", m.project_id, f"Milestone {m.title} changed",
           diff(before, {"title": m.title, "due_date": str(m.due_date), "done_at": str(m.done_at)}))  # fmt: skip
    db.commit()
    return {"id": m.id, "version": m.version}


@router.post("/projects/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
def add_task(project_id: uuid.UUID, body: TaskIn, p: require("projects.create"), db: DB) -> dict:
    pr = _project(db, project_id, lock=True)
    _site_of(db, pr, body.site_id)
    _active_user(db, body.assignee_id, "assignee_id")
    t = ProjectTask(project_id=pr.id, **body.model_dump())
    db.add(t)
    record(db, p.user, "task.create", "project", pr.id, f"{pr.number}: task {t.title}")
    db.commit()
    return {"id": t.id}


@router.patch("/tasks/{task_id}")
def update_task(task_id: uuid.UUID, body: TaskPatch, p: require("projects.create"), db: DB) -> dict:
    t = get_or_404(db, ProjectTask, task_id, "Task", lock=True)
    if t.version != body.version:
        raise stale()
    _active_user(db, body.assignee_id, "assignee_id")
    before = {"status": t.status, "assignee_id": str(t.assignee_id), "due_date": str(t.due_date)}
    for f in ("status", "assignee_id", "due_date"):
        if f in body.model_fields_set and (f != "status" or body.status is not None):
            setattr(t, f, getattr(body, f))
    t.version += 1
    record(db, p.user, "task.update", "project", t.project_id, f"Task {t.title}: {t.status}",
           diff(before, {"status": t.status, "assignee_id": str(t.assignee_id), "due_date": str(t.due_date)}))  # fmt: skip
    db.commit()
    return {"id": t.id, "version": t.version}


@router.post("/projects/{project_id}/notes", status_code=status.HTTP_201_CREATED)
def add_note(project_id: uuid.UUID, body: NoteIn, p: require("projects.create"), db: DB) -> dict:
    pr = get_or_404(db, Project, project_id, "Project")
    _site_of(db, pr, body.site_id)
    n = ProjectNote(project_id=pr.id, author_id=p.user.id, **body.model_dump())
    db.add(n)
    db.commit()
    return {"id": n.id}


@router.post("/projects/{project_id}/changes", status_code=status.HTTP_201_CREATED)
def ask_change(
    project_id: uuid.UUID, body: ChangeIn, p: require("projects.create"), db: DB
) -> dict:
    pr = _project(db, project_id, lock=True)
    c = ProjectChange(project_id=pr.id, requested_by=p.user.id, **body.model_dump())
    db.add(c)
    db.flush()
    record(db, p.user, "project_change.request", "project", pr.id, f"{pr.number}: {body.kind} change requested",
           reason=body.description[:500])  # fmt: skip
    db.commit()
    return {"id": c.id}


@router.post("/changes/{change_id}/{verdict}")
def decide_change(change_id: uuid.UUID, verdict: Literal["approve", "reject"], body: DecideIn,
                  p: require("projects.approve"), db: DB) -> dict:  # fmt: skip
    c = get_or_404(db, ProjectChange, change_id, "Change", lock=True)
    if c.version != body.version:
        raise stale()
    if c.status != "pending":
        raise conflict(f"Already {c.status}")
    if c.requested_by == p.user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Someone else must decide on a change you asked for"
        )
    if verdict == "reject" and not body.reason:
        raise field_error("reason", "Say why")
    c.status = "approved" if verdict == "approve" else "rejected"
    c.decided_by, c.decided_at, c.decision_reason, c.version = (
        p.user.id,
        utcnow(),
        body.reason,
        c.version + 1,
    )
    record(
        db,
        p.user,
        f"project_change.{c.status}",
        "project",
        c.project_id,
        f"{c.kind} change {c.status}",
        reason=body.reason,
    )
    db.commit()
    return {"id": c.id, "status": c.status}


# ---------- requirement sheets ----------


class SheetLineIn(BaseModel):
    item_id: uuid.UUID
    configuration: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""
    qty: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=3)] | None = (
        None  # empty: the rule's suggestion
    )
    rule_id: uuid.UUID | None = None
    inputs: Values = {}
    notes: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = ""


class SheetIn(BaseModel):
    site_id: uuid.UUID | None = None
    notes: Text = ""
    lines: Annotated[list[SheetLineIn], Field(min_length=1, max_length=200)]


class SheetPut(SheetIn):
    version: int


def sheet_row(db: Session, s: RequirementSheet) -> dict:
    lines = db.scalars(
        select(RequirementLine)
        .where(RequirementLine.sheet_id == s.id)
        .order_by(RequirementLine.sort)
    ).all()
    items = {
        i.id: i for i in db.scalars(select(Item).where(Item.id.in_([ln.item_id for ln in lines])))
    }
    names = _names(db, [s.prepared_by, s.decided_by])
    return {"id": s.id, "project_id": s.project_id, "site_id": s.site_id, "version_no": s.version_no, "status": s.status,
            "notes": s.notes, "prepared_by": names.get(s.prepared_by), "editor_ids": s.editors,
            "decided_by": names.get(s.decided_by), "decided_at": s.decided_at, "decision_reason": s.decision_reason,
            "created_at": s.created_at, "version": s.version,
            "lines": [{"id": ln.id, "item": item_ref(items[ln.item_id]), "configuration": ln.configuration, "qty": ln.qty,
                       "calculation": ln.calculation, "notes": ln.notes} for ln in lines]}  # fmt: skip


def _sheet_lines(db: Session, s: RequirementSheet, lines: list[SheetLineIn]) -> None:
    items = {
        i.id: i for i in db.scalars(select(Item).where(Item.id.in_([ln.item_id for ln in lines])))
    }
    if len(items) != len({ln.item_id for ln in lines}) or any(not i.active for i in items.values()):
        raise field_error("lines", "Unknown or inactive item")
    db.query(RequirementLine).filter(RequirementLine.sheet_id == s.id).delete()
    for n, ln in enumerate(lines):
        calc = None
        if ln.rule_id:
            rule = get_or_404(db, SizingRule, ln.rule_id, "Sizing rule")
            if not rule.active:
                raise field_error("lines", f"Sizing rule {rule.name} is switched off")
            calc = calculate(rule, ln.inputs)
        qty = ln.qty if ln.qty is not None else (Decimal(calc["suggested_qty"]) if calc else None)
        if not qty or qty <= 0:
            raise field_error(
                "lines", f"{items[ln.item_id].code}: enter a quantity, or a rule and its inputs"
            )
        if items[ln.item_id].tracking == "serial" and qty != qty.to_integral_value():
            raise field_error("lines", f"{items[ln.item_id].code} is counted in whole units")
        if calc and Decimal(calc["suggested_qty"]) != qty:
            calc["overridden"] = (
                True  # the engineer sees that the typed quantity differs from the rule
            )
        db.add(RequirementLine(sheet_id=s.id, item_id=ln.item_id, configuration=ln.configuration, qty=qty,
                               calculation=calc, notes=ln.notes, sort=n))  # fmt: skip


@router.post("/projects/{project_id}/sheets", status_code=status.HTTP_201_CREATED)
def new_sheet(project_id: uuid.UUID, body: SheetIn, p: require("projects.create"), db: DB) -> dict:
    pr = _project(db, project_id, lock=True)
    _site_of(db, pr, body.site_id)
    n = (
        db.scalar(
            select(func.max(RequirementSheet.version_no)).where(
                RequirementSheet.project_id == pr.id
            )
        )
        or 0
    ) + 1
    s = RequirementSheet(
        project_id=pr.id,
        site_id=body.site_id,
        version_no=n,
        notes=body.notes,
        prepared_by=p.user.id,
        editors=[p.user.id],
    )
    db.add(s)
    db.flush()
    _sheet_lines(db, s, body.lines)
    record(
        db, p.user, "sheet.create", "project", pr.id, f"{pr.number}: requirement sheet v{n} drafted"
    )
    db.commit()
    return sheet_row(db, s)


def _sheet(db: Session, sheet_id, version: int, *statuses: str) -> RequirementSheet:
    s = get_or_404(db, RequirementSheet, sheet_id, "Requirement sheet", lock=True)
    if s.version != version:
        raise stale()
    if s.status not in statuses:
        raise conflict(f"Version {s.version_no} is {s.status}: draft a new version to change it")
    return s


@router.put("/sheets/{sheet_id}")
def edit_sheet(sheet_id: uuid.UUID, body: SheetPut, p: require("projects.create"), db: DB) -> dict:
    s = _sheet(db, sheet_id, body.version, "draft", "rejected")
    _site_of(db, db.get(Project, s.project_id), body.site_id)
    s.site_id, s.notes, s.status = body.site_id, body.notes, "draft"
    _sheet_lines(db, s, body.lines)
    if p.user.id not in s.editors:
        s.editors = [*s.editors, p.user.id]
    s.version += 1
    record(
        db,
        p.user,
        "sheet.edit",
        "project",
        s.project_id,
        f"Requirement sheet v{s.version_no} changed",
    )
    db.commit()
    return sheet_row(db, s)


@router.post("/sheets/{sheet_id}/submit")
def submit_sheet(
    sheet_id: uuid.UUID, body: DecideIn, p: require("projects.create"), db: DB
) -> dict:
    s = _sheet(db, sheet_id, body.version, "draft")
    s.status, s.version = "submitted", s.version + 1
    record(
        db,
        p.user,
        "sheet.submit",
        "project",
        s.project_id,
        f"Requirement sheet v{s.version_no} submitted for engineering review",
    )
    db.commit()
    return sheet_row(db, s)


@router.post("/sheets/{sheet_id}/{verdict}")
def decide_sheet(sheet_id: uuid.UUID, verdict: Literal["approve", "reject"], body: DecideIn, p: require("projects.approve"),
                 db: DB) -> dict:  # fmt: skip
    """Engineering review. Approving supersedes the project's earlier approved sheet for the same site."""
    s = _sheet(db, sheet_id, body.version, "submitted")
    if p.user.id in {s.prepared_by, *s.editors}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Someone else must review a sheet you prepared or changed"
        )
    if verdict == "reject" and not body.reason:
        raise field_error("reason", "Say what to fix")
    newer = db.scalar(select(RequirementSheet.version_no).where(
        RequirementSheet.project_id == s.project_id, RequirementSheet.status == "approved",
        RequirementSheet.version_no > s.version_no,
        RequirementSheet.site_id.is_(None) if s.site_id is None else RequirementSheet.site_id == s.site_id))  # fmt: skip
    if verdict == "approve" and newer:
        raise conflict(f"Version {newer} is already approved: draft a new version instead")
    if verdict == "approve":
        for old in db.scalars(select(RequirementSheet).where(
                RequirementSheet.project_id == s.project_id, RequirementSheet.status == "approved",
                RequirementSheet.site_id.is_(None) if s.site_id is None else RequirementSheet.site_id == s.site_id).with_for_update()):  # fmt: skip
            old.status, old.version = "superseded", old.version + 1
    s.status = "approved" if verdict == "approve" else "rejected"
    s.decided_by, s.decided_at, s.decision_reason, s.version = (
        p.user.id,
        utcnow(),
        body.reason,
        s.version + 1,
    )
    record(
        db,
        p.user,
        f"sheet.{s.status}",
        "project",
        s.project_id,
        f"Requirement sheet v{s.version_no} {s.status}",
        reason=body.reason,
    )
    db.commit()
    return sheet_row(db, s)


# ---------- the material list: approved requirements against stock ----------


def materials(db: Session, pr: Project) -> list[dict]:
    """Per item: required by approved sheets, reserved for the project, free, still short."""
    need: dict = {}
    for ln in db.scalars(select(RequirementLine).join(RequirementSheet, RequirementSheet.id == RequirementLine.sheet_id)
                         .where(RequirementSheet.project_id == pr.id, RequirementSheet.status == "approved")):  # fmt: skip
        need[ln.item_id] = need.get(ln.item_id, ZERO) + ln.qty
    if not need:
        return []
    items = {i.id: i for i in db.scalars(select(Item).where(Item.id.in_(list(need))))}
    orders = [
        str(i) for i in db.scalars(select(SalesOrder.id).where(SalesOrder.project_id == pr.id))
    ]
    reserved = dict(db.execute(select(Reservation.item_id, func.sum(Reservation.qty)).where(  # the project's and its orders'
        or_(and_(Reservation.ref_type == "project", Reservation.ref_id == str(pr.id)),
            and_(Reservation.ref_type == "sales_order", Reservation.ref_id.in_(orders))),
        Reservation.status.in_(("active", "used"))).group_by(Reservation.item_id)).all())  # fmt: skip
    out = []
    for item_id, q in need.items():
        r, f = reserved.get(item_id, ZERO), free(db, item_id)
        out.append({"item": item_ref(items[item_id]), "assembled": items[item_id].assembled, "required": q,
                    "reserved": r, "free": f, "short": max(q - r - max(f, ZERO), ZERO)})  # fmt: skip
    return sorted(out, key=lambda x: x["item"]["code"])


class KeyIn(BaseModel):
    key: Key


@router.post("/projects/{project_id}/reserve")
def reserve_for_project(
    project_id: uuid.UUID, body: KeyIn, p: require("projects.create"), db: DB
) -> dict:
    """Reserve free stock for approved requirements not yet reserved."""
    if (prev := claim_key(db, body.key, "project_reserve")) is not None:
        return prev
    pr = _project(db, project_id, lock=True)
    rows = materials(db, pr)
    if not rows:
        raise conflict("No approved requirement sheet yet")
    items = lock_items(db, [x["item"]["id"] for x in rows])
    done = []
    for x in rows:
        item = items[x["item"]["id"]]
        take = min(x["required"] - x["reserved"], max(free(db, item.id), ZERO))
        if item.tracking == "serial":
            take = take.to_integral_value(rounding="ROUND_FLOOR")
        if take > 0:
            reserve(db, p.user, item, take, "project", str(pr.id), f"{pr.number} {pr.name}"[:120])
            done.append(f"{item.code} {take.normalize():f}")
    record(
        db,
        p.user,
        "project.reserve",
        "project",
        pr.id,
        f"{pr.number}: reserved {', '.join(done) or 'nothing (no free stock)'}",
    )
    result = remember(db, body.key, {"id": pr.id, "reserved": done})
    db.commit()
    return result


@router.post("/projects/{project_id}/purchase-request", status_code=status.HTTP_201_CREATED)
def project_shortage(project_id: uuid.UUID, p: require("projects.create"), db: DB) -> dict:
    pr = _project(db, project_id, lock=True)
    short = [x for x in materials(db, pr) if x["short"] > 0 and not x["assembled"]]
    if not short:
        raise conflict("Nothing bought is short for this project")
    r = create_pr(db, p.user, PRIn(source="project", ref=pr.number,
                                   lines=[PRLineIn(item_id=x["item"]["id"], qty=x["short"], notes=pr.name[:300]) for x in short]))  # fmt: skip
    db.commit()
    return pr_row(db, r, full=True)


class ProjectAssembly(BaseModel):
    item_id: uuid.UUID
    warehouse_id: uuid.UUID
    qty: int = Field(ge=1, le=10_000)


@router.post("/projects/{project_id}/assembly-order", status_code=status.HTTP_201_CREATED)
def project_assembly(
    project_id: uuid.UUID,
    body: ProjectAssembly,
    p: require("assembly.create", "projects.view"),
    db: DB,
) -> dict:
    pr = _project(db, project_id, lock=True)
    if not any(x["item"]["id"] == body.item_id and x["assembled"] for x in materials(db, pr)):
        raise field_error("item_id", "Choose an assembled battery on the approved requirements")
    return plan_order(OrderIn(item_id=body.item_id, qty_planned=body.qty, warehouse_id=body.warehouse_id,
                              source="project", source_ref=pr.number), p, db)  # fmt: skip


@router.post("/projects/{project_id}/quotation", status_code=status.HTTP_201_CREATED)
def project_quotation(
    project_id: uuid.UUID, p: require("projects.view", "sales.create"), db: DB
) -> dict:
    """A draft quotation with the approved requirements as lines; prices are filled in on the draft."""
    pr = _project(db, project_id, lock=True)
    rows = materials(db, pr)
    if not rows:
        raise conflict("No approved requirement sheet yet")
    items = {
        i.id: i
        for i in db.scalars(select(Item).where(Item.id.in_([x["item"]["id"] for x in rows])))
    }
    lines = [QuoteLineIn(line_type="battery" if items[x["item"]["id"]].kind in ("battery_model", "battery_unit") else "accessory",
                         item_id=x["item"]["id"], qty=x["required"], unit_price=ZERO) for x in rows]  # fmt: skip
    qt = create_quotation(db, p.user, QuotationIn(customer_id=pr.customer_id, project_id=pr.id,
                                                  currency=db.get(Party, pr.customer_id).currency, lines=lines))  # fmt: skip
    db.commit()
    return quotation_row(db, qt, full=True)
