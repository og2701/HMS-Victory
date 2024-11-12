from discord import Embed, VoiceChannel, Permissions
from lib.settings import *

async def lockdown_vcs(interaction):
    """
    Locks down all voice channels, removing members without specific roles and preventing re-entry.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """

    allowed_roles = [
        ROLES.DUKE, ROLES.MARQUESS, ROLES.EARL, ROLES.VISCOUNT, ROLES.BARON,
        ROLES.KNIGHT, ROLES.LORD, ROLES.ESQUIRE, ROLES.GENTLEMAN, ROLES.YEOMAN,
        ROLES.COMMONER, ROLES.FREEMAN, ROLES.PEASANT, ROLES.SERF
    ]
    
    lockdown_embed = Embed(
        title="🚨 Voice Channel Lockdown 🚨",
        description=(
            "🔒 All voice channels are now **restricted**.\n"
            "Only members with authorized roles can join or stay in the voice channels.\n"
            "Members without permissions will be removed from VCs immediately."
        ),
        color=0xFF0000
    )
    lockdown_embed.set_footer(text=f"Lockdown initiated by {interaction.user.name}")

    await interaction.response.send_message(embed=lockdown_embed)
    
    logs_channel = interaction.guild.get_channel(CHANNELS.LOGS)
    if logs_channel:
        await logs_channel.send(embed=lockdown_embed)

    guild = interaction.guild
    for channel in guild.voice_channels:
        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.connect = False
        await channel.set_permissions(guild.default_role, overwrite=overwrite)

        for member in channel.members:
            if not any(role.id in allowed_roles for role in member.roles):
                await member.move_to(None)

async def end_lockdown_vcs(interaction):
    """
    Ends the lockdown on all voice channels, restoring access for all members.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """

    end_lockdown_embed = Embed(
        title="✅ Voice Channel Lockdown Ended",
        description=(
            "🔓 All voice channels are now open to all members.\n"
            "Voice channel access has been fully restored."
        ),
        color=0x00FF00
    )
    end_lockdown_embed.set_footer(text=f"Lockdown ended by {interaction.user.name}")

    await interaction.response.send_message(embed=end_lockdown_embed)

    logs_channel = interaction.guild.get_channel(CHANNELS.LOGS)
    if logs_channel:
        await logs_channel.send(embed=end_lockdown_embed)

    guild = interaction.guild
    for channel in guild.voice_channels:
        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(guild.default_role, overwrite=overwrite)
