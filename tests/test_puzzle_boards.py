"""The Wordle and Crossword board pictures: every game state draws, and the coin tells the truth."""

import datetime
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import config
from lib.features import crossword as X
from lib.features import wordle as W

DAY = datetime.date(2026, 10, 2)


def _crossword_states():
    keys = [X._key(e) for e in X._todays_puzzle(DAY)["entries"]]
    return {"fresh": {}, "mid": {"solved": keys[:3], "revealed": [f"{keys[4]}:0"], "wrong": 1},
            "done": {"solved": keys, "done": True}}


def test_the_crossword_board_draws_in_every_state(monkeypatch):
    from PIL import Image
    for state in _crossword_states().values():
        p = X._blank() | state
        monkeypatch.setattr(X, "_player", lambda _d, _u, p=p: p)
        img = Image.open(io.BytesIO(X.draw_board(1, DAY).getvalue()))
        assert img.width == 820 and img.height > 1000


def test_the_crossword_board_still_draws_without_the_bundled_font(monkeypatch):
    monkeypatch.setattr(X, "_ARCHIVO", "/nowhere/Archivo.ttf")
    monkeypatch.setattr(X, "_font_cache", {})
    monkeypatch.setattr(X, "_player", lambda _d, _u: X._blank())
    assert X.draw_board(1, DAY).getvalue()[:8] == b"\x89PNG\r\n\x1a\n"


def _wordle(monkeypatch, guesses, solved=False, done=False):
    monkeypatch.setattr(W, "_todays_word", lambda _d: "ghost")
    monkeypatch.setattr(W, "_player", lambda _d, _u: {"guesses": guesses, "solved": solved, "done": done})
    return W._board_html(1, DAY)


def test_the_wordle_coin_shows_what_the_next_guess_is_worth(monkeypatch):
    fresh = _wordle(monkeypatch, [])
    assert f"{config.WORDLE_REWARDS[0]:,} UKP" in fresh and "if you get it first try" in fresh
    mid = _wordle(monkeypatch, ["crane", "moist", "frost"])
    assert f"{config.WORDLE_REWARDS[3]:,} UKP" in mid and "if you get it next" in mid
    assert mid.count("row next") == 1


def test_the_wordle_coin_shows_the_win_or_the_word(monkeypatch):
    won = _wordle(monkeypatch, ["crane", "ghost"], solved=True, done=True)
    assert f"+{config.WORDLE_REWARDS[1]:,} UKP" in won and "solved in 2/6" in won
    assert "row next" not in won
    lost = _wordle(monkeypatch, ["crane", "moist", "frost", "joist", "hoist", "boost"], done=True)
    assert "GHOST" in lost and "was the word" in lost
