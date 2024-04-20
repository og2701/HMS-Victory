from discord import Embed

async def placeholderCommandName(interaction, role_name: str):
    """
    What the command does.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        parameter (str): The purpose of this parameter

    Returns:
        None
    """

    initial_embed = Embed(
        title="Placeholder", 
        description="Placeholder", 
        color=0xFFA500
    )

    
    await interaction.response.send_message(embed=initial_embed)