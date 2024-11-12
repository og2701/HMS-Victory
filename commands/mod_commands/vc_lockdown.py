from discord import Embed, VoiceChannel, Permissions
from lib.settings import *

allowed_roles = [
    ROLES.DUKE, ROLES.MARQUESS, ROLES.EARL, ROLES.VISCOUNT, ROLES.BARON,
    ROLES.KNIGHT, ROLES.LORD, ROLES.ESQUIRE, ROLES.GENTLEMAN, ROLES.YEOMAN,
    ROLES.COMMONER, ROLES.FREEMAN, ROLES.PEASANT, ROLES.SERF
]

async def lockdown_vcs(interaction):
    """
    Locks down all voice channels in a specified category, removing members without specific roles and preventing re-entry.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """
    
    lockdown_embed = Embed(
        title="🚨 Voice Channel Lockdown 🚨",
        description=(
            "🔒 All voice channels in the specified category are now **restricted**.\n"
            "Only members with authorised roles can join or stay in these voice channels.\n"
            "Members without permissions will be removed from VCs immediately."
        ),
        color=0xFF0000
    )
    lockdown_embed.set_footer(text=f"Lockdown initiated by {interaction.user.name}")

    await interaction.response.send_message(embed=lockdown_embed)
    
    logs_channel = interaction.guild.get_channel(CHANNELS.LOGS)
    if logs_channel:
        await logs_channel.send(embed=lockdown_embed)

    category_id = CATEGORIES.PERM_VC
    guild = interaction.guild
    category = guild.get_channel(category_id)
    
    if category is None:
        await interaction.response.send_message("Category not found.", ephemeral=True)
        return
    
    for channel in category.voice_channels:
        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.connect = False
        await channel.set_permissions(guild.default_role, overwrite=overwrite)

        for role_id in allowed_roles:
            role = guild.get_role(role_id)
            if role:
                role_overwrite = channel.overwrites_for(role)
                role_overwrite.connect = True
                await channel.set_permissions(role, overwrite=role_overwrite)

        for member in channel.members:
            if not any(role.id in allowed_roles for role in member.roles):
                await member.move_to(None)

async def end_lockdown_vcs(interaction):
    """
    Ends the lockdown on all voice channels in the specified category, restoring access for all members.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """

    end_lockdown_embed = Embed(
        title="✅ Voice Channel Lockdown Ended",
        description=(
            "🔓 All voice channels in the specified category are now open to all members.\n"
            "Voice channel access has been fully restored."
        ),
        color=0x00FF00
    )
    end_lockdown_embed.set_footer(text=f"Lockdown ended by {interaction.user.name}")

    await interaction.response.send_message(embed=end_lockdown_embed)

    logs_channel = interaction.guild.get_channel(CHANNELS.LOGS)
    if logs_channel:
        await logs_channel.send(embed=end_lockdown_embed)

    category_id = 959493057076666379
    guild = interaction.guild
    category = guild.get_channel(category_id)
    
    if category is None:
        await interaction.response.send_message("Category not found.", ephemeral=True)
        return
    
    for channel in category.voice_channels:
        overwrite = channel.overwrites_for(guild.default_role)
        overwrite.connect = None
        await channel.set_permissions(guild.default_role, overwrite=overwrite)

        for role_id in allowed_roles:
            role = guild.get_role(role_id)
            if role:
                role_overwrite = channel.overwrites_for(role)
                role_overwrite.connect = None
                await channel.set_permissions(role, overwrite=role_overwrite)
