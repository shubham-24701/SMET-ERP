# Security view of the 111-checkpoint register

The master register is [../engineering/ENGINEERING_CHECKLIST_111.md](../engineering/ENGINEERING_CHECKLIST_111.md),
with the user's exact list of 111 checkpoints. Not all of them are security controls; this file lists
the ones that are, by their original number, and adds ERP-specific security areas from the master
prompt (§28), which are supplementary and not part of the original list.

## Security-relevant checkpoints (original numbering)

| # | Checkpoint | Status at end of Phase 1 |
| --- | --- | --- |
| 1 | Rate Limiting | Pass for sign-in, password change and the public QR check (30 per minute per address) |
| 4 | Reverse Proxies | Host allow-list passes; proxy hardening in P8, including trusting only the proxy's address for client IPs (until then, clients behind a proxy share one rate-limit bucket) |
| 11 | Timeouts | Database statement and connect timeouts pass |
| 14 | Idempotency | Pass for setup, jobs and every stock-changing operation (operation keys) |
| 39 | Race Conditions | Pass for setup, job claims, access changes, reservations of the last unit and serial reuse |
| 44 | Backpressure | Request size, page size, export size and hashing limits pass |
| 59 | Logging | Redaction tests pass |
| 67 | Secrets Management | Built; secret scan blocked until CI can run (GitHub billing) |
| 68 | IAM | Pass (route matrix, escalation guards, database grants) |
| 69 | OAuth | Not applicable now |
| 70 | JWT Rotation | Not applicable: opaque sessions, rotated at sign-in (tests pass) |
| 71 | TLS | Pending hosting (P8) |
| 72 | Encryption at Rest | Pending hosting; reset codes encrypted in P6 |
| 73 | Encryption in Transit | Pending hosting (P8) |
| 74 | WAF | Pending; not active, not claimed |
| 75 | DDOS Protection | Pending; app-level limits only |
| 76 | CORS | Pass: no cross-origin access |
| 77 | CSRF | Pass on every write route |
| 78 | SQL Injection | Pass |
| 79 | XSS | Pass for the API and exports; web-app CSP at the proxy in P8 |
| 80 | SSRF | Not applicable until provider calls (P6–P7) |
| 83 | Disaster Recovery | Not built (P8) |
| 84 | Backups | Not built (P8) |
| 101 | Webhooks | Not built (P6–P7): signatures and replay protection required |
| 109 | Production Incidents | Not built (P8) |
| 111 | Post Morterm | Not built (P8) |

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
