"""Read-side of the chronicle: the aggregates a year-in-review is built from.

Pure SQL over ``chronicle.db`` with no discord.py in sight, so it can be run against a
copied file offline and unit-tested without a gateway. Ids come back as ints; turning
them into names is the caller's job.

Times are UTC epoch seconds in the table; the year and hour-of-day breakdowns are
computed in Europe/London, because "what hour do you post at" should match the clock
people were actually looking at.
"""

import calendar
from datetime import datetime

import pytz

from lib.chronicle.db import ChronicleDB

UK = pytz.timezone("Europe/London")

# Pins, forwards and thread starters carry a message reference exactly like a reply, so
# a reply is decided by the message type. Rows written before msg_type existed (NULL
# until the reactions walk fills them in) fall back to trusting the reference.
_IS_REPLY = "(msg_type = 'reply' OR msg_type IS NULL)"

# Day and hour come from the local_day / local_hour columns the recorder fills in UK
# time, rather than SQLite's strftime - which has no timezone database and would answer
# in whatever TZ the box runs in.


def year_bounds(year):
    """(start, end) epoch seconds for a UK calendar year."""
    start = UK.localize(datetime(year, 1, 1)).timestamp()
    end = UK.localize(datetime(year + 1, 1, 1)).timestamp()
    return int(start), int(end)


def _rows(sql, params=()):
    return ChronicleDB.fetch_all(sql, params) or []


def _one(sql, params=(), default=0):
    row = ChronicleDB.fetch_one(sql, params)
    return (row[0] if row and row[0] is not None else default)


# ----------------------------------------------------------------------- server-wide

def server_wrapped(year, top_n=10, include_bots=False):
    """Everything a server-level year-in-review needs, in one dict."""
    lo, hi = year_bounds(year)
    bots = "" if include_bots else " AND is_bot = 0"
    p = (lo, hi)

    totals = ChronicleDB.fetch_one(
        f"SELECT COUNT(*), COALESCE(SUM(char_count),0), COALESCE(SUM(word_count),0), "
        f"COALESCE(SUM(n_attachments),0), COUNT(DISTINCT user_id), "
        f"COALESCE(SUM(deleted_ts IS NOT NULL),0), COALESCE(SUM(edited_ts IS NOT NULL),0) "
        f"FROM messages WHERE ts >= ? AND ts < ?{bots}", p) or (0,) * 7

    return {
        "year": year,
        "messages": totals[0],
        "characters": totals[1],
        "words": totals[2],
        "attachments": totals[3],
        "posters": totals[4],
        "deleted": totals[5],
        "edited": totals[6],
        "reactions": _one("SELECT COUNT(*) FROM reactions WHERE ts >= ? AND ts < ? AND action = 'add'", p),
        "members_joined": _one("SELECT COUNT(*) FROM member_events WHERE ts >= ? AND ts < ? AND action = 'join'", p),
        "members_left": _one("SELECT COUNT(*) FROM member_events WHERE ts >= ? AND ts < ? AND action = 'leave'", p),
        "commands_used": _one("SELECT COUNT(*) FROM interactions WHERE ts >= ? AND ts < ? AND kind = 'command'", p),
        "top_posters": _rows(
            f"SELECT user_id, COUNT(*) n FROM messages WHERE ts >= ? AND ts < ?{bots} "
            f"GROUP BY user_id ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "top_channels": _rows(
            f"SELECT channel_id, COUNT(*) n FROM messages WHERE ts >= ? AND ts < ?{bots} "
            f"GROUP BY channel_id ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "top_emoji": _rows(
            "SELECT emoji, emoji_id, SUM(n) total FROM emoji_uses WHERE ts >= ? AND ts < ? "
            "GROUP BY emoji, emoji_id ORDER BY total DESC LIMIT ?", (*p, top_n)),
        "top_reactions": _rows(
            "SELECT emoji, emoji_id, COUNT(*) n FROM reactions WHERE ts >= ? AND ts < ? "
            "AND action = 'add' GROUP BY emoji, emoji_id ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "top_commands": _rows(
            "SELECT name, COUNT(*) n FROM interactions WHERE ts >= ? AND ts < ? AND kind = 'command' "
            "GROUP BY name ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "busiest_days": _rows(
            f"SELECT local_day, COUNT(*) n FROM messages WHERE ts >= ? AND ts < ?{bots} "
            f"GROUP BY local_day ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "by_hour": _rows(
            f"SELECT local_hour, COUNT(*) n FROM messages "
            f"WHERE ts >= ? AND ts < ?{bots} GROUP BY local_hour ORDER BY local_hour", p),
        "by_month": _rows(
            f"SELECT CAST(substr(local_day, 6, 2) AS INTEGER) m, COUNT(*) n FROM messages "
            f"WHERE ts >= ? AND ts < ?{bots} GROUP BY m ORDER BY m", p),
        "voice_hours": round(voice_seconds(lo, hi) / 3600, 1),
    }


