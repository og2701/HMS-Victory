"""The most anyone could score in Spitfire or Climb in a given time: the bot builds the day's
level exactly as the page does, and a perfect run through it sets the limit."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.activities import score_limits as L  # noqa: E402

SEED = "test-seed"


def test_the_levels_match_the_pages():
    # from the page's own rng() and generate() (src/spitfire/game.ts, src/climb/game.ts) run in node
    gates = L.spitfire_gates(SEED, 151)
    assert [round(x, 6) for x in gates[:6]] == [560, 831.098422, 1084.058281, 1350.955324, 1611.220609, 1886.911511]
    assert abs(gates[150] - 34967.72638278482) < 1e-6
    holds = L.climb_footholds(SEED, 3000)
    assert [(round(y, 6), k) for y, k in holds[:6]] == [
        (0, "jump"), (52.745939, "jump"), (104.657901, "jump"), (147.918823, "jump"), (190.356773, "jump"),
        (237.650424, "jump")]
    assert [h for h in holds if h[1] != "jump"][:2] == [(1216.2077013757207, "sail"), (1928.4649002098647, "sail")]


def test_spitfire_scores_at_the_planes_pace():
    assert L.spitfire_max(SEED, 3) == 0                       # the first gap is 3.6 seconds out
    assert [L.spitfire_max(SEED, t) for t in (10, 30, 60, 90)] == sorted(L.spitfire_max(SEED, t) for t in (10, 30, 60, 90))
    pace = L.spitfire_max(SEED, 65) / 65
    assert 0.7 < pace < 0.85                                  # real long runs go 0.74 to 0.81 a second


def test_climb_allows_the_best_real_climbs_but_not_much_more():
    assert L.climb_max(SEED, 0) <= 5                           # a bounce off the deck
    # the best real climbs: 145 m in 16 s, 745 m in 178 s, 1,261 m in 248 s
    assert 145 < L.climb_max(SEED, 16) < 250
    assert 900 < L.climb_max(SEED, 178) < 1400
    assert 1300 < L.climb_max(SEED, 248) < 1900
    assert L.climb_max(SEED, 60) < 60 * 9 + 40                 # tighter than the old 9 m a second
