# Result 01: Architecture and performance

## Status

DONE

## Summary

The dominant performance issue is synchronous, serialized SQLite and file I/O on Discord's event loop, especially the per-message summary path. Twelve candidates were returned; the parent accepted the strongest leads for independent verification: a dedicated DB execution boundary, atomic summary counters, message-hot-path query reduction, nonblocking serialized backups, durable scheduler runs, leaner ready initialization, query indexes, versioned migrations, and explicit module boundaries.

## Evidence and paths

- `database.py:7-107`: one shared synchronous connection and global reentrant lock, including a five-second busy timeout.
- `main.py:230-285`, `lib/features/summary.py:197-311`, `lib/features/message_archive.py:33-43`, `lib/features/xp_system.py:199-221`: synchronous DB and whole-blob/file work on every message.
- `lib/bot/backup_manager.py:843-868`, `lib/bot/scheduled_tasks.py:547-549`: synchronous snapshot under the DB lock every five minutes; only compression is threaded.
- `lib/bot/event_handlers.py:900-928`: command sync, per-command sleeps, stage refresh, and two backups block ready initialization.
- `lib/bot/scheduled_tasks.py:514-603`: stable process job IDs exist, but recurring financial jobs lack durable run-period claims.
- `database.py:250-956`: monolithic boot-time schema creation, migration, repair, and seeding.

## Files changed

None by the delegated agent.

## Verification run

`python3 -m unittest tests.test_lifecycle_safety` from the repository root exited 0 with 28 tests passed. Static inspection used read-only Git commands, `rg`, `fd`, `nl`, `sed`, and `wc`.

## Concerns and risks

No production event-loop lag, database size, query plan, scheduler runtime, or message-rate telemetry was available. Database work must preserve transaction ordering and the closed-economy invariant; scheduler catch-up is unsafe without idempotency keys. APScheduler is unpinned, so defaults should not be assumed.

## Parent action

Verify the hot-path call graph, backup blocking boundary, scheduler registration, migration behavior, and cited lines. Integrate the accepted candidates with reliability findings, keeping composite indexes and offloaded backups as low-risk early work.
