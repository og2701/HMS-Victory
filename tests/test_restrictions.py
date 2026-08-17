"""Graded economy restrictions: what each tier blocks, and how it resolves."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.core import restrictions as R


def _rows(monkeypatch, flags):
    monkeypatch.setattr(R.DatabaseManager, "fetch_all",
                        staticmethod(lambda *a, **k: [(f,) for f in flags]))


def test_tiers_are_strictly_nested(monkeypatch):
    """A higher tier must block everything the one below it does, or a 'promotion'
    could silently unblock something."""
    order = sorted(R.TIERS, key=lambda k: R.TIERS[k]["rank"])
    for lower, higher in zip(order, order[1:]):
        assert R.blocks(lower) < R.blocks(higher), f"{higher} must be stricter than {lower}"


def test_pay_only_blocks_pay_and_nothing_else(monkeypatch):
    _rows(monkeypatch, ["pay_only"])
    assert R.is_blocked(1, "pay") == "pay_only"
    for allowed in ("blackjack", "roulette", "shop", "wordle", "skyrim"):
        assert R.is_blocked(1, allowed) is None


def test_economy_blocks_the_casino_and_dailies_but_not_skyrim(monkeypatch):
    _rows(monkeypatch, ["economy"])
    for blocked in ("pay", "blackjack", "roulette", "wager", "shop", "lottery", "wordle"):
        assert R.is_blocked(1, blocked) == "economy", blocked
    assert R.is_blocked(1, "skyrim") is None


def test_full_also_blocks_skyrim(monkeypatch):
    _rows(monkeypatch, ["full"])
    assert R.is_blocked(1, "skyrim") == "full"
    assert R.is_blocked(1, "pay") == "full"


def test_legacy_flag_keeps_behaving_as_it_did(monkeypatch):
    """Rows written before tiers existed blocked /pay only; a deploy must not change
    what someone is already serving."""
    _rows(monkeypatch, ["flagged_alt"])
    assert R.tier_of(1) == "pay_only"
    assert R.is_blocked(1, "pay") == "pay_only"
    assert R.is_blocked(1, "blackjack") is None


def test_the_strictest_tier_wins_when_several_are_held(monkeypatch):
    _rows(monkeypatch, ["pay_only", "full", "economy"])
    assert R.tier_of(1) == "full"


def test_an_unrestricted_member_is_never_blocked(monkeypatch):
    _rows(monkeypatch, [])
    assert R.tier_of(1) is None
    assert R.is_blocked(1, "pay") is None


def test_a_database_failure_fails_open(monkeypatch):
    """The gate runs on every command. A broken lookup must let people play, not
    lock the whole server out of the economy."""
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(R.DatabaseManager, "fetch_all", staticmethod(boom))
    assert R.tier_of(1) is None
    assert R.is_blocked(1, "pay") is None


def test_every_tier_can_describe_itself():
    """The panel and the refusal message both render these, so none may be blank."""
    for key, meta in R.TIERS.items():
        assert meta["label"] and meta["summary"], key
        assert R.summary(key) in R.refusal_message(key)
        assert "under review" in R.refusal_message(key)


def test_every_blocked_name_is_a_real_command():
    """A tier that names a command which does not exist is a gate that silently does
    nothing. Checked against the decorator list rather than a copy of it."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "lib" / "bot" / "setup_commands.py"
    registered = set(re.findall(r'@command\("([a-z0-9-]+)"', src.read_text()))
    assert registered, "could not read the command list - has the decorator changed?"
    for tier in R.TIERS:
        unknown = R.blocks(tier) - registered
        assert not unknown, f"{tier} blocks commands that do not exist: {sorted(unknown)}"


def test_the_money_commands_are_all_covered_by_the_economy_tier():
    """Anything that pays out or takes a stake must be caught, or a restricted member
    just plays a different game with the same money."""
    must_block = {"pay", "benefits", "blockade", "chest", "wager", "shop", "lottery", "bond"}
    assert must_block <= R.blocks("economy")
