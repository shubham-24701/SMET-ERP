"""Permissions, the shipped role templates, and the check every protected route uses.

A permission is "<module>.<action>". Actions follow the proposal's vocabulary: view, create, edit,
delete, submit, approve, reject, export, configure, sensitive. Each phase adds the permissions of
the modules it builds; roles are configurable from this catalogue only.

The System Administrator configures the system but holds no business permissions, so it cannot
approve purchases, adjust stock or change warranties (docs/ROLE_PERMISSION_MATRIX.md).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role, RolePermission, TemplateGrant

# code -> (module label, what it allows). Order = the order the role editor shows them.
PERMISSIONS: dict[str, tuple[str, str]] = {
    "users.view": ("Users", "See users and the roles they hold"),
    "users.create": ("Users", "Add users"),
    "users.edit": ("Users", "Change a user's roles, reset passwords, deactivate and reactivate"),
    "roles.view": ("Roles & permissions", "See roles and what each allows"),
    "roles.configure": ("Roles & permissions", "Create, change and delete roles"),
    "audit.view": ("Audit log", "Read the audit log"),
    "audit.export": ("Audit log", "Download the audit log as CSV"),
    "settings.view": ("Company settings", "See company settings"),
    "settings.configure": ("Company settings", "Change company name, time zone and currency"),
    "system.view": ("System health", "See system health and background jobs"),
    # Phase 2
    "items.view": ("Items", "See items, battery models, specifications and units"),
    "items.edit": ("Items", "Add and change items and their specifications"),
    "items.configure": ("Items", "Manage units of measure"),
    "parties.view": ("Suppliers & customers", "See suppliers, customers, distributors and dealers"),
    "parties.edit": ("Suppliers & customers", "Add and change them, their contacts, terms and supplied items"),
    "warehouses.view": ("Warehouses", "See warehouses and storage locations"),
    "warehouses.configure": ("Warehouses", "Add and change warehouses and storage locations"),
    "purchasing.view": ("Purchasing", "See purchase requests, supplier quotations, purchase orders, receipts and returns"),
    "purchasing.create": ("Purchasing", "Raise purchase requests, record quotations, draft and submit purchase orders, return goods to suppliers"),
    "purchasing.approve": ("Purchasing", "Approve or reject submitted purchase orders"),
    "receipts.create": ("Goods receipt", "Record deliveries against purchase orders"),
    "stock.view": ("Stock", "See stock, movements, reservations, counts and serial numbers"),
    "stock.create": ("Stock", "Move stock between locations, reserve and release stock, run stock counts"),
    "stock.export": ("Stock", "Download stock and movement reports as CSV"),
    "adjustments.create": ("Stock corrections", "Request stock corrections"),
    "adjustments.approve": ("Stock corrections", "Approve or reject stock corrections, which changes stock"),
    # Phase 3
    "bom.view": ("Bills of materials", "See bills of materials"),
    "bom.configure": ("Bills of materials", "Create bill of materials versions and make them active"),
    "assembly.view": ("Assembly", "See assembly orders and work in progress"),
    "assembly.create": ("Assembly", "Plan, release, complete and cancel assembly orders"),
    "assembly.edit": ("Assembly", "Issue and return materials, build batteries and record what went into them"),
    "quality.create": ("Quality", "Decide on received goods and inspect finished batteries (pass, hold, rework)"),
    "quality.configure": ("Quality", "Set test parameters, limits and inspection checklists"),
}  # fmt: skip

CONFIGURE_PERMS = [p for p in PERMISSIONS if p.endswith(".configure")]
ADMIN_PERMS = [
    p for p in PERMISSIONS if p.split(".")[0] in ("users", "roles", "audit", "settings", "system")
]
# The administrator also holds every .configure permission: it can then grant them (the escalation
# guard) and set up master data, but still approves, moves or inspects nothing.
ADMIN_PERMS = list(
    dict.fromkeys(ADMIN_PERMS + CONFIGURE_PERMS + ["items.view", "warehouses.view", "bom.view"])
)
VIEW_ALL = [p for p in PERMISSIONS if p.endswith(".view")]


def is_guarded(perm: str) -> bool:
    """Permissions that give power over other people or the system's configuration. Nobody can
    grant one they don't hold, nor change someone who holds one they lack."""
    module, _, action = perm.partition(".")
    return module in ("users", "roles") or action in ("configure", "sensitive")


