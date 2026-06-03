import os
import sys
import discord
import asyncio
from dotenv import load_dotenv

load_dotenv()

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CHANNELS
from database import DatabaseManager

class NotifyClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        user_id = 797207976548499518  # Johnny
        badge_id = 'high_roller'
        
        # Get badge info
        badge_info = DatabaseManager.fetch_one("SELECT name, description, icon_path, rarity FROM badges WHERE id = ?", (badge_id,))
        if not badge_info:
            print(f"Badge ID '{badge_id}' not found in database.")
            await self.close()
            return
        
        badge_name, badge_desc, badge_icon, badge_rarity = badge_info
        
        # Log to bot-usage-log channel
        log_channel_id = CHANNELS.BOT_USAGE_LOG
        log_channel = self.get_channel(log_channel_id)
        if log_channel:
            await log_channel.send(f"🎖️ **Badge Awarded**: <@{user_id}> just earned the **{badge_name}** {badge_icon} badge!")
            print("Logged to bot-usage-log")
        else:
            print(f"Could not find log channel with ID {log_channel_id}")
            
        # Notify user via DM
        try:
            user = await self.fetch_user(user_id)
            if user:
                color_map = {"Gold": 0xFFD700, "Silver": 0xC0C0C0, "Bronze": 0xCD7F32}
                embed = discord.Embed(
                    title="🎖️ New Badge Earned!",
                    description=f"Congratulations! You've just earned a new badge.",
                    color=color_map.get(badge_rarity, 0x3498db)
                )
                embed.add_field(name="Badge", value=f"{badge_icon} **{badge_name}**", inline=True)
                embed.add_field(name="How to earn", value=badge_desc, inline=True)
                embed.add_field(name="Rarity", value=badge_rarity, inline=True)
                embed.set_footer(text="Check your /rank to see all your badges!")
                
                await user.send(embed=embed)
                print(f"Successfully sent badge DM to user {user_id}")
            else:
                print(f"Could not find user with ID {user_id}")
        except Exception as e:
            print(f"Error sending DM: {e}")
            
        await self.close()

async def main():
    # Attempt to load token from environment
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        # Fallback to systemd env check
        service_file = "/etc/systemd/system/hms-victory.service"
        if os.path.exists(service_file):
            import re
            try:
                with open(service_file, 'r') as f:
                    content = f.read()
                    match = re.search(r'Environment="DISCORD_TOKEN=([^"]+)"', content)
                    if match:
                        token = match.group(1)
            except Exception as e:
                print(f"Error reading systemd service file: {e}")
                
    if not token:
        print("Error: DISCORD_TOKEN not found.")
        return
        
    client = NotifyClient()
    async with client:
        await client.start(token)

if __name__ == "__main__":
    asyncio.run(main())
