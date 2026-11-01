# Architecture decisions

Short records: the decision, why, what was rejected, and when to revisit.

## ADR-001 Modular monolith on one PostgreSQL database (2026-10-28)
**Decision.** One FastAPI service and one PostgreSQL database for every module.
**Why.** SMET's modules share the same records (a battery moves through all of them); one database
gives single transactions, foreign keys and simple backups. Expected load (tens of staff) fits one
small server many times over.
**Rejected.** Microservices, message brokers, Kubernetes: cost and operational load without a need.
**Revisit** if independent teams deploy separately or one module's load dominates.

## ADR-002 PostgreSQL everywhere, including development (2026-10-28)
**Decision.** No SQLite fallback. Windows development uses the free PostgreSQL installer.
**Why.** Row locking, `SKIP LOCKED`, `timestamptz`, JSONB and triggers behave the same in tests,
development and production; stock integrity depends on them.

## ADR-003 Two database logins (2026-10-28)
**Decision.** `smet_owner` owns the schema and runs migrations; `smet_app` (the app) has row access only,
and no UPDATE/DELETE/TRUNCATE on `audit_events`.
**Why.** Least privilege: a bug or an injection through the app can't change the schema or rewrite
history. `app.setup_db` makes this a one-command setup.

## ADR-004 Opaque server sessions, not JWT (2026-10-28)
**Decision.** Random session tokens in an HttpOnly, SameSite=Lax cookie; only their SHA-256 is stored;
CSRF double-submit on writes.
**Why.** Instant revocation (deactivation, password change), nothing readable by page scripts, no key
rotation to operate. Checkpoint 70 (JWT Rotation) is assessed as not applicable for this reason.
**Revisit** if a native app or third-party API clients need tokens.

## ADR-005 Configurable permissions with shipped role templates (2026-10-28)
**Decision.** A code-defined permission catalogue; roles are data (17 templates + custom); a person may
hold several roles. Template grants are recorded so upgrades never re-add a removed permission.
**Why.** The proposal asks for module- and action-level access configurable by SMET without code changes.

## ADR-006 Database-backed job queue (2026-10-28)
**Decision.** Jobs in a PostgreSQL table, claimed with `FOR UPDATE SKIP LOCKED`; worker thread in the API.
**Why.** Reliable retries and dead letters with no extra service to run or pay for.
**Revisit** at thousands of jobs per minute (not expected).

## ADR-007 No server-side cache (2026-10-28)
**Decision.** No Redis or in-process cache; API responses are `Cache-Control: no-store`.
**Why.** Stock availability and authorization must come from the database; at this scale queries are
fast enough. Static assets are content-hashed and cacheable.

## ADR-008 Single company per installation (2026-10-28)
**Decision.** No tenant column; distributors and customers are parties with scoped portal access.
**Why.** The proposal describes one business; tenancy would add a filter to every query and a class of bugs.

## ADR-009 Interface: Carbon geometry with the "Achromatic ledger" app style (2026-10-28)
**Decision.** IBM Plex Sans and Carbon's grid (awesome-design-md: IBM), with the taste skill's
app-screen style: no colour in the chrome, near-black primary action, one data hue (#1d5fbf).
**Why.** Dense operational screens read best with colour reserved for data and status. Details in
[../DESIGN.md](../DESIGN.md).
