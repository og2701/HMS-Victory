"""The Safecracker's mini-games beyond the dial: what each keeps from the page, and how each is scored."""

import copy
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities import heist_games as G  # noqa: E402
from lib.activities.heist_games import safecracker as S  # noqa: E402

SIZES = ["s", "m", "big"]


def fresh(game, size="s", seed=0):
    return game.make(random.Random(seed), size, {})


def play(game, p, *moves):
    for mv in moves:
        game.move(p, json.loads(json.dumps(mv)))
    return p


@pytest.mark.parametrize("game", [S.SweetSpot(), S.Pins(), S.Levers(), S.Order()], ids=lambda g: g.key)
@pytest.mark.parametrize("size", SIZES)
def test_a_fresh_puzzle_looks_the_same_whatever_its_secret(game, size):
    # the page sees nothing that tells one fresh puzzle from another: the secret's nowhere in it
    shown = {json.dumps(game.public(fresh(game, size, seed)), sort_keys=True) for seed in range(12)}
    assert len(shown) == 1


@pytest.mark.parametrize("size", SIZES)
def test_the_secrets_never_go_to_the_page(size):
    for seed in range(6):
        rng = random.Random(seed)
        for game, secret in ((S.SweetSpot(), "spot"), (S.Pins(), "h"), (S.Levers(), "want"), (S.Order(), "order"),
                             (S.Rings(), "par")):
            p = game.make(rng, size, {})
            for _ in range(4):
                if game.done(p):
                    break
                game.move(p, game.bot(p, rng, 0.3))
                assert secret not in game.public(p)
    p = fresh(S.Order(), "big", 3)
    play(S.Order(), p, {"at": p["order"][0]}, {"at": p["order"][1]})
    assert S.Order().public(p)["lit"] == p["order"][:2]                 # only what's lit, never what's next


def test_sweet_spot_says_how_far_off_in_rings():
    g = S.SweetSpot()
    p = fresh(g, "big")
    n, spot = p["n"], p["spot"]
    play(g, p, {"at": next(i for i in range(n * n) if g._far(n, i, spot) == 1)})
    assert g.public(p)["taps"][-1]["far"] == 1 and g.score(p) == pytest.approx(0.4)    # missed, but hot
    assert g._far(8, 0, 63) == g.COLD                                    # a corner to the far corner is just cold
    assert g._far(8, 0, 3) == g._far(8, 0, 4) == 3 and g._far(8, 0, 5) == g.COLD   # cool is three or four off
    play(g, p, {"at": spot})
    assert g.done(p) and g.score(p) == 1.0
    p = fresh(g, "s")
    wrong = next(i for i in range(36) if i != p["spot"])
    play(g, p, *[{"at": wrong}] * 4, {"at": p["spot"]})                # found on the fifth, par is three
    assert g.score(p) == pytest.approx(1 - 2 * 0.12)


def test_sweet_spot_runs_out_of_taps():
    g = S.SweetSpot()
    p = fresh(g, "s")
    far = max(range(36), key=lambda i: g._far(6, i, p["spot"]))
    play(g, p, *[{"at": far}] * p["limit"])
    assert g.done(p) and g.score(p) < 0.4


def test_pins_only_say_how_many_are_set_and_how_many_too_high():
    g = S.Pins()
    p = fresh(g, "m", 4)
    p["h"] = [1, 3, 9, 4]
    play(g, p, {"set": [3, 3, 3, 3]})
    assert g.public(p)["tests"] == [{"pins": [3, 3, 3, 3], "set": 1, "high": 1}]     # never which pins
    assert not g.done(p) and g.score(p) == pytest.approx(0.6 * 1 / 4)                # partway: one pin set
    play(g, p, {"set": [1, 3, 5, 5]}, {"set": [1, 3, 9, 4]})
    assert g.done(p) and g.score(p) == 1.0                              # three tests, inside the par of five
    p = fresh(g, "s", 4)
    p["h"] = [2, 2, 2]
    play(g, p, *[{"set": [1, 1, 1]}] * 5, {"set": [2, 2, 2]})
    assert g.done(p) and g.score(p) == pytest.approx(0.92)             # on the last of six, par five


