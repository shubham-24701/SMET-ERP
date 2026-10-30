# Data model

Conventions: UUID primary keys; `timestamptz` in UTC; `version` on editable records (optimistic
locking); history is never rewritten: corrections are new rows (stock movements, audit events).
Migrations in `backend/migrations/versions/` are the authoritative schema (0001 foundation; 0002
hardens the audit trigger and protects `alembic_version`; 0003 Phases 2–3; 0004 review fixes:
longer movement document numbers, order editors, count counters, returns not replaced).

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

## Planned entities (from the proposal, by phase)

Phases 2 and 3 are built (above).

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
