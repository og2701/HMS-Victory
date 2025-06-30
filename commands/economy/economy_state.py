import discord
from discord import Interaction
import os
import logging
logger = logging.getLogger(__name__) 

from lib.economy_stats_html import create_economy_stats_image

async def handle_ukpeconomy_command(interaction: discord.Interaction) -> discord.File | None:
    try:
        image_path = await create_economy_stats_image(interaction.guild)
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                discord_file = discord.File(f, filename="ukpeconomy_stats.png")
            # os.remove(image_path)
            return discord_file
        else:
            logger.error("Economy stats image path not found or does not exist.")
            return None
    except Exception as e:
        logger.error(f"Error in handle_ukpeconomy_command: {e}", exc_info=True)
        return None