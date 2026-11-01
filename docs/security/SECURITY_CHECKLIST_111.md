# Security view of the 111-checkpoint register

The master register is [../engineering/ENGINEERING_CHECKLIST_111.md](../engineering/ENGINEERING_CHECKLIST_111.md),
with the user's exact list of 111 checkpoints. Not all of them are security controls; this file lists
the ones that are, by their original number, and adds ERP-specific security areas from the master
prompt (§28), which are supplementary and not part of the original list.

## Security-relevant checkpoints (original numbering)

| # | Checkpoint | Status at end of Phase 8 |
| --- | --- | --- |
| 1 | Rate Limiting | Pass: sign-in 5/min per account and address plus 100 per 15 min per account, 30/min per address; password change; public checks and forms (in memory, one API process) |
| 4 | Reverse Proxies | Pass: host allow-list; `X-Forwarded-For` believed only from `TRUSTED_PROXIES` (`test_the_client_address_is_believed_only_from_trusted_proxies`) |
| 11 | Timeouts | Pass: database connect and statement timeouts; provider calls time out |
| 14 | Idempotency | Pass for setup, jobs, every stock-changing operation and message delivery keys |
| 39 | Race Conditions | Pass for setup, job claims, access changes, last-unit reservations, serial reuse and concurrent saves (load check: [200, 409]) |
| 44 | Backpressure | Pass: request size, page size, export size and hashing limits |
| 59 | Logging | Pass: redaction tests |
| 67 | Secrets Management | Pass: `.env` only, mode 600; secret scan in CI; provider credentials encrypted; owner password kept out of the process list during backup and restore (`PGPASSWORD`) |
| 68 | IAM | Pass (route matrix, per-report permissions, escalation guards, database grants re-applied after a restore). Residual risk L2 below |
| 69 | OAuth | Not applicable |
| 70 | JWT Rotation | Not applicable: opaque sessions, rotated at sign-in; the CSRF token is replaced at sign-in too |
| 71 | TLS | Partial: production refuses insecure cookies and sends HSTS; certificate at hosting, not tested |
| 72 | Encryption at Rest | Partial: reset codes and provider credentials encrypted (Fernet); S3 objects stored with server-side encryption; disk and backup encryption at hosting |
| 73 | Encryption in Transit | Pending hosting (TLS to browsers and to a managed database) |
| 74 | WAF | Pending; not active, not claimed |
| 75 | DDOS Protection | Pending; app-level limits only |
| 76 | CORS | Pass: no cross-origin access |
| 77 | CSRF | Pass on every write route; token replaced at sign-in (`test_signing_in_replaces_the_csrf_token`) |
| 78 | SQL Injection | Pass |
| 79 | XSS | Pass for the API and exports (cells and headings neutralised); web-app CSP set at the proxy (hosting) |
| 80 | SSRF | Partial: fixed provider hosts; the SMTP host is admin-set (residual risk L6 below) |
| 83 | Disaster Recovery | Pass: restore tested on every run; drill documented in the runbook, not yet exercised |
| 84 | Backups | Pass: `app.backup` create, verify, restore (`test_a_backup_restores_the_data_and_the_files`); scheduling and off-site copy at hosting |
| 101 | Webhooks | Not built: needs a public address (hosting); signatures and replay protection required |
| 109 | Production Incidents | Procedure written (runbook); not exercised |
| 111 | Post Morterm | Template written (runbook) |

## Supplementary ERP security areas (master prompt §28, not the original list)

