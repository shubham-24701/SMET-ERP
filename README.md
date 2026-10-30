# SMET Operations

Battery Operations ERP for Smart Mechanical Equipment Trading LLC (SMET), built by Secure Hive from
the proposal *Secure Hive – Project Proposal – SMET v1.0*. One shared record for every battery, from
supplier receipt through assembly, stock, sales, projects, dispatch, installation, warranty and service.

**Status:** Phases 1–6 (0.5.0). Foundation (sign-in, users, configurable roles, append-only audit
log, settings); master data, purchasing and goods receipt with inspection, the stock ledger with
reservations, counts and corrections; bills of materials, assembly orders, serial numbers and the
final-inspection gate; enquiries, quotations with revisions, sales orders, projects with engineer-approved
requirement sheets, and dispatch with delivery notes; battery banks, installation, commissioning and
handover visits with evidence, QR labels and each battery's lifecycle; warranty policies and
registrations, a public check and request page, maintenance contracts, service requests and
maintenance reset codes. Notifications and reports follow; see
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Stack

| Layer | Choice |
| --- | --- |
| Web app | React 19 + TypeScript + Vite (browser and mobile browser) |
| API | Python 3.13, FastAPI, REST under `/api/v1` |
| Database | PostgreSQL 16 or 17 (SQLAlchemy 2, Alembic migrations) |
| Background jobs | Database-backed queue, worker inside the API by default |
| Tests | pytest, Vitest, Playwright (Edge on Windows in CI) |

Everything is open source and free. Nothing here buys, rents or switches on a paid service.

## Run it on Windows 11

One-time setup. You need Git, [uv](https://docs.astral.sh/uv/), Node.js 22 or 24, and PostgreSQL:

```powershell
winget install -e --id PostgreSQL.PostgreSQL.17 --interactive
```

The installer asks for a password for the `postgres` superuser (keep it; you need it once below) and
where to keep the data (a folder on D: is fine if C: is small). Then, in PowerShell:

```powershell
git clone https://github.com/shubham-24701/SMET-ERP
cd SMET-ERP\backend
uv sync
uv run python -m app.setup_db      # asks for the postgres password; writes backend\.env
uv run alembic upgrade head
cd ..\frontend
npm install
```

`setup_db` creates two databases (`smet`, and `smet_test` for the automated tests) and two logins with
random passwords: `smet_owner` (owns the schema, used by migrations) and `smet_app` (what the app uses;
it cannot change the schema or the audit log). Their addresses go to `backend\.env`, which is never
committed. Running it again gives both logins new passwords and updates `.env`.

Start it, in two PowerShell windows:

```powershell
# window 1: the API (and the background worker)
cd SMET-ERP\backend
uv run uvicorn app.main:app --reload --no-proxy-headers --host 127.0.0.1 --port 8000

# window 2: the web app
cd SMET-ERP\frontend
npm run dev
```

Open <http://localhost:5173>. The first visit asks for the company name and creates the first System
Administrator. That account manages users and roles but holds no business permissions: create a
separate account for anyone who approves purchases, adjusts stock or handles warranties.

After pulling a new version: `cd backend; uv sync; uv run alembic upgrade head; cd ..\frontend; npm install`.

Photographs and reports from site visits are stored in `backend\data\files` unless `FILES_DIR` in
`backend\.env` points elsewhere (for example `FILES_DIR=D:/smet-files`, to keep them off the system
drive). Include that folder in backups. QR labels point to `PUBLIC_BASE_URL`; until it is set to
SMET's public address, labels work only on this computer.

`CODES_KEY` in `backend\.env` encrypts maintenance reset codes; `setup_db` writes it once. Back it
up with `.env`: without it, recorded codes can't be read. The public page is `/verify`.

## Checks

```powershell
cd backend
uv run ruff check . ; uv run ruff format --check . ; uv run pytest
cd ..\frontend
npm run lint ; npm run typecheck ; npm test ; npm run build
$env:PW_CHANNEL = "msedge"; npx playwright test   # the browser journeys, in Edge
```

The tests use the `smet_test` database and wipe it; they never touch `smet`. CI runs all of this on
Linux and Windows on every push (`.github/workflows/ci.yml`), plus a secret scan and dependency audits.

## Documentation

- [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md): what is built, verified and next
- [docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md](docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md): business rules SMET must confirm
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/engineering/ARCHITECTURE_DECISIONS.md](docs/engineering/ARCHITECTURE_DECISIONS.md)
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md), [docs/API.md](docs/API.md)
- [docs/ROLE_PERMISSION_MATRIX.md](docs/ROLE_PERMISSION_MATRIX.md)
- [docs/engineering/ENGINEERING_CHECKLIST_111.md](docs/engineering/ENGINEERING_CHECKLIST_111.md): the 111 engineering checkpoints, one by one
- [docs/security/SECURITY_CHECKLIST_111.md](docs/security/SECURITY_CHECKLIST_111.md): the security view of the same register
- [docs/TEST_STRATEGY.md](docs/TEST_STRATEGY.md), [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)
- [docs/INTEGRATION_REGISTER.md](docs/INTEGRATION_REGISTER.md): email, SMS/WhatsApp, supplier API, file storage
- [docs/DESIGN.md](docs/DESIGN.md): the interface's design decisions
