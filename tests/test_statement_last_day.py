"""The statement's Last 24h period - a rolling day rather than a calendar month."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.economy import statement


def _patch(monkeypatch, rows, *, opening=100, live=200):
    """Point the statement at a fixed ledger, snapshot set and live balance."""
    captured = {}

    def _fetch_all(_sql, params=None, *a, **kw):
        captured["window"] = (params[1], params[2])     # user_id, start_ts, end_ts
        start, end = params[1], params[2]
        return [r for r in rows if start <= r[0] < end]

    monkeypatch.setattr(statement.DatabaseManager, "fetch_all", staticmethod(_fetch_all))
    monkeypatch.setattr(statement, "_live_balance_before", lambda _uid, _ts: opening)
    monkeypatch.setattr(statement, "_snapshots", lambda _uid: [])
    monkeypatch.setattr(statement, "get_bb", lambda _uid: live)
    return captured


def test_last_day_window_is_a_rolling_24_hours():
    start, end = statement._day_bounds()
    assert end - start == 86_400
    assert abs(end - int(time.time())) < 5          # ends now, not at midnight


def test_last_day_reads_only_the_last_24_hours(monkeypatch):
    now = int(time.time())
    rows = [
        (now - 200_000, 500, 600, "Chat activity reward", None),   # two days ago
        (now - 3_600, -40, 560, "Blackjack", None),                # an hour ago
    ]
    captured = _patch(monkeypatch, rows)

    view = statement.build_statement_view(
        target_id=1, target_name="Tester", viewer_id=1, offset=0, client=None, day=True)

    start, end = captured["window"]
    assert end - start == 86_400
    text = " ".join(i.content for i in _texts(view))
    assert "Last 24 hours" in text
    assert "Casino" in text                          # the recent play is in
    assert "Chat activity reward" not in text        # the older row is not


def test_last_day_stamps_times_and_months_stamp_dates(monkeypatch):
    now = int(time.time())
    rows = [(now - 3_600, -40, 560, "Blackjack", None)]
    _patch(monkeypatch, rows)

    day_view = statement.build_statement_view(
        target_id=1, target_name="Tester", viewer_id=1, offset=0, client=None, day=True)
    body = " ".join(i.content for i in _texts(day_view))
    stamp = body.split("`")[1]
    assert ":" in stamp and len(stamp) == 5           # HH:MM, not "05 Feb"


def test_last_day_opening_ignores_a_stale_snapshot(monkeypatch):
    """An end-of-day snapshot can be 24h stale on a rolling window, so the ledger's
    running balance has to win - otherwise a day of real moves lands in the residual."""
    now = int(time.time())
    _patch(monkeypatch, [(now - 3_600, -40, 560, "Blackjack", None)], opening=600, live=560)
    monkeypatch.setattr(statement, "_snapshots", lambda _uid: [(now - 90_000, 999)])

    view = statement.build_statement_view(
        target_id=1, target_name="Tester", viewer_id=1, offset=0, client=None, day=True)
    text = " ".join(i.content for i in _texts(view))
    assert "600" in text                              # ledger opening, not the 999 snapshot
    assert "Rewards & other" not in text              # nothing spurious in the residual


def test_nav_offers_last_24h_and_a_way_back():
    """Last 24h is off the calendar, so the month steppers have nothing to step through
    while it's showing - but This month always leads back."""
    on_day = statement._StatementNav(1, "Tester", 1, 0, True)
    assert on_day.last_day.disabled                   # already there
    assert on_day.previous.disabled and on_day.next_month.disabled
    assert not on_day.this_month.disabled             # the way back

    this_month = statement._StatementNav(1, "Tester", 1, 0, False)
    assert not this_month.last_day.disabled
    assert not this_month.previous.disabled
    assert this_month.this_month.disabled and this_month.next_month.disabled

    older = statement._StatementNav(1, "Tester", 1, 3, False)
    assert not older.last_day.disabled                # reachable from any month
    assert not older.this_month.disabled and not older.next_month.disabled

    oldest = statement._StatementNav(1, "Tester", 1, statement._MAX_OFFSET, False)
    assert oldest.previous.disabled                   # retention floor, unchanged


def _texts(view):
    """Every TextDisplay in the rendered card, in order."""
    import discord
    out = []
    for item in view.children:
        for child in getattr(item, "children", []):
            if isinstance(child, discord.ui.TextDisplay):
                out.append(child)
    return out
