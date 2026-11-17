"""The Hacker's mini-games beyond the contract every game keeps (test_heist_games.py): what each keeps secret stays
out of public(), the drawing or board the page gets is enough to play from, and each scores the way it says."""

import itertools
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities.heist_games import Refuse  # noqa: E402
from lib.activities.heist_games.hacker import GAMES, Lights, _clue, _turn  # noqa: E402

SIZES = ("s", "m", "big")


def made(key, size, seed=0):
    return GAMES[key].make(random.Random(seed), size, {})


@pytest.mark.parametrize("key", ["wire", "password", "grid", "logic", "tiles"])
def test_true_and_false_are_not_numbers_or_letters(key):
    g = GAMES[key]
    p = made(key, "s")
    for body in ({"pick": True}, {"at": False}, {"at": True}, {"letter": True}, {"code": True}, {"pick": 1.0}):
        before = json.dumps(p)
        try:
            g.move(p, body)
        except Refuse:
            assert json.dumps(p) == before


@pytest.mark.parametrize("n", [3, 4, 5])
def test_a_quiet_pattern_on_the_alarm_panel_changes_nothing(n):
    quiet = Lights._quiet(n)
    assert len(quiet) == {3: 1, 4: 16, 5: 4}[n] and len(set(quiet)) == len(quiet)
    for q in quiet:
        on = [0] * (n * n)
        for i in range(n * n):
            if q >> i & 1:
                Lights._press(on, n, i)
        assert not any(on)


@pytest.mark.parametrize("size", SIZES)
def test_the_alarm_panel_needs_every_press_it_was_made_from_and_locks_soon_after(size):
    g = GAMES["lights"]
    n, k, spare, cost, _ = g.SIZES[size]
    assert spare == (3 if size == "s" else 5 if size == "m" else 6)
    for seed in range(15):
        p = made("lights", size, seed)
        assert p["n"] == n and p["par"] == k == len(g.solve(p)) and p["limit"] == k + spare
        assert "sol" not in g.public(p)
    p = made("lights", size)
    wrong = next(i for i in range(n * n) if i not in p["sol"])
    g.move(p, {"at": wrong})                                # a wrong tap, put right again, then the rest
    g.move(p, {"at": wrong})
    for mv in g.solve(p):
        g.move(p, mv)
    assert g.done(p) and g.score(p) == pytest.approx(1 - 2 * cost)       # 0.8 on the small panel, 0.9 on the others


def test_a_panel_left_part_dark_gets_more_credit_on_the_bigger_panels():
    g = GAMES["lights"]
    for size, share in (("s", 0.5), ("m", 0.6), ("big", 0.6)):
        p = made("lights", size)
        assert g.score(p) == pytest.approx(share * (1 - sum(p["on"]) / len(p["on"])))


@pytest.mark.parametrize("size", SIZES)
def test_the_wire_page_gets_the_drawing_and_not_which_wire_it_is(size):
    g = GAMES["wire"]
    for seed in range(20):
        p = made("wire", size, seed)
        pub = g.public(p)
        assert set(pub) == {"n", "at", "rounds", "got", "wires", "lamps", "lamp", "t0", "fuse"}
        assert all(set(w) == {"c", "pts"} for w in pub["wires"])
        for rd in p["rounds"]:
            n, gap = p["n"], 100 / p["n"]
            # the drawing does say it, for anyone who follows the wire: it starts at the terminal and ends at the lamp
            ends = {round(w["pts"][0][1] / gap - 0.5): round(w["pts"][-1][1] / gap - 0.5) for w in rd["wires"]}
            assert sorted(ends) == list(range(n)) and ends[rd["answer"]] == rd["lamp"]
            # and the wire's colour alone won't: another wire of its colour crosses it
            cut = next(w for w in rd["wires"] if round(w["pts"][0][1] / gap - 0.5) == rd["answer"])
            assert sum(w["c"] == cut["c"] for w in rd["wires"]) >= 2


def test_the_wire_scores_by_rounds_cut_right_before_the_fuse_goes_out():
    g = GAMES["wire"]
    p = made("wire", "m", 3)
    answer = [rd["answer"] for rd in p["rounds"]]
    with pytest.raises(Refuse):
        g.move(p, {"pick": answer[0]})                      # no time on it
    assert g.public(p)["fuse"] == 7000 + 2000               # the first round's longer, to take it in
    g.move(p, {"pick": (answer[0] + 1) % p["n"], "t": 3000})     # the wrong wire
    g.move(p, {"pick": answer[1], "t": 6000})                    # right, in 3s
    assert g.public(p)["got"] == [False, True] and g.public(p)["t0"] == 6000 and g.public(p)["fuse"] == 7000
    g.move(p, {"pick": answer[2], "t": 6000 + 7000 + 500})      # right, but the fuse had gone out
    g.move(p, {"pick": answer[3], "t": 1000})                    # the page's clock started again (a reload): 1s
    assert g.done(p) and g.public(p)["got"] == [False, True, False, True] and g.score(p) == pytest.approx(0.5)
    p = made("wire", "m", 4)
    g.move(p, {"pick": -1, "t": 9500})                            # the page says the fuse went out
    assert g.public(p)["got"] == [False]


