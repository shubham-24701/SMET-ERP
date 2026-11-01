# Implementation status

**Updated:** 2026-10-30 · **Version:** 0.5.0 · **Current phase:** 6 (warranty and after-sales)
built and verified locally and in CI, on top of Phase 5 (pull request #3, stacked on #2 and #1). Phase 7
(notifications, dashboards, reports, exports) next on the owner's go-ahead.

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
| 1 | Foundation: sign-in, sessions, CSRF, configurable roles/permissions (17 role templates), users, append-only audit log, company settings, job queue, health, CI, design system | **Verified (local + CI)** |
| 2 | Master data (models, items, units, parties, warehouses, numbering, business rules), suppliers & procurement (requests, quotations and comparison, POs with approval, receipts & inspection, returns), inventory (ledger, reservations, transfers, counts, corrections, reconciliation, serials) | **Verified (local + CI)** |
| 3 | Assembly & quality: BOM versions, assembly orders, reserve/issue/consume/return, serial assignment with component links, configurable checklists and test parameters, pass/hold/rework, finished-stock receipt only through a passing inspection | **Verified (local + CI)** |
| 4 | Sales & projects: enquiries, quotations with revisions and approval, sales orders, projects/sites/surveys, requirement sheets with engineering approval and SMET-supplied sizing rules, procurement & assembly links, dispatch and delivery | **Verified (local + CI)** |
| 5 | Installation & traceability: visits, commissioning, handover, QR labels, battery banks, lifecycle timeline | **Verified (local + CI)** |
| 6 | Warranty & after-sales: registration/activation, public verification portal, AMC, service requests, replacements, maintenance alerts and reset codes (manual process until a supplier API is confirmed) | **Verified (local + CI)** |
| 7 | Notifications (in-app; email/SMS/WhatsApp adapters off until approved), dashboards, reports, exports, file attachments | Planned |
| 8 | Hardening: 111-checkpoint evidence, performance and concurrency tests, backup/restore drill, deployment package, incident process | Planned |
| 9 | End-to-end verification, release readiness, activation checklist | Planned |

## Phase 8: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| 111 checkpoints | Every checkpoint re-assessed with Phase 8 evidence; hosting-dependent ones stay Pending | `docs/engineering/ENGINEERING_CHECKLIST_111.md`, `docs/security/SECURITY_CHECKLIST_111.md` |
| Backup and restore | Dump and files zip with checksums; verify; restore (database replaced, files unzipped safely); tested end to end | `test_phase8.py::test_a_backup_restores_the_data_and_the_files` |
| Files | Virus-scan hook (fail closed), S3-compatible storage adapter, scan result recorded, Unicode-safe download names | `::test_uploads_pass_the_virus_scanner…`, `::test_files_can_live_in_an_s3_bucket` |
| Proxy | Client address from X-Forwarded-For only through TRUSTED_PROXIES; used by limits and the audit log | `::test_the_client_address_is_believed…` |
| Dates | Every "today" in the app is the company's date | `::test_dates_are_smet_s_dates…` |
| Retention | Read notifications and old message texts purged daily per the rules | `::test_retention_keeps_records…` |
| Integration health | Storage, scanner, channels, supplier API, backups, server clock on System health | `::test_system_health_reports_each_outside_dependency` |
| Performance | 20k serials, 60k movements, 3k orders, 3k requests, 30k notifications: report pages 18–117 ms p50; dashboards 255 ms; 8 users at once p95 1.4 s in one test process; concurrent saves resolved [200, 409] | `tests/load_check.py`, TEST_RESULTS.md |
| Runbook | Daily backup, monthly restore drill, restore after a loss, incident steps, on-call table, post-mortem template | `docs/operations/RUNBOOK.md` |

## Customization (0.7.0): what was built

| Area | What works | Evidence |
| --- | --- | --- |
| Report permissions | One permission per report; dashboards, rows and downloads check it; roles that saw the area got it once (migration 0010, templates) | `test_customization.py::test_a_report_needs_its_own_permission…`, `::test_every_report_has_its_own_permission` |
| Custom fields | Added per kind of record (14 kinds) in Administration → Fields; typed and checked values; required; switched off without losing values; shown on the record, edited with its permission; extra report and download columns | `::test_custom_fields_are_defined_filled_checked_and_reported`, `customize.spec.ts` |
| Built-in fields | Optional fields renamed, hidden (value kept on save) or required (checked by the server on every route; records the system opens by itself are completed later) | `::test_built_in_fields_can_be_renamed_hidden_or_required`, `customize.spec.ts` |
| Workflows | Purchase order and quotation approval on or off; extra roles told about each event; service-request statuses and moves edited, with checks against stranded or in-use statuses | `::test_approval_switches_and_extra_roles_told`, `::test_service_statuses_and_moves_are_set_on_screen` |
| Kept fixed on purpose | Stock, receipt inspection, assembly, warranty and reset-code status flows: the stock ledger, the inspection gate and warranty rules depend on them | design decision |

## Phase 7: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| Events | Approval requests and decisions (purchase orders, stock corrections, quotations, requirement sheets, project changes, warranty registrations); service request status; warranty decisions and missing information; dispatch and handover progress; hourly: items below minimum, late supplier deliveries and assembly orders, maintenance visits coming up and overdue (planned or due by contract), contracts ending | `test_notifications.py`, `test_review_p7.py::test_a_rejected_sheet…` |
| Recipients | Holders of the permission that acts, or the people assigned; never the person who did it; each person once per event; muted kinds respected; staff email only if they ask | `::test_an_approval_request_reaches_the_approvers…`, `::test_muted_events_stay_quiet` |
| Reminders | Repeat every `reminder_repeat_days` while late; escalation to a chosen role after `escalate_after_days` (off until set) | `::test_reminders_repeat_on_schedule_and_escalate` |
| Safety of the action | The notice is a job added in the action's transaction and built after the commit: a failure can't undo or slow the action; a rolled-back action notifies nobody | independent review |
| Channels | Email (SMTP), SMS (Twilio), WhatsApp (Meta templates) behind one interface; off / mock / sandbox (test recipient only) / production; the server's `EXTERNAL_SENDS` (default off) caps them all; credentials encrypted (SECRETS_KEY), never shown or audited, re-entered if the server changes; connection test without sending | `::test_nothing_leaves_the_server_until_switched_on_and_allowed`, `::test_sandbox_sends_only_to_the_test_recipient`, `test_review_p7.py::test_the_stored_password…` |
| Delivery | Queued with a unique key; retried with growing delay only when the provider was unreachable; a send that may have arrived is "unknown", never repeated; Twilio delivery status followed up; log with masked recipients; send again (with a warning for "unknown") | `::test_a_message_is_never_sent_twice`, `test_review_p7.py::test_a_send_that_may_have_arrived…` |
| Consent and opt-out | Customers only about their own request or registration, only with consent from the form or staff, only to numbers in SMET's country; opt-outs checked when queued and when sent | `::test_no_consent_or_an_opt_out_means_no_message`, `test_review_p7.py::test_the_public_form…` |
| Wording | Every message editable per channel; only the event's fields accepted; live preview with sample values marked as samples; SMS length and parts | `::test_wording_is_checked_before_it_is_saved` |
| Dashboards | Procurement, inventory, assembly, sales, installation, warranty, maintenance and service, traceability, exceptions; every figure is the row count of a report and opens those rows; only figures the person may see | `test_reports.py::test_dashboards_show_only…` |
| Reports | 18 reports; filters by date (SMET's dates), product, serial, customer, distributor, supplier, warehouse, project, status and report options; filters a report doesn't offer are refused | `test_reports.py::test_every_report_runs…`, `test_review_p7.py::test_report_dates_are_smet_dates` |
| Downloads | CSV and Excel with `reports.export`, up to 10,000 rows, formula-safe, audited with the filters; refusals audited | `test_reports.py::test_downloads_need…`, `test_review_p7.py::test_every_report_downloads…` |
| Search | Batteries, customers and suppliers, projects, service requests, warranty registrations, purchase and sales orders, quotations; each kind only with its view permission | `test_reports.py::test_search_finds_only…` |
| Independent review | 3 high (Excel downloads failing, the stored password could be sent to another server, a message could be sent twice), 9 medium, 6 low; all fixed except one low recorded; 12 regression tests that fail without the fixes | `test_review_p7.py`, TEST_RESULTS.md |
| Screens | Bell and inbox with settings; Messages (channels, wording, sent messages, opt-outs); Reports and report pages with filters and downloads; search in the menu; consent boxes on the public and staff forms; reminder rules | `phase7.spec.ts` |

## Phase 6: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| Warranty policies | Per battery model, entered by SMET: months, the event that starts it, automatic or manual approval, proof needed, replacement rule, in force from a date; terms in use can't change; none shipped | `test_aftersales.py::test_a_distributor_sale_is_checked…` |
| Registration | Opened at handover for every battery of the bank; by staff for distributor and direct sales; on the public form. Serial, sale status, customer, dates and proof checked against the policy in force; one open registration per battery; start and expiry calculated; pending, correction required, approved, rejected, ended | `::test_smet_installation_activates_by_policy…`, `::test_a_distributor_sale_is_checked…` |
| Decisions | Automatic only where the policy allows and nothing is missing; otherwise someone other than the submitter; reasons kept | same |
| Corrections | Approved warranties corrected only with `warranty.correct` and a reason; dates worked out again; every change in the audit log; refused attempts audited | same |
| Distributor dispatch | Never starts a warranty | `::test_smet_installation_activates_by_policy_at_handover_and_never_at_dispatch` |
| Public portal | `/verify` and the QR label: product, in service or not, warranty state (dates if allowed), the note that a record is not proof of authenticity; register or ask for service, answered the same either way; rate-limited; a hashed address logged when a limit is hit | `::test_the_public_forms_land_as_pending_work…`, `phase6.spec.ts` |
| AMC | Customer, site, banks or chosen batteries; covered and excluded services; planned maintenance visits; reminders for upcoming, overdue and ending contracts; renewal keeps the old contract and its visits; cancellation cancels future visits | `::test_amc_plans_visits_and_its_coverage_is_never_inferred_from_warranty` |
| Coverage | Warranty and AMC checked separately and shown side by side; neither inferred from the other | same |
| Service requests | New → under review → scheduled → in progress → pending action → closed by allowed moves only, each with who, when and why; coordinator, priority, diagnosis, work done; service visits recorded by the technicians; workshop receipt and return, parts and replacements as stock movements with operation keys; closing needs no open visit or alert | `::test_a_service_request_moves_by_the_rules…` |
| Replacement | Linked to the original in the bank and on the request; its warranty follows the original's policy (rest of term, new term or longer); the original's ends; histories kept | `::test_a_replacement_is_linked_to_the_original_and_takes_its_warranty` |
| Reset codes | Alert → verification of serial, customer and location → completed inspection visit → code typed in by `codes.reveal`, encrypted, recorded once; reveal needs a reason and is audited; never in lists, the audit log or the application log; erased after the retention period; cleared or not recorded as seen on site | `::test_a_service_request_moves_by_the_rules_and_a_reset_code_stays_secret` |
| Lifecycle | Warranty decisions, service requests and alerts on each battery's timeline; current warranty and AMC on the battery page | `::test_a_replacement_is_linked…` |
| Independent review | 3 high (a corrected replacement got a new term; an expired original passed on a backwards warranty; the public form revealed registrable serials), 6 medium, 3 low; all fixed, 9 with regression tests that fail without the fix | `test_review_p6.py`, TEST_RESULTS.md |
| Screens | Service requests, maintenance contracts with reminders, warranty registrations and policies, maintenance alerts, the public page; after-sales rules in Rules & numbering; dashboard lines | `phase6.spec.ts` |

## Phase 5: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| Battery banks | A bank per system at a site, with configuration; batteries delivered to that site placed in named positions; remove, replace and move with a reason; every placement kept with who, when, why and the battery replaced. One battery in one place, one battery per position (database indexes) | `test_installation.py::test_bank_changes_keep_every_battery_s_history` |
| Stages | planned → installed → commissioned → handed over, each only by completing a visit of that stage, each with its own date, never earlier than the one before; a battery takes its bank's stage (delivered, installed, commissioned, in service, removed); no battery joins a live bank except as a replacement recorded on a visit | `::test_a_bank_goes_from_delivery_to_handover_one_stage_at_a_time`, `test_review_p5.py::test_no_battery_skips_a_stage` |
| Visits | Scheduling with technicians, time, site readiness, access requirements and contacts; reschedule, reassign, cancel with a reason; technicians record only their own visits | same, `test_security_matrix.py` |
| Visit record | Serials received at the site (only ones delivered there), SMET's checklist, readings with the limits that applied, observations, issues and corrective actions, the customer's acknowledgement; frozen once completed. Issues can't be deleted or quietly closed | same, `test_review_p5.py::test_saving_a_visit_never_drops…` |
| Completion | Refused, with the list of what is missing, until: every required check done, required readings taken and inside limits SMET confirmed, photographs per the evidence policy, every installed battery's receipt confirmed, at handover the customer's name and date and no open issue (configurable) | `::test_a_bank_goes_from_delivery_to_handover…` |
| Site checks | SMET's checklist lines and readings per stage; limits apply only once marked confirmed (none are built in) | same |
| Evidence | Photographs, test reports and signed acknowledgements; type read from the content (JPEG, PNG, WebP, PDF), 10 MB limit, stored outside the code folder under a random name with SHA-256; downloads need installation.view | `::test_evidence_files_are_checked_by_content_and_kept_safely` |
| Milestones | When every bank of a project has reached a stage, its milestone of that kind is marked done | `::test_a_bank_goes_from_delivery_to_handover…` |
| QR labels | Printed from the browser on any printer (90 × 40 mm), QR made locally (segno, no paid service); the code is a random token, not an id | `::test_qr_labels_lead_to_a_public_check…` |
| Public check | `/v/<token>`: serial, product and whether installed, in service, removed or withdrawn; no customer, site or internal id; 30 checks a minute per address | same, `test_review_p5.py::test_the_public_check_tells_installed…` |
| Traceability | Find by scanned label, serial or supplier serial (handheld scanner or phone camera where the browser can read QR codes); one timeline per battery from receipt through inspection, assembly, dispatch, delivery, bank placements and stages, read from the records that own each event; where it is now | `::test_bank_changes_keep_every_battery_s_history`, `phase5.spec.ts` |
| Independent review | 2 high (a technician could move a battery into a bank outside their visit; a battery could join a live bank and skip installation), 2 medium, 5 low; all fixed with regression tests except the proxy address for rate limits (deployment, P8) | `test_review_p5.py`, TEST_RESULTS.md |
| Screens | Site visits (to do, mine), the visit record for technicians at phone width, battery banks, site checks, find a battery, labels, the public check; installation panel on the project; lifecycle on the battery; evidence rules in Rules & numbering | `phase5.spec.ts` |

## Phase 4: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| Enquiries | Customer, products-only or project supply, items or free text, quantities, needed-by date; a quotation draft in one click | `test_sales.py::test_a_quotation_goes_from_enquiry_to_exactly_one_order` |
| Quotations | Separate lines for batteries, accessories, installation, commissioning, AMC and other; customer pricing and discounts per line; scope notes, terms, installation / commissioning / handover dates. A draft can be edited; from submission a revision never changes; changes are new revisions, earlier ones kept as "revised". Statuses draft, waiting for approval, approved, rejected, accepted, declined, revised, and expired (past validity, computed) | same, `::test_revisions_are_kept_and_conversion_happens_once`, `::test_expired_quotations_cannot_be_accepted` |
| Approval | Every quotation needs an approver unless SMET sets an automatic limit (total in company currency and maximum discount); nobody who edited or submitted a revision approves it | `::test_small_quotations_without_discount_can_approve_themselves` |
| Customer answer and conversion | Who accepted, their reference, the date; an accepted revision becomes exactly one sales order (unique in the database; three people converting at once: one order) | `::test_revisions_are_kept_and_conversion_happens_once` |
| Sales orders | Lines with sent, delivered, still to send, reserved, free and short; reserve free stock; a purchase request for what bought goods are short of; a planned assembly order for an assembled battery that is short; close or cancel (reservations released) | `::test_what_an_order_is_short_of_becomes_a_purchase_request` |
| Dispatch | Plan (nothing moves) → send (only available stock: nothing on hold, uninspected or under assembly; exact serials; the order's then the project's reservations used first; never more than is left to send, counting other planned dispatches) → delivery acknowledgement (received by, date; refused goods back to the customer-returns area and owed again; damaged recorded) → or cancel (a sent dispatch's goods and reservations come back; the record stays). Printable delivery note | `::test_dispatch_takes_reserved_available_serials_and_delivery_records_refusals`, `::test_cancelling_a_sent_dispatch_puts_stock_and_reservations_back` |
| Projects | Customer, scope, contacts, manager, dates, status; sites with address, contact and survey (date, by whom, findings, technical requirements); milestones (material ready, delivery, installation, commissioning, handover); tasks with assignees; progress notes and pending actions; scope, quantity and schedule changes decided by someone else | `test_projects.py::test_changes_need_someone_else_and_progress_is_kept` |
| Sizing rules | No formula is built in. SMET enters rules (named inputs, an arithmetic formula, a unit) and marks them confirmed; the evaluator accepts only numbers, inputs, + − × ÷, brackets and ceil/floor/round/max/min | `::test_formulas_are_arithmetic_only` |
| Requirement sheets | Per project or site; quantities typed in or worked out by a rule, with inputs and result kept on the line (and marked if the typed quantity differs); engineering review by someone who didn't prepare it; approved sheets never change; a new approval supersedes the old | `::test_a_project_runs_from_survey_to_approved_requirements` |
| Material list | Approved requirements against stock: reserve, request purchase of what is short, plan assembly, prepare the project quotation; linked documents listed on the project | same |
| Independent review | 3 high (reservations revived by a cancellation; project reservations counted twice), 5 medium and 6 low findings; all high and medium fixed with regression tests (migration 0006) | `test_review_p4.py` |
| Screens | Enquiries, quotations (revisions, approval, answer, conversion), sales orders, dispatch (to send, plan with serial picker, send, delivery, cancel, delivery note), projects (sites, sheets with rule calculator, material list, milestones, tasks, changes, notes), sizing rules | Playwright `phase4.spec.ts` (two people in two browsers; phone width checked) |

## Phases 2–3: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| Master data | Items of five kinds (battery models with capacity, voltage, chemistry and free specifications; purchased battery units; components; accessories; materials), units of measure, tracking by quantity, lot or serial, minimum stock. Suppliers, customers, distributors and dealers in one register with contacts, terms and supplied items. Warehouses created with one area per stock status; racks added as locations. Fields history depends on (unit, tracking, kind; a location's use while it holds stock) lock once used | `test_purchasing.py::test_items_keep_what_history_depends_on`, `::test_new_warehouses_get_every_area_the_workflows_need` |
| Rules & numbering | Purchase-order auto-approval limit (off by default), self-approval switches (off), document prefixes and digits per type; every change audited | `::test_document_numbers_follow_on_and_can_be_reformatted`, `::test_small_orders_in_the_company_currency_can_approve_themselves` |
| Purchasing | Purchase requests (from shortages, assembly, projects), supplier quotations compared side by side (lowest price per currency and earliest delivery marked, no invented exchange rates), order from the chosen quotation, purchase orders drafted → submitted → approved/rejected (no self-approval), cancel, close short | `::test_the_whole_purchase_journey_keeps_quantities_apart` |
| Receiving | Deliveries recorded against approved orders only, never more than outstanding; everything lands in receiving inspection unless the person may decide on quality; decisions accept / reject / hold, and release from hold; serial units registered once with the supplier's serial kept; returns of rejected goods, with replacement tracked as outstanding | same test, `::test_received_serials_are_registered_once`, `::test_goods_are_received_only_against_approved_orders` |
| Stock ledger | Every change is one append-only movement plus a balance that can't go below zero (database CHECK); a repeated submit is recorded once (operation keys); reconciliation report compares balances with the ledger and serials with balances | `test_stock.py::test_stock_never_goes_negative`, `::test_the_ledger_cannot_be_rewritten_by_the_app`, `reconciled()` after every flow |
| Availability and reservations | Available = in available-stock locations; free = available − active reservations; reservations against projects, sales orders or assembly orders; four people reserving the last unit at once: one succeeds | `::test_reservations_hold_stock_and_the_last_units_go_to_one_person` |
| Transfers | Between locations of the same use only, so no status (accepted, released, passed) can be skipped by moving stock | `::test_transfers_never_change_a_status` |
| Corrections and counts | Corrections need a reason and a second person; reserved stock can't be corrected away; counts list the books, counters enter quantities or scanned serials, differences become one correction worked out against the stock at approval | `::test_corrections_need_a_second_person_and_a_reason`, `::test_a_count_corrects_against_the_stock_at_approval` |
| Bills of materials | Versions per assembled model; one active at a time; an active version never changes (a change is a new draft) | `test_assembly.py::test_an_active_bom_never_changes` |
| Assembly orders | Plan with responsible person and target date; release reserves every component or lists the shortage, which becomes a purchase request in one click; issue to the assembly floor (reservation used first); record each battery with its serial (SMET numbering or captured), actual consumption and links to every component serial or lot; return unused material; complete or cancel with checks | `::test_release_reserves_components_or_lists_what_is_short`, `::test_from_components_to_a_passed_battery`, `::test_only_issued_material_can_be_consumed` |
| Quality gate | Test parameters and checklists per model; limits apply only once SMET confirms them; a battery passes only with every required check done, every required reading taken and readings within confirmed limits; hold → quarantine, rework → back to the floor and inspected again; only a pass puts a built battery into available stock; inspections keep the limits that applied | `::test_from_components_to_a_passed_battery`, `::test_unconfirmed_limits_are_recorded_not_applied` |
| Independent review | A separate reviewer proved 11 defects, including two ways a battery could reach available stock without inspection (bought from a supplier; found in a count). All fixed, each with a regression test; migration 0004 | `test_review_p23.py` |
| Traceability | Serial record with purchase order, components, what it was built into, inspections and every movement | `GET /serials/{id}`, Playwright journey |
| Screens | Purchase requests and comparison, purchase orders with quantity columns, delivery and decision dialogs, stock (with below-minimum filter and one-click purchase request), movements with CSV, serial numbers and records, reservations, counts and corrections, assembly orders, final inspection, bills of materials, items with inspection set-up, suppliers & customers, warehouses, rules & numbering, "Waiting for you" on the overview | Playwright `phase23.spec.ts` (two people: one raises, the other approves; phone width checked) |

## Phase 1: what was built

| Area | What works | Evidence |
| --- | --- | --- |
| First-run setup | Company profile + first System Administrator; runs once even when two requests race | `test_auth.py::test_setup_runs_once_even_when_two_arrive_together` |
| Sign-in and sessions | Argon2 passwords (12+ chars), opaque server sessions (only SHA-256 stored), new token each sign-in, 10 h limit, 60 min idle limit, logout, generic errors, rate limits (5/min per account, 30/min per client), failed sign-ins audited | `test_auth.py`, `test_platform.py::test_each_sign_in_issues_a_new_session_token` |
| Temporary passwords | Admin-created users and resets get a one-time password shown once; the user must replace it before anything else; changing a password signs out every other session | `test_new_user_must_change_the_temporary_password_first`, Playwright journey |
| Roles & permissions | Permission catalogue (`module.action`), 17 role templates from the proposal, custom roles, several roles per person, read fresh on every request; template permissions added by later versions never re-add what an admin removed | `test_admin.py` |
| Escalation guards | Nobody changes their own roles or access, nor edits a role they hold; the last person able to manage users and roles can't be removed; portal roles can't go to staff; the System Administrator holds no business permissions | `test_nobody_changes_their_own_roles_or_a_role_they_hold`, `test_the_last_admin_can_never_be_removed`, `test_admin_holds_no_business_permissions` |
| Independent review | A separate reviewer found 3 high, 3 medium and 6 low defects (escalation through `users.edit`, roles lost on save, concurrent admin removal, unlocked version checks, a browser-breaking time zone, file and grant hardening). All fixed except proxy client addresses (deployment, P8); each has a regression test | `test_admin.py` review regressions, migration 0002 |
| Server-side enforcement | Every route checked from the API's own route list: sign-in required, permission required, CSRF required on every write | `test_security_matrix.py` (mutation check recorded in TEST_RESULTS.md) |
| Audit log | Who, when, what changed (field diffs; permissions added/removed), why, IP, request id. Append-only: the app's login has no UPDATE/DELETE/TRUNCATE on it, and a trigger refuses them even for the schema owner. Filters, CSV export (formula-safe, audited) | `test_platform.py::test_the_app_login_cannot_change_or_delete_audit_events`, `::test_not_even_the_schema_owner_can_rewrite_audit_events` |
| Optimistic locking | `version` on users, roles, company; a stale save gets 409 | `test_role_changes_are_audited...`, `test_company_settings_validate_and_lock` |
| Company settings | Name, IANA time zone (Asia/Dubai default), currency (AED default), live preview | `test_company_settings_validate_and_lock` |
| Job queue | PostgreSQL `FOR UPDATE SKIP LOCKED`, retries with exponential backoff, dead letters kept, idempotency keys, hourly purge | `test_jobs_retry_with_backoff_then_dead_letter`, `test_parallel_workers_never_run_a_job_twice` |
| Platform | Liveness/readiness (database + migration version), host allow-list, chunked body limit, security headers, calm 500s without internals, JSON logs with redaction, 30 s statement timeout | `test_platform.py` |
| Database setup | `python -m app.setup_db`: two databases, least-privilege logins, `.env` written | used by CI on Linux and Windows |
| Web app | Sign-in, setup, forced password change, overview (team setup, roles in use, recent changes), users (count-card filters, add/edit/deactivate/reset), roles editor, audit log, company settings, system health; light/dark/system theme; phone layout; permission-aware navigation | Vitest (5), Playwright (2 journeys), design inspector at 1440/768/390 |

## Verification results (2026-11-01, end of Phase 7)

See [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) for the commands and outputs.

- Backend: 156 pytest tests passed (Phases 1–6: 127; Phase 7: 29 including review regressions); ruff clean.
- Frontend: oxlint and TypeScript clean, 7 Vitest tests.
- Browser: 7 Playwright journeys passed locally (Chromium) and in CI (Edge on Windows).
- CI run 41287446892: every job green.
- External messages: **mock-tested only**. Email, SMS and WhatsApp adapters are checked against recorded requests and a fake SMTP server; none has been run against a real or sandbox account, and nothing can leave the server while `EXTERNAL_SENDS` is off (the default).
- Independent reviews of Phases 2–3, 4, 5, 6 and 7: every finding fixed or recorded with its reason (TEST_RESULTS.md).

## Known limitations (Phase 7)

- No provider is chosen or connected (questions 24, 39); WhatsApp delivery reports need a public webhook (Phase 8).
- Customer texts go only to numbers in SMET's country (A33); customers abroad can be emailed.
- Reminder periods follow the calendar, so the first repeat can come sooner than the period (A31).
- Dashboards count on demand (about 45 small queries); fine for SMET's size, cached later if needed.
- The object-storage adapter and malware-scan hook for files (§26) move to Phase 8 with hosting.

## Known limitations (Phase 6)

- Notifications arrived in Phase 7 (above).
- Distributors and customers have no login of their own; the public form and SMET staff cover registration and requests (question 36).
- Reset codes are recorded by hand: no supplier API is known (question 22).
- Service requests have no response-time targets (question 35).
- Dates use the server's clock: the server must run on Asia/Dubai time (P8 deployment note).

## Known limitations (Phase 5)

- Warranty activation at handover, distributor-installed batteries and their registration come with warranties in Phase 6; handover dates are recorded ready for it.
- A battery taken out of a bank stays at the site as "removed" until a service return or replacement flow (Phase 6) brings it back to stock.
- Labels point to `PUBLIC_BASE_URL`; until SMET has a public address (question 34) they only work on this computer.
- The public check's rate limit counts by client address; behind a reverse proxy it must read the proxy's forwarded address first (P8, checkpoint 4).
- Reading QR codes with the camera needs a browser that supports it (Chrome and Edge on Android, Safari); elsewhere a phone's camera app or a handheld scanner opens the label.
- Visit lists inside a project and a bank's history (last 200 placements) are not paginated; sizes per project stay small (P8).

## Known limitations (Phase 4)

- Sales orders come only from accepted quotations; a direct order (for example a distributor's repeat order) needs a quotation first.
- Installation, commissioning and AMC lines are sold and listed; their work is recorded in Phases 5–6.
- Supporting documents for sales and projects wait for file storage (Phase 7); site photographs are stored from Phase 5.
- No sizing rule is shipped: quantities are typed in until SMET supplies its engineering rules (question 15).

## Known limitations (Phases 2–3)

- Supporting documents and photos (quotations, delivery notes, test attachments) need file storage: Phase 7.
- No import of existing items, suppliers or opening stock yet: waiting for SMET's sample files (question 27).
- Rework keeps the battery's components; swapping a component during rework is not built.
- Purchase prices are reference only; there is no stock valuation until SMET answers question 7.
- Sales orders and projects arrive in Phase 4; until then a reservation names what it is for in words.

## Known limitations (Phase 1)

- Rate limits are in memory, per API process (fine for one process; noted in code).
- No email yet, so password resets are handed over by the administrator (one-time password).
- Multi-factor authentication: not built; the session layer is the extension point (Phase 8 decision).
- Portal users (distributors, customers) arrive with the portals (Phases 4–6); their role templates exist.
- Dark mode is styled but was not photographed by the inspector.

## Blockers

None. The defaults used until SMET answers are listed in
[docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md](docs/ASSUMPTIONS_AND_OPEN_QUESTIONS.md) (A8–A15); they must be
confirmed before real use.
