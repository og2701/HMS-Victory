"""Closed-economy invariant tests.

The whole server theme rests on a fixed 800,000 UKPence supply: the sum of every
balance in the `ukpence` table (users + the bot, which acts as the bank) must
always equal 800,000. These tests exercise the money-movement primitives against
a throwaway SQLite database and assert that the invariant holds across transfers,
bank withdrawals/deposits, the wealth tax, and prediction payouts.

Runnable either under pytest (`pytest tests/test_economy_invariant.py`) or directly
with the stdlib (`python3 tests/test_economy_invariant.py`) - no pytest required.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TOTAL_SUPPLY = 800_000


def _fresh_economy(tmpdir):
    """Point the shared DatabaseManager at a fresh temp DB, initialise it, and
    return (economy_manager, bank_manager, database) modules."""
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


def _total_supply(database):
    rows = database.DatabaseManager.fetch_all("SELECT balance FROM ukpence")
    return sum(r[0] for r in rows)


class _Skip(Exception):
    """Raised to skip a test in the stdlib runner when an optional dep is absent."""


def _require_discord():
    """Skip prediction tests when their optional runtime deps are unavailable."""
    try:
        import discord  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        try:
            import pytest
            pytest.skip("prediction runtime dependencies not installed")
        except ImportError:
            raise _Skip("prediction runtime dependencies not installed")


def test_initial_supply_is_800k():
    with tempfile.TemporaryDirectory() as d:
        _, _, database = _fresh_economy(d)
        assert _total_supply(database) == TOTAL_SUPPLY


def test_add_then_remove_conserves_supply():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        user = 111
        assert em.add_bb(user, 5000, from_bank=True, taxable=False) is True
        assert em.get_bb(user) == 5000
        assert _total_supply(database) == TOTAL_SUPPLY

        assert em.remove_bb(user, 2000, to_bank=True) is True
        assert em.get_bb(user) == 3000
        assert _total_supply(database) == TOTAL_SUPPLY


def test_transfer_moves_funds_and_conserves():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        a, b = 222, 333
        em.add_bb(a, 1000, from_bank=True, taxable=False)
        assert database.DatabaseManager.transfer(a, b, 400, reason="test") is True
        assert em.get_bb(a) == 600
        assert em.get_bb(b) == 400
        assert _total_supply(database) == TOTAL_SUPPLY


def test_transfer_insufficient_is_noop():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        a, b = 444, 555
        em.add_bb(a, 600, from_bank=True, taxable=False)
        assert database.DatabaseManager.transfer(a, b, 10_000) is False
        assert em.get_bb(a) == 600
        assert em.get_bb(b) == 0
        assert _total_supply(database) == TOTAL_SUPPLY


def test_remove_insufficient_is_noop():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        user = 666
        em.add_bb(user, 100, from_bank=True, taxable=False)
        assert em.remove_bb(user, 999, to_bank=True) is False
        assert em.get_bb(user) == 100
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bank_payment_records_pay_audit_and_conserves_supply():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        user = 665
        from config import BOT_ID

        assert em.add_bb(user, 500, reason="seed", taxable=False) is True
        assert em.remove_bb(
            user,
            200,
            reason="/pay to HMS Victory (Bank)",
            to_bank=True,
            record_pay_transfer=True,
        ) is True

        assert database.DatabaseManager.fetch_all(
            "SELECT payer_id, recipient_id, amount FROM pay_transfers"
        ) == [(str(user), str(BOT_ID), 200)]
        assert em.get_bb(user) == 300
        assert _total_supply(database) == TOTAL_SUPPLY


def test_atomic_bank_user_moves_update_balances_stats_and_ledgers():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        user = 667
        from config import BOT_ID

        initial_bank = em.get_bb(BOT_ID)
        assert em.add_bb(
            user, 500, reason="Blackjack payout", taxable=False
        ) is True
        assert em.remove_bb(
            user, 200, reason="Blackjack bet", to_bank=True
        ) is True

        assert em.get_bb(user) == 300
        assert em.get_bb(BOT_ID) == initial_bank - 300
        bank_row = database.DatabaseManager.fetch_one(
            "SELECT balance, total_revenue, total_blackjack_in, total_blackjack_out "
            "FROM bank WHERE id = 1"
        )
        assert bank_row == (initial_bank - 300, 200, 200, 500)
        assert _total_supply(database) == TOTAL_SUPPLY

        economy_log_count = database.DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM economy_transactions"
        )[0]
        assert economy_log_count == 4
        history = database.DatabaseManager.fetch_all(
            "SELECT balance FROM balance_history WHERE user_id = ? ORDER BY id",
            (str(user),),
        )
        assert history == [(500,), (300,)]
        statement = database.DatabaseManager.fetch_all(
            "SELECT amount, balance_after, reason FROM user_transactions "
            "WHERE user_id = ? ORDER BY id",
            (str(user),),
        )
        assert statement == [
            (500, 500, "Blackjack payout"),
            (-200, 300, "Blackjack bet"),
        ]


def test_taxed_credit_updates_tax_accounting_in_same_conserved_move():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        user = 668
        from config import BOT_ID

        assert em.add_bb(user, 12_000, reason="seed", taxable=False) is True
        bank_before = em.get_bb(BOT_ID)
        log_count_before = database.DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM economy_transactions"
        )[0]

        assert em.add_bb(user, 1_000, reason="taxed reward", taxable=True) is True

        # The 10k-20k bracket taxes this earning at 60%: 1,000 gross,
        # 600 returned as tax, and 400 credited to the user.
        assert em.get_bb(user) == 12_400
        assert em.get_bb(BOT_ID) == bank_before - 400
        bank_row = database.DatabaseManager.fetch_one(
            "SELECT balance, total_revenue, total_tax_collected FROM bank WHERE id = 1"
        )
        assert bank_row == (bank_before - 400, 600, 600)
        assert database.DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM economy_transactions"
        )[0] == log_count_before + 3
        last_statement = database.DatabaseManager.fetch_one(
            "SELECT amount, balance_after, reason FROM user_transactions "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (str(user),),
        )
        assert last_statement[0:2] == (400, 12_400)
        assert "gross: 1,000" in last_statement[2]
        assert "tax: -600" in last_statement[2]
        assert _total_supply(database) == TOTAL_SUPPLY


def test_bulk_handout_deduplicates_recipients_and_conserves_supply():
    with tempfile.TemporaryDirectory() as d:
        em, bank_manager, database = _fresh_economy(d)
        first, second = 669, 670
        from config import BOT_ID

        bank_before = em.get_bb(BOT_ID)
        assert bank_manager.BankManager.transfer_to_users(
            [first, second, first],
            250,
            description="Handout by test",
            user_reason="ukpadd test",
        ) is True

        assert em.get_bb(first) == 260
        assert em.get_bb(second) == 260
        assert em.get_bb(BOT_ID) == bank_before - 520
        assert database.DatabaseManager.fetch_one(
            "SELECT balance FROM bank WHERE id = 1"
        )[0] == bank_before - 520
        assert database.DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM user_transactions WHERE reason = 'ukpadd test'"
        )[0] == 2
        assert _total_supply(database) == TOTAL_SUPPLY


def test_atomic_tax_batch_clamps_combines_and_conserves_supply():
    with tempfile.TemporaryDirectory() as d:
        em, bank_manager, database = _fresh_economy(d)
        first, second = 671, 672
        from config import BOT_ID

        assert em.add_bb(first, 1_000, reason="seed", taxable=False) is True
        assert em.add_bb(second, 400, reason="seed", taxable=False) is True
        bank_before = em.get_bb(BOT_ID)
        stats_before = database.DatabaseManager.fetch_one(
            "SELECT total_revenue, total_tax_collected FROM bank WHERE id = 1"
        )
        log_count_before = database.DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM economy_transactions"
        )[0]

        charged = bank_manager.BankManager.collect_tax_batch(
            [(first, 300), (second, 900), (first, 50)],
            description="Inactivity tax",
            bank_description="Inactivity tax from 2 users",
        )

        assert charged == [
            (str(first), 350, 650),
            (str(second), 400, 0),
        ]
        assert em.get_bb(first) == 650
        assert em.get_bb(second) == 0
        assert em.get_bb(BOT_ID) == bank_before + 750
        bank_row = database.DatabaseManager.fetch_one(
            "SELECT balance, total_revenue, total_tax_collected FROM bank WHERE id = 1"
        )
        assert bank_row == (
            bank_before + 750,
            stats_before[0] + 750,
            stats_before[1] + 750,
        )
        assert database.DatabaseManager.fetch_one(
            "SELECT COUNT(*) FROM economy_transactions"
        )[0] == log_count_before + 3
        assert _total_supply(database) == TOTAL_SUPPLY


def test_wealth_tax_returns_to_bank_and_conserves():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        user = 777
        # Seat the user in a taxable bracket (>= 10k) without tripping high_roller (>= 30k).
        em.add_bb(user, 12_000, from_bank=True, taxable=False)
        before = em.get_bb(user)
        em.add_bb(user, 1000, from_bank=True, taxable=True)
        gained = em.get_bb(user) - before
        # Some of the 1000 is taxed back to the bank, so the user nets less than 1000...
        assert 0 < gained < 1000
        # ...but the closed-economy total never changes.
        assert _total_supply(database) == TOTAL_SUPPLY


def test_compute_wealth_tax_bounds():
    with tempfile.TemporaryDirectory() as d:
        em, _, _ = _fresh_economy(d)
        assert em.compute_wealth_tax(0, 100) == 0          # below the 10k threshold
        assert em.compute_wealth_tax(50_000, 0) == 0       # no earning, no tax
        tax = em.compute_wealth_tax(20_000, 1000)
        assert 0 <= tax <= 1000


def test_bank_cannot_overdraw():
    with tempfile.TemporaryDirectory() as d:
        _, bank_manager, database = _fresh_economy(d)
        assert bank_manager.BankManager.withdraw(TOTAL_SUPPLY + 1) is False
        assert _total_supply(database) == TOTAL_SUPPLY


def test_prediction_payout_conserves_supply():
    _require_discord()
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        from lib.economy.prediction_system import Prediction

        a, b = 888, 999
        em.add_bb(a, 10_000, from_bank=True, taxable=False)
        em.add_bb(b, 10_000, from_bank=True, taxable=False)
        assert _total_supply(database) == TOTAL_SUPPLY

        pred = Prediction(msg_id=1, title="t", options=["A", "B"], end_ts=0)
        # Keep stakes well under 50% of balance so the (client-dependent)
        # double_or_nothing badge path isn't taken.
        assert pred.stake(a, 1, 1000) is True
        assert pred.stake(b, 2, 1000) is True
        # Stakes are banked at bet time - supply still conserved.
        assert _total_supply(database) == TOTAL_SUPPLY

        payouts = pred.resolve(1)  # side A wins the 2000 pool
        assert payouts.get(a) == 2000
        assert em.get_bb(a) == 11_000   # 10000 - 1000 stake + 2000 winnings
        assert em.get_bb(b) == 9_000    # 10000 - 1000 stake
        assert _total_supply(database) == TOTAL_SUPPLY


def test_prediction_one_sided_winner_no_inflation():
    """If the side with zero backers is declared winner, the forfeited pool must
    stay in the bank (already there from bet time) - not be deposited again."""
    _require_discord()
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        from lib.economy.prediction_system import Prediction

        a = 1234
        em.add_bb(a, 10_000, from_bank=True, taxable=False)
        pred = Prediction(msg_id=2, title="t", options=["A", "B"], end_ts=0)
        pred.stake(a, 1, 1000)             # only side A is backed
        payouts = pred.resolve(2)          # declare the unbacked side B the winner
        assert payouts == {}
        assert em.get_bb(a) == 9_000       # stake forfeited
        assert _total_supply(database) == TOTAL_SUPPLY


def test_three_way_prediction_conserves_and_validates():
    _require_discord()
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        from lib.economy.prediction_system import Prediction

        a, b, c = 11, 22, 33
        for uid in (a, b, c):
            em.add_bb(uid, 10_000, from_bank=True, taxable=False)

        pred = Prediction(msg_id=3, title="3-way", options=["X", "Y", "Z"], end_ts=0)
        assert len(pred.bets) == 3
        assert pred.stake(a, 1, 1000) is True
        assert pred.stake(b, 2, 2000) is True
        assert pred.stake(c, 3, 3000) is True
        # A user may only back ONE outcome.
        assert pred.stake(a, 2, 500) is False
        # Out-of-range side is rejected.
        assert pred.stake(a, 4, 500) is False
        assert _total_supply(database) == TOTAL_SUPPLY

        # Side X (1000 staked) wins the whole 6000 pool.
        payouts = pred.resolve(1)
        assert payouts == {a: 6000}
        assert em.get_bb(a) == 15_000      # 10000 - 1000 + 6000
        assert em.get_bb(b) == 8_000       # lost 2000
        assert em.get_bb(c) == 7_000       # lost 3000
        assert _total_supply(database) == TOTAL_SUPPLY


def test_clean_options_validation():
    _require_discord()
    from lib.economy.prediction_system import _clean_options, MAX_PRED_OPTIONS
    assert _clean_options(["A", "B", None, None, None])[0] == ["A", "B"]
    assert _clean_options(["A", " ", None])[1] is not None          # too few
    assert _clean_options(["A", "a"])[1] is not None                # not distinct (case-insensitive)
    assert _clean_options(["A", "B", "C", "D", "E", "F"])[1] is not None  # too many
    ok, err = _clean_options(["Labour", "Tory", "Reform"])
    assert err is None and ok == ["Labour", "Tory", "Reform"]
    assert MAX_PRED_OPTIONS == 5


def test_transfer_awards_high_roller():
    with tempfile.TemporaryDirectory() as d:
        em, _, database = _fresh_economy(d)
        a, b = 222, 333
        database.DatabaseManager.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(a), 35000))
        assert database.DatabaseManager.transfer(a, b, 31000, reason="test") is True
        res = database.DatabaseManager.fetch_all("SELECT badge_id FROM user_badges WHERE user_id = ?", (str(b),))
        badge_ids = [r[0] for r in res]
        assert "high_roller" in badge_ids


if __name__ == "__main__":
    import traceback

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    skipped = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except _Skip as s:
            skipped += 1
            print(f"SKIP  {fn.__name__} ({s})")
        except Exception:
            failures += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures - skipped}/{len(tests)} passed, {skipped} skipped, {failures} failed")
    sys.exit(1 if failures else 0)
