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
    day = bg._build_html("og", [(now - 80_000, 100), (now, 120)])
    wide = bg._build_html("og", [(now - 30 * 86_400, 100), (now, 120)])

    assert re.search(r"class='xlab'>\d{2}:\d{2}<", day)
    assert not re.search(r"class='xlab'>\d{2}:\d{2}<", wide)
    # A bare clock time needs a day against it to mean anything.
    assert re.search(r"Balance over time · \d+ \w{3} \d{2}:\d{2} to \d{2}:\d{2}", day)
    assert re.search(r"Balance over time · \d+ \w{3} to \d+ \w{3}", wide)
