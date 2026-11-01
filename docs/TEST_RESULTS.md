# Test results

Every result below was produced by running the command shown. Results that have not run yet say so.

## 2026-10-30 · Phase 4 · local and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend | `uv run ruff check .`, `uv run pytest -q -W error::DeprecationWarning` | clean; **100 passed** (82 before Phase 4, 11 Phase 4, 7 review regressions) |
| Migrations | upgrade, downgrade to 0004, upgrade, `alembic check` | clean; 0005 Phase 4, 0006 review fix |
| Frontend | lint, typecheck, Vitest, build | clean; 7 passed; JS 516 kB (137 kB gzipped) |
| Browser journeys | `npx playwright test` (local Chromium) | **4 passed**; `phase4.spec.ts` runs two people in two browsers: enquiry → quotation draft → prices → submit → approval by the other → customer answer → sales order → reservation → dispatch of two picked serials → delivery note → delivery; project → site → requirement sheet → engineering review → material list; phone width without sideways scroll |

### Independent review of Phase 4 (2026-10-30)

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | High | Cancelling a sent dispatch could revive a reservation of an order closed in the meantime | Each dispatch line records the reservations it used; a cancellation gives back exactly those, and none if the order is closed | `test_a_dispatch_cancelled_after_its_order_closed_returns_goods_to_free_stock` |
| 2 | High | Every order of a project counted the whole project reservation as its own (shortages under-reported) | A project's reservation is shared once across its open orders, oldest first | `test_a_project_reservation_is_shared_once_across_its_orders` |
| 3 | High | The project reserve ignored what its orders already held (double reservation) | The material list counts the orders' reservations | same |
| 4 | Medium | Planning a dispatch for an unknown order gave a 500 | Order and warehouse checked before anything is written | `test_a_dispatch_for_an_unknown_order_is_refused_cleanly` |
| 5 | Medium | A function name used as a value passed the formula check, then failed at every evaluation | Function names only as calls; input names can't be function names | `test_odd_but_valid_input_answers_clearly_not_with_500` |
| 6 | Medium | Huge numbers crashed the evaluator | Constants above 10¹² refused; overflow answered as a clear error | same |
| 7 | Medium | Renaming a sizing rule to a taken name gave a 500 | Same check as on creation | same |
| 8 | Medium | An explicit null on a required project or task field gave a 500 | Treated as unchanged | same |
| 9 | Low | Raising purchase requests from orders and projects bypassed `purchasing.create`; order and project reservations had different rules | Intended and recorded: a purchase request is only a request (an order still needs purchasing approval); a project now reserves for itself with `projects.create`, as an order does with `sales.create` | ROLE_PERMISSION_MATRIX.md |
| 10 | Low | A revision reset its editors, so an earlier author could approve the next revision | Editors and the submitter carry over | `test_whoever_shaped_a_quotation_cannot_approve_its_next_revision` |
| 11 | Low | Approving an older sheet version replaced a newer approved one | Refused | `test_an_older_sheet_cannot_replace_a_newer_approved_one` |
| 13 | Low | A sizing rule couldn't be switched off from the screen | Checkbox read correctly | code review |
| 14 | Low | Delivery and answer dates defaulted to UTC (yesterday after midnight in Dubai); a quantity refused on a serial item was ignored | Local date; 422 asking for serials | `test_refused_serials_are_named_not_counted` |

