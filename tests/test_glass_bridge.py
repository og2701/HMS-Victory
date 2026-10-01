"""The Glass Bridge: the maths, and the ways a ladder game leaks money.

The dangerous bugs in a cash-out ladder are not "does the coin flip work". They are the
house edge drifting between steps so there is a clever place to stop, the payout being
credited twice, and a board that is both paid and resumable - which mints UKP on the next
boot, since the fixed supply assumes every payout happened exactly once.
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from commands.economy import glass_bridge as G

P1 = 4242


def _game(bet=100, bridge=None):
    g = G.GlassBridgeGame.new(P1, "Alice", 1, bet)
    if bridge:
        g.bridge = list(bridge)
    return g


def _walk(game, sides):
    return [game.take_step(s) for s in sides]


# --- the maths ------------------------------------------------------------------------
def test_the_edge_is_identical_at_every_step():
    """The whole point of deriving the ladder. If the edge drifted, one number of steps
    would be the right one to stop at and the game would be solvable."""
    edge = G._edge()
    for n in range(1, G._steps() + 1):
        # EV of taking one more step, as a fraction of what you are holding
        ev = 0.5 * G.multiplier_for(n) / G.multiplier_for(n - 1) if n > 1 else \
            0.5 * G.multiplier_for(1)
        assert abs(ev - (1 - edge)) < 1e-9, f"step {n} pays {ev}, not {1 - edge}"


def test_the_ladder_matches_the_published_numbers():
    """1.92, 3.68, 7.05 ... is 1.92^n rounded down, which is what a flat 4% produces."""
    for n, want in enumerate([1.92, 3.68, 7.05, 13.50, 25.80, 49.50, 94.00, 180.00], 1):
        got = G.multiplier_for(n)
        assert got >= want, f"step {n}: {got:.2f} is below the advertised {want}"
        assert got < want * 1.06, f"step {n}: {got:.2f} drifts too far above {want}"


def test_crossing_nothing_pays_nothing():
    """0 steps must be 0x, not 1x. A 1x floor would make stepping on and stopping a free
    refund, and the Cash Out button is hidden on step 0 for the same reason."""
    assert G.multiplier_for(0) == 0.0
    assert _game(bet=100).current_payout() == 0


def test_the_win_cap_bounds_the_bank():
    """184x on the max stake is the deepest exposure any game here has."""
    cap = int(getattr(config, "GLASS_MAX_WIN", 0) or 0)
    if cap <= 0:
        return
    g = _game(bet=config.GLASS_MAX_BET)
    g.step = G._steps()
    assert g.current_payout() == cap
    assert int(g.bet * g.multiplier()) > cap, "the cap is not actually binding"


# --- crossing -------------------------------------------------------------------------
def test_the_right_panel_advances_and_the_wrong_one_ends_it():
    g = _game(bridge=[G.LEFT] * 8)
    assert g.take_step(G.LEFT) == "on" and g.step == 1
    assert g.take_step(G.RIGHT) == "fell"
    assert g.state == "over" and g.outcome == "lose" and g.payout == 0


def test_falling_records_which_side_was_safe():
    """Being told which way you should have gone is the whole sting, and the report reads
    it back off the game rather than re-rolling."""
    g = _game(bridge=[G.RIGHT] * 8)
    g.take_step(G.LEFT)
    assert g.fell_on == G.LEFT and g.safe_side(g.step) == G.RIGHT
    assert "right" in G._status_text(g)


def test_reaching_the_far_side_banks_automatically():
    g = _game(bridge=[G.LEFT] * 8)
    results = _walk(g, [G.LEFT] * 8)
    assert results[-1] == "across"
    assert g.across() and g.state == "over" and g.outcome == "win"
    assert g.payout == g.payout_for(G._steps())


def test_a_finished_crossing_cannot_be_played_on():
    g = _game(bridge=[G.LEFT] * 8)
    g.take_step(G.RIGHT)
    assert g.take_step(G.LEFT) == "ignore"
    assert g.step == 0 and g.payout == 0


def test_cashing_out_pays_what_the_board_said():
    g = _game(bet=100, bridge=[G.LEFT] * 8)
    _walk(g, [G.LEFT, G.LEFT, G.LEFT])
    shown = g.current_payout()
    assert g.cash_out() == shown == int(100 * G.multiplier_for(3))
    assert g.state == "over" and g.outcome == "win"


def test_the_bridge_is_rolled_once_and_not_per_step():
    """A resumed game after a restart has to be the same bridge. Rolling per step would be
    invisible to the player but means the crossing is not a fixed thing."""
    g = _game()
    before = list(g.bridge)
    _walk(g, [g.safe_side(0), g.safe_side(1)])
    assert g.bridge == before


def test_a_resumed_game_keeps_the_same_bridge():
    g = _game(bridge=[G.LEFT, G.RIGHT] * 4)
    g.message_id = 99
    g.take_step(G.LEFT)
    back = G.GlassBridgeGame.from_dict(g.to_dict())
    assert back.bridge == g.bridge and back.step == g.step and back.bet == g.bet


def test_the_bridge_is_not_all_one_side():
    """A biased generator would be free money for anyone who noticed."""
    random.seed(7)
    sides = [s for _ in range(400) for s in G.GlassBridgeGame.new(P1, "a", 1, 5).bridge]
    left = sides.count(G.LEFT) / len(sides)
    assert 0.45 < left < 0.55, f"left came up {left:.0%} of the time"


# --- the board ------------------------------------------------------------------------
def test_the_board_never_shows_which_side_is_safe():
    """The one bug that would give the game away completely."""
    g = _game(bridge=[G.LEFT] * 8)
    g.take_step(G.LEFT)
    body = G._status_text(g)
    for banned in ("'L'", '"L"', "bridge=", str(g.bridge)):
        assert banned not in body, f"the board leaked the bridge: {body!r}"
    assert "left" not in body.lower() or "Panel" in body


def _labels(game):
    view = G.build_glass_view(game, with_image=False)
    return [getattr(c, "label", "") for row in view.children
            for c in getattr(row, "children", [])]


def test_cash_out_is_not_offered_before_anything_is_banked():
    assert not any("Cash Out" in (l or "") for l in _labels(_game()))
    g = _game(bridge=[G.LEFT] * 8)
    g.take_step(G.LEFT)
    assert any("Cash Out" in (l or "") for l in _labels(g))


def test_the_walkway_shows_progress_and_the_panel_that_went():
    g = _game(bridge=[G.LEFT] * 8)
    _walk(g, [G.LEFT, G.LEFT])
    assert G._walkway(g).count("🟩") == 2
    g.take_step(G.RIGHT)
    assert "💥" in G._walkway(g)


def test_a_finished_board_offers_play_again_and_no_way_to_step():
    g = _game(bridge=[G.LEFT] * 8)
    g.take_step(G.RIGHT)
    labels = _labels(g)
    assert "Play Again" in labels
    assert not any("Panel" in (l or "") for l in labels), labels


# --- the picture ----------------------------------------------------------------------
import asyncio

import pytest


@pytest.fixture
def no_db_ledger(monkeypatch):
    """The board's money ledger reads balances and casino history; the picture tests don't
    need either."""
    monkeypatch.setattr(G.felt, "player_ledger",
                        lambda *a, **k: [("Bet", "100"), ("Balance", "1,000"), ("Session", "0"), ("Career", "0")])


def _states():
    fresh = _game(bridge=[G.LEFT] * 8)
    mid = _game(bridge=[G.LEFT] * 8)
    _walk(mid, [G.LEFT] * 3)
    fell = _game(bridge=[G.LEFT] * 8)
    fell.take_step(G.RIGHT)
    done = _game(bridge=[G.LEFT] * 8)
    _walk(done, [G.LEFT] * 8)
    cashed = _game(bridge=[G.LEFT] * 8)
    _walk(cashed, [G.LEFT] * 2)
    cashed.cash_out()
    return {"fresh": fresh, "mid": mid, "fell": fell, "across": done, "cashed": cashed}


def test_the_board_builds_in_every_state(no_db_ledger):
    """A board that will not draw costs somebody their crossing, so every state has to
    survive the builder, including the one where nothing has happened yet."""
    want = {"fresh": "Pick a panel", "mid": "held", "fell": "The glass went",
            "across": "Across!", "cashed": "Cashed out"}
    for name, g in _states().items():
        page = G.build_board_html(g)
        assert "<svg" in page and "{{" not in page, name
        assert want[name] in page, f"{name} rail is missing {want[name]!r}"


def test_the_board_never_shows_the_panes_ahead(no_db_ledger):
    """Two bridges that differ only beyond where the player has got to must draw the same
    board, or the picture would give away the safe side."""
    a = _game(bridge=[G.LEFT, G.LEFT, G.LEFT, G.LEFT, G.LEFT, G.LEFT, G.LEFT, G.LEFT])
    b = _game(bridge=[G.LEFT, G.LEFT, G.RIGHT, G.RIGHT, G.LEFT, G.RIGHT, G.RIGHT, G.LEFT])
    b.game_id = a.game_id
    _walk(a, [G.LEFT, G.LEFT])
    _walk(b, [G.LEFT, G.LEFT])
    assert G.build_board_html(a) == G.build_board_html(b)


def test_a_render_failure_falls_back_to_text_rather_than_raising(monkeypatch):
    """The picture is a nicety; the crossing is the game."""
    g = _game()

    async def broken(_game):
        raise RuntimeError("no browser")

    monkeypatch.setattr(G, "render_board", broken)
    assert asyncio.run(G.board_files(g)) == ([], None)
    view, files = asyncio.run(G.build_glass_layout(g))
    assert files == []
    assert any(isinstance(c, G.discord.ui.Container) for c in view.children)
    assert "🪟" in G._status_text(g, walkway=True)


def test_pictures_can_be_switched_off(monkeypatch):
    import config
    monkeypatch.setattr(config, "GLASS_IMAGE_ENABLED", False, raising=False)
    assert asyncio.run(G.board_files(_game())) == ([], None)
    # with no picture the text panel has to carry the walkway itself
    assert "🟦" in G._status_text(_game(), walkway=True)


# --- the bank has to be able to see the money -----------------------------------------
def test_every_money_reason_says_glass_bridge():
    """BankManager routes a stake or payout to a game by matching the reason string. A
    reason that does not say "Glass Bridge" still moves the UKP but lands in the bank
    unattributed, so /bank-status and the casino totals silently under-count it - which is
    exactly what happened for the first few crossings."""
    import ast
    import pathlib as _p
    src = _p.Path("commands/economy/glass_bridge.py").read_text()
    tree = ast.parse(src)
    reasons = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fn not in ("remove_bb", "credit_from_bank"):
            continue
        for kw in node.keywords:
            if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                reasons.append(kw.value.value)
        for arg in node.args[2:]:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                reasons.append(arg.value)
    assert reasons, "found no stake or payout calls to check"
    for r in reasons:
        assert "Glass Bridge" in r, f"the bank cannot attribute {r!r}"


def test_the_bank_routes_the_reason_to_the_glass_columns():
    from lib.economy.bank_manager import BankManager
    amounts = BankManager._game_amounts(500, "Glass Bridge bet")
    assert sum(amounts) == 500, "the stake was not attributed to exactly one game"
    assert amounts[-1] == 500, "it did not land in the glass column"
    # and nothing else claims it
    assert BankManager._game_amounts(500, "Chest bet")[-1] == 0


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
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
