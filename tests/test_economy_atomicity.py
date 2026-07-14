"""Failure-path tests for atomic UKP moves between the bank and users."""

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOTAL_SUPPLY = 800_000


def _fresh_economy(tmpdir):
    import database

    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    database.DB_FILE = os.path.join(tmpdir, "test.db")
    database.init_db()

    import lib.economy.economy_manager as economy_manager
    import lib.economy.bank_manager as bank_manager

    economy_manager._HIST_LAST.clear()
    return economy_manager, bank_manager, database


def _snapshot(database):
    db = database.DatabaseManager
    return {
        "balances": db.fetch_all(
            "SELECT user_id, balance FROM ukpence ORDER BY user_id"
        ),
        "bank": db.fetch_one("SELECT * FROM bank WHERE id = 1"),
        "economy_transactions": db.fetch_all(
            "SELECT timestamp, log_text FROM economy_transactions ORDER BY id"
        ),
        "balance_history": db.fetch_all(
            "SELECT user_id, ts, balance FROM balance_history ORDER BY id"
        ),
        "user_transactions": db.fetch_all(
            "SELECT user_id, ts, amount, balance_after, reason, counterparty_id "
            "FROM user_transactions ORDER BY id"
        ),
        "bonds": db.fetch_all(
            "SELECT id, user_id, principal, rate_pct, term_days, opened_ts, "
            "matures_ts, status FROM bonds ORDER BY id"
        ),
        "notifications": db.fetch_all(
            "SELECT user_id, category, title, body, created_at, read_at "
            "FROM notifications ORDER BY id"
        ),
        "pay_transfers": db.fetch_all(
            "SELECT timestamp, payer_id, recipient_id, amount "
            "FROM pay_transfers ORDER BY id"
        ),
    }


def _total_supply(database):
    return sum(
        row[0]
        for row in database.DatabaseManager.fetch_all("SELECT balance FROM ukpence")
    )


def _fail_before_insert(database, table, trigger_name):
    database.DatabaseManager.execute(f"""
        CREATE TRIGGER {trigger_name}
        BEFORE INSERT ON {table}
        BEGIN
            SELECT RAISE(ABORT, 'injected {table} failure');
        END
    """)


def _assert_bank_storage_error(bank_module, callback):
    try:
        callback()
        raise AssertionError("injected bank storage failure did not propagate")
    except bank_module.BankStorageError:
        pass


def test_bank_to_user_rolls_back_at_each_durable_ledger():
    """Each ledger fails after at least one balance/accounting write has run."""
    for table in ("economy_transactions", "balance_history", "user_transactions"):
        with tempfile.TemporaryDirectory() as tmpdir:
            economy, bank_module, database = _fresh_economy(tmpdir)
            user_id = 10_001
            before = _snapshot(database)
            _fail_before_insert(database, table, f"fail_{table}")

            _assert_bank_storage_error(
                bank_module,
                lambda: economy.add_bb(
                    user_id, 500, reason="Blackjack payout", taxable=False
                ),
            )

            assert _snapshot(database) == before
            assert economy.get_bb(user_id) == 0
            assert _total_supply(database) == TOTAL_SUPPLY
            assert str(user_id) not in economy._HIST_LAST


def test_taxed_bank_credit_rolls_back_gross_withdrawal_and_tax_refund():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, bank_module, database = _fresh_economy(tmpdir)
        user_id = 10_002
        assert economy.add_bb(
            user_id, 12_000, reason="seed", taxable=False
        ) is True
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)
        _fail_before_insert(database, "user_transactions", "fail_taxed_user_ledger")

        # At 12k, this normally withdraws 1,000 gross, returns 600 tax to the
        # bank, and credits 400. The late statement failure must undo all of it.
        _assert_bank_storage_error(
            bank_module,
            lambda: economy.add_bb(
                user_id, 1_000, reason="taxed reward", taxable=True
            ),
        )

        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert _total_supply(database) == TOTAL_SUPPLY


def test_user_to_bank_rolls_back_when_final_bank_log_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        user_id = 10_003
        assert economy.add_bb(
            user_id, 1_000, reason="seed", taxable=False
        ) is True
        database.DatabaseManager.execute("""
            CREATE TRIGGER fail_bank_deposit_log
            BEFORE INSERT ON economy_transactions
            WHEN NEW.log_text LIKE '🏦 Bank deposit:%'
            BEGIN
                SELECT RAISE(ABORT, 'injected bank deposit log failure');
            END
        """)
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)

        # The user balance/history/statement and bank balance/stats have all
        # been written before the bank log trigger aborts the transaction.
        assert economy.remove_bb(
            user_id, 250, reason="Blackjack bet", to_bank=True
        ) is False

        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert _total_supply(database) == TOTAL_SUPPLY


