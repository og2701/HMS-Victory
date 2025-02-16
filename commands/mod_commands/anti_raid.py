import discord
import os
import json
import asyncio
from discord import Embed
from discord.app_commands import CommandTree, Command
from discord.interactions import Interaction
from lib.settings import *

ANTI_RAID_FILE = "anti_raid_active"
PERMISSIONS_BACKUP_FILE = "role_permissions_backup.json"
QUARANTINE_ROLE_ID = 962009285116710922
ANTI_RAID_LOG_CHANNEL_ID = 1172677237988929646
BATCH_SIZE = 10

def is_anti_raid_enabled():
    return os.path.exists(ANTI_RAID_FILE)

def set_anti_raid_status(active: bool):
    if active:
        open(ANTI_RAID_FILE, "w").close()
    else:
        if os.path.exists(ANTI_RAID_FILE):
            os.remove(ANTI_RAID_FILE)

def backup_role_permissions(guild: discord.Guild):
    role_permissions = {}
    
    for role in guild.roles:
        role_permissions[role.id] = {
            "use_external_apps": role.permissions.use_external_apps
        }
    
    with open(PERMISSIONS_BACKUP_FILE, "w") as f:
        json.dump(role_permissions, f)

async def restore_role_permissions(guild: discord.Guild):
    if not os.path.exists(PERMISSIONS_BACKUP_FILE):
        return
    
    with open(PERMISSIONS_BACKUP_FILE, "r") as f:
        role_permissions = json.load(f)
    
    roles = [role for role in guild.roles if str(role.id) in role_permissions]
    
    for i in range(0, len(roles), BATCH_SIZE):
        batch = roles[i:i + BATCH_SIZE]
        tasks = []
        for role in batch:
            permissions = role.permissions
            permissions.update(
                use_external_apps=role_permissions[str(role.id)]["use_external_apps"]
            )
            tasks.append(role.edit(permissions=permissions))
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"Failed to restore permissions for some roles: {e}")
        
        await asyncio.sleep(1)

async def disable_role_permissions(guild: discord.Guild):
    backup_role_permissions(guild)
    
    roles = [role for role in guild.roles]
    
    for i in range(0, len(roles), BATCH_SIZE):
        batch = roles[i:i + BATCH_SIZE]
        tasks = []
        for role in batch:
            permissions = role.permissions
            permissions.update(
                use_external_apps=False
            )
            tasks.append(role.edit(permissions=permissions))
        
        try:
            await asyncio.gather(*tasks)
        except Exception as e:
            print(f"Failed to disable permissions for some roles: {e}")
        
        await asyncio.sleep(1)

async def send_backup_file(guild: discord.Guild):
    channel = guild.get_channel(ANTI_RAID_LOG_CHANNEL_ID)
    if channel and os.path.exists(PERMISSIONS_BACKUP_FILE):
        await channel.send("Backup of role permissions before enabling anti-raid:", file=discord.File(PERMISSIONS_BACKUP_FILE))

async def toggle_anti_raid(interaction: Interaction):
    await interaction.response.defer()
    active = is_anti_raid_enabled()
    
    if active:
        set_anti_raid_status(False)
        await restore_role_permissions(interaction.guild)
        embed = Embed(
            title="Anti-Raid Disabled",
            description="New joins will no longer be timed out or quarantined. Role permissions have been restored.",
            color=0x00FF00
        )
    else:
        set_anti_raid_status(True)
        await disable_role_permissions(interaction.guild)
        await send_backup_file(interaction.guild)
        embed = Embed(
            title="Anti-Raid Enabled",
            description="Any new join will be auto-timed-out and assigned the quarantine role. Role permissions have been restricted.",
            color=0xFF0000
        )
    
    embed.set_footer(text=f"Triggered by {interaction.user.name}")
    await interaction.followup.send(embed=embed)

async def handle_new_member_anti_raid(member: discord.Member):
    if is_anti_raid_enabled():
        try:
            quarantine_role = member.guild.get_role(QUARANTINE_ROLE_ID)
            if quarantine_role:
                await member.add_roles(quarantine_role)
            # await member.timeout(discord.utils.utcnow() + discord.utils.timedelta(minutes=1440))
        except Exception as e:
            print(e)
