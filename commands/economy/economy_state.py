import discord
from discord import Interaction
import logging
logger = logging.getLogger(__name__) 

from lib.economy.economy_stats_html import create_economy_stats_image

async def handle_ukpeconomy_command(interaction: discord.Interaction) -> discord.File | None:
    try:
        image_buffer = await create_economy_stats_image(interaction.guild, interaction.client)
        if image_buffer is not None:
            if image_buffer.getbuffer().nbytes == 0:
                logger.error("Economy stats image buffer is empty")
                return None
            return discord.File(image_buffer, filename="ukpeconomy_stats.png")
        else:
            return None
    except Exception as e:
        logger.error(f"Error in handle_ukpeconomy_command: {e}", exc_info=True)
        return None