def test_standalone_bank_operations_roll_back_if_their_log_fails():
    for operation in ("deposit", "withdraw"):
        with tempfile.TemporaryDirectory() as tmpdir:
            _, bank_module, database = _fresh_economy(tmpdir)
            before = _snapshot(database)
            _fail_before_insert(
                database, "economy_transactions", f"fail_{operation}_log"
            )

            method = getattr(bank_module.BankManager, operation)
            assert method(100, description=f"standalone {operation}") is False

            assert _snapshot(database) == before


def test_direct_user_credit_rolls_back_balance_and_history_on_ledger_failure():
    """The explicit casino insolvency mint fallback still uses this path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        user_id = 10_004
        before = _snapshot(database)
        _fail_before_insert(database, "user_transactions", "fail_direct_credit_ledger")

        try:
            economy.UKPenceManager.add_amount(
                user_id, 75, reason="bank insolvent - minted"
            )
            raise AssertionError("injected ledger failure did not propagate")
        except sqlite3.IntegrityError:
            pass

        assert _snapshot(database) == before
        assert str(user_id) not in economy._HIST_LAST


def test_casino_storage_failure_aborts_without_entering_mint_fallback():
    from commands.economy import blackjack, casino_base, higher_lower, slots

    payout_callers = (
        casino_base.credit_from_bank,
        blackjack._credit,
        higher_lower._credit,
        slots._credit,
    )
    for offset, payout in enumerate(payout_callers):
        with tempfile.TemporaryDirectory() as tmpdir:
            economy, bank_module, database = _fresh_economy(tmpdir)
            user_id = 10_014 + offset
            before = _snapshot(database)
            database.DatabaseManager.execute("""
                CREATE TRIGGER fail_bank_accounting_update
                BEFORE UPDATE ON bank
                BEGIN
                    SELECT RAISE(ABORT, 'injected bank accounting failure');
                END
            """)

            _assert_bank_storage_error(
                bank_module,
                lambda payout=payout: payout(user_id, 75, "Casino win"),
            )

            assert _snapshot(database) == before
            assert economy.get_bb(user_id) == 0
            assert str(user_id) not in economy._HIST_LAST
            assert _total_supply(database) == TOTAL_SUPPLY


def test_casino_confirmed_insolvency_still_mints_promised_payout():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, bank_module, database = _fresh_economy(tmpdir)
        user_id = 10_018
        assert bank_module.BankManager.set_balance(
            0, description="test confirmed insolvency"
        ) is True

        assert economy.credit_casino_payout(
            user_id, 75, "Blackjack win"
        ) is True

        assert economy.get_bb(user_id) == 75
        assert bank_module.BankManager.get_balance() == 0
        transaction = database.DatabaseManager.fetch_one(
            "SELECT amount, balance_after, reason FROM user_transactions "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (str(user_id),),
        )
        assert transaction == (
            75,
            75,
            "Blackjack win [bank insolvent - minted]",
        )


def test_missing_bank_balance_row_is_storage_failure_not_insolvency():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, bank_module, database = _fresh_economy(tmpdir)
        from config import BOT_ID

        user_id = 10_019
        database.DatabaseManager.execute(
            "DELETE FROM ukpence WHERE user_id = ?", (str(BOT_ID),)
        )
        before = _snapshot(database)

        _assert_bank_storage_error(
            bank_module,
            lambda: economy.credit_casino_payout(
                user_id, 75, "Blackjack win"
            ),
        )

        assert _snapshot(database) == before
        assert economy.get_bb(user_id) == 0


def test_bulk_bank_to_users_rolls_back_every_recipient_on_late_failure():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, bank_module, database = _fresh_economy(tmpdir)
        first, second = 10_005, 10_006
        before = _snapshot(database)
        database.DatabaseManager.execute(f"""
            CREATE TRIGGER fail_second_recipient_ledger
            BEFORE INSERT ON user_transactions
            WHEN NEW.user_id = '{second}'
            BEGIN
                SELECT RAISE(ABORT, 'injected second recipient failure');
            END
        """)

        assert bank_module.BankManager.transfer_to_users(
            [first, first, second],
            125,
            description="Handout test",
            user_reason="ukpadd test",
        ) is False

        assert _snapshot(database) == before
        assert economy.get_bb(first) == 0
        assert economy.get_bb(second) == 0
        assert _total_supply(database) == TOTAL_SUPPLY


def test_tax_batch_rolls_back_all_users_when_final_bank_log_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, bank_module, database = _fresh_economy(tmpdir)
        first, second = 10_007, 10_008
        assert economy.add_bb(first, 1_000, reason="seed", taxable=False) is True
        assert economy.add_bb(second, 400, reason="seed", taxable=False) is True
        database.DatabaseManager.execute("""
            CREATE TRIGGER fail_tax_batch_bank_log
            BEFORE INSERT ON economy_transactions
            WHEN NEW.log_text LIKE '🏦 Bank deposit:%'
            BEGIN
                SELECT RAISE(ABORT, 'injected tax bank log failure');
            END
        """)
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)

        result = bank_module.BankManager.collect_tax_batch(
            [(first, 300), (second, 900)],
            description="Inactivity tax",
            bank_description="Inactivity tax from batch",
        )

        assert result is None
        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bank_payment_rolls_back_if_pay_audit_insert_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        user_id = 10_009
        assert economy.add_bb(
            user_id, 500, reason="seed", taxable=False
        ) is True
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)
        _fail_before_insert(database, "pay_transfers", "fail_bank_pay_audit")

        assert economy.remove_bb(
            user_id,
            200,
            reason="/pay to HMS Victory (Bank)",
            to_bank=True,
            record_pay_transfer=True,
        ) is False

        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bond_open_rolls_back_money_when_state_insert_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        from lib.economy import bonds

        user_id = 10_010
        assert economy.add_bb(user_id, 1_000, reason="seed", taxable=False)
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)
        _fail_before_insert(database, "bonds", "fail_bond_state_insert")

        bond, error = bonds.open_bond(user_id, 250, 3)

        assert bond is None
        assert "not changed" in error
        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bond_early_withdrawal_rolls_back_state_when_credit_ledger_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        from lib.economy import bonds

        user_id = 10_011
        assert economy.add_bb(user_id, 1_000, reason="seed", taxable=False)
        bond, error = bonds.open_bond(user_id, 250, 3)
        assert error is None and bond["status"] == "active"
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)
        _fail_before_insert(database, "user_transactions", "fail_bond_refund_ledger")

        refund, penalty, error = bonds.withdraw_early(user_id)

        assert (refund, penalty) == (0, 0)
        assert "not changed" in error
        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert bonds.get_active(user_id)["id"] == bond["id"]
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bond_maturity_rolls_back_state_when_credit_ledger_fails():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        from lib.economy import bonds

        user_id = 10_012
        assert economy.add_bb(user_id, 1_000, reason="seed", taxable=False)
        bond, error = bonds.open_bond(user_id, 250, 3)
        assert error is None
        database.DatabaseManager.execute(
            "UPDATE bonds SET matures_ts = 0 WHERE id = ?", (bond["id"],)
        )
        before = _snapshot(database)
        history_cache_before = dict(economy._HIST_LAST)
        _fail_before_insert(database, "user_transactions", "fail_bond_maturity_ledger")

        asyncio.run(bonds.mature_due(object()))

        assert _snapshot(database) == before
        assert economy._HIST_LAST == history_cache_before
        assert bonds.get_active(user_id)["id"] == bond["id"]
        assert _total_supply(database) == TOTAL_SUPPLY


def test_successful_bond_maturity_commits_payout_state_and_notice():
    with tempfile.TemporaryDirectory() as tmpdir:
        economy, _, database = _fresh_economy(tmpdir)
        import config
        from lib.economy import bonds

        class User:
            async def send(self, _message):
                return None

        class Client:
            def get_user(self, _user_id):
                return User()

        user_id = 10_013
        assert economy.add_bb(user_id, 1_000, reason="seed", taxable=False)
        bond, error = bonds.open_bond(user_id, 250, 3)
        assert error is None
        database.DatabaseManager.execute(
            "UPDATE bonds SET matures_ts = 0 WHERE id = ?", (bond["id"],)
        )

        original_sources_file = config.EARNED_SOURCES_FILE
        config.EARNED_SOURCES_FILE = os.path.join(tmpdir, "earned_sources.json")
        try:
            asyncio.run(bonds.mature_due(Client()))
        finally:
            config.EARNED_SOURCES_FILE = original_sources_file

        assert bonds.get_active(user_id) is None
        assert database.DatabaseManager.fetch_one(
            "SELECT status FROM bonds WHERE id = ?", (bond["id"],)
        ) == ("matured",)
        assert economy.get_bb(user_id) == 1_005
        assert database.DatabaseManager.fetch_one(
            "SELECT category, title FROM notifications WHERE user_id = ?",
            (str(user_id),),
        ) == ("economy", "Bond matured")
        assert _total_supply(database) == TOTAL_SUPPLY


if __name__ == "__main__":
    import traceback

    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception:
            failures += 1
            print(f"FAIL  {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed, {failures} failed")
    sys.exit(1 if failures else 0)
