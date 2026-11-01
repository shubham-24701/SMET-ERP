# Test strategy

| Layer | Tool | What it proves | Where |
| --- | --- | --- | --- |
| Unit and integration | pytest against real PostgreSQL (`smet_test`) | Business rules, transactions, locking, constraints, permissions, audit rows, migrations | `backend/tests/` |
| Route matrix | pytest over the OpenAPI route list | Every route needs sign-in, its permission and the CSRF token; a new route that forgets one fails without anyone writing a test | `test_security_matrix.py` |
| Database privileges | pytest as the app login and as the owner | Append-only audit log; least-privilege grants | `test_platform.py` |
| Concurrency | threads against the real database | Setup runs once; parallel workers never run a job twice; later: simultaneous reservations, serial assignment | `test_auth.py`, `test_platform.py` |
| Frontend units | Vitest + Testing Library with a fake API | Screens for setup, sign-in, forced password change, permission-aware menu, unreachable server | `frontend/src/app.test.tsx` |
| Browser journeys | Playwright: real API + real database + web app | Whole workflows through the UI, plus direct API refusals; phone width | `frontend/e2e/` (Edge on Windows in CI) |
| Design | taste-skill inspector (`--app`) | Contrast, type, layout, phone fold, banned patterns; screenshots reviewed by eye | `.taste-skill/` (local, not committed) |
| Supply chain | gitleaks, pip-audit, npm audit | No committed secrets; no known-vulnerable dependencies | CI |

Rules: tests never touch the working database (`conftest_env.use_test_database` refuses any database
whose name doesn't end in `_test`); test data is synthetic; external providers are replaced by fakes,
never called. Each phase adds its unit, integration and browser tests, and the business scenarios of the
master prompt (§29) become Playwright journeys as their modules arrive (distributor supply in Phase 4,
project delivery in 5, warranty and service in 6).
