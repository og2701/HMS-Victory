"""One-shot, idempotent backfill of badge UKPence rewards for badges members have ALREADY
earned (amounts from config.BADGE_REWARDS by rarity, paid from the bank).

Scope: only CURRENT server members who are in the economy (have a UKPence balance row).
Members who have left get nothing (run reclaim_left_member_balances.py to sweep their
leftover balances back to the bank). Idempotent via the badge_rewards ledger - safe to
re-run; it only pays badges not yet paid.

Usage (from the project root, with DISCORD_TOKEN in the env / .env / systemd unit):
    python scripts/backfill_badge_rewards.py --dry-run   # show what WOULD be paid
    python scripts/backfill_badge_rewards.py             # actually pay
"""
import os
import sys
import asyncio

import discord
from dotenv import load_dotenv

load_dotenv()
# Run from the project root so the relative DB path (DB_FILE='database.db') resolves.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import config
from database import DatabaseManager
from lib.economy.badge_rewards import reward_amount, already_paid, pay_badge_reward

DRY_RUN = "--dry-run" in sys.argv
BANK = str(config.BOT_ID)


class BackfillClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True  # need the full member list to scope to current members
        super().__init__(intents=intents)

    async def on_ready(self):
        try:
            await self._run()
        finally:
            await self.close()

    async def _run(self):
        # Ensure the reward ledger exists even if the bot hasn't restarted onto the new
        # schema yet (idempotent; identical DDL to database.init_db).
        DatabaseManager.execute(
            "CREATE TABLE IF NOT EXISTS badge_rewards (user_id TEXT NOT NULL, "
            "badge_id TEXT NOT NULL, amount INTEGER NOT NULL, paid_at INTEGER NOT NULL, "
            "PRIMARY KEY (user_id, badge_id))")

        guild = self.get_guild(config.GUILD_ID) or await self.fetch_guild(config.GUILD_ID)
        member_ids = {str(m.id) async for m in guild.fetch_members(limit=None)}
        expected = guild.member_count or 0
        print(f"Guild: {guild.name} · fetched {len(member_ids):,} members "
              f"(guild reports {expected:,})")
        if expected and len(member_ids) < expected * 0.9:
            print("ABORT: fetched member list looks incomplete (<90% of the guild). Re-run.")
            return

        # Badges earned by in-economy users (have a balance row), oldest awards first.
        rows = DatabaseManager.fetch_all(
            "SELECT ub.user_id, ub.badge_id FROM user_badges ub "
            "JOIN ukpence uk ON uk.user_id = ub.user_id "
            "WHERE uk.user_id != ? ORDER BY ub.awarded_at", (BANK,)) or []

        total = paid_count = skipped_left = skipped_done = 0
        users = set()
        for uid, badge_id in rows:
            if uid not in member_ids:
                skipped_left += 1
                continue
            if reward_amount(badge_id) <= 0:
                continue
            if already_paid(uid, badge_id):
                skipped_done += 1
                continue
            if DRY_RUN:
                got = reward_amount(badge_id)
            else:
                got = pay_badge_reward(uid, badge_id)
            if got > 0:
                total += got
                paid_count += 1
                users.add(uid)

        bank_row = DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (BANK,))
        bank = bank_row[0] if bank_row else 0
        print(f"\n[{'DRY RUN - nothing paid' if DRY_RUN else 'PAID'}] badge-reward backfill")
        print(f"  badges rewarded        : {paid_count:,}  to  {len(users):,} members")
        print(f"  total UKPence          : {total:,}")
        print(f"  skipped (left server)  : {skipped_left:,}")
        print(f"  skipped (already paid) : {skipped_done:,}")
        print(f"  bank balance now       : {bank:,}")
        if DRY_RUN:
            print("  (re-run without --dry-run to apply)")


def _load_token():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sf = "/etc/systemd/system/hms-victory.service"
        if os.path.exists(sf):
            import re
            try:
                with open(sf) as f:
                    m = re.search(r'Environment="DISCORD_TOKEN=([^"]+)"', f.read())
                    if m:
                        token = m.group(1)
            except Exception as e:
                print(f"Error reading systemd service file: {e}")
    return token


async def main():
    token = _load_token()
    if not token:
        print("Error: DISCORD_TOKEN not found (set it in .env or the systemd unit).")
        return
    client = BackfillClient()
    async with client:
        await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
