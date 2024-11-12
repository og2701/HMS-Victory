import discord
import asyncio
from discord import Embed
from lib.settings import *
import os

def set_lockdown_status(active):
    if active:
        open(VC_LOCKDOWN_FILE, "w").close()
    else:
        if os.path.exists(VC_LOCKDOWN_FILE):
            os.remove(VC_LOCKDOWN_FILE)

async def lockdown_vcs(interaction):
    set_lockdown_status(True)

    lockdown_embed = Embed(
        title="🚨 Voice Channel Lockdown Activated 🚨",
        description=(
            "🔒 All voice channels are now restricted. Unauthorised members will be server-muted and deafened."
        ),
        color=0xFF0000
    )
    lockdown_embed.set_footer(text=f"Lockdown initiated by {interaction.user.name}")
    
    await interaction.response.send_message(embed=lockdown_embed)
    
    guild = interaction.guild
    log_channel = guild.get_channel(CHANNELS.LOGS)
    police_station_channel = guild.get_channel(CHANNELS.POLICE_STATION)
    
    if log_channel:
        await log_channel.send(embed=lockdown_embed)
    if police_station_channel:
        await police_station_channel.send(embed=lockdown_embed)
    
    for channel in guild.voice_channels:
        for member in channel.members:
            if not any(role.id in VC_LOCKDOWN_WHITELIST for role in member.roles):
                await member.edit(mute=True, deafen=True)

async def end_lockdown_vcs(interaction):
    set_lockdown_status(False)

    end_lockdown_embed = Embed(
        title="✅ Voice Channel Lockdown Ended",
        description="🔓 Voice channel access restrictions have been lifted. All members are free to join.",
        color=0x00FF00
    )
    end_lockdown_embed.set_footer(text=f"Lockdown ended by {interaction.user.name}")
    
    await interaction.response.send_message(embed=end_lockdown_embed)
    
    guild = interaction.guild
    log_channel = guild.get_channel(CHANNELS.LOGS)
    police_station_channel = guild.get_channel(CHANNELS.POLICE_STATION)
    
    if log_channel:
        await log_channel.send(embed=end_lockdown_embed)
    if police_station_channel:
        await police_station_channel.send(embed=end_lockdown_embed)
    
    for channel in guild.voice_channels:
        for member in channel.members:
            await member.edit(mute=False, deafen=False)
