# Result: 01-atomic-economy

## Outcome

UKP bank-to-user, user-to-bank, user-to-user, bulk handout, and batch tax moves now commit their balances, bank accounting, economy logs, balance history, user statements, wealth-tax legs, and optional `/pay` audit rows in one locked SQLite transaction. A late failure rolls back the entire logical move.

Parent integration wired Deputy PM bulk handouts, both scheduled tax jobs, user `/pay`, and direct `/pay` to the bank to the new atomic APIs.

## Files

- `lib/economy/economy_manager.py`
- `lib/economy/bank_manager.py`
- `database.py`
- `lib/economy/bank_commands_ui.py`
- `lib/bot/setup_commands.py`
- `lib/bot/scheduled_tasks.py`
- `tests/test_economy_atomicity.py`
- `tests/test_economy_invariant.py`

Independent review additionally separated confirmed bank insolvency from SQLite or integrity failures. Casino payouts may enter the existing explicit mint fallback only after a valid, synchronised bank state proves the balance is insufficient; storage faults now raise and leave settlement unchanged.

## Verification

- `python3 tests/test_economy_atomicity.py` — 15/15 passed, including late-ledger, bond-state, bulk-recipient, tax-batch, casino-storage, and missing-bank-row fault injection.
- `python3 tests/test_economy_invariant.py` — 14 passed, 4 optional-dependency skips.
- Full `tests/` pytest run with requirements — passed as part of integration verification.

## Residual policy

Explicit casino insolvency fallbacks still mint a payout by existing policy; the direct credit and ledgers are transactional, but that deliberate fallback can increase total supply. Storage failures cannot enter that fallback.
