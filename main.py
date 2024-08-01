import discord
from discord import app_commands, Intents, Interaction, Client, InteractionType, Member
from typing import Optional
from lib.log_functions import *
import os
import json
from datetime import datetime, timedelta
from discord.ext import tasks
from collections import defaultdict

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

# Initialize the JSON data file
SUMMARY_DATA_FILE = "daily_summary.json"
if not os.path.exists(SUMMARY_DATA_FILE):
    with open(SUMMARY_DATA_FILE, "w") as file:
        json.dump({
            "members_joined": 0,
            "members_left": 0,
            "members_banned": 0,
            "messages": {},
            "reactions_added": 0,
            "reactions_removed": 0,
            "deleted_messages": 0,
            "boosters_gained": 0,
            "boosters_lost": 0,
            "active_members": defaultdict(int),
            "reacted_messages": defaultdict(int),
            "reacting_members": defaultdict(int)
        }, file)

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

    async def on_ready(self):
        global tree
        if not self.synced:
            await tree.sync()
            self.synced = True
        print(f"Logged in as {self.user}")
        for command in tree.get_commands():
            print(f"Command loaded: {command.name}")
        
        self.daily_summary.start()

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
        
        self.update_summary_data("deleted_messages")

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
        self.update_summary_data("members_joined")

    async def on_member_remove(self, member):
        self.update_summary_data("members_left")

    async def on_member_ban(self, guild, user):
        self.update_summary_data("members_banned")

    async def on_message(self, message):
        if message.author.bot:
            return
        self.update_summary_data("messages", channel_id=message.channel.id)
        self.update_summary_data("active_members", user_id=message.author.id)

    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        self.update_summary_data("reactions_added")
        self.update_summary_data("reacted_messages", message_id=reaction.message.id)
        self.update_summary_data("reacting_members", user_id=user.id)

    async def on_reaction_remove(self, reaction, user):
        if user.bot:
            return
        self.update_summary_data("reactions_removed")
        self.update_summary_data("reacted_messages", message_id=reaction.message.id, remove=True)
        self.update_summary_data("reacting_members", user_id=user.id, remove=True)

    def update_summary_data(self, key, channel_id=None, user_id=None, message_id=None, remove=False):
        with open(SUMMARY_DATA_FILE, "r") as file:
            data = json.load(file)

        if key == "messages" and channel_id:
            if str(channel_id) not in data["messages"]:
                data["messages"][str(channel_id)] = 0
            data["messages"][str(channel_id)] += 1
        elif key == "active_members" and user_id:
            if str(user_id) not in data["active_members"]:
                data["active_members"][str(user_id)] = 0
            data["active_members"][str(user_id)] += 1
        elif key == "reacted_messages" and message_id:
            if str(message_id) not in data["reacted_messages"]:
                data["reacted_messages"][str(message_id)] = 0
            data["reacted_messages"][str(message_id)] += 1 if not remove else -1
            if data["reacted_messages"][str(message_id)] <= 0:
                del data["reacted_messages"][str(message_id)]
        elif key == "reacting_members" and user_id:
            if str(user_id) not in data["reacting_members"]:
                data["reacting_members"][str(user_id)] = 0
            data["reacting_members"][str(user_id)] += 1 if not remove else -1
            if data["reacting_members"][str(user_id)] <= 0:
                del data["reacting_members"][str(user_id)]
        else:
            data[key] += 1

        with open(SUMMARY_DATA_FILE, "w") as file:
            json.dump(data, file)

    @tasks.loop(minutes=1)
    async def daily_summary(self):
        await self.post_daily_summary()
        self.reset_summary_data()

    async def post_daily_summary(self):
        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            with open(SUMMARY_DATA_FILE, "r") as file:
                data = json.load(file)

            guild = log_channel.guild
            total_members = guild.member_count
            active_members = sorted(data.get("active_members", {}).items(), key=lambda x: x[1], reverse=True)[:5]
            reacted_messages = sorted(data.get("reacted_messages", {}).items(), key=lambda x: x[1], reverse=True)[:5]
            reacting_members = sorted(data.get("reacting_members", {}).items(), key=lambda x: x[1], reverse=True)[:5]

            embed = discord.Embed(
                title="Daily Server Summary",
                description=f"Here is the summary for {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                color=discord.Color.blue()
            )
            embed.add_field(name="Total Members", value=total_members, inline=False)
            embed.add_field(name="Members Joined", value=data["members_joined"], inline=False)
            embed.add_field(name="Members Left", value=f"{data['members_left']} ({data['members_banned']} banned)", inline=False)
            embed.add_field(name="Reactions Added/Removed", value=f"{data['reactions_added']} / {data['reactions_removed']}", inline=False)
            embed.add_field(name="Deleted Messages", value=data["deleted_messages"], inline=False)
            embed.add_field(name="Boosters (New/Lost)", value=f"{data['boosters_gained']} / {data['boosters_lost']}", inline=False)
            
            top_channels = sorted(data.get("messages", {}).items(), key=lambda x: x[1], reverse=True)[:5]
            if top_channels:
                top_channels_str = "\n".join([f"<#{channel_id}>: {count} messages" for channel_id, count in top_channels])
                embed.add_field(name="Top 5 Active Channels", value=top_channels_str, inline=False)

            if active_members:
                top_members_str = "\n".join([f"<@{user_id}>: {count} messages" for user_id, count in active_members])
                embed.add_field(name="Top 5 Active Members", value=top_members_str, inline=False)
            
            if reacted_messages:
                top_reacted_messages_str = "\n".join([f"[Message](https://discord.com/channels/{log_channel.guild.id}/{log_channel.id}/{message_id}): {count} reactions" for message_id, count in reacted_messages])
                embed.add_field(name="Top 5 Most Reacted Messages", value=top_reacted_messages_str, inline=False)
            
            if reacting_members:
                top_reacting_members_str = "\n".join([f"<@{user_id}>: {count} reactions" for user_id, count in reacting_members])
                embed.add_field(name="Top 5 Reacting Members", value=top_reacting_members_str, inline=False)

            await log_channel.send(embed=embed)

    def reset_summary_data(self):
        with open(SUMMARY_DATA_FILE, "w") as file:
            json.dump({
                "members_joined": 0,
                "members_left": 0,
                "members_banned": 0,
                "messages": {},
                "reactions_added": 0,
                "reactions_removed": 0,
                "deleted_messages": 0,
                "boosters_gained": 0,
                "boosters_lost": 0,
                "active_members": defaultdict(int),
                "reacted_messages": defaultdict(int),
                "reacting_members": defaultdict(int)
            }, file)

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