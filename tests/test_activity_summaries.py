"""Each casino game's own session summary: every round keeps what its picture needs, and the
picture draws from real rounds played through the adapters."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.activities.casino import sessions, summaries
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_activity_casino_games import UID, em, play  # noqa: E402,F401  (em is a fixture)

# How to see a round through to the end: the deal, then the moves to make while it's still going.
ROUNDS = {
    "plinko": ({"bet": 100, "risk": "medium"}, []),
    "slots": ({"bet": 10}, []),
    "roulette": ({"bet": 30, "bets": {"straight:17": 10, "red": 10, "dozen2": 10}}, []),
    "higherlower": ({"bet": 100}, [("higher", {}), ("cashout", {})]),
    "videopoker": ({"bet": 100}, [("draw", {"held": []})]),
    "reddog": ({"bet": 100}, [("call", {})]),
    "tcp": ({"bet": 100}, [("play", {})]),
    "blackjack": ({"bet": 100}, [("stand", {})]),
    "mines": ({"bet": 100}, [("reveal", {"tile": 7}), ("reveal", {"tile": 13}), ("cashout", {})]),
    "chest": ({"bet": 100}, [("upgrade", {}), ("cashout", {})]),
    "glass": ({"bet": 100}, [("right", {}), ("cashout", {})]),
    "blockade": ({"bet": 100}, [("sail", {}), ("sail", {}), ("anchor", {})]),
    "darts": ({"bet": 100}, [("throw", {}), ("throw", {}), ("stand", {})]),
    "penalty": ({"bet": 100}, [("shoot", {"spot": "tl"}), ("shoot", {"spot": "br"}), ("cashout", {})]),
}


def _one_round(key):
    deal, moves = ROUNDS[key]
    out = play(key, "deal", deal)
    for action, body in moves:
        if out["table"]["over"]:
            break
        out = play(key, action, body)
    assert out["table"]["over"], f"{key} round didn't finish"


def _sitting(key):
    return next(s for s in sessions._sittings.values() if s.key == key)


@pytest.mark.parametrize("key", sorted(ROUNDS))
def test_every_game_keeps_what_its_summary_draws_and_draws_it(em, key):
    for _ in range(3):
        _one_round(key)
    s = _sitting(key)
    assert len(s.history) == 3
    assert all(isinstance(h.get("d"), dict) for h in s.history), s.history
    page = summaries.summary_html(key, s.unit, s.history, 2)
    assert page and 'class="card"' in page
    assert "3 " + s.unit.upper() in page            # the corner says how many rounds


def test_a_sitting_with_no_digests_falls_back_to_the_plain_card():
    history = [{"net": 50, "staked": 100, "payout": 150, "multiple": 1.5, "outcome": "", "big": False}] * 3
    assert summaries.summary_html("plinko", "balls", history, 1) is None
    assert summaries.summary_html("nosuchgame", "rounds", history, 1) is None


def test_a_broken_digest_is_dropped_not_raised():
    assert summaries.digest("plinko", {"slot": 3}) is None
    assert summaries.digest("plinko", None) is None


def test_penalties_remember_every_kick_across_a_save():
    from commands.economy import penalty as PE
    g = PE.PenaltyGame.new(1, "A", None, 100)
    g.kick("tl")
    g.kick("br")
    again = PE.PenaltyGame.from_dict(g.to_dict())
    assert [k[0] for k in again.kicks] == ["tl", "br"]
    assert all(k[1] in ("goal", "save") for k in again.kicks)


def test_penny_falls_cups_are_drawn_from_coins_in_and_out():
    view = {"dropped": 40, "won": 52, "goldsWon": 1}
    d = summaries.digest("pennyfalls", view)
    assert d == {"fed": 40, "won": 52, "golds": 1}
    history = [{"net": 120, "staked": 400, "payout": 520, "multiple": 1.3, "outcome": "", "big": False, "d": d},
               {"net": -150, "staked": 300, "payout": 150, "multiple": 0.5, "outcome": "", "big": False,
                "d": {"fed": 30, "won": 15, "golds": 0}}]
    page = summaries.summary_html("pennyfalls", "cups", history, 4)
    assert "1 GOLD COIN" in page and "−30" in page
