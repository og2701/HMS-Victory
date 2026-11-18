"""Every Bank Job mini-game in lib/activities/heist_games keeps the same contract: made as plain data at every size,
nothing but public() goes to the page, a perfect player scores 1, a fresh one scores less, rubbish moves are
refused (never a crash), and the dev bots can play one to the end."""

import copy
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities import heist_games as G  # noqa: E402

STORY = {"name": "Denise Wright", "firm": "Thames Water", "why": "a leak", "floor": "the second floor", "ref": "7354",
         "boss": "Julie in head office"}
ALL = [(role, key, g) for role in G.ROLES for key, g in G.GAMES[role].items()]


def sizes(role):
    return ["s", "m", "big", "loot"] if role == "muscle" else ["s", "m", "big"]


def cases():
    return [pytest.param(role, key, size, id=f"{role}-{key}-{size}") for role, key, _ in ALL for size in sizes(role)]


@pytest.mark.parametrize("role,key", [pytest.param(r, k, id=f"{r}-{k}") for r, k, _ in ALL])
def test_a_game_knows_who_it_is(role, key):
    g = G.GAMES[role][key]
    assert g.key == key and g.role == role and g.title and g.title == g.title.upper() and key not in G.keys(role)[:1]


@pytest.mark.parametrize("role,key,size", cases())
def test_made_as_plain_data_and_shown_as_plain_data(role, key, size):
    g = G.GAMES[role][key]
    for seed in range(5):
        p = g.make(random.Random(seed), size, STORY)
        assert json.loads(json.dumps(p)) == p
        json.dumps(g.public(p))
        assert not g.done(p)


@pytest.mark.parametrize("role,key,size", cases())
def test_a_perfect_player_scores_one_and_a_fresh_puzzle_less(role, key, size):
    g = G.GAMES[role][key]
    for seed in range(8):
        p = g.make(random.Random(seed), size, STORY)
        assert g.score(p) < 1
        for mv in g.solve(copy.deepcopy(p)):
            assert not g.done(p)
            g.move(p, json.loads(json.dumps(mv)))
            json.dumps(g.public(p))
        assert g.done(p) and g.score(p) == pytest.approx(1.0), (seed, p)


@pytest.mark.parametrize("role,key,size", cases())
def test_rubbish_is_refused_not_a_crash(role, key, size):
    g = G.GAMES[role][key]
    p = g.make(random.Random(1), size, STORY)
    for body in ({}, {"x": 1}, {"seq": "nope"}, {"taps": [1e12, -5, "a"]}, {"n": None}, {"path": [[999, 999]]},
                 {"answer": 99}, {"load": [99]}, {"code": "zzzz"}, {"pick": -1}, {"at": [None]}):
        if g.done(p):
            break
        before = copy.deepcopy(p)
        try:
            g.move(p, body)
        except G.Refuse:
            assert p == before                             # a refused move changes nothing
        json.dumps(g.public(p))
        assert 0.0 <= g.score(p) <= 1.0


@pytest.mark.parametrize("role,key,size", cases())
def test_the_dev_bots_can_finish_one(role, key, size):
    g = G.GAMES[role][key]
    for seed in range(4):
        rng = random.Random(seed)
        p = g.make(rng, size, STORY)
        for _ in range(300):
            if g.done(p):
                break
            try:
                g.move(p, json.loads(json.dumps(g.bot(p, rng, rng.uniform(0.5, 0.9)))))
            except G.Refuse:
                pass
        assert g.done(p) and 0.0 <= g.score(p) <= 1.0


def test_a_loud_mash_counts_two_hands_as_one_thumb():
    """MASH GRAB TILL THE TIME'S UP: a press read twice (two taps within pace.MIN_GAP) counts once, and two hands at
    it (the space bar and the mouse, or a doctored page's doubled taps) are held to one thumb's 12 a second."""
    from lib.activities.heist_games import loud as L
    from lib.activities.heist_games import pace
    g = L.LOOSE["till"]                                                   # (the loud Muscle's CARRY THE SWAG now)
    assert isinstance(g, L.Mash)
    one, two = g.make(random.Random(1), "big", {}), g.make(random.Random(1), "big", {})
    taps = [i * 100 + 5 for i in range(70)]                               # ten a second for seven seconds
    g.move(one, {"taps": taps})
    g.move(two, {"taps": sorted(taps + [t + 20 for t in taps])})          # each one read twice
    assert one["n"] == two["n"] == 70
    p = g.make(random.Random(1), "big", {})
    g.move(p, {"taps": [0, 10, 39, 40, 100, 120, 141]})
    assert p["n"] == 4 and p["last"] == 141
    # two hands mashing 10 a second each, out of step: no more than one thumb's 12 a second (and a few in hand)
    p = g.make(random.Random(1), "big", {})
    g.move(p, {"taps": sorted(taps + [t + 50 for t in taps])})
    assert p["n"] <= 7 * pace.RATE + pace.BURST


def test_a_loud_mash_never_drops_a_real_thumbs_taps():
    """A thumb at 6 to 9 taps a second, its taps wobbling by up to 25ms either way, or a few landing close together
    (a quick double, a run bunched up): every one counts."""
    from lib.activities.heist_games import loud as L
    g = L.LOOSE["till"]                                                   # (the loud Muscle plays CARRY THE SWAG now)
    rng = random.Random(3)
    for rate in (6, 7, 8, 9):
        p = g.make(random.Random(1), "big", {})
        taps, t = [], 20.0
        while t < 6900:
            taps.append(round(t + rng.uniform(-25, 25)))
            t += 1000 / rate
        taps = sorted(taps)
        for k in range(0, len(taps), 5):                                  # sent a few at a time, as the page does
            g.move(p, {"taps": taps[k:k + 5]})
        assert p["n"] == len(taps), rate
    p = g.make(random.Random(1), "big", {})
    g.move(p, {"taps": [100, 150, 200, 260, 420, 470, 520, 640]})         # bunched pairs and runs, 8 a second overall
    assert p["n"] == 8
