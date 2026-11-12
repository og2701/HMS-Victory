"""Reserve policy tests: the discretionary throttle, the mint ceiling and the dividend pot.

These guard the invariants that matter if the bank ever gets into trouble:
  - obligations (casino wins, refunds, bonds) are NEVER scaled, only discretionary rewards;
  - the emergency mint cannot push total supply past MAX_TOTAL_SUPPLY unnoticed;
  - the dividend pot is an earmark, so no amount of pot activity changes total supply.

Runnable under pytest or straight from the stdlib (`python3 tests/test_reserve_policy.py`).
"""
import os
import sys

import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
import database
from database import DatabaseManager

BOT = "999000999"


def _fresh_db():
    """A throwaway database with the bank row seeded, wired into DatabaseManager."""
    fd, path = tempfile.mkstemp(prefix="reserve_test_", suffix=".db")
    os.close(fd)
    database.DB_FILE = path
    DatabaseManager._connection = None
    config.BOT_ID = int(BOT)
    database.init_db()
    DatabaseManager.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)",
                            (BOT, 200_000))
    DatabaseManager.execute("UPDATE bank SET balance = 200000, chat_reward_pot = 0 WHERE id = 1")
    return path


def _set_bank(amount):
    """Set the bank's float. BankManager cross-checks the BOT_ID balance against the bank
    accounting row and refuses to pay if they disagree, so both must move together."""
    DatabaseManager.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)",
                            (BOT, amount))
    DatabaseManager.execute("UPDATE bank SET balance = ? WHERE id = 1", (amount,))


def _supply():
    row = DatabaseManager.fetch_one("SELECT SUM(balance) FROM ukpence")
    return int(row[0]) if row and row[0] is not None else 0


def test_throttle_tiers_scale_only_when_reserves_fall():
    from lib.economy import reserve_policy as R
    _fresh_db()
    # Healthy reserves: rewards pay in full.
    _set_bank(400_000)
    assert R.throttle_multiplier() == 1.0
    assert R.scale_reward(100) == 100
    # Each tier bites at its ceiling. First match wins, so check the boundaries.
    for reserves, expected in ((110_000, 0.75), (60_000, 0.50), (45_000, 0.50), (5_001, 0.50), (5_000, 0.25),
                               (4_999, 0.25), (1, 0.25)):
        _set_bank(reserves)
        assert R.throttle_multiplier() == expected, reserves
    assert R.throttle_multiplier(110_001) == 1.0
    assert R.throttle_multiplier(60_001) == 0.75
    _set_bank(60_000)
    assert R.scale_reward(100) == 50
    _set_bank(110_000)
    assert R.scale_reward(100) == 75


def test_throttle_never_rounds_a_reward_away_to_nothing():
    """A 1-UKP chat reward at 0.25x must still pay 1, not silently vanish - a reward that
    pays zero reads as a broken feature rather than a tightened belt."""
    from lib.economy import reserve_policy as R
    _fresh_db()
    _set_bank(5_000)
    assert R.throttle_multiplier() == 0.25
    assert R.scale_reward(1) == 1
    assert R.scale_reward(3) == 1
    assert R.scale_reward(0) == 0        # a no-op stays a no-op
    assert R.scale_reward(-5) == -5      # negatives pass through untouched


def test_obligations_are_never_throttled():
    """The whole point of the discretionary flag: a won bet is a debt, not a gift."""
    from lib.economy import reserve_policy as R
    from lib.economy.economy_manager import add_bb, get_bb
    _fresh_db()
    _set_bank(5_000)
    assert R.throttle_multiplier() == 0.25

    add_bb(4242, 100, reason="Casino payout", from_bank=True, taxable=False)
    assert get_bb(4242) == 100                     # paid in full: no discretionary flag

    add_bb(4243, 100, reason="Tree watering reward", from_bank=True, discretionary=True)
    assert get_bb(4243) == 25                      # scaled


def test_mint_ceiling_bounds_total_supply():
    from lib.economy import reserve_policy as R
    _fresh_db()
    config.MAX_TOTAL_SUPPLY = 1_000_000
    _set_bank(200_000)                              # supply = 200,000
    assert R.mint_headroom() == 800_000
    assert R.may_mint(500_000)
    assert not R.may_mint(900_000)
    _set_bank(1_000_000)                            # supply at the ceiling
    assert R.mint_headroom() == 0
    assert not R.may_mint(1)


