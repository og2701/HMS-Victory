"""Climb HMS Victory: the activity's Doodle Jump style climb up the rigging.

The game runs on the player's screen; the bot keeps the money honest. Every run starts with
/climb/start (the bot notes the time) and ends with /climb/finish (the height reached). A height
is only believed up to what a sailor could climb in the time the bot saw pass, at CLIMB_MAX_SPEED
metres a second plus a little slack, so a faked score can't outrun the clock.

Only each player's best height of the day pays, at CLIMB_RATE UKP a metre up to CLIMB_CAP: beat
your best and you're paid the difference, and runs below it pay nothing. The rigging comes from
the day's seed (keyed with the activity's secret), so everyone climbs the same layout.

A new best is announced straight away in the channel the game was opened in, and that one line
is rewritten as the player beats it again within the half hour, so a run of attempts makes one
post.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time

import config
from database import DatabaseManager

log = logging.getLogger(__name__)

SLACK = 40                   # metres allowed on top of what the clock says is possible
START_GAP = 1.5              # seconds between one player's runs
_runs: dict[int, dict] = {}  # uid -> the run in progress
_posts: dict[int, asyncio.Task] = {}


def enabled() -> bool:
    return bool(getattr(config, "CLIMB_ENABLED", True))


def rate() -> float:
    return float(getattr(config, "CLIMB_RATE", 0.5))


def cap() -> int:
    return int(getattr(config, "CLIMB_CAP", 150))


def max_speed() -> float:
    return float(getattr(config, "CLIMB_MAX_SPEED", 9.0))


def seed_for(iso: str) -> str:
    salt = os.getenv("ACTIVITIES_SESSION_SECRET") or "climb"
    return hmac.new(salt.encode(), f"climb:{iso}".encode(), hashlib.sha256).hexdigest()[:16]


def _day(uid: int, iso: str) -> dict:
    row = DatabaseManager.fetch_one(
        "SELECT best, paid, runs FROM climb_days WHERE user_id = ? AND date = ?", (str(uid), iso))
    return {"best": row[0], "paid": row[1], "runs": row[2]} if row else {"best": 0, "paid": 0, "runs": 0}


def _save_day(uid: int, iso: str, d: dict) -> None:
    DatabaseManager.execute(
        "INSERT INTO climb_days (user_id, date, best, paid, runs) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, date) DO UPDATE SET best = excluded.best, paid = excluded.paid, runs = excluded.runs",
        (str(uid), iso, d["best"], d["paid"], d["runs"]))


def _ever(uid: int) -> int:
    row = DatabaseManager.fetch_one("SELECT MAX(best) FROM climb_days WHERE user_id = ?", (str(uid),))
    return int(row[0] or 0) if row else 0


def state(uid: int, date) -> dict:
    iso = date.isoformat()
    d = _day(uid, iso)
    return {"game": "climb", "date": iso, "dateLabel": f"{date:%A %-d %B}", "seed": seed_for(iso),
            "rate": rate(), "cap": cap(), "today": d, "best": _ever(uid),
            "rank": rank(iso, d["best"]) if d["best"] else None, "players": _players(iso)}


def _players(iso: str) -> int:
    row = DatabaseManager.fetch_one("SELECT COUNT(*) FROM climb_days WHERE date = ? AND best > 0", (iso,))
    return int(row[0]) if row else 0


BOARD = 10


def board(uid: int, date) -> dict:
    """The day's top climbers and the best ever, by user id (the API puts names to them), and
    where this player stands in each even when they're outside the top."""
    iso = date.isoformat()
    today = DatabaseManager.fetch_all(
        "SELECT user_id, best FROM climb_days WHERE date = ? AND best > 0 ORDER BY best DESC, user_id LIMIT ?",
        (iso, BOARD))
    ever = DatabaseManager.fetch_all(
        "SELECT user_id, MAX(best) AS top FROM climb_days WHERE best > 0 GROUP BY user_id ORDER BY top DESC, user_id LIMIT ?",
        (BOARD,))
    mine, mine_ever = _day(uid, iso)["best"], _ever(uid)
    row = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM (SELECT MAX(best) AS top FROM climb_days GROUP BY user_id) WHERE top > ?", (mine_ever,))
    everyone = DatabaseManager.fetch_one("SELECT COUNT(DISTINCT user_id) FROM climb_days WHERE best > 0")

    def rows(found):
        out, place, last = [], 0, None
        for i, (u, h) in enumerate(found):
            if h != last:
                place, last = i + 1, h
            out.append({"uid": str(u), "height": int(h), "rank": place})
        return out

    return {
        "today": rows(today), "allTime": rows(ever),
        "you": {"uid": str(uid), "today": {"height": mine, "rank": rank(iso, mine) if mine else None},
                "allTime": {"height": mine_ever, "rank": (int(row[0]) + 1) if mine_ever and row else None}},
        "players": {"today": _players(iso), "allTime": int(everyone[0]) if everyone else 0},
    }