Recorded, not changed: the full project record and order dispatch lists are not paginated, and the
order count cards ignore the search (#12); sizes per project stay small; revisit in P8.

## 2026-10-30 · Phases 2–3 · local (Linux cloud workspace, PostgreSQL 16, Python 3.13, Node 22) and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend lint and format | `uv run ruff check .`, `uv run ruff format --check .` | clean |
| Backend tests | `uv run pytest -q -W error::DeprecationWarning` | **82 passed** (53 Phase 1, 19 Phases 2–3, 10 review regressions) |
| Migrations | `alembic upgrade head`, `downgrade 0002`, `upgrade head`, `alembic check` | clean both ways; models match migrations |
| Frontend | `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` | clean; **7 Vitest passed**; JS 435 kB (122 kB gzipped) |
| Browser journeys | `npx playwright test` (local Chromium) | **3 passed**, including `phase23.spec.ts`: two people, warehouse → items → supplier → purchase order (no self-approval) → delivery → inspection decision (8 accepted, 2 rejected) → stock → bill of materials → inspection set-up → assembly order (release, issue, build) → final inspection → serial record; phone width without sideways scroll (asserted) |
| Dependency audits | `pip-audit` on the exported requirements; `npm audit --omit=dev` | No known vulnerabilities; 0 vulnerabilities |
| CI | GitHub Actions on `phase-2-3` (public repository) | Ubuntu and Windows backends, frontend, Playwright in Edge on Windows, gitleaks, pip-audit: all green |

### Independent review (2026-10-30)

A reviewer that had not written the code proved 11 defects with throwaway tests. All are fixed; each
High and Medium one has a regression test in `tests/test_review_p23.py`.

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | High | An assembled battery could be bought and accepted into available stock with no inspection | Assembled models can't be on requests, quotations or orders; receiving refuses them too | `test_assembled_batteries_are_never_bought` |
| 2 | High | A stock count could register a found assembled battery as available | Such a correction is refused at approval; the battery goes in through quarantine and is inspected | `test_a_count_cannot_put_a_found_battery_into_available_stock` |
| 3 | Medium | The same item twice in one build or issue could take another order's material | Each item and lot once per request | `test_a_repeated_line_cannot_take_another_orders_material` |
| 4 | Medium | Inspecting a battery with a serial over 30 characters gave a 500 | Movement document numbers widened to 60 (migration 0004) | `test_long_serials_and_repeated_supplier_serials_are_handled` |
| 5 | Medium | A repeated supplier serial gave a 500 | Checked first, with a clear message | same |
| 6 | Medium | Someone who changed a draft order, or entered counts, could approve the result | Orders keep everyone who edited them, counts everyone who counted; none of them may approve; saved counts are audited | `test_whoever_edited_an_order_or_counted_cannot_approve_the_result` |
| 7 | Medium | A correction sent twice became two corrections | Corrections carry an operation key | `test_a_correction_sent_twice_is_recorded_once` |
| 8 | Medium | Writing off a unit issued to an assembly order left the order stuck | The assembly floor changes only through its orders: no transfers, corrections or counts there | `test_the_assembly_floor_changes_only_through_orders` |
| 9 | Low | Lot-tracked goods couldn't be returned to a supplier from the screen | Lot field on the return form | Playwright (screen), code review |
| 10 | Low | Returning materials with two lots on the floor failed | One row per lot | code review |
| 11 | Low | Pickers stopped at 200 items | `GET /items/options` lists every active item in a few fields | route matrix |
| 13 | Low | A repeated reservation answered in another shape | The first answer is stored whole | `test_repeats_return_the_same_answer…` |
| 15 | Low | "No replacement" on a return still counted the goods as owed | Recorded per line (`qty_not_replaced`); outstanding and on-order drop | `test_goods_not_replaced_are_no_longer_outstanding` |
| 16 | Low | A model with a BOM or orders could change its kind | Every link counts as "in use" | `test_a_model_with_a_bill_of_materials_keeps_its_kind` |
| 17 | Low | A received serial equal to the next SMET number blocked automatic numbering | Numbering skips taken serials | `test_repeats_return_the_same_answer_and_numbering_skips_taken_serials` |

Not changed, recorded: per-row queries in document lists (≤ 50 rows a page; measured in P8) and a full
ledger scan in the on-demand reconciliation report (#12); `setup_db.py` already revokes stock-ledger
writes on every run (#14, checked).

## 2026-10-28 · Phase 1 · local (Linux cloud workspace, PostgreSQL 16.15, Python 3.13.18, Node 22)

| Check | Command | Result |
| --- | --- | --- |
| Backend lint | `cd backend; uv run ruff check .` | All checks passed |
| Backend format | `uv run ruff format --check .` | clean |
| Backend tests | `uv run pytest -q` | **53 passed** in 20 s (after the review fixes; 47 before) |
| Mutation check of the route matrix | removed the permission check in `auth.require`, ran `tests/test_security_matrix.py` | failed as it should (1 failed), restored → 4 passed |
| Python dependency audit | `uvx pip-audit -r requirements.txt --no-deps` (from `uv export`) | No known vulnerabilities found |
| Frontend lint | `cd frontend; npm run lint` (oxlint) | clean |
| Type check | `npm run typecheck` | clean |
| Frontend tests | `npm test` (Vitest) | **5 passed** |
| Production build | `npm run build` | built (JS 300 kB, 94 kB gzipped) |
| npm audit | `npm audit --omit=dev` | found 0 vulnerabilities |
| Browser journeys | `npx playwright test` (local Chromium) | **2 passed**: admin → new user → forced password change → limited menu and 403 from the API; phone width without sideways scroll |
| Design inspector | `inspect.mjs --app` on 6 screens + sign-in, at 1440/768/390 | 0 FAIL; WARNs listed in DESIGN.md |
| Secret scan (stand-in for CI's gitleaks) | `uvx detect-secrets scan --all-files` | Only expected hits: synthetic test passwords, the CI-only database password, `CHANGE_ME` placeholders, the URL template in `setup_db.py`; `backend/.env` (real local passwords) is gitignored and not committed |
| Database privileges | `psql` as `smet_app`: UPDATE/DELETE/TRUNCATE on `audit_events` | permission denied; as owner: "audit_events is append-only" |

## CI (GitHub Actions)

Run 40694915802 (push of `phase-1`): **no job started**. GitHub's annotation: recent account payments
failed or the spending limit needs to be increased. Nothing was verified by CI. Jobs: backend on Ubuntu (PostgreSQL 17 container) and on
Windows (the runner's PostgreSQL), frontend checks, Playwright in Edge on Windows, gitleaks, pip-audit.

## Not run

- No test against a real email, SMS or WhatsApp provider (none built or approved).
- No load or soak test yet (Phase 8).
- No penetration test. Security testing so far is the automated suite above.

## Backend tests in this run

- `test_admin.py :: test_all_role_templates_ship`
- `test_admin.py :: test_admin_holds_no_business_permissions`
- `test_admin.py :: test_create_user_with_roles_returns_a_one_time_password`
- `test_admin.py :: test_portal_roles_cannot_go_to_staff`
- `test_admin.py :: test_nobody_changes_their_own_roles_or_a_role_they_hold`
- `test_admin.py :: test_the_last_admin_can_never_be_removed`
- `test_admin.py :: test_role_changes_are_audited_with_added_and_removed_permissions`
- `test_admin.py :: test_unknown_permissions_and_duplicate_names_are_refused`
- `test_admin.py :: test_roles_in_use_and_templates_cannot_be_deleted`
- `test_admin.py :: test_a_removed_template_permission_stays_removed_after_upgrades`
- `test_admin.py :: test_reset_password_signs_the_user_out`
- `test_admin.py :: test_company_settings_validate_and_lock`
- `test_admin.py :: test_user_status_counts_filter_the_list`
- `test_auth.py :: test_first_run_setup_creates_the_admin_and_signs_in`
- `test_auth.py :: test_setup_runs_once_even_when_two_arrive_together`
- `test_auth.py :: test_weak_passwords_are_refused`
- `test_auth.py :: test_wrong_password_is_generic_and_audited`
- `test_auth.py :: test_login_is_rate_limited_per_account`
- `test_auth.py :: test_forged_and_missing_sessions_are_rejected`
- `test_auth.py :: test_logout_ends_the_session`
- `test_auth.py :: test_idle_and_expired_sessions_end`
- `test_auth.py :: test_new_user_must_change_the_temporary_password_first`
- `test_auth.py :: test_changing_password_signs_out_other_sessions`
- `test_auth.py :: test_deactivated_user_is_signed_out_and_cannot_sign_in`
- `test_platform.py :: test_the_app_login_cannot_change_or_delete_audit_events`
- `test_platform.py :: test_not_even_the_schema_owner_can_rewrite_audit_events`
- `test_platform.py :: test_audit_log_filters_and_export`
- `test_platform.py :: test_health`
- `test_platform.py :: test_system_status_needs_permission`
- `test_platform.py :: test_security_headers_and_request_id`
- `test_platform.py :: test_writes_need_the_csrf_token`
- `test_platform.py :: test_unknown_hosts_are_refused`
- `test_platform.py :: test_oversized_bodies_are_refused_even_when_chunked`
- `test_platform.py :: test_crashes_answer_calmly_without_internals`
- `test_platform.py :: test_logs_redact_secrets_and_personal_data`
- `test_platform.py :: test_jobs_retry_with_backoff_then_dead_letter`
- `test_platform.py :: test_parallel_workers_never_run_a_job_twice`
- `test_platform.py :: test_database_statements_time_out`
- `test_platform.py :: test_periodic_purge_removes_expired_sessions_and_old_jobs`
- `test_platform.py :: test_no_cross_origin_access_is_granted`
- `test_platform.py :: test_injection_payloads_are_just_text`
- `test_platform.py :: test_roles_list_runs_a_fixed_number_of_queries`
- `test_platform.py :: test_each_sign_in_issues_a_new_session_token`
- `test_security_matrix.py :: test_every_route_but_the_public_ones_needs_sign_in`
- `test_security_matrix.py :: test_a_user_without_permissions_is_refused_everywhere`
- `test_security_matrix.py :: test_every_write_needs_the_csrf_token`
- `test_security_matrix.py :: test_route_list_is_not_empty`

## Independent review (2026-10-28)

A reviewer agent that had not written the code read every file and proved four defects with throwaway
tests. Findings and fixes:

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| H1 | High | `users.edit` alone could reset an admin's password and sign in as them | Guard: can't manage someone holding guarded powers you lack | `test_a_helpdesk_user_cannot_take_over_an_admin` |
| H2 | High | `users.edit` could give the System Administrator role to a new account | Guard: can't grant guarded permissions you don't hold | `test_nobody_hands_out_powers_they_dont_hold` |
| H3 | High | The edit dialog sent an empty role list when roles weren't loaded, removing every role | Roles sent only when the full list is shown | Playwright journey (edit path), code review |
| M1 | Medium | Two admins removing each other at once could both succeed | Advisory transaction lock on user and role changes | `test_two_admins_removing_each_other_at_once_leave_one` (run 3×) |
| M2 | Medium | Version checks without a row lock | `SELECT … FOR UPDATE` on the record | covered by the same lock |
| M3 | Medium | Python lists time zones ("Factory") browsers reject; the app would go blank | Region/City list only, validated; safe date formatting | `test_only_browser_safe_time_zones_are_offered` |
| L1 | Low | `.env` readable by every account | `chmod 600` when written | checked: mode 600 |
| L2 | Low | Trigger's owner check could be fooled by a temporary view | `TG_RELID` + `pg_catalog`, pinned `search_path`; app loses TEMPORARY | `test_audit_trigger_functions_pin_their_search_path`; psql: temp table refused |
| L3 | Low | App login could rewrite `alembic_version` | Migration 0002 revokes it | psql: permission denied |
| L4 | Low | Behind a proxy all clients share one address (rate limit, audit IP) | **Open**: set trusted proxy addresses at deployment (P8) | — |
| L5 | Low | Names of only spaces saved as empty | Strip before length checks | `test_blank_names_are_refused` |
| L6 | Low | Deleting a role assigned a moment earlier gave a 500 | 409 on the foreign-key error | code review |
