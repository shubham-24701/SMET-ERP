# Data model

Conventions: UUID primary keys; `timestamptz` in UTC; `version` on editable records (optimistic
locking); history is never rewritten: corrections are new rows (stock movements, audit events).
Migrations in `backend/migrations/versions/` are the authoritative schema (0001 foundation; 0002
hardens the audit trigger and protects `alembic_version`).

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

## Planned entities (from the proposal, by phase)

- **2** parties (suppliers, distributors, dealers, customers) and contacts; items (battery models,
  units, components, accessories) with specifications; units of measure; warehouses and locations;
  document numbering; purchase requests, supplier quotations, purchase orders and lines, approvals,
  goods receipts and inspections, supplier returns; stock movement ledger (source of truth), stock
  balances (derived, reconcilable), reservations, counts, adjustments.
- **3** BOM versions and lines; assembly orders, issues, consumption, returns; finished batteries
  (serial records) with component links; checklists and test records; quality decisions.
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
