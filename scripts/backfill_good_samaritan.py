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


def _load_token():
    """Resolve the bot token. The bot itself reads it from the systemd unit's
    Environment=DISCORD_TOKEN=...; that env isn't inherited when this script is run by hand, so
    fall back to env -> .env -> reading it straight from the unit file (never printed)."""
    tok = os.getenv("DISCORD_TOKEN")
    if tok:
        return tok
    load_dotenv()
    tok = os.getenv("DISCORD_TOKEN")
    if tok:
        return tok
    import re
    import subprocess
    unit = "/etc/systemd/system/hms-victory.service"
    text = ""
    try:
        with open(unit) as f:
            text = f.read()
    except (PermissionError, FileNotFoundError):
        try:
            text = subprocess.run(["sudo", "-n", "cat", unit],
                                  capture_output=True, text=True).stdout
        except Exception:
            text = ""
    m = re.search(r'DISCORD_TOKEN=["\']?([^"\'\s]+)', text)
    return m.group(1) if m else None


if __name__ == "__main__":
    token = _load_token()
    if not token:
        print("Error: couldn't find DISCORD_TOKEN (env, .env, or the systemd unit).")
        sys.exit(1)
    client = BackfillClient(intents=discord.Intents.default())
    client.run(token)
