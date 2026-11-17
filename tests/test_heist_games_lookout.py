"""The Lookout's games beyond the first: what the page sees never gives the answer away, and they score the way
they say they do."""

import copy
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities.heist_games import Refuse  # noqa: E402
from lib.activities.heist_games import lookout as L  # noqa: E402

SIZES = ["s", "m", "big"]


def made(key, size="s", seed=1):
    g = L.GAMES[key]
    return g, g.make(random.Random(seed), size, {})


def shown(g, p):
    return json.dumps(g.public(p))


# ---- the looks: a scene only once it's asked for, and only this round's ---------------------------------------

@pytest.mark.parametrize("key", ["spot", "count", "moved"])
@pytest.mark.parametrize("size", SIZES)
def test_a_round_is_never_out_before_its_look(key, size):
    g, p = made(key, size)
    for i, rd in enumerate(p["rounds"]):
        before = g.public(p)
        assert "scene" not in before and len(before["was"]) == i     # nothing of this round, nor the next
        g.move(p, {"look": True})
        assert g.public(p)["scene"] == g._scene(rd)
        g.move(p, {"look": True})                                    # asking twice changes nothing
        g.move(p, {g.ANSWER: g._right(rd)})
        assert "scene" not in g.public(p)
    assert g.done(p) and g.score(p) == 1


@pytest.mark.parametrize("key", ["spot", "count", "moved"])
def test_an_answer_needs_a_look_first(key):
    g, p = made(key)
    before = copy.deepcopy(p)
    with pytest.raises(Refuse):
        g.move(p, {g.ANSWER: 0})
    assert p == before


def test_spot_hides_the_guard_until_looked_at_and_scores_next_door_a_little():
    g, p = made("spot", "big")
    assert "guard" not in shown(g, p)
    rd = p["rounds"][0]
    g.move(p, {"look": True})
    out = g.public(p)
    assert set(out) == {"rounds", "at", "said", "looking", "marks", "was", "scene"}
    assert set(out["scene"]) == {"figs", "flash"}
    assert sum(f is not None and f["k"] in L.GUARDS for f in out["scene"]["figs"]) == 1
    r, c = divmod(rd["guard"], 3)
    door = (r * 3 + c + 1) if c < 2 else (r * 3 + c - 1)
    g.move(p, {"at": door})
    assert g.public(p)["marks"] == [0.3]


def test_count_scores_one_out_a_little_and_further_out_nothing():
    g, p = made("count", "m")
    n = [rd["guards"] for rd in p["rounds"]]
    for said in (n[0], n[1] + 1, n[2] + 3 if n[2] + 3 < g.TOP else 0):
        g.move(p, {"look": True})
        g.move(p, {"n": said})
    assert g.score(p) == pytest.approx((1 + 0.3 + 0) / 3)
    assert g.public(p)["was"] == n


def test_count_parades_are_busier_and_quicker_as_they_go():
    g, p = made("count", "big", 3)
    folk = [rd["folk"] for rd in p["rounds"]]
    assert [len(f) for f in folk] == [9, 11, 13]
    assert sum(x["dur"] for x in folk[0]) / 9 > sum(x["dur"] for x in folk[2]) / 13
    assert all(x["k"] in L.GUARDS + g.OTHERS + L.LOOKALIKES for f in folk for x in f)


def test_count_puts_look_alikes_among_the_guards_past_the_smallest():
    g = L.GAMES["count"]
    seen = {"m": 0, "big": 0}
    for size in seen:
        for seed in range(20):
            p = g.make(random.Random(seed), size, {})
            seen[size] += sum(x["k"] in L.LOOKALIKES for rd in p["rounds"] for x in rd["folk"])
    assert seen["big"] > seen["m"] > 20


def test_moved_turns_others_round_where_they_stand_but_moves_only_one():
    g, p = made("moved", "big", 2)
    for rd in p["rounds"]:
        assert rd["mover"] not in rd["turn"] and len(rd["turn"]) in (1, 2)
        assert all(rd["a"][i] == rd["b"][i] for i in rd["turn"])
    g.move(p, {"look": True})
    assert g.public(p)["scene"]["turn"] == p["rounds"][0]["turn"]


def test_moved_moves_exactly_one_person_to_an_empty_spot():
    for seed in range(40):
        g, p = made("moved", "big", seed)
        for rd in p["rounds"]:
            moved = [i for i, (a, b) in enumerate(zip(rd["a"], rd["b"])) if a != b]
            assert moved == [rd["mover"]]
            assert rd["b"][rd["mover"]] not in rd["a"] and len(set(rd["b"])) == len(rd["b"])


