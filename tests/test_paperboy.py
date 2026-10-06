"""Paperboy's day beyond the score: the theme, the three jobs and their multiplier, the streak, and
the wardrobe (lib/activities/paperboy.py)."""

import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from test_daily_score import UID, clock, _deliveries  # noqa: E402,F401

DAY = datetime.date(2026, 10, 7)


def test_every_day_of_the_week_has_its_theme():
    from lib.activities import paperboy, paperboy_sim
    seen = {paperboy_sim.theme_of((DAY + datetime.timedelta(days=k)).isoformat()) for k in range(7)}
    assert seen == set(paperboy_sim.RULES) == set(paperboy.THEMES)
    assert paperboy_sim.theme_of("2026-10-05") == "drizzle" and paperboy_sim.theme_of("2026-10-11") == "sunday"
    assert paperboy.theme("2026-10-06")["name"] == "Bin Day"


def test_three_jobs_a_day_the_same_for_everyone_led_by_the_theme():
    from lib.activities import paperboy
    for k in range(14):
        iso = (DAY + datetime.timedelta(days=k)).isoformat()
        jobs = paperboy.jobs_for(iso)
        assert len(jobs) == 3 and len({j["key"] for j in jobs}) == 3
        assert jobs[1]["key"] == paperboy.THEME_JOB[paperboy.paperboy_sim.theme_of(iso)]
        assert jobs == paperboy.jobs_for(iso)
        assert all(j["text"] and str(j["target"]) in j["text"].replace(",", "") or "AM" in j["text"] for j in jobs)


def test_jobs_count_from_replayed_runs_and_raise_the_multiplier(clock):
    from lib.activities import paperboy
    iso = DAY.isoformat()
    jobs = paperboy.jobs_for(iso)
    assert paperboy.mult(UID, iso) == 1
    big = {"score": 10_000, "steps": 120 * 300, "papers": 500,
           "stats": {k: 10_000 for k in ("got", "golden", "doubled", "ramps", "hops", "bins", "dogs", "ducks", "saves",
                                         "magnets", "doubles", "helmets")}}
    got = paperboy.record(UID, iso, big)
    # one huge run does every job except the ones that need several rounds
    multi = [j for j in jobs if j["stat"] == "rounds"]
    assert len(got["jobsDone"]) == 3 - len(multi) and got["mult"] == 1 + 3 - len(multi)
    assert paperboy.mult(UID, iso) == got["mult"]
    assert all(p["done"] for p in paperboy.progress(UID, iso) if p["stat"] != "rounds")
    # papers won for the jobs go to the wardrobe
    assert paperboy.balance(UID) == paperboy.JOB_PAPERS * len(got["jobsDone"]) + (paperboy.STREAK_PAPERS if got["mult"] == 4 else 0)


def test_a_one_round_job_counts_the_best_round_and_a_day_job_adds_up(clock):
    from lib.activities import paperboy
    iso = DAY.isoformat()
    small = {"score": 1, "steps": 120, "papers": 0, "stats": {"ramps": 1, "ducks": 1, "bins": 1, "got": 1}}
    paperboy.record(UID, iso, small)
    paperboy.record(UID, iso, small)
    got, _ = paperboy._row(UID, iso)
    for j in paperboy.jobs_for(iso):
        v = paperboy.run_values(small)[j["stat"]] if j["stat"] in paperboy.run_values(small) else 0
        assert got[j["key"]] == (v * 2 if j["scope"] == "day" else v), j["key"]


def test_the_streak_counts_days_with_every_job_done(clock):
    from database import DatabaseManager
    from lib.activities import paperboy
    for k, done in enumerate([3, 3, 1, 3, 3, 3]):
        iso = (DAY + datetime.timedelta(days=k)).isoformat()
        DatabaseManager.execute("INSERT INTO paperboy_jobs (user_id, date, progress, done) VALUES (?, ?, '{}', ?)", (str(UID), iso, done))
    last = DAY + datetime.timedelta(days=5)
    assert paperboy.streak(UID, last.isoformat()) == 3
    assert paperboy.streak(UID, (last + datetime.timedelta(days=1)).isoformat()) == 3     # today's still open
    assert paperboy.streak(UID, (last + datetime.timedelta(days=2)).isoformat()) == 0


def test_the_wardrobe_spends_papers_and_puts_things_on(clock):
    from database import DatabaseManager
    from lib.activities import paperboy
    k = paperboy.kit(UID)
    assert k["balance"] == 0 and k["wearing"]["bike"] == "bike-red" and "bike-red" in k["owned"]
    with pytest.raises(paperboy.WardrobeRefuse):
        paperboy.buy(UID, "bike-green")
    DatabaseManager.execute(
        "INSERT INTO paperboy_runs (user_id, date, started, ended, score, reported, game_time, papers, earned, trimmed) "
        "VALUES (?, ?, 1, 2, 10, 10, 1.0, 450, 0, 0)", (str(UID), DAY.isoformat()))
    k = paperboy.buy(UID, "bike-green")
    assert k["balance"] == 150 and k["wearing"]["bike"] == "bike-green" and "bike-green" in k["owned"]
    k = paperboy.wear(UID, "bike-red")
    assert k["wearing"]["bike"] == "bike-red" and k["balance"] == 150
    assert paperboy.buy(UID, "bike-green")["balance"] == 150           # buying what you own just wears it
    with pytest.raises(paperboy.WardrobeRefuse):
        paperboy.wear(UID, "bike-gold")
    with pytest.raises(paperboy.WardrobeRefuse):
        paperboy.buy(UID, "nonsense")


def test_a_finished_run_is_multiplied_by_the_jobs_done_before_it(clock):
    from lib.activities import daily_score, paperboy, paperboy_sim
    g = daily_score.PAPERBOY
    g.clock = clock
    iso = DAY.isoformat()
    seed = g.seed_for(iso)
    import json
    from database import DatabaseManager
    two = {j["key"]: j["target"] for j in paperboy.jobs_for(iso)[:2]}          # two of the day's jobs already done
    DatabaseManager.execute("INSERT INTO paperboy_jobs (user_id, date, progress, done) VALUES (?, ?, ?, 2)",
                            (str(UID), iso, json.dumps(two)))
    inputs = _deliveries(seed, 30)
    played = paperboy_sim.replay(seed, inputs, 120 * 30, iso)
    st = g.start(UID, DAY)
    assert st["mult"] == 3 and len(st["jobs"]) == 3 and st["theme"]["key"] == "works" and "balance" in st["kit"]
    clock["t"] += played["steps"] / 120 + 1
    s, r = g.finish(UID, DAY, {"score": played["score"] * 3, "time": played["steps"] / 120, "count": played["papers"],
                               "seed": seed, "run": st["run"], "inputs": inputs})
    assert r["height"] == played["score"] * 3 and not r["trimmed"] and "jobsDone" in r and r["mult"] >= 3
    # and the wardrobe's actions come through the game
    s = g.act(UID, DAY, "wear", {"item": "cap-red"})
    assert s["kit"]["wearing"]["cap"] == "cap-red"
    with pytest.raises(daily_score.Refuse):
        g.act(UID, DAY, "buy", {"item": "bike-gold"})
