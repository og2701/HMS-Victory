# Result: 02-lifecycle-safety

## Outcome

Discord reconnects no longer repeat successful process boot work. Scheduler registration and start are independently guarded, while every process job has a stable ID and replacement semantics so a retry after partial registration cannot duplicate jobs.

Database startup now validates both existing and restored SQLite files with `quick_check` and essential-schema checks. Restore downloads stay in same-filesystem temporary paths, ZIP input is tightly constrained and size-bounded, and promotion uses atomic replacement. Missing credentials/backups, corrupt data, unsafe archives, or an invalid existing database stop startup before `init_db`. Empty bootstrap requires explicit `ALLOW_EMPTY_DB_BOOTSTRAP=true` and only applies when the database path is absent.

## Files

- `main.py`
- `lib/bot/event_handlers.py`
- `lib/bot/scheduled_tasks.py`
- `lib/bot/backup_manager.py`
- `.env.example`
- `tests/test_lifecycle_safety.py`

## Verification

- `python3 tests/test_lifecycle_safety.py` — 14/14 passed.
- Current workspace `database.db` passed the new validator.
- Changed modules compiled and passed whitespace validation.

## Operational note

A recovery failure can cause the service manager to restart repeatedly, but it cannot silently initialise an empty production database.
