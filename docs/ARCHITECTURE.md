# Architecture

A modular monolith: one API process, one PostgreSQL database, one web app. Each business area is a
module in the same codebase sharing one set of records, so a battery's procurement, assembly,
dispatch, installation and service history is one chain of rows, not copies kept in sync.

```
 Browser (desktop or phone)
   │  https, same origin
   ▼
 Reverse proxy (production: TLS, HTTP/2, compression; development: Vite on :5173)
   │  /api/*                          everything else
   ▼                                  ▼
 FastAPI app (:8000)                 Static web app (React build)
   ├─ middleware: host allow-list, body limit, CSRF, request id, security headers, JSON logs
   ├─ auth.py      sessions, sign-in, permission checks (require("module.action"))
   ├─ admin.py     users, roles, audit log, company settings
   ├─ <modules>    procurement, inventory, assembly, sales, projects, … (Phases 2–7)
   └─ jobs.py      worker thread: queued jobs + periodic tasks
   │
   ▼
 PostgreSQL ─ smet (logins: smet_owner owns the schema; smet_app reads/writes rows)
```

## Request path

1. The host allow-list refuses any `Host` other than the configured ones (DNS-rebinding protection).
2. Bodies over 1 MB are refused while they stream in.
3. Writes need the CSRF header matching the `smet_csrf` cookie (double submit).
4. The route's dependency loads the session from `smet_session` (only its SHA-256 is stored),
   checks expiry and idleness, loads the person's permissions fresh, and refuses with 401/403.
5. The handler validates input (Pydantic), does its work in one transaction, and adds audit rows
   in the same transaction, so a change and its audit record commit or roll back together.

## Consistency rules

- One database transaction per business operation. No distributed transactions: there is one database.
- Contended rows are locked (`SELECT … FOR UPDATE`); edited records carry `version` (409 on a stale save).
- Things that leave the system (email, SMS, supplier calls) are queued as jobs after the commit and
  retried; they never run inside the business transaction (Phase 7).
- The audit log is append-only at the database level (grants and a trigger).
- Timestamps are `timestamptz` in UTC; the web app shows them in the company's time zone.

## Background work

`jobs.py`: a `jobs` table claimed with `FOR UPDATE SKIP LOCKED`, so several workers can run without a
leader. Failures retry with exponential backoff (30 s × 2ⁿ, at most 1 h), then stay as dead letters
for review. Periodic tasks (session and job clean-up; later reminders, AMC schedules) run in the
same loop. The worker runs inside the API by default; `python -m app.jobs` runs it separately.

## Code layout

```
backend/app/    config, db, models, auth, rbac, audit, admin, jobs, logs, setup_db, main
backend/migrations/versions/   Alembic migrations (0001: foundation + audit trigger)
backend/tests/  pytest suites; conftest uses the smet_test database
frontend/src/   api.ts (fetch + CSRF), lib.ts (hooks), ui.tsx (components), Shell, Auth, Admin, pages
frontend/e2e/   Playwright journeys
docs/           this documentation
```

Decisions and their reasons: [engineering/ARCHITECTURE_DECISIONS.md](engineering/ARCHITECTURE_DECISIONS.md).
