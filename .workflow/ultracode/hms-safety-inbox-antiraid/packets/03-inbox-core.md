# Packet 03-inbox-core: Durable notification inbox

## Objective

Create a durable private inbox service and Discord UI, then wire bounded existing notification producers without editing shared schema or command registration files.

## Context

Important notices are currently scattered across DMs and logs. Closed DMs can lose badge, bond, moderation, and shop outcomes.

## Sources

- `database.py`
- `lib/bot/event_handlers.py`
- `lib/economy/bonds.py`
- `lib/economy/shop_items.py`
- `lib/bot/setup_commands.py`

## Ownership

Write-capable inbox worker.

## Write scope

- `lib/features/inbox.py`
- `commands/social/inbox.py`
- `lib/economy/bonds.py`
- narrowly scoped notification call sites in `lib/economy/shop_items.py`
- `tests/test_inbox.py`

## Coordination rule

You are not alone in the codebase. Do not revert edits made by others. Adapt to nearby changes.

## Do

- Build storage helpers around a `notifications` table contract supplied in code comments/tests.
- Support category, title, body, creation time, read time, optional jump URL, pagination, mark-read, and clear-read.
- Keep `/inbox` ephemeral and interaction-owner-only.
- Store important notices independently of DM success.
- Add focused storage/render/helper tests.

## Do not

- Edit `database.py`, `lib/bot/setup_commands.py`, or `lib/bot/event_handlers.py`; parent integrates those shared files.
- Store raw moderation evidence in the inbox.
- Commit or deploy.

## Expected output

New inbox service/UI, bounded producer wiring, tests, and exact integration requirements for the parent.

## Verification

Focused inbox tests and compile check.

## Handoff format

Files changed, table contract, producer integrations, tests run, parent TODOs.
