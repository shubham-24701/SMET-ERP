# Test results

Every result below was produced by running the command shown. Results that have not run yet say so.

## 2026-11-01 · Phase 8 (0.8.0) · local and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend | ruff, `uv run pytest -q` | clean; **181 passed**, including `test_phase8.py` (backup round trip with real `pg_dump`/`pg_restore`, scanner refusal and failure, S3 against a stand-in client, trusted proxies, retention, system health, SMET's date, production HTTPS) and the security regressions below |
| Frontend | `tsc -b`, oxlint, `vitest run` | clean; 7 passed |
| Browser journeys | `npx playwright test` (local Chromium) | **8 passed**, with the CSRF token now replaced at sign-in |
| Load and concurrency | `uv run python -m tests.load_check` (400 items, 3,000 orders, 20,000 serials, 60,000 stock moves, 3,000 service requests, 30,000 notifications) | report pages 18–117 ms median; dashboards 255 ms median, 422 ms p95; 8 people at once: 160 requests in about 9 s, p95 1.4 s; two saves of the same record at once: [200, 409] |
| Regression check | each security test against the code before its fix | M2, M3, L1 and L4 tests failed before and pass after; M1 was seen failing on the restored test database and passes after |
| CI | GitHub Actions on `phase-8` | see below |

The full security review table is in [security/SECURITY_CHECKLIST_111.md](security/SECURITY_CHECKLIST_111.md#independent-security-review-of-the-whole-app-2026-11-01).
The load check ran in the cloud workspace (shared CPU), not on SMET's hardware: treat the numbers
as a floor to compare against, not a capacity promise.

## 2026-11-01 · Customization (0.7.0) · local and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend | ruff, `uv run pytest -q` | clean; **171 passed** (156 before, 6 customization scenarios, 9 review regressions) |
| Migration 0010 | down to 0009 and up again, on a database holding a shipped role | report permissions given to roles with the area permission; template grants recorded so startup sync doesn't re-add removed ones |
| Browser journeys | `npx playwright test` (local Chromium) | **8 passed**, including `customize.spec.ts` (a required choice field added, a built-in field renamed and another hidden, a service status added with its moves, the form changed for another user, the field filled and shown as a report column) |
| Regression check | the review tests against the code before the fixes | 8 of 9 failed before, all pass after; the ninth (template grants) is checked on the migration above |

### Independent review of the customization work (2026-11-01)

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1–2 | High | A required built-in field stopped dispatch planning and purchase-order creation even when filled (checked before the route had copied it) | Required fields are checked when the change is committed, on its final values | `test_a_required_field_filled_in_the_same_request_is_accepted` |
| 3 | High | An upgrade would give a report back to a shipped role whose area permission an administrator had removed | Migration 0010 records the template grants | migration check above |
| 4 | High | Any technician could change any visit's details | Only technicians on the visit, as for its record | `test_technicians_fill_details_only_on_their_own_visits` |
| 5 | High | Odd numbers (1e999, lists) crashed the save | Refused with a field error; finite numbers only | `test_bad_numbers_and_wrong_types_are_refused_not_crashes` |
| 6 | High | A new service status typed letter by letter got a one-letter key and was refused | The key follows the name until saved; an error if it has no Latin letter | browser journey |
| 7 | Medium | Required sales-order fields were never checked | Checked at conversion too | covered by 1–2 |
| 8 | Medium | A removed choice option made saving erase the value | The stored value is kept and offered | `test_finished_records_keep_their_details_and_removed_options_survive` |
| 9 | Medium | Saving workflows without the roles list wiped "also tell" settings | Left unchanged when the list isn't loaded | code review |
| 10 | Medium | A deleted role blocked every later rule change | Roles checked only when that setting is changed | `test_a_deleted_role_doesn_t_block_other_rule_changes` |
| 11 | Medium | Details of closed, cancelled or approved records could be changed | Locked like the record (approved warranties: `warranty.correct`) | `test_finished_records_keep…` |
| 12 | Medium | Text fields stored objects as text | Text and choice take text only | `test_bad_numbers…` |
| 13 | Medium | Required custom fields looked enforced but were reminders | Said so on screen ("still to fill in") | — |
| 14 | Medium | Editors without view permission got a 403 after saving | The answer no longer needs view | `test_editing_without_view_permission…` |
| 15–21 | Low | Switched-off fields in report data; a custom "Notes" picking up the built-in rule; wrong reason when quotation approval is off; a status called "open"; status keys in customer texts; downgrade leaving `fields.configure`; a double add giving 500 | All fixed | `test_switched_off_fields…`, `test_open_is_not_a_status_name`, code review |

## 2026-11-01 · Phase 7 · local and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend | `uv run ruff check .`, `ruff format --check .`, `uv run pytest -q` | clean; **156 passed** (127 before Phase 7, 17 Phase 7 scenarios, 12 review regressions) |
| Migrations | upgrade to 0009 on the working and test databases | clean |
| Frontend | lint, typecheck, Vitest | clean; 7 passed |
| Browser journeys | `npx playwright test` (local Chromium) | **7 passed**; `phase7.spec.ts`: SMS switched to mock and its connection tested; a message reworded with its field check and preview; a submitted purchase order reaching the approver's bell and inbox and opening the order; a stranger asking for service with text updates; the request moved on and its text recorded (mock), recipient masked; dashboards, a figure opened to its rows, filtered, downloaded as Excel; search. One earlier local full run had the Phase 6 journey time out once; it passed alone and in the next two full runs |
| CI | GitHub Actions run 41287446892 | **all six jobs green** (Ubuntu and Windows backends, frontend, Edge on Windows, secret scan, dependency audit). The run before failed once: the Windows runner reloaded before the worker had built the notice; the journey now waits for it |
| Regression check | the 12 review tests run against the code before the fixes | 12 failed before, 12 pass after |
| External sends | every provider call in tests goes to a recorded fake; a test fails if a provider is called while sends are off | nothing left the machine |

### Independent review of Phase 7 (2026-11-01)

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | High | Excel downloads of stock movements and quality holds failed (time zones) | Times converted to SMET's zone | `test_every_report_downloads_in_both_formats` |
| 2 | High | The stored SMTP password could be sent to another server by changing the host, and a connection test ran with sends off | The credential must be typed again when the server or account changes; no password over plain text; tests only when the server allows | `test_the_stored_password_never_follows_a_new_server` |
| 3 | High | A timeout after sending, an unreadable reply or an SMTP cut-off was retried, so a customer could get a message twice | Marked "unknown", never resent by itself | `test_a_send_that_may_have_arrived_is_never_repeated` |
| 4 | Medium | The public form could make SMET text any number, at SMET's cost | No text on opening from the public form; texts only to numbers in SMET's country (A33) | `test_the_public_form_cant_text_numbers_abroad_or_on_opening` |
| 5 | Medium | Status checks kept calling Twilio after sends were switched off | Skipped unless allowed | `test_status_checks_stop_when_the_server_cap_is_off` |
| 6 | Medium | A download over the limit loaded every row first | Counted first | `test_export_refuses_too_many_rows_before_loading_them` |
| 7 | Medium | CSV turned negative numbers into text | Only text is guarded against formulas | `test_numbers_stay_numbers_and_text_cant_become_a_formula` |
| 8 | Medium | A dashboard link with several statuses showed "All" in the filter | The combined value is shown | browser check |
| 9 | Medium | Saving reminder rules could clear the escalation role (late-loading list) | The select waits for the list; without it the current role is kept | code review |
| 10 | Medium | A rejected sheet or change didn't reach its author | Author and requester added | `test_a_rejected_sheet_reaches_the_person_who_wrote_it` |
| 11 | Medium | Staff copies of customer events had customer wording and no link | Separate "email to customers" wording | `test_staff_email_keeps_the_link_and_customers_get_their_own_wording` |
| 12 | Medium | Report dates were UTC dates; "today" came from two clocks | Company dates and today everywhere in reports and reminders | `test_report_dates_are_smet_dates` |
| 13 | Low | Reminder periods follow the calendar, not when each item became late | Recorded (A31); harmless repeat at most once a period | — |
| 14 | Low | Two first saves of a channel or wording at once gave a 500 | Answered as "changed by someone else" | code review |
| 15 | Low | An opt-out after queueing didn't stop the send | Re-checked at send time | `test_wording_only_for_real_channels_and_opt_outs_checked_at_send` |
| 16 | Low | Duplicate row keys in two reports | Row index | code review |
| 17 | Low | The sandbox test number was kept in full in the audit log | Masked | `test_the_sandbox_recipient_is_masked_in_the_audit_log` |
| 18 | Low | Wording could be saved for a channel that doesn't exist | Refused | same as 15 |

The reviewer confirmed the audit listener can't break or roll back the action it follows: it only
adds a job row in the same transaction; the notifications are built by the worker after the commit.

## 2026-10-30 · Phase 6 · local and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend | `uv run ruff check .`, `ruff format --check .`, `uv run pytest -q -W error::DeprecationWarning` | clean; **127 passed** (109 before Phase 6, 9 Phase 6 scenarios, 9 review regressions) |
| Migrations | downgrade to 0007, upgrade, `alembic check` | clean; 0008 Phase 6 |
| Privileges | `has_table_privilege('smet_app', …)` | no DELETE on warranty, contract, service and alert tables |
| Frontend | lint, typecheck, Vitest, build | clean; 7 passed |
| Browser journeys | `npx playwright test` (local Chromium) | **6 passed**; `phase6.spec.ts` uses three browsers: the warranty policy entered on screen, the warranty approved by that policy at handover, an AMC with three planned visits, a stranger checking a battery and asking for service on the public page, the request run through a service visit, a maintenance alert verified, its code recorded, shown again with a reason, and closed, and the request closed; phone width without sideways scroll |
| Regression check | the 9 review tests run against the code before the fixes | 9 failed before, 9 pass after |

The brief's scenarios are covered by `test_aftersales.py`: 3 (registration, approval, refused
correction audited), 4 (AMC, reminders, service request, reset code), 5 (replacement linked and
warranty carried), 6 (refused attempts leak nothing; the security matrix covers every route).

