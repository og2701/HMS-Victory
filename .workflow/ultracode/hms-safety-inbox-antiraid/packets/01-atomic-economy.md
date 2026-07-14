# Packet 01-atomic-economy: Atomic UKP bank/user flows

## Objective

Make all bank-to-user and user-to-bank UKP operations transactional across balances, bank statistics, economy log, balance history, and user transaction ledger, including failure rollback tests.

## Context

`add_bb` currently withdraws from the bank before separately crediting a user; `remove_bb` debits a user before separately depositing into the bank. The fixed-supply invariant must survive exceptions and process interruption boundaries.

## Sources

- `database.py`
- `lib/economy/economy_manager.py`
- `lib/economy/bank_manager.py`
- `tests/test_economy_invariant.py`

## Ownership

Write-capable economy worker.

## Write scope

- `lib/economy/economy_manager.py`
- `lib/economy/bank_manager.py`
- `tests/test_economy_atomicity.py`
- focused additions to `tests/test_economy_invariant.py`

## Coordination rule

You are not alone in the codebase. Do not revert edits made by others. Adapt to nearby changes.

## Do

- Preserve public function signatures where practical.
- Use one SQLite transaction per logical bank/user move.
- Include tax handling and all durable ledgers in the same transaction.
- Add deterministic fault-injection or rollback tests.
- Check callers that rely on `BankManager` accounting fields.

## Do not

- Edit `database.py`, command registration, inbox, lifecycle, or moderation files.
- Change game payout policy or economic rates.
- Commit or deploy.

## Expected output

Atomic implementation with focused tests and a concise risk note.

## Verification

Economy invariant tests plus new rollback/failure tests.

## Handoff format

Files changed, transaction design, tests run, remaining payout/minting risks.
