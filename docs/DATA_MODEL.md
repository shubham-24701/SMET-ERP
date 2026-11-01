# Data model

Conventions: UUID primary keys; `timestamptz` in UTC; `version` on editable records (optimistic
locking); history is never rewritten: corrections are new rows (stock movements, audit events).
Migrations in `backend/migrations/versions/` are the authoritative schema (0001 foundation; 0002
hardens the audit trigger and protects `alembic_version`; 0003 Phases 2–3; 0004 review fixes:
longer movement document numbers, order editors, count counters, returns not replaced; 0005 Phase 4; 0006 dispatch lines remember the reservations they used).

## Phase 1 tables

| Table | Holds | Key rules |
| --- | --- | --- |
| `company` | The one company: name, time zone, currency | Single row, `id = 1` (setup collides on it, so it runs once) |
| `users` | Staff accounts (portal accounts from Phase 6) | `email` unique, stored lower-case; `active`; `must_change_password`; argon2 hash |
| `roles` | Role templates and custom roles | `name` unique; `template_key` unique for shipped roles; `audience` internal / distributor / customer |
| `role_permissions` | Permissions of each role | PK (role, permission); permission must exist in the code catalogue |
| `user_roles` | Roles held by each user | PK (user, role); a role in use can't be deleted (`RESTRICT`) |
| `template_grants` | Template permissions already given once | Stops upgrades re-adding a permission an admin removed |
| `auth_sessions` | Sign-in sessions | Only the token's SHA-256; `expires_at` (10 h), `last_seen_at` (idle 60 min) |
| `audit_events` | Who did what, when, what changed, why | Append-only: no UPDATE/DELETE/TRUNCATE grant for the app; trigger refuses them for everyone else; `actor_id` has no foreign key so entries outlive users |
| `jobs` | Background work | `key` unique (idempotency); status pending / running / done / dead; lease for crashed workers |

## Phase 2 tables

| Table | Holds | Key rules |
| --- | --- | --- |
| `units` | Units of measure (PCS, M, KG…) | Seeded at setup; configurable |
| `items` | Battery models, battery units, components, accessories, materials | `code` unique; `tracking` none / lot / serial; `assembled` only for battery models and needs serial tracking; kind, unit and tracking lock once used |
| `parties` | Suppliers, customers, distributors, dealers | `code` unique; `kinds` array (one company can be several); contacts as a list |
| `supplier_items` | What a supplier supplies, their code, last price, lead time | PK (party, item) |
| `warehouses`, `locations` | Storage, and areas by `purpose`: available, inspection, quarantine, rejected, assembly, repair, returns | (warehouse, code) unique; purpose can't change while stock is there |
| `number_sequences` | Prefix, digits, yearly reset, next value per document type | Row locked while a number is taken |
| `operation_keys` | Keys of posted operations and their result | A repeated submit returns the first result; nothing is posted twice |
| `company.rules` | Business rules (auto-approval limit, self-approval switches) | Defaults are the cautious choice |
| `purchase_requests` (+ `_lines`) | Needs, why, for what, by when | open / ordered / cancelled |
| `supplier_quotes` (+ `_lines`) | Offers per request: price, quantity, delivery, currency | open / selected / declined |
| `purchase_orders` (+ `_lines`) | Orders, approval trail (`editors`: everyone who drafted or changed it), expected dates; per line `qty_not_replaced` for returns the supplier won't replace | Line CHECK: received = awaiting + accepted + rejected + held; returned ≤ rejected |
| `goods_receipts` (+ `_lines`) | Deliveries against an order | Never more than outstanding |
| `supplier_returns` (+ `_lines`) | Rejected goods sent back | From the rejected area only |
| `serials` | One row per tracked unit: serial, supplier serial, lot, status, location, receipt or assembly order | `serial` unique; (item, supplier serial) unique |
| `stock_moves` | The ledger: item, quantity, from, to, lot, serial, kind, document, user, reason | Append-only (grants + trigger); quantity > 0 |
| `stock_balances` | Quantity per (item, location, lot) | Kept in the same transaction as the move; CHECK qty ≥ 0 |
| `reservations` | Stock promised to an assembly order, sales order or project | qty_used ≤ qty; active / used / released |
| `stock_adjustments` (+ `_lines`) | Corrections: signed change, or a count's counted figure, or serials in / out / found | pending → approved / rejected by someone else |
| `stock_counts` (+ `_lines`) | A location's books when the count started, what was found, and who counted (`counters`, who can't approve the result) | open → submitted / cancelled; never on the assembly floor |

## Phase 3 tables