# ---- against the clock ------------------------------------------------------------------------------------

def test_sweep_spots_a_dash_while_the_camera_looks_and_not_while_it_looks_away():
    g, p = made("sweep", "s")
    lk = next(x for x in p["looks"] if not x["twitch"])
    q = copy.deepcopy(p)
    g.move(q, {"tap": lk["at"] - 800})                               # it's looking down the corridor
    assert q["seen"] == [True]
    q = copy.deepcopy(p)
    t = g._clear(q, 0)
    assert lk["at"] <= t <= lk["at"] + 2 * p["turn"] + lk["away"]  # the first look away
    g.move(q, {"tap": t})
    assert q["seen"] == [False]
    assert "seen" not in json.dumps(g.public(p)["looks"])


def test_sweep_wants_rising_dashes_a_dash_apart_and_takes_one_sent_twice_once():
    g, p = made("sweep", "m")
    g.move(p, {"tap": 1000})
    for bad in ({"tap": 1000 + p["dash"] - 1}, {"tap": 500}, {"tap": True}, {"tap": "1500"}, {"tap": float("nan")},
                {"tap": g.LONGEST + 1}):
        before = copy.deepcopy(p)
        with pytest.raises(Refuse):
            g.move(p, bad)
        assert p == before
    g.move(p, {"tap": 1000})
    assert p["taps"] == [1000]
    g.move(p, {"tap": 1000.4 + p["dash"]})
    assert p["taps"] == [1000, 1000 + p["dash"]]


def test_sweep_scores_by_clean_dashes_and_its_passes_quicken():
    g, p = made("sweep", "big", 5)
    aways = [x["away"] for x in p["looks"] if not x["twitch"]]
    assert aways[0] > aways[-1]
    moves = g.solve(copy.deepcopy(p))
    g.move(p, {"tap": p["looks"][0]["at"] - 600})                 # spotted
    for mv in g.solve(copy.deepcopy(p)):
        g.move(p, mv)
    assert g.done(p) and g.score(p) == pytest.approx(5 / 6)
    assert len(moves) == 6


def test_duck_scores_quick_hides_best_and_costs_false_alarms():
    g, p = made("duck", "s", 2)
    torches = [e for e in p["events"] if e["k"] == "torch"]
    fake = next(e for e in p["events"] if e["k"] != "torch")
    q = copy.deepcopy(p)
    for mv in g.solve(copy.deepcopy(q)):
        g.move(q, mv)
    assert g.score(q) == 1
    # every torch hidden from, but slowly
    q = copy.deepcopy(p)
    for e in torches:
        g.move(q, {"tap": e["at"] + e["hide"] - 1})
    assert 0.25 < g.score(q) < 0.35
    # the first torch missed, and a hide for a fake-out
    q = copy.deepcopy(p)
    taps = sorted([e["at"] + 200 for e in torches[1:]] + [fake["at"] + 200])
    for t in taps:
        g.move(q, {"tap": t})
    g.move(q, {"end": p["len"]})
    assert g.done(q) and g.score(q) == pytest.approx((len(torches) - 1 - g.WRONG) / len(torches))


def test_duck_wont_end_early_or_hide_twice_at_once():
    g, p = made("duck", "m")
    for bad in ({"end": p["len"] - 1000}, {"end": None}, {"tap": p["len"] + 1}, {"tap": -1}):
        before = copy.deepcopy(p)
        with pytest.raises(Refuse):
            g.move(p, bad)
        assert p == before
    g.move(p, {"tap": 3000})
    with pytest.raises(Refuse):
        g.move(p, {"tap": 3000 + g.GAP - 1})
    assert not g.done(p)
    g.move(p, {"end": p["len"]})
    assert g.done(p)


def test_duck_hiding_from_a_torch_shone_aside_is_a_false_alarm():
    g = L.GAMES["duck"]
    for seed in range(20):
        p = g.make(random.Random(seed), "m", {})
        aside = next((e for e in p["events"] if e["k"] == "aside"), None)
        if aside:
            assert aside["dir"] in (1, -1) and "win" in aside
            g.move(p, {"tap": aside["at"] + 200})
            assert g._marks(p)[1] == 1
            return
    raise AssertionError("no torch shone aside in twenty streets")


