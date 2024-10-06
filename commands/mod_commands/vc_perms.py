from discord import Embed, CategoryChannel
from discord.ext import commands

async def toggleMuteDeafenPermissions(interaction, member):
    """
    This command toggles the specified user's permissions to server mute and deafen others in all voice channels
    under the 'permanent vc' category.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        member (discord.Member): The user whose mute and deafen permissions will be toggled.

    Returns:
        None
    """

    category = interaction.guild.get_channel(959493057076666379)

    if category is not None and isinstance(category, CategoryChannel):
        current_perms = category.permissions_for(member)
        if current_perms.mute_members and current_perms.deafen_members:
            await category.set_permissions(member, overwrite=None)
            action = "removed"
            for channel in category.voice_channels:
                await channel.set_permissions(member, overwrite=None)
        else:
            await category.set_permissions(member, mute_members=True, deafen_members=True)
            action = "granted"
            for channel in category.voice_channels:
                await channel.set_permissions(member, mute_members=True, deafen_members=True)
        
        confirmation_embed = Embed(
            title="Success",
            description=f"Mute and deafen permissions for {member.display_name} have been {action} in the 'permanent vc' category and all voice channels under it.",
            color=0x00FF00
        )
        await interaction.response.send_message(embed=confirmation_embed)
    else:
        error_embed = Embed(
            title="Error",
            description="Category 'permanent vc' (959493057076666379) not found.",
            color=0xFF0000
        )
        await interaction.response.send_message(embed=error_embed)