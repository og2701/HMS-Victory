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

import datetime

DOC = json.load(open(os.path.join(ROOT, "data", "words", "crosswords.json"),
                     encoding="utf-8"))
SETS = DOC["sets"] if isinstance(DOC, dict) else [{"from": "2024-01-01", "size": 5,
                                                   "puzzles": DOC}]
PUZZLES = [p for s in SETS for p in s["puzzles"]]          # every shipped grid, all eras


def _size_of(p):
    for s in SETS:
        if p in s["puzzles"]:
            return int(s.get("size", 5))
    return 5


def _grid(p):
    g = {}
    for e in p["entries"]:
        for i, cell in enumerate(e["cells"]):
            g[tuple(cell)] = e["answer"][i]
    return g


def test_every_shipped_puzzle_is_structurally_sound():
    assert len(PUZZLES) >= 50
    for p in PUZZLES:
        N = _size_of(p)
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
        N = _size_of(p)
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
    """Within a set only. Two puzzles in different eras are months apart in play, so
    sharing a few words there is invisible to anyone."""
    for si, st in enumerate(SETS):
        sigs = [set(e["answer"] for e in q["entries"]) for q in st["puzzles"]]
        for i, a in enumerate(sigs):
            for j, b in enumerate(sigs):
                if i < j:
                    assert len(a & b) <= 5, \
                        f"set {si} puzzles {i+1}/{j+1} share {len(a & b)} words"


def test_puzzle_of_the_day_is_stable_and_rotates_within_its_set():
    """Rotation is checked inside each set's own window - a later set can start before the
    earlier one has finished its cycle, and that's fine: it supersedes it."""
    for i, s in enumerate(SETS):
        start = datetime.date.fromisoformat(s["from"])
        n = len(s["puzzles"])
        nxt = (datetime.date.fromisoformat(SETS[i + 1]["from"]) if i + 1 < len(SETS)
               else start + datetime.timedelta(days=n + 1))
        window = min(n, (nxt - start).days)
        assert X._todays_puzzle(start) is X._todays_puzzle(start)      # same day, same grid
        seen = {id(X._todays_puzzle(start + datetime.timedelta(days=k)))
                for k in range(window)}
        assert len(seen) == window                                     # no repeats early
        if window == n:                                                # full cycle visible
            assert (X._todays_puzzle(start)
                    is X._todays_puzzle(start + datetime.timedelta(days=n)))


def test_solving_every_clue_completes_and_pays_top_tier():
    import datetime
    d, uid = datetime.date(2026, 3, 4), 4242
    p = X._todays_puzzle(d)
    for e in p["entries"]:
        pl = X._player(d.isoformat(), uid)
        if X._key(e) in pl["solved"]:
            continue                                # a crossing already filled this one
        status, _msg, pl = X.submit(uid, d.isoformat(), p, X._key(e), e["answer"])
        assert status == "ok"
    pl = X._player(d.isoformat(), uid)
    assert pl["done"] and len(pl["solved"]) == len(p["entries"])
    assert X.reward_for(pl, d) == X.rules(d)["rewards"][0]      # clean solve -> top payout


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
    tiers = X.rules(d)["rewards"]
    pl = X._player(d.isoformat(), uid)
    assert X.reward_for(pl, d) == tiers[0]
    for i in range(1, len(tiers)):
        msg, pl = X.reveal_letter(uid, d.isoformat(), p, d)
        assert msg and X.reward_for(pl, d) == tiers[i]
    # the floor holds: more reveals never pay less than the last tier
    for _ in range(4):
        _msg, pl = X.reveal_letter(uid, d.isoformat(), p, d)
    assert X.reward_for(pl, d) == tiers[-1]


def test_hints_move_on_once_an_entry_is_fully_revealed():
    """Regression: the hint used to lock onto the shortest unsolved entry and, once every
    letter of it was given away, return nothing forever - so a player could be left unable
    to take a hint with nine clues still blank."""
    import datetime
    d, uid = datetime.date(2026, 3, 10), 4248
    p = X._todays_puzzle(d)
    shortest = min(len(e["answer"]) for e in p["entries"])

    # exhaust the shortest entry, then keep going
    for _ in range(shortest):
        msg, _pl = X.reveal_letter(uid, d.isoformat(), p)
        assert msg
    msg, pl = X.reveal_letter(uid, d.isoformat(), p)
    assert msg, "hint stopped working after the shortest entry ran out"

    # every hint puts a NEW letter on the board - never one a crossing already showed,
    # which would charge a reward tier for nothing
    for _ in range(60):
        before = X._letters(p, X._player(d.isoformat(), uid))
        msg, pl = X.reveal_letter(uid, d.isoformat(), p)
        if msg is None:
            break
        after = X._letters(p, X._player(d.isoformat(), uid))
        assert len(after) > len(before), "a hint revealed a letter that was already visible"
    # ...and they run out only once there's nothing left to give
    msg, pl = X.reveal_letter(uid, d.isoformat(), p)
    assert msg is None
    assert pl["done"] or len(pl["solved"]) == len(p["entries"])


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


