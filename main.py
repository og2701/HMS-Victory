import discord
from discord.ext import tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging
import pytz
from datetime import datetime, timedelta
import shutil
import os

from lib.event_handlers import *
from lib.setup_commands import define_commands
from lib.settings import *
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.on_message_functions import *

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def zip_and_send_folder(client, folder_path, channel_id, zip_filename_prefix):
    zip_file_path = f"{folder_path}.zip"

    if os.path.exists(folder_path):
        shutil.make_archive(folder_path, "zip", folder_path)
        archive_channel = client.get_channel(channel_id)

        if archive_channel:
            with open(zip_file_path, "rb") as zip_file:
                await archive_channel.send(
                    file=discord.File(zip_file, f"{zip_filename_prefix}.zip")
                )

            logger.info(
                f"Sent archive '{zip_filename_prefix}.zip' to channel {archive_channel.name}."
            )

        os.remove(zip_file_path)
    else:
        logger.warning(f"Folder '{folder_path}' does not exist.")


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
            for role in message.author.roles:
                if role.id == ROLES.DONT_DM_WHEN_MESSAGE_BLOCKED:
                    return

            embed = message.embeds[0]
            rule_name = embed.fields[0].value
            channel_id = embed.fields[1].value
            bad_word = embed.fields[4].value

            button = discord.ui.Button(
                    custom_id = f"role_{ROLES.DONT_DM_WHEN_MESSAGE_BLOCKED}",
                    label = "Toggle DMs when a message is blocked",
                    style = discord.ButtonStyle.primary
                )
            
            view = discord.ui.View(timeout=None)
            view.add_item(button)

            await message.author.send(
                f"<@{message.author.id}>, your message in <#{channel_id}> was blocked due to it triggering **{rule_name}**, the flagged word is ||{bad_word}||",
                view = view
            )

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

    async def clear_image_cache(self):
        self.image_cache.clear()
        logger.info("Image cache cleared.")

    async def daily_summary(self):
        uk_timezone = pytz.timezone("Europe/London")
        yesterday = (datetime.now(uk_timezone) - timedelta(days=1)).strftime("%Y-%m-%d")

        await post_summary(self, CHANNELS.COMMONS, "daily", date=yesterday)

        await zip_and_send_folder(
            client=self,
            folder_path="./daily_summaries",
            channel_id=CHANNELS.DATA_BACKUP,
            zip_filename_prefix=f"daily_summaries_as_of_{yesterday}",
        )

    async def weekly_summary(self):
        await post_summary(self, CHANNELS.COMMONS, "weekly")

    async def monthly_summary(self):
        await post_summary(self, CHANNELS.COMMONS, "monthly")


client = AClient()
tree = discord.app_commands.CommandTree(client)

define_commands(tree, client)
