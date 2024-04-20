from discord import Embed

"""
What the command does.

Args:
    interaction (discord.Interaction): The interaction that triggered the command.
    parameter (str): The purpose of this parameter

Returns:
    None
"""
async def placeholderCommandName(interaction, role_name: str):
    initial_embed = Embed(
        title=f"Placeholder", 
        description=f"Placeholder", 
        color=0xFFA500
    )

    
    await interaction.response.send_message(embed=initial_embed)