# ------------------------------------------------------------------------- per-user

def user_wrapped(user_id, year, top_n=5):
    """One member's year: volume, habits, and who they actually talk to."""
    lo, hi = year_bounds(year)
    p = (user_id, lo, hi)

    totals = ChronicleDB.fetch_one(
        "SELECT COUNT(*), COALESCE(SUM(char_count),0), COALESCE(SUM(word_count),0), "
        "COALESCE(SUM(n_attachments),0), MIN(ts), MAX(ts) "
        "FROM messages WHERE user_id = ? AND ts >= ? AND ts < ?", p) or (0,) * 6

    rank_row = ChronicleDB.fetch_one(
        "SELECT COUNT(*) + 1 FROM (SELECT user_id, COUNT(*) n FROM messages "
        "WHERE ts >= ? AND ts < ? AND is_bot = 0 GROUP BY user_id) "
        "WHERE n > (SELECT COUNT(*) FROM messages WHERE user_id = ? AND ts >= ? AND ts < ?)",
        (lo, hi, user_id, lo, hi))

    return {
        "user_id": user_id,
        "year": year,
        "messages": totals[0],
        "characters": totals[1],
        "words": totals[2],
        "attachments": totals[3],
        "first_message_ts": totals[4],
        "last_message_ts": totals[5],
        "rank": rank_row[0] if rank_row else None,
        "active_days": _one(
            "SELECT COUNT(DISTINCT local_day) FROM messages "
            "WHERE user_id = ? AND ts >= ? AND ts < ?", p),
        "top_channels": _rows(
            "SELECT channel_id, COUNT(*) n FROM messages WHERE user_id = ? AND ts >= ? AND ts < ? "
            "GROUP BY channel_id ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "by_hour": _rows(
            "SELECT local_hour, COUNT(*) n FROM messages "
            "WHERE user_id = ? AND ts >= ? AND ts < ? GROUP BY local_hour ORDER BY local_hour", p),
        "top_emoji": _rows(
            "SELECT emoji, emoji_id, SUM(n) total FROM emoji_uses WHERE user_id = ? "
            "AND ts >= ? AND ts < ? GROUP BY emoji, emoji_id ORDER BY total DESC LIMIT ?", (*p, top_n)),
        "reactions_given": _one(
            "SELECT COUNT(*) FROM reactions WHERE user_id = ? AND ts >= ? AND ts < ? AND action = 'add'", p),
        "reactions_received": _one(
            "SELECT COUNT(*) FROM reactions WHERE author_id = ? AND ts >= ? AND ts < ? AND action = 'add'", p),
        "mentions_sent": _one(
            "SELECT COUNT(*) FROM mentions WHERE user_id = ? AND ts >= ? AND ts < ? AND kind = 'user'", p),
        "mentions_received": _one(
            "SELECT COUNT(*) FROM mentions WHERE target_id = ? AND ts >= ? AND ts < ? AND kind = 'user'", p),
        "replies_to": _rows(
            "SELECT reply_to_user, COUNT(*) n FROM messages WHERE user_id = ? AND ts >= ? AND ts < ? "
            f"AND reply_to_user IS NOT NULL AND reply_to_user != user_id AND {_IS_REPLY} "
            "GROUP BY reply_to_user ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "replied_by": _rows(
            "SELECT user_id, COUNT(*) n FROM messages WHERE reply_to_user = ? AND ts >= ? AND ts < ? "
            f"AND user_id != reply_to_user AND {_IS_REPLY} GROUP BY user_id ORDER BY n DESC LIMIT ?",
            (*p, top_n)),
        "most_reacted_message": ChronicleDB.fetch_one(
            "SELECT m.message_id, m.channel_id, m.content, COUNT(r.id) n FROM messages m "
            "JOIN reactions r ON r.message_id = m.message_id AND r.action = 'add' "
            "WHERE m.user_id = ? AND m.ts >= ? AND m.ts < ? "
            "GROUP BY m.message_id ORDER BY n DESC LIMIT 1", p),
        "commands_used": _rows(
            "SELECT name, COUNT(*) n FROM interactions WHERE user_id = ? AND ts >= ? AND ts < ? "
            "AND kind = 'command' GROUP BY name ORDER BY n DESC LIMIT ?", (*p, top_n)),
        "voice_hours": round(voice_seconds(lo, hi, user_id) / 3600, 1),
        "longest_streak": longest_streak(user_id, lo, hi),
    }


