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
