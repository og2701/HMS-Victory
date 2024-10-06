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

    initial_embed = Embed(
        title="Toggle Permissions",
        description=f"Toggling mute and deafen permissions for {member.display_name} in all 'permanent vc' channels.",
        color=0xFFA500
    )

    await interaction.response.send_message(embed=initial_embed)

    category = interaction.guild.get_channel(959493057076666379)

    if category is not None and isinstance(category, discord.CategoryChannel):
        for channel in category.voice_channels:
            current_perms = channel.permissions_for(member)
            if current_perms.mute_members and current_perms.deafen_members:
                await channel.set_permissions(member, overwrite=None)
            else:
                await channel.set_permissions(member, mute_members=True, deafen_members=True)
        
        confirmation_embed = Embed(
            title="Success",
            description=f"Mute and deafen permissions for {member.display_name} have been toggled in all 'permanent vc' channels.",
            color=0x00FF00
        )
        await interaction.followup.send(embed=confirmation_embed)
    else:
        error_embed = Embed(
            title="Error",
            description="Category 'permanent vc' (959493057076666379) not found.",
            color=0xFF0000
        )
        await interaction.followup.send(embed=error_embed)