# --- date-gated rule changes ---------------------------------------------------------
LEGACY = datetime.date(2026, 7, 31)          # the last day of the original 5x5 era
HARD = datetime.date(2026, 8, 1)             # first day of the tightened 6x6 era


def test_a_rules_change_never_alters_a_live_puzzle():
    """The whole point of date-gating. Someone mid-solve on the old rules must keep the
    old grid AND the old payout, however much the new set tightens things."""
    import subprocess
    v1 = json.loads(subprocess.check_output(
        ["git", "show", "c3fde93:data/words/crosswords.json"], cwd=ROOT))
    epoch = datetime.date(2024, 1, 1)
    d = epoch
    while d < HARD:
        want = v1[(d - epoch).days % len(v1)]
        got = X._todays_puzzle(d)
        assert [e["answer"] for e in want["entries"]] == [e["answer"] for e in got["entries"]], d
        assert X.rules(d)["rewards"][0] == 250, d
        assert X.rules(d)["size"] == 5, d
        d += datetime.timedelta(days=29)      # sample the era rather than all 900 days


def test_the_new_set_is_bigger_harder_and_cheaper():
    old, new = X.rules(LEGACY), X.rules(HARD)
    assert new["size"] > old["size"]                          # bigger grid
    assert len(X._todays_puzzle(HARD)["entries"]) > len(X._todays_puzzle(LEGACY)["entries"])
    assert new["rewards"][0] < old["rewards"][0]              # pays less
    assert new["max_hints"] and not old["max_hints"]          # hints are now finite
    assert new["wrong_per_tier"] and not old["wrong_per_tier"]  # wrong answers now cost


def test_wrong_answers_cost_a_tier_under_the_new_rules():
    uid = 5001
    p = X._todays_puzzle(HARD)
    per = X.rules(HARD)["wrong_per_tier"]
    tiers = X.rules(HARD)["rewards"]
    entry = p["entries"][0]
    key = X._key(entry)
    assert X.reward_for(X._player(HARD.isoformat(), uid), HARD) == tiers[0]
    for _ in range(per):
        X.submit(uid, HARD.isoformat(), p, key, "Z" * len(entry["answer"]))
    pl = X._player(HARD.isoformat(), uid)
    assert pl["wrong"] == per
    assert X.reward_for(pl, HARD) == tiers[1], "a run of wrong answers should cost a tier"
    # ...and the legacy era is untouched by the penalty
    assert X.penalties({"revealed": [], "wrong": 99}, LEGACY)[1] == 0


def test_hints_are_capped_under_the_new_rules():
    uid = 5002
    p = X._todays_puzzle(HARD)
    cap = X.rules(HARD)["max_hints"]
    for i in range(cap):
        msg, pl = X.reveal_letter(uid, HARD.isoformat(), p, HARD)
        assert msg, f"hint {i + 1} of {cap} should be allowed"
    msg, pl = X.reveal_letter(uid, HARD.isoformat(), p, HARD)
    assert msg is None, "hints past the cap must be refused"
    assert len(pl["revealed"]) == cap


def test_crossings_fill_in_the_entries_they_complete():
    """A crossword fills itself sideways: get every Down and the Acrosses are already on
    the board. Before this, the grid could be visibly complete while the game insisted you
    weren't finished - and the last clue was unanswerable, since all its letters showed."""
    import datetime
    d, uid = datetime.date(2026, 8, 5), 9100
    p = X._todays_puzzle(d)
    downs = [e for e in p["entries"] if e["dir"] == "down"]
    across = [e for e in p["entries"] if e["dir"] == "across"]
    assert downs and across

    for e in downs:
        X.submit(uid, d.isoformat(), p, X._key(e), e["answer"])
    pl = X._player(d.isoformat(), uid)
    # every Across is spelled out by the Downs on a fully-crossed grid, so all of them
    # should now be marked - and the puzzle finished without typing a single Across
    assert len(pl["solved"]) == len(p["entries"]), pl["solved"]
    assert pl["done"]


def test_the_cascade_reports_what_it_filled_in():
    import datetime
    d, uid = datetime.date(2026, 8, 5), 9101
    p = X._todays_puzzle(d)
    downs = [e for e in p["entries"] if e["dir"] == "down"]
    msgs = []
    for e in downs:
        _st, msg, _pl = X.submit(uid, d.isoformat(), p, X._key(e), e["answer"])
        if msg:
            msgs.append(msg)
    assert msgs, "finishing the Downs should announce the Acrosses it completed"
    assert "filled in" in msgs[-1]


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
