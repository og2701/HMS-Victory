import discord
from discord import Interaction
import logging
logger = logging.getLogger(__name__) 

from lib.economy.economy_stats_html import create_economy_stats_image

async def handle_ukpeconomy_command(interaction: discord.Interaction) -> discord.File | None:
    try:
        logger.info("[ECON DEBUG] handle_ukpeconomy_command called")
        image_buffer = await create_economy_stats_image(interaction.guild, interaction.client)
        if image_buffer is not None:
            buf_size = image_buffer.getbuffer().nbytes
            logger.info(f"[ECON DEBUG] Image buffer ready, size: {buf_size} bytes")
            if buf_size == 0:
                logger.error("[ECON DEBUG] Image buffer is EMPTY (0 bytes)!")
                return None
            return discord.File(image_buffer, filename="ukpeconomy_stats.png")
        else:
            logger.error("[ECON DEBUG] Economy stats image returned None")
            return None
    except Exception as e:
        logger.error(f"[ECON DEBUG] Error in handle_ukpeconomy_command: {e}", exc_info=True)
        return None
