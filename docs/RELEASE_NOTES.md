# Release notes

## 0.1.0 · Phase 1 foundation · 2026-10-28

New
- First-run setup: company profile and the first System Administrator.
- Sign-in with temporary passwords that must be replaced; change password; sign out everywhere on change.
- Users: add, edit, give roles, deactivate and reactivate, reset password; filter by status.
- Roles & permissions: 17 role templates from the proposal, custom roles, permission editor with the
  effect of each change; guards against changing your own access.
- Audit log: every sign-in and change with who, when, what changed and why; filters; CSV export.
  Entries can't be edited or deleted, by anyone.
- Company settings (name, time zone, currency) with live previews; System health.
- Light, dark and system themes; works at phone width.

Setup
- Needs PostgreSQL. Run `uv run python -m app.setup_db` once, then `uv run alembic upgrade head`
  (README, "Run it on Windows 11").

Known limitations
- No business modules yet (procurement and inventory come in 0.2.0).
- No email: password resets are handed over by an administrator.
- No multi-factor sign-in yet.
