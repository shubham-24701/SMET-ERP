# Implementation status

**Updated:** 2026-10-28 · **Version:** 0.1.0 · **Current phase:** 1 (foundation) built and verified
locally; CI first run pending; Phase 2 starts on the owner's go-ahead.

Labels used below: **Verified** = automated tests pass locally (and in CI once the run is green);
**Built, not yet verified** = code exists, the check has not run; **Planned** = not built.
Nothing here has been sandbox-tested against an external provider or production-verified.

## Baseline (Phase 0)

- Repository `shubham-24701/SMET-ERP` was empty (one-line `README.md`, branch `main`, one commit).
  No code, tests, dependencies or uncommitted work to preserve. No baseline failures.
- Requirements source: *Secure Hive – Project Proposal – SMET v1.0* (26 pages read). The master
  implementation prompt and its 111-checkpoint addendum are the engineering brief.
- Development environment: owner's Windows 11 laptop (Python via uv, Node, Git; no Docker). This
  build was done in a Linux cloud workspace with PostgreSQL 16; CI is set up to prove it on Windows
  (first run pending).
- Reused, adapted: proven patterns from the owner's Calling-Bot project (cookie sessions + CSRF,
  JSON log redaction, database job queue, security route matrix, Windows CI), moved from SQLite
  to PostgreSQL and from fixed roles to configurable permissions.

## Phase plan

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Baseline, plan, assumptions, 111-checkpoint register | Written |
| 1 | Foundation: sign-in, sessions, CSRF, configurable roles/permissions (17 role templates), users, append-only audit log, company settings, job queue, health, CI, design system | **Built, verified locally; CI pending** |
| 2 | Master data (models, BOM-ready items, UoM, parties, warehouses, numbering), suppliers & procurement (requests, quotations, POs with approval, receipts & inspection, returns), inventory (ledger, reservations, transfers, counts, adjustments, reconciliation) | Next |
| 3 | Assembly & quality: BOM versions, assembly orders, issue/consume/return, serial assignment, configurable checklists and test records, hold/rework, finished-stock receipt | Planned |
| 4 | Sales & projects: enquiries, quotations with revisions and approval, sales orders, projects/sites/surveys, requirement sheets with approval, procurement & assembly links, dispatch and delivery | Planned |
| 5 | Installation & traceability: visits, commissioning, handover, QR labels, battery banks, lifecycle timeline | Planned |
| 6 | Warranty & after-sales: registration/activation, public verification portal, AMC, service requests, replacements, maintenance alerts and reset codes (manual process until a supplier API is confirmed) | Planned |
| 7 | Notifications (in-app; email/SMS/WhatsApp adapters off until approved), dashboards, reports, exports, file attachments | Planned |
| 8 | Hardening: 111-checkpoint evidence, performance and concurrency tests, backup/restore drill, deployment package, incident process | Planned |
| 9 | End-to-end verification, release readiness, activation checklist | Planned |

