"""The Wheelman's mini-games (lib/activities/heist_games/wheelman.py): what each keeps from the page, and how each
scores, beyond the contract every game keeps (tests/test_heist_games.py)."""

import copy
import json
import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities import heist_games as G  # noqa: E402

W = G.GAMES["wheelman"]
SIZES = ["s", "m", "big"]


def made(key, size="s", seed=1):
    return W[key].make(random.Random(seed), size, {})


def refused(g, p, body):
    before = copy.deepcopy(p)
    with pytest.raises(G.Refuse):
        g.move(p, body)
    assert p == before


def shown(g, p):
    return json.dumps(g.public(p))


# ---- GEAR CHANGE ---------------------------------------------------------------------------------------------

def tap_at(p, gr, begin, x):
    """When to shift to catch the needle at x (it climbs as (time / climb) ** curve)."""
    return begin + round(x ** (1 / gr["curve"]) * gr["climb"])


def test_gears_a_gear_climbs_from_the_last_shift_and_a_shift_on_the_limiter_scores_nothing():
    g = W["gears"]
    p = made("gears", "m")
    first, second, third = p["gears"][:3]
    one = tap_at(p, first, p["start"], first["sweet"])
    g.move(p, {"taps": [one]})
    assert g.score(p) == pytest.approx(1 / 5)
    # the second gear starts the needle's drop after the first shift, so a perfect second shift is timed from it
    g.move(p, {"taps": [one, tap_at(p, second, one + p["gap"], second["sweet"])]})
    assert g.score(p) == pytest.approx(2 / 5)
    # much later than the limiter: the van shifted itself on it, and that's a redline
    g.move(p, {"taps": [*p["taps"], 10 ** 5]})
    assert p["taps"][2] == p["taps"][1] + p["gap"] + third["climb"] + third["climb"] // 4
    assert g.score(p) == pytest.approx(2 / 5)


def test_gears_early_bogs_and_late_costs_and_the_middle_of_the_band_is_best():
    g = W["gears"]
    p = made("gears", "m")
    gr = p["gears"][0]
    assert 1.2 <= gr["curve"] <= 1.8                                       # the needle speeds up as it climbs
    scores = {}
    for name, x in (("spot", gr["sweet"]), ("edge", gr["sweet"] + gr["half"]), ("early", gr["sweet"] - gr["half"] - 0.05),
                    ("bog", 0.2)):
        q = copy.deepcopy(p)
        g.move(q, {"taps": [tap_at(q, gr, q["start"], x)]})
        scores[name] = g.score(q) * len(p["gears"])
    assert scores["spot"] == pytest.approx(1)
    assert 0.45 < scores["edge"] < 0.55
    assert 0 < scores["early"] < scores["edge"]
    assert scores["bog"] == 0


def test_gears_the_bands_get_narrower_with_the_size():
    g = W["gears"]
    widths = {size: [gr["half"] for gr in made("gears", size)["gears"]] for size in SIZES}
    assert widths["big"][0] < widths["m"][0] < widths["s"][0]
    assert all(a > b for a, b in zip(widths["big"], widths["big"][1:]))
    assert widths["m"][0] * made("gears", "m")["gears"][0]["climb"] < 200        # under a fifth of a second either side


def test_gears_shifts_are_checked_against_what_went_before():
    g = W["gears"]
    p = made("gears")
    refused(g, p, {"taps": [p["start"] - 1]})                      # before the lights
    refused(g, p, {"taps": [True]})
    refused(g, p, {"taps": []})
    t = p["start"] + 900
    g.move(p, {"taps": [t]})
    g.move(p, {"taps": [t]})                                        # a page sending it again: no change
    assert p["taps"] == [t]
    refused(g, p, {"taps": [t + 5, t + 3000]})                      # not the shift that was made
    refused(g, p, {"taps": [t, t + 100]})                           # the needle's still dropping
    refused(g, p, {"taps": [t, t + 1000, t + 2000, t + 3000, t + 4000]})   # more shifts than gears


# ---- LANE DODGE ----------------------------------------------------------------------------------------------

def test_dodge_sitting_in_the_middle_hits_whatever_is_in_the_middle_lane():
    g = W["dodge"]
    for size in SIZES:
        p = made("dodge", size, seed=3)
        middle = sum(1 for wv in p["waves"] if 1 in g._blocked(wv))
        g.move(p, {"steer": [], "t": p["end"]})
        assert g.done(p) and len(g._hits(p)) == middle
        assert g.score(p) == pytest.approx(max(0.0, 1 - p["hit"] * middle))


