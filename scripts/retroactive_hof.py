import asyncio
import os
import discord
from dotenv import load_dotenv
import logging

load_dotenv()

from lib.bot.event_handlers import create_quote_image
from config import CHANNELS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("retroactive_hof")

class RetroactiveClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True
        super().__init__(intents=intents)
        self.image_cache = {}
        self.session = None

    async def setup_hook(self):
        import aiohttp
        self.session = aiohttp.ClientSession()

    async def on_ready(self):
        logger.info(f"Logged in as {self.user}")
        try:
            thread = self.get_channel(CHANNELS.HALL_OF_FAME_THREAD)
            if not thread:
                thread = await self.fetch_channel(CHANNELS.HALL_OF_FAME_THREAD)
            
            logger.info(f"Fetched thread: {thread.name}")
            count = 0
            
            async for msg in thread.history(limit=100):
                if msg.author.id == self.user.id and msg.embeds:
                    embed = msg.embeds[0]
                    if embed.url and "discord.com/channels/" in embed.url:
                        parts = embed.url.split("/")
                        guild_id = int(parts[-3])
                        channel_id = int(parts[-2])
                        message_id = int(parts[-1])
                        
                        try:
                            guild = self.get_guild(guild_id) or await self.fetch_guild(guild_id)
                            channel = guild.get_channel(channel_id) or await guild.fetch_channel(channel_id)
                            original_msg = await channel.fetch_message(message_id)
                            
                            logger.info(f"Regenerating image for: {message_id} by {original_msg.author}")
                            image_buffer = await create_quote_image(self, original_msg)
                            file = discord.File(image_buffer, filename="hof_quote.png")
                            
                            embed.set_image(url="attachment://hof_quote.png")
                            await msg.edit(embed=embed, attachments=[file])
                            logger.info(f"Updated Hall of Fame message: {msg.id}")
                            count += 1
                            await asyncio.sleep(2)
                            
                        except Exception as e:
                            logger.error(f"Error processing {message_id}: {e}")
            
            logger.info(f"Finished retroactively updating {count} messages.")
        except Exception as e:
            logger.error(f"Fatal error: {e}")
        finally:
            await self.close()

if __name__ == "__main__":
    client = RetroactiveClient()
    client.run(os.getenv("DISCORD_TOKEN"))
