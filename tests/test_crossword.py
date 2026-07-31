"""HMS Crossword tests: the shipped puzzle set, and the play/reward state machine.

The puzzle-set tests are the important ones. A crossword with a bad crossing or a missing
clue is unplayable and there's no way for a player to work around it, so every shipped
grid is checked cell by cell rather than trusted from the generator.

Runnable under pytest or straight from the stdlib (`python3 tests/test_crossword.py`).
"""
import os
import sys
import json
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
config.CROSSWORD_STATE_FILE = os.path.join(tempfile.mkdtemp(prefix="xw_"), "state.json")

from lib.features import crossword as X

N = X.N
PUZZLES = json.load(open(os.path.join(ROOT, "data", "words", "crosswords.json"),
                         encoding="utf-8"))


def _grid(p):
    g = {}
    for e in p["entries"]:
        for i, cell in enumerate(e["cells"]):
            g[tuple(cell)] = e["answer"][i]
    return g


def test_every_shipped_puzzle_is_structurally_sound():
    assert len(PUZZLES) >= 25
    for p in PUZZLES:
        black = {tuple(b) for b in p["black"]}
        grid = {}
        for e in p["entries"]:
            cells = [tuple(c) for c in e["cells"]]
            assert len(e["answer"]) == len(cells), (p["id"], e["answer"])
            assert len(cells) >= 3, (p["id"], e["answer"])       # no 2-letter entries
            assert e["answer"].isalpha() and e["answer"].isupper(), (p["id"], e["answer"])
            assert e["clue"].strip(), (p["id"], e["answer"])     # every entry is clued
            assert e["dir"] in ("across", "down")
            # cells must be contiguous and in line
            rs = {c[0] for c in cells}
            cs = {c[1] for c in cells}
            if e["dir"] == "across":
                assert len(rs) == 1 and cs == set(range(min(cs), max(cs) + 1))
            else:
                assert len(cs) == 1 and rs == set(range(min(rs), max(rs) + 1))
            for i, cell in enumerate(cells):
                assert cell not in black, (p["id"], "entry crosses a black square")
                # crossings must agree - the whole point of a crossword
                assert grid.get(cell, e["answer"][i]) == e["answer"][i], \
                    (p["id"], f"crossing conflict at {cell}")
                grid[cell] = e["answer"][i]
        # no white cell is left out of both an across and a down entry
        for r in range(N):
            for c in range(N):
                if (r, c) not in black:
                    assert (r, c) in grid, (p["id"], f"orphan cell {(r, c)}")
        assert any(e["dir"] == "across" for e in p["entries"]), p["id"]
        assert any(e["dir"] == "down" for e in p["entries"]), p["id"]


def test_clue_numbering_follows_crossword_convention():
    """Numbers run left-to-right, top-to-bottom, and a cell that starts both an across and
    a down entry carries ONE number shared by the two."""
    for p in PUZZLES:
        black = {tuple(b) for b in p["black"]}
        expected, n = {}, 0
        for r in range(N):
            for c in range(N):
                if (r, c) in black:
                    continue
                a = (c == 0 or (r, c - 1) in black) and c + 1 < N and (r, c + 1) not in black
                d = (r == 0 or (r - 1, c) in black) and r + 1 < N and (r + 1, c) not in black
                if a or d:
                    n += 1
                    expected[(r, c)] = n
        for e in p["entries"]:
            start = tuple(e["cells"][0])
            assert expected.get(start) == e["num"], (p["id"], e["num"], start)


def test_puzzles_are_distinct_enough_to_feel_different():
    sigs = [set(e["answer"] for e in p["entries"]) for p in PUZZLES]
    for i, a in enumerate(sigs):
        for j, b in enumerate(sigs):
            if i < j:
                assert len(a & b) <= 3, f"puzzles {i+1}/{j+1} share {len(a & b)} words"


def test_puzzle_of_the_day_is_stable_and_rotates():
    import datetime
    d = datetime.date(2026, 1, 1)
    assert X._todays_puzzle(d) is X._todays_puzzle(d)                 # same day, same grid
    seen = {id(X._todays_puzzle(d + datetime.timedelta(days=k))) for k in range(len(PUZZLES))}
    assert len(seen) == len(PUZZLES)                                   # cycles through all
    # and wraps back round
    assert X._todays_puzzle(d) is X._todays_puzzle(d + datetime.timedelta(days=len(PUZZLES)))


def test_solving_every_clue_completes_and_pays_top_tier():
    import datetime
    d, uid = datetime.date(2026, 3, 4), 4242
    p = X._todays_puzzle(d)
    for e in p["entries"][:-1]:
        status, _msg, pl = X.submit(uid, d.isoformat(), p, X._key(e), e["answer"])
        assert status == "ok"
        assert not pl["done"]                       # not finished until the last one
    last = p["entries"][-1]
    status, _msg, pl = X.submit(uid, d.isoformat(), p, X._key(last), last["answer"])
    assert status == "ok" and pl["done"]
    assert X.reward_for(pl) == config.CROSSWORD_REWARDS[0]      # no hints -> top payout