def test_dividend_pot_is_an_earmark_and_never_moves_supply():
    from lib.economy import reserve_policy as R
    _fresh_db()
    config.DEMURRAGE_DIVIDEND_ENABLED = True
    config.DEMURRAGE_DIVIDEND_PCT = 0.5
    before = _supply()

    assert R.dividend_pot() == 0
    assert R.fund_dividend_pot(1000) == 500         # half the run is earmarked
    assert R.dividend_pot() == 500
    assert R.spend_dividend(200) is True
    assert R.dividend_pot() == 300
    # the pot is a marker over the bank's own balance - none of this creates or destroys UKP
    assert _supply() == before


def test_dividend_pot_cannot_be_overdrawn():
    from lib.economy import reserve_policy as R
    _fresh_db()
    config.DEMURRAGE_DIVIDEND_ENABLED = True
    R.fund_dividend_pot(200)                        # -> 100 at 50%
    assert R.dividend_pot() == 100
    assert R.spend_dividend(101) is False           # more than the pot holds
    assert R.dividend_pot() == 100                  # ...and nothing was taken
    assert R.spend_dividend(100) is True
    assert R.dividend_pot() == 0
    assert R.spend_dividend(1) is False             # dry pot pays nothing


def test_dry_pot_stops_chat_rewards_but_a_disabled_pot_does_not():
    from lib.economy import reserve_policy as R
    _fresh_db()
    config.DEMURRAGE_DIVIDEND_ENABLED = True
    config.DEMURRAGE_DIVIDEND_FULL_POT = 2_500
    config.DEMURRAGE_DIVIDEND_MIN_RATE = 0.25

    assert R.dividend_rate() == 0.0                 # empty -> chat rewards paused
    R.fund_dividend_pot(5_000)                      # -> 2,500, a full pot
    assert R.dividend_rate() == 1.0
    R.spend_dividend(1_250)                         # half drained
    assert R.dividend_rate() == 0.5
    R.spend_dividend(1_200)                         # nearly dry, but floored
    assert R.dividend_rate() == 0.25

    # Turning the feature off must leave chat rewards exactly as they were before it
    # existed, not silently stop them because the pot happens to be empty.
    config.DEMURRAGE_DIVIDEND_ENABLED = False
    R.fund_dividend_pot(10_000)
    assert R.dividend_rate() == 1.0
    assert R.spend_dividend(999_999) is True        # not gated when disabled
    config.DEMURRAGE_DIVIDEND_ENABLED = True


def test_reserve_state_reports_the_floor_and_headroom():
    from lib.economy import reserve_policy as R
    _fresh_db()
    old_floor = config.RESERVE_FLOOR
    config.RESERVE_FLOOR = 80_000
    config.MAX_TOTAL_SUPPLY = 1_000_000
    _set_bank(200_000)
    st = R.reserve_state()
    assert st["reserves"] == 200_000
    assert st["above_floor"] == 120_000
    assert st["throttled"] is False
    assert st["mint_headroom"] == 800_000
    _set_bank(60_000)
    st = R.reserve_state()
    assert st["above_floor"] == -20_000             # below the floor reads negative
    assert st["throttled"] is True
    assert st["multiplier"] == 0.50                 # 60k free sits in the 60k tier
    config.RESERVE_FLOOR = old_floor


def _add_bond(user_id, principal, status="active"):
    DatabaseManager.execute(
        "INSERT INTO bonds (user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status) "
        "VALUES (?, ?, 30, 30, 0, 9999999999, ?)", (str(user_id), principal, status))


def test_bank_reserves_exclude_locked_bond_principal():
    """Savers' locked principal sits in the bank's balance but is owed back, so free
    reserves (what the throttle reads) leave it out. Only ACTIVE bonds count."""
    from lib.economy import reserve_policy as R
    _fresh_db()
    _set_bank(203_925)
    _add_bond(1, 5_000)
    _add_bond(2, 71_618)
    _add_bond(3, 9_000, status="matured")          # already paid back: not locked
    assert R.bank_balance() == 203_925
    assert R.locked_bond_principal() == 76_618
    assert R.bank_reserves() == 127_307
    # Ship-day check: the shifted tiers leave these free reserves unthrottled, as the raw
    # balance was under the old tiers.
    assert R.throttle_multiplier() == 1.0
    st = R.reserve_state()
    assert st["reserves"] == 127_307 and st["bonded"] == 76_618
    assert st["bank_balance"] == 203_925
    assert st["circulating"] == R.total_supply() - 203_925
    # Never negative, even if the book somehow exceeded the bank.
    _set_bank(10_000)
    assert R.bank_reserves() == 0