def home_card(uid: int, date) -> dict | None:
    if not enabled():
        return None
    d = _day(uid, date.isoformat())
    return {"best": d["best"], "paid": d["paid"], "cap": cap()}


def opened(client, uid: int, date) -> None:
    pass


class Refuse(Exception):
    """A run the game won't take, with the reason to show the player."""


def start(uid: int, date) -> dict:
    if not enabled():
        raise Refuse("Climb HMS Victory is closed for now.")
    now = time.time()
    last = _runs.get(uid)
    if last and now - last["started"] < START_GAP:
        raise Refuse("Slow down a little.")
    iso = date.isoformat()
    _runs[uid] = {"started": now, "date": iso, "seed": seed_for(iso)}
    d = _day(uid, iso)
    d["runs"] += 1
    _save_day(uid, iso, d)
    return state(uid, date)


def finish(uid: int, date, body: dict) -> tuple[dict, dict]:
    """End the player's run. Returns (the new state, what happened: the height counted,
    UKP earned, whether it was a new best today, whether the height was cut down)."""
    run = _runs.pop(uid, None)
    if run is None:
        raise Refuse("That climb wasn't started properly, so it can't count. Have another go.")
    try:
        reported = max(0, int(body.get("height") or 0))
        said_time = float(body.get("time") or 0)
        bounces = max(0, int(body.get("bounces") or 0))
    except (TypeError, ValueError):
        raise Refuse("That climb didn't make sense.")
    if str(body.get("seed") or "") != run["seed"]:
        raise Refuse("That was a different day's rigging.")
    now = time.time()
    elapsed = now - run["started"]
    # what the clock allows: the bot's own time, and never more than the game says it took
    allowed = int(min(elapsed, said_time + 5) * max_speed() + SLACK)
    height = min(reported, allowed)
    trimmed = height < reported
    if trimmed:
        log.warning("climb height trimmed for %s: reported %s m in %.1fs (game said %.1fs), allowed %s m",
                    uid, reported, elapsed, said_time, allowed)
    iso = run["date"]
    d = _day(uid, iso)
    new_best = height > d["best"]
    earned = 0
    if new_best:
        d["best"] = height
        target = min(cap(), int(height * rate()))
        earned = max(0, target - d["paid"])
        if earned:
            from lib.economy.economy_manager import add_bb
            # discretionary like the daily puzzles: it scales with the bank's reserves
            if add_bb(uid, earned, reason="Climb HMS Victory", taxable=False, discretionary=True):
                d["paid"] += earned
            else:
                earned = 0
    _save_day(uid, iso, d)
    DatabaseManager.execute(
        "INSERT INTO climb_runs (user_id, date, started, ended, height, reported, game_time, bounces, earned, trimmed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uid), iso, int(run["started"]), int(now), height, reported, round(said_time, 1), bounces, earned, int(trimmed)))
    return state(uid, date), {"height": height, "earned": earned, "newBest": new_best, "trimmed": trimmed}


def rank(iso: str, height: int) -> int:
    row = DatabaseManager.fetch_one("SELECT COUNT(*) FROM climb_days WHERE date = ? AND best > ?", (iso, height))
    return int(row[0]) + 1 if row else 1


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


SITTING = 30 * 60            # beat your best again within this long and the same post is updated
_sittings: dict[int, dict] = {}


def post_best(uid: int, channel_id: int, iso: str) -> None:
    """Announce a new best in the channel the game was opened in: straight away, as one line
    per sitting that's rewritten as the best goes up, so a run of attempts makes one post."""
    async def send():
        d = _day(uid, iso)
        if not d["best"]:
            return
        paid = f" · +{d['paid']:,} UKP today" if d["paid"] else ""
        text = (f"⚓ <@{uid}> climbed **HMS Victory** to **{d['best']:,} m** "
                f"({_ordinal(rank(iso, d['best']))} today){paid}")
        prev = _sittings.get(uid)
        reuse = (prev and prev["ch"] == channel_id and prev["date"] == iso and time.time() - prev["at"] < SITTING)
        from lib.activities import launcher
        mid = await launcher.announce_or_edit(channel_id, text, "climb", prev["msg"] if reuse else None)
        if mid:
            _sittings[uid] = {"ch": channel_id, "date": iso, "msg": mid, "at": time.time()}
            log.info("climb best for %s %s in %s (%s m)", uid, "updated" if reuse else "posted", channel_id, d["best"])

    task = asyncio.create_task(send())
    _posts[uid] = task
    task.add_done_callback(lambda t: _posts.pop(uid, None) if _posts.get(uid) is t else None)
