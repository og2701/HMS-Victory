"""Booster bonus and new-bond rates follow the reserve throttle (Oggers, 24 Nov 2026)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import pytest

from lib.economy import bonds as B
from lib.economy import reserve_policy as R


@pytest.mark.parametrize("mult,expected", [(1.0, 100), (0.75, 50), (0.5, 50), (0.25, 25)])
def test_booster_bonus_halves_only_while_throttled(monkeypatch, mult, expected):
    monkeypatch.setattr(R, "throttle_multiplier", lambda *a, **k: mult)
    assert R.booster_bonus(100) == expected


@pytest.mark.parametrize("mult", [1.0, 0.75, 0.5, 0.25])
def test_new_bond_rates_scale_with_the_throttle(monkeypatch, mult):
    monkeypatch.setattr(R, "throttle_multiplier", lambda *a, **k: mult)
    base = B._terms_bp()
    live = B._live_terms_bp()
    for d, (lo, hi) in base.items():
        assert live[d] == (int(lo * mult), int(hi * mult))
    assert (B._reduced_note() == "") == (mult >= 1.0)


def test_bond_rates_can_be_pinned_to_normal(monkeypatch):
    import config
    monkeypatch.setattr(R, "throttle_multiplier", lambda *a, **k: 0.5)
    monkeypatch.setattr(config, "BOND_RATES_FOLLOW_THROTTLE", False, raising=False)
    assert B._live_terms_bp() == dict(B._terms_bp())
