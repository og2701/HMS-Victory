"""Extra figures for the daily/weekly/monthly summary cards.

The per-day counters in ``daily_summaries`` carry the headline numbers. The rest of the
card (hourly activity, media, first posts) is read here from tables other features already
maintain. Every lookup is best-effort: a missing table or a purged range yields zeros.

All day boundaries are UK local days, matching how ``daily_summaries`` is keyed.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

import pytz

from database import DatabaseManager

log = logging.getLogger(__name__)

UK = pytz.timezone("Europe/London")


def day_bounds(start: date, end: date) -> tuple[int, int]:
    """Epoch seconds for 00:00 UK on ``start`` up to 00:00 UK the day after ``end``."""
    lo = UK.localize(datetime(start.year, start.month, start.day))
    nxt = end + timedelta(days=1)
    hi = UK.localize(datetime(nxt.year, nxt.month, nxt.day))
    return int(lo.timestamp()), int(hi.timestamp())


def bucket_by_day_hour(timestamps, start: date, end: date) -> dict[date, list[int]]:
    """Count epoch timestamps into {UK date: [24 hourly counts]} for every day in range."""
    days = {}
    d = start
    while d <= end:
        days[d] = [0] * 24
        d += timedelta(days=1)
    for ts in timestamps:
        local = datetime.fromtimestamp(int(ts), UK)
        row = days.get(local.date())
        if row is not None:
            row[local.hour] += 1
    return days


def _safe_fetch_all(sql, params=()):
    try:
        return DatabaseManager.fetch_all(sql, params) or []
    except Exception:
        log.debug("summary stats query failed: %s", sql, exc_info=True)
        return []


def _safe_fetch_one(sql, params=()):
    try:
        return DatabaseManager.fetch_one(sql, params)
    except Exception:
        log.debug("summary stats query failed: %s", sql, exc_info=True)
        return None


def message_activity(start: date, end: date) -> dict[date, list[int]]:
    """Hourly message counts per day, from the rolling message archive."""
    lo, hi = day_bounds(start, end)
    rows = _safe_fetch_all("SELECT ts FROM message_archive WHERE ts >= ? AND ts < ?", (lo, hi))
    return bucket_by_day_hour((ts for (ts,) in rows), start, end)


def media_count(start: date, end: date) -> int:
    """Messages carrying an attachment. The archive keeps ~30 days, so the first day of a
    31-day month can come back short."""
    lo, hi = day_bounds(start, end)
    row = _safe_fetch_one(
        "SELECT COUNT(*) FROM message_archive WHERE ts >= ? AND ts < ? "
        "AND attachments IS NOT NULL AND attachments NOT IN ('', '[]')", (lo, hi))
    return int(row[0]) if row else 0


def new_voices(start: date, end: date) -> int:
    """Members whose first message on record landed in the range (tracking began Aug 2026)."""
    lo, hi = day_bounds(start, end)
    row = _safe_fetch_one(
        "SELECT COUNT(*) FROM member_profile WHERE first_seen >= ? AND first_seen < ?", (lo, hi))
    return int(row[0]) if row else 0


def daily_series(start: date, end: date) -> dict[date, dict]:
    """{date: {"messages": n, "members": n}} for each stored day in range."""
    rows = _safe_fetch_all(
        "SELECT date, data FROM daily_summaries WHERE date BETWEEN ? AND ? ORDER BY date ASC",
        (start.isoformat(), end.isoformat()))
    out = {}
    for day_str, raw in rows:
        try:
            data = json.loads(raw)
            out[date.fromisoformat(day_str)] = {
                "messages": int(data.get("total_messages", 0)),
                "members": int(data.get("total_members", 0)),
            }
        except (ValueError, TypeError):
            continue
    return out
