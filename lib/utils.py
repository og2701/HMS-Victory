from datetime import datetime, timezone
import discord
from discord import app_commands, Interaction, Member, TextChannel
from discord import Interaction
import json
import os

PERSISTENT_VIEWS_FILE = "persistent_views.json"
WHITELIST_FILE = "whitelist.json"

async def restrict_channel_for_new_members(
    message: discord.Message,
    channel_id: int,
    days_required: int = 7,
    whitelisted_user_ids: list[int] = [],
):
    """Restricts channel access to members who have been in the server for a specified duration"""
    if message.channel.id != channel_id:
        return True

    if message.author.id in whitelisted_user_ids:
        return True

    join_date = message.author.joined_at
    if join_date is None or (datetime.now(timezone.utc) - join_date).days < days_required:
        await message.delete()
        await message.channel.send(
            f"{message.author.mention}, you need to be in the server for at least {days_required} days to use this channel. "
            f"If you believe you should be whitelisted, please <#1143560594138595439>",
            delete_after=10,
        )
        return False

    return True


def has_role(interaction: Interaction, role_id: int) -> bool:
    """Checks if a user has a specific role"""
    return any(role.id == role_id for role in interaction.user.roles)


def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    """Checks if a user has any of the specified roles"""
    return any(role.id in role_ids for role in interaction.user.roles)


def save_data(data: dict | list, filename: str):
    """Saves data to a JSON file"""
    with open(filename, "w") as f:
        json.dump(data, f)


def load_data(filename: str) -> dict | list:
    """Loads data from a JSON file. Returns an empty dict if the file doesn't exist"""
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_whitelist(whitelist: list[int]):
    """Saves the whitelist to a JSON file"""
    save_data(whitelist, WHITELIST_FILE)



def load_whitelist() -> list[int]:
    """Loads the whitelist from a JSON file"""
    data = load_data(WHITELIST_FILE)
    return data if isinstance(data, list) else []


def load_persistent_views() -> dict:
    """Loads persistent view data from a JSON file"""
    return load_data(PERSISTENT_VIEWS_FILE)


def save_persistent_views(data: dict):
    """Saves persistent view data to a JSON file"""
    save_data(data, PERSISTENT_VIEWS_FILE)

async def toggle_user_role(interaction: Interaction, user: Member, role: discord.Role):
    """Toggles a given role for a user"""
    if role in user.roles:
        await user.remove_roles(role)
        await interaction.response.send_message(
            f"Role {role.name} has been removed from {user.mention}.", ephemeral=True
        )
    else:
        await user.add_roles(role)
        await interaction.response.send_message(
            f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True
        )

async def validate_and_format_date(interaction: Interaction, date_str: str = None) -> str | None:
    """Validates and formats the date string for summary commands"""
    uk_timezone = pytz.timezone("Europe/London")
    if date_str is None:
        return datetime.now(uk_timezone).strftime("%Y-%m-%d")
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message(
            "Invalid date format. Please use YYYY-MM-DD.", ephemeral=True
        )
        return None

async def post_summary_helper(interaction: Interaction, summary_type: str):
    """Helper function to post weekly/monthly summaries based on type"""
    uk_timezone = pytz.timezone("Europe/London")
    now = datetime.now(uk_timezone)
    if summary_type == "weekly":
        weekday = now.weekday()
        this_monday = now - timedelta(days=weekday)
        date_str = this_monday.strftime("%Y-%m-%d")
        message = f"Posted last week's summary using {date_str} (covers the Monday-Sunday prior)."
    elif summary_type == "monthly":
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_str = this_month_start.strftime("%Y-%m-%d")
        message = f"Posted last month's monthly summary ({date_str})."
    else:
        return

    await post_summary(interaction.client, interaction.channel.id, summary_type, interaction.channel, date_str)
    await interaction.response.send_message(message, ephemeral=True)


async def handle_roast_command(interaction: Interaction, channel: TextChannel, user: Member):
    """Handles the roast command, including usage limits"""
    today = datetime.now().date()
    usage_data = command_usage_tracker[interaction.user.id]

    if interaction.user.id == USERS.OGGERS:
        await roast(interaction, channel, user)
        return

    if usage_data["last_used"] != today:
        usage_data["count"] = 0
        usage_data["last_used"] = today

    if usage_data["count"] >= SUMMARISE_DAILY_LIMIT:
        await interaction.response.send_message(
            f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command",
            ephemeral=True,
        )
        return

    usage_data["count"] += 1
    await roast(interaction, channel, user)