def longest_streak(user_id, lo, hi):
    """Most consecutive UK days with at least one message."""
    days = [r[0] for r in _rows(
        "SELECT DISTINCT local_day FROM messages WHERE user_id = ? AND ts >= ? AND ts < ? "
        "AND local_day IS NOT NULL ORDER BY local_day", (user_id, lo, hi))]
    best = run = 0
    previous = None
    for day in days:
        current = datetime.strptime(day, "%Y-%m-%d").date()
        run = run + 1 if previous and (current - previous).days == 1 else 1
        best = max(best, run)
        previous = current
    return best


def voice_seconds(lo, hi, user_id=None):
    """Time in voice, by pairing each join/move with the next leave/move for that user.

    Sessions still open at the end of the window are closed at ``hi``; a session that
    started before it is counted from ``lo``, so a year's total never double-counts.
    """
    where = "ts >= ? AND ts < ?"
    params = [lo, hi]
    if user_id is not None:
        where += " AND user_id = ?"
        params.append(user_id)
    rows = _rows(f"SELECT user_id, action, ts FROM voice_events WHERE {where} "
                 f"AND action IN ('join','leave','move') ORDER BY user_id, ts", tuple(params))
    total = 0
    open_at = {}
    for uid, action, ts in rows:
        if action in ("join", "move"):
            if uid in open_at:              # move: close the old leg, open the new one
                total += ts - open_at[uid]
            open_at[uid] = ts
        elif action == "leave":
            start = open_at.pop(uid, None)
            if start is not None:
                total += ts - start
    for start in open_at.values():
        total += hi - start
    return max(total, 0)


# -------------------------------------------------------------------------- health

def coverage():
    """What the chronicle actually holds - the first thing to check before trusting a stat."""
    row = ChronicleDB.fetch_one(
        "SELECT COUNT(*), MIN(ts), MAX(ts), SUM(source = 'backfill') FROM messages") or (0, None, None, 0)
    def fmt(ts):
        return datetime.fromtimestamp(ts, UK).strftime("%Y-%m-%d %H:%M") if ts else None
    return {
        "messages": row[0],
        "first": fmt(row[1]),
        "last": fmt(row[2]),
        "backfilled": row[3] or 0,
        "reactions": _one("SELECT COUNT(*) FROM reactions"),
        "voice_events": _one("SELECT COUNT(*) FROM voice_events"),
        "member_events": _one("SELECT COUNT(*) FROM member_events"),
        "interactions": _one("SELECT COUNT(*) FROM interactions"),
        "channels_backfilled": _one("SELECT COUNT(*) FROM backfill_progress WHERE complete = 1"),
        "caught_up": _one("SELECT COUNT(*) FROM messages WHERE source = 'catchup'"),
        "outages": _one("SELECT COUNT(*) FROM outages"),
        "size_bytes": ChronicleDB.size_bytes(),
    }


def month_name(m):
    return calendar.month_abbr[m]