@pytest.mark.parametrize("size,lo,hi", [("s", 5, 6), ("m", 7, 7), ("big", 8, 10)])
def test_the_password_is_never_shown_and_its_length_fits_the_size(size, lo, hi):
    g = GAMES["password"]
    for word in g.WORDS[size].split():
        assert lo <= len(word) <= hi and word.isalpha() and word.isupper()
    for seed in range(20):
        p = made("password", size, seed)
        pub = json.dumps(g.public(p))
        assert p["word"] not in pub and "word" not in g.public(p)


def test_the_password_scores_by_misses_and_gives_credit_for_letters_found():
    g = GAMES["password"]
    p = {"word": "NELSON", "tried": []}
    for ch in "NZ":
        g.move(p, {"letter": ch})
    assert g.public(p)["shown"] == ["N", "", "", "", "", "N"] and g.public(p)["misses"] == 1
    with pytest.raises(Refuse):
        g.move(p, {"letter": "n"})                          # tried already, in any case
    for ch in "ELSO":
        g.move(p, {"letter": ch})
    assert g.done(p) and g.score(p) == 1.0                  # one wrong letter's free
    p = {"word": "NELSON", "tried": list("NQXZJKV")}
    assert g.done(p) and g.score(p) == pytest.approx(0.5 * 2 / 6)


@pytest.mark.parametrize("size", SIZES)
def test_the_power_grid_keeps_its_path_secret_and_lights_what_the_power_reaches(size):
    g = GAMES["grid"]
    for seed in range(15):
        p = made("grid", size, seed)
        pub = g.public(p)
        assert set(pub) == {"n", "tiles", "lit", "on", "src", "dst", "turns", "limit"} and not pub["on"]
        lo, hi = {"s": (9, 12), "m": (10, 13), "big": (13, 17)}[size]
        assert lo <= len(p["path"]) <= hi
        assert g._shortest(p) <= len(p["path"])                # the laid path's a way through, whatever else is
        for mv in g.solve(p):
            g.move(p, mv)
        pub = g.public(p)
        assert pub["on"] and pub["lit"][p["src"] * p["n"]] and g.score(p) == 1.0


def test_a_tile_turns_clockwise_and_the_power_follows_the_ends():
    assert _turn(1) == 2 and _turn(8) == 1 and _turn(5) == 10 and _turn(_turn(_turn(_turn(7)))) == 7
    g = GAMES["grid"]
    # a straight across the top row of a 2x2 board, the source and the alarm box both on row 0
    p = {"n": 2, "tiles": [10, 5, 0, 0], "src": 0, "dst": 0, "path": [0, 1], "sol": [10, 10], "turns": 0, "par": 1,
         "limit": 9}
    assert g.public(p)["lit"] == [1, 0, 0, 0] and not g.done(p)
    assert g.score(p) == pytest.approx(0.3)                 # the power's halfway along
    g.move(p, {"at": 1})
    assert g.public(p)["on"] and g.done(p) and g.score(p) == 1.0


@pytest.mark.parametrize("size", SIZES)
def test_the_logic_lock_has_exactly_one_answer_and_never_shows_it(size):
    g = GAMES["logic"]
    for seed in range(25):
        p = made("logic", size, seed)
        codes = ["".join(c) for c in itertools.permutations(p["digits"], p["len"])]
        fits = [c for c in codes if all(_clue(c, cl["code"]) == (cl["right"], cl["near"]) for cl in p["clues"])]
        assert fits == [p["code"]]
        assert any(cl["right"] == cl["near"] == 0 for cl in p["clues"])
        pub = g.public(p)
        assert "code" not in pub and p["code"] not in [cl["code"] for cl in pub["clues"]]


def test_the_logic_lock_scores_by_the_try_and_a_wrong_try_says_how_close():
    g = GAMES["logic"]
    p = {"len": 3, "digits": "123456", "code": "453", "clues": [], "tries": []}
    with pytest.raises(Refuse):
        g.move(p, {"code": "445"})                          # every digit different
    g.move(p, {"code": "435"})
    assert g.public(p)["tries"] == [{"code": "435", "right": 1, "near": 2}]
    assert not g.done(p)
    g.move(p, {"code": "453"})
    assert g.done(p) and g.score(p) == 0.6                  # the second and last try


@pytest.mark.parametrize("size", SIZES)
def test_the_sliding_tiles_take_at_least_so_many_slides_to_put_back(size):
    g = GAMES["tiles"]
    for seed in range(15):
        p = made("tiles", size, seed)
        assert sorted(p["at"]) == list(range(9)) and p["at"] != list(range(9))
        assert len(g.solve(p)) == p["par"] >= g.SIZES[size][1]
        assert set(g.public(p)) == {"at", "moves", "limit"}


def test_the_sliding_tiles_only_slide_next_to_the_gap_and_score_by_slides():
    g = GAMES["tiles"]
    p = {"at": [0, 1, 2, 3, 4, 5, 6, 8, 7], "par": 8, "moves": 0, "limit": 44}
    with pytest.raises(Refuse):
        g.move(p, {"at": 0})
    assert g.score(p) == pytest.approx(0.5 * 7 / 8)
    for _ in range(8):                                      # back and forth: eight slides wasted
        g.move(p, {"at": 8})
        g.move(p, {"at": 7})
    g.move(p, {"at": 8})
    assert g.done(p) and g.score(p) == pytest.approx(1 - 0.3 * 7 / 8)     # seven over par and its two spare
