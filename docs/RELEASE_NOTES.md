# Release notes

## 0.4.0 · Phase 5: installation and traceability · 2026-10-30

New
- Battery banks at a site: place delivered batteries in positions; remove, replace and move them with
  a reason; every placement is kept.
- Site visits for installation, commissioning and handover: date, technicians, site readiness,
  access and contacts. The technicians on a visit confirm the serials received, tick SMET's
  checklist, take readings, note issues and corrective actions, add photographs and reports, and
  record the customer's acceptance.
- A visit completes only when SMET's checks, readings within confirmed limits and the evidence
  policy are met. Each completion dates the bank's stage; the project's milestones follow.
- Site checks: SMET's checklists and readings per stage.
- QR labels printed from the browser; scanning one opens a public check that shows the serial,
  product and whether it is in service, and nothing about the customer.
- Find a battery by scanning its label or typing a serial; each battery's whole life on one timeline.

Upgrade
- `uv sync` (adds python-multipart and segno), then `uv run alembic upgrade head` (migration 0007).
- Set `FILES_DIR` (e.g. `D:/smet-files`) and, before printing real labels, `PUBLIC_BASE_URL`.

## 0.3.0 · Phase 4: sales, projects and dispatch · 2026-10-30

New
- Enquiries, and quotations prepared from them: battery, accessory, installation, commissioning and
  AMC lines with customer prices and discounts; revisions that never change once submitted; approval
  by someone else; the customer's answer; one sales order per accepted revision.
- Sales orders: what is still to send, reserved, free and short; reserve stock; request purchases or
  plan assembly for what is short.
- Dispatch: plan, send exact serial numbers from available stock only, print the delivery note, record
  delivery with refused and damaged goods, or cancel with stock and reservations restored.
- Projects: sites and surveys, milestones, tasks, notes, changes approved by someone else; battery
  requirement sheets approved by an engineer; sizing rules SMET can enter (none is built in); the
  material list with reservations, purchase requests, assembly and the project quotation.

Upgrade
- `uv run alembic upgrade head` (migration 0005; existing warehouses get a customer-returns area).

## 0.2.0 · Phases 2–3: purchasing, stock, assembly and quality · 2026-10-30

New
- Master data: items (battery models with capacity, voltage, chemistry; battery units; components;
  accessories; materials), units, suppliers and customers with contacts, terms and supplied items,
  warehouses with standard areas and your own racks, rules and document numbering.
- Purchasing: requests, supplier quotations compared side by side, purchase orders with approval by
  someone else, deliveries, inspection decisions (accept, reject, hold, release), returns to supplier.
- Stock: on hand by status, free after reservations, below-minimum list with one-click purchase
  request, movements with CSV, serial numbers with full history, reservations, counts, corrections
  approved by a second person, reconciliation.
- Assembly: bills of materials with versions, assembly orders that reserve, issue and return
  materials, each battery serialised with links to its components.
- Final inspection: checklists and readings per model; limits apply once SMET confirms them; only a
  pass puts a battery into available stock; hold and rework.
- Overview: "Waiting for you" lists approvals, deliveries, corrections, low stock and inspections.

Upgrade
- `uv run alembic upgrade head` (migration 0003). New permissions reach the role templates at start-up.

Known limitations
- No file attachments yet (Phase 7); no import of existing data yet (waiting for SMET's files).
- Purchase prices are reference only; no stock valuation.

## 0.1.0 · Phase 1 foundation · 2026-10-28

New
- First-run setup: company profile and the first System Administrator.
- Sign-in with temporary passwords that must be replaced; change password; sign out everywhere on change.
- Users: add, edit, give roles, deactivate and reactivate, reset password; filter by status.
- Roles & permissions: 17 role templates from the proposal, custom roles, permission editor with the
  effect of each change; guards against changing your own access.
- Audit log: every sign-in and change with who, when, what changed and why; filters; CSV export.
  Entries can't be edited or deleted, by anyone.
- Company settings (name, time zone, currency) with live previews; System health.
- Light, dark and system themes; works at phone width.

Setup
- Needs PostgreSQL. Run `uv run python -m app.setup_db` once, then `uv run alembic upgrade head`
  (README, "Run it on Windows 11").

Known limitations
- No business modules yet (procurement and inventory come in 0.2.0).
- No email: password resets are handed over by an administrator.
- No multi-factor sign-in yet.
