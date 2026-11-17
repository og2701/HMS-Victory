"""The Face's mini-games (lib/activities/heist_games/face.py), beyond the contract every game keeps: what the page is
shown never gives the answer away, and each is scored the way it says."""

import copy
import json
import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "dev")

from lib.activities.heist_games import Refuse  # noqa: E402
from lib.activities.heist_games import face as F  # noqa: E402

STORY = {"name": "Denise Wright", "firm": "Thames Water", "why": "a leak", "floor": "the second floor", "ref": "7354",
         "boss": "Julie in head office"}
SIZES = ["s", "m", "big"]


def shown(g, p) -> str:
    return json.dumps(g.public(p))


def play(g, p, moves):
    for mv in moves:
        g.move(p, json.loads(json.dumps(mv)))


@pytest.mark.parametrize("size", SIZES)
def test_quizzes_never_show_which_answer_is_right(size):
    for g in (F.Talk(), F.Names(), F.Rota()):
        for seed in range(20):
            p = g.make(random.Random(seed), size, STORY)
            moves = g.solve(copy.deepcopy(p))
            for mv in moves:
                assert '"answer"' not in shown(g, p) and '"who"' not in shown(g, p)
                g.move(p, mv)


def test_the_rounds_and_the_board_sizes():
    assert [len(F.Talk().make(random.Random(1), z, STORY)["qs"]) for z in SIZES] == [3, 4, 5]
    ids = [F.FakeId().make(random.Random(1), z, STORY) for z in SIZES]
    assert [(len(p["rounds"]), len(p["rounds"][0]["lines"]), p["look"]) for p in ids] == [(2, 4, 9), (3, 5, 8), (4, 6, 9)]
    assert [F.Calm().make(random.Random(1), z, STORY)["secs"] for z in SIZES] == [14, 18, 22]
    names = [F.Names().make(random.Random(1), z, STORY) for z in SIZES]
    assert [(len(p["staff"]), len(p["qs"]), len(p["qs"][0]["options"])) for p in names] == [(4, 2, 3), (5, 4, 3), (6, 5, 4)]
    words = [F.Anagram().make(random.Random(1), z, STORY)["words"] for z in SIZES]
    assert [sorted(len(w["word"]) for w in ws) for ws in words] == [[6], [7], [7, 8]]
    rotas = [F.Rota().make(random.Random(1), z, STORY) for z in SIZES]
    assert [(len(p["days"]), len(p["shifts"]), len(p["qs"])) for p in rotas] == [(5, 3, 3), (6, 3, 4), (7, 4, 5)]


def test_there_is_plenty_of_small_talk_and_every_reply_is_different():
    assert len(F.Talk.BANK) >= 30
    for say, *replies in F.Talk.BANK:
        assert len(set(replies)) == 3 and "\u2014" not in say + "".join(replies)


def test_the_staff_board_goes_when_youre_ready_and_the_question_pins_one_of_them_down():
    g = F.Names()
    for seed in range(30):
        p = g.make(random.Random(seed), "m", STORY)
        assert g.public(p)["staff"] == p["staff"]
        assert len({tuple(x["things"].values()) for x in p["staff"]}) == len(p["staff"])   # everyone different
        names = [x["name"] for x in p["staff"]]
        assert len(set(names)) == len(names)
        with pytest.raises(Refuse):
            g.move(p, {"pick": 0})
        g.move(p, {"ready": True})
        for i, q in enumerate(p["qs"]):
            pub = g.public(p)
            assert "staff" not in pub and pub["cue"] == q["cue"]
            fits = [x["name"] for x in p["staff"] if all(x["things"][k] == v for k, v in q["cue"].items())]
            assert fits == [pub["options"][q["answer"]]]                             # only one person has it
            g.move(p, {"pick": q["answer"], "round": i})
        assert g.score(p) == 1.0