@pytest.mark.parametrize("body", [{"set": [3, 3]}, {"set": [0, 3, 3]}, {"set": [3, True, 3]}, {"set": "333"},
                                  {"set": [3, 3, 10]}, {"set": [3.0, 3, 3]}])
def test_pins_refuse_a_bad_test(body):
    g = S.Pins()
    p = fresh(g, "s")
    before = copy.deepcopy(p)
    with pytest.raises(G.Refuse):
        g.move(p, body)
    assert p == before


def test_levers_only_say_how_many():
    g = S.Levers()
    p = fresh(g, "s")
    p["want"] = [0, 1, 2, 0]
    play(g, p, {"set": [0, 0, 0, 0]})
    assert g.public(p)["pulls"] == [{"set": [0, 0, 0, 0], "right": 2}]
    assert g.score(p) == pytest.approx(0.25)
    play(g, p, {"set": [1, 0, 0, 0]}, {"set": [0, 1, 0, 0]}, {"set": [0, 1, 1, 0]}, {"set": [0, 1, 2, 0]})
    assert g.done(p) and g.score(p) == pytest.approx(0.9)               # five pulls for four levers
    with pytest.raises(G.Refuse):
        g.move(fresh(g, "big"), {"set": [0, 1, 2, 3, 0]})


def test_button_order_clunks_resets_and_scrambles():
    g = S.Order()
    p = fresh(g, "s", 2)
    a, b, c, d = p["order"]
    play(g, p, {"at": a}, {"at": b})
    before = copy.deepcopy(p)
    with pytest.raises(G.Refuse):                                      # a lit one can't be pressed again
        g.move(p, {"at": a})
    assert p == before
    laid = list(p["layout"])
    play(g, p, {"at": d})
    shown = g.public(p)
    assert shown["lit"] == [] and shown["wrong"] == d and shown["clunks"] == 1
    assert shown["layout"] != laid and sorted(shown["layout"]) == [0, 1, 2, 3]       # the keypad's scrambled
    assert g.score(p) == pytest.approx(0.25)                            # partway: it got two of four in once
    play(g, p, {"at": a}, {"at": b}, {"at": c}, {"at": d})
    assert g.done(p) and g.score(p) == 1.0                              # trying d once at the third was fair


def test_button_order_costs_only_the_presses_a_good_memory_saves():
    g = S.Order()
    p = fresh(g, "m", 5)
    first, second, third, fourth, fifth = p["order"]
    play(g, p, {"at": second})                                          # a fair guess for the first: no cost
    play(g, p, {"at": second})                                          # the same again: wasted
    play(g, p, {"at": first}, {"at": second}, {"at": fifth})            # a fair guess for the third
    assert g._wasted(p) == 1
    play(g, p, {"at": second})                                          # after a clunk, first was known: wasted
    assert g._wasted(p) == 2
    play(g, p, *[{"at": b} for b in p["order"]])
    assert g.done(p) and g.score(p) == pytest.approx(1 - 2 * g.WASTE)


def needle_at(g, p, t):
    """A needle puzzle moved on to tap at t."""
    return play(g, p, {"tap": t})


def test_the_needle_is_worked_out_from_the_tap_time():
    g = S.Needle()
    p = fresh(g, "s", 7)
    t = g._next(p, 500)
    assert g._off(p, t) == pytest.approx(0, abs=1e-6)
    needle_at(g, p, t)
    assert p["set"] == [1.0] and p["seg"]["t"] == t                     # the next run starts from the hit
    w, v = p["widths"][1], p["speeds"][1]
    edge = g._next(p, t + 300) + 0.95 * w / v * 1000                     # nearly at the notch's edge
    needle_at(g, p, edge)
    assert len(p["set"]) == 2 and g.EDGE <= p["set"][1] < g.EDGE + 0.1
    late = g._next(p, edge + 300) + 3 * p["widths"][2] / p["speeds"][2] * 1000
    needle_at(g, p, late)
    assert len(p["set"]) == 2 and p["miss"] == 1 and p["misses"] == 1
    assert g.score(p) == pytest.approx((1 + p["set"][1]) / 3 * (1 - g.MISS))