### Independent review of Phase 6 (2026-10-30)

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | High | Correcting a replacement's contact details reset its term to a full new one | A replacement's dates stay as the replacement rule set them | `test_correcting_a_replacement_keeps_its_term` |
| 2 | High | A replacement for a battery whose warranty had expired got an approved warranty ending before it started | Only a warranty in force is carried over | `test_an_expired_warranty_carries_nothing_to_its_replacement` |
| 3 | High | The public form told real, sold, unregistered serials apart through file errors | The file is judged before the serial is looked up | `test_the_public_form_answers_alike_even_with_a_bad_file` |
| 4 | Medium | The battery timeline showed service text, alert recipients and warranty state to anyone with `stock.view` | Each group only for its own permission | `test_the_timeline_shows_after_sales_only_to_those_allowed` |
| 5 | Medium | Cancelling a renewal draft left the original contract "renewed" for good | The original becomes active again | `test_cancelling_a_renewal_gives_the_contract_back` |
| 6 | Medium | A request could close with its battery still at the workshop, or as not replaced after a replacement was sent | Both refused | `test_a_request_doesnt_close_with_its_battery_at_the_workshop` |
| 7 | Medium | Under a handover-start policy, staff and public registrations could never be approved (no handover date field) | Handover date on the registration form | `test_staff_can_register_under_a_handover_policy` |
| 8 | Medium | Two people sending the same replacement at once could both succeed (a stale copy of the battery was checked) | Locked serials are always read fresh (shared helper, so dispatch and assembly benefit too) | code review |
| 9 | Medium | A battery at the workshop still showed as under contract; a contract listing batteries didn't follow a replacement | Only batteries at the customer are covered; the replacement joins the list | `test_a_battery_in_the_workshop_is_not_under_contract` |
| 10 | Low | A replacement that once had its own warranty blocked the close | Its earlier registration ends | `test_a_refurbished_replacement_with_an_old_warranty_still_closes` |
| 11 | Low | Files could be added to a closed request; an alert could be registered while its request closed | Refused; the request is locked | code review |
| 12 | Low | Two public registrations of one serial at once gave a 500 | The second gets the same answer | code review |

