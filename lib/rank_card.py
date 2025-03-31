import os
import io
import uuid
import discord
from discord import Interaction, Member
from PIL import Image
from html2image import Html2Image

from lib.rank_constants import *
from lib.shutcoin import get_shutcoins
from lib.settings import *
from lib.utils import *

hti = Html2Image()


async def generate_rank_card(interaction: Interaction, member: Member) -> discord.File:
    if not hasattr(interaction.client, "xp_system"):
        from lib.xp_system import XPSystem
        interaction.client.xp_system = XPSystem()

    xp_system = interaction.client.xp_system
    rank, current_xp = xp_system.get_rank(str(member.id))
    rank_display = f"#{rank}" if rank is not None else "Unranked"
    if current_xp is None:
        current_xp = 0

    current_role_id = None
    next_role_id = None
    next_threshold = None
    for threshold, role_id in CHAT_LEVEL_ROLE_THRESHOLDS:
        if current_xp >= threshold:
            current_role_id = role_id
        else:
            next_role_id = role_id
            next_threshold = threshold
            break

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

    current_role_name = "None"
    if current_role_id:
        current_role = interaction.guild.get_role(current_role_id)
        if current_role:
            current_role_name = current_role.name

    template_path = os.path.join("templates", "rank_card.html")
    html_content = read_html_template(template_path)
    html_content = html_content.replace("{profile_pic}", str(member.display_avatar.url))
    html_content = html_content.replace("{username}", member.display_name)
    html_content = html_content.replace("{rank}", rank_display)
    html_content = html_content.replace("{xp_display}", xp_display)
    html_content = html_content.replace("{progress_percent}", f"{progress_percent}%")
    html_content = html_content.replace("{current_role}", current_role_name)
    html_content = html_content.replace("{next_role_html}", next_role_html)

    shutcoin_html = ""
    if SHUTCOIN_ENABLED:
        shutcoin_count = get_shutcoins(member.id)
        shutcoin_html = f'<p class="text-xs text-gray-300 xp-box"><span class="xp-text">Shutcoins: {shutcoin_count}</span></p>'
    html_content = html_content.replace("{shutcoin_html}", shutcoin_html)

    user_id_str = str(member.id)
    custom_bg_filename = CUSTOM_RANK_BACKGROUNDS.get(user_id_str, "unionjack.png")
    background_path = os.path.join("data", "rank_cards", custom_bg_filename)
    background_data_uri = encode_image_to_data_uri(background_path)
    html_content = html_content.replace("{unionjack}", background_data_uri)

    size = (1600, 1000)
    output_file = f"{uuid.uuid4()}.png"
    try:
        hti.screenshot(html_str=html_content, save_as=output_file, size=size)
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
