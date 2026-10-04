"""The daily score games (Climb HMS Victory, Spitfire): only the day's best pays, the difference
each time it's beaten, up to the cap; a score is only believed as far as the clock allows; runs
need the bot's receipt from their start, survive a restart, and count once."""

import asyncio
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

UID = 7171
DAY = datetime.date(2026, 10, 7)


@pytest.fixture
def clock(tmp_path, monkeypatch):
    import database
    if database.DatabaseManager._connection is not None:
        database.DatabaseManager._connection.close()
        database.DatabaseManager._connection = None
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "test.db"))
    database.init_db()
    import lib.economy.economy_manager as economy
    economy._HIST_LAST.clear()
    from lib.activities import daily_score
    for g in daily_score.GAMES.values():
        for d in (g._runs, g._last_start, g._sittings, g._posts):
            d.clear()
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(daily_score.time, "time", lambda: now["t"])
    return now


@pytest.fixture(params=["climb", "spitfire"])
def game(request, clock):
    from lib.activities import daily_score
    g = daily_score.GAMES[request.param]
    g.clock = clock
    return g


@pytest.fixture
def climb(clock):
    from lib.activities import daily_score
    g = daily_score.CLIMB
    g.clock = clock
    return g


def _bb(uid):
    from lib.economy.economy_manager import get_bb
    return get_bb(uid)


def _run(g, score, seconds, uid=UID, day=DAY):
    st = g.start(uid, day)
    g.clock["t"] += seconds
    return g.finish(uid, day, {"score": score, "time": seconds, "count": 40, "seed": g.seed_for(day.isoformat()),
                               "run": st["run"]})


def _fair(g, score):
    """Seconds a real run to this score would take."""
    return score / g.speed() + 5


def test_the_state_carries_the_days_seed(game):
    s = game.state(UID, DAY)
    assert s["game"] == game.key and s["seed"] == game.seed_for(DAY.isoformat()) and s["today"]["best"] == 0
    assert game.seed_for("2026-10-07") != game.seed_for("2026-10-08")


def test_only_improvements_pay_up_to_the_cap(game):
    before = _bb(UID)
    low = 10
    s, r = _run(game, low, _fair(game, low))
    assert r["newBest"] and r["earned"] == int(low * game.pay_rate())
    s, r = _run(game, low - 2, _fair(game, low))
    assert not r["newBest"] and r["earned"] == 0
    big = int(game.pay_cap() / game.pay_rate()) + 20
    s, r = _run(game, big, _fair(game, big))
    assert s["today"]["paid"] == game.pay_cap() and _bb(UID) - before == game.pay_cap()
    assert game.home_card(UID, DAY) == {"best": big, "paid": game.pay_cap(), "cap": game.pay_cap()}


def test_a_score_faster_than_the_clock_is_cut_down(game):
    s, r = _run(game, 900, 20)
    assert r["trimmed"] and r["height"] == int(20 * game.speed() + game.slack)


def test_runs_need_a_genuine_receipt(game):
    seed = game.seed_for(DAY.isoformat())
    with pytest.raises(game.Refuse if hasattr(game, "Refuse") else Exception):
        game.finish(UID, DAY, {"score": 5, "time": 10, "seed": seed})
    st = game.start(UID, DAY)
    game.clock["t"] += 20
    from lib.activities.daily_score import Refuse
    with pytest.raises(Refuse):
        game.finish(UID + 1, DAY, {"score": 5, "time": 20, "seed": seed, "run": st["run"]})
    with pytest.raises(Refuse):
        game.finish(UID, DAY, {"score": 5, "time": 20, "seed": "yesterday", "run": st["run"]})
    game.clock["t"] += 31 * 60
    with pytest.raises(Refuse):
        game.finish(UID, DAY, {"score": 5, "time": 20, "seed": seed, "run": st["run"]})


def test_a_receipt_survives_a_restart_and_counts_once(game):
    st = game.start(UID, DAY)
    game._runs.clear()                        # the bot restarted mid-run
    game.clock["t"] += 40
    body = {"score": 20, "time": 40, "count": 30, "seed": game.seed_for(DAY.isoformat()), "run": st["run"]}
    s, r = game.finish(UID, DAY, body)
    assert r["newBest"] and r["earned"] == int(20 * game.pay_rate())
    s, r = game.finish(UID, DAY, body)        # sent again after a lost reply
    assert r.get("again") and r["earned"] == 0


def test_runs_cant_be_spammed(game):
    from lib.activities.daily_score import Refuse
    game.start(UID, DAY)
    with pytest.raises(Refuse):
        game.start(UID, DAY)
    game.clock["t"] += 2
    game.start(UID, DAY)


def test_the_games_keep_separate_books(clock):
    from lib.activities import daily_score
    c, sp = daily_score.CLIMB, daily_score.SPITFIRE
    c.clock = sp.clock = clock
    _run(c, 100, 30)
    assert sp.day(UID, DAY.isoformat())["best"] == 0 and c.day(UID, DAY.isoformat())["best"] == 100


def test_the_leaderboard(game):
    for uid, sc in ((1, 40), (2, 25), (3, 25), (UID, 12)):
        _run(game, sc, _fair(game, sc), uid=uid)
    b = game.board(UID, DAY)
    assert [(r["uid"], r["height"], r["rank"]) for r in b["today"]] == [
        ("1", 40, 1), ("2", 25, 2), ("3", 25, 2), (str(UID), 12, 4)]
    assert b["you"]["today"] == {"height": 12, "rank": 4} and b["players"]["today"] == 4
    yesterday = DAY - datetime.timedelta(days=1)
    _run(game, 60, _fair(game, 60), uid=5, day=yesterday)
    b = game.board(UID, DAY)
    assert b["allTime"][0]["uid"] == "5" and b["you"]["allTime"]["rank"] == 5
    assert game.state(UID, DAY)["rank"] == 4


def test_a_new_best_posts_once_and_then_updates(game, monkeypatch):
    from lib.activities import launcher
    calls = []

    async def fake(channel_id, text, key, message_id=None, png=None):
        calls.append((key, text, message_id))
        return message_id or 999

    async def no_picture(*a):
        return None

    monkeypatch.setattr(launcher, "announce_or_edit", fake)
    monkeypatch.setattr(game, "picture", no_picture)

    async def go():
        _run(game, 10, _fair(game, 10))
        game.post_best(UID, 55, DAY.isoformat())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        _run(game, 15, _fair(game, 15))
        game.post_best(UID, 55, DAY.isoformat())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert calls[0][0] == game.key and calls[0][2] is None and "10" in calls[0][1] and "1st today" in calls[0][1]
    assert calls[1][2] == 999 and "15" in calls[1][1]


def test_old_climb_pages_still_count(climb):
    """Pages from before the shared games sent height and bounces, and no receipt."""
    climb.start(UID, DAY)
    climb.clock["t"] += 30
    s, r = climb.finish(UID, DAY, {"height": 100, "time": 30, "bounces": 40, "seed": climb.seed_for(DAY.isoformat())})
    assert r["newBest"] and s["today"]["best"] == 100


def test_the_post_pictures_draw():
    from lib.activities import daily_card
    for key in ("climb", "spitfire"):
        html = daily_card.card_html(key, name="Pooja", avatar=None, score=23, rank=1, players=4, paid=43,
                                    top=[("Pooja", 23, True)])
        assert "Pooja" in html and daily_card.STYLES[key]["big"] in html
