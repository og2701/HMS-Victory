"""Badge-reward idempotency + conservation tests.

pay_badge_reward credits via commands.economy.casino_base.credit_from_bank, which pulls in
discord at import. We stub that one symbol to the real add_bb so the test exercises the
actual ledger + bank transfer without discord. Runnable under pytest or the stdlib
(`python3 tests/test_badge_rewards.py`).
"""
import os
import sys
import types
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _fresh_db(tmp):
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    database.DB_FILE = os.path.join(tmp, "t.db")
    database.init_db()
    return database


def _stub_casino_base():
    # credit_from_bank -> real add_bb (from_bank=True, non-taxable); no discord needed.
    from lib.economy.economy_manager import add_bb
    for p in ("commands", "commands.economy"):
        if p not in sys.modules:
            m = types.ModuleType(p)
            m.__path__ = []
            sys.modules[p] = m
    cb = types.ModuleType("commands.economy.casino_base")
    cb.credit_from_bank = lambda uid, amount, reason="": add_bb(uid, amount, reason=reason, taxable=False)
    sys.modules["commands.economy.casino_base"] = cb


def run():
    with tempfile.TemporaryDirectory() as tmp:
        db = _fresh_db(tmp)
        _stub_casino_base()
        import config
        from lib.economy import badge_rewards as br
        from lib.economy.economy_manager import get_bb
        BANK = int(config.BOT_ID)

        def supply():
            return sum(r[0] for r in db.DatabaseManager.fetch_all("SELECT balance FROM ukpence"))

        uid = 424242
        assert supply() == 800_000, supply()
        bank0 = get_bb(BANK)

        # Gold badge (high_roller) -> 500, taken from the bank, supply conserved.
        assert config.BADGE_REWARDS["Gold"] == 500
        assert br.pay_badge_reward(uid, "high_roller") == 500
        assert get_bb(uid) == 500
        assert get_bb(BANK) == bank0 - 500
        assert supply() == 800_000

        # Idempotent: a re-run (and the live hook firing again) pays nothing.
        assert br.pay_badge_reward(uid, "high_roller") == 0
        assert get_bb(uid) == 500
        assert br.already_paid(uid, "high_roller") is True

        # Each tier pays its configured amount; unknown badge pays 0.
        assert br.pay_badge_reward(uid, "roaster") == 25            # Bronze
        assert br.pay_badge_reward(uid, "philanthropist") == 100    # Silver
        assert br.pay_badge_reward(uid, "echo") == 1000             # Secret
        assert br.pay_badge_reward(uid, "does_not_exist") == 0
        assert get_bb(uid) == 500 + 25 + 100 + 1000
        assert supply() == 800_000

        print(f"OK: user={get_bb(uid)} bank={get_bb(BANK)} supply={supply()} (conserved)")
        return True


_TESTS = [run]


def test_badge_rewards():
    assert run()


if __name__ == "__main__":
    ok = True
    for t in _TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            ok = False
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            ok = False
            print(f"ERROR {t.__name__}: {e!r}")
    sys.exit(0 if ok else 1)