Recorded, not changed: dates use the server's clock (set the server to Asia/Dubai when deploying; P8),
and behind a reverse proxy the public limits count the proxy's address (P8, checkpoint 4).

## 2026-10-30 · Phase 5 · local and CI

| Check | Command | Result |
| --- | --- | --- |
| Backend | `uv run ruff check .`, `ruff format --check .`, `uv run pytest -q -W error::DeprecationWarning` | clean; **109 passed** (100 before Phase 5, 5 Phase 5, 4 review regressions) |
| Migrations | downgrade to 0004, upgrade, `alembic check` | clean; 0007 Phase 5 |
| Privileges | `has_table_privilege('smet_app', …)` | no DELETE on `bank_positions`, `site_visits`, `battery_banks`, `files`; no UPDATE on `files` |
| Frontend | lint, typecheck, Vitest, build | clean; 7 passed; JS 572 kB (149 kB gzipped) |
| Dependencies | `pip-audit` (runtime), `npm audit --omit=dev` | no known vulnerabilities |
| Browser journeys | `npx playwright test` (local Chromium) | **5 passed**; `phase5.spec.ts`: coordinator and technician in two browsers: bank planned, visit scheduled, batteries placed, completion refused with the reason and then completed with checklist and photo, commissioning with a reading outside the confirmed limits flagged, lifecycle on the battery, label printed, public check in a third browser without the customer's name or site, find by scanned label, visit at phone width without sideways scroll |

