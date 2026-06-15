"""Reclaim the UKPence balances of users who have LEFT the server back to the bank.

Supply-conserving: each departed user's balance is moved into the bank (the fixed 800k
total is unchanged), and their balance is left at 0. A current member's balance is never
touched. Idempotent - a reclaimed balance is 0, so a re-run sweeps nothing.

Run this alongside the badge-reward backfill so departed members don't sit on balances (and
so nobody who has left can be paid).

Usage (from the project root, with DISCORD_TOKEN in the env / .env / systemd unit):
    python scripts/reclaim_left_member_balances.py --dry-run   # show what WOULD move
    python scripts/reclaim_left_member_balances.py             # actually reclaim
"""
import os
import sys
import asyncio

import discord
from dotenv import load_dotenv

load_dotenv()
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(PROJECT_ROOT)
os.chdir(PROJECT_ROOT)

import config
from database import DatabaseManager
from lib.economy.economy_manager import remove_bb

DRY_RUN = "--dry-run" in sys.argv
BANK = str(config.BOT_ID)


class ReclaimClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)

    async def on_ready(self):
        try:
            await self._run()
        finally:
            await self.close()

    async def _run(self):
        guild = self.get_guild(config.GUILD_ID) or await self.fetch_guild(config.GUILD_ID)
        member_ids = {str(m.id) async for m in guild.fetch_members(limit=None)}
        print(f"Guild: {guild.name} · current members: {len(member_ids):,}")

        rows = DatabaseManager.fetch_all(
            "SELECT user_id, balance FROM ukpence WHERE user_id != ? AND balance > 0 "
            "ORDER BY balance DESC", (BANK,)) or []
        total = count = 0
        for uid, bal in rows:
            if uid in member_ids:
                continue  # still in the server - leave their balance alone
            count += 1
            total += bal
            if not DRY_RUN:
                # to_bank=True moves the debited amount into the bank: supply is conserved.
                remove_bb(int(uid), bal, reason="Reclaimed: left the server", to_bank=True)

        bank_row = DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (BANK,))
        bank = bank_row[0] if bank_row else 0
        print(f"\n[{'DRY RUN - nothing moved' if DRY_RUN else 'RECLAIMED'}] left-member balances")
        print(f"  departed users with a balance : {count:,}")
        print(f"  UKPence reclaimed to bank      : {total:,}")
        print(f"  bank balance now               : {bank:,}")
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
    client = ReclaimClient()
    async with client:
        await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
