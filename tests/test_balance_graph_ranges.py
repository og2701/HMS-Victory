"""The balance graph's range windows, including the 24H one."""

import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.economy import balance_graph as bg


def _patch(monkeypatch, points, live):
    monkeypatch.setattr(bg, "_snapshot_points", lambda _uid: [])
    monkeypatch.setattr(bg, "_history_points", lambda _uid: list(points))
    monkeypatch.setattr(bg, "get_bb", lambda _uid: live)


def test_24h_is_offered_and_maps_to_one_day():
    labels = [lab for lab, _ in bg.BalanceGraphRangeView.RANGES]
    assert labels == ["24H", "7D", "30D", "90D", "All"]
    assert dict(bg.BalanceGraphRangeView.RANGES)["24H"] == 1


def test_24h_window_keeps_only_the_last_day(monkeypatch):
    now = int(time.time())
    _patch(monkeypatch, [(now - 400_000, 5_000), (now - 200_000, 6_000),
                         (now - 3_600, 7_000)], live=7_000)

    pts = bg._load_points("1", days=1)

    assert all(ts >= now - 86_400 - 1 for ts, _ in pts)
    # The window's left edge is anchored at the balance held going in, so the line
    # starts at the right level rather than jumping from zero.
    assert pts[0][1] == 6_000


def test_quiet_24h_still_renders_a_flat_line(monkeypatch):
    """Nothing happened today - that's an answer, not an error. The anchor plus the
    live tip give the two points the renderer needs."""
    now = int(time.time())
    _patch(monkeypatch, [(now - 400_000, 5_000)], live=5_000)

    pts = bg._load_points("1", days=1)

    assert len(pts) >= 2
    assert {b for _, b in pts} == {5_000}


def test_intraday_axis_uses_times_and_wider_windows_use_dates():
    now = int(time.time())
    day = bg._build_html("og", [(now - 80_000, 100), (now, 120)], days=1)
    wide = bg._build_html("og", [(now - 30 * 86_400, 100), (now, 120)], days=30)

    assert re.search(r"class='xlab'[^>]*>\d{2}:\d{2}<", day)
    assert not re.search(r"class='xlab'[^>]*>\d{2}:\d{2}<", wide)
    # A bare clock time needs a day against it to mean anything.
    assert re.search(r"class='span'>\d+ \w{3} \d{2}:\d{2} to \d{2}:\d{2}<", day)
    assert re.search(r"class='span'>\d+ \w{3} to \d+ \w{3}<", wide)
    # ...and the bars below go by the hour on a 24H card, by the day on a 30D one.
    assert "EACH HOUR" in day and "EACH DAY" in wide


def test_each_period_sums_to_the_whole_change():
    now = int(time.time())
    pts = [(now - 5 * 86_400, 1_000), (now - 3 * 86_400, 1_500), (now - 3 * 86_400 + 60, 900),
           (now - 86_400, 2_400), (now, 2_000)]
    unit, changes = bg._period_changes(pts)
    assert unit == "day"
    assert sum(c for _, c in changes) == 2_000 - 1_000
    assert any(c < 0 for _, c in changes) and any(c > 0 for _, c in changes)


def test_long_windows_bucket_by_week():
    now = int(time.time())
    unit, changes = bg._period_changes([(now - 200 * 86_400, 0), (now, 500)])
    assert unit == "week"
    assert 28 <= len(changes) <= 31


def test_card_shows_rank_flows_and_escapes_the_name():
    now = int(time.time())
    page = bg._build_html("<b>og</b>", [(now - 30 * 86_400, 700), (now, 9_120)], days=30, rank=20,
                          holders=3_772, came_in={"Casino": 31_812, "Pay": 14_085},
                          went_out={"Casino": -37_600, "Shop": -3_000})
    assert "&lt;b&gt;og&lt;/b&gt;" in page and "<b>og</b>" not in page
    assert "#20 RICHEST OF 3,772" in page
    assert "+8,420 · 30 days" in page
    assert "CAME IN +45,897" in page
    assert "WENT OUT \u221240,600" in page


def test_a_quiet_window_says_so():
    now = int(time.time())
    page = bg._build_html("og", [(now - 86_400, 500), (now, 500)], days=1)
    assert "No change · 24 hours" in page
    assert "Nothing came in" in page and "Nothing went out" in page
