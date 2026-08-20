"""The daily anti-shuffle cap counts /pay and wagers lost together.

Losing a wager on purpose moves UKP to another member exactly as a payment does, so it has
to spend the same allowance - otherwise hitting the /pay cap and then throwing a game is a
way to keep going. Genuine losses spend it too: telling the two apart is not possible, and
measured against real history the cost is close to nothing (median day's wager losses were
18 UKP against a 10,000 cap).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from database import DatabaseManager
from lib.economy import economy_manager as E

ALICE, BOB, BANK = "9000001", "9000002", "9000003"


def _clear():
    for uid in (ALICE, BOB):
        DatabaseManager.execute("DELETE FROM pay_transfers WHERE payer_id = ?", (uid,))
        DatabaseManager.execute("DELETE FROM game_transfers WHERE loser_id = ?", (uid,))


def _paid(payer, recipient, amount, when=None):
    DatabaseManager.execute(
        "INSERT INTO pay_transfers (timestamp, payer_id, recipient_id, amount) VALUES (?, ?, ?, ?)",
        (int(when or time.time()), payer, recipient, int(amount)))


def _lost(loser, winner, amount, when=None):
    DatabaseManager.execute(
        "INSERT INTO game_transfers (timestamp, loser_id, winner_id, amount) VALUES (?, ?, ?, ?)",
        (int(when or time.time()), loser, winner, int(amount)))


def test_a_payment_and_a_lost_wager_draw_on_the_same_allowance():
    _clear()
    try:
        assert E.daily_transfer_used(ALICE, BANK) == 0
        _paid(ALICE, BOB, 400)
        assert E.daily_transfer_used(ALICE, BANK) == 400
        _lost(ALICE, BOB, 600)
        assert E.daily_transfer_used(ALICE, BANK) == 1000, "the wager loss was not counted"
    finally:
        _clear()


def test_winning_a_wager_costs_you_nothing():
    """The allowance is about money leaving, so only the loser spends it."""
    _clear()
    try:
        _lost(BOB, ALICE, 5000)
        assert E.daily_transfer_used(ALICE, BANK) == 0
        assert E.daily_transfer_used(BOB, BANK) == 5000
    finally:
        _clear()


def test_paying_the_bank_is_not_shuffling():
    _clear()
    try:
        _paid(ALICE, BANK, 9000)
        assert E.daily_transfer_used(ALICE, BANK) == 0, "money leaving circulation was counted"
    finally:
        _clear()


def test_yesterdays_movements_do_not_count():
    _clear()
    try:
        _paid(ALICE, BOB, 5000, when=time.time() - 36 * 3600)
        _lost(ALICE, BOB, 5000, when=time.time() - 36 * 3600)
        assert E.daily_transfer_used(ALICE, BANK) == 0
    finally:
        _clear()


def test_a_wager_is_refused_once_the_allowance_cannot_cover_losing_it():
    """Checked on the stake, before anyone commits - refusing at settle time would mean
    voiding a game that had already been played."""
    _clear()
    try:
        cap = int(getattr(config, "DAILY_PAY_CAP", 10000))
        assert E.wager_blocked_reason(ALICE, 500, BANK) is None
        _paid(ALICE, BOB, cap - 200)
        assert E.wager_blocked_reason(ALICE, 100, BANK) is None, "200 left, 100 should be fine"
        why = E.wager_blocked_reason(ALICE, 500, BANK)
        assert why and "200" in why, f"expected the remaining allowance in the message: {why}"
    finally:
        _clear()


def test_hitting_the_cap_by_losing_blocks_further_wagers():
    """The route this exists to close: cap reached, then keep moving money through games."""
    _clear()
    try:
        cap = int(getattr(config, "DAILY_PAY_CAP", 10000))
        _lost(ALICE, BOB, cap)
        why = E.wager_blocked_reason(ALICE, 1, BANK)
        assert why and "daily limit" in why
        assert E.daily_transfer_state(ALICE, BANK)[1] == 0
    finally:
        _clear()


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            print(f"ERROR {name}: {e!r}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
