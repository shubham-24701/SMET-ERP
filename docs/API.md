# API

REST under `/api/v1`, JSON in and out. The full, always-current reference is the OpenAPI document:
start the API with `API_DOCS=true` and open `/api/v1/docs` (off by default: Swagger UI loads scripts
from a CDN).

## Conventions

- **Authentication**: the `smet_session` cookie from `POST /auth/login` (or `/auth/setup` on first
  run). No bearer tokens.
- **CSRF**: every POST, PUT, PATCH and DELETE sends header `X-CSRF-Token` equal to the `smet_csrf`
  cookie (get it from `GET /auth/csrf`). Missing or wrong → 403.
- **Permissions**: each route needs a permission (`users.edit` etc.); missing → 403. The route matrix
  test lists every route.
- **Lists** return `{"items": [...], "total": n}` with `limit` (≤ 200) and `offset`.
- **Optimistic locking**: edits send the record's `version`; if someone saved first → 409.
- **Errors**: `{"detail": "..."}`; validation errors (422) carry `detail` as a list of
  `{loc, msg}` so forms can mark fields. 500s say only "The server had a problem (ref <request id>)";
  the request id is in the `X-Request-ID` header and in the log.
- **Limits**: request bodies ≤ 1 MB; sign-in 5/min per account and 30/min per client (429 with
  `Retry-After`).
- **Times**: ISO 8601 with offset, UTC.
- **Versioning**: breaking changes go to `/api/v2`; additive changes stay in v1.

## Phase 1 routes

| Method | Path | Permission |
| --- | --- | --- |
| GET | `/health/live`, `/health/ready` | public |
| GET | `/auth/csrf`, `/auth/setup-status` | public |
| POST | `/auth/setup` (first run only), `/auth/login`, `/auth/logout` | public |
| GET | `/auth/me` | signed in |
| POST | `/auth/password` | signed in |
| GET | `/users` (`q`, `status`, `limit`, `offset`; includes `counts`) | `users.view` |
| POST | `/users` | `users.create` (+ `users.edit` to give roles) |
| PATCH | `/users/{id}` | `users.edit` |
| POST | `/users/{id}/reset-password` | `users.edit` |
| GET | `/permissions`, `/roles` | `roles.view` |
| POST | `/roles`; PUT/DELETE `/roles/{id}` | `roles.configure` |
| GET | `/audit` (filters: `action`, `actor`, `entity_type`, `entity_id`, `since`, `until`, `q`) | `audit.view` |
| GET | `/audit/export` (CSV) | `audit.export` |
| GET | `/company` | `settings.view` |
| PATCH | `/company` | `settings.configure` |
| GET | `/system/status` | `system.view` |

## Phase 2–3 routes

Stock-changing posts (receipts, decisions, returns, transfers, reservations, issues, returns from
assembly, batteries built, inspections) carry a `key` (8–64 letters, digits, `-` or `_`), new for each
form. Sending the same key again returns the first result with `"duplicate": true` and posts nothing.
Quantities are JSON numbers.

