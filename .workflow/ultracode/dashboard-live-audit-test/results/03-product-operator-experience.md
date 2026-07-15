# Result 03: Product and operator experience

## Status

DONE_WITH_CONCERNS

## Summary

The strongest product opportunities are operational rather than cosmetic: explicit audited moderation actions, proactive but human-controlled raid alerts, complete quarantine triage, canonical notification delivery, command grouping and native permissions, and a staff health dashboard. Existing inbox ownership and anti-raid recovery primitives are suitable foundations.

## Evidence and paths

- `lib/bot/setup_commands.py:225-273`, `lib/core/discord_helpers.py:49-65`: several state-inverting moderation toggles lack reason, confirmation, and common audit behavior.
- `commands/moderation/anti_raid.py:255-261,621-653,697-705,912-950`: join velocity is manual-view-only and quarantine selection stops at 25 members.
- `lib/features/inbox.py:69-202`, `commands/social/inbox.py:70-190`: durable storage is strong but producers and discovery/filter/retention workflows are limited.
- `lib/bot/setup_commands.py:83-110,134-762`: roughly 67 flat commands use a custom runtime permission wrapper rather than native guild/default-permission metadata.
- `lib/bot/event_handlers.py:1248-1328`: hidden message-prefix admin tools include raw SQL diagnostics.
- `lib/bot/scheduled_tasks.py:522-568`, `config.py:355-465`: many critical jobs and hard-coded Discord resources have no consolidated operator health view.
- `commands/moderation/announcement_command.py:54-197`: announcement drafts are public, in-memory, unbounded views with limited editing/recovery.

## Files changed

None by the delegated agent.

## Verification run

The targeted pytest attempt exited 1 because pytest is not installed. `python3 tests/test_inbox.py` exited 0 with 7/7 tests passed. All other work was static, local, and read-only.

## Concerns and risks

Live Discord mobile layout, command picker crowding, inbox discovery, and appropriate raid thresholds cannot be established statically. Staff policy is needed for notification retention and which financial/moderation events should notify users. Command renames require a migration plan.

## Parent action

Include explicit moderation actions, proactive raid alerts, quarantine pagination, notification expansion, native command permissions/grouping, and operator health in the final backlog. Keep automatic banning out of scope and require live staff/mobile validation before setting thresholds or final layout.
