import discord
from discord import Interaction
import os
import logging
logger = logging.getLogger(__name__) 

from lib.economy_stats_html import create_economy_stats_image

async def handle_ukpeconomy_command(interaction: Interaction):
    await interaction.response.defer(ephemeral=False) 
    try:
        image_path = await create_economy_stats_image(interaction.guild)
        if image_path and os.path.exists(image_path):
            with open(image_path, "rb") as f:
                discord_file = discord.File(f, filename="ukpeconomy_stats.png")
                await interaction.followup.send(file=discord_file)
            os.remove(image_path)
        else:
            await interaction.followup.send("Sorry, couldn't generate the economy stats image.", ephemeral=True)
    except Exception as e:
        logger.error(f"Error in handle_ukpeconomy_command during /ukpeconomy: {e}", exc_info=True)
        await interaction.followup.send("An error occurred while generating economy stats. Please check bot logs for details.", ephemeral=True)