| Area | Routes | Permission |
| --- | --- | --- |
| Rules & numbering | `GET/PATCH /rules`, `GET /numbering`, `PUT /numbering/{doc_type}` | settings.view / settings.configure |
| Units | `GET /units`; `POST /units`, `PATCH /units/{code}` | items.view; items.configure |
| Items | `GET /items`, `GET /items/{id}`; `POST /items`, `PATCH /items/{id}` | items.view; items.edit |
| Suppliers & customers | `GET /parties`, `GET /parties/{id}`; `POST /parties`, `PATCH /parties/{id}`, `PUT /parties/{id}/items` | parties.view; parties.edit |
| Warehouses | `GET /warehouses`; `POST /warehouses`, `PATCH /warehouses/{id}`, `POST /warehouses/{id}/locations`, `PATCH /locations/{id}` | warehouses.view; warehouses.configure |
| Purchase requests | `GET /purchase-requests[/{id}]` (detail has quotations and the comparison); `POST /purchase-requests`, `POST …/{id}/cancel` | purchasing.view; purchasing.create |
| Supplier quotations | `GET /supplier-quotes`; `POST /supplier-quotes`, `POST /supplier-quotes/{id}/order` (draft PO) | purchasing.view; purchasing.create |
| Purchase orders | `GET /purchase-orders[/{id}]`; `POST /purchase-orders`, `PUT …/{id}`, `POST …/{id}/submit`, `…/cancel`, `…/close`, `…/returns` | purchasing.view; purchasing.create |
| Approval | `POST /purchase-orders/{id}/approve`, `…/reject` | purchasing.approve (not on your own order) |
| Receiving | `POST /purchase-orders/{id}/receipts` (decisions in the same call need quality.create); `GET /goods-receipts[/{id}]` | receipts.create; purchasing.view |
| Receipt decisions | `POST /purchase-orders/{id}/lines/{line_id}/decide` | quality.create |
| Stock | `GET /stock/summary` (`low=true`), `/stock/balances`, `/stock/moves`, `/stock/reconcile`, `/serials[/{id}]`; `GET /stock/moves/export` | stock.view; stock.export |
| Transfers, reservations, counts | `POST /stock/transfers`; `GET/POST /reservations`, `POST /reservations/{id}/release`; `GET/POST /counts`, `GET /counts/{id}`, `PUT /counts/{id}/lines`, `POST /counts/{id}/submit`, `…/cancel` | stock.view; stock.create |
| Corrections | `GET /adjustments[/{id}]`; `POST /adjustments`; `POST /adjustments/{id}/approve`, `…/reject` | stock.view; adjustments.create; adjustments.approve (not your own) |
| Bills of materials | `GET /boms[/{id}]`; `POST /boms`, `PUT /boms/{id}`, `POST /boms/{id}/activate`, `…/retire` | bom.view; bom.configure |
| Inspection set-up | `GET /items/{id}/quality`; `PUT /items/{id}/quality` | items.view; quality.configure |
| Assembly orders | `GET /assembly-orders[/{id}]`, `/assembly-orders/people`; `POST /assembly-orders`, `…/{id}/release`, `…/complete`, `…/cancel`; `POST …/{id}/issue`, `…/return`, `…/units` | assembly.view; assembly.create; assembly.edit |
| Shortage to purchase | `POST /assembly-orders/{id}/shortage-request` | purchasing.create + assembly.view |
| Final inspection | `GET /inspections/queue`; `POST /serials/{id}/inspect` | assembly.view; quality.create |

## Phase 4 routes

| Area | Routes | Permission |
| --- | --- | --- |
| Enquiries | `GET /enquiries[/{id}]`; `POST /enquiries`, `PATCH /enquiries/{id}`, `POST /enquiries/{id}/quote` | sales.view; sales.create |
| Quotations | `GET /quotations[/{id}]`; `POST /quotations`, `PUT /quotations/{id}/draft`, `POST …/{id}/submit`, `…/revise`, `…/answer`, `…/convert` | sales.view; sales.create |
| Quotation approval | `POST /quotations/{id}/approve`, `…/reject` | sales.approve (not on your own revision) |
| Sales orders | `GET /sales-orders[/{id}]`; `POST …/{id}/reserve`, `…/purchase-request`, `…/close` | sales.view; sales.create |
| Assembly for an order | `POST /sales-orders/{id}/assembly-order` | assembly.create + sales.view |
| Dispatch | `GET /dispatch/plan`, `GET /dispatches[/{id}]`; `POST /dispatches`, `PUT /dispatches/{id}`, `POST …/{id}/send`, `…/deliver`, `…/cancel` | dispatch.view; dispatch.create |
| Projects | `GET /projects[/{id}]`, `/projects/people`; `POST /projects`, `PATCH /projects/{id}`, `POST /projects/{id}/sites`, `PUT /sites/{id}`, milestones, tasks, notes, changes | projects.view; projects.create |
| Decisions | `POST /changes/{id}/approve\|reject`, `POST /sheets/{id}/approve\|reject` | projects.approve (not your own) |
| Requirement sheets | `POST /projects/{id}/sheets`, `PUT /sheets/{id}`, `POST /sheets/{id}/submit` | projects.create |
| Material list | `POST /projects/{id}/reserve` (+ stock.create), `…/purchase-request`, `…/assembly-order` (assembly.create), `…/quotation` (sales.create) | projects.create / view |
| Sizing rules | `GET /sizing-rules`, `POST /sizing-rules/{id}/evaluate`; `POST /sizing-rules`, `PUT /sizing-rules/{id}` | projects.view; projects.configure |

