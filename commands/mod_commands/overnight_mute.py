import discord
import os
from lib.constants import ROLES, OVERNIGHT_MUTE_FILE
from lib.utils import set_file_status

async def mute_visitors(guild):
    if not os.path.exists(OVERNIGHT_MUTE_FILE):
        return

    visitor_role = guild.get_role(ROLES.VISITOR)
    if not visitor_role:
        return

    for member in guild.members:
        if visitor_role in member.roles:
            try:
                await member.edit(mute=True, reason="Overnight mute for visitors.")
            except discord.Forbidden:
                pass

async def unmute_visitors(guild):
    if not os.path.exists(OVERNIGHT_MUTE_FILE):
        return

    visitor_role = guild.get_role(ROLES.VISITOR)
    if not visitor_role:
        return

    for member in guild.members:
        if visitor_role in member.roles:
            try:
                await member.edit(mute=False, reason="Overnight mute for visitors has ended.")
            except discord.Forbidden:
                pass


async def toggle_overnight_mute(interaction):
    currently_active = os.path.exists(OVERNIGHT_MUTE_FILE)
    set_file_status(OVERNIGHT_MUTE_FILE, not currently_active)
    status = "enabled" if not currently_active else "disabled"
    await interaction.response.send_message(f"Overnight mute for visitors has been {status}.", ephemeral=True)