def test_casino_limits_read_whole_balance_unless_told_to_exclude_bonds():
    from lib.economy import reserve_policy as R
    _fresh_db()
    _set_bank(200_000)
    _add_bond(1, 100_000)
    old = getattr(config, "CASINO_EXPOSURE_EXCLUDES_BONDS", False)
    try:
        config.CASINO_EXPOSURE_EXCLUDES_BONDS = False
        assert R.max_casino_net_payout() == int(200_000 * config.MAX_EXPOSURE_PCT)
        config.CASINO_EXPOSURE_EXCLUDES_BONDS = True
        assert R.max_casino_net_payout() == int(100_000 * config.MAX_EXPOSURE_PCT)
        assert R.max_casino_bet(2.0, 250_000) == int(100_000 * config.MAX_EXPOSURE_PCT / 2.0)
    finally:
        config.CASINO_EXPOSURE_EXCLUDES_BONDS = old


def test_throttle_tiers_are_the_agreed_values():
    assert tuple(config.RESERVE_THROTTLE_TIERS) == ((5_000, 0.25), (60_000, 0.50), (110_000, 0.75))


def test_alert_seeds_silently_then_fires_once_per_change():
    from lib.economy import reserve_policy as R
    config.RESERVE_ALERT_ENABLED = True
    config.RESERVE_ALERT_HYSTERESIS = 2_000
    st, msg = R.check_throttle_alert({}, 200_000, 250_000, 50_000)
    assert msg is None and st == {"multiplier": 1.0}      # first run: seed only
    st2, msg = R.check_throttle_alert(st, 190_000, 0, 0)  # still off: nothing
    assert msg is None and st2 == st
    st, msg = R.check_throttle_alert(st, 100_000, 150_000, 50_000)
    assert "ENABLED" in msg and "100,000" in msg and "150,000" in msg and "50,000" in msg
    assert st == {"multiplier": 0.75}
    st2, msg = R.check_throttle_alert(st, 99_000, 99_000, 0)   # same tier: silent
    assert msg is None
    st, msg = R.check_throttle_alert(st, 50_000, 50_000, 0)
    assert "tier changed" in msg and "tightened" in msg and st["multiplier"] == 0.50
    st, msg = R.check_throttle_alert(st, 130_000, 130_000, 0)
    assert "DISABLED" in msg and st["multiplier"] == 1.0


def test_alert_hysteresis_stops_flapping_at_a_threshold():
    from lib.economy import reserve_policy as R
    config.RESERVE_ALERT_ENABLED = True
    config.RESERVE_ALERT_HYSTERESIS = 2_000
    st = {"multiplier": 0.75}
    # hover around 110k: throttle (real) flips, alert does not relax until 112,001+
    for r in (110_001, 111_000, 112_000, 110_500):
        st, msg = R.check_throttle_alert(st, r, r, 0)
        assert msg is None and st["multiplier"] == 0.75, r
    st, msg = R.check_throttle_alert(st, 112_001, 112_001, 0)
    assert msg and "DISABLED" in msg
    # tightening is immediate, and dipping back by less than the margin does not re-announce
    st, msg = R.check_throttle_alert(st, 110_000, 110_000, 0)
    assert msg and "ENABLED" in msg
    st, msg = R.check_throttle_alert(st, 111_500, 111_500, 0)
    assert msg is None


def test_alert_disabled_flag_is_silent():
    from lib.economy import reserve_policy as R
    config.RESERVE_ALERT_ENABLED = False
    st, msg = R.check_throttle_alert({"multiplier": 1.0}, 1_000, 1_000, 0)
    assert msg is None
    config.RESERVE_ALERT_ENABLED = True


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {t.__name__}: {e!r}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
