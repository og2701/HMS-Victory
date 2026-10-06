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


def _fair(g, score, day=DAY):
    """Seconds a real run to this score would take: a little longer than a perfect one."""
    seed, t = g.seed_for(day.isoformat()), 0
    while g.limit(seed, t) < score:
        t += 1
    return t + 5


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
    from lib.activities.daily_score import GRACE
    s, r = _run(game, 900, 20)
    best_possible = game.limit(game.seed_for(DAY.isoformat()), 20 + GRACE)
    assert r["trimmed"] and r["height"] == best_possible < int(20 * game.speed() + game.slack)
    s, r = _run(game, best_possible, 20)
    assert not r["trimmed"]


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
    _run(game, 60, _fair(game, 60, yesterday), uid=5, day=yesterday)
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


def _deliveries(seed: str, secs: float) -> list[list[int]]:
    """Moves that get round the first few things in the road, from the bot's own copy: change lane
    away from anything in the way, a little before it, and hop anything low that's still in the way."""
    from lib.activities import paperboy_sim as S
    run, inputs = S.Run(seed, S.theme_of(DAY.isoformat())), []
    while not run.over and run.step < secs * 120:
        v = S.speed_at(run.step)
        ahead = [o for o in run.street.obs if 0 < run.ob_d(o) - o["hl"] - run.dist < 900 + v * 30]
        close = [o for o in ahead if run.ob_d(o) - o["hl"] - run.dist < 900 + v * 8 and abs(run.x - run.ob_x(o)) < 45 + o["hw"]]
        if close and all(o.get("height") == "low" for o in close) and run.height() == 0:
            run.input(S.UP)
            inputs.append([run.step, S.UP])
        if ahead and abs(run.x - S.LANES[run.lane]) < 4:
            for lane in (run.lane - 1, run.lane + 1, run.lane - 2, run.lane + 2):
                if 0 <= lane <= 2 and not any(abs(S.LANES[lane] - run.ob_x(o)) < 45 + o["hw"] for o in ahead):
                    code = S.LEFT if lane < run.lane else S.RIGHT
                    for _ in range(abs(lane - run.lane)):
                        run.input(code)
                        inputs.append([run.step, code])
                    break
        run.tick()
    return inputs


def test_paperboy_scores_what_the_rules_give_for_the_runs_inputs(clock):
    from lib.activities import daily_score, paperboy_sim
    g = daily_score.PAPERBOY
    g.clock = clock
    seed = g.seed_for(DAY.isoformat())
    from lib.activities import paperboy
    iso = DAY.isoformat()
    inputs = _deliveries(seed, 60)
    played = paperboy_sim.replay(seed, inputs, 120 * 60, iso)
    assert played["score"] > 0
    st = g.start(UID, DAY)
    clock["t"] += played["steps"] / 120 + 1
    s, r = g.finish(UID, DAY, {"score": played["score"], "time": played["steps"] / 120, "count": played["papers"],
                               "seed": seed, "run": st["run"], "inputs": inputs})
    assert r["height"] == played["score"] and not r["trimmed"]          # no jobs done yet: x1
    # a page that claims more than its inputs earn gets what they earn (times the jobs done so far)
    clock["t"] += 5
    times = paperboy.mult(UID, iso)
    st = g.start(UID, DAY)
    clock["t"] += played["steps"] / 120 + 1
    s, r = g.finish(UID, DAY, {"score": 9999, "time": played["steps"] / 120, "count": 1, "seed": seed, "run": st["run"],
                               "inputs": inputs})
    assert r["height"] == played["score"] * times and r["trimmed"]
    # and with no moves at all, only what riding straight on earns
    clock["t"] += 5
    times = paperboy.mult(UID, iso)
    st = g.start(UID, DAY)
    clock["t"] += 30
    s, r = g.finish(UID, DAY, {"score": 5000, "time": 30, "count": 9, "seed": seed, "run": st["run"]})
    assert r["height"] == paperboy_sim.replay(seed, [], 120 * 30, iso)["score"] * times < 5000 and r["trimmed"]


def test_a_paperboy_replay_is_cut_off_at_the_bots_clock(clock):
    from lib.activities import daily_score, paperboy_sim
    g = daily_score.PAPERBOY
    g.clock = clock
    seed = g.seed_for(DAY.isoformat())
    inputs = _deliveries(seed, 60)
    full = paperboy_sim.replay(seed, inputs, 120 * 60, DAY.isoformat())
    st = g.start(UID, DAY)
    clock["t"] += 10                      # the page says the run took far longer than the bot saw
    s, r = g.finish(UID, DAY, {"score": full["score"], "time": full["steps"] / 120, "count": full["papers"],
                               "seed": seed, "run": st["run"], "inputs": inputs})
    assert r["height"] == paperboy_sim.replay(seed, inputs, int((10 + daily_score.GRACE) * 120), DAY.isoformat())["score"] < full["score"]
