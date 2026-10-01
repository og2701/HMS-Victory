"""Extra figures for the daily/weekly/monthly summary cards.

The per-day counters in ``daily_summaries`` carry the headline numbers. Everything else
on the card (hourly activity, media, first posts, casino, economy, Big Brother) is read
here from the tables other features already maintain. Every lookup is best-effort: a
missing table or a purged range yields an empty result and the card drops that widget.

All day boundaries are UK local days, matching how ``daily_summaries`` is keyed.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
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


def casino(start: date, end: date) -> dict | None:
    lo, hi = day_bounds(start, end)
    row = _safe_fetch_one(
        "SELECT COUNT(*), COUNT(DISTINCT user_id), COALESCE(SUM(net), 0) "
        "FROM casino_results WHERE timestamp >= ? AND timestamp < ?", (lo, hi))
    if not row or not row[0]:
        return None
    fave = _safe_fetch_one(
        "SELECT game, COUNT(*) FROM casino_results WHERE timestamp >= ? AND timestamp < ? "
        "GROUP BY game ORDER BY COUNT(*) DESC LIMIT 1", (lo, hi))
    try:
        from lib.economy.casino_stats import GAME_LABELS
    except Exception:
        GAME_LABELS = {}
    return {
        "games": int(row[0]),
        "players": int(row[1]),
        "house_net": -int(row[2]),  # players' net is the house's loss
        "fave": (GAME_LABELS.get(fave[0], str(fave[0]).title()), int(fave[1])) if fave else None,
    }


def economy(daily: bool) -> dict | None:
    """Latest UKPence circulation, with the change since the previous snapshot on daily cards.

    Snapshots are kept for 48h, so a week/month delta isn't available.
    """
    rows = _safe_fetch_all(
        "SELECT total_circulation FROM circulation_snapshots ORDER BY timestamp DESC LIMIT 2")
    if not rows:
        return None
    out = {"circulation": int(rows[0][0]), "change": None, "lottery_pot": None}
    if daily and len(rows) > 1:
        out["change"] = int(rows[0][0]) - int(rows[1][0])
    try:
        from lib.economy import lottery
        rnd = lottery.get_open_round()
        if rnd:
            out["lottery_pot"] = lottery.tickets_sold(rnd["id"]) * int(rnd["ticket_price"])
    except Exception:
        log.debug("lottery pot lookup failed", exc_info=True)
    return out


def big_brother(start: date, end: date) -> dict | None:
    lo, hi = day_bounds(start, end)
    rows = _safe_fetch_all(
        "SELECT kind, COUNT(*) FROM bb_events WHERE at >= ? AND at < ? GROUP BY kind", (lo, hi))
    counts = Counter({k: int(n) for k, n in rows})
    if not counts:
        return None
    return {
        "evicted": counts.get("evicted", 0),
        "votes": counts.get("vote_cast", 0),
        "nominations": counts.get("nominated", 0),
        "shop": counts.get("shop_purchase", 0),
    }
