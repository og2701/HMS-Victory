import asyncio
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytz

from lib.features import summary_stats as stats
from lib.features.summary_html import (
    build_summary_html, calendar_weeks, cal_level, heat_level, hour_label,
)
from lib.features.summary import _period_bounds, _subtitle, _build_card

UK = pytz.timezone("Europe/London")


def _card(**overrides):
    card = {
        "frequency": "daily", "subtitle": "Wednesday 30 September 2026",
        "messages": 4901, "messages_prev": 4401, "message_series": [5178, 8491, 4901],
        "message_caption": "Last 7 days", "members": 13229, "members_change": 18,
        "member_series": [13147, 13211, 13229], "member_caption": "+83 this week",
        "activity": {"kind": "hours", "hours": [0] * 21 + [502, 10, 0]},
        "joined": 60, "left": 42, "banned": 9, "reactions": 381, "reactions_removed": 15,
        "boosts_gained": 0, "boosts_lost": 0, "chatting": 127, "new_voices": 22, "media": 189,
        "deleted": 15, "channels": [("#general", 2915), ("#politics", 513)],
        "chatters": [{"name": "Gazza", "count": 485, "avatar": None, "colour": "#3B5BDB"}],
        "reactors": [{"name": "<b>Mrs Doyle</b> {{body}}", "count": 75, "avatar": None, "colour": "#BE185D"}],
    }
    card.update(overrides)
    return card


class PeriodTests(unittest.TestCase):
    def test_weekly_covers_the_monday_to_sunday_before_the_post(self):
        start, end, prev_start, prev_end = _period_bounds("weekly", date(2026, 10, 5))
        self.assertEqual((start, end), (date(2026, 9, 28), date(2026, 10, 4)))
        self.assertEqual((prev_start, prev_end), (date(2026, 9, 21), date(2026, 9, 27)))

    def test_monthly_covers_the_previous_calendar_month(self):
        start, end, prev_start, prev_end = _period_bounds("monthly", date(2026, 10, 1))
        self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual((prev_start, prev_end), (date(2026, 8, 1), date(2026, 8, 31)))

    def test_week_subtitle_spans_months(self):
        self.assertEqual(_subtitle("weekly", date(2026, 9, 28), date(2026, 10, 4)), "28 Sep - 4 Oct 2026")
        self.assertEqual(_subtitle("weekly", date(2026, 9, 21), date(2026, 9, 27)), "21 - 27 September 2026")
        self.assertEqual(_subtitle("monthly", date(2026, 9, 1), date(2026, 9, 30)), "September 2026")


class BucketTests(unittest.TestCase):
    def test_day_bounds_follow_uk_time(self):
        lo, hi = stats.day_bounds(date(2026, 9, 30), date(2026, 9, 30))
        self.assertEqual(datetime.fromtimestamp(lo, UK).strftime("%Y-%m-%d %H:%M"), "2026-09-30 00:00")
        self.assertEqual(hi - lo, 86400)
        # The October clock change makes that Sunday 25 hours long.
        lo, hi = stats.day_bounds(date(2026, 10, 25), date(2026, 10, 25))
        self.assertEqual(hi - lo, 25 * 3600)

    def test_messages_bucket_into_uk_local_hours(self):
        late_utc = int(datetime(2026, 9, 30, 23, 30, tzinfo=pytz.utc).timestamp())  # 00:30 BST on 1 Oct
        nine_pm = int(UK.localize(datetime(2026, 9, 30, 21, 5)).timestamp())
        days = stats.bucket_by_day_hour([late_utc, nine_pm, nine_pm], date(2026, 9, 30), date(2026, 10, 1))
        self.assertEqual(days[date(2026, 9, 30)][21], 2)
        self.assertEqual(days[date(2026, 10, 1)][0], 1)
        self.assertEqual(sum(days[date(2026, 9, 30)]), 2)

    def test_missing_tables_degrade_to_nothing(self):
        with patch.object(stats.DatabaseManager, "fetch_one", side_effect=Exception("no table")), \
             patch.object(stats.DatabaseManager, "fetch_all", side_effect=Exception("no table")):
            self.assertEqual(stats.media_count(date(2026, 9, 30), date(2026, 9, 30)), 0)
            self.assertEqual(stats.new_voices(date(2026, 9, 30), date(2026, 9, 30)), 0)


class LayoutTests(unittest.TestCase):
    def test_calendar_starts_on_monday(self):
        days = [(date(2026, 9, 1) + timedelta(days=i), i) for i in range(30)]
        weeks = calendar_weeks(days)
        self.assertEqual(weeks[0][0], None)                # 1 Sep 2026 is a Tuesday
        self.assertEqual(weeks[0][1][0], date(2026, 9, 1))
        self.assertTrue(all(len(w) == 7 for w in weeks))
        self.assertEqual(weeks[-1][2][0], date(2026, 9, 30))
        self.assertEqual(weeks[-1][3:], [None] * 4)

    def test_levels(self):
        self.assertEqual(heat_level(0, 100), 0)
        self.assertEqual(heat_level(5, 100), 1)
        self.assertEqual(heat_level(100, 100), 5)
        self.assertEqual(cal_level(None, 100), 0)
        self.assertEqual(cal_level(100, 100), 4)

    def test_number_formats(self):
        self.assertEqual(hour_label(0), "12am")
        self.assertEqual(hour_label(21), "9pm")


