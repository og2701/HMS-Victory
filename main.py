import discord
from discord.ext import tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import pytz
from datetime import datetime, timedelta
import shutil
import os
import zipfile
import io
import json
import aiohttp

from lib.bot.event_handlers import *
from lib.features.on_message_functions import *
from lib.bot.setup_commands import define_commands
from config import *
from lib.features.summary import initialize_summary_data, update_summary_data, post_summary
from lib.bot.event_handlers import *
from lib.economy.prediction_system import Prediction, _load as load_predictions, _save as save_predictions
from lib.economy.economy_manager import add_bb, get_all_balances as load_ukpence_data
from lib.economy.economy_stats_html import create_economy_stats_image
from database import init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_PART_SIZE = 8 * 1024 * 1024


from lib.core.backup_manager import restore_database_if_missing


class AClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.synced = False
        self.scheduler = AsyncIOScheduler()
        self.image_cache = {}
        self.stage_events=set()
        self.stage_join_times={}
        self.predictions={int(k):Prediction.from_dict(v) for k,v in load_predictions().items()}
        self._pending_uploads = {}  # For custom emoji/sticker uploads

    async def on_ready(self):
        await on_ready(self, tree, self.scheduler)

    async def on_message(self, message):
        if (
            message.author.id == USERS.COUNTRYBALL_BOT
            and "A wild countryball" in message.content
        ):
            channel = client.get_channel(CHANNELS.BOT_SPAM)
            if channel:
                await channel.send(
                    f"<@&{ROLES.BALL_INSPECTOR}> A wild countryball appeared!"
                )
            return

        if message.author.id == 557628352828014614 and message.embeds:
            await handle_ticket_closed_message(self, message)
            return

        if message.author.bot:
            return

        if message.type == discord.MessageType.auto_moderation_action:
            target_user_id_str = None
            if message.embeds:
                embed = message.embeds[0]
                for field in embed.fields:
                    if field.name.lower() == "user":
                        import re
                        match = re.search(r"<@!?(\d+)>", field.value)
                        if match:
                            target_user_id_str = match.group(1)
                        break

            target_user = None
            if target_user_id_str:
                try:
                    target_user = await self.fetch_user(int(target_user_id_str))
                except (discord.NotFound, ValueError):
                    logger.warning(f"Could not find user for automod DM: {target_user_id_str}")

            if target_user:
                member = message.guild.get_member(target_user.id)
                if member and any(role.id == ROLES.DONT_DM_WHEN_MESSAGE_BLOCKED for role in member.roles):
                    return

                rule_name = embed.fields[0].value
                channel_mention = embed.fields[1].value
                bad_word = embed.fields[4].value

                button = discord.ui.Button(
                        custom_id = f"role_{ROLES.DONT_DM_WHEN_MESSAGE_BLOCKED}",
                        label = "Toggle DMs when a message is blocked",
                        style = discord.ButtonStyle.primary
                    )

                view = discord.ui.View(timeout=None)
                view.add_item(button)

                try:
                    await target_user.send(
                        f"Your message in {channel_mention} was blocked due to it triggering **{rule_name}** filter. The flagged word/phrase was ||{bad_word}||.",
                        view=view
                    )
                except discord.Forbidden:
                    logger.warning(f"Cannot DM user {target_user.id} (automod notification).")
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

    async def on_voice_state_update(self, member, before, after):
        await on_voice_state_update(member, before, after)

    async def on_stage_instance_create(self, stage_instance):
        await on_stage_instance_create(stage_instance)

    async def on_stage_instance_delete(self, stage_instance):
        await on_stage_instance_delete(stage_instance)

    async def clear_image_cache(self):
        self.image_cache.clear()
        logger.info("Image cache cleared.")


client = AClient()
tree = discord.app_commands.CommandTree(client)

define_commands(tree, client)

async def main():
    async with client:
        await restore_database_if_missing()
        await client.start(os.getenv("DISCORD_TOKEN"))
