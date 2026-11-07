"""Pred dates are typed and shown the UK way: DD/MM/YYYY HH:MM, 24h, Europe/London,
and an ambiguous date is always read day first."""

import os
import sys
from datetime import datetime

import pytest
import pytz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.economy import prediction_system as ps  # noqa: E402

UK = pytz.timezone("Europe/London")


def _uk_ts(y, mo, d, h, mi):
    return UK.localize(datetime(y, mo, d, h, mi)).timestamp()


def test_ambiguous_date_is_day_first():
    got = ps._parse_uk_datetime("08/05/2030 19:30")
    assert (got.day, got.month, got.year, got.hour, got.minute) == (8, 5, 2030, 19, 30)


@pytest.mark.parametrize("text", [
    "14/11/2030 19:45", "14/11/30 19:45", "14-11-2030 19:45", "14.11.2030 19:45",
    "19:45 14/11/2030", "  14/11/2030   19:45 ", "2030-11-14 19:45",
])
def test_accepted_forms(text):
    assert ps._parse_uk_datetime(text).timestamp() == _uk_ts(2030, 11, 14, 19, 45)


@pytest.mark.parametrize("text", ["11/14/2030 19:45", "14/11/2030", "7pm Friday", "14/11/2030 7:45pm"])
def test_month_first_and_junk_are_refused(text):
    with pytest.raises(ValueError):
        ps._parse_uk_datetime(text)


def test_uses_uk_summer_time():
    # 1 July is BST, so 19:30 UK is 18:30 UTC.
    assert ps._parse_uk_datetime("01/07/2030 19:30").timestamp() == datetime(2030, 7, 1, 18, 30, tzinfo=pytz.utc).timestamp()


def test_format_round_trips_and_is_day_first():
    ts = _uk_ts(2030, 5, 8, 9, 5)
    assert ps._fmt_uk_datetime(ts) == "08/05/2030 09:05"
    assert ps._parse_uk_datetime(ps._fmt_uk_datetime(ts)).timestamp() == ts


def test_post_time_and_end_time_take_uk_dates():
    posts = ps._parse_post_time("08/05/2030 19:30")
    assert posts == int(_uk_ts(2030, 5, 8, 19, 30))
    assert ps._resolve_end_ts("08/05/2030 21:00", posts) == _uk_ts(2030, 5, 8, 21, 0)
    assert ps._resolve_end_ts("90", posts) == posts + 90 * 60


def test_bad_input_message_shows_the_uk_format():
    with pytest.raises(ValueError, match="DD/MM/YYYY HH:MM"):
        ps._parse_post_time("next tuesday")
    with pytest.raises(ValueError, match="DD/MM/YYYY HH:MM"):
        ps._resolve_end_ts("next tuesday", 0)


def test_scheduled_edit_modal_prefills_uk_format():
    ts = int(_uk_ts(2030, 5, 8, 19, 30))
    modal = ps.PredictionEditModal(1, "Match", ["A", "B"], ts, 90)
    assert modal.when_input.default == "08/05/2030 19:30"
    assert "DD/MM/YYYY" in modal.when_input.label


def test_scheduled_list_select_shows_uk_time():
    ts = int(_uk_ts(2030, 7, 1, 19, 30))
    view = ps.ScheduledPredSelectView([(1, 555, "Match", ts, 90, 42)], client=None)
    assert view.select.options[0].description == "Posts: 01/07/2030 19:30 UK"