## Phase 1: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| First-run setup | Company profile + first System Administrator; runs once even when two requests race | `test_auth.py::test_setup_runs_once_even_when_two_arrive_together` |
| Sign-in and sessions | Argon2 passwords (12+ chars), opaque server sessions (only SHA-256 stored), new token each sign-in, 10 h limit, 60 min idle limit, logout, generic errors, rate limits (5/min per account, 30/min per client), failed sign-ins audited | `test_auth.py`, `test_platform.py::test_each_sign_in_issues_a_new_session_token` |
| Temporary passwords | Admin-created users and resets get a one-time password shown once; the user must replace it before anything else; changing a password signs out every other session | `test_new_user_must_change_the_temporary_password_first`, Playwright journey |
| Roles & permissions | Permission catalogue (`module.action`), 17 role templates from the proposal, custom roles, several roles per person, read fresh on every request; template permissions added by later versions never re-add what an admin removed | `test_admin.py` |
| Escalation guards | Nobody changes their own roles or access, nor edits a role they hold; the last person able to manage users and roles can't be removed; portal roles can't go to staff; the System Administrator holds no business permissions | `test_nobody_changes_their_own_roles_or_a_role_they_hold`, `test_the_last_admin_can_never_be_removed`, `test_admin_holds_no_business_permissions` |
| Server-side enforcement | Every route checked from the API's own route list: sign-in required, permission required, CSRF required on every write | `test_security_matrix.py` (mutation check recorded in TEST_RESULTS.md) |
| Audit log | Who, when, what changed (field diffs; permissions added/removed), why, IP, request id. Append-only: the app's login has no UPDATE/DELETE/TRUNCATE on it, and a trigger refuses them even for the schema owner. Filters, CSV export (formula-safe, audited) | `test_platform.py::test_the_app_login_cannot_change_or_delete_audit_events`, `::test_not_even_the_schema_owner_can_rewrite_audit_events` |
| Optimistic locking | `version` on users, roles, company; a stale save gets 409 | `test_role_changes_are_audited...`, `test_company_settings_validate_and_lock` |
| Company settings | Name, IANA time zone (Asia/Dubai default), currency (AED default), live preview | `test_company_settings_validate_and_lock` |
| Job queue | PostgreSQL `FOR UPDATE SKIP LOCKED`, retries with exponential backoff, dead letters kept, idempotency keys, hourly purge | `test_jobs_retry_with_backoff_then_dead_letter`, `test_parallel_workers_never_run_a_job_twice` |
| Platform | Liveness/readiness (database + migration version), host allow-list, chunked body limit, security headers, calm 500s without internals, JSON logs with redaction, 30 s statement timeout | `test_platform.py` |
| Database setup | `python -m app.setup_db`: two databases, least-privilege logins, `.env` written | used by CI on Linux and Windows |
| Web app | Sign-in, setup, forced password change, overview (team setup, roles in use, recent changes), users (count-card filters, add/edit/deactivate/reset), roles editor, audit log, company settings, system health; light/dark/system theme; phone layout; permission-aware navigation | Vitest (5), Playwright (2 journeys), design inspector at 1440/768/390 |

## Verification results (2026-10-28, local)

See [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) for the commands and outputs.

- Backend: 47 pytest tests passed; ruff lint and format clean; pip-audit: no known vulnerabilities.
- Frontend: oxlint clean, TypeScript clean, 5 Vitest tests passed, production build OK, npm audit: 0.
- Browser: 2 Playwright journeys passed locally (Chromium); CI will run them in Edge on Windows.
- Design: taste-skill inspector, app rules, 0 FAIL on every screen; remaining WARNs are defended
  in [docs/DESIGN.md](docs/DESIGN.md).
- CI (GitHub Actions): **blocked.** The first run (40694915802) did not start any job: GitHub reports
  that recent account payments failed or the spending limit must be raised (private repositories use
  paid Actions minutes beyond the free allowance). So the Windows and Edge checks have **not run yet**.
  Owner's choice: fix the billing setting, or make the repository public (Actions is free for public
  repositories), or run the README checks on the laptop.

## Known limitations (Phase 1)

- Rate limits are in memory, per API process (fine for one process; noted in code).
- No email yet, so password resets are handed over by the administrator (one-time password).
- Multi-factor authentication: not built; the session layer is the extension point (Phase 8 decision).
- Portal users (distributors, customers) arrive with the portals (Phases 4–6); their role templates exist.
- Dark mode is styled but was not photographed by the inspector.

## Blockers

- CI cannot run until the GitHub billing issue above is resolved by the owner (no change made here).

None that stop Phase 2 from being built: every rule it needs has a safe, changeable default. The
answers marked *(Phase 2)* in [docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md](docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md)
(models, warehouses, stock corrections, stock value, purchase approval, document numbers) are needed
before real use.
