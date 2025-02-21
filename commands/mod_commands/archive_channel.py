import discord
import asyncio
import json
import time
import copy

from lib.utils import load_persistent_views, save_persistent_views
from lib.settings import *

ARCHIVIST_ROLE_ID = 1281602571416375348
ARCHIVE_CATEGORY_ID = 962003831313555537

persistent_views = load_persistent_views()

class ArchiveButtonView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Toggle Archivist Role", style=discord.ButtonStyle.primary, custom_id="archive_button")
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

async def schedule_archive_move(channel: discord.TextChannel, guild: discord.Guild, target_timestamp: float, bot):
    delay = target_timestamp - time.time()
    if delay > 0:
        await asyncio.sleep(delay)
    archive_category = guild.get_channel(ARCHIVE_CATEGORY_ID)
    if archive_category and isinstance(archive_category, discord.CategoryChannel):
        new_overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False)
        }
        archive_role = guild.get_role(ARCHIVIST_ROLE_ID)
        if archive_role:
            new_overwrites[archive_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
        await channel.edit(overwrites=new_overwrites, category=archive_category)
        move_embed = discord.Embed(
            title="Channel Moved to Archive",
            description="This channel has been moved to the archive category.",
            color=0x00FF00,
        )
        await channel.send(embed=move_embed)
        key = f"archive_{channel.id}"
        persistent_views.pop(key, None)
        save_persistent_views(persistent_views)

async def archive_channel(interaction: discord.Interaction, bot):
    guild = interaction.guild
    channel = interaction.channel
    if not channel:
        error_embed = discord.Embed(title="Error", description="Channel not found.", color=0xFF0000)
        await interaction.response.send_message(embed=error_embed, ephemeral=True)
        return

    for target, overwrite in channel.overwrites.items():
        if isinstance(target, discord.Role):
            new_overwrite = copy.copy(overwrite)
            new_overwrite.send_messages = False
            await channel.set_permissions(target, overwrite=new_overwrite)

    view = ArchiveButtonView(bot)
    bot.add_view(view)
    
    embed = discord.Embed(
        title="Channel Archived",
        description=(
            f"{interaction.user.mention} has archived this channel. It will be moved to the archive in 24 hours.\n"
            "If you want to still be able to see it after that, click the button below to toggle the **Archivist** role."
        ),
        color=0xFFA500,
    )
    msg = await channel.send(embed=embed, view=view)
    target_timestamp = time.time() + 3
    persistent_views[f"archive_{channel.id}"] = {"msg_id": msg.id, "move_timestamp": target_timestamp}
    save_persistent_views(persistent_views)
    
    await interaction.response.send_message("Channel archived successfully!", ephemeral=True)
    
    asyncio.create_task(schedule_archive_move(channel, guild, target_timestamp, bot))