### Independent review of Phase 5 (2026-10-30)

| # | Severity | Finding | Fix | Regression test |
| --- | --- | --- | --- | --- |
| 1 | High | A technician could move a battery from a bank on their visit into any bank of the same customer | The destination must be on the visit too | `test_a_technician_moves_batteries_only_into_banks_their_visit_covers` |
| 2 | High | A battery added to, or moved into, an installed or handed-over bank took that stage without any check | Adding only to planned banks; moves only between banks at the same stage; a replacement into a live bank is recorded on a visit | `test_no_battery_skips_a_stage` |
| 3 | Medium | Saving the visit replaced its issues: open issues could be deleted, or closed without a record of who and when | Issues can only be added or reworded; closing is its own action | `test_saving_a_visit_never_drops_or_quietly_closes_an_issue` |
| 4 | Medium | After a refused completion the form kept an old version, so every later save failed as "changed by someone else" | The page reloads after the save, whether or not completion is accepted | `phase5.spec.ts` (completion refused, then completed) |
| 5 | Low | The public check called an installed but uncommissioned battery "in service", and a removed one "not yet installed" | Separate wording for installed and removed | `test_the_public_check_tells_installed…` |
| 6 | Low | Two opposite moves at once could deadlock; two renames to the same name at once could give a 500 | Banks locked in id order; the site locked for renames | code review |
| 7 | Low | Technicians could not replace, move or remove batteries from the visit page | Actions on each position of the visit's banks | `phase5.spec.ts` (screen), code review |
| 8 | Low | Behind a reverse proxy, every public check would share the proxy's address for the rate limit | Recorded for deployment (P8, checkpoint 4) | — |
| 9 | Low | Some stock moves appeared on the timeline as raw words ("Accept") | Named | code review |
| 10 | Medium (found by CI on Windows) | After Save, clicking Save and complete before the page had reloaded sent the old version and was refused as a conflict | The form remembers the version its own save returned | `phase5.spec.ts` slows the reload by 1.5 s; failed before the fix, passes after |

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
