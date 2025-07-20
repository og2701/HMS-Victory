import discord
from discord import Embed
import asyncio
from config import *
from config import *
from lib.utils import set_file_status, send_embed_to_channels, edit_voice_channel_members

async def lockdown_vcs(interaction):
    set_file_status(VC_LOCKDOWN_FILE, True)
    lockdown_embed = Embed(
        title="🚨 Voice Channel Lockdown Activated 🚨",
        description="🔒 All voice channels are now restricted. Unauthorised members will be server-muted and deafened.",
        color=0xFF0000,
    )
    lockdown_embed.set_footer(text=f"Lockdown initiated by {interaction.user.name}")
    await interaction.response.send_message(embed=lockdown_embed)
    guild = interaction.guild
    await send_embed_to_channels(guild, lockdown_embed, [CHANNELS.LOGS, CHANNELS.POLICE_STATION])
    await edit_voice_channel_members(guild, mute=True, deafen=True, whitelist=VC_LOCKDOWN_WHITELIST)

async def end_lockdown_vcs(interaction):
    set_file_status(VC_LOCKDOWN_FILE, False)
    end_lockdown_embed = Embed(
        title="✅ Voice Channel Lockdown Ended",
        description="🔓 Voice channel access restrictions have been lifted. All members are free to join.",
        color=0x00FF00,
    )
    end_lockdown_embed.set_footer(text=f"Lockdown ended by {interaction.user.name}")
    await interaction.response.send_message(embed=end_lockdown_embed)
    guild = interaction.guild
    await send_embed_to_channels(guild, end_lockdown_embed, [CHANNELS.LOGS, CHANNELS.POLICE_STATION])
    await edit_voice_channel_members(guild, mute=False, deafen=False)
