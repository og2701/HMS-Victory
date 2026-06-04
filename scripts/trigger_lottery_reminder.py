"""Manually fire a lottery reminder into the casino channel, right now.

Bypasses the random scheduler entirely - logs into the gateway, finds the open
round and posts one reminder (same format the scheduled job uses, with a random
flavour line and a link back to the live board). Useful for testing the copy or
nudging the channel on demand.

Usage:
    python scripts/trigger_lottery_reminder.py            # post a reminder now
    python scripts/trigger_lottery_reminder.py --dry-run  # print the body, don't send

The DISCORD_TOKEN is read from the environment (.env) or, on the server, from the
systemd unit - same fallback as the other gateway scripts.
"""

import os
import sys
import asyncio
import discord
from dotenv import load_dotenv

load_dotenv()

# Add parent directory to path so `lib`/`config` import cleanly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.economy import lottery

DRY_RUN = "--dry-run" in sys.argv


class ReminderClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        try:
            rnd = lottery.get_open_round()
            if not rnd:
                print("No open lottery round - nothing to remind about.")
                return
            if not rnd.get("message_id") or not rnd.get("channel_id"):
                print(f"Round #{rnd['id']} has no live board message to link - skipping.")
                return
            if lottery._sold_out(rnd):
                print(f"Round #{rnd['id']} is sold out - reminders are suppressed.")
                return

            if DRY_RUN:
                sold = lottery.tickets_sold(rnd["id"])
                pot = sold * rnd["ticket_price"]
                print("--- DRY RUN (not sending) ---")
                print(f"Round #{rnd['id']} · jackpot {pot:,} UKPence "
                      f"({sold:,}/{rnd['ticket_cap']:,} sold) · price {rnd['ticket_price']:,}")
                return

            await lottery._post_reminder(self, rnd)
            print(f"Posted lottery reminder for round #{rnd['id']}.")
        finally:
            await self.close()


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        service_file = "/etc/systemd/system/hms-victory.service"
        if os.path.exists(service_file):
            import re
            try:
                with open(service_file) as f:
                    match = re.search(r'Environment="DISCORD_TOKEN=([^"]+)"', f.read())
                    if match:
                        token = match.group(1)
            except Exception as e:
                print(f"Error reading systemd service file: {e}")

    if not token:
        print("Error: DISCORD_TOKEN not found (set it in .env or the systemd unit).")
        return

    client = ReminderClient()
    async with client:
        await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