| Area | Phase 1 state | Evidence |
| --- | --- | --- |
| Password hashing | Argon2id (argon2-cffi), rehash on stronger settings, 12+ characters | `auth.py` |
| Session security | HttpOnly, SameSite=Lax cookie; hash stored; 10 h absolute, 60 min idle; rotation at sign-in; revocation on deactivate, reset and password change | `test_auth.py` |
| Account enumeration | Same message and timing for unknown email and wrong password (dummy hash) | `test_wrong_password_is_generic_and_audited` |
| Account recovery | Admin-issued one-time password, must be changed at first sign-in; email reset when email exists (P7) | `test_new_user_must_change…` |
| MFA | Not built; extension point is the session layer. Decision in P8 | — |
| Object-level authorization / IDOR | Staff records are company-wide by design (one company); every route checks its permission (route matrix). Party scoping arrives with the portals (P6) | `test_security_matrix.py` |
| Approval separation | Purchase orders, stock corrections, quotations, requirement sheets and project changes can't be approved by anyone who prepared, changed or submitted them (configurable for orders, corrections and quotations; off) | `test_the_whole_purchase_journey…`, `test_corrections_need_a_second_person…` |
| Audit integrity | Audit log and stock ledger append-only at database level, for the app and the owner | `test_platform.py`, `test_the_ledger_cannot_be_rewritten_by_the_app` |
| File uploads | Pass for visit evidence: type read from the content (JPEG, PNG, WebP, PDF only), 10 MB limit enforced while the body arrives, names stripped of paths, stored outside the web root under a random name with SHA-256, downloads need a permission and are sent with `nosniff` and a strict CSP; PDFs download rather than open | `test_evidence_files_are_checked_by_content_and_kept_safely` |
| Formula injection | Sizing rules run through an AST whitelist (numbers, inputs, arithmetic, five functions); everything else is refused before it is stored or run | `test_formulas_are_arithmetic_only` |
| Public endpoint data minimisation | Health only in P1; verification portal returns approved fields only (P6) | — |
| Dependency review | pip-audit and npm audit clean; run in CI | TEST_RESULTS.md |
| Error handling | 500s carry a reference, never internals | `test_crashes_answer_calmly_without_internals` |

An independent code review (2026-10-28) found 12 defects; 11 are fixed with regression tests, one
(proxy client addresses) waits for hosting. See docs/TEST_RESULTS.md. No penetration test has been performed. "Pass" above means the named automated test passed.

### Phase 5 additions

| Topic | Status | Evidence |
| --- | --- | --- |
| QR payload | Only `<public address>/v/<random 32-hex token>`: no customer, site, internal id or secret; tokens can't be derived from serial numbers | `test_qr_labels_lead_to_a_public_check_that_reveals_nothing_private` |
| Public check | No sign-in; shows serial, product and an in-service status only; unknown or malformed tokens get the same 404; 30 per minute per address | same, `test_the_public_check_tells_installed…` |
| Field work | A technician records only visits they are assigned to, and changes only banks their visit covers | `test_a_bank_goes_from_delivery_to_handover…`, `test_a_technician_moves_batteries_only_into_banks_their_visit_covers` |
| History | Bank placements, visits, banks and file records can't be deleted by the app login; file records can't be changed (migration 0007) | migration 0007, privilege check in TEST_RESULTS.md |

### Phase 6 additions

| Topic | Status | Evidence |
| --- | --- | --- |
| Public portal | No sign-in; product, in-service state and warranty state only (dates if SMET allows); same answer whether or not a submitted serial exists; 30 checks/min and 5 submissions/hour per address; a hashed address is logged on abuse | `test_the_public_forms_land_as_pending_work_and_say_the_same_thing_either_way`, `test_smet_installation_activates_by_policy…` |
| Reset codes | Encrypted at rest (Fernet, `CODES_KEY` in `.env`); never returned in lists or written to the audit or application log; revealed only with `codes.reveal` and a reason, each view audited; erased after the retention period | `test_a_service_request_moves_by_the_rules_and_a_reset_code_stays_secret` |
| Refused attempts | Approvals, corrections, codes, exports, settings, users and roles: refusals are audited (`access.denied`) | `test_a_distributor_sale_is_checked_against_policy_and_decided_by_someone_else` |
| Separation | Nobody decides a warranty registration they submitted (configurable); the administrator can grant code access without holding it | `test_the_administrator_can_give_out_code_access_without_holding_it` |
| History | Warranty, AMC, service and alert records can't be deleted by the app login; `setup_db` keeps every history protection on a re-run | migration 0008, `setup_db.HISTORY_RIGHTS` |

