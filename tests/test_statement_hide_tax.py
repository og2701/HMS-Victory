"""Statements fold tax charges into the residual instead of naming them."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from lib.economy import statement


_ROWS = [
    (1000, 50, 50, "Chat activity reward", None),
    (2000, -40, 10, "Inactivity tax (60+ days dormant)", None),
    (3000, -5, 5, "Wealth demurrage (5%/wk over 20,000)", None),
]


def _patch_rows(monkeypatch):
    monkeypatch.setattr(
        statement.DatabaseManager,
        "fetch_all",
        staticmethod(lambda *args, **kwargs: list(_ROWS)),
    )


def test_tax_rows_are_hidden_when_enabled(monkeypatch):
    _patch_rows(monkeypatch)
    monkeypatch.setattr(config, "STATEMENT_HIDE_TAX", True, raising=False)

    _rows, entries, total_in, total_out, breakdown = statement._gather("1", 0, 10_000, None)

    assert "Tax" not in breakdown
    descriptions = " ".join(desc.lower() for _, _, desc, _ in entries)
    assert "tax" not in descriptions and "demurrage" not in descriptions
    # Hidden charges stay out of the visible totals; the residual reconciles them.
    assert total_in == 50 and total_out == 0


def test_tax_rows_are_itemised_when_disabled(monkeypatch):
    _patch_rows(monkeypatch)
    monkeypatch.setattr(config, "STATEMENT_HIDE_TAX", False, raising=False)

    _rows, entries, total_in, total_out, breakdown = statement._gather("1", 0, 10_000, None)

    assert breakdown.get("Tax") == -45
    assert total_out == 45
