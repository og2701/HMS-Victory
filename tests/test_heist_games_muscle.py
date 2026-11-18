"""The Muscle's mini-games (lib/activities/heist_games/muscle.py) beyond the contract every game keeps: nothing the
player has to work out reaches the page, each is made so it can always be done, and each is scored from the moves
(and, for the timed ones, the times) exactly as the rules say."""

import copy
import itertools
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities.heist_games import Refuse  # noqa: E402
from lib.activities.heist_games import muscle as M  # noqa: E402

SIZES = ["s", "m", "big", "loot"]


def made(game, size="s", seed=1):
    return game.make(random.Random(seed), size, {})


def refused(game, p, body):
    before = copy.deepcopy(p)
    with pytest.raises(Refuse):
        game.move(p, body)
    assert p == before


def play(game, p, moves):
    for mv in moves:
        game.move(p, json.loads(json.dumps(mv)))


def test_loot_plays_like_m():
    for g in M.GAMES.values():
        a, b = g.make(random.Random(7), "m", {}), g.make(random.Random(7), "loot", {})
        assert a == b


# ---- exact weight --------------------------------------------------------------------------
@pytest.mark.parametrize("size", SIZES)
def test_weight_always_has_an_answer_of_three_or_more_bags_and_never_shows_it(size):
    g = M.Weight()
    for seed in range(60):
        p = made(g, size, seed)
        ws = [b["w"] for b in p["bags"]]
        assert sum(ws[i] for i in p["sol"]) == p["target"] and len(p["sol"]) >= 3
        exact = [c for m in range(1, len(ws) + 1) for c in itertools.combinations(ws, m) if sum(c) == p["target"]]
        assert min(map(len, exact)) >= len(p["sol"])                  # no shorter load makes it
        assert "sol" not in json.dumps(g.public(p))
        # the decoys sit a kilo or two off the answer's bags, so near misses abound
        answer = [ws[i] for i in p["sol"]]
        assert all(min(abs(w - a) for a in answer) <= 2 for i, w in enumerate(ws) if i not in p["sol"])