def test_wrong_and_malformed_answers_are_rejected_without_progress():
    import datetime
    d, uid = datetime.date(2026, 3, 5), 4243
    p = X._todays_puzzle(d)
    e = p["entries"][0]
    key = X._key(e)

    status, msg, _pl = X.submit(uid, d.isoformat(), p, key, "ZZZZZZZ")
    assert status == "invalid" and "letters" in msg          # wrong length
    status, _m, pl = X.submit(uid, d.isoformat(), p, key, "Q" * len(e["answer"]))
    assert status == "wrong" and pl["wrong"] == 1            # right length, wrong word
    assert key not in pl["solved"]

    # case and stray punctuation are forgiven - it's a word, not a password
    status, _m, pl = X.submit(uid, d.isoformat(), p, key, e["answer"].lower())
    assert status == "ok" and key in pl["solved"]
    status, _m, _pl = X.submit(uid, d.isoformat(), p, key, e["answer"])
    assert status == "already"                                # no double-solving

    status, msg, _pl = X.submit(uid, d.isoformat(), p, "99-across", "ANYTHING")
    assert status == "invalid"


def test_revealing_letters_costs_reward_tiers():
    import datetime
    d, uid = datetime.date(2026, 3, 6), 4244
    p = X._todays_puzzle(d)
    tiers = config.CROSSWORD_REWARDS
    pl = X._player(d.isoformat(), uid)
    assert X.reward_for(pl) == tiers[0]
    for i in range(1, len(tiers)):
        msg, pl = X.reveal_letter(uid, d.isoformat(), p)
        assert msg and X.reward_for(pl) == tiers[i]
    # the floor holds: more reveals never pay less than the last tier
    for _ in range(4):
        _msg, pl = X.reveal_letter(uid, d.isoformat(), p)
    assert X.reward_for(pl) == tiers[-1]


def test_hints_move_on_once_an_entry_is_fully_revealed():
    """Regression: the hint used to lock onto the shortest unsolved entry and, once every
    letter of it was given away, return nothing forever - so a player could be left unable
    to take a hint with nine clues still blank."""
    import datetime
    d, uid = datetime.date(2026, 3, 10), 4248
    p = X._todays_puzzle(d)
    shortest = min(len(e["answer"]) for e in p["entries"])
    total_letters = sum(len(e["answer"]) for e in p["entries"])

    # exhaust the shortest entry, then keep going
    for _ in range(shortest):
        msg, _pl = X.reveal_letter(uid, d.isoformat(), p)
        assert msg
    msg, pl = X.reveal_letter(uid, d.isoformat(), p)
    assert msg, "hint stopped working after the shortest entry ran out"
    assert len(pl["revealed"]) == shortest + 1

    # hints only dry up when the whole grid has been given away
    for _ in range(total_letters):
        X.reveal_letter(uid, d.isoformat(), p)
    msg, pl = X.reveal_letter(uid, d.isoformat(), p)
    assert msg is None
    assert len(pl["revealed"]) == total_letters


def test_revealed_letters_show_on_the_board_before_the_entry_is_solved():
    import datetime
    d, uid = datetime.date(2026, 3, 7), 4245
    p = X._todays_puzzle(d)
    assert X._letters(p, X._player(d.isoformat(), uid)) == {}    # nothing visible yet
    X.reveal_letter(uid, d.isoformat(), p)
    seen = X._letters(p, X._player(d.isoformat(), uid))
    assert len(seen) == 1
    (cell, ch), = seen.items()
    assert _grid(p)[cell] == ch                                  # and it's the right letter


def test_share_block_never_leaks_an_answer():
    import datetime
    d, uid = datetime.date(2026, 3, 8), 4246
    p = X._todays_puzzle(d)
    for e in p["entries"]:
        X.submit(uid, d.isoformat(), p, X._key(e), e["answer"])
    block = X.share_block(uid, d)
    for e in p["entries"]:
        assert e["answer"] not in block.upper(), e["answer"]
    assert "10/10" in block or f"{len(p['entries'])}/{len(p['entries'])}" in block


def test_board_html_renders_and_hides_unsolved_answers():
    import datetime
    d, uid = datetime.date(2026, 3, 9), 4247
    p = X._todays_puzzle(d)
    first = p["entries"][0]
    X.submit(uid, d.isoformat(), p, X._key(first), first["answer"])
    html = X._board_html(uid, d).upper()
    assert "HMS CROSSWORD" in html and "ACROSS" in html and "DOWN" in html
    # a solved entry's letters are on the grid; an unsolved one's are not spelled out
    unsolved = [e for e in p["entries"]
                if e["dir"] == first["dir"] and e is not first and len(e["answer"]) == 5]
    for e in unsolved:
        assert e["answer"] not in html.replace("<", " ").replace(">", " "), e["answer"]


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            print(f"ERROR {t.__name__}: {e!r}")
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