| Table | Holds | Key rules |
| --- | --- | --- |
| `boms`, `bom_lines` | Bill of materials versions per assembled model; components and quantity per battery | (item, version) unique; one `active` per item (partial unique index); an active version never changes |
| `assembly_orders` | Model, BOM version, quantity, warehouse, why, responsible person, target date | draft → released → in progress → completed / cancelled |
| `serial_components` | What went into each finished battery: component serial, or lot and quantity | A component serial is used once (unique) |
| `test_parameters` | Readings per model, unit, lowest / highest, required, `confirmed` | Limits apply only when confirmed; rows are switched off, never deleted |
| `checklist_items` | Inspection checks per model | Same |
| `inspections` | Result (passed / on hold / rework), readings and checklist as recorded, with the limits that applied | Copies limits, so later changes never rewrite history |

## Phase 4 tables

| Table | Holds | Key rules |
| --- | --- | --- |
| `enquiries` | Customer, products or project, requested lines (item or text), needed-by | open / quoted / lost / closed |
| `quotations` | Number, customer, enquiry, project | Content lives in revisions |
| `quotation_revisions` (+ `quotation_lines`) | Revision number, currency, validity, terms, scope, dates, editors, approval, customer answer; lines by type (battery, accessory, installation, commissioning, AMC, other) with price and discount | (quotation, rev) unique; editable only as a draft; "expired" is computed from validity |
| `sales_orders` (+ `_lines`) | Customer, the accepted revision, project, committed date, address; per line ordered, dispatched, delivered | `quotation_revision_id` unique: one order per accepted revision; dispatched ≤ ordered (CHECK) |
| `projects` | Customer, scope, contacts, manager, dates, status | planning / active / on hold / completed / cancelled |
| `sites` | Address, contact, survey (date, by whom, findings, requirements) | Per project |
| `project_milestones`, `project_tasks`, `project_notes`, `project_changes` | Milestones with due and done dates; tasks with assignee; notes and pending actions; changes with decision | A change is decided by someone else |
| `sizing_rules` | Named inputs, arithmetic expression, result unit, confirmed | Expression checked against a whitelist before it is stored or run |
| `requirement_sheets` (+ `requirement_lines`) | Version, site, status, preparer and editors, decision; lines with item, configuration, quantity and the calculation shown | (project, version) unique; approved sheets never change; a new approval supersedes the old |
| `dispatches` (+ `dispatch_lines`) | Order, site, warehouse, dates, destination, transport, acknowledgement, cancellation; lines with quantity, lot, serials, picking location, reserved quantity used, refused and damaged | planned → dispatched → delivered, or cancelled; refused + damaged ≤ sent (CHECK) |
| `serials.party_id`, `serials.site_id` | Who holds a dispatched unit, and where | Cleared when it comes back |

## Phase 5 tables

| Table | Holds | Key rules |
| --- | --- | --- |
| `battery_banks` | Number, site, name, configuration, stage, installation / commissioning / handover dates | planned → installed → commissioned → handed over, one step per completed visit; (site, name) unique |
| `bank_positions` | Which battery was in which position of which bank, from when to when, why it started and ended, who, the battery it replaced, the visit | Closed, never rewritten; at most one open row per battery and per bank position (partial unique indexes); the app login cannot delete |
| `stage_checks` | SMET's checklist lines and readings per stage, with unit and limits | Limits judged only once `confirmed` |
| `site_visits` | Number, site, stage, banks, date and time, technicians, readiness, access, contacts; the record: serials received, checklist and readings (copied with the text and limits that applied), observations, issues, customer acknowledgement; completion | scheduled → started → completed, or cancelled; the record is frozen once completed; the app login cannot delete |
| `files` | Owner (a visit), kind, original name, type read from the content, size, SHA-256, caption, uploader | Bytes on disk under a random name in `FILES_DIR`; the app login can't change or delete a row |
| `serials.qr_token` | Random 32-character token printed in the QR label | Unique; not derived from any id |
| `dispatches.delivered_at` | When the delivery was recorded | Orders the lifecycle timeline |

A battery's status follows its bank: delivered (planned bank), installed, commissioned, in service
(handed over), removed. The lifecycle timeline is not a table: it is read from stock movements,
inspections, dispatches, bank positions and completed visits, so it can't disagree with them.

## Planned entities (from the proposal, by phase)

Phases 2 to 5 are built (above).

- **4** enquiries, quotations and immutable revisions, sales orders; projects, sites, surveys,
  requirement sheets, milestones, tasks, scope changes; dispatches, lines with exact serials,
  acknowledgements, discrepancies.
- **5** installation visits, commissioning and handover records; battery banks and positions with
  assignment history; lifecycle events.
- **6** warranty policies (by model, effective-dated), registrations, activations, corrections; AMC
  contracts, covered batteries, schedules, visits; service requests, diagnoses, work logs, parts,
  replacements; maintenance alerts and reset-code records (encrypted).
- **7** notification templates, events, attempts; file attachments (metadata in the database, files
  outside the web root).
