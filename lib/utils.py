from datetime import datetime, timezone, timedelta
import discord
from discord import Interaction, Member, TextChannel
import json
import os
from PIL import Image, ImageChops
import pytz
import random
import io
import tempfile
import time
import uuid
import logging
import base64
from html2image import Html2Image

from config import CHROME_PATH

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)

logger = logging.getLogger(__name__)

CHAT_LEVEL_ROLE_THRESHOLDS = [
    (1000, 1226311204281122926),   # SERF
    (2500, 1228860092200386571),   # PEASANT
    (5000, 1226311235537080340),   # FREEMAN
    (10000, 1195060173346177065),   # COMMONER
    (15000, 1226311471269675018),   # YEOMAN
    (20000, 1195060260956807280),   # GENTLEMAN
    (25000, 1226312063941607525),   # ESQUIRE
    (50000, 1226312237766021216),   # KNIGHT
    (70000, 1226312266430021674),   # BARON
    (100000, 1226309228315017226),  # VISCOUNT
    (150000, 1226304907750150264),  # EARL
    (200000, 1226304808257065155),  # MARQUESS
    (250000, 1226304695086219345),  # DUKE
]



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
        if (join_date is None) or ((datetime.now(timezone.utc) - join_date).days < days_required):
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

def load_json(filename):
    """Loads JSON data from a file"""
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}


def save_json(filename, data):
    """Saves JSON data to a file"""
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)


def is_lockdown_active():
    """Checks whether a lockdown is active"""
    return os.path.exists(VC_LOCKDOWN_FILE)

async def toggle_user_role(interaction: Interaction, user: Member, role):
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

async def validate_and_format_date(interaction: Interaction, date_str: str = None):
    """Validates and formats the date string for summary commands"""
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
    """Handles the roast command, including usage limits"""
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
        await interaction.response.send_message(
            f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command", ephemeral=True
        )
        return
    usage_data["count"] += 1
    await roast(interaction, channel, user)

async def post_summary_helper(interaction: Interaction, summary_type: str):
    """Helper function to post weekly/monthly summaries based on type"""
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

def set_file_status(file_path: str, active: bool):
    """Creates or removes a file to represent a toggle status"""
    if active:
        open(file_path, "w").close()
    else:
        if os.path.exists(file_path):
            os.remove(file_path)

async def send_embed_to_channels(guild: discord.Guild, embed: discord.Embed, channel_ids: list[int]):
    """Sends an embed to each channel (if exists) from the provided list of channel IDs"""
    for cid in channel_ids:
        channel = guild.get_channel(cid)
        if channel:
            await channel.send(embed=embed)

async def edit_voice_channel_members(guild: discord.Guild, mute: bool, deafen: bool, whitelist: list[int] = None):
    """Edits voice channel members: if a whitelist is provided, only members NOT having any whitelisted role are edited"""
    for channel in guild.voice_channels:
        for member in channel.members:
            if whitelist:
                if any(role.id in whitelist for role in member.roles):
                    continue
            await member.edit(mute=mute, deafen=deafen)

def random_color_excluding_blue_and_dark():
    """Generates a random RGB colour excluding overly blue or dark colours"""
    while True:
        r = random.randint(100, 255)
        g = random.randint(100, 255)
        b = random.randint(0, 100)
        if r > 100 or g > 100:
            return (r, g, b)

def get_text_position(font, text, bounds, existing_positions, max_attempts=100):
    """Attempts to find a non-overlapping position within the given bounds for the text"""
    text_bbox = font.getbbox(text)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    if text_width > (bounds[1][0] - bounds[0][0]) or text_height > (bounds[1][1] - bounds[0][1]):
        raise ValueError("Text is too large to fit within the bounds")
    for _ in range(max_attempts):
        x = random.randint(bounds[0][0], bounds[1][0] - text_width)
        y = random.randint(bounds[0][1], bounds[1][1] - text_height)
        new_position = (x, y, x + text_width, y + text_height)
        if not any(
            pos[0] < new_position[2] and pos[2] > new_position[0] and pos[1] < new_position[3] and pos[3] > new_position[1]
            for pos in existing_positions
        ):
            return (x, y)
    return None