def test_dodge_scores_how_far_it_got_and_never_more():
    g = W["dodge"]
    p = made("dodge", "m")
    half = (p["start"] + p["end"]) // 2
    q = copy.deepcopy(p)
    plan = g.solve(q)[0]["steer"]
    g.move(p, {"steer": [s for s in plan if s[0] <= half], "t": half})
    assert not g.done(p) and g.score(p) == pytest.approx(0.5, abs=0.01)
    refused(g, p, {"steer": p["steer"], "t": half - 10})            # going backwards
    refused(g, p, {"steer": [*p["steer"], [half - 50, 1]], "t": half + 100})   # steering in the past
    refused(g, p, {"steer": p["steer"], "t": p["end"] + 1})


def test_dodge_cannot_leave_the_road():
    g = W["dodge"]
    p = made("dodge")
    refused(g, p, {"steer": [[3000, -1], [3001, -1]], "t": 3100})
    refused(g, p, {"steer": [[3000, 2]], "t": 3100})
    refused(g, p, {"steer": [[3000, True]], "t": 3100})
    g.move(p, {"steer": [[3000, -1]], "t": 3100})
    assert p["steer"] == [[3000, -1]]


def test_dodge_every_wave_leaves_a_lane_and_the_road_speeds_up():
    g = W["dodge"]
    for size in SIZES:
        for seed in range(5):
            p = made("dodge", size, seed)
            assert all(len(g._blocked(wv)) < 3 for wv in p["waves"])
            gaps = [b["t"] - a["t"] for a, b in zip(p["waves"], p["waves"][1:])]
            assert min(gaps) > 2 * max(wv["w"] for wv in p["waves"])          # time to get across between them
            assert sum(gaps[-3:]) < sum(gaps[:3])


# ---- JAMMED IN -----------------------------------------------------------------------------------------------

def test_jammed_par_is_the_fewest_moves_and_cars_slide_only_along_their_length():
    g = W["jammed"]
    for size in SIZES:
        for seed in range(4):
            p = made("jammed", size, seed)
            n, lo, hi, _ = g.SIZES[size]
            assert p["n"] == n and lo - 2 <= p["par"] <= hi and len(g._way_out(p)) == p["par"]   # a slow search settles near
            assert not g._out(p)
            cells = [c for car in p["cars"] for c in g._cells(car, n)]
            assert len(cells) == len(set(cells)) and all(0 <= r < n and 0 <= c < n for r, c in cells)


def test_jammed_moves_are_checked():
    g = W["jammed"]
    p = made("jammed", "m")
    refused(g, p, {"car": 0, "to": p["cars"][0]["c"]})               # nowhere
    refused(g, p, {"car": 0, "to": p["n"] - 2})                      # something's in the way (it's not solved yet)
    refused(g, p, {"car": 99, "to": 0})
    refused(g, p, {"car": True, "to": 0})
    i, q = g._way_out(p)[0]
    g.move(p, {"car": i, "to": q})
    assert p["moves"] == 1 and p["left"] == p["par"] - 1


def test_jammed_scores_moves_over_par_and_partway_by_how_near_the_van_is():
    g = W["jammed"]
    p = made("jammed", "big")
    assert g.score(p) == 0
    way = g._way_out(p)
    for i, q in way[:-1]:
        g.move(p, {"car": i, "to": q})
    assert g.score(p) == pytest.approx(0.45 * (1 - 1 / p["par"]))   # one move off: not solved, so under half
    # four wasted moves (a car slid away and back, twice), then out
    car, there = next((k, q) for k, q, _ in g._next(g._state(p), g._masks(p["cars"], p["n"])) if k)
    back = p["cars"][car]["c"] if p["cars"][car]["h"] else p["cars"][car]["r"]
    for _ in range(2):
        g.move(p, {"car": car, "to": there})
        g.move(p, {"car": car, "to": back})
    g.move(p, {"car": way[-1][0], "to": way[-1][1]})
    assert g.done(p) and p["moves"] == p["par"] + 4 and g.score(p) == pytest.approx(0.76)


# ---- ROUTE MEMORY --------------------------------------------------------------------------------------------

def test_memory_the_route_goes_once_you_say_go():
    g = W["memory"]
    for size in SIZES:
        p = made("memory", size)
        assert g.public(p)["route"] == p["route"] and len(p["route"]) == g.SIZES[size][0]
        refused(g, p, {"turns": [p["route"][0]]})                    # watch it first
        g.move(p, {"go": True})
        g.move(p, {"go": True})                                      # again: no harm
        assert "route" not in g.public(p) and "right" not in g.public(p)
        g.move(p, {"turns": p["route"][:2]})
        assert "route" not in g.public(p) and g.public(p)["turns"] == p["route"][:2]
        refused(g, p, {"turns": ["X", "L"]})
        refused(g, p, {"turns": [("L" if p["route"][0] != "L" else "R")]})   # not the way you went