### Phase 8 additions

| Topic | Status | Evidence |
| --- | --- | --- |
| Uploads and malware | Every file passes `MALWARE_SCAN_COMMAND` when set; a refusal (exit 1) is a 422, any scanner error a 503: nothing is stored unscanned (fail closed). The result is kept with the file | `test_uploads_pass_the_virus_scanner_or_are_refused` |
| Backups | `pg_dump` custom format plus a files zip, each with SHA-256; verify checks the checksum and the archive; restore refuses zip entries outside the files folder and re-applies the app login's rights | `test_a_backup_restores_the_data_and_the_files` |
| Retention | Read notifications and message texts older than the retention days are removed or blanked (recipient masked); records and evidence are kept | `test_retention_keeps_records_and_forgets_old_texts` |
| First run | `/auth/setup` works once, for whoever reaches it first: run setup before the server is reachable from outside (runbook) | runbook, `test_setup_runs_once_even_when_two_arrive_together` |

### Independent security review of the whole app (2026-11-01)

A reviewer agent that had not written the code read every module and probed the running app. No
critical or high findings.

| # | Severity | Finding | Fix | Evidence |
| --- | --- | --- | --- | --- |
| M1 | Medium | A restore re-created the tables with default rights, so the app login could again change or delete audit and history rows | `setup_db.app_rights` re-applied at the end of every restore | `test_a_backup_restores_the_data_and_the_files` checks the app login is refused afterwards; the earlier state made `test_the_app_login_cannot_change_or_delete_audit_events` fail |
| M2 | Medium | Five wrong passwords from anywhere locked the account owner out for a minute | Strict limit per account and address; a looser one per account (100 per 15 min) that one address can't reach | `test_login_is_rate_limited_per_account` (fails before) |
| M3 | Medium | The person who submitted a warranty could change its dates after someone else approved it | A date correction follows the same four-eyes rule as approving; contact fixes don't | `test_a_distributor_sale_is_checked_against_policy_and_decided_by_someone_else` (fails before) |
| L1 | Low | Export headings (admin-typed field labels) weren't neutralised against spreadsheet formulas | Headings go through `csv_cell` like cells | `test_a_field_label_can_t_become_a_spreadsheet_formula` (fails before) |
| L2 | Low | The administrator can still reach business powers indirectly: grant `codes.reveal` or approval permissions to a second account, reset a business user's password, or loosen approval rules. The `.sensitive` part of the guard checked nothing | Dead check removed. **Accepted residual risk**: every such action is in the append-only audit log. Follow-up: alert management when approve/reveal/correct permissions are granted or a business user's password is reset | `test_the_administrator_can_give_out_code_access_without_holding_it` documents the behaviour |
| L3 | Low | The database owner's password was on the `pg_dump`/`pg_restore` command line | Passed through `PGPASSWORD` | code review |
| L4 | Low | The CSRF token survived sign-out and sign-in as another user | New token at sign-in, removed at sign-out; the web app fetches the new one | `test_signing_in_replaces_the_csrf_token` (fails before); browser journeys pass |
| L5 | Low | Production could run with insecure cookies and no HSTS; first-run setup is open until used | Refused at start; HSTS sent with secure cookies; setup before exposing (runbook) | `test_production_needs_https_and_https_gets_hsts` |
| L6 | Low | The SMTP host and port are free, so the connection test could probe internal machines | **Accepted**: needs `notifications.configure`, and the test is refused while `EXTERNAL_SENDS=off` (the default). Revisit if non-administrators get that permission | code review |

No penetration test has been performed. "Pass" means the named automated test passed.
