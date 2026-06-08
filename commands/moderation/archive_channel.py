import discord
import asyncio
import json
import time
import copy
from lib.core.file_operations import load_persistent_views, save_persistent_views
from config import *

ARCHIVIST_ROLE_ID = 1281602571416375348
ARCHIVE_CATEGORY_ID = 962003831313555537

persistent_views = load_persistent_views()

class ArchiveButtonView(discord.ui.View):
    def __init__(self, bot, channel_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.channel_id = channel_id
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "placeholder_custom_id":
                child.custom_id = f"archive_button_{channel_id}"

    @discord.ui.button(label="Toggle Archivist Role", style=discord.ButtonStyle.primary, custom_id="placeholder_custom_id")
    async def archive_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        role = guild.get_role(ARCHIVIST_ROLE_ID)
        if not role:
            await interaction.response.send_message("Archivist role not found.", ephemeral=True)
            return
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message("Archivist role removed!", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message("Archivist role assigned!", ephemeral=True)

class UnarchiveButtonView(discord.ui.View):
    def __init__(self, bot, channel_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.channel_id = channel_id
        for child in self.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "placeholder_unarchive_id":
                child.custom_id = f"unarchive_button_{channel_id}"

    @discord.ui.button(label="Unarchive Channel", style=discord.ButtonStyle.success, custom_id="placeholder_unarchive_id")
    async def unarchive_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        member = guild.get_member(interaction.user.id)
        if not member or not any(r.id in [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO] for r in member.roles):
            await interaction.response.send_message("You do not have permission to unarchive channels.", ephemeral=True)
            return

        from database import DatabaseManager
        record = DatabaseManager.get_archived_channel(self.channel_id)
        if not record:
            await interaction.response.send_message("No archive record found for this channel.", ephemeral=True)
            return
            
        original_category_id, original_overwrites_json = record
        
        channel = guild.get_channel(self.channel_id)
        if not channel:
            await interaction.response.send_message("Channel not found.", ephemeral=True)
            return
            
        # Parse overwrites
        try:
            overwrites_data = json.loads(original_overwrites_json)
            new_overwrites = {}
            for item in overwrites_data:
                target = guild.get_role(item['id']) if item['type'] == 'role' else guild.get_member(item['id'])
                if target:
                    new_overwrites[target] = discord.PermissionOverwrite.from_pair(
                        discord.Permissions(item['allow']),
                        discord.Permissions(item['deny'])
                    )
        except Exception as e:
            await interaction.response.send_message(f"Failed to parse original permissions: {e}", ephemeral=True)
            return

        category = guild.get_channel(int(original_category_id)) if original_category_id else None
        await channel.edit(category=category, overwrites=new_overwrites)
        
        DatabaseManager.delete_archived_channel(self.channel_id)
        
        # Disable button
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        
        success_embed = discord.Embed(
            title="Channel Unarchived",
            description=f"{interaction.user.mention} has restored this channel to its original state.",
            color=0x00FF00
        )
        await interaction.response.send_message(embed=success_embed)

async def schedule_archive_move(channel: discord.TextChannel, guild: discord.Guild, target_timestamp: float, bot, private: bool = False):
    delay = target_timestamp - time.time()
    if delay > 0:
        await asyncio.sleep(delay)
    
    archive_cat_id = 1281601623747072104 if private else ARCHIVE_CATEGORY_ID
    archive_category = guild.get_channel(archive_cat_id)
    
    if archive_category and isinstance(archive_category, discord.CategoryChannel):
        if private:
            await channel.edit(category=archive_category, sync_permissions=True)
            desc = "This channel has been moved to the private archive category."
        else:
            new_overwrites = {guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)}
            archive_role = guild.get_role(ARCHIVIST_ROLE_ID)
            if archive_role:
                new_overwrites[archive_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
            await channel.edit(overwrites=new_overwrites, category=archive_category)
            desc = "This channel has been moved to the archive category."
            
        move_embed = discord.Embed(
            title="Channel Moved to Archive",
            description=desc,
            color=0x00FF00,
        )
        
        view = UnarchiveButtonView(bot, channel.id)
        bot.add_view(view)
        msg = await channel.send(embed=move_embed, view=view)
        
        key = f"archive_{channel.id}"
        persistent_views.pop(key, None)
        persistent_views[f"unarchive_{channel.id}"] = {"msg_id": msg.id}
        save_persistent_views(persistent_views)

async def archive_channel(interaction: discord.Interaction, bot, seconds: int, private: bool = False):
    guild = interaction.guild
    channel = interaction.channel
    if not channel:
        error_embed = discord.Embed(title="Error", description="Channel not found.", color=0xFF0000)
        await interaction.followup.send(embed=error_embed, ephemeral=True)
        return
        
    if private:
        seconds = 0
        
    from database import DatabaseManager
    # Save original perms before we strip them
    original_overwrites = []
    for target, overwrite in channel.overwrites.items():
        original_overwrites.append({
            "id": target.id,
            "type": "role" if isinstance(target, discord.Role) else "member",
            "allow": overwrite.pair()[0].value,
            "deny": overwrite.pair()[1].value
        })
    DatabaseManager.save_archived_channel(
        channel.id, 
        channel.category_id, 
        json.dumps(original_overwrites)
    )
        
    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role):
            new_overwrite = copy.copy(overwrite)
            new_overwrite.send_messages = False
            await channel.set_permissions(target, overwrite=new_overwrite)
            
    if private:
        embed = discord.Embed(
            title="Channel Archived",
            description=f"{interaction.user.mention} has archived this channel. It will be moved to the private archive immediately.",
            color=0xFFA500,
        )
        msg = await channel.send(embed=embed)
    else:
        view = ArchiveButtonView(bot, channel_id=channel.id)
        bot.add_view(view)
        embed = discord.Embed(
            title="Channel Archived",
            description=(f"{interaction.user.mention} has archived this channel. It will be moved to the archive in {seconds} seconds.\n"
                         "If you want to still be able to see it after that, click the button below to toggle the **Archivist** role."),
            color=0xFFA500,
        )
        msg = await channel.send(embed=embed, view=view)
        
    target_timestamp = time.time() + seconds
    persistent_views[f"archive_{channel.id}"] = {"msg_id": msg.id, "move_timestamp": target_timestamp, "private": private}
    save_persistent_views(persistent_views)

    if private:
        await interaction.followup.send("Channel will be archived immediately!", ephemeral=True)
    else:
        await interaction.followup.send(f"Channel will be archived in {seconds // 3600} hours!", ephemeral=True)
    
    asyncio.create_task(schedule_archive_move(channel, guild, target_timestamp, bot, private))
