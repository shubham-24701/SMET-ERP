# Engineering checklist: all 111 checkpoints

The master register. Names, numbering and spelling are exactly as supplied (including "SLO's",
"SLI's" and "Post Morterm"). Updated at the end of every phase.

**Assessment**: Applicable · Partial (some of it applies) · Not applicable (with the reason) ·
Pending (decided at deployment). **Result**: Pass (a named test or check ran and passed) · Not tested ·
Blocked · N/A. An applicable item that isn't built yet is *Applicable / Not tested*, never "N/A".

To keep 111 rows readable, the 16 fields the addendum lists are folded into these columns:
*Decision* carries purpose, rationale, risk if omitted and cost; *Where* carries the implementation
location and status; *Next* carries dependencies, follow-up and residual risk.

Last update: 2026-10-30, end of Phase 6. Deployment items (TLS, WAF, backups…) wait for the hosting
decision in Phase 8 and are marked Pending, not Pass.

| # | Checkpoint | Assessment | Decision | Where | Verification | Result | Next |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Rate Limiting | Applicable | Brute force on sign-in and later enumeration of public serial checks are the real threats. 5/min per account, 30/min per client, 5/min for password changes | `auth.rate_limit`, `public.limit` | `test_login_is_rate_limited_per_account`, `test_the_public_check_tells_installed_from_in_service_and_is_rate_limited` | Pass (sign-in; public checks 30/min and public forms 5/hour per address) | In memory per process: move to the database before a second API process; behind a proxy, key on the forwarded address (checkpoint 4) |
| 2 | Caching | Partial | No server cache: stock and permissions must come from the database (ADR-007). API answers `no-store`; built assets are content-hashed | `main.SECURITY_HEADERS`, Vite build | `test_security_headers_and_request_id` | Pass (no-store) | Revisit only if a report is measured slow (P8) |
| 3 | Load Balancing | Pending | One small server is enough; a load balancer would need a shared rate-limit store and a separate worker first | — | — | Not tested | P8 deployment note; revisit at >1 server |
| 4 | Reverse Proxies | Applicable | Production sits behind a reverse proxy (TLS, HTTP/2, compression). The app ignores proxy headers by default and only answers allowed host names | `--no-proxy-headers`, `TrustedHostMiddleware` | `test_unknown_hosts_are_refused` | Pass (host allow-list) | P8: trust only the proxy's address for client IPs; until then all clients behind a proxy share one rate-limit bucket and audit IP (review L4) |
| 5 | API Gateways | Not applicable | One API; what a gateway would do (auth, limits, size caps, headers) is in the app's middleware | `main.py` | — | N/A | Revisit if third parties consume the API |
| 6 | CI/CD | Applicable | CI on every push: lint, tests on Linux and Windows with PostgreSQL, Edge journeys, secret scan, audits. Deployment stays manual until hosting is approved | `.github/workflows/ci.yml` | first CI run (40694915802) | Blocked: GitHub did not start jobs (account billing) | Owner resolves billing; P8: a release workflow |
| 7 | Docker | Partial | Not required to develop (the owner's laptop has none). A production image is useful for hosting | — | — | Not tested | P8: Dockerfile + compose, built in CI |
| 8 | Kubernetes | Not applicable | One app and one database; a cluster's cost and operation are not justified at SMET's scale | — | — | N/A | Revisit at several services or teams |
| 9 | Service Discovery | Not applicable | Fixed addresses: one API, one database | — | — | N/A | — |
| 10 | Circuit Breakers | Applicable | Needed when calling email, SMS/WhatsApp and supplier APIs, so a provider outage can't pile up work | — | — | Not tested | P7 with the first provider adapter |
| 11 | Timeouts | Applicable | Database: 5 s to connect, 30 s per statement; job lease 5 min; provider calls get timeouts in P7 | `db.make_engine`, `jobs.LEASE` | `test_database_statements_time_out` | Pass | P7: HTTP timeouts |
| 12 | Retries | Applicable | Failed jobs retry automatically up to `max_attempts` | `jobs.run_one` | `test_jobs_retry_with_backoff_then_dead_letter` | Pass | — |
| 13 | Exponential Backoff | Applicable | 30 s × 2ⁿ, capped at 1 h | `jobs.run_one` | same test | Pass | — |
| 14 | Idempotency | Applicable | Jobs carry unique keys; setup runs once; every stock-changing post (receipt, decision, return, transfer, reservation, issue, build, inspection) carries an operation key recorded in the same transaction, so a retry returns the first result; dispatch send, delivery and cancel too; service receipt, return, parts and replacements too; a quotation converts to one order (unique key) | `jobs.enqueue`, `inventory.claim_key`, `sales_orders.quotation_revision_id` | `test_the_whole_purchase_journey…`, `test_dispatch_takes_reserved…` (repeated send), job key test | Pass | — |
| 15 | Message Queues | Applicable | PostgreSQL table queue, no broker to run or pay for (ADR-006) | `jobs.py` | `test_parallel_workers_never_run_a_job_twice` | Pass | — |
| 16 | Pub/Sub | Not applicable (now) | One process; notifications will be events written in the same transaction and fanned out by the worker | — | — | N/A | P7 |
| 17 | Event-Driven Architecture | Partial | Audit events and queued jobs now; domain events for notifications and reminders in P7. Core stock and approval logic stays synchronous and transactional | `audit.record`, `jobs` | — | Not tested | P7 |
| 18 | Distributed Transactions | Not applicable | One database: every business operation is one transaction | — | — | N/A | — |
| 19 | Saga Pattern | Not applicable | External steps (sending a message) run after commit as retryable jobs (outbox); no multi-service rollback exists | — | — | N/A | P7 outbox |
| 20 | Dead Letter Queues | Applicable | A job that exhausts its retries stays `dead`, with its error, on System health | `jobs.run_one`, System health | `test_jobs_retry…` | Pass | P7: requeue button |
| 21 | Cron Jobs | Applicable | Periodic tasks in the worker loop (hourly clean-up now; AMC schedules and reminders later) | `jobs.PERIODIC` | `test_periodic_purge_removes…` | Pass | P6: AMC and warranty reminders |
| 22 | Web Sockets | Not applicable | No live two-way need; polling or SSE covers notifications | — | — | N/A | — |
| 23 | Long Polling | Not applicable | As above | — | — | N/A | — |
| 24 | Server Sent Events | Not applicable (now) | Considered for the notification bell in P7 | — | — | N/A | P7 decision |
| 25 | Database Indexing | Applicable | Indexes on foreign keys, sessions' token, audit (time, action, entity, actor), jobs (status, run time); stock moves by (item, time), document, location and serial; balances by location; serials by status, item, location | migrations 0001, 0003 | — | Not tested | P8: query plans at volume |
| 26 | Query Optimization | Applicable | Permissions load in one join per request; lists are capped at 200 rows | `auth.load_access` | — | Not tested | P8 measurements |
| 27 | N+1 Queries | Applicable | Roles and users lists load related rows in bulk; stock summary and movement lists load names in a few queries per page (`summaries`, `move_rows`); document lists still read lines per row (≤ 50 per page) | `admin._role_rows`, `admin._roles_of`, `inventory.summaries`, `inventory.move_rows` | `test_roles_list_runs_a_fixed_number_of_queries` | Pass | Add the same guard to each new list |
| 28 | Connection Pooling | Applicable | SQLAlchemy pool (5 + 5 overflow), pre-ping replaces dropped connections | `db.py` | — | Not tested | P8: connection-exhaustion test |
| 29 | Read Replicas | Not applicable | Load is small; reports run on the primary | — | — | N/A | Revisit if reports slow down writes |
| 30 | Sharding | Not applicable | Data volume (thousands of batteries a year) fits one database comfortably | — | — | N/A | — |
| 31 | Partitioning | Not applicable (now) | Audit and stock-movement tables could be partitioned by year past tens of millions of rows | — | — | N/A | Revisit at volume |
| 32 | Replication | Pending | Production: managed database with standby, or WAL archiving on a VM | — | — | Not tested | P8 with hosting cost |
| 33 | Leader Election | Not applicable | `SKIP LOCKED` lets workers share jobs without a leader; periodic tasks are idempotent clean-ups | `jobs.claim` | parallel worker test | N/A | — |
| 34 | CAP Theorem | Applicable (assessed) | One primary database: consistency over availability during a partition. Right for stock and approvals | ADR-001 | — | N/A | — |
| 35 | Eventual Consistency | Partial | Only outbound notifications are eventual; stock, serials and authorization are always read from committed data | ADR-007 | — | Not tested | P7 |
| 36 | Optimistic Locking | Applicable | `version` on users, roles, company, items, parties, warehouses, locations, orders, reservations, corrections, counts; stale saves get 409 | `admin.py`, `masters.py`, `procurement.py`, `inventory.py`, `assembly.py` | `test_role_changes_are_audited…`, `test_corrections_need_a_second_person…` (second approval refused) | Pass | Every editable record in later phases |
| 37 | Pessimistic Locking | Applicable | Company, user and role rows locked on update; access changes serialised by an advisory lock; items locked (in id order) before any change of availability; serials, order lines and number sequences locked while used; balances changed by a guarded UPDATE | `admin.*`, `inventory.lock_items`, `inventory.serials_at`, `masters.next_number` | `test_reservations_hold_stock_and_the_last_units_go_to_one_person` | Pass | P4: dispatch |
| 38 | Distributed Locking | Not applicable | Database row and advisory locks suffice with one database | — | — | N/A | — |
| 39 | Race Conditions | Applicable | Simultaneous setup; parallel job workers; two admins removing each other; four people reserving the last unit at once; a serial used twice; consuming more than issued | `auth.setup`, `jobs.claim`, `admin.serialize_access_changes`, `inventory.lock_items` | `test_setup_runs_once…`, parallel worker test, `test_two_admins…`, `test_reservations_hold_stock_and_the_last_units_go_to_one_person`, `test_from_components_to_a_passed_battery`, `test_revisions_are_kept_and_conversion_happens_once` (three simultaneous conversions); battery banks: partial unique indexes (one place per battery, one battery per position), banks locked in id order for moves, the site locked for renames | Pass | — |
| 40 | Deadlocks | Applicable | Multi-item operations lock items in id order (`lock_items`), serials in id order | `inventory.lock_items`, `inventory.serials_at` | concurrent reservation test (no deadlock) | Pass (P2–P3 scope) | P8: mixed-load soak test |
| 41 | Memory Leaks | Applicable | In-memory rate-limit map is bounded (10,000 keys, idle keys dropped) | `auth.rate_limit` | — | Not tested | P8 soak test |
| 42 | Garbage collection | Not applicable | CPython defaults; nothing long-lived beyond the bounded map | — | — | N/A | Watch in the P8 soak test |
| 43 | Thread Safety | Applicable | Worker thread uses its own database sessions; password hashing limited by a semaphore | `jobs.py`, `auth._HASHING` | parallel worker test | Pass | — |
| 44 | Backpressure | Applicable | Request size cap (streamed), page size ≤ 200, export ≤ 50,000 rows, 4 concurrent password hashes, worker batches | `main.BodyLimit`, `admin` | `test_oversized_bodies_are_refused_even_when_chunked` | Pass | — |
| 45 | Autoscaling | Not applicable | Steady office-hours load on one server | — | — | N/A | — |
| 46 | Horizontal Scaling | Partial | Possible later: sessions are in the database; the rate-limit map must move to the database first | `auth.py` shortcut note | — | Not tested | Revisit at >1 server |
| 47 | Vertical scaling | Applicable | The growth path: a larger server | — | — | N/A | P8 sizing |
| 48 | CDN | Pending | Not needed at first; a free CDN tier can front the static app if wanted | — | — | Not tested | P8 option |
| 49 | Edge Caching | Not applicable | Business data must not be cached at the edge (`no-store`) | headers | headers test | N/A | — |
| 50 | Cache Invalidation | Not applicable | No caches to invalidate (ADR-007) | — | — | N/A | — |
| 51 | Feature Flags | Partial | Environment switches (API docs, worker placement); integrations will have explicit owner-only activation. Flags never replace permission checks | `config.py` | — | Not tested | P7 activation flags |
| 52 | Blue Green Deployment | Not applicable (now) | One server; a short maintenance window is acceptable for an internal ERP | — | — | N/A | Revisit with an uptime target |
| 53 | Canary Releases | Not applicable | One customer, one installation | — | — | N/A | — |
| 54 | Rolling Deployments | Not applicable | One process | — | — | N/A | — |
| 55 | Rollbacks | Applicable | Every migration has a downgrade; the browser tests downgrade to empty and upgrade on each run. Production rollback = previous release + backup taken before migrating | `migrations/`, `tests/e2e_server.py` | Playwright run (downgrade base → head) | Pass (migration round trip) | P8: release runbook |
| 56 | Health Checks | Applicable | Live (process up) and ready (database answers and schema is current) | `/health/live`, `/health/ready` | `test_health` | Pass | — |
| 57 | Liveness and Readiness Probes | Applicable | Same endpoints, for an uptime monitor or orchestrator | as above | `test_health` | Pass | — |
| 58 | Monitoring | Partial | System health page (database, jobs, version) | `/system/status` | `test_system_status_needs_permission` | Pass (page) | P8: external uptime check |
| 59 | Logging | Applicable | JSON lines with request id; secrets, emails, phone numbers and database passwords redacted; exception text never logged | `logs.py`, `main.request_context` | `test_logs_redact_secrets_and_personal_data` | Pass | — |
| 60 | Distributed Tracing | Not applicable | One service; the request id ties a user's error message to its log lines | `X-Request-ID` | headers test | N/A | — |
| 61 | Metrics | Partial | Each request's duration is logged; aggregated metrics in P8 | `main.request_context` | — | Not tested | P8 |
| 62 | Alerts | Applicable | Failed jobs, failed backups, readiness down: who is told, how | — | — | Not tested | P8 |
| 63 | SLO's | Pending | Targets need the hosting choice and SMET's working hours; illustrative only until measured | — | — | Not tested | P8 |
| 64 | SLI's | Pending | Candidates: API availability, p95 latency, failed jobs, notification failures, backup success | — | — | Not tested | P8 |
| 65 | Error Budgets | Pending | Follows the SLOs | — | — | Not tested | P8 |
| 66 | Observability | Partial | Logs, request ids, health and system pages | as above | as above | Pass (P1 scope) | P8 |
| 67 | Secrets Management | Applicable | `.env` never committed; `setup_db` generates database passwords; no secret reaches the web app; secret scan in CI; provider credentials will be stored encrypted | `.gitignore`, `setup_db.py`, CI `secrets` | gitleaks in CI | Blocked (CI not running: account billing) | P7: encrypted credentials |
| 68 | IAM | Applicable | Configurable permissions, escalation guards (no granting or managing guarded powers you lack), separate database logins without TEMPORARY or schema-record writes (ADR-003, ADR-005) | `rbac.py`, `admin.py`, `setup_db.py` | `test_security_matrix.py`, `test_admin.py`, audit grant tests | Pass | P2+: data scoping by party |
| 69 | OAuth | Not applicable (now) | Staff sign in with SMET accounts; OAuth only if a mail provider requires it for sending (P7) | — | — | N/A | P7 |
| 70 | JWT Rotation | Not applicable | No JWTs (ADR-004); the session token is replaced at every sign-in and all others end on a password change | `auth.start_session` | `test_each_sign_in_issues_a_new_session_token`, `test_changing_password_signs_out_other_sessions` | N/A (equivalent controls pass) | Revisit if tokens are introduced |
| 71 | TLS | Pending | Mandatory in production (proxy with automatic certificates); `COOKIE_SECURE=true` then | `config.cookie_secure` | — | Not tested | P8 |
| 72 | Encryption at Rest | Partial | Disk encryption from the host; backups encrypted; reset codes encrypted in the database with Fernet (`CODES_KEY`, written once by `setup_db`, never rotated by it) | `codes.fernet` | `test_a_service_request_moves_by_the_rules_and_a_reset_code_stays_secret` (stored value is not the code) | Pass (reset codes); disk and backups pending | P8: disk and backup encryption with hosting; key backup procedure |
| 73 | Encryption in Transit | Pending | TLS to browsers; TLS to a managed database (`sslmode=require`) | — | — | Not tested | P8 |
| 74 | WAF | Pending | A free edge WAF can sit in front if the app is public; not active and not claimed | — | — | Not tested | P8 option and cost |
| 75 | DDOS Protection | Pending | Edge protection if public; the app's own limits blunt small floods | `auth.rate_limit`, `BodyLimit` | — | Not tested | P8 |
| 76 | CORS | Applicable | No cross-origin access at all: the web app is served from the same origin | no CORS middleware | `test_no_cross_origin_access_is_granted` | Pass | — |
| 77 | CSRF | Applicable | Double-submit token on every write; SameSite=Lax cookies | `main.csrf_guard` | `test_every_write_needs_the_csrf_token` (all routes) | Pass | — |
| 78 | SQL Injection | Applicable | Parameterised queries only (SQLAlchemy); no SQL built from input | all modules | `test_injection_payloads_are_just_text` | Pass | Keep the rule; reviewed per phase |
| 79 | XSS | Applicable | React escapes all output; no raw HTML; API CSP `default-src 'none'`; CSV exports neutralise formulas | `main.SECURITY_HEADERS`, `admin.csv_cell` | `test_audit_log_filters_and_export` | Pass (P1 scope) | P8: CSP for the web app at the proxy |
| 80 | SSRF | Not applicable (now) | The server fetches no URLs in P1. Provider and supplier calls in P6–P7 will use fixed hosts, no redirects | — | — | N/A | P6–P7 |
| 81 | Database Migration | Applicable | Alembic, run as the schema owner; tests migrate a fresh database every run | `migrations/` | test session start; Playwright round trip | Pass | — |
| 82 | Schema Versioning | Applicable | `alembic_version` is checked by readiness (`migrations: pending` → 503) | `main.ready` | `test_health` | Pass | — |
| 83 | Disaster Recovery | Applicable | Recovery objectives and a restore drill | — | — | Not tested | P8 |
| 84 | Backups | Applicable | `pg_dump` schedule with retention, kept off the server, restore tested | — | — | Not tested | P8 |
| 85 | Failover | Pending | Managed database standby, or restore-from-backup with a stated recovery time | — | — | Not tested | P8 with cost |
| 86 | Multi-Region Deployment | Not applicable | One business in Dubai; cost not justified. Data location may need to stay in the UAE (open question 21) | — | — | N/A | — |
| 87 | Chaos Engineering | Partial | No production chaos. Locally: database restart, provider outage, crashed worker (lease recovery) tests | — | — | Not tested | P8 failure tests |
| 88 | Cost Optimization | Applicable | Open source throughout, no paid service, no Docker or broker needed; hosting options compared before any spend | ADR-001, ADR-006 | — | N/A | P8 cost table |
| 89 | Cold Starts | Not applicable | Not serverless: the worker and the connection pool need a long-running process | ADR-001 | — | N/A | — |
| 90 | Serverless Limits | Not applicable | Serverless rejected: background worker, file storage on disk, database connections, execution limits | ADR-001 | — | N/A | — |
| 91 | Latency | Applicable | Each request's time is logged | `main.request_context` | — | Not tested | P8 measurements |
| 92 | Throughput | Applicable | Requests per second under a representative mix | — | — | Not tested | P8 |
| 93 | P99 Latency | Applicable | Measured in the P8 load test | — | — | Not tested | P8 |
| 94 | Tail Latency | Applicable | Sign-in hashing is bounded (4 at once) so one burst can't stall everything | `auth._HASHING` | — | Not tested | P8 |
| 95 | Network Partitions | Partial | Database unreachable → readiness 503, requests fail cleanly, pool reconnects, worker loop survives | `db.py`, `jobs.worker_loop` | — | Not tested | P8 database-restart test |
| 96 | Clock Skew | Applicable | All times from the app server's clock, stored in UTC; one server; NTP on the host | `models.utcnow` | — | N/A | P8: host NTP check |
| 97 | DNS | Pending | A domain and records for the hosted app; host allow-list updated with it | `ALLOWED_HOSTS` | — | Not tested | P8 |
| 98 | TCP vs UDP | Not applicable | HTTPS and PostgreSQL over TCP only | — | — | N/A | — |
| 99 | HTTP/2 and HTTP/3 | Pending | The reverse proxy serves HTTP/2 (and HTTP/3 if supported) to browsers; the app speaks HTTP/1.1 behind it | — | — | Not tested | P8 |
| 100 | gRPC | Not applicable | Browser clients and REST; no service-to-service calls | — | — | N/A | — |
| 101 | Webhooks | Applicable (later) | Provider delivery receipts and supplier alerts: signature checks and replay protection | — | — | Not tested | P6–P7 |
| 102 | API Versioning | Applicable | Everything under `/api/v1`; breaking changes go to v2 | `main.api` | route matrix | Pass | — |
| 103 | Semantic Versioning | Applicable | 0.x while phases land; version in `pyproject.toml` and `package.json`, shown on System health | `config.VERSION` | — | Pass | Release notes from P2 |
| 104 | Infrastructure as a Code | Pending | A compose file and setup script are the proportionate form for one server | `setup_db.py` (database) | CI uses it | Partial | P8 |
| 105 | Terraform | Not applicable (likely) | One server doesn't need it; decided with hosting | — | — | N/A | P8 review |
| 106 | Helm Charts | Not applicable | No Kubernetes | — | — | N/A | — |
| 107 | Build Caching | Applicable | CI caches npm packages; uv caches wheels | `ci.yml` | first CI run | Blocked (CI not running: account billing) | — |
| 108 | Dependency Hell | Applicable | Lock files (`uv.lock`, `package-lock.json`), Python pinned to 3.13, TypeScript ~6.0, audits in CI | lock files | `pip-audit`, `npm audit` | Pass | Renovate-style updates later |
| 109 | Production Incidents | Applicable | Severity levels, triage, evidence, customer-data handling | — | — | Not tested | P8 runbook |
| 110 | On Call | Applicable | Business-hours ownership for a small team; no 24/7 rota assumed | — | — | Not tested | P8 |
| 111 | Post Morterm | Applicable | Blameless review template with actions, owners and follow-up dates | — | — | Not tested | P8 template |
