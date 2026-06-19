"""One-off: award the Good Samaritan badge (with DM + the Silver reward) to anyone who has
already paid SOMEONE ELSE's benefits fine, found from the durable user_transactions ledger.

Run once on the VM:

    cd /home/ubuntu/HMS-Victory && venv/bin/python3 scripts/backfill_good_samaritan.py

Idempotent: award_badge_with_notify only notifies/pays a user who doesn't already hold the badge,
and this script also skips anyone who already has it - so re-running is safe (no double DMs).
"""
import os
import sys

import discord
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager  # noqa: E402


class BackfillClient(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user}. Backfilling Good Samaritan...")
        from lib.bot.event_handlers import award_badge_with_notify

        rows = DatabaseManager.fetch_all(
            "SELECT DISTINCT user_id FROM user_transactions "
            "WHERE reason LIKE 'Paid benefits fraud fine for %'")
        payers = [r[0] for r in (rows or [])]
        print(f"Found {len(payers)} member(s) who paid someone else's fine: {payers}")

        awarded = 0
        for uid in payers:
            try:
                if DatabaseManager.fetch_one(
                        "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_id = 'good_samaritan'",
                        (str(uid),)):
                    print(f"  {uid}: already has the badge - skipped.")
                    continue
                await award_badge_with_notify(self, int(uid), "good_samaritan")
                awarded += 1
                print(f"  {uid}: awarded Good Samaritan (+ DM + reward).")
            except Exception as e:
                print(f"  {uid}: FAILED - {e}")

        print(f"Done. Newly awarded: {awarded}.")
        await self.close()


if __name__ == "__main__":
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN is missing in the environment (.env or env var).")
        sys.exit(1)
    client = BackfillClient(intents=discord.Intents.default())
    client.run(token)
