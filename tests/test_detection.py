"""Detection rules and the review actions that act on them.

The rules matter more than they look: each one decides whether a real person gets accused
of cheating, so the tests lean on the edges - one second either side of a floor, one game
short of a threshold - because that is where a rule either catches nothing or catches
everybody.
"""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import config

_TMP = tempfile.mkdtemp(prefix="detection_test_")
config.SKYRIM_PROFILES_FILE = os.path.join(_TMP, "profiles.json")

from lib.core import detection as D
from lib.core import detection_rules as R


# --- Module A: Wordle -------------------------------------------------------------
def test_fast_solve_is_flagged_but_a_paced_one_is_not():
    now = 1_000_000
    fast = R.wordle_solve_findings([now + 2], now, solved=True)
    assert [k for k, _ in fast] == [D.WORDLE_FAST_SOLVE]

    paced = R.wordle_solve_findings([now + 30], now, solved=True)
    assert paced == []


def test_the_solve_floor_is_inclusive_at_the_boundary():
    now = 1_000_000
    floor = config.WORDLE_MIN_SOLVE_SECONDS
    assert R.wordle_solve_findings([now + floor], now, solved=True) == []
    just_under = R.wordle_solve_findings([now + floor - 0.1], now, solved=True)
    assert [k for k, _ in just_under] == [D.WORDLE_FAST_SOLVE]


def test_an_unfinished_or_unmeasurable_game_is_never_judged():
    """Games started before this shipped have no opened_at. Absence of evidence must not
    become evidence - otherwise every pre-existing game flags on the first day."""
    assert R.wordle_solve_findings([1_000_000], None, solved=True) == []
    assert R.wordle_solve_findings([], 1_000_000, solved=True) == []
    assert R.wordle_solve_findings([1_000_000], 999_000, solved=False) == []


def test_a_dummy_guess_before_the_answer_is_caught():
    """The dodge for a first-try penalty: throw one word away, then type the answer. A
    human who just guessed wrong needs a moment; someone reading an answer does not."""
    now = 1_000_000
    findings = R.wordle_solve_findings([now, now + 1], now - 60, solved=True)
    assert D.WORDLE_DUMMY_GUESS in [k for k, _ in findings]

    thinking = R.wordle_solve_findings([now, now + 45], now - 60, solved=True)
    assert D.WORDLE_DUMMY_GUESS not in [k for k, _ in thinking]


# --- Module B: Crossword ----------------------------------------------------------
def test_a_clean_sub_minute_grid_is_flagged():
    findings = R.crossword_solve_findings(1_000_000, 1_000_030, hints=0, wrong=0,
                                          order=[], entry_count=12)
    assert [k for k, _ in findings] == [D.CROSSWORD_FAST_SOLVE]


def test_hints_or_wrong_answers_exempt_a_fast_grid():
    """A genuine solve that fast would have neither, so their presence is evidence of
    somebody actually working the grid rather than copying a finished one."""
    assert R.crossword_solve_findings(1_000_000, 1_000_030, hints=1, wrong=0,
                                      order=[], entry_count=12) == []
    assert R.crossword_solve_findings(1_000_000, 1_000_030, hints=0, wrong=2,
                                      order=[], entry_count=12) == []


def test_a_slow_grid_is_left_alone():
    assert R.crossword_solve_findings(1_000_000, 1_000_600, hints=0, wrong=0,
                                      order=[], entry_count=12) == []


def test_a_short_solve_order_is_not_treated_as_a_fingerprint():
    """Three entries collide by chance constantly; only a long order identifies anyone."""
    matched, _ = R.crossword_sequence_findings(1, ["1-across", "2-down", "3-across"])
    assert matched is None


# --- Module D: washing ------------------------------------------------------------
def test_wager_wash_needs_both_a_long_run_and_a_skewed_one(monkeypatch):
    pair = [("11", "22", 500)] * 9 + [("22", "11", 500)]      # 90% one-way over 10 games
    monkeypatch.setattr(R.DatabaseManager, "fetch_all",
                        staticmethod(lambda *a, **k: list(pair)))
    assert R.wager_wash_findings("11", "22") is not None

    # Same skew, too few games - a bad night is not a laundering network.
    short = [("11", "22", 500)] * 3
    monkeypatch.setattr(R.DatabaseManager, "fetch_all",
                        staticmethod(lambda *a, **k: list(short)))
    assert R.wager_wash_findings("11", "22") is None


def test_an_even_run_of_games_is_never_flagged(monkeypatch):
    even = [("11", "22", 100)] * 5 + [("22", "11", 100)] * 5
    monkeypatch.setattr(R.DatabaseManager, "fetch_all",
                        staticmethod(lambda *a, **k: list(even)))
    assert R.wager_wash_findings("11", "22") is None


def test_money_passed_straight_on_is_flagged_and_money_kept_is_not(monkeypatch):
    monkeypatch.setattr(R.DatabaseManager, "fetch_all",
                        staticmethod(lambda *a, **k: [(900,)]))
    assert R.recycle_findings("11", 1000, 1_000_000) is not None

    monkeypatch.setattr(R.DatabaseManager, "fetch_all",
                        staticmethod(lambda *a, **k: [(50,)]))
    assert R.recycle_findings("11", 1000, 1_000_000) is None


# --- Module E: funnelling ---------------------------------------------------------
def test_three_low_tenure_senders_trip_the_funnel(monkeypatch):
    rows = [("a", 400), ("b", 400), ("c", 400)]
    monkeypatch.setattr(R.DatabaseManager, "fetch_all", staticmethod(lambda *a, **k: rows))
    ages = {"a": 2, "b": 5, "c": 1}
    found = R.funnel_findings("main", member_ages=ages)
    assert found and "3" in found["distinct low-tenure senders"]