def test_memory_the_route_stays_on_the_map_and_never_crosses_itself():
    g = W["memory"]
    step = ((0, -1), (1, 0), (0, 1), (-1, 0))
    for size in SIZES:
        for seed in range(20):
            p = made("memory", size, seed)
            x, y = p["start"]
            d, seen = 0, {(x, y), (x, y - 1)}
            y -= 1
            for t in p["route"]:
                d = (d + "LSR".index(t) - 1) % 4
                x, y = x + step[d][0], y + step[d][1]
                assert 0 <= x < p["w"] and 0 <= y < p["h"] and (x, y) not in seen
                seen.add((x, y))


def test_memory_scores_the_turns_before_the_first_wrong_one_and_a_little_for_the_rest():
    g = W["memory"]
    p = made("memory", "big")
    n = len(p["route"])
    g.move(p, {"go": True})
    wrong = [t for t in "LSR" if t != p["route"][2]][0]
    g.move(p, {"turns": [*p["route"][:2], wrong, *p["route"][3:]]})
    assert g.done(p) and g.score(p) == pytest.approx((2 + 0.25 * (n - 3)) / n)
    assert g.public(p)["route"] == p["route"] and g.public(p)["right"][2] is False


# ---- HOT-WIRE ------------------------------------------------------------------------------------------------

def test_hotwire_the_cover_fills_the_board_and_never_goes_out():
    g = W["hotwire"]
    for size in SIZES:
        for seed in range(6):
            p = made("hotwire", size, seed)
            n, k = g.SIZES[size]
            assert p["n"] == n and len(p["ends"]) == k and "sol" not in g.public(p)
            cells = [c for w in p["sol"] for c in w]
            assert sorted(cells) == list(range(n * n))                    # every square, once
            assert all(g._near(a, b, n) for w in p["sol"] for a, b in zip(w, w[1:]))
            assert all(len(w) >= 3 and not g._near(w[0], w[-1], n) for w in p["sol"])
            assert [[w[0], w[-1]] for w in p["sol"]] == p["ends"]


def test_hotwire_full_marks_need_every_pair_joined_and_every_square_filled():
    g = W["hotwire"]
    # 0  1  2  3      two pairs, 0-3 and 12-15; the short way round leaves the middle rows empty
    # 4  5  6  7
    # 8  9 10 11
    # 12 13 14 15
    p = {"n": 4, "ends": [[0, 3], [12, 15]], "sol": [[0, 4, 5, 1, 2, 6, 7, 3], [12, 8, 9, 13, 14, 10, 11, 15]],
         "wires": [None, None]}
    g.move(p, {"wire": 0, "path": [0, 1, 2, 3]})
    g.move(p, {"wire": 1, "path": [12, 13, 14, 15]})
    assert not g.done(p) and g.score(p) == pytest.approx(0.5 + 0.5 * 0.5 ** 4)    # joined, half full: about 0.53
    g.move(p, {"wire": 0, "path": [0, 4, 5, 1, 2, 6, 7, 3]})
    assert not g.done(p) and 0.6 < g.score(p) < 0.8
    g.move(p, {"wire": 1, "path": [15, 11, 10, 14, 13, 9, 8, 12]})                 # any full fill will do
    assert g.done(p) and g.score(p) == 1


def test_hotwire_a_wire_laid_over_another_cuts_it_out():
    g = W["hotwire"]
    # wire 0 dips from 0 to 3 through the second row; wire 1 comes up from 12 to 15 through 5 and 6, so it cuts
    # wire 0 out
    p = {"n": 4, "ends": [[0, 3], [12, 15]], "sol": [[0, 1, 2, 3], [12, 13, 14, 15]], "wires": [None, None]}
    g.move(p, {"wire": 0, "path": [0, 4, 5, 6, 7, 3]})
    assert g.score(p) == pytest.approx(0.5 * 0.5 + 0.5 * (6 / 16) ** 4)
    g.move(p, {"wire": 1, "path": [15, 11, 10, 6, 5, 9, 8, 12]})                   # drawn from either end
    assert p["wires"] == [None, [15, 11, 10, 6, 5, 9, 8, 12]]