class HtmlTests(unittest.TestCase):
    def test_daily_card(self):
        page = build_summary_html(_card())
        self.assertIn("Daily recap", page)
        self.assertIn("4,901", page)
        self.assertIn("+11%", page)
        self.assertIn("Peak 9pm", page)
        self.assertIn('class="bars"', page)
        # Member names are escaped and never treated as template tokens.
        escaped_name = "&lt;b&gt;Mrs Doyle&lt;/b&gt; {{body}}"
        self.assertIn(escaped_name, page)
        self.assertNotIn("{{", page.replace(escaped_name, ""))  # no unfilled tokens
        self.assertNotIn("<b>Mrs Doyle</b>", page)

    def test_weekly_card_has_heatmap_and_runners_up(self):
        days = [(date(2026, 9, 28) + timedelta(days=i), [i] * 24) for i in range(7)]
        chatters = [{"name": f"user{i}", "count": 100 - i, "avatar": None, "colour": "#000000"} for i in range(10)]
        page = build_summary_html(_card(
            frequency="weekly", activity={"kind": "week", "days": days}, chatters=chatters))
        self.assertIn("Weekly recap", page)
        self.assertEqual(page.count('class="heat-row"'), 7)
        self.assertIn("10&nbsp;&nbsp;user9", page)
        self.assertNotIn("AROUND THE SERVER", page)

    def test_monthly_card_has_calendar(self):
        days = [(date(2026, 9, 1) + timedelta(days=i), 1000 + i * 10) for i in range(30)]
        page = build_summary_html(_card(frequency="monthly", activity={"kind": "month", "days": days}))
        self.assertIn("Monthly recap", page)
        self.assertIn("Best: Wed 30 Sep", page)
        self.assertIn("Quietest: Tue 1 Sep", page)
        self.assertIn("#F0B232", page)


class BuildCardTests(unittest.TestCase):
    def test_live_member_count_ends_the_series(self):
        history = {
            date(2026, 9, 23): {"messages": 10049, "members": 13146},
            date(2026, 9, 29): {"messages": 4401, "members": 13211},
            date(2026, 9, 30): {"messages": 4901, "members": 0},
        }
        guild = SimpleNamespace(get_member=lambda uid: None, get_channel=lambda cid: SimpleNamespace(name="general"))
        data = {"total_messages": 4901, "members_joined": 60, "members_left": 42, "members_banned": 9,
                "reactions_added": 381, "reactions_removed": 15, "deleted_messages": 15,
                "boosters_gained": 0, "boosters_lost": 0, "messages": {"1": 2915},
                "active_members": {"7": 485, "8": 360}, "reacting_members": {"7": 75}}
        with patch.object(stats, "daily_series", return_value=history), \
             patch.object(stats, "message_activity", return_value={date(2026, 9, 30): [1] * 24}), \
             patch.object(stats, "media_count", return_value=189), \
             patch.object(stats, "new_voices", return_value=22):
            card = asyncio.run(_build_card(
                None, guild, "daily", data, {"total_messages": 4401}, 13229, 18,
                date(2026, 9, 30), date(2026, 9, 30)))
        self.assertEqual(card["member_series"][-1], 13229)
        self.assertEqual(card["member_caption"], "+83 this week")
        self.assertEqual(card["message_series"][-2:], [4401, 4901])
        self.assertEqual(card["chatting"], 2)
        self.assertEqual(card["channels"], [("#general", 2915)])
        self.assertEqual(card["chatters"][0]["name"], "Unknown Member")
        self.assertEqual(card["activity"]["kind"], "hours")

    def test_weekly_series_is_daily_totals_not_the_week_total(self):
        days = [date(2026, 9, 21) + timedelta(days=i) for i in range(7)]
        history = {d: {"messages": 1000 + i, "members": 13000 + i} for i, d in enumerate(days)}
        guild = SimpleNamespace(get_member=lambda uid: None, get_channel=lambda cid: None)
        data = {"total_messages": 7021, "messages": {}, "active_members": {}, "reacting_members": {}}
        with patch.object(stats, "daily_series", return_value=history), \
             patch.object(stats, "message_activity", return_value={d: [0] * 24 for d in days}), \
             patch.object(stats, "media_count", return_value=0), \
             patch.object(stats, "new_voices", return_value=0):
            card = asyncio.run(_build_card(
                None, guild, "weekly", data, None, 13010, 10, days[0], days[-1]))
        self.assertEqual(card["message_series"], [1000, 1001, 1002, 1003, 1004, 1005, 1006])
        self.assertEqual(card["member_series"][-1], 13010)
        self.assertEqual(card["activity"]["kind"], "week")


if __name__ == "__main__":
    unittest.main()
