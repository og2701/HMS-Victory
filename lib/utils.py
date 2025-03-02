from datetime import datetime, timezone, timedelta
import discord
from discord import Interaction, Member, TextChannel
import json
import os
import pytz

PERSISTENT_VIEWS_FILE = "persistent_views.json"


async def restrict_channel_for_new_members(
    message: discord.Message,
    channel_id: int,
    days_required: int = 7,
    whitelisted_user_ids: list[int] = [],
):
    if message.channel.id == channel_id:
        if message.author.id in whitelisted_user_ids:
            return True
        join_date = message.author.joined_at
        if (
            join_date is None
            or (datetime.now(timezone.utc) - join_date).days < days_required
        ):
            await message.delete()
            await message.channel.send(
                f"{message.author.mention}, you need to be in the server for at least {days_required} days to use this channel. If you believe you should be whitelisted, please <#1143560594138595439>",
                delete_after=10,
            )
            return False
    return True


def has_role(interaction: Interaction, role_id: int) -> bool:
    return any(role.id == role_id for role in interaction.user.roles)


def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in interaction.user.roles)


def save_whitelist(whitelist):
    with open("whitelist.json", "w") as f:
        json.dump(whitelist, f)


def load_whitelist():
    try:
        with open("whitelist.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def load_persistent_views():
    try:
        with open(PERSISTENT_VIEWS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_persistent_views(data):
    with open(PERSISTENT_VIEWS_FILE, "w") as f:
        json.dump(data, f)

async def toggle_user_role(interaction: Interaction, user: Member, role):
    """Toggles a given role for a user."""
    if role in user.roles:
        await user.remove_roles(role)
        await interaction.response.send_message(f"Role {role.name} has been removed from {user.mention}.", ephemeral=True)
    else:
        await user.add_roles(role)
        await interaction.response.send_message(f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True)

async def validate_and_format_date(interaction: Interaction, date_str: str = None):
    """Validates and formats the date string for summary commands."""
    if date_str is None:
        uk_timezone = pytz.timezone("Europe/London")
        return datetime.now(uk_timezone).strftime("%Y-%m-%d")
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD.", ephemeral=True)
        return None

async def handle_roast_command(interaction: Interaction, channel: TextChannel, user: Member):
    """Handles the roast command, including usage limits."""
    from lib.commands import roast
    from lib.settings import USERS, SUMMARISE_DAILY_LIMIT, command_usage_tracker
    today = datetime.now().date()
    usage_data = command_usage_tracker[interaction.user.id]
    if interaction.user.id == USERS.OGGERS:
        await roast(interaction, channel, user)
        return
    if usage_data["last_used"] != today:
        usage_data["count"] = 0
        usage_data["last_used"] = today
    if usage_data["count"] >= SUMMARISE_DAILY_LIMIT:
        await interaction.response.send_message(f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command", ephemeral=True)
        return
    usage_data["count"] += 1
    await roast(interaction, channel, user)

async def post_summary_helper(interaction: Interaction, summary_type: str):
    """Helper function to post weekly/monthly summaries based on type."""
    from lib.summary import post_summary
    uk_timezone = pytz.timezone("Europe/London")
    now = datetime.now(uk_timezone)
    if summary_type == "weekly":
        this_monday = now - timedelta(days=now.weekday())
        date_str = this_monday.strftime("%Y-%m-%d")
        summary_label = "weekly"
        message = f"Posted last week's summary using {date_str} (covers the Monday–Sunday prior)."
    elif summary_type == "monthly":
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_str = this_month_start.strftime("%Y-%m-%d")
        summary_label = "monthly"
        message = f"Posted last month's monthly summary ({date_str})."
    else:
        await interaction.response.send_message("Invalid summary type.", ephemeral=True)
        return
    client = interaction.client
    await post_summary(client, interaction.channel.id, summary_label, interaction.channel, date_str)
    await interaction.response.send_message(message, ephemeral=True)