def rota_truth(q, days, shifts, grid):
    """Every right answer to a rota question, read off the grid from the question's own words."""
    one = {v: k for k, v in F.Rota.ONE.items()}
    many = {v: k for k, v in F.Rota.MANY.items()}
    row = lambda name: shifts.index(name)  # noqa: E731
    on = lambda name, s: [d for d in range(len(days)) if grid[s][d] == name]  # noqa: E731
    works = lambda name: {d for r in grid for d, n in enumerate(r) if n == name}  # noqa: E731
    ask = q["ask"]
    if m := re.fullmatch(r"WHO'S ON (\w+) THE DAY AFTER (\w+)'S (.+)\?", ask):
        return {grid[row(m[1])][d + 1] for d in on(m[2], row(one[m[3]])) if d + 1 < len(days)} if len(on(m[2], row(one[m[3]]))) == 1 else None
    if m := re.fullmatch(r"WHO'S ON (\w+) WHEN (\w+)'S ON (\w+)\?", ask):
        return {grid[row(m[1])][d] for d in on(m[2], row(m[3]))} if len(on(m[2], row(m[3]))) == 1 else None
    if m := re.fullmatch(r"WHO'S ON (\w+) (\w+)\?", ask):
        return {grid[row(m[1])][days.index(m[2])]}
    if m := re.fullmatch(r"WHAT'S (\w+) ON (\w+)\?", ask):
        return {shifts[s] for s in range(len(shifts)) if grid[s][days.index(m[2])] == m[1]}
    if m := re.fullmatch(r"WHEN'S (\w+) ON (\w+)\?", ask):
        return {days[d] for d in on(m[1], row(m[2]))}
    if m := re.fullmatch(r"HOW MANY (.+) IS (\w+) ON\?", ask):
        return {str(len(on(m[2], row(many[m[1]]))))}
    if m := re.fullmatch(r"WHICH DAY ARE (\w+) AND (\w+) BOTH ON\?", ask):
        return {days[d] for d in works(m[1]) & works(m[2])}
    raise AssertionError(ask)


@pytest.mark.parametrize("size", SIZES)
def test_every_rota_question_has_one_right_answer(size):
    g = F.Rota()
    kinds = set()
    for seed in range(60):
        p = g.make(random.Random(seed), size, STORY)
        days, shifts, grid = p["days"], p["shifts"], p["grid"]
        counts = {}
        for r in grid:
            for n in r:
                counts[n] = counts.get(n, 0) + 1
        assert set(counts.values()) <= {3, 4}                                    # everyone three or four times
        assert all(len({r[d] for r in grid}) == len(shifts) for d in range(len(days)))  # one shift a day each
        for q in p["qs"]:
            true = rota_truth(q, days, shifts, grid)
            assert true is not None and [o in true for o in q["options"]] == [i == q["answer"] for i in range(3)], (seed, q)
            kinds.add(q["kind"])
        assert len({q["ask"] for q in p["qs"]}) == len(p["qs"])
        assert any(q["kind"] in F.Rota.KINDS["two"] for q in p["qs"])         # always a two-step one
    assert kinds >= {"who", "what", "when", "count", "after", "whenon"} | ({"both"} if size != "s" else set())


def test_the_rota_guard_only_waits_so_long():
    g = F.Rota()
    p = g.make(random.Random(2), "m", STORY)
    assert g.public(p)["patience"] == p["qs"][0]["wait"] and all(6 <= q["wait"] <= 8 for q in p["qs"])
    g.move(p, {"slow": True, "round": 0})
    assert p["picks"] == [None] and g.score(p) == 0
    with pytest.raises(Refuse):
        g.move(p, {"pick": p["qs"][1]["answer"], "round": 0})    # a tap meant for the last question
    g.move(p, {"pick": p["qs"][1]["answer"], "round": 1})
    assert g.score(p) == 0.25


def test_small_talk_replies_start_alike_and_the_guard_waits():
    for say, *replies in F.Talk.BANK:
        assert len({r.split(" ")[0] for r in replies}) == 1, replies
    g = F.Talk()
    p = g.make(random.Random(3), "m", STORY)
    assert g.public(p)["patience"] == 8
    g.move(p, {"slow": True, "round": 0})
    assert p["picks"] == [None]


