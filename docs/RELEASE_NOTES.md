# Release notes

## 0.8.0 · Phase 8: security, integration and operational hardening · 2026-11-01

New
- Backups: `python -m app.backup create | verify | restore` (database and files, with checksums);
  a monthly restore drill and incident steps in `docs/operations/RUNBOOK.md`.
- Uploads can pass a virus scanner (MALWARE_SCAN_COMMAND, e.g. ClamAV); a refused or failed scan
  stores nothing. Each file records whether it was scanned.
- Files can live in an S3-compatible bucket (FILES_BACKEND=s3) instead of this computer's disk.
- Behind a reverse proxy, the real client address is used for limits and the audit log
  (TRUSTED_PROXIES), and only from the proxies listed.
- Every date (deliveries, warranties, quotations, reminders, reports) is SMET's date in its time
  zone, whatever the server's clock is set to.
- Retention: read notifications are deleted and old message texts cleared after the days set in
  Rules & numbering; business records and files stay.
- System health lists file storage, the virus scanner, each message channel, the supplier API,
  backups and the server clock.
- Performance and concurrency check (`tests/load_check.py`) with a few years' synthetic volume.

Upgrade
- `uv run alembic upgrade head` (migration 0011). Schedule the nightly backup (runbook).

## 0.7.0 · Customization · 2026-11-01

New
- A permission for each report (Roles & permissions → Reports). Roles that could see a report's
  records were given it once; from now on roles decide.
- Administration → Fields: add your own fields (short or long text, number, date, choice, yes/no;
  required or not) to items, suppliers and customers, purchase and assembly orders, quotations,
  sales orders, projects and sites, dispatches, site visits, battery banks, warranty registrations,
  maintenance contracts and service requests. They show under "More details" on the record and
  as extra columns in its reports and downloads. Switching one off keeps its values.
- Built-in optional fields can be renamed, hidden or made required. Required is checked by the server
  on every route; a hidden field keeps its value when a form is saved.
- Rules & numbering → Workflows: purchase order and quotation approval on or off; extra roles told
  about each kind of notification; service-request statuses and the moves between them.

Upgrade
- `uv run alembic upgrade head` (migration 0010).

## 0.6.0 · Phase 7: notifications and reporting · 2026-11-01

New
- Notifications in the app: approval requests and decisions, late supplier deliveries and assembly
  orders, items below minimum, maintenance visits coming up or overdue, contracts ending, service
  request status, warranty decisions and missing registration information. Each goes to the people
  whose role or assignment covers it, never the person who did it. A bell with the unread count;
  each person chooses which kinds they hear about and whether to get them by email too.
- Reminders repeat while something stays late; escalation to a chosen role after a number of days
  (Rules & numbering).
- Messages (Administration): email, SMS and WhatsApp channels, each off, mock, sandbox or production;
  credentials stored encrypted with a connection test; the wording of every message, checked and
  previewed; a log of sent messages with masked recipients and "send again"; opt-outs. Nothing
  leaves the server until the server setting allows it.
- Customers can ask for updates on the public forms; staff record the same consent.
- Reports: dashboards by area (procurement, inventory, assembly, sales, installation, warranty,
  maintenance and service, traceability) and an exceptions view; every figure opens the rows it
  counts. 18 reports with filters by date, product, serial, customer, distributor, supplier,
  warehouse, project and status; CSV and Excel downloads with their own permission, audited.
- Search across batteries, customers and suppliers, projects and documents, limited to what each
  person may see.

Upgrade
- `uv sync` (adds openpyxl), `python -m app.setup_db` once (writes `SECRETS_KEY`; back it up with
  `.env`), then `uv run alembic upgrade head` (migration 0009). Leave `EXTERNAL_SENDS` unset (off)
  until a provider is approved.

## 0.5.0 · Phase 6: warranty and after-sales · 2026-10-30

New
- Warranty policies per battery model, entered by SMET; registrations opened at handover, by staff for
  distributor and direct sales, or on the public form; checked against the policy, approved
  automatically only where the policy allows, otherwise by someone else; corrections with a reason.
- Public page: check a battery by its QR label or serial number, see its warranty state, register a
  warranty or ask for service. Nothing about the customer is shown.
- Maintenance contracts (AMC) for a customer, site, banks or chosen batteries, with planned visits,
  reminders for upcoming and overdue visits and contracts ending, and renewal.
- Service requests: status rules with a kept history, service visits, workshop receipt and return,
  parts used, replacement batteries, closure with the customer's acknowledgement. Warranty and AMC
  cover are shown separately.
- Maintenance alerts: verification, then the supplier's reset code typed in by an authorised person,
  stored encrypted, shown again only with a reason, erased after the retention period.
- A battery's lifecycle now includes warranty, service and alert events.

Fixed
- Re-running `setup_db` no longer gives the app back the right to delete Phase 5 history.
- List tables kept their columns on desktop (a layout rule turned some into grids).

Upgrade
- `uv sync` (adds cryptography), `python -m app.setup_db` once (writes `CODES_KEY`; back it up with
  `.env`), then `uv run alembic upgrade head` (migration 0008; every warehouse gets a service workshop).

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
