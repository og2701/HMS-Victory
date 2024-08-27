import discord
from discord.ext import tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
from lib.summary import initialize_summary_data, update_summary_data, post_summary
import pytz
from datetime import datetime, timedelta

from lib.event_handlers import *
from lib.setup_commands import define_commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COMMONS_CHANNEL_ID = 959501347571531776

class AClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.synced = False
        self.scheduler = AsyncIOScheduler()
        self.image_cache = {}

    async def on_ready(self):
        await on_ready(self, tree, self.scheduler)

    async def on_message(self, message):
        if message.author.bot:
            return

        initialize_summary_data()
        update_summary_data("messages", channel_id=message.channel.id)
        update_summary_data("active_members", user_id=message.author.id)

        await on_message(self, message)

    async def on_interaction(self, interaction):
        await on_interaction(interaction)

    async def on_member_join(self, member):
        initialize_summary_data()
        update_summary_data("members_joined")

        await on_member_join(member)

    async def on_member_remove(self, member):
        initialize_summary_data()
        update_summary_data("members_left")

        await on_member_remove(member)

    async def on_member_ban(self, guild, user):
        initialize_summary_data()
        update_summary_data("members_banned")

        await on_member_ban(guild, user)

    async def on_message_delete(self, message):
        if message.author.bot:
            return

        initialize_summary_data()
        update_summary_data("deleted_messages")

        await on_message_delete(self, message)

    async def on_message_edit(self, before, after):
        await on_message_edit(self, before, after)

    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        initialize_summary_data()
        update_summary_data("reactions_added")
        update_summary_data("reacting_members", user_id=user.id)

        await on_reaction_add(reaction, user)

    async def on_reaction_remove(self, reaction, user):
        if user.bot:
            return
        initialize_summary_data()
        update_summary_data("reactions_removed")
        update_summary_data("reacting_members", user_id=user.id, remove=True)

        await on_reaction_remove(reaction, user)

    async def on_member_update(self, before, after):
        await on_member_update(before, after)

    async def clear_image_cache(self):
        self.image_cache.clear()
        logger.info("Image cache cleared.")

    async def daily_summary(self):
        uk_timezone = pytz.timezone("Europe/London")
        yesterday = (datetime.now(uk_timezone) - timedelta(days=1)).strftime("%Y-%m-%d")
        
        await post_summary(self, COMMONS_CHANNEL_ID, "daily", date=yesterday)

    async def weekly_summary(self):
        await post_summary(self, COMMONS_CHANNEL_ID, "weekly")

    async def monthly_summary(self):
        await post_summary(self, COMMONS_CHANNEL_ID, "monthly")

client = AClient()
tree = discord.app_commands.CommandTree(client)

define_commands(tree, client)