async def fetch_messages_with_context(channel, user, user_messages, total_limit=100, context_depth=2):
    """Fetches messages from a channel, grouping a user's messages along with a few preceding messages as context"""
    try:
        user_message_count = 0
        message_history = []
        async for message in channel.history(limit=None, after=datetime.utcnow() - timedelta(days=7), oldest_first=True):
            if message.author.bot:
                continue
            message_history.append(message)
            if message.author == user:
                user_message_count += 1
                if user_message_count >= total_limit:
                    break
        i = 0
        while i < len(message_history):
            message = message_history[i]
            if message.author == user:
                context = []
                context_count = 0
                j = i - 1
                while context_count < context_depth and j >= 0:
                    if (not message_history[j].author.bot) and (message_history[j].author != user):
                        context.append(message_history[j])
                        context_count += 1
                    j -= 1
                context.reverse()
                user_message_block = []
                while i < len(message_history) and message_history[i].author == user:
                    user_message_block.append(
                        f"{message_history[i].created_at.strftime('%Y-%m-%d %H:%M:%S')} - {user.display_name}: {message_history[i].content}"
                    )
                    i += 1
                user_message_block_text = "\n".join(user_message_block)
                if context:
                    context_text = "\n".join(
                        [f"{m.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {m.author.display_name}: {m.content}" for m in context]
                    )
                    user_messages.append(f"Context:\n{context_text}\n{user_message_block_text}")
                else:
                    user_messages.append(user_message_block_text)
            else:
                i += 1
    except discord.Forbidden:
        pass

def estimate_tokens(text):
    """Estimates token count by splitting text by whitespace"""
    return len(text.split())

def trim(im):
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
    diff = ImageChops.difference(im, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    if bbox:
        return im.crop(bbox)
    return im

def read_html_template(file_path):
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()

def encode_image_to_data_uri(image_path):
    with open(image_path, "rb") as img_file:
        data = img_file.read()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/png;base64,{encoded}"

def calculate_estimated_height(content, line_height=20, base_height=100):
    message_lines = content.split("\n")
    total_lines = sum(len(line) // 80 + 1 for line in message_lines)
    content_height = line_height * total_lines
    estimated_height = max(base_height, content_height + 100)
    return estimated_height

async def generate_rank_card(interaction: Interaction, member: Member) -> discord.File:
    if not hasattr(interaction.client, "xp_system"):
        from xp_system import XPSystem
        interaction.client.xp_system = XPSystem()
    xp_system = interaction.client.xp_system
    rank, current_xp = xp_system.get_rank(str(member.id))
    rank_display = f"#{rank}" if rank is not None else "Unranked"
    if current_xp is None:
        current_xp = 0
    next_threshold = None
    for threshold, _ in CHAT_LEVEL_ROLE_THRESHOLDS:
        if current_xp < threshold:
            next_threshold = threshold
            break
    if next_threshold is None:
        next_threshold = current_xp
    progress_percent = (current_xp / next_threshold) * 100 if next_threshold > 0 else 100
    template_path = os.path.join("templates", "rank_card.html")
    html_content = read_html_template(template_path)
    html_content = html_content.replace("{profile_pic}", str(member.display_avatar.url))
    html_content = html_content.replace("{username}", member.display_name)
    html_content = html_content.replace("{rank}", rank_display)
    html_content = html_content.replace("{current_xp}", str(current_xp))
    html_content = html_content.replace("{next_threshold}", str(next_threshold))
    html_content = html_content.replace("{progress_percent}", f"{progress_percent}%")
    unionjack_path = os.path.join("data", "unionjack.png")
    unionjack_data_uri = encode_image_to_data_uri(unionjack_path)
    html_content = html_content.replace("{unionjack}", unionjack_data_uri)
    size = (1600, 1000)
    output_file = f"{uuid.uuid4()}.png"
    try:
        hti.screenshot(html_str=html_content, save_as=output_file, size=size, options={'zoom': 2})
    except Exception as e:
        raise Exception(f"Error taking screenshot: {e}")
    try:
        image = Image.open(output_file)
        image = trim(image)
        image.save(output_file)
    except Exception as e:
        raise Exception(f"Error processing image: {e}")
    with open(output_file, "rb") as f:
        image_bytes = io.BytesIO(f.read())
    os.remove(output_file)
    return discord.File(fp=image_bytes, filename="rank.png")