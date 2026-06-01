import discord
import os
import json
import asyncio
from discord import Embed
from discord.interactions import Interaction
from config import *
from lib.core.file_operations import set_file_status, atomic_write_json  # Reuse our file toggle utility

# Store the flag under the persistent data dir (not the CWD) so the active state
# survives a restart regardless of the working directory the bot is launched from.
ANTI_RAID_FILE = os.path.join(JSON_DATA_DIR, "anti_raid_active")
from config import PERMISSIONS_BACKUP_FILE
QUARANTINE_ROLE_ID = 962009285116710922
ANTI_RAID_LOG_CHANNEL_ID = 1172677237988929646
BATCH_SIZE = 10

# Permissions stripped from every role while a raid lockdown is active. These are
# the high-abuse spam vectors; send_messages is deliberately left untouched so the
# lockdown doesn't silence the whole server (the quarantine role handles joiners).
RESTRICTED_RAID_PERMS = {
    "use_external_apps": False,
    "mention_everyone": False,
    "embed_links": False,
    "attach_files": False,
}

def is_anti_raid_enabled():
    return os.path.exists(ANTI_RAID_FILE)

def set_anti_raid_status(active: bool):
    set_file_status(ANTI_RAID_FILE, active)

def backup_role_permissions(guild: discord.Guild):
    # Store the FULL permissions integer per role so restore is exact, not just a
    # single bit (which previously left most of a role's perms unrecoverable).
    role_permissions = {str(role.id): role.permissions.value for role in guild.roles}
    atomic_write_json(PERMISSIONS_BACKUP_FILE, role_permissions)

async def restore_role_permissions(guild: discord.Guild):
    if not os.path.exists(PERMISSIONS_BACKUP_FILE):
        return
    with open(PERMISSIONS_BACKUP_FILE, "r") as f:
        role_permissions = json.load(f)
    roles = [role for role in guild.roles if str(role.id) in role_permissions]
    for i in range(0, len(roles), BATCH_SIZE):
        batch = roles[i : i + BATCH_SIZE]
        tasks = []
        for role in batch:
            saved = role_permissions[str(role.id)]
            if isinstance(saved, dict):
                # Legacy backup format: only use_external_apps was stored.
                permissions = role.permissions
                permissions.update(use_external_apps=saved.get("use_external_apps", True))
            else:
                permissions = discord.Permissions(int(saved))
            tasks.append(role.edit(permissions=permissions))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for role, result in zip(batch, results):
            if isinstance(result, Exception):
                print(f"Failed to restore permissions for {role.name}: {result}")
        await asyncio.sleep(1)

async def disable_role_permissions(guild: discord.Guild):
    backup_role_permissions(guild)
    roles = list(guild.roles)
    for i in range(0, len(roles), BATCH_SIZE):
        batch = roles[i : i + BATCH_SIZE]
        tasks = []
        for role in batch:
            permissions = role.permissions
            permissions.update(**RESTRICTED_RAID_PERMS)
            tasks.append(role.edit(permissions=permissions))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for role, result in zip(batch, results):
            if isinstance(result, Exception):
                print(f"Failed to restrict permissions for {role.name}: {result}")
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
            description="New joins will no longer be quarantined. Role permissions have been restored.",
            color=0x00FF00,
        )
    else:
        set_anti_raid_status(True)
        await disable_role_permissions(interaction.guild)
        await send_backup_file(interaction.guild)
        embed = Embed(
            title="Anti-Raid Enabled",
            description="Any new join will be assigned the quarantine role. Role permissions have been restricted.",
            color=0xFF0000,
        )
    embed.set_footer(text=f"Triggered by {interaction.user.name}")
    await interaction.followup.send(embed=embed)
    
async def handle_new_member_anti_raid(member: discord.Member):
    if is_anti_raid_enabled():
        try:
            quarantine_role = member.guild.get_role(QUARANTINE_ROLE_ID)
            if quarantine_role:
                await member.add_roles(quarantine_role)
        except Exception as e:
            print(e)