## Phase 5 routes

| Area | Routes | Permission |
| --- | --- | --- |
| Battery banks | `GET /banks`, `/banks/{id}`, `/sites/{id}/banks`, `/sites/{id}/batteries` (delivered, not in a bank); `POST /sites/{id}/banks`, `PUT /banks/{id}` | installation.view; installation.create |
| Bank changes | `POST /banks/{id}/batteries`, `…/remove`, `…/replace`, `…/relocate` | installation.create, or installation.record on a visit you are assigned to that covers the bank |
| Visits | `GET /visits[/{id}]`, `/installation/technicians`, `/projects/{id}/installation`; `POST /visits`, `PATCH /visits/{id}`, `POST …/{id}/cancel` | installation.view; installation.create |
| Visit work | `PUT /visits/{id}/record`, `POST …/{id}/complete`, `POST …/{id}/issues/{n}/close`, `POST …/{id}/files` (multipart, up to `MAX_UPLOAD_MB`) | installation.record on your own visit, or installation.create |
| Evidence | `GET /files/{id}` | installation.view |
| Stage checks | `GET /stage-checks`; `POST /stage-checks`, `PUT /stage-checks/{id}` | installation.view or .configure; installation.configure |
| Traceability | `GET /trace/scan?code=` (label address, token, serial or supplier serial), `/serials/{id}/timeline`, `/serial-labels?ids=` (≤100, QR as SVG) | stock.view |
| Public check | `GET /public/verify/{token}`: serial, product, in service or not | none; 30 per minute per address |

Completing a visit answers 422 with `detail.missing` (a list in words) when something SMET requires
is not recorded yet; the same list is on `GET /visits/{id}` as `missing`.

## Phase 6 routes

| Area | Routes | Permission |
| --- | --- | --- |
| Warranty policies | `GET /warranty-policies`; `POST /warranty-policies`, `PUT /warranty-policies/{id}` | warranty.view; warranty.configure |
| Registrations | `GET /warranty-registrations[/{id}]`; `POST /warranty-registrations`, `PUT …/{id}`, `POST …/{id}/files` | warranty.view; warranty.create |
| Decisions | `POST /warranty-registrations/{id}/approve`, `…/reject`, `…/request-correction` | warranty.approve (not the submitter's own) |
| Corrections | `POST /warranty-registrations/{id}/correct` (approved only; reason required) | warranty.correct |
| AMC | `GET /amc[/{id}]`, `/amc/reminders`; `POST /amc`, `PUT /amc/{id}`, `POST …/{id}/activate`, `…/cancel`, `…/renew` | amc.view; amc.create |
| Service requests | `GET /service-requests[/{id}]`, `/service/people`; `POST /service-requests`, `PATCH …/{id}`, `POST …/{id}/status`, `…/close`, `…/files` | service.view; service.create |
| Service visit | `POST /service-requests/{id}/visit` | service.create + installation.create |
| Service stock | `POST /service-requests/{id}/receive`, `…/return-battery`, `…/parts`, `…/send-replacement` (each with an operation key) | service.create + stock.create |
| Maintenance alerts | `GET /maintenance-alerts[/{id}]`; `POST /maintenance-alerts`, `…/{id}/verify`, `…/{id}/close` | codes.view; codes.create |
| Reset codes | `POST /maintenance-alerts/{id}/code`, `…/{id}/reveal` (reason required, audited) | codes.reveal |
| Public | `GET /public/verify/{token}`, `GET /public/lookup?serial=`; `POST /public/registrations` (multipart), `POST /public/service-requests` | none; 30 checks a minute and 5 submissions an hour per address |

Refused attempts at approvals, corrections, codes, exports, settings, users and roles are written to
the audit log (`access.denied`) in their own transaction.
