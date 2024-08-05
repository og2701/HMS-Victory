import discord
from discord import app_commands, Intents, Interaction, Client, InteractionType, Member
from typing import Optional
from lib.log_functions import *
from lib.utils import restrict_channel_for_new_members
from lib.summary import initialize_summary_data, update_summary_data, post_summary
import os
import json
from datetime import datetime, timedelta
from discord.ext import tasks
from collections import defaultdict
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from lib.commands import (
    updateRoleAssignments,
    colourPalette,
    gridify,
    persistantRoleButtons,
    handleRoleButtonInteraction,
    screenshotCanvas,
    add_iceberg_text,
    show_iceberg
)

MINISTER_ROLE_ID = 1250190944502943755
CABINET_ROLE_ID = 959493505930121226
LOG_CHANNEL_ID = 959723562892144690
POLITICS_CHANNEL_ID = 1141097424849481799
COMMONS_CHANNEL_ID = 1197572903294730270 #959501347571531776

POLITICS_WHITELISTED_USER_IDS = []

initialize_summary_data()

class AClient(Client):
    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.reactions = True
        intents.typing = True
        intents.voice_states = True
        intents.webhooks = True
        intents.members = True

        super().__init__(intents=intents)
        self.synced = False
        self.scheduler = AsyncIOScheduler()

    async def on_ready(self):
        global tree
        if not self.synced:
            await tree.sync()
            self.synced = True
        print(f"Logged in as {self.user}")
        for command in tree.get_commands():
            print(f"Command loaded: {command.name}")

        # self.scheduler.add_job(self.daily_summary, CronTrigger(hour=0, minute=0, timezone="Europe/London"))
        self.scheduler.add_job(self.weekly_summary, CronTrigger(day_of_week="sun", hour=23, minute=59, timezone="Europe/London"))
        # self.scheduler.add_job(self.monthly_summary, CronTrigger(day=1, hour=0, minute=0, timezone="Europe/London"))
        self.scheduler.add_job(self.daily_summary, CronTrigger(minute="*"))
        # self.scheduler.add_job(self.weekly_summary, CronTrigger(minute="*"))
        self.scheduler.add_job(self.monthly_summary, CronTrigger(minute="*"))
        self.scheduler.start()

    async def on_interaction(self, interaction: Interaction):
        if (
            interaction.type == InteractionType.component
            and "custom_id" in interaction.data
        ):
            custom_id = interaction.data["custom_id"]
            if custom_id.startswith("role_"):
                await handleRoleButtonInteraction(interaction)

    async def on_message_delete(self, message):
        if message.author.bot:
            return

        async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=1):
            if entry.target.id == message.author.id and entry.extra.channel.id == message.channel.id:
                deleter = entry.user
                break
        else:
            deleter = None

        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            image_file_path = await create_message_image(message, "Deleted Message")

            channel_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"
            description = f"Message by {message.author.mention} ({message.author.id}) deleted in {message.channel.mention}."
            if deleter and deleter != message.author:
                description += f"\nDeleted by {deleter.mention} ({deleter.id})."
            
            embed = discord.Embed(
                title="Message Deleted",
                description=description,
                color=discord.Color.red()
            )
            embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
            embed.set_image(url="attachment://deleted_message.png")
            if image_file_path is not None:
                with open(image_file_path, "rb") as f:
                    await log_channel.send(file=discord.File(f, "deleted_message.png"), embed=embed)
                os.remove(image_file_path)
        
        update_summary_data("deleted_messages")

    async def on_message_edit(self, before, after):
        if before.author.bot:
            return

        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            image_file_path = await create_edited_message_image(before, after)

            message_link = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{after.id}"
            embed = discord.Embed(
                title="Message Edited",
                description=f"Message edited in {before.channel.mention} by {before.author.mention} ({before.author.id}).",
                color=discord.Color.orange()
            )
            embed.add_field(name="Message Link", value=f"[Click here]({message_link})")
            embed.set_image(url="attachment://edited_message.png")
            if image_file_path is not None:
                with open(image_file_path, "rb") as f:
                    await log_channel.send(file=discord.File(f, "edited_message.png"), embed=embed)
                os.remove(image_file_path)

    async def on_member_join(self, member):
        update_summary_data("members_joined")

    async def on_member_remove(self, member):
        update_summary_data("members_left")

    async def on_member_ban(self, guild, user):
        update_summary_data("members_banned")

    async def on_message(self, message):
        if message.author.bot:
            return

        if not await restrict_channel_for_new_members(message, POLITICS_CHANNEL_ID, 7, POLITICS_WHITELISTED_USER_IDS):
            return

        update_summary_data("messages", channel_id=message.channel.id)
        update_summary_data("active_members", user_id=message.author.id)

    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        update_summary_data("reactions_added")
        update_summary_data("reacting_members", user_id=user.id)

    async def on_reaction_remove(self, reaction, user):
        if user.bot:
            return
        update_summary_data("reactions_removed")
        update_summary_data("reacting_members", user_id=user.id, remove=True)

    async def daily_summary(self):
        await post_summary(self, COMMONS_CHANNEL_ID, "daily")

    async def weekly_summary(self):
        await post_summary(self, COMMONS_CHANNEL_ID, "weekly")

    async def monthly_summary(self):
        await post_summary(self, COMMONS_CHANNEL_ID, "monthly")

client = AClient()
tree = app_commands.CommandTree(client)

def has_role(interaction: Interaction, role_id: int) -> bool:
    return any(role.id == role_id for role in interaction.user.roles)

def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in interaction.user.roles)

@tree.command(
    name="role-manage",
    description="Manages user roles by assigning a specified role to members who don't have it",
)
async def role_management(interaction: Interaction, role_name: str):
    await updateRoleAssignments(interaction, role_name)

@tree.command(
    name="colour-palette", description="Generates a colour palette from an image"
)
async def colour_palette(interaction: Interaction, attachment_url: str):
    await colourPalette(interaction, attachment_url)

@tree.command(name="gridify", description="Adds a pixel art grid overlay to an image")
async def gridify_command(interaction: Interaction, attachment_url: str):
    await gridify(interaction, attachment_url)

@tree.command(name="role-react", description="Adds a reaction role to a message")
async def role_react_command(interaction: Interaction):
    if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    await persistantRoleButtons(interaction)

@tree.command(name="screenshot-canvas", description="Takes a screenshot of the current canvas")
async def screenshot_canvas(interaction: Interaction, x: Optional[int] = -770, y: Optional[int] = 7930):
    await screenshotCanvas(interaction, x, y)

@tree.command(name="user-activity", description="Gets user activity stats to find their most active hour")
async def user_activity_command(interaction: Interaction, month: str, user: Member, channel_name: str):
    await userActivity(interaction, month, user, channel_name)

@tree.command(name="add-to-iceberg", description="Adds text to the iceberg image")
async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
    if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    await add_iceberg_text(interaction, text, level)

@tree.command(name="show-iceberg", description="Shows the iceberg image")
async def show_iceberg_command(interaction: Interaction):
    await show_iceberg(interaction)

@tree.command(name="add-whitelist", description="Adds a user to the whitelist for the politics channel")
async def add_whitelist_command(interaction: Interaction, user: Member):
    if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return

    if user.id not in POLITICS_WHITELISTED_USER_IDS:
        POLITICS_WHITELISTED_USER_IDS.append(user.id)
        await interaction.response.send_message(f"{user.mention} has been added to the whitelist.", ephemeral=True)
    else:
        await interaction.response.send_message(f"{user.mention} is already in the whitelist.", ephemeral=True)