@pytest.mark.parametrize("size", SIZES)
def test_a_fake_id_has_exactly_one_slip_and_it_stays_hidden_till_the_round_is_over(size):
    g = F.FakeId()
    for seed in range(40):
        p = g.make(random.Random(seed), size, STORY)
        sheet = dict(p["sheet"])
        for r in p["rounds"]:
            diff = [i for i, (label, value) in enumerate(r["lines"]) if value not in sheet.values()]
            assert diff == [r["wrong"]], (seed, r)
        for i, r in enumerate(p["rounds"]):
            pub = g.public(p)
            assert pub["at"] == i and pub["lines"] == r["lines"] and "wrong" not in {**pub, "prev": None}
            g.move(p, {"at": r["wrong"], "round": i})
            assert g.public(p)["prev"]["wrong"] == r["wrong"]


def test_a_fake_id_wrong_tap_costs_and_the_guard_looking_ends_it():
    g = F.FakeId()
    p = g.make(random.Random(7), "m", STORY)
    r0, r1 = p["rounds"][0], p["rounds"][1]
    other = next(i for i in range(len(r0["lines"])) if i != r0["wrong"])
    g.move(p, {"at": other, "round": 0})
    with pytest.raises(Refuse):
        g.move(p, {"at": other, "round": 0})                     # the same line twice
    g.move(p, {"at": r0["wrong"], "round": 0})
    g.move(p, {"look": True, "round": 0})                        # a late one from the page: nothing happens
    assert g.public(p)["at"] == 1 and g.score(p) == pytest.approx(0.5 / 3)
    g.move(p, {"look": True, "round": 1})
    assert r1["end"] == "late" and g.score(p) == pytest.approx(0.5 / 3)
    r2 = p["rounds"][2]
    wrongs = [i for i in range(len(r2["lines"])) if i != r2["wrong"]][:2]
    play(g, p, [{"at": i, "round": 2} for i in wrongs])          # two wrong taps and he's seen you fiddling
    assert g.done(p) and r2["end"] == "caught"


@pytest.mark.parametrize("size", SIZES)
def test_keep_calm_a_cool_head_stays_in_the_green_and_doing_nothing_doesnt(size):
    g = F.Calm()
    for seed in range(30):
        p = g.make(random.Random(seed), size, STORY)
        idle = copy.deepcopy(p)
        g.move(idle, {"holds": [], "end": True})
        assert g.score(idle) < 0.6
        play(g, p, g.solve(copy.deepcopy(p)))
        assert g.score(p) == pytest.approx(1.0)


def test_keep_calm_takes_every_hold_so_far_and_shrugs_off_a_late_list():
    g = F.Calm()
    p = g.make(random.Random(3), "s", STORY)
    g.move(p, {"holds": [[2000.4, 2600]]})
    assert p["holds"] == [[2000, 2600]] and not g.done(p)
    part = g.score(p)
    assert 0 < part < 0.2                                         # scored as it stands: a little of the stare
    g.move(p, {"holds": [[2000, 2600], [4000, 4500]]})
    g.move(p, {"holds": [[2000, 2600]]})                          # an older list turning up late
    assert p["holds"] == [[2000, 2600], [4000, 4500]]
    for bad in ([[2000, 2600], [2500, 3000]], [[3000, 2000]], [[2100, 2600]], [[True, 5]], "x"):
        before = copy.deepcopy(p)
        with pytest.raises(Refuse):
            g.move(p, {"holds": bad})
        assert p == before
    g.move(p, {"holds": [[2000, 2600], [4000, 4500], [6000, 99999]], "end": True})
    assert g.done(p) and p["holds"][-1] == [6000, g._end(p)] and "good" in g.public(p)


