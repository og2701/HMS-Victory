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

from lib.event_handlers import *
from lib.setup_commands import define_commands
from lib.settings import *
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.on_message_functions import *
from lib.prediction_system import Prediction, _load, _save
from lib.ukpence import add_bb 
from lib.constants import GUILD_ID, CHANNELS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


MAX_PART_SIZE = 8 * 1024 * 1024 #8mb

async def zip_and_send_folder(client, folder_path, channel_id, zip_filename_prefix):
    if not os.path.exists(folder_path):
        logger.warning(f"Folder '{folder_path}' does not exist.")
        return

    archive_channel = client.get_channel(channel_id)
    if not archive_channel:
        logger.warning(f"Channel ID {channel_id} not found.")
        return

    logger.info(f"Creating in-memory ZIP for {folder_path}...")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_path):
            for file_in_folder in files:
                file_path = os.path.join(root, file_in_folder)
                archive_name = os.path.relpath(file_path, start=folder_path)
                zipf.write(file_path, archive_name)

    zip_buffer.seek(0) 

    file_number = 1
    while True:
        chunk = zip_buffer.read(MAX_PART_SIZE)
        if not chunk:
            break 

        part_filename = f"{zip_filename_prefix}_part{file_number}.zip"
        part_buffer = io.BytesIO(chunk)
        part_buffer.seek(0)

        await archive_channel.send(file=discord.File(fp=part_buffer, filename=part_filename))
        logger.info(f"Sent part {file_number}: {part_filename}")

        file_number += 1

    logger.info("Backup complete.")


async def send_json_files(client, folder_path, channel_id):
    if not os.path.exists(folder_path):
        logger.warning(f"Folder '{folder_path}' does not exist.")
        return

    archive_channel = client.get_channel(channel_id)
    if not archive_channel:
        logger.warning(f"Channel ID {channel_id} not found.")
        return

    json_files = [f for f in os.listdir(folder_path) if f.endswith(".json") and os.path.isfile(os.path.join(folder_path, f))]

    if not json_files:
        logger.info("No JSON files found to upload.")
        return

    logger.info(f"Found {len(json_files)} JSON files. Uploading...")

    for file_name in json_files:
        file_path = os.path.join(folder_path, file_name)

        if os.path.getsize(file_path) > 8 * 1024 * 1024:
            logger.warning(f"Skipping {file_name} - File too large for Discord.")
            continue

        with open(file_path, "rb") as file_to_send:
            await archive_channel.send(file=discord.File(file_to_send, filename=file_name))
            logger.info(f"Uploaded {file_name}.")

    logger.info("All JSON files uploaded.")


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
        self.predictions={int(k):Prediction.from_dict(v) for k,v in _load().items()}

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

        if message.author.id == 557628352828014614 and message.embeds: # User ID for "Ticket Tool" bot
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

    async def daily_summary(self):
        uk_timezone = pytz.timezone("Europe/London")
        yesterday_dt = datetime.now(uk_timezone) - timedelta(days=1)
        yesterday_str = yesterday_dt.strftime("%Y-%m-%d")

        summary_file_path = f"daily_summaries/daily_summary_{yesterday_str}.json"
        awarded_users_for_log = []

        if os.path.exists(summary_file_path):
            try:
                with open(summary_file_path, "r") as file:
                    daily_data = json.load(file)
            except json.JSONDecodeError:
                logger.error(f"Could not decode JSON from {summary_file_path}. Skipping top chatter rewards for {yesterday_str}.")
                daily_data = {}
            
            active_members_data = daily_data.get("active_members", {})
            if active_members_data:
                sorted_active_members = sorted(active_members_data.items(), key=lambda item: item[1], reverse=True)

                reward_amount = 50  # Flat reward for top 5
                num_to_reward = 5   # Number of top chatters to reward

                log_channel_target_id = CHANNELS.LOGS 
                log_channel = self.get_channel(log_channel_target_id)

                for i, (user_id_str, message_count) in enumerate(sorted_active_members):
                    if i < num_to_reward: 
                        user_id = int(user_id_str)
                        add_bb(user_id, reward_amount) 
                        
                        awarded_user_info = f"User ID {user_id} (Top {i+1} chatter, {message_count} messages): +{reward_amount} UKPence"
                        awarded_users_for_log.append(awarded_user_info)
                    else:
                        break 
                
                if awarded_users_for_log and log_channel:
                    log_message = f"Top {num_to_reward} Chatter Rewards for {yesterday_str}:\n" + "\n".join(awarded_users_for_log)
                    logger.info(log_message) 
                    try:
                        await log_channel.send(f"```{log_message}```")
                    except Exception as e:
                        logger.error(f"Failed to send top chatter reward log to Discord: {e}")
                elif not awarded_users_for_log and active_members_data :
                     logger.info(f"Fewer than {num_to_reward} chatters on {yesterday_str}, or no one eligible for rewards.")
                elif not active_members_data: #This case is covered by the outer 'if active_members_data:'
                     logger.info(f"No active members data found in {summary_file_path} for {yesterday_str}. No rewards distributed.")
            else:
                logger.info(f"No active members data found in {summary_file_path} for {yesterday_str}. No rewards distributed.")
        else:
            logger.warning(f"No summary data file found at {summary_file_path} for {yesterday_str}. Skipping top chatter rewards.")

        await post_summary(self, CHANNELS.COMMONS, "daily", date=yesterday_str)

        await zip_and_send_folder(
            client=self,
            folder_path="./daily_summaries",
            channel_id=CHANNELS.DATA_BACKUP,
            zip_filename_prefix=f"daily_summaries_as_of_{yesterday_str}",
        )

    async def weekly_summary(self):
        await post_summary(self, CHANNELS.COMMONS, "weekly")

    async def monthly_summary(self):
        await post_summary(self, CHANNELS.COMMONS, "monthly")

    async def backup_bot(self):
        logger.info("Backing up bot...")
        await send_json_files(
            client=self,
            folder_path="./", 
            channel_id=CHANNELS.DATA_BACKUP
        )


client = AClient()
tree = discord.app_commands.CommandTree(client)

define_commands(tree, client)