OVERSIGHT = VIEW_ALL + ["stock.export"]

# key -> (name, audience, description, permissions). Business permissions arrive with each phase.
TEMPLATES: dict[str, tuple[str, str, str, list[str]]] = {
    "system_admin": ("System Administrator", "internal",
        "Users, roles, settings and technical configuration. No business approvals.", ADMIN_PERMS),
    "operations_manager": ("Management / Operations Manager", "internal",
        "Dashboards and visibility across all modules, plus specifically granted approvals.",
        OVERSIGHT + ["adjustments.approve", "assembly.create", "bom.configure"]),
    "procurement_officer": ("Procurement Officer", "internal",
        "Suppliers, purchase requests, supplier quotations, purchase orders and receipt tracking.",
        ["items.view", "items.edit", "parties.view", "parties.edit", "purchasing.view",
         "purchasing.create", "stock.view", "warehouses.view"]),
    "procurement_approver": ("Procurement Approver", "internal",
        "Approves or rejects purchase orders, with reasons.",
        ["items.view", "parties.view", "purchasing.view", "purchasing.approve", "stock.view"]),
    "warehouse_officer": ("Warehouse / Inventory Officer", "internal",
        "Receipts, transfers, reservations, stock counts and permitted adjustments.",
        ["items.view", "parties.view", "warehouses.view", "purchasing.view", "receipts.create",
         "stock.view", "stock.create", "stock.export", "adjustments.create", "assembly.view",
         "assembly.edit"]),
    "assembly_technician": ("Assembly Technician", "internal",
        "Assigned assembly orders, material consumption and quality checklists.",
        ["items.view", "warehouses.view", "bom.view", "assembly.view", "assembly.edit", "stock.view"]),
    "quality_inspector": ("Quality Inspector", "internal",
        "Inspection records, pass / hold / rework decisions and approved test parameters.",
        ["items.view", "warehouses.view", "purchasing.view", "bom.view", "assembly.view",
         "stock.view", "quality.create", "quality.configure"]),
    "sales_officer": ("Sales Officer", "internal",
        "Enquiries, quotations, customers and sales orders.", []),
    "sales_approver": ("Sales Approver", "internal",
        "Commercial approvals, discounts and quotation approval.", []),
    "project_manager": ("Project Manager", "internal",
        "Projects, site requirements, milestones, documents and installation coordination.", []),
    "installation_technician": ("Installation Technician", "internal",
        "Assigned visits, installation records, commissioning checklists and handover evidence.", []),
    "warranty_admin": ("Warranty Administrator", "internal",
        "Registration review, warranty activation and permitted corrections.", []),
    "service_coordinator": ("Service Coordinator", "internal",
        "Service intake, coverage checks, assignments and scheduling.", []),
    "service_technician": ("Service Technician", "internal",
        "Assigned service jobs, diagnostics, work performed and parts used.", []),
    "code_authorizer": ("Maintenance Code Authorizer", "internal",
        "Explicitly authorised maintenance reset-code workflows.", []),
    "distributor_user": ("Distributor User", "distributor",
        "Portal: only this distributor's records and registration workflows.", []),
    "customer_user": ("Customer User", "customer",
        "Portal: only this customer's batteries, warranties, service requests and documents.", []),
}  # fmt: skip


def sync_templates(db: Session) -> None:
    """Create missing template roles and give each template permission once (see TemplateGrant).
    Runs at startup and at first-run setup; safe to run any number of times."""
    roles = {
        r.template_key: r for r in db.scalars(select(Role).where(Role.template_key.is_not(None)))
    }
    granted = set(db.execute(select(TemplateGrant.template_key, TemplateGrant.permission)).all())
    for key, (name, audience, description, perms) in TEMPLATES.items():
        role = roles.get(key)
        if role is None:
            # A custom role may already use the template's name: keep it, mark ours as shipped.
            taken = db.scalar(select(Role.id).where(Role.name == name))
            role = Role(name=f"{name} (template)" if taken else name, audience=audience,
                        description=description, template_key=key)  # fmt: skip
            db.add(role)
            db.flush()
        have = set(
            db.scalars(select(RolePermission.permission).where(RolePermission.role_id == role.id))
        )
        for perm in perms:
            if (key, perm) in granted:
                continue
            if perm not in have:
                db.add(RolePermission(role_id=role.id, permission=perm))
            db.add(TemplateGrant(template_key=key, permission=perm))
    db.flush()
