import os
import sys
import sqlite3
import discord
import asyncio
from datetime import datetime, timezone
import re
from dotenv import load_dotenv

load_dotenv()

# Attempt to load token from systemd service file if not in env
if not os.getenv("DISCORD_TOKEN"):
    service_file = "/etc/systemd/system/hms-victory.service"
    if os.path.exists(service_file):
        try:
            with open(service_file, 'r') as f:
                content = f.read()
                match = re.search(r'Environment="DISCORD_TOKEN=([^"]+)"', content)
                if match:
                    os.environ["DISCORD_TOKEN"] = match.group(1)
        except Exception as e:
            print(f"Error reading systemd service file: {e}")


# Add parent directory to path to import config and database
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import GUILD_ID, USERS
from database import award_badge, DatabaseManager

class BackdateClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.guilds = True
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Logged in as {self.user} (ID: {self.user.id})")
        print("Starting backdate process...")
        
        guild = self.get_guild(GUILD_ID)
        if not guild:
            print(f"Error: Could not find guild with ID {GUILD_ID}")
            await self.close()
            return

        print(f"Checking guild: {guild.name}")
        
        # 1. Backdate Server Boosters
        print("\n--- Checking Server Boosters ---")
        boost_count = 0
        yearly_boost_count = 0
        now = datetime.now(timezone.utc)
        
        for member in guild.members:
            if member.premium_since:
                # Award regular booster badge
                if award_badge(str(member.id), 'server_booster'):
                    print(f"Awarded 'Supporter' to {member.display_name}")
                    boost_count += 1
                
                # Check for yearly booster
                boost_duration = now - member.premium_since
                if boost_duration.days >= 365:
                    if award_badge(str(member.id), 'yearly_booster'):
                        print(f"Awarded 'Diamond Hands' to {member.display_name}")
                        yearly_boost_count += 1
        
        print(f"Finished boosters: {boost_count} regular, {yearly_boost_count} yearly.")

        # 2. Backdate First Purchase
        print("\n--- Checking Shop Purchases ---")
        purchase_count = 0
        purchasers = DatabaseManager.fetch_all("SELECT DISTINCT user_id FROM shop_purchases")
        for (user_id,) in purchasers:
            if award_badge(user_id, 'first_purchase'):
                print(f"Awarded 'First Purchase' to user ID {user_id}")
                purchase_count += 1
        print(f"Finished purchases: {purchase_count} awarded.")

        # 3. Backdate Hall of Fame (if any)
        # Assuming HOF entries might be in user_badges already, but if we have a separate source:
        # For now, we'll just check if the hof badge was missed by anyone in the HOF list if it existed.
        
        print("\nBackdate process complete!")
        await self.close()

async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN environment variable not set.")
        return

    client = BackdateClient()
    async with client:
        await client.start(token)

if __name__ == "__main__":
    asyncio.run(main())