def test_duck_only_uses_the_windows_the_page_shows():
    for seed in range(20):
        g, p = made("duck", "big", seed)
        wins = [e["win"] for e in p["events"] if "win" in e]
        assert all(0 <= w < g.WINDOWS for w in wins)
        assert all(a != b for a, b in zip(wins, wins[1:]))
        ats = [e["at"] for e in p["events"]]
        assert all(b - a >= g.APART for a, b in zip(ats, ats[1:]))


# ---- the floor ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("size", SIZES)
def test_sensors_never_show_until_its_over_and_the_first_step_is_always_safe(size):
    g = L.GAMES["sensors"]
    for seed in range(30):
        p = g.make(random.Random(seed), size, {})
        n = p["n"]
        first = (n - 1) * n + seed % n
        g.move(p, {"at": first})
        assert p["seen"][first] == 0                              # safe, and nothing round it either
        assert "sensors" not in g.public(p) and json.dumps(p["sensors"]) not in shown(g, p)
        assert g._fair(n, set(p["sensors"]), first)                # you can get there from the numbers alone
        for mv in g.solve(copy.deepcopy(p)):
            assert "sensors" not in g.public(p)
            g.move(p, mv)
        assert g.done(p) and g.score(p) == 1 and g.public(p)["sensors"] == p["sensors"]


def test_sensors_are_dense_and_a_middling_player_doesnt_ace_them():
    g = L.GAMES["sensors"]
    assert {size: g.SIZES[size] for size in SIZES} == {"s": (5, 6), "m": (6, 9), "big": (7, 13)}
    for size, most in (("s", 0.9), ("big", 0.8)):
        scores = []
        for seed in range(150):
            rng = random.Random(seed)
            p = g.make(rng, size, {})
            while not g.done(p):
                g.move(p, g.bot(p, rng, 0.7))
            assert len(p["sensors"]) == g.SIZES[size][1]
            scores.append(g.score(p))
        assert 0.5 < sum(scores) / len(scores) < most, size


def test_sensors_the_numbers_say_safe_are_safe():
    g = L.GAMES["sensors"]
    for seed in range(40):
        rng = random.Random(seed)
        p = g.make(rng, "big", {})
        while not g.done(p):
            safe, found = g._read(p)
            assert not safe & set(p["sensors"] or []) and found <= set(p["sensors"] or found)
            g.move(p, g.bot(p, rng, 0.6))


def test_sensors_keep_to_where_youve_been():
    g, p = made("sensors", "m")
    n = p["n"]
    for bad in ({"at": 0}, {"at": n * n}, {"at": True}, {"at": "3"}):
        with pytest.raises(Refuse):
            g.move(p, bad)
    g.move(p, {"at": (n - 1) * n})                                 # the bottom left corner
    before = copy.deepcopy(p)
    for bad in ({"at": (n - 1) * n}, {"at": (n - 1) * n + 3}, {"at": 0}):
        with pytest.raises(Refuse):
            g.move(p, bad)
        assert p == before
    g.move(p, {"at": (n - 2) * n + 1})                               # a corner's next to it


def test_sensors_cost_a_chunk_each_and_partway_counts_for_how_far_up():
    g, p = made("sensors", "big", 4)
    n = p["n"]
    first = (n - 1) * n + n // 2
    g.move(p, {"at": first})
    assert g.score(p) == 0
    step = g._route(n, set(p["sensors"]), {first})[0]
    g.move(p, {"at": step})
    assert g.score(p) == pytest.approx(0.6 * (n - 1 - step // n) / (n - 1))
    # walk on to a sensor next to somewhere stood on, and it costs
    stood = {i for i, v in enumerate(p["seen"]) if v != -1}
    trap = next((s for s in p["sensors"] if any(j in stood for j in g._near(n, s))), None)
    if trap is not None:
        was = g.score(p)
        g.move(p, {"at": trap})
        assert p["trips"] == 1 and p["seen"][trap] == g.SENSOR
        assert g.score(p) == pytest.approx(max(0.0, 0.6 * max(n - 1 - i // n for i in stood | {trap}) / (n - 1) - g.TRIP))
        assert g.score(p) < was or was < g.TRIP


def test_three_sensors_set_off_and_its_over():
    g = L.GAMES["sensors"]
    p = g.make(random.Random(9), "big", {})
    n = p["n"]
    g.move(p, {"at": (n - 1) * n + n // 2})
    p["trips"] = g.CAUGHT
    assert g.done(p) and g.score(p) == 0
