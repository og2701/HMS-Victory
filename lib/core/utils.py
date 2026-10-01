import discord
from discord import Interaction
import logging
import traceback
import io
import os
import pytz
import random
import re
from datetime import datetime, timedelta

from config import *
from lib.core.constants import CUSTOM_RANK_BACKGROUNDS, CHAT_LEVEL_ROLE_THRESHOLDS
from lib.core.image_processing import trim_image, encode_image_to_data_uri, screenshot_html, find_non_overlapping_position
from lib.core.file_operations import read_html_template, load_whitelist, save_whitelist, load_persistent_views, save_persistent_views, load_json_file, save_json_file, set_file_status, is_file_status_active
from lib.core.discord_helpers import restrict_channel_for_new_members, has_role, has_any_role, toggle_user_role, validate_and_format_date, send_embed_to_channels, edit_voice_channel_members, fetch_messages_with_context, estimate_tokens
from lib.economy.economy_manager import get_shutcoins, SHUTCOIN_ENABLED, get_bb
from database import DatabaseManager, get_user_badges

logger = logging.getLogger(__name__)

load_json = load_json_file
save_json = save_json_file


def get_twemoji_url(emoji_char: str) -> str:
    """Convert an emoji character to its corresponding Twemoji CDN URL."""
    # Handle both single emojis and sequences (like variation selectors or ZWJ)
    codepoints = []
    for char in emoji_char:
        cp = ord(char)
        # Skip variation selector-16 (fe0f) as Twemoji often omits it in the filename
        if cp == 0xFE0F:
            continue
        codepoints.append(f"{cp:x}")
    
    codepoint_str = "-".join(codepoints)
    return f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{codepoint_str}.png"


def is_lockdown_active():
    return is_file_status_active(VC_LOCKDOWN_FILE)


async def post_summary_helper(interaction: Interaction, summary_type: str):
    from lib.features.summary import post_summary
    uk_timezone = pytz.timezone("Europe/London")
    now = datetime.now(uk_timezone)
    if summary_type == "weekly":
        this_monday = now - timedelta(days=now.weekday())
        date_str = this_monday.strftime("%Y-%m-%d")
        summary_label = "weekly"
        message = f"Posted last week's summary using {date_str} (covers the Monday-Sunday prior)."
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
    await interaction.followup.send(message, ephemeral=True)



_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _safe_colour(value, default):
    """Only plain #RRGGBB colours reach the card's CSS."""
    return value if isinstance(value, str) and _HEX_COLOUR.match(value) else default


async def generate_rank_card(interaction: discord.Interaction, member: discord.Member) -> discord.File:
    from lib.features.rank_card import build_rank_card_html, tier_progress
    logger.info(f"Initiating rank card generation for {member.display_name} (ID: {member.id})")
    try:
        if not hasattr(interaction.client, "xp_system"):
            from lib.features.xp_system import XPSystem
            interaction.client.xp_system = XPSystem(interaction.client)
        xp_system = interaction.client.xp_system

        rank, current_xp = xp_system.get_rank(str(member.id), interaction.guild)
        current_xp = int(current_xp or 0)

        # Progress runs from the current peerage's threshold to the next one, so a member
        # who has just been promoted starts on an empty bar.
        tier = tier_progress(current_xp, CHAT_LEVEL_ROLE_THRESHOLDS)
        guild = interaction.guild

        def role_name(role_id):
            role = guild.get_role(role_id) if role_id and guild else None
            return role.name if role else None

        current_role_name = role_name(tier["current_id"])
        # Special override: "Duke" -> "Duchess" for specific users
        if tier["current_id"] == ROLES.DUKE and member.id in [USERS.CHIN, USERS.CHERRY_BLOSSOM]:
            current_role_name = "Duchess"

        shutcoins = None
        try:
            shutcoins = int(get_shutcoins(member.id))
        except Exception as e:
            logger.error(f"Error getting shutcoins: {e}")

        user_id_str = str(member.id)
        customization = DatabaseManager.fetch_one(
            "SELECT background, primary_color, secondary_color, tertiary_color, title FROM user_rank_customization WHERE user_id = ?",
            (user_id_str,)
        )
        bg_file = CUSTOM_RANK_BACKGROUNDS.get(user_id_str, "unionjack.png")
        primary_color, secondary_color, tertiary_color = '#CF142B', '#00247D', '#FFFFFF'
        title = ""
        if customization:
            res_bg, res_p, res_s, res_t, res_title = customization
            if res_bg and res_bg != 'unionjack.png':
                bg_file = res_bg
            primary_color = _safe_colour(res_p, primary_color)
            secondary_color = _safe_colour(res_s, secondary_color)
            tertiary_color = _safe_colour(res_t, tertiary_color)
            title = res_title or ""

        background_path = os.path.join(BASE_DIR, "data", "rank_cards", bg_file)
        if not os.path.exists(background_path):
            background_path = os.path.join(BASE_DIR, "data", "rank_cards", "unionjack.png")

        badges = []
        for b_id, b_name, b_desc, icon, awarded_at, rarity in get_user_badges(user_id_str) or []:
            icon_file_path = os.path.join(BASE_DIR, "data", "badges", icon)
            src = encode_image_to_data_uri(icon_file_path) if os.path.exists(icon_file_path) else get_twemoji_url(icon)
            badges.append({"src": src, "rarity": rarity, "name": b_name})

        card = {
            "username": member.display_name,
            "title": title,
            "rank_label": f"#{rank}" if rank is not None else "Unranked",
            "xp": current_xp,
            "current_role": current_role_name,
            "next_role": role_name(tier["next_id"]) if tier["next_id"] else None,
            "to_next": tier["to_next"],
            "progress": tier["progress"],
            "ukpence": int(get_bb(member.id) or 0),
            "shutcoins": shutcoins,
            "ukpence_icon": encode_image_to_data_uri(os.path.join(BASE_DIR, "data", "ukpence.png")),
            "shutcoin_icon": encode_image_to_data_uri(os.path.join(BASE_DIR, "data", "shutcoin.png")),
            "avatar_url": member.display_avatar.with_size(256).with_static_format("png").url,
            "background": encode_image_to_data_uri(background_path),
            "primary": primary_color,
            "secondary": secondary_color,
            "tertiary": tertiary_color,
            "badges": badges,
        }

        import time
        image_bytes = await screenshot_html(build_rank_card_html(card), size=(1000, 700), element_selector=".card")
        filename = f"rank_{int(time.time())}.png"
        return discord.File(fp=image_bytes, filename=filename)

    except Exception as e:
        logger.critical(f"An unrecoverable error occurred in generate_rank_card for {member.display_name}: {e}")
        logger.critical(traceback.format_exc())
        return None
