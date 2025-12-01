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

logger = logging.getLogger(__name__)

load_json = load_json_file
save_json = save_json_file


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
            interaction.client.xp_system = XPSystem()
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
            next_role_html = ""
        else:
            progress_percent = (current_xp / next_threshold) * 100 if next_threshold > 0 else 100
            xp_display = f"{current_xp} / {next_threshold}"
            next_role = interaction.guild.get_role(next_role_id) if next_role_id else None
            next_role_name = next_role.name if next_role else "Max"
            next_role_html = f'<div class="role-label next-role">{next_role_name}</div>'
        logger.info(f"Progress calculated: {progress_percent:.2f}%")

        current_role_name = "None"
        if current_role_id:
            current_role = interaction.guild.get_role(current_role_id)
            if current_role:
                current_role_name = current_role.name
        logger.info(f"Current role set to: {current_role_name}")

        template_path = os.path.join("templates", "rank_card.html")
        logger.debug(f"Reading template from {template_path}")
        html_content = read_html_template(template_path)

        html_content = html_content.replace("{profile_pic}", str(member.display_avatar.url))
        html_content = html_content.replace("{username}", member.display_name)
        html_content = html_content.replace("{rank}", rank_display)
        html_content = html_content.replace("{xp_display}", xp_display)
        html_content = html_content.replace("{progress_percent}", f"{progress_percent}%")
        html_content = html_content.replace("{current_role}", current_role_name)
        html_content = html_content.replace("{next_role_html}", next_role_html)
        logger.debug("Main HTML content has been populated.")

        shutcoin_html = ""
        if SHUTCOIN_ENABLED:
            shutcoin_count = get_shutcoins(member.id)
            shutcoin_icon_path = os.path.join("data", "shutcoin.png")
            shutcoin_icon_uri = encode_image_to_data_uri(shutcoin_icon_path)
            shutcoin_html = f'<div class="coin-box"><img src="{shutcoin_icon_uri}" class="coin-icon" /><span class="xp-text">{shutcoin_count:,}</span></div>'
            logger.debug(f"Shutcoin HTML populated with count: {shutcoin_count}")

        britbuck_amount = get_bb(member.id)
        britbuck_icon_path = os.path.join("data", "ukpence.png")
        britbuck_icon_uri = encode_image_to_data_uri(britbuck_icon_path)
        britbuck_html = f'<div class="coin-box"><img src="{britbuck_icon_uri}" class="coin-icon" /><span class="xp-text">{britbuck_amount:,}</span></div>'
        logger.debug(f"UKPence HTML populated with amount: {britbuck_amount}")

        html_content = html_content.replace("{shutcoin_html}", shutcoin_html)
        html_content = html_content.replace("{britbuck_html}", britbuck_html)

        user_id_str = str(member.id)
        custom_bg_filename = CUSTOM_RANK_BACKGROUNDS.get(user_id_str, "unionjack.png")
        background_path = os.path.join("data", "rank_cards", custom_bg_filename)
        logger.info(f"Using background image: {custom_bg_filename}")
        background_data_uri = encode_image_to_data_uri(background_path)
        html_content = html_content.replace("{unionjack}", background_data_uri)

        size = (1600, 1000)
        image_bytes = screenshot_html(html_content, size)
        return discord.File(fp=image_bytes, filename="rank.png")

    except Exception as e:
        logger.critical(f"An unrecoverable error occurred in generate_rank_card for {member.display_name}: {e}")
        logger.critical(traceback.format_exc())
        return None
