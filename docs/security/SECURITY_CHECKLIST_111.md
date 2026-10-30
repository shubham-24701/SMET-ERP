# Security view of the 111-checkpoint register

The master register is [../engineering/ENGINEERING_CHECKLIST_111.md](../engineering/ENGINEERING_CHECKLIST_111.md),
with the user's exact list of 111 checkpoints. Not all of them are security controls; this file lists
the ones that are, by their original number, and adds ERP-specific security areas from the master
prompt (§28), which are supplementary and not part of the original list.

## Security-relevant checkpoints (original numbering)

| # | Checkpoint | Status at end of Phase 1 |
| --- | --- | --- |
| 1 | Rate Limiting | Pass for sign-in and password change; public verification in P6 |
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
| Approval separation | Purchase orders and stock corrections can't be approved by the person who raised them (configurable, off) | `test_the_whole_purchase_journey…`, `test_corrections_need_a_second_person…` |
| Audit integrity | Audit log and stock ledger append-only at database level, for the app and the owner | `test_platform.py`, `test_the_ledger_cannot_be_rewritten_by_the_app` |
| File uploads | Not built (P7): type sniffing, size limits, storage outside the web root, permission-checked downloads | — |
| Public endpoint data minimisation | Health only in P1; verification portal returns approved fields only (P6) | — |
| Dependency review | pip-audit and npm audit clean; run in CI | TEST_RESULTS.md |
| Error handling | 500s carry a reference, never internals | `test_crashes_answer_calmly_without_internals` |

An independent code review (2026-10-28) found 12 defects; 11 are fixed with regression tests, one
(proxy client addresses) waits for hosting. See docs/TEST_RESULTS.md. No penetration test has been performed. "Pass" above means the named automated test passed.
