# Orchestration

## Parent critical path

Own shared schema and command registration, implement anti-raid control centre, integrate agent changes, wire remaining inbox producers, and run final verification.

## Packets

- `01-atomic-economy`: worker, write-capable, economy modules and focused tests.
- `02-lifecycle-safety`: worker, write-capable, startup/scheduler/recovery modules and focused tests.
- `03-inbox-core`: worker, write-capable, new inbox modules plus bounded producer integrations and tests.
- `04-anti-raid-control`: parent, anti-raid module/tests and command integration.
- `05-integration-verification`: parent, shared files, combined tests, review, documentation.

## Delegation

Spawn three native agents after artifacts exist. Agents work concurrently with non-overlapping ownership. The parent does not wait until anti-raid discovery and shared integration design are ready.

## Agents

- Economy worker: `lib/economy/economy_manager.py`, `lib/economy/bank_manager.py`, economy-focused tests.
- Lifecycle worker: `main.py`, `lib/bot/event_handlers.py`, `lib/bot/scheduled_tasks.py`, `lib/bot/backup_manager.py`, `.env.example`, lifecycle tests.
- Inbox worker: new inbox modules/tests plus `lib/economy/bonds.py` and narrowly scoped shop notification call sites.

## Wait points

- Wait before editing any agent-owned file.
- Wait for economy worker before final schema/ledger integration review.
- Wait for lifecycle worker before wiring inbox into `event_handlers.py`.

## Fallback

If an agent blocks, the parent takes over that packet after recording the handoff. If tests cannot run in the system Python, use an isolated local environment without modifying production configuration.

## Verification order

Focused packet tests, integration tests, full suitable pytest suite, compile check, diff review, independent review.
