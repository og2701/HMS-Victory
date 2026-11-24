"""The daily Wordle word can't be worked out from the open repo once the server key is set."""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy import secret_config
from lib.features import wordle as W

TODAY = datetime.date(2026, 11, 24)


def _public_word(d):
    return W._ANSWERS[(d - W._EPOCH).days % len(W._ANSWERS)]


def test_without_the_key_the_wordle_keeps_the_public_order(monkeypatch):
    monkeypatch.delenv("BADGE_SECRET_KEY", raising=False)
    monkeypatch.setattr(W, "_KEYED", [])
    for k in range(5):
        d = TODAY + datetime.timedelta(days=k)
        assert W._todays_word(d) == _public_word(d)


def test_with_the_key_the_wordle_order_changes_from_the_25th(monkeypatch):
    monkeypatch.setenv("BADGE_SECRET_KEY", "test-key")
    monkeypatch.setattr(W, "_KEYED", [])
    assert W._todays_word(TODAY) == _public_word(TODAY)          # today's word stays put
    days = [TODAY + datetime.timedelta(days=k) for k in range(1, 31)]
    keyed = [W._todays_word(d) for d in days]
    assert keyed != [_public_word(d) for d in days]
    assert len(set(keyed)) == len(keyed)                         # still no repeats close together
    assert all(w in W._VALID for w in keyed)
    monkeypatch.setattr(W, "_KEYED", [])
    monkeypatch.setenv("BADGE_SECRET_KEY", "another-key")
    assert [W._todays_word(d) for d in days] != keyed            # a different key, a different order
