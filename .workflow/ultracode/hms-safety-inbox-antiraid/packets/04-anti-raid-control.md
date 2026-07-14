# Packet 04-anti-raid-control: Staff control centre

## Objective

Implement a private staff anti-raid dashboard with mode status, recent join activity, quarantined members, enable/disable actions, and safe member release.

## Context

The current toggle backs up role permissions, restricts selected permissions globally, and adds the quarantine role to new joins.

## Sources

- `commands/moderation/anti_raid.py`
- `lib/bot/setup_commands.py`
- `lib/bot/event_handlers.py`
- `config.py`

## Ownership

Parent integrator.

## Do

- Keep existing toggle compatibility.
- Add recent-join tracking with bounded persistence or runtime state.
- Add owner-checked Components V2 or standard Discord view controls.
- Release quarantined users explicitly; never auto-ban.
- Make enable/disable behavior idempotent and surface partial failures.
- Add focused pure/helper tests where Discord integration is difficult.

## Do not

- Remove existing permission backup safeguards.
- Automatically ban or infer guilt from account age.
- Deploy or mutate the live guild.

## Expected output

Control view, staff command integration, safe state helpers, tests.

## Verification

Focused helper tests plus compile/import check.

## Handoff format

Files changed, staff workflow, tests, manual Discord checks remaining.
