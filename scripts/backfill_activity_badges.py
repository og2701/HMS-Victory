"""One-shot backfill of the activity games' badges from what's already saved, through the bot's own award path
(award_badge_with_notify: the badge, its UKPence from the bank, the inbox note, the bot-usage-log line and the DM),
for current members who don't hold them yet. Idempotent: a badge already held is skipped, so it's safe to re-run.

    Learner Plates   every kart race kept (kart_results), practice included
    Chequered Flag   first home in a kept race with at least two people in it
    No Peeking       a crossword finished with no hints (crossword_results)
    All-Rounder      a day with a Wordle guess, a crossword answer or hint, and a run of Climb, Spitfire and Paperboy

(Purple Patch is left out on purpose. Grand Slam goes by the race history already, as it's earned.)

Usage, from the project root on the bot's box:
    python scripts/backfill_activity_badges.py --dry-run   # who would get what
    python scripts/backfill_activity_badges.py             # award them
"""
import asyncio
import os
import sys

import discord
from dotenv import load_dotenv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
os.chdir(PROJECT_ROOT)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

import config  # noqa: E402
from database import DatabaseManager  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv
GAP = 1.5                     # seconds between awards, to keep the DMs and log lines well inside the rate limits


def _q(sql, args=()):
    return DatabaseManager.fetch_all(sql, args) or []


def earned() -> dict[str, set[str]]:
    """badge id -> the user ids the saved history says have earned it."""
    learner = {u for (u,) in _q("SELECT DISTINCT user_id FROM kart_results WHERE user_id IS NOT NULL AND finish_time IS NOT NULL")}
    chequered = {u for (u,) in _q(
        "SELECT k.user_id FROM kart_results k JOIN kart_races r ON r.id = k.race_id "
        "WHERE r.mode = 'race' AND r.outcome = 'race' AND k.place = 1 AND k.user_id IS NOT NULL AND k.finish_time IS NOT NULL "
        "AND (SELECT COUNT(*) FROM kart_results x WHERE x.race_id = k.race_id AND x.user_id IS NOT NULL) >= 2")}
    peeking = {u for (u,) in _q("SELECT DISTINCT user_id FROM crossword_results WHERE done AND hints = 0")}
    rounder = set()
    for day, uid in _q("SELECT day, user_id FROM wordle_results WHERE guesses > 0"):
        if not _q("SELECT 1 FROM crossword_results WHERE day = ? AND user_id = ? "
                  "AND (solved != '[]' OR hints > 0 OR wrong > 0 OR done)", (day, uid)):
            continue
        if all(_q(f"SELECT 1 FROM {g}_days WHERE date = ? AND user_id = ? AND runs > 0", (day, uid))
               for g in ("climb", "spitfire", "paperboy")):
            rounder.add(uid)
    return {"kart_learner": learner, "kart_chequered": chequered, "crossword_no_peeking": peeking, "all_rounder": rounder}


class Backfill(discord.Client):
    async def on_ready(self):
        try:
            from lib.bot.event_handlers import award_badge_with_notify
            guild = self.get_guild(config.GUILD_ID)
            todo = []
            for badge, users in earned().items():
                held = {u for (u,) in _q("SELECT user_id FROM user_badges WHERE badge_id = ?", (badge,))}
                todo += [(str(u), badge) for u in sorted(users - held)]
            members, gone = {}, set()
            for uid, _ in todo:
                if uid in members or uid in gone:
                    continue
                try:
                    members[uid] = await guild.fetch_member(int(uid))
                except discord.NotFound:
                    gone.add(uid)
            todo = [(u, b) for u, b in todo if u in members]
            print(f"{len(todo)} to award ({len(gone)} people no longer in the server left out)")
            for uid, badge in todo:
                print(("would award" if DRY_RUN else "awarding"), badge, "to", members[uid].display_name, uid)
                if not DRY_RUN:
                    await award_badge_with_notify(self, int(uid), badge)
                    await asyncio.sleep(GAP)
        finally:
            await self.close()


if __name__ == "__main__":
    Backfill(intents=discord.Intents.default()).run(os.environ["DISCORD_TOKEN"], log_handler=None)
