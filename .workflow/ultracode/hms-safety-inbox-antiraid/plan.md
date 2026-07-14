# HMS Victory safety, inbox, and anti-raid improvements

## Goal

Implement five requested changes: atomic UKP bank/user movements, idempotent scheduler startup, fail-safe database recovery, a durable `/inbox`, and an anti-raid control centre.

## Success criteria

- Bank-to-user and user-to-bank UKP operations commit balances, bank statistics, history, and transaction ledgers together or not at all.
- Repeated Discord `on_ready` calls do not register duplicate jobs or restart an already-running scheduler.
- A missing database is restored through a validated temporary snapshot; failed recovery stops startup unless an explicit empty-database bootstrap flag is set.
- `/inbox` provides a private, paginated durable notification list with read/clear controls and is fed by important existing notification paths.
- Staff can open a private anti-raid control centre, see mode/recent joins/quarantined members, enable or disable protection, and approve releases without automatic bans.
- Focused and full practical tests pass; skipped checks are documented.

## Current context

- The bot uses a single SQLite connection in WAL mode and a closed 800,000-UKP economy.
- `add_bb` and `remove_bb` currently perform bank and user mutations across separate commits.
- `on_ready` calls `schedule_client_jobs` on every gateway ready event.
- missing/failed database restoration currently initializes an empty database and continues.
- notification delivery is spread across DMs and logs, with DM failures often only logged.
- anti-raid currently toggles global role restrictions and quarantines every new join while enabled.

## Constraints

- Preserve user-owned Skyrim image changes and all unrelated worktree changes.
- Never use `grep` or `find`; use `rg` and `fd`.
- Do not commit, push, publish, or deploy.
- Do not touch production data or the remote VM.
- Use `/home/ubuntu/HMS-Victory/update_bot.sh` only if a future deployment is explicitly requested.

## Risk level

High. The work changes the economy chokepoint, startup/recovery behavior, persistent data schema, and staff moderation controls.

## Approval gates

No additional gate is required for local source edits and tests; the user explicitly requested these changes. Production migration, deployment, or live data mutation remains out of scope.

## Mode

Delegated Ultracode workflow using three bounded write-capable agents plus parent-owned integration.

## Work packets

1. `01-atomic-economy`: make bank/user UKP flows transactionally atomic and add failure-path tests.
2. `02-lifecycle-safety`: make scheduler startup idempotent and database recovery fail safe.
3. `03-inbox-core`: implement the durable inbox modules and focused producer integrations without editing shared registration/schema files.
4. `04-anti-raid-control`: parent implements the anti-raid dashboard and staff actions.
5. `05-integration-verification`: parent integrates schema/commands/producers, reviews diffs, and runs combined verification.

## Integration policy

The parent owns `database.py`, `lib/bot/setup_commands.py`, cross-packet conflict resolution, final producer wiring, workflow artifacts, and all combined verification. Agents must stay inside their declared write scopes.

## Verification plan

- Focused tests for economy rollback, scheduler re-entry, recovery validation, inbox storage/UI helpers, and anti-raid state/actions.
- Run every suitable committed test module when dependencies permit.
- Compile all changed Python modules.
- Inspect the full diff and git status for unrelated changes.
- Independent final review of correctness and missed integration risks.

## Completion criteria

All five requested outcomes are implemented, combined verification is green or any environmental limitation is explicit, workflow artifacts contain evidence, and no deploy/commit has occurred.
