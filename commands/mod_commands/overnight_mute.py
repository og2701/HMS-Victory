import discord
import os
from config import ROLES, CHANNELS, OVERNIGHT_MUTE_FILE
from lib.utils import set_file_status

async def mute_visitors(guild):
    if not os.path.exists(OVERNIGHT_MUTE_FILE):
        return

    visitor_role = guild.get_role(ROLES.VISITOR)
    general_channel = guild.get_channel(CHANNELS.GENERAL)

    if not visitor_role or not general_channel:
        return

    try:
        await general_channel.set_permissions(visitor_role, send_messages=False, reason="Overnight mute for visitors.")
    except discord.Forbidden:
        pass

async def unmute_visitors(guild):
    if not os.path.exists(OVERNIGHT_MUTE_FILE):
        return

    visitor_role = guild.get_role(ROLES.VISITOR)
    general_channel = guild.get_channel(CHANNELS.GENERAL)

    if not visitor_role or not general_channel:
        return

    try:
        await general_channel.set_permissions(visitor_role, send_messages=True, reason="Overnight mute for visitors has ended.")
    except discord.Forbidden:
        pass

async def toggle_overnight_mute(interaction):
    currently_active = os.path.exists(OVERNIGHT_MUTE_FILE)
    set_file_status(OVERNIGHT_MUTE_FILE, not currently_active)
    status = "enabled" if not currently_active else "disabled"
    await interaction.response.send_message(f"Overnight mute for visitors has been {status}.", ephemeral=True)