"""Daily score games in the activity (Climb HMS Victory, Spitfire): free to play, new each day,
paying for the player's best score of the day.

The game runs on the player's screen; the bot keeps the money honest. Every run starts with
/<game>/start, which hands back a signed receipt of when it began, and ends with /<game>/finish
carrying that receipt and the score. A score is only believed up to what's possible in the time
the bot saw pass (the game's max_rate a second, plus a little slack), so a faked score can't
outrun the clock, and a run sent twice counts once. The receipt means a run survives the bot
restarting, and a finish that had to wait for a phone's connection still counts (for 30 minutes).

Only each player's best of the day pays, at <PREFIX>_RATE UKP a point up to <PREFIX>_CAP: beat
your best and you're paid the difference, and runs below it pay nothing. The level comes from the
day's seed (keyed with the activity's secret), so everyone plays the same one.

A new best is announced straight away in the channel the game was opened in, with a picture, and
that one post is rewritten as the player beats it again within the half hour.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass, field

import config
from database import DatabaseManager

log = logging.getLogger(__name__)

START_GAP = 1.0              # seconds between one player's runs
RUN_MAX_AGE = 30 * 60        # a finish that arrives later than this after its start doesn't count
SITTING = 30 * 60            # beat your best again within this long and the same post is updated
BOARD = 100                  # rows on each leaderboard tab (the page scrolls them)


class Refuse(Exception):
    """A run the game won't take, with the reason to show the player."""


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _member(uid: int):
    """The player as the main bot sees them (it has the member list; the activities bot doesn't)."""
    try:
        from lib.activities.casino import base
        guild = base.CLIENT.get_guild(config.GUILD_ID) if base.CLIENT else None
        return guild.get_member(int(uid)) if guild else None
    except Exception:
        return None