def test_weight_scores_exact_close_and_over():
    g = M.Weight()
    p = made(g, "m", 3)
    want = p["target"]
    weigh = lambda w: g.score({**p, "bags": [{"w": w, "art": "gold"}], "on": [0]})   # noqa: E731
    assert weigh(want) == 1.0
    assert 0.4 < weigh(want - 1) < 0.6 and 0 < weigh(want + 1) < weigh(want - 1) and weigh(want - 2) < weigh(want - 1)
    assert weigh(want // 2) == 0.0 and g.score(p) == 0.0


def test_weight_taps_run_out_and_a_bag_comes_back_off():
    g = M.Weight()
    p = made(g, "s", 4)
    wrong = next(i for i in range(len(p["bags"])) if i not in p["sol"])
    play(g, p, [{"bag": wrong, "t": 1000}, {"bag": wrong, "t": 2000}])   # on and off again: the two spare taps gone
    assert p["on"] == [] and g.public(p)["left"] == len(p["sol"])
    play(g, p, g.solve(p))
    assert g.done(p) and g.score(p) == 1.0
    q = made(g, "s", 4)
    for t in range(q["limit"]):
        g.move(q, {"bag": wrong, "t": 1000 + t})
    assert g.done(q) and g.score(q) < 1


def test_weight_runs_on_a_fuse():
    g = M.Weight()
    p = made(g, "big", 5)
    assert p["fuse"] == 25000 and made(g, "s")["fuse"] == 20000
    refused(g, p, {"bag": 0, "t": p["fuse"] + g.SLACK + 1})            # too late
    g.move(p, {"bag": 0, "t": 3000})
    refused(g, p, {"bag": 1, "t": 2000})                              # times only go forward
    g.move(p, {"out": True, "t": p["fuse"]})                          # the fuse went: sold as it stands
    assert g.done(p) and g.score(p) < 1


def test_weight_refuses_rubbish():
    g = M.Weight()
    p = made(g)
    for body in ({"bag": True, "t": 5}, {"bag": 99, "t": 5}, {"bag": "1", "t": 5}, {"bag": -1, "t": 5}, {"bag": 0},
                 {"bag": 0, "t": True}, {"bag": 0, "t": float("inf")}, {"out": 1, "t": 5}, {}):
        refused(g, p, body)


# ---- pack the van ----------------------------------------------------------------------------
@pytest.mark.parametrize("size", SIZES)
def test_pack_cuts_the_floor_round_the_arches_adds_a_spare_and_hides_where_they_go(size):
    g = M.Pack()
    rows, cols, ks, spares, arches = M._sized(g.SIZES, size)
    for seed in range(40):
        p = made(g, size, seed)
        assert (p["rows"], p["cols"]) == (rows, cols) and len(p["blocked"]) == arches
        assert all(r == rows - 1 and c in (0, cols - 1) for r, c in p["blocked"])      # the back corners
        real = [c for c in p["crates"] if c["home"]]
        assert len(real) in ks and len(p["crates"]) == len(real) + spares
        cells = sorted(tuple(x) for c in real for x in c["home"])
        floor = sorted({(r, c) for r in range(rows) for c in range(cols)} - {tuple(b) for b in p["blocked"]})
        assert cells == floor                                         # the real crates fill it exactly
        assert sum(len(c["shape"]) for c in p["crates"]) > len(floor)  # and there's more crate than van
        canon = [M._canon(c["shape"]) for c in p["crates"]]
        assert all(canon.count(M._canon(c["shape"])) == 1 for c in p["crates"] if not c["home"])   # the spare's its own
        assert max(map(canon.count, canon)) <= (1 if len(real) <= 7 else 2)
        assert all(3 <= len(c["shape"]) <= 6 for c in p["crates"])
        pub = json.dumps(g.public(p))
        assert "home" not in pub and "turn" not in pub
        for c in real:                                                # turned back, each is its own home
            assert M._turn(c["shape"], c["turn"]) == M._norm(c["home"])


def test_pack_places_by_the_corner_and_refuses_what_wont_fit():
    g = M.Pack()
    p = made(g, "s", 2)
    shape = p["crates"][0]["shape"]
    spot = next([r, c] for r in range(p["rows"]) for c in range(p["cols"])
                if all([a - shape[0][0] + r, b - shape[0][1] + c] not in p["blocked"] and a - shape[0][0] + r < p["rows"]
                       and b - shape[0][1] + c < p["cols"] for a, b in shape))
    g.move(p, {"put": 0, "rot": 0, "at": spot})
    first = p["put"][0]
    refused(g, p, {"put": 1, "rot": 0, "at": first[0]})               # on top of the first
    refused(g, p, {"put": 1, "rot": 0, "at": [p["rows"] - 1, p["cols"]]})   # off the edge
    arch = p["blocked"][0]
    one = next(i for i, c in enumerate(p["crates"]) if i)
    refused(g, p, {"put": one, "rot": 0, "at": arch})                 # on the wheel arch
    for body in ({"put": 1, "rot": 4, "at": [0, 0]}, {"put": 1, "rot": True, "at": [0, 0]}, {"put": 9, "at": [0, 0]},
                 {"put": 1, "at": [0]}, {"put": 1, "at": [0, True]}, {"take": 3}, {"take": None}):
        refused(g, p, body)
    assert 0 < g.score(p) < 1
    g.move(p, {"take": 0})
    assert p["put"][0] is None and g.score(p) == 0


def fits(g, p, i):
    """Somewhere crate i goes in the van as it is, as a move."""
    for r in range(p["rows"]):
        for c in range(p["cols"]):
            for rot in range(4):
                cells = g._cells(p, i, rot, [r, c])
                taken = [x for j, put in enumerate(p["put"]) if put and j != i for x in put] + p["blocked"]
                if all(0 <= a < p["rows"] and 0 <= b < p["cols"] and [a, b] not in taken for a, b in cells):
                    return {"put": i, "rot": rot, "at": [r, c]}
    return None


def test_pack_only_a_full_van_is_full_marks():
    g = M.Pack()
    for seed in range(20):
        p = made(g, "m", seed)
        spare = next(i for i, c in enumerate(p["crates"]) if not c["home"])
        g.move(p, fits(g, p, spare))                                  # the spare in first: it has to come out again
        assert g.score(p) < 1
        moves = g.solve(p)
        assert moves[0] == {"take": spare}
        for mv in moves:
            assert not g.done(p)
            g.move(p, mv)
        assert g.done(p) and g.score(p) == 1.0 and p["put"][spare] is None


def test_pack_moves_run_out():
    g = M.Pack()
    p = made(g, "s", 3)
    while not g.done(p):
        g.move(p, fits(g, p, 0))
        if not g.done(p):
            g.move(p, {"take": 0})
    assert p["moves"] == p["limit"] and g.score(p) < 1


# ---- count the cash --------------------------------------------------------------------------
@pytest.mark.parametrize("size", SIZES)
def test_cash_shows_the_bundles_not_which_button(size):
    g = M.Cash()
    for seed in range(30):
        p = made(g, size, seed)
        for r in p["rounds"]:
            assert r["options"][r["answer"]] == sum(r["notes"]) and len(set(r["options"])) == len(r["options"])
        pub = g.public(p)
        assert "answer" not in json.dumps(pub) and len(pub["shown"]) == 1      # only the round on the table


def test_cash_counts_a_late_or_wrong_total_as_wrong():
    g = M.Cash()
    p = made(g, "m", 5)
    r0 = p["rounds"][0]
    landed = g.LEAD + (len(r0["notes"]) - 1) * p["every"]
    g.move(p, {"pick": r0["answer"], "t": landed + p["window"] + g.SLACK + 50})   # right, but too late
    assert g.public(p)["right"] == [False] and g.public(p)["start"] == p["picks"][-1][1] + g.GAP
    refused(g, p, {"pick": 0, "t": p["picks"][-1][1] - 1})                # times only go forward
    refused(g, p, {"pick": 0, "t": True})
    refused(g, p, {"pick": 7, "t": 99999})
    r1 = p["rounds"][1]
    g.move(p, {"pick": (r1["answer"] + 1) % len(r1["options"]), "t": p["picks"][-1][1] + 3000})
    g.move(p, {"pick": -1, "t": p["picks"][-1][1] + 9000})               # ran out of time
    assert g.done(p) and g.score(p) == 0.0


def test_cash_solves_after_a_good_round():
    g = M.Cash()
    p = made(g, "big", 6)
    play(g, p, g.solve(p)[:1])
    play(g, p, g.solve(p))
    assert g.done(p) and g.score(p) == 1.0


# ---- dye packs -------------------------------------------------------------------------------
@pytest.mark.parametrize("size", SIZES)
def test_dye_sends_the_shuffle_but_not_where_it_ends(size):
    g = M.Dye()
    for seed in range(30):
        p = made(g, size, seed)
        for r in p["rounds"]:
            at = r["start"]
            for move in r["swaps"]:
                assert 1 <= len(move) <= 2 and len({x for pair in move for x in pair}) == 2 * len(move)   # pairs apart
                assert len({sum(pair) for pair in move}) == len(move)          # and never meeting in the middle
                for a, b in move:
                    at = b if at == a else a if at == b else at
            assert at == r["end"]
        for k in range(len(p["rounds"])):
            shown = g.public(p)["shown"]
            assert "end" not in shown[-1] and all("end" in s for s in shown[:-1])
            g.move(p, {"pick": p["rounds"][k]["end"]})
        assert g.score(p) == 1.0


def test_dye_scores_right_picks():
    g = M.Dye()
    p = made(g, "m", 2)
    r = p["rounds"][0]
    g.move(p, {"pick": (r["end"] + 1) % r["n"]})
    refused(g, p, {"pick": 9})
    refused(g, p, {"pick": False})
    play(g, p, g.solve(p))
    assert g.done(p) and g.score(p) == pytest.approx(2 / 3)


# ---- heave -------------------------------------------------------------------------------------
def test_mash_counts_taps_against_the_target():
    g = M.Mash()
    p = made(g, "s")
    go = p["go"]
    play(g, p, [{"taps": [go + 100 * i for i in range(1, 11)]}])
    assert p["counted"] == 10 and not g.done(p)
    assert g.score(p) == pytest.approx((10 / p["target"]) ** g.CURVE)
    assert p["target"] == 9 * 6 and made(g, "big")["target"] == 10 * 8   # a strong thumb's nine a second, ten at the vault
    assert (0.8 ** g.CURVE) < 0.76                                     # and 80% of it isn't 0.8
    play(g, p, [{"end": True}])
    assert g.done(p)


def test_mash_drops_taps_faster_than_any_thumb():
    g = M.Mash()
    p = made(g, "big")
    play(g, p, [{"taps": [p["go"] + 20 * i for i in range(50)]}])        # fifty a second for a second
    assert p["counted"] == 13                                            # one every MIN_GAP (80ms on this grid)
    p = made(g, "big")
    play(g, p, [{"taps": [p["go"] + 70 * i for i in range(15)]}])        # a touch over fourteen a second
    assert p["counted"] == g.CAP


def test_mash_counts_two_hands_as_one_thumb():
    """The space bar and the mouse together (or a page sending a doubled burst): a tap within MIN_GAP of the last
    one that counted doesn't, so two at once make no more steps than one quick thumb's taps."""
    g = M.Mash()
    one, two = made(g, "big"), made(g, "big")
    taps = [one["go"] + 100 * i for i in range(1, 41)]                   # ten a second, the vault's target pace
    play(g, one, [{"taps": taps}])
    play(g, two, [{"taps": sorted(taps + [t + 30 for t in taps])}])
    assert one["counted"] == two["counted"] == 40
    p = made(g, "big")
    play(g, p, [{"taps": [p["go"] + 1, p["go"] + 60, p["go"] + 71, p["go"] + 140, p["go"] + 141]}])
    assert p["taps"] == [p["go"] + 1, p["go"] + 71, p["go"] + 141]


def test_mash_refuses_taps_out_of_time_or_out_of_order():
    g = M.Mash()
    p = made(g, "m")
    go, end = p["go"], p["go"] + p["secs"]
    for body in ({"taps": [go - 1000]}, {"taps": [end + 2000]}, {"taps": [go + 500, go + 400]}, {"taps": [True]},
                 {"taps": [float("nan")]}, {"taps": []}, {"taps": "lots"}, {"end": 1}, {"taps": [go + 1] * 201}):
        refused(g, p, body)
    play(g, p, [{"taps": [go + 500]}])
    refused(g, p, {"taps": [go + 400]})                                  # a later batch can't go back in time
    play(g, p, g.solve(p))
    assert g.done(p) and g.score(p) == 1.0


# ---- stack the gold ----------------------------------------------------------------------------
def test_stack_replays_the_drops_from_their_times():
    g = M.Stack()
    p = made(g, "m", 3)
    p["sides"][0] = 0                                                    # the first bar sets off from the left
    v = p["speeds"][0]
    px, pw = p["bars"][-1]
    late = 200                                                           # ms late: it's slid v * 0.2 past the stack
    g.move(p, {"tap": g.GO + px / v * 1000 + late})
    x, w = p["bars"][-1]
    assert x == pytest.approx(px + v * late / 1000) and w == pytest.approx(pw - v * late / 1000)
    assert 0 < g.score(p) < 1
    refused(g, p, {"tap": g._start(p) - 500})                            # before the next bar's moving
    refused(g, p, {"tap": True})


def test_stack_snaps_a_near_miss_and_ends_on_a_clean_miss():
    g = M.Stack()
    p = made(g, "s", 4)
    first = g.solve(p)[0]["tap"]
    v = p["speeds"][0]
    g.move(p, {"tap": first + (g.SNAP - 0.5) / v * 1000})                # nearly dead on counts as dead on
    assert p["bars"][-1] == p["bars"][0]
    play(g, p, g.solve(p))
    assert g.done(p) and g.score(p) == 1.0
    q = made(g, "s", 4)
    q["bars"].append([0.0, 10.0])                                        # one thin bar on, hard at the left
    q["taps"].append(2000.0)
    q["sides"][1] = 0
    g.move(q, {"tap": 2000 + g.GAP + 60 / q["speeds"][1] * 1000})        # dropped well over to the right of it
    assert q["missed"] and g.done(q) and len(q["bars"]) == 2
    assert g.score(q) == pytest.approx(1 / 6 * (0.35 + 0.65 * 10 / q["bars"][0][1]))


def test_cash_only_offers_near_totals():
    g = M.Cash()
    for seed in range(30):
        for r in made(g, "big", seed)["rounds"]:
            total = sum(r["notes"])
            assert all(abs(o - total) <= 20 for o in r["options"])
