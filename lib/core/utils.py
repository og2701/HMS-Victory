import discord
from discord import Interaction
import logging
import traceback
import io
import os
import pytz
import random
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
    await interaction.followup.send(message, ephemeral=True)



async def generate_rank_card(interaction: discord.Interaction, member: discord.Member) -> discord.File:
    logger.info(f"Initiating rank card generation for {member.display_name} (ID: {member.id})")
    try:
        if not hasattr(interaction.client, "xp_system"):
            logger.warning("XPSystem not found on client. Initializing now.")
            from lib.features.xp_system import XPSystem
            interaction.client.xp_system = XPSystem(interaction.client)
        xp_system = interaction.client.xp_system
        logger.debug("XPSystem has been accessed.")

        rank, current_xp = xp_system.get_rank(str(member.id))
        rank_display = f"#{rank}" if rank is not None else "Unranked"
        if current_xp is None:
            current_xp = 0
            logger.warning(f"No XP data for {member.id}, defaulting to 0.")
        logger.info(f"Data for {member.display_name}: Rank={rank_display}, XP={current_xp}")

        current_role_id, next_role_id, next_threshold = None, None, None
        for threshold, role_id in CHAT_LEVEL_ROLE_THRESHOLDS:
            if current_xp >= threshold:
                current_role_id = role_id
            else:
                next_role_id = role_id
                next_threshold = threshold
                break
        logger.debug(f"Role state: current_role_id={current_role_id}, next_role_id={next_role_id}, next_threshold={next_threshold}")

        if next_threshold is None:
            progress_percent = 100
            xp_display = str(current_xp)
            next_role_name = "MAX"
        else:
            progress_percent = (current_xp / next_threshold) * 100 if next_threshold > 0 else 100
            xp_display = f"{current_xp} / {next_threshold}"
            next_role = interaction.guild.get_role(next_role_id) if next_role_id else None
            next_role_name = next_role.name if next_role else "Max"
        logger.info(f"Progress calculated: {progress_percent:.2f}%")

        current_role_name = "None"
        if current_role_id:
            current_role = interaction.guild.get_role(current_role_id)
            if current_role:
                current_role_name = current_role.name
        logger.info(f"Current role set to: {current_role_name}")

        from config import BASE_DIR
        template_path = os.path.join(BASE_DIR, "templates", "rank_card.html")
        logger.debug(f"Reading template from {template_path}")
        html_content = read_html_template(template_path)

        import re
        def safe_replace(content, key, value):
            # Matches {key}, { key }, or { \n key \n }
            pattern = r'\{\s*' + re.escape(key) + r'\s*\}'
            return re.sub(pattern, str(value), content, flags=re.MULTILINE)

        shutcoin_html = ""
        if True: # Force enable for display, or use config explicitly
            try:
                from lib.economy.economy_manager import get_shutcoins
                shutcoin_count = get_shutcoins(member.id)
                shutcoin_icon_path = os.path.join(BASE_DIR, "data", "shutcoin.png")
                shutcoin_icon_uri = encode_image_to_data_uri(shutcoin_icon_path)
                shutcoin_html = f'<div class="coin-box"><img src="{shutcoin_icon_uri}" class="coin-icon" /><span class="xp-text">{shutcoin_count:,}</span></div>'
            except Exception as e:
                logger.error(f"Error getting shutcoins: {e}")

        britbuck_amount = get_bb(member.id)
        britbuck_icon_path = os.path.join(BASE_DIR, "data", "ukpence.png")
        britbuck_icon_uri = encode_image_to_data_uri(britbuck_icon_path)
        britbuck_html = f'<div class="coin-box"><img src="{britbuck_icon_uri}" class="coin-icon" /><span class="xp-text">{britbuck_amount:,}</span></div>'

        user_id_str = str(member.id)
        customization = DatabaseManager.fetch_one(
            "SELECT background, primary_color, secondary_color, tertiary_color FROM user_rank_customization WHERE user_id = ?",
            (user_id_str,)
        )
        
        bg_file = CUSTOM_RANK_BACKGROUNDS.get(user_id_str, "unionjack.png")
        primary_color, secondary_color, tertiary_color = '#CF142B', '#00247D', '#FFFFFF'
        
        if customization:
            res_bg, res_p, res_s, res_t = customization
            if res_bg: bg_file = res_bg
            if res_p: primary_color = res_p
            if res_s: secondary_color = res_s
            if res_t: tertiary_color = res_t

        background_path = os.path.join(BASE_DIR, "data", "rank_cards", bg_file)
        if not os.path.exists(background_path):
            bg_file = "unionjack.png"
            background_path = os.path.join(BASE_DIR, "data", "rank_cards", bg_file)
        background_data_uri = encode_image_to_data_uri(background_path)

        # Add badges
        badges_html = ""
        user_badges = get_user_badges(user_id_str)
        if user_badges:
            # Sort by rarity: Gold (0), Silver (1), Bronze (2)
            rarity_map = {"Gold": 0, "Silver": 1, "Bronze": 2}
            user_badges.sort(key=lambda x: rarity_map.get(x[5], 3))
            
            for badge in user_badges:
                b_id, b_name, b_desc, icon, awarded_at, rarity = badge
                rarity_class = f"rarity-{rarity.lower()}"
                
                # Check if it's a file path or a raw emoji
                icon_file_path = os.path.join(BASE_DIR, "data", "badges", icon)
                if os.path.exists(icon_file_path):
                    data_uri = encode_image_to_data_uri(icon_file_path)
                    badges_html += f'<div class="badge-item {rarity_class}"><img src="{data_uri}" alt="{b_name}"></div>'
                else:
                    # Assume it's a raw emoji
                    twemoji_url = get_twemoji_url(icon)
                    badges_html += f'<div class="badge-item emoji {rarity_class}"><img src="{twemoji_url}" alt="{b_name}"></div>'
        
        # Apply replacements
        html_content = safe_replace(html_content, "profile_pic", member.display_avatar.url)
        html_content = safe_replace(html_content, "username", member.display_name)
        html_content = safe_replace(html_content, "rank", rank)
        html_content = safe_replace(html_content, "xp_display", xp_display)
        html_content = safe_replace(html_content, "progress_percent", f"{progress_percent}%")
        html_content = safe_replace(html_content, "current_role", current_role_name)
        html_content = safe_replace(html_content, "next_role_name", next_role_name)
        html_content = safe_replace(html_content, "shutcoin_html", shutcoin_html)
        html_content = safe_replace(html_content, "britbuck_html", britbuck_html)
        html_content = safe_replace(html_content, "bg_image", background_data_uri)
        html_content = safe_replace(html_content, "primary_color", primary_color)
        html_content = safe_replace(html_content, "secondary_color", secondary_color)
        html_content = safe_replace(html_content, "tertiary_color", tertiary_color)
        html_content = safe_replace(html_content, "badges_html", badges_html)

        import time
        size = (1400, 1000)
        image_bytes = await screenshot_html(html_content, size)
        filename = f"rank_{int(time.time())}.png"
        return discord.File(fp=image_bytes, filename=filename)

    except Exception as e:
        logger.critical(f"An unrecoverable error occurred in generate_rank_card for {member.display_name}: {e}")
        logger.critical(traceback.format_exc())
        return None
