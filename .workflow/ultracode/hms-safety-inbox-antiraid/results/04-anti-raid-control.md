# Result: 04-anti-raid-control

## Outcome

Added a private `/anti-raid` staff control centre showing protection status, recent join velocity/history, permission-backup status, and currently quarantined members. Staff can enable or disable protection, refresh state, select quarantined members, and explicitly release them. The existing `/toggle-anti-raid` command remains compatible.

Mode transitions are idempotent and fail closed: enabling preflights the quarantine role and bot hierarchy, writes the permission backup before activating, and persists in-progress or partial enforcement failures. Disabling restores permissions before clearing active state. The canonical active/degraded state is a backed-up JSON document, with automatic migration from the legacy extensionless marker, so disaster recovery cannot silently forget an active lockdown. Degraded enforcement is visible and has an explicit retry action that never overwrites the original permission backup.

Every join is recorded in a bounded 24-hour operational history, but account age is context only and no automatic ban path exists.

Successful quarantine and release actions create generic durable inbox notices without storing raid evidence.

## Files

- `commands/moderation/anti_raid.py`
- `tests/test_anti_raid.py`
- Integration: `lib/bot/setup_commands.py`

## Verification

- `uv run --with-requirements requirements.txt --with pytest python -m pytest tests/test_anti_raid.py -q` — 10 passed.
- Changed modules compile successfully.
- `git diff --check` passed.

## Remaining manual check

Exercise permission edits and the ephemeral release selector in a non-production Discord guild before deployment.
