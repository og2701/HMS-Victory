"""What the indirect-clue bar admits, and what it must not.

The bar decides which words can appear in a hard puzzle at all, so it is the difficulty
control. It used to accept any clue carrying wordplay punctuation, which let the easiest
things in the bank through on a technicality - "We?" for SHALL, "Go ..." for DUTCH - while
the point of the bar was to keep exactly those out.

Runnable under pytest or straight from the stdlib.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import crossword_bank as B


def test_a_marker_alone_no_longer_carries_a_clue():
    """These are the shortest clues the old bar accepted, and the easiest in the bank."""
    for clue in ("We?", "You!", "That!", "Go ...", "At ...", "No ...", "Up ...", "... so"):
        assert not B._is_indirect(clue), f"{clue!r} should not clear the bar"


def test_double_definitions_are_kept_even_when_short():
    """Two readings to reconcile is the good stuff, and it can be stated briefly."""
    for clue in ("Safe; or fasten", "Pine, or yearn", "Soft; or an offer"):
        assert B._is_indirect(clue), f"{clue!r} should clear the bar"


def test_a_clue_without_a_second_reading_has_to_say_something():
    assert not B._is_indirect("Counsel")                    # a dictionary entry
    assert not B._is_indirect("Go ...")                     # the phrase is the whole clue
    assert B._is_indirect("Top of the pack, or a serve you can't return")
    # Four flat words clears the bar, and ideally wouldn't - "Sap the strength of" is not
    # much of a puzzle. Raising the floor to five was measured and it starves the grid
    # search: 1074 words could not fill a single 6x6, against 1170 that fills in seconds.
    # Making these harder means writing better clues, not moving this number.
    assert B._is_indirect("Sap the strength of")


def test_the_bank_still_fills_every_shelf_a_six_by_six_needs():
    """Tightening the bar shrinks the bank, and a shelf that empties makes the generator
    unable to build a grid at all - the failure is silent, so it gets asserted here."""
    from collections import Counter
    by_length = Counter(len(w) for w in B.HARD)
    for n in (3, 4, 5, 6):
        assert by_length[n] >= 100, f"only {by_length[n]} {n}-letter words left"


def test_the_bar_governs_the_double_definitions_but_not_the_vocabulary():
    """Two tiers, two standards, on purpose.

    The bar exists to stop a bare synonym passing as a clue for an everyday word, so it
    demands the clue say enough to be worth solving. That test is meaningless for the
    vocabulary tier, where the difficulty is carried by the ANSWER and the clue should be
    terse: "Frustrate a plan" is exactly right for THWART and is three words long.
    """
    assert B.HARD, "hard bank is empty"
    for word, clue in B.HARD.items():
        if word in B.VOCAB:
            continue
        assert B._is_indirect(clue), f"{word} kept a clue that fails the bar: {clue!r}"


def test_the_vocabulary_tier_is_present_and_reaches_every_shelf():
    """This is where difficulty now lives, so a shelf going empty would quietly undo it."""
    from collections import Counter
    assert len(B.VOCAB) >= 300, f"vocabulary tier is only {len(B.VOCAB)} words"
    by_length = Counter(len(w) for w in B.VOCAB)
    for n in (3, 4, 5, 6):
        assert by_length[n] >= 20, f"only {by_length[n]} {n}-letter harder words"
    # Every one has to be usable in a 6x6, and reachable by the fill.
    assert all(3 <= len(w) <= 6 for w in B.VOCAB)
    assert all(w in B.HARD for w in B.VOCAB), "vocabulary is not reaching the fill"


def test_no_clue_contains_its_own_answer():
    """The one quality failure that IS mechanically detectable.

    The bar judges a clue's shape and can say nothing about whether the answer is sitting
    in the text - "EVER | For ... and ever" cleared it comfortably while printing the word
    it was asking for. Six were in the bank and two had shipped.
    """
    import re
    for word, clue in B.HARD.items():
        assert not re.search(rf"\b{re.escape(word.lower())}\b", clue.lower()), \
            f"{word} gives itself away: {clue!r}"


def test_no_shipped_puzzle_gives_its_answer_away():
    """Same rule, against what players actually get - the bank and the shipped sets can
    drift, because a set bakes its clues in at generation time."""
    import json
    import os
    import re
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "words", "crosswords.json")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    for st in doc["sets"]:
        for puzzle in st["puzzles"]:
            for entry in puzzle["entries"]:
                answer, clue = entry["answer"].lower(), entry["clue"].lower()
                assert not re.search(rf"\b{re.escape(answer)}\b", clue), \
                    f"set {st['from']}: {entry['answer']} gives itself away: {entry['clue']!r}"


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