def test_unscramble_never_shows_the_word_and_scores_tries_time_and_a_good_start():
    g = F.Anagram()
    p = g.make(random.Random(5), "s", STORY)
    word = p["words"][0]["word"]
    assert word not in shown(g, p) and sorted(g.public(p)["tiles"]) == sorted(word)
    wrong = [word[:2] + "".join(sorted(word[2:])), word[:2] + "".join(sorted(word[2:], reverse=True)),
             word[1] + word[0] + word[2:]]
    wrong = [x for x in dict.fromkeys(wrong) if x != word]
    g.move(p, {"word": wrong[0], "t": 3000})
    assert word not in shown(g, p) and g.public(p)["tries"][0]["right"] is None      # no hint after one wrong go
    assert g.score(p) == pytest.approx(0.4 * F.Anagram._prefix(wrong[0], word) / len(word))
    with pytest.raises(Refuse):
        g.move(p, {"word": "ZZZZZZ", "t": 4000})                 # not the letters on the note
    g.move(p, {"word": wrong[1], "t": 4500})
    assert [t["right"] for t in g.public(p)["tries"]] == [F.Anagram._prefix(x, word) for x in wrong[:2]]
    g.move(p, {"word": word, "t": 6000})
    assert g.done(p) and g.score(p) == pytest.approx(0.6)        # third go, inside par
    slow = g.make(random.Random(5), "s", STORY)
    g.move(slow, {"word": word, "t": 40_000})                    # first go, but five times par
    assert g.score(slow) == pytest.approx(0.6)
    quick = g.make(random.Random(5), "s", STORY)
    g.move(quick, {"word": word, "t": 7_000})
    assert g.score(quick) == pytest.approx(1.0)


def test_unscramble_words_are_well_jumbled_and_none_has_a_twin():
    letters = [("".join(sorted(w)), w) for n in F.Anagram.WORDS.values() for w, _ in n]
    assert len({k for k, _ in letters}) == len(letters)
    g = F.Anagram()
    for seed in range(40):
        for w in g.make(random.Random(seed), "big", STORY)["words"]:
            assert w["tiles"][0] != w["word"][0] and sum(a == b for a, b in zip(w["tiles"], w["word"])) <= 1


def test_unscramble_big_is_two_words_each_timed_from_the_last():
    g = F.Anagram()
    p = g.make(random.Random(8), "big", STORY)
    a, b = (w["word"] for w in p["words"])
    g.move(p, {"word": a, "t": 30_000})
    g.move(p, {"word": b, "t": 35_000})
    assert g.done(p) and g.public(p)["results"][1] == 1.0 and g.public(p)["results"][0] == pytest.approx(0.6)


def test_a_fake_id_slip_only_reads_wrong_next_to_the_sheet():
    g = F.FakeId()
    floors = [x.upper() for x in F.FLOORS]
    her = {x.upper() for x in F.HER}
    story = {**STORY, "why": "a fire door inspection"}
    for size in SIZES:
        for seed in range(60):
            for r in g.make(random.Random(seed), size, story)["rounds"]:
                label, fake = r["lines"][r["wrong"]]
                key = next(k for k, lab in F.FakeId.KINDS[r["kind"]] if lab == label)
                real = story[key].upper()
                if key == "name":                                 # starts the same, and stays a woman's name
                    first, real_first = fake.split(" ")[0], real.split(" ")[0]
                    assert fake[0] == real[0] and first[0] == real_first[0], fake
                    assert first == real_first or first in her or len(first) != len(real_first) or \
                        sum(a != b for a, b in zip(first, real_first)) <= 2, fake          # a slip of the pen
                elif key == "floor":                              # the floor next door
                    assert abs(floors.index(fake) - floors.index(real)) == 1
                elif key == "ref":                                # one digit out, or two swapped
                    assert len(fake) == len(real) and (sorted(fake) == sorted(real) or sum(a != b for a, b in zip(fake, real)) == 1)
                elif key == "why" and size != "s":                # nearly the job
                    assert fake == "A FIRE ALARM INSPECTION"


def test_a_forged_first_name_is_never_a_mans():
    g = F.FakeId()
    men = {x.upper() for x in F.FIRST} - {x.upper() for x in F.HER}
    for size in SIZES:
        for seed in range(200):
            for r in g.make(random.Random(seed), size, STORY)["rounds"]:
                label, fake = r["lines"][r["wrong"]]
                if next(k for k, lab in F.FakeId.KINDS[r["kind"]] if lab == label) == "name":
                    assert fake.split(" ")[0] not in men, fake    # never a man's name next to her photo
