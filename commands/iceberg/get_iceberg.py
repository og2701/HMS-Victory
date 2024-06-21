import os
from discord import File

UPDATED_IMAGE_PATH = "data/updated_iceberg.png"

async def show_iceberg(interaction):
    """
    Shows the current state of the iceberg image.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """

    if not os.path.exists(UPDATED_IMAGE_PATH):
        await interaction.response.send_message("The iceberg image has not been created yet.", ephemeral=True)
        return

    file = File(UPDATED_IMAGE_PATH, filename="current_iceberg.png")
    await interaction.response.send_message(file=file)
