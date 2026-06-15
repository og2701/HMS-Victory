"""Mines maths + state-machine tests.

mines.py imports discord and the casino economy layer at module load, neither of
which is needed for the pure game logic. We stub those modules so the *real*
MinesGame class can be imported and exercised directly. Runnable under pytest or
straight from the stdlib (`python3 tests/test_mines.py`).
"""
import os
import sys
import types
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _install_stubs():
    discord = types.ModuleType("discord")
    discord.ButtonStyle = types.SimpleNamespace(secondary=0, success=1, danger=2, primary=3, link=4)
    discord.Colour = type("Colour", (), {"__init__": lambda self, v=0: None})
    discord.Interaction = type("Interaction", (), {})
    discord.File = type("File", (), {})
    discord.utils = types.SimpleNamespace(escape_markdown=lambda s: s)
    ui = types.ModuleType("discord.ui")

    class _Item:
        def __init__(self, *a, **k):
            self.callback = None

        def add_item(self, *a, **k):
            return self
    for name in ("Button", "ActionRow", "Container", "TextDisplay", "LayoutView",
                 "MediaGallery", "Section", "View", "Modal"):
        setattr(ui, name, type(name, (_Item,), {}))
    discord.ui = ui
    sys.modules["discord"] = discord
    sys.modules["discord.ui"] = ui

    econ = types.ModuleType("lib.economy.economy_manager")
    econ.get_bb = lambda uid: 0
    econ.remove_bb = lambda *a, **k: True
    sys.modules["lib.economy.economy_manager"] = econ

    for pkg in ("commands", "commands.economy"):
        if pkg not in sys.modules:
            mod = types.ModuleType(pkg)
            mod.__path__ = []
            sys.modules[pkg] = mod
    cb = types.ModuleType("commands.economy.casino_base")
    cb.credit_from_bank = lambda *a, **k: None
    cb.reject_if_maintenance = lambda *a, **k: False
    cb.save_state = lambda *a, **k: None
    cb.delete_state = lambda *a, **k: None
    cb.ACCENT = None
    sys.modules["commands.economy.casino_base"] = cb


def _load_mines():
    _install_stubs()
    path = os.path.join(ROOT, "commands", "economy", "mines.py")
    spec = importlib.util.spec_from_file_location("mines_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MINES = _load_mines()
import config

EDGE = getattr(config, "MINES_HOUSE_EDGE", 0.02)


def _game(bet=100, mines=3, mine_positions=None):
    if mine_positions is None:
        mine_positions = list(range(mines))
    return MINES.MinesGame("gid", 1, "p", 1, bet, mines, mine_positions)


def _survive_prob(k, m, T=25):
    p = 1.0
    for i in range(k):
        p *= (T - m - i) / (T - i)
    return p


def test_zero_reveal_multiplier_is_one_minus_edge():
    g = _game(mines=3)
    assert abs(g.multiplier(0) - (1 - EDGE)) < 1e-12


def test_ev_is_constant_regardless_of_strategy():
    # For ANY mine count and ANY number of reveals k, the unconditional EV of
    # "reveal k tiles then cash out" must equal stake * (1 - edge): the house edge
    # does not depend on how greedy the player is.
    for m in range(1, 25):
        g = _game(bet=1, mines=m)
        for k in range(0, g.safe_tiles + 1):
            ev_fraction = _survive_prob(k, m) * g.multiplier(k)
            assert abs(ev_fraction - (1 - EDGE)) < 1e-9, (m, k, ev_fraction)


def test_multiplier_strictly_increases():
    g = _game(mines=5)
    last = -1.0
    for k in range(0, g.safe_tiles + 1):
        cur = g.multiplier(k)
        assert cur > last, (k, cur, last)
        last = cur


def test_reveal_gem_then_mine():
    g = _game(bet=100, mines=3, mine_positions=[0, 1, 2])
    assert g.reveal(5) == "gem"
    assert g.revealed_count == 1
    assert g.state == "playing"
    assert g.reveal(0) == "mine"          # tile 0 is a mine
    assert g.state == "over" and g.outcome == "lose" and g.hit_mine == 0


def test_reveal_already_revealed_is_ignored():
    g = _game(mine_positions=[0, 1, 2])
    assert g.reveal(5) == "gem"
    assert g.reveal(5) == "ignore"        # same tile again
    assert g.revealed_count == 1


def test_clearing_board_auto_cashes_out():
    g = _game(bet=10, mines=24, mine_positions=list(range(24)))  # 1 safe tile (#24)
    res = g.reveal(24)
    assert res == "win"
    assert g.state == "over" and g.outcome == "win"
    # 1 gem, 24 mines: fair mult = 25/1, scaled by edge.
    assert g.payout == min(int(10 * (1 - EDGE) * (25 / 1)), getattr(config, "MINES_MAX_WIN", 100_000))


def test_payout_is_capped():
    cap = getattr(config, "MINES_MAX_WIN", 100_000)
    # 3 mines, clear all 22 safe tiles at max bet -> raw multiplier ~2300x, far over cap.
    g = _game(bet=10_000, mines=3, mine_positions=[0, 1, 2])
    assert g.payout_for(22) == cap
    assert g.multiplier(22) * 10_000 > cap   # sanity: it really would have exceeded the cap


def test_cashout_uses_current_reveals():
    g = _game(bet=100, mines=3, mine_positions=[0, 1, 2])
    g.reveal(5)
    g.reveal(6)
    expected = g.current_payout()
    assert g.cash_out() == expected
    assert g.state == "over" and g.outcome == "win"


def test_roundtrip_serialisation():
    g = _game(bet=250, mines=4, mine_positions=[3, 8, 12, 20])
    g.reveal(0)
    g.reveal(1)
    d = g.to_dict()
    assert d["type"] == "mines"
    g2 = MINES.MinesGame.from_dict(d)
    assert g2.mine_positions == g.mine_positions
    assert g2.revealed == g.revealed
    assert g2.bet == g.bet and g2.mines == g.mines
    assert abs(g2.multiplier() - g.multiplier()) < 1e-12


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
