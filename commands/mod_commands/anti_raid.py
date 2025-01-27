# anti_raid.py

import discord
import os
from discord import Embed
from discord.app_commands import CommandTree, Command
from discord.interactions import Interaction

ANTI_RAID_FILE = "anti_raid_active"
QUARANTINE_ROLE_ID = 962009285116710922

def is_anti_raid_enabled():
    return os.path.exists(ANTI_RAID_FILE)

def set_anti_raid_status(active: bool):
    if active:
        open(ANTI_RAID_FILE, "w").close()
    else:
        if os.path.exists(ANTI_RAID_FILE):
            os.remove(ANTI_RAID_FILE)

async def toggle_anti_raid(interaction: Interaction):
    active = is_anti_raid_enabled()
    if active:
        set_anti_raid_status(False)
        embed = Embed(
            title="Anti-Raid Disabled",
            description="New joins will no longer be timed out or quarantined.",
            color=0x00FF00
        )
    else:
        set_anti_raid_status(True)
        embed = Embed(
            title="Anti-Raid Enabled",
            description="Any new join will be auto-timed-out and assigned the quarantine role.",
            color=0xFF0000
        )

    embed.set_footer(text=f"Triggered by {interaction.user.name}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

async def handle_new_member_anti_raid(member: discord.Member):
    if is_anti_raid_enabled():
        try:
            quarantine_role = member.guild.get_role(QUARANTINE_ROLE_ID)
            if quarantine_role:
                await member.add_roles(quarantine_role)
            await member.timeout(discord.utils.utcnow() + discord.utils.timedelta(minutes=1440))
        except Exception as e:
            print(e)