def test_hotwire_a_good_player_joining_the_short_way_scores_about_six_tenths():
    g = W["hotwire"]
    from collections import deque
    got = []
    for size in SIZES:
        for seed in range(10):
            p = made("hotwire", size, seed)
            n = p["n"]
            for j, (a, b) in enumerate(p["ends"]):
                # the shortest way that doesn't cross what's laid or run over another pair's ends
                block = {e for i, ends in enumerate(p["ends"]) if i != j for e in ends} | g._filled(p)
                came, todo = {a: None}, deque([a])
                while todo:
                    x = todo.popleft()
                    r, c = divmod(x, n)
                    for y in (x - n if r else -1, x + n if r < n - 1 else -1, x - 1 if c else -1, x + 1 if c < n - 1 else -1):
                        if y >= 0 and y not in came and y not in block:
                            came[y] = x
                            todo.append(y)
                if b in came:
                    path = [b]
                    while came[path[-1]] is not None:
                        path.append(came[path[-1]])
                    g.move(p, {"wire": j, "path": path[::-1]})
            if all(p["wires"]):
                got.append(g.score(p))
    assert got and 0.5 < sum(got) / len(got) < 0.7


def test_hotwire_wires_are_checked():
    g = W["hotwire"]
    p = made("hotwire", "s")
    a, b = p["ends"][0]
    refused(g, p, {"wire": 0, "path": [a, b]})                       # not next to each other
    refused(g, p, {"wire": 1, "path": p["sol"][0]})                  # someone else's ends
    refused(g, p, {"wire": 0, "path": [a, a, b]})
    refused(g, p, {"wire": 0, "path": [*p["sol"][0][:-1], p["ends"][1][0], b]})
    refused(g, p, {"wire": True, "path": p["sol"][0]})


# ---- NUMBER PLATE --------------------------------------------------------------------------------------------

PLATE = re.compile(r"^[A-Z]{2}\d{2} [A-Z]{3}$")


def differ(a, b):
    return sum(x != y for x, y in zip(a, b))


def test_plate_near_misses_are_real_looking_and_closer_each_round():
    g = W["plate"]
    first, last = [], []
    for seed in range(60):
        p = made("plate", "m", seed)
        for i, r in enumerate(p["rounds"]):
            assert len(r["options"]) == g.SIZES["m"][1] == len(set(r["options"]))
            assert r["options"][r["answer"]] == r["plate"]
            assert all(PLATE.match(o) and "I" not in o and "Q" not in o for o in r["options"])
            near = [differ(o, r["plate"]) for o in r["options"] if o != r["plate"]]
            (first if i == 0 else last if i == len(p["rounds"]) - 1 else []).extend(near)
    assert sum(last) / len(last) < sum(first) / len(first)


def test_plate_no_one_character_gives_yours_away():
    g = W["plate"]
    for size in ("m", "big"):
        for seed in range(20):
            for r in made("plate", size, seed)["rounds"]:
                hit = {i for o in r["options"] if o != r["plate"] for i in range(8) if o[i] != r["plate"][i]}
                assert len(hit) >= 6                                       # nearly every character is off somewhere


def test_plate_the_fuse_burning_out_loses_the_round():
    g = W["plate"]
    p = made("plate", "big")
    assert g.public(p)["show"] < 2000 and g.public(p)["fuse"] <= 8000
    g.move(p, {"seen": True})
    refused(g, p, {"pick": -1})
    g.move(p, {"miss": True})
    assert g.public(p)["got"] == [False] and g.public(p)["last"]["pick"] == -1 and g.score(p) == 0


def test_plate_yours_is_gone_once_the_car_park_shows():
    g = W["plate"]
    p = made("plate", "m")
    refused(g, p, {"pick": 0})                                       # look first
    assert g.public(p)["plate"] == p["rounds"][0]["plate"] and "options" not in g.public(p)
    g.move(p, {"seen": True})
    pub = g.public(p)
    assert "plate" not in pub and "answer" not in shown(g, p) and pub["options"] == p["rounds"][0]["options"]
    refused(g, p, {"pick": 99})
    wrong = (p["rounds"][0]["answer"] + 1) % len(pub["options"])
    g.move(p, {"pick": wrong})
    pub = g.public(p)
    assert pub["got"] == [False] and pub["last"]["answer"] == p["rounds"][0]["answer"]   # the round gone, shown
    assert "options" not in pub and pub["plate"] == p["rounds"][1]["plate"]
    assert g.score(p) == 0


def test_plate_scores_the_right_picks():
    g = W["plate"]
    p = made("plate", "big")
    for i, r in enumerate(p["rounds"]):
        g.move(p, {"seen": True})
        g.move(p, {"pick": r["answer"] if i % 2 else (r["answer"] + 1) % len(r["options"])})
    assert g.done(p) and g.score(p) == pytest.approx(0.5)
