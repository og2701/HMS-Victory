from discord import Embed
from discord.ext import commands
from lib.settings import *

async def toggleMuteDeafenPermissions(interaction, member):
    """
    This command toggles the specified user's role to grant or remove mute/deafen permissions.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        member (discord.Member): The user whose role will be toggled.

    Returns:
        None
    """

    role_id = ROLES.VOICE_CHAT_WARDEN
    role = interaction.guild.get_role(role_id)

    if role is not None:
        if role in member.roles:
            await member.remove_roles(role)
            action = "removed"
        else:
            await member.add_roles(role)
            action = "granted"

        confirmation_embed = Embed(
            title="Success",
            description=f"The `Voice Chat Warden` permissions have been {action} for {member.display_name}.",
            color=0x00FF00
        )
        await interaction.response.send_message(embed=confirmation_embed)
    else:
        error_embed = Embed(
            title="Error",
            description="Role not found.",
            color=0xFF0000
        )
        await interaction.response.send_message(embed=error_embed)
