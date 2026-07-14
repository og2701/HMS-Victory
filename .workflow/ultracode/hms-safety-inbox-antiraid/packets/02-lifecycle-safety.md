# Packet 02-lifecycle-safety: Idempotent startup and safe recovery

## Objective

Prevent duplicate scheduler startup across repeated ready events and make missing-database recovery validated and fail closed by default.

## Context

`on_ready` unconditionally calls `schedule_client_jobs`; restore errors currently create an empty database and continue.

## Sources

- `main.py`
- `lib/bot/event_handlers.py`
- `lib/bot/scheduled_tasks.py`
- `lib/bot/backup_manager.py`
- `.env.example`

## Ownership

Write-capable lifecycle worker.

## Write scope

- `main.py`
- `lib/bot/event_handlers.py`
- `lib/bot/scheduled_tasks.py`
- `lib/bot/backup_manager.py`
- `.env.example`
- `tests/test_lifecycle_safety.py`

## Coordination rule

You are not alone in the codebase. Do not revert edits made by others. Adapt to nearby changes.

## Do

- Make job registration and scheduler start safe on repeat calls.
- Restore into a temporary location, validate SQLite integrity and essential schema, then atomically promote.
- Refuse empty bootstrap unless an explicit environment flag permits it.
- Avoid zip traversal and partial-file promotion.
- Add focused tests without requiring Discord network access.

## Do not

- Edit economy, inbox, anti-raid, database schema, or command registration files.
- Contact Discord or mutate a live database during tests.
- Commit or deploy.

## Expected output

Lifecycle changes, bootstrap documentation, and focused tests.

## Verification

Repeat-start test, valid/invalid snapshot tests, and compile/import check.

## Handoff format

Files changed, recovery contract, tests run, operational caveats.
