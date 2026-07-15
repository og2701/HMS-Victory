# Result 02: Reliability, security, and recovery

## Status

DONE_WITH_CONCERNS

## Summary

Core SQLite transfers, online snapshots, restore validation, safe ZIP extraction, and fail-closed anti-raid recovery are strong. The highest risks are economy workflows split across SQLite and JSON/in-memory state, an unauthorized bulk-role button callback, a non-transactional `/pay` cap, one-generation recovery selection, and insufficient operational proof that backups and invariants remain healthy.

## Evidence and paths

- `commands/economy/wager.py:17-170`: in-memory settlement claim, sequential escrow debits, persistence after Discord send.
- `lib/economy/prediction_system.py:72-122,883-921`: debit/payout operations separated from JSON state transitions.
- `commands/moderation/role_management.py:48-79`: public bulk-role callback does not re-authorize the clicker.
- `lib/bot/setup_commands.py:438-492`: daily cap is read before the separate transfer transaction.
- `lib/features/ukp_rewards.py:662-678,737-779`: benefits claims and fines span JSON and SQLite commits.
- `lib/bot/backup_manager.py:385-409,754-802`: recovery stops after selecting the newest matching generation even if validation rejects it.
- `database.py:18-25,260-263,509-548`: foreign keys are not enabled and financial constraints are mainly enforced in application code.
- `lib/bot/scheduled_tasks.py:765-811`: the Discord economy-log queue deletes source rows after delivery and lacks retained delivery state.

## Files changed

None by the delegated agent.

## Verification run

The pytest attempt exited 1 because pytest is not installed. Direct isolated checks succeeded: `python3 tests/test_economy_atomicity.py` (15/15), `python3 tests/test_economy_invariant.py` (14 passed, 4 dependency-skipped), `python3 tests/test_lifecycle_safety.py` (28 passed), and `python3 tests/test_badge_rewards.py` (passed with conserved 800,000 supply).

## Concerns and risks

Live Discord permissions and backup-channel ACLs, external snapshots, monitoring, production backup age, and restore drills were not available. Schema constraints require a pre-migration data audit. Automatic anti-raid action needs threshold tuning and should not include automatic bans.

## Parent action

Prioritize durable atomic wager/prediction settlement, bulk-role callback authorization, and transactional `/pay` cap enforcement. Verify newest-valid backup fallback and retain existing strict candidate validation. Coordinate scheduler idempotency, offsite backups, and observability as one reliability program.
