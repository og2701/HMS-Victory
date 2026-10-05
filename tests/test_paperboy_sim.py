"""Paperboy's rules, the bot's copy, against runs recorded through the page's copy
(ukplace-activities scripts/paperboy-vectors.ts): the same inputs must give exactly the same run."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.activities import paperboy_sim as S  # noqa: E402

VECTORS = os.path.join(os.path.dirname(__file__), "data", "paperboy_vectors.json")


def test_the_bot_replays_runs_exactly_as_the_page_played_them():
    with open(VECTORS) as f:
        runs = json.load(f)
    assert len(runs) >= 9
    for v in runs:
        assert S.replay(v["seed"], v["inputs"], v["maxSteps"]) == v["result"], v["seed"]


def test_inputs_that_arent_inputs_are_ignored():
    assert S.clean_inputs([[5, 1], [3, 9], ["x", 1], [-1, 2], [7, True], "nope", [8, 4]]) == [[5, 1], [8, 4]]
    assert S.clean_inputs("nope") == []


def test_a_faked_score_counts_for_nothing():
    # no inputs at all: no throws, so no score, however long it went on
    assert S.replay("f00d", [], 120 * 30)["score"] == 0
    # throwing papers at nothing scores nothing either
    assert S.replay("f00d", [[k * 10, 4] for k in range(10)], 120 * 5)["score"] == 0