def test_established_senders_do_not_count_towards_the_funnel():
    """The rule is about a network of throwaways, not a popular person being paid."""
    rows = [("a", 400), ("b", 400), ("c", 400)]
    import lib.core.detection_rules as rules_mod
    original = rules_mod.DatabaseManager.fetch_all
    rules_mod.DatabaseManager.fetch_all = staticmethod(lambda *a, **k: rows)
    try:
        ages = {"a": 900, "b": 800, "c": 700}
        assert rules_mod.funnel_findings("main", member_ages=ages) is None
    finally:
        rules_mod.DatabaseManager.fetch_all = original


def test_a_sender_with_no_known_age_still_counts(monkeypatch):
    """A failed lookup must not be a way to vanish from the tally."""
    rows = [("a", 400), ("b", 400), ("c", 400)]
    monkeypatch.setattr(R.DatabaseManager, "fetch_all", staticmethod(lambda *a, **k: rows))
    assert R.funnel_findings("main", member_ages={}) is not None


# --- the review view --------------------------------------------------------------
def test_the_money_buttons_are_hidden_when_there_is_nothing_to_take():
    """A reviewer must never be offered an action that cannot do anything."""
    labels = {c.label for c in D.ReviewView("k", 1, amount=0).children}
    assert "Tax 50%" not in labels and "Confiscate 100%" not in labels
    assert "Allow / Dismiss" in labels and "Flag / Restrict" in labels

    with_money = {c.label for c in D.ReviewView("k", 1, amount=500).children}
    assert "Tax 50%" in with_money and "Confiscate 100%" in with_money


def test_flag_is_silent_when_detection_is_disabled(monkeypatch):
    monkeypatch.setattr(config, "DETECTION_ENABLED", False, raising=False)
    called = []
    monkeypatch.setattr(D, "_mark_alerted", lambda *a, **k: called.append(a))
    D.flag(object(), D.WORDLE_FAST_SOLVE, 1, {"x": 1})
    assert called == []


def test_flag_never_raises_even_when_everything_is_broken(monkeypatch):
    """It runs on the path that pays a player out. A broken detector must cost a solve
    nothing at all, so the failure has to stay inside this call."""
    monkeypatch.setattr(config, "DETECTION_ENABLED", True, raising=False)
    monkeypatch.setattr(D, "already_alerted",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db is down")))
    D.flag(object(), D.WORDLE_FAST_SOLVE, 1, {"x": 1})       # must not raise


def test_flag_without_a_registered_client_is_dropped_not_raised(monkeypatch):
    monkeypatch.setattr(config, "DETECTION_ENABLED", True, raising=False)
    monkeypatch.setattr(D, "_client", None)
    D.flag(None, D.WAGER_WASHING, 1, {"x": 1})               # must not raise


def test_a_restricted_account_reads_back_as_flagged(monkeypatch):
    """The Flag button has to do more than record an opinion - /pay reads this."""
    store = {}
    monkeypatch.setattr(D.DatabaseManager, "execute",
                        staticmethod(lambda q, p=(): store.update({p[0]: p[1]})
                                     if "INSERT INTO detection_flags" in q else None))
    monkeypatch.setattr(D.DatabaseManager, "fetch_one",
                        staticmethod(lambda q, p=(): (1,) if store.get(p[0]) == p[1] else None))
    assert not D.is_flagged("777")
    D.set_flag("777", "flagged_alt", by_id="1", note="test")
    assert D.is_flagged("777")


def test_the_funnel_rule_is_not_run_where_ages_cannot_be_resolved():
    """Regression guard. It used to run inside the transfer, which has no route to a guild
    member - so every sender counted as low-tenure and anyone paid by three people tripped
    it. It belongs at the /pay handler, which can resolve ages."""
    import pathlib
    db = pathlib.Path(__file__).resolve().parent.parent / "database.py"
    assert "funnel_findings" not in db.read_text()

    cmds = pathlib.Path(__file__).resolve().parent.parent / "lib/bot/setup_commands.py"
    body = cmds.read_text()
    assert "funnel_findings" in body and "member_ages=ages" in body
    assert "await _check_funnel()" in body      # defined AND called


# --- the review panel --------------------------------------------------------------
def test_the_flags_panel_lists_who_is_flagged_and_offers_them(monkeypatch):
    """The panel is the only route back from a Flag / Restrict, since the alert's own
    buttons disable themselves once one is pressed."""
    import discord
    store = {}
    monkeypatch.setattr(D.DatabaseManager, "execute",
                        staticmethod(lambda q, p=(): store.setdefault(p[0], p)))
    monkeypatch.setattr(D.DatabaseManager, "fetch_all",
                        staticmethod(lambda q, p=(): [(u, "flagged_alt", 1_700_000_000, "kind")
                                                     for u in store]))
    D.set_flag("111", "flagged_alt", by_id="1", note="kind")
    D.set_flag("222", "flagged_alt", by_id="1", note="kind")

    content, view = D.build_flags_panel(None, 1)
    assert "2 member(s)" in content
    picks = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert picks and {o.value for o in picks[0].options} == {"111", "222"}


def test_the_panel_is_empty_and_harmless_when_nobody_is_flagged(monkeypatch):
    monkeypatch.setattr(D.DatabaseManager, "fetch_all", staticmethod(lambda *a, **k: []))
    content, view = D.build_flags_panel(None, 1)
    assert "Nobody is flagged" in content
    assert not view.children, "an empty panel must not offer a dropdown with no options"
