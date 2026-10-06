"""Paperboy's rules, the bot's copy, against runs recorded through the page's copy
(ukplace-activities scripts/paperboy-vectors.ts): the same inputs must give exactly the same run."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.activities import paperboy_sim as S  # noqa: E402

VECTORS = os.path.join(os.path.dirname(__file__), "data", "paperboy_vectors.json")


def test_the_bot_replays_runs_exactly_as_the_page_played_them():
    with open(VECTORS) as f:
        runs = json.load(f)
    assert len(runs) >= 9
    for v in runs:
        assert S.replay(v["seed"], v["inputs"], v["maxSteps"], theme=v["theme"]) == v["result"], (v["seed"], v["theme"])


def test_inputs_that_arent_inputs_are_ignored():
    assert S.clean_inputs([[5, 1], [3, 9], ["x", 1], [-1, 2], [7, True], "nope", [8, 4], [9, 5]]) == [[5, 1], [8, 4]]
    assert S.clean_inputs("nope") == []


def test_riding_scores_a_point_every_ten_metres_and_a_point_a_paper():
    run = S.Run("f00d")
    while run.step < 120 * 3:                       # the first 36 m are clear
        run.tick()
    assert not run.over and run.score == run.dist // 10000 + run.papers
    # with no moves at all you ride straight into the first thing in the middle lane
    played = S.replay("f00d", [], 120 * 60)
    assert played["crashed"] and played["steps"] < 120 * 60


def test_a_helmet_takes_one_knock_and_then_its_gone():
    bare = S.replay("f00d", [], 120 * 60)
    run = S.Run("f00d")
    run.helmet = True
    while not run.over and run.step < bare["steps"] + 1:
        run.tick()
    assert not run.over and not run.helmet          # rode on through the first thing in the way
    assert any(o["hit"] for o in run.street.obs)
    while not run.over and run.step < 120 * 120:
        run.tick()
    assert run.over                                  # the next one ends it


def test_the_magnet_pulls_in_papers_from_every_lane():
    bare, pulled = S.Run("2026-12-25:xmas"), S.Run("2026-12-25:xmas")
    pulled.magnet_to = 10 ** 9
    while not bare.over and not pulled.over:          # riding straight on, up to the first crash
        bare.tick()
        pulled.tick()
    assert pulled.papers > bare.papers


def test_a_ramp_throws_you_over_a_wall_of_traffic():
    run = S.Run("0123abcd")
    while not any(rp["d"] > run.dist + 30000 for rp in run.street.ramps):
        run.street.ensure(run.street.ob_to + 1)
    ramp = next(rp for rp in run.street.ramps if rp["d"] > run.dist + 30000)
    # ride straight up the ramp's lane from just before it, with nothing else in the way
    run.street.obs = [o for o in run.street.obs if o["height"] == "block" and abs(o["d"] - ramp["d"]) < 15000]
    run.lane, run.x, run.dist = S.LANES.index(ramp["x"]), ramp["x"], ramp["d"] - 2000
    top, landed = 0, False
    while not run.over and run.dist < ramp["d"] + S.JUMP + 2000:
        run.tick()
        top = max(top, run.height())
    assert not run.over and top >= 400
    assert any(abs(o["x"] - ramp["x"]) < 10 for o in run.street.obs)   # there was something there to clear
    assert not run.jumping()


def test_the_milk_float_pulls_up_behind_whatever_is_in_its_lane():
    run = S.Run("0123abcd")
    run.street.ensure(3_000_000)
    floats = [o for o in run.street.obs if o["kind"] == "float"]
    assert floats
    for f in floats:
        f["trig"] = 0
        f["stop"] = run._float_stop(f)
        for step in range(0, 120 * 60, 6):
            run.step = step
            d = run.ob_d(f)
            for q in run.street.obs:
                if q is f or q["kind"] in ("dog", "cab", "float") or abs(q["x"] - f["x"]) >= q["hw"] + f["hw"]:
                    continue
                assert abs(q["d"] - d) >= q["hl"] + f["hl"], (f["id"], q["kind"], step)
