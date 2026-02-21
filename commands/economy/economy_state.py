import discord
from discord import Interaction
import logging
logger = logging.getLogger(__name__) 

from lib.economy.economy_stats_html import create_economy_stats_image

async def handle_ukpeconomy_command(interaction: discord.Interaction) -> discord.File | None:
    try:
        image_buffer = await create_economy_stats_image(interaction.guild, interaction.client)
        if image_buffer is not None:
            return discord.File(image_buffer, filename="ukpeconomy_stats.png")
        else:
            logger.error("Economy stats image path not found or does not exist.")
            return None
    except Exception as e:
        logger.error(f"Error in handle_ukpeconomy_command: {e}", exc_info=True)
        return None
