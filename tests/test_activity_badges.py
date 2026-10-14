"""The activity games' badges: who gets which, from a race, a run, a round, a solve or a kick."""

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy import activity_badges as AB  # noqa: E402


@pytest.fixture
def got(monkeypatch):
    awarded = []
    monkeypatch.setattr(AB, "_award", lambda uid, badge: awarded.append((str(uid), badge)))
    monkeypatch.setattr(AB, "all_rounder", lambda uid, iso: None)
    return awarded


def room(**kw):
    grid = [{"id": "111", "cpu": False}, {"id": "222", "cpu": False}, {"id": "cpu1", "cpu": True}]
    base = {"id": "r", "how": "race", "grid": grid, "players": [111, 222], "finish": {"111": 150.0, "222": 160.0, "cpu1": 170.0},
            "prog": {}, "laps": {"111": 49.0, "222": 47.5, "cpu1": 48.0}, "track": "village"}
    base.update(kw)
    return base


def test_a_kart_race_hands_out_learner_plates_the_win_and_the_fastest_lap(got, monkeypatch):
    monkeypatch.setattr(AB, "_grand_slam", lambda uid: got.append((str(uid), "checked slam")))
    AB.kart_race(room())
    assert ("111", "kart_learner") in got and ("222", "kart_learner") in got
    assert ("111", "kart_chequered") in got and ("111", "checked slam") in got
    assert ("222", "kart_purple") in got and ("111", "kart_purple") not in got


def test_no_chequered_flag_alone_and_no_fastest_lap_for_a_cpu(got, monkeypatch):
    monkeypatch.setattr(AB, "_grand_slam", lambda uid: None)
    AB.kart_race(room(players=[111], grid=[{"id": "111", "cpu": False}, {"id": "cpu1", "cpu": True}],
                      finish={"111": 150.0, "cpu1": 151.0}, laps={"111": 50.0, "cpu1": 48.0}))
    assert ("111", "kart_learner") in got and ("111", "kart_chequered") not in got
    assert not any(b == "kart_purple" for _, b in got)
    got.clear()
    AB.kart_race(room(how="refund"))
    assert got == []


def test_grand_slam_needs_a_win_on_every_track(got, monkeypatch):
    from database import DatabaseManager
    from lib.activities import kart
    tracks = list(kart.TRACKS)
    monkeypatch.setattr(DatabaseManager, "fetch_all", staticmethod(lambda q, a=(): [(t,) for t in tracks[:-1]]))
    AB._grand_slam(111)
    assert got == []
    monkeypatch.setattr(DatabaseManager, "fetch_all", staticmethod(lambda q, a=(): [(t,) for t in tracks]))
    AB._grand_slam(111)
    assert got == [("111", "kart_grand_slam")]


def test_the_secret_ones_go_by_the_secret_config(got, monkeypatch):
    from lib.economy import secret_config
    monkeypatch.setattr(secret_config, "bid", lambda k: {"a10": "s10", "a11": "s11"}.get(k))
    monkeypatch.setattr(secret_config, "param", lambda k: {"a10": 3, "a11": 4}.get(k))
    r = room()
    for _ in range(2):
        AB.kart_ouch(r, 111, "sheep")
    assert got == []
    AB.kart_ouch(r, 111, "sheep")
    assert got == [("111", "s10")]
    got.clear()
    AB.penalty_saved(222, 3)
    assert got == []
    AB.penalty_saved(222, 4)
    assert got == [("222", "s11")]


def test_a_sheep_only_counts_near_the_sheep(monkeypatch):
    from lib.activities import kart
    lap = kart.TRACKS["village"]["length"]
    r = {"track": "village", "prog": {"111": lap + 0.545 * lap + 10, "222": 0.2 * lap}}
    assert kart._near_hazard(r, "111", "sheep")
    assert not kart._near_hazard(r, "222", "sheep")
    assert not kart._near_hazard(r, "333", "sheep")


def test_countdown_wordle_crossword_and_paperboy(got):
    AB.countdown_round({"111": {"word": "abcdefghi", "valid": True}, "222": {"word": "abcdefghi", "valid": False},
                        "333": {"word": "abcdefgh", "valid": True}})
    assert got == [("111", "countdown_nine")]
    got.clear()
    AB.wordle_solved(111, 5, "2026-10-14")
    AB.wordle_solved(222, 6, "2026-10-14")
    assert got == [("222", "wordle_phew")]
    got.clear()
    AB.crossword_finished(111, {"revealed": []}, "2026-10-14")
    AB.crossword_finished(222, {"revealed": ["1a:0"]}, "2026-10-14")
    assert got == [("111", "crossword_no_peeking")]
    got.clear()
    AB.daily_run(111, "paperboy", "2026-10-14", {"crashedOn": "dog"})
    AB.daily_run(222, "paperboy", "2026-10-14", {"crashedOn": "bin"})
    AB.daily_run(333, "climb", "2026-10-14", None)
    assert got == [("111", "paperboy_dogs")]


def test_all_rounder_needs_every_daily_game(monkeypatch):
    awarded = []
    monkeypatch.setattr(AB, "_award", lambda uid, badge: awarded.append((str(uid), badge)))
    from lib.activities import daily_score
    from lib.features import crossword as X
    from lib.features import wordle as W
    runs = {"climb": 1, "spitfire": 1, "paperboy": 0}

    class Game:
        def __init__(self, k):
            self.k = k

        def day(self, uid, iso):
            return {"runs": runs[self.k]}

    monkeypatch.setattr(daily_score, "GAMES", {k: Game(k) for k in runs})
    monkeypatch.setattr(W, "_player", lambda iso, uid: {"guesses": ["crane"]})
    monkeypatch.setattr(X, "_player", lambda iso, uid: {"solved": [], "revealed": [], "wrong": 1})
    AB.all_rounder(111, "2026-10-14")
    assert awarded == []
    runs["paperboy"] = 2
    AB.all_rounder(111, "2026-10-14")
    assert awarded == [("111", "all_rounder")]


def test_paperboy_replays_say_what_they_crashed_on():
    from lib.activities import paperboy_sim
    # riding straight on with no steering, a run soon hits something: the replay says what
    out = paperboy_sim.replay("seed", [], 120 * 120)
    assert out["crashed"] and out["crashedOn"]