def test_any_red_over_the_green_counts_as_an_edge_tap():
    g = S.Needle()
    p = fresh(g, "s", 7)
    w, v = p["widths"][0], p["speeds"][0]
    graze = g._next(p, 500) + (w + 0.8 * g.GRACE) / v * 1000            # the blade's edge just over the green
    needle_at(g, p, graze)
    assert p["set"] == [pytest.approx(g.EDGE)] and p["misses"] == 0
    past = g._next(p, graze + 300) + (p["widths"][1] + 1.5 * g.GRACE) / p["speeds"][1] * 1000
    needle_at(g, p, past)
    assert len(p["set"]) == 1 and p["misses"] == 1
    assert g.public(p)["grace"] == g.GRACE


def test_three_misses_on_a_tumbler_slip_the_last_one_back():
    g = S.Needle()
    p = fresh(g, "m", 1)
    needle_at(g, p, g._next(p, 0))
    t = p["taps"][-1]
    for _ in range(3):
        t = g._next(p, t + 300) + 180 / p["speeds"][len(p["set"])] * 1000     # across the dial from the notch
        needle_at(g, p, t)
    assert p["set"] == [] and p["miss"] == 0 and p["misses"] == 3 and not g.done(p)


@pytest.mark.parametrize("body", [{"tap": 1999}, {"tap": -1}, {"tap": True}, {"tap": "2500"}, {"tap": float("nan")},
                                  {"tap": 10 ** 9}, {"tap": None}, {}, {"stop": 100}, {"stop": False}])
def test_the_needle_refuses_a_bad_time(body):
    g = S.Needle()
    p = needle_at(g, fresh(g, "s"), 2000)
    before = copy.deepcopy(p)
    with pytest.raises(G.Refuse):                                      # 1999 is before the last tap
        g.move(p, body)
    assert p == before


def test_the_needle_stops_when_its_time_is_up():
    g = S.Needle()
    p = fresh(g, "big")
    needle_at(g, p, g._next(p, 0))
    play(g, p, {"stop": p["len"]})
    assert g.done(p) and 0 < g.score(p) < 0.25


def test_the_needle_speeds_up_and_the_notch_moves():
    for seed in range(20):
        p = fresh(S.Needle(), "big", seed)
        assert p["speeds"] == sorted(p["speeds"]) and len(set(p["notches"])) == 5
        assert all(p["ways"][i] == -p["ways"][i + 1] for i in range(4))


def test_a_ring_with_a_bar_turns_its_neighbour():
    g = S.Rings()
    for seed in range(30):
        p = fresh(g, "big", seed)
        i = next(j for j, x in enumerate(p["link"]) if x is not None)
        j = p["link"][i]
        assert abs(i - j) == 1 and p["link"][j] != i                     # a neighbour, and never both ways
        was = list(p["pos"])
        play(g, p, {"ring": i})
        assert p["pos"][i] == (was[i] + 1) % 8 and p["pos"][j] == (was[j] + 1) % 8
        assert all(p["pos"][k] == was[k] for k in range(4) if k not in (i, j))


@pytest.mark.parametrize("size", SIZES)
def test_rings_start_out_and_the_fewest_turns_is_par(size):
    g = S.Rings()
    for seed in range(20):
        p = fresh(g, size, seed)
        assert all(p["pos"]) and len(g.solve(p)) == p["par"] < p["limit"]


def test_a_wasted_lap_costs_about_a_third():
    g = S.Rings()
    p = fresh(g, "s", 3)
    free = next(i for i, x in enumerate(p["link"]) if x is None)
    play(g, p, *[{"ring": free}] * 8)                                    # once round for nothing
    for mv in g.solve(p):
        play(g, p, mv)
    assert g.done(p) and g.score(p) == pytest.approx(0.7)


@pytest.mark.parametrize("key", list(S.GAMES))
@pytest.mark.parametrize("size", SIZES)
def test_a_middling_bot_scores_like_a_decent_player_and_never_aces_it(key, size):
    g = S.GAMES[key]
    scores = []
    for seed in range(80):
        rng = random.Random(seed)
        p = g.make(rng, size, {})
        while not g.done(p):
            g.move(p, json.loads(json.dumps(g.bot(p, rng, 0.7))))
        scores.append(g.score(p))
    assert 0.45 <= sum(scores) / len(scores) < 0.93
