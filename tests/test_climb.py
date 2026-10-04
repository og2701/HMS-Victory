"""Climb HMS Victory: only the day's best pays, the difference each time it's beaten, up to the
cap; a height is only believed as far as the clock allows; runs must be started first."""

import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

UID = 7171
DAY = datetime.date(2026, 10, 7)


@pytest.fixture
def climb(tmp_path, monkeypatch):
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    import lib.economy.economy_manager as economy
    economy._HIST_LAST.clear()
    from lib.activities import climb as C
    C._runs.clear()
    clock = {"t": 1_000_000.0}
    monkeypatch.setattr(C.time, "time", lambda: clock["t"])
    monkeypatch.setattr("lib.economy.reserve_policy.scale_reward", lambda n: n, raising=False)
    C.clock = clock
    return C


def _bb(uid):
    from lib.economy.economy_manager import get_bb
    return get_bb(uid)


def _run(C, height, seconds, uid=UID):
    C.start(uid, DAY)
    C.clock["t"] += seconds
    return C.finish(uid, DAY, {"height": height, "time": seconds, "bounces": 40, "seed": C.seed_for(DAY.isoformat())})


def test_the_state_carries_the_days_seed(climb):
    s = climb.state(UID, DAY)
    assert s["seed"] == climb.seed_for(DAY.isoformat()) and s["cap"] == climb.cap() and s["today"]["best"] == 0
    assert climb.seed_for("2026-10-07") != climb.seed_for("2026-10-08")


def test_only_improvements_pay(climb):
    before = _bb(UID)
    s, r = _run(climb, 100, 30)
    assert r["newBest"] and r["earned"] == 50 and s["today"]["paid"] == 50
    s, r = _run(climb, 80, 30)
    assert not r["newBest"] and r["earned"] == 0 and s["today"]["best"] == 100
    s, r = _run(climb, 140, 40)
    assert r["earned"] == 20 and s["today"]["paid"] == 70
    assert _bb(UID) - before == 70
    assert climb.home_card(UID, DAY) == {"best": 140, "paid": 70, "cap": climb.cap()}


def test_the_daily_cap(climb):
    s, r = _run(climb, 280, 60)
    assert r["earned"] == 140
    s, r = _run(climb, 500, 90)
    assert r["earned"] == 10 and s["today"]["paid"] == climb.cap() == 150
    s, r = _run(climb, 600, 100)
    assert r["newBest"] and r["earned"] == 0 and s["today"]["best"] == 600


def test_a_height_faster_than_the_clock_is_cut_down(climb):
    s, r = _run(climb, 900, 20)                       # 45 m/s: not a real climb
    assert r["trimmed"] and r["height"] == int(20 * climb.max_speed() + climb.SLACK)
    assert s["today"]["best"] == r["height"]


def test_runs_must_be_started_and_on_todays_rigging(climb):
    with pytest.raises(climb.Refuse):
        climb.finish(UID, DAY, {"height": 50, "time": 10, "seed": climb.seed_for(DAY.isoformat())})
    climb.start(UID, DAY)
    climb.clock["t"] += 20
    with pytest.raises(climb.Refuse):
        climb.finish(UID, DAY, {"height": 50, "time": 20, "seed": "yesterday"})


def test_runs_can_not_be_spammed(climb):
    climb.start(UID, DAY)
    with pytest.raises(climb.Refuse):
        climb.start(UID, DAY)
    climb.clock["t"] += 2
    climb.start(UID, DAY)


def test_rank_and_post_text(climb):
    _run(climb, 200, 60, uid=1)
    _run(climb, 120, 60, uid=2)
    assert climb.rank(DAY.isoformat(), 120) == 2
    assert climb._ordinal(1) == "1st" and climb._ordinal(2) == "2nd" and climb._ordinal(13) == "13th"
