# Operations runbook

For whoever runs SMET Operations day to day. Commands run in `backend/` unless said otherwise.

## Every day

- **Backup.** Schedule `uv run python -m app.backup create` once a night (Windows Task Scheduler or
  cron). It writes a database dump and a zip of the files to `BACKUP_DIR`, each with a `.sha256`.
  Copy the folder off the machine (another disk, or encrypted cloud storage). System health warns when
  the newest backup is more than a day and a half old.
- **Keys.** `backend/.env` holds `CODES_KEY` and `SECRETS_KEY`. They are not in the backup on
  purpose. Keep a copy of `.env` in a password manager or a safe; without it, reset codes and
  provider credentials in a restored database can't be read.
- **Look at System health** (Administration). Anything marked *Check* or *Error* needs a person:
  failed background jobs, a channel whose connection test failed, storage, scanner, backups, clock.

## Every month: restore drill

1. `uv run python -m app.backup verify <newest .dump>`: checksum and archive readable.
2. Restore into a spare copy, never the working database: point `MIGRATION_DATABASE_URL` at an empty
   test database, then `uv run python -m app.backup restore <dump> --files <zip> --yes`.
3. Sign in to that copy, open a few recent records and a photograph. Note the date and the result in
   the log below. (The automated test `test_a_backup_restores_the_data_and_the_files` does the same
   round trip on every CI run.)

## Restore after a loss

1. Stop the app. Keep the damaged database if you can (rename it) for the post mortem.
2. Restore the newest good backup: `uv run python -m app.backup restore <dump> --files <zip> --yes`.
3. Put back `.env` (same keys). `uv run alembic upgrade head` if the backup is from an older version.
4. Start the app, check System health, tell users which period may be missing (since the backup).

## When something goes wrong (incident)

1. **Contain.** If data is at risk or leaking: stop the app or take the site offline. If a password
   or key may be known to someone else: change it (Users → reset password; regenerate the provider
   credential at the provider and save the new one under Messages; for `CODES_KEY` see below).
2. **Note.** Time it started, what users saw, the request id shown in the error message
   ("ref …"), and what was changed. The application log has the same request id.
3. **Fix** with the smallest safe change; restore from backup only if data was lost.
4. **Tell** the owner and affected users what happened and what to expect.
5. **Post mortem** within a week (template below). No blame; the aim is the change that stops a repeat.

A lost or exposed `CODES_KEY` can't simply be replaced: codes encrypted with it become unreadable.
Record new reset codes from the supplier again and keep the old key until no code needs it.

## On call

SMET is a small team in one time zone: business hours, with the owner as the contact out of hours.
Fill in before going live:

| Role | Name | Phone | Hours |
| --- | --- | --- | --- |
| First contact (app problems) | | | |
| Owner (decisions, outside hours) | | | |
| Hosting provider support | | | |

## Post mortem template

- What happened, in a few sentences, with times (Dubai time).
- Who and what was affected, and for how long.
- How it was noticed, and how long that took.
- Root cause (the system or process, not a person).
- What fixed it.
- What we will change so it can't happen again, each with an owner and a date.

## Log

| Date | What | Who | Result |
| --- | --- | --- | --- |
| | | | |