@dataclass
class ScoreGame:
    key: str                 # "climb": the routes, the seed, the Play button
    label: str               # "Climb HMS Victory": the payment reason and the closed message
    prefix: str              # "CLIMB": config names (CLIMB_RATE, CLIMB_CAP, ...)
    days: str                # table of each player's day: best, paid, runs
    runs: str                # table of every run
    score_col: str           # the runs table's score column
    count_col: str           # the runs table's column for bounces, flaps, ...
    rate: float              # defaults for the config values
    cap: int
    max_rate: float          # score a second the clock allows
    max_score: int
    slack: float             # score allowed on top of what the clock says
    post: str                # "climbed **HMS Victory** to **{score} m**"
    emoji: str
    nobody: str = "A sailor"
    _runs: dict = field(default_factory=dict)        # uid -> the run in progress (pages from before receipts)
    _last_start: dict = field(default_factory=dict)
    _posts: dict = field(default_factory=dict)
    _sittings: dict = field(default_factory=dict)

    # ---- settings ------------------------------------------------------------------------------

    def _cfg(self, name, default):
        return getattr(config, f"{self.prefix}_{name}", default)

    def enabled(self) -> bool:
        return bool(self._cfg("ENABLED", True))

    def pay_rate(self) -> float:
        return float(self._cfg("RATE", self.rate))

    def pay_cap(self) -> int:
        return int(self._cfg("CAP", self.cap))

    def speed(self) -> float:
        return float(self._cfg("MAX_SPEED", self.max_rate))

    def ceiling(self) -> int:
        return int(self._cfg("MAX_HEIGHT", self.max_score))

    def seed_for(self, iso: str) -> str:
        salt = os.getenv("ACTIVITIES_SESSION_SECRET") or self.key
        return hmac.new(salt.encode(), f"{self.key}:{iso}".encode(), hashlib.sha256).hexdigest()[:16]

    # ---- a player's day ------------------------------------------------------------------------

    def day(self, uid: int, iso: str) -> dict:
        row = DatabaseManager.fetch_one(
            f"SELECT best, paid, runs FROM {self.days} WHERE user_id = ? AND date = ?", (str(uid), iso))
        return {"best": row[0], "paid": row[1], "runs": row[2]} if row else {"best": 0, "paid": 0, "runs": 0}

    def _save_day(self, uid: int, iso: str, d: dict) -> None:
        DatabaseManager.execute(
            f"INSERT INTO {self.days} (user_id, date, best, paid, runs) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, date) DO UPDATE SET best = excluded.best, paid = excluded.paid, runs = excluded.runs",
            (str(uid), iso, d["best"], d["paid"], d["runs"]))

    def ever(self, uid: int) -> int:
        row = DatabaseManager.fetch_one(f"SELECT MAX(best) FROM {self.days} WHERE user_id = ?", (str(uid),))
        return int(row[0] or 0) if row else 0

    def players(self, iso: str) -> int:
        row = DatabaseManager.fetch_one(f"SELECT COUNT(*) FROM {self.days} WHERE date = ? AND best > 0", (iso,))
        return int(row[0]) if row else 0

    def rank(self, iso: str, score: int) -> int:
        row = DatabaseManager.fetch_one(f"SELECT COUNT(*) FROM {self.days} WHERE date = ? AND best > ?", (iso, score))
        return int(row[0]) + 1 if row else 1

    def state(self, uid: int, date) -> dict:
        iso = date.isoformat()
        d = self.day(uid, iso)
        return {"game": self.key, "date": iso, "dateLabel": f"{date:%A %-d %B}", "seed": self.seed_for(iso),
                "rate": self.pay_rate(), "cap": self.pay_cap(), "today": d, "best": self.ever(uid),
                "rank": self.rank(iso, d["best"]) if d["best"] else None, "players": self.players(iso)}

    def home_card(self, uid: int, date) -> dict | None:
        if not self.enabled():
            return None
        d = self.day(uid, date.isoformat())
        return {"best": d["best"], "paid": d["paid"], "cap": self.pay_cap()}

    def opened(self, client, uid: int, date) -> None:
        pass

    def board(self, uid: int, date) -> dict:
        """The day's top scores and the best ever, by user id (the API puts names to them), and
        where this player stands in each even when they're outside the top. Rows carry the
        score as "height", the name the first game gave it."""
        iso = date.isoformat()
        today = DatabaseManager.fetch_all(
            f"SELECT user_id, best FROM {self.days} WHERE date = ? AND best > 0 ORDER BY best DESC, user_id LIMIT ?",
            (iso, BOARD))
        ever = DatabaseManager.fetch_all(
            f"SELECT user_id, MAX(best) AS top FROM {self.days} WHERE best > 0 GROUP BY user_id "
            "ORDER BY top DESC, user_id LIMIT ?", (BOARD,))
        mine, mine_ever = self.day(uid, iso)["best"], self.ever(uid)
        row = DatabaseManager.fetch_one(
            f"SELECT COUNT(*) FROM (SELECT MAX(best) AS top FROM {self.days} GROUP BY user_id) WHERE top > ?", (mine_ever,))
        everyone = DatabaseManager.fetch_one(f"SELECT COUNT(DISTINCT user_id) FROM {self.days} WHERE best > 0")

        def rows(found):
            out, place, last = [], 0, None
            for i, (u, h) in enumerate(found):
                if h != last:
                    place, last = i + 1, h
                out.append({"uid": str(u), "height": int(h), "rank": place})
            return out

        return {
            "today": rows(today), "allTime": rows(ever),
            "you": {"uid": str(uid), "today": {"height": mine, "rank": self.rank(iso, mine) if mine else None},
                    "allTime": {"height": mine_ever, "rank": (int(row[0]) + 1) if mine_ever and row else None}},
            "players": {"today": self.players(iso), "allTime": int(everyone[0]) if everyone else 0},
        }

    # ---- runs --------------------------------------------------------------------------------

    def _sign(self, uid: int, started_ms: int, iso: str) -> str:
        salt = os.getenv("ACTIVITIES_SESSION_SECRET") or self.key
        return hmac.new(salt.encode(), f"{self.key}run:{uid}:{started_ms}:{iso}".encode(), hashlib.sha256).hexdigest()[:20]

    def _receipt(self, uid: int, started: float, iso: str) -> str:
        """A signed note of when a run started, handed to the page so the run can be counted even
        if the bot restarts or the finish has to be sent again: the page can't forge or move it."""
        ms = int(started * 1000)
        return f"{ms}.{iso}.{self._sign(uid, ms, iso)}"

    def _read_receipt(self, uid: int, receipt: str) -> dict | None:
        try:
            ms, iso, sig = receipt.split(".")
            ms = int(ms)
        except ValueError:
            return None
        if not hmac.compare_digest(sig, self._sign(uid, ms, iso)):
            return None
        return {"started": ms / 1000, "date": iso, "seed": self.seed_for(iso)}

    def start(self, uid: int, date) -> dict:
        if not self.enabled():
            raise Refuse(f"{self.label} is closed for now.")
        now = time.time()
        if now - self._last_start.get(uid, 0) < START_GAP:
            raise Refuse("Slow down a little.")
        self._last_start[uid] = now
        iso = date.isoformat()
        self._runs[uid] = {"started": now, "date": iso, "seed": self.seed_for(iso)}
        d = self.day(uid, iso)
        d["runs"] += 1
        self._save_day(uid, iso, d)
        return {**self.state(uid, date), "run": self._receipt(uid, now, iso)}

    def finish(self, uid: int, date, body: dict) -> tuple[dict, dict]:
        """End the player's run. Returns (the new state, what happened: the score counted, UKP
        earned, whether it was a new best today, whether the score was cut down)."""
        receipt = str(body.get("run") or "")
        if receipt:
            run = self._read_receipt(uid, receipt)
            if run is None:
                raise Refuse("That run's receipt didn't check out.")
            if self._runs.get(uid, {}).get("started") == run["started"]:
                self._runs.pop(uid, None)
        else:
            run = self._runs.pop(uid, None)
        if run is None:
            raise Refuse("That run wasn't started properly, so it can't count. Have another go.")
        if time.time() - run["started"] > RUN_MAX_AGE:
            raise Refuse("That run finished too long ago to count now.")
        if DatabaseManager.fetch_one(f"SELECT 1 FROM {self.runs} WHERE user_id = ? AND started = ?",
                                     (str(uid), int(run["started"]))):
            # already counted: the page sent it again because it never heard back the first time
            return self.state(uid, date), {"height": 0, "earned": 0, "newBest": False, "trimmed": False, "again": True}
        try:
            reported = max(0, int(body.get("score", body.get("height")) or 0))
            said_time = float(body.get("time") or 0)
            count = max(0, int(body.get("count", body.get("bounces")) or 0))
        except (TypeError, ValueError):
            raise Refuse("That run didn't make sense.")
        if str(body.get("seed") or "") != run["seed"]:
            raise Refuse("That was a different day's level.")
        now = time.time()
        elapsed = now - run["started"]
        # what the clock allows: the bot's own time, and never more than the game says it took
        allowed = min(int(min(elapsed, said_time + 5) * self.speed() + self.slack), self.ceiling())
        score = min(reported, allowed)
        trimmed = score < reported
        if trimmed:
            log.warning("%s score trimmed for %s: reported %s in %.1fs (game said %.1fs), allowed %s",
                        self.key, uid, reported, elapsed, said_time, allowed)
        iso = run["date"]
        d = self.day(uid, iso)
        new_best = score > d["best"]
        earned = 0
        if new_best:
            d["best"] = score
            target = min(self.pay_cap(), int(score * self.pay_rate()))
            earned = max(0, target - d["paid"])
            if earned:
                from lib.economy.economy_manager import add_bb
                # discretionary like the daily puzzles: it scales with the bank's reserves
                if add_bb(uid, earned, reason=self.label, taxable=False, discretionary=True):
                    d["paid"] += earned
                else:
                    earned = 0
        self._save_day(uid, iso, d)
        DatabaseManager.execute(
            f"INSERT INTO {self.runs} (user_id, date, started, ended, {self.score_col}, reported, game_time, "
            f"{self.count_col}, earned, trimmed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(uid), iso, int(run["started"]), int(now), score, reported, round(said_time, 1), count, earned, int(trimmed)))
        return self.state(uid, date), {"height": score, "earned": earned, "newBest": new_best, "trimmed": trimmed}

    # ---- telling the channel -------------------------------------------------------------------

    async def picture(self, uid: int, iso: str, d: dict) -> bytes | None:
        """The card for the post: avatar, name, score, place, pay, and today's top three."""
        try:
            from lib.activities import daily_card
            member = _member(uid)
            avatar = None
            if member is not None:
                try:
                    avatar = await member.display_avatar.replace(size=256, format="png").read()
                except Exception:
                    avatar = None
            top = DatabaseManager.fetch_all(
                f"SELECT user_id, best FROM {self.days} WHERE date = ? AND best > 0 ORDER BY best DESC, user_id LIMIT 3",
                (iso,))
            names = []
            for u, h in top:
                m = _member(int(u))
                names.append((getattr(m, "display_name", None) or self.nobody, int(h), str(u) == str(uid)))
            return await daily_card.card_png(self.key, name=getattr(member, "display_name", None) or self.nobody,
                                             avatar=avatar, score=d["best"], rank=self.rank(iso, d["best"]),
                                             players=self.players(iso), paid=d["paid"], top=names)
        except Exception:
            log.warning("couldn't make the %s picture", self.key, exc_info=True)
            return None

    def post_best(self, uid: int, channel_id: int, iso: str) -> None:
        """Announce a new best in the channel the game was opened in: straight away, as one post
        per sitting that's rewritten as the best goes up, so a run of attempts makes one post."""
        async def send():
            d = self.day(uid, iso)
            if not d["best"]:
                return
            paid = f" · +{d['paid']:,} UKP today" if d["paid"] else ""
            best = f"{d['best']:,}"
            text = (f"{self.emoji} <@{uid}> {self.post.format(score=best)} "
                    f"({ordinal(self.rank(iso, d['best']))} today){paid}")
            prev = self._sittings.get(uid)
            reuse = (prev and prev["ch"] == channel_id and prev["date"] == iso and time.time() - prev["at"] < SITTING)
            png = await self.picture(uid, iso, d)
            from lib.activities import launcher
            mid = await launcher.announce_or_edit(channel_id, text, self.key, prev["msg"] if reuse else None, png=png)
            if mid:
                self._sittings[uid] = {"ch": channel_id, "date": iso, "msg": mid, "at": time.time()}
                log.info("%s best for %s %s in %s (%s)", self.key, uid, "updated" if reuse else "posted",
                         channel_id, d["best"])

        task = asyncio.create_task(send())
        self._posts[uid] = task
        task.add_done_callback(lambda t: self._posts.pop(uid, None) if self._posts.get(uid) is t else None)


CLIMB = ScoreGame(
    key="climb", label="Climb HMS Victory", prefix="CLIMB", days="climb_days", runs="climb_runs",
    score_col="height", count_col="bounces", rate=0.5, cap=150, max_rate=9.0, max_score=2000, slack=40,
    post="climbed **HMS Victory** to **{score} m**", emoji="⚓")

SPITFIRE = ScoreGame(
    key="spitfire", label="Spitfire", prefix="SPITFIRE", days="spitfire_days", runs="spitfire_runs",
    score_col="score", count_col="flaps", rate=3, cap=150, max_rate=1.25, max_score=1000, slack=3,
    post="flew the **Spitfire** past **{score} balloons**", emoji="✈️", nobody="A pilot")

GAMES = {g.key: g for g in (CLIMB, SPITFIRE)}
