import discord
from discord import app_commands
from discord.ext import commands

ARCHIVIST_ROLE_ID = 1281602571416375348
ARCHIVE_CATEGORY_ID = 962003831313555537

class ArchiveChannel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="archive-channel", description="Moves the current channel to the archive category.")
    @app_commands.checks.has_role(ARCHIVIST_ROLE_ID)
    async def archive_channel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        channel_to_archive = interaction.channel
        guild = interaction.guild
        archive_category = self.bot.get_channel(ARCHIVE_CATEGORY_ID)
        archive_role = guild.get_role(ARCHIVIST_ROLE_ID)

        if not all([archive_category, archive_role]):
            await interaction.followup.send("Archive category or role not found. Please check the IDs.", ephemeral=True)
            return
            
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False, send_messages=False),
                archive_role: discord.PermissionOverwrite(view_channel=True, send_messages=False),
                guild.me: discord.PermissionOverwrite(view_channel=True)
            }
            
            await channel_to_archive.edit(category=archive_category, overwrites=overwrites)
            
            embed = discord.Embed(
                title="Channel Archived",
                description="This channel has been moved to the archives. It is now read-only.",
                color=0x00FF00
            )
            await channel_to_archive.send(embed=embed)

            await interaction.followup.send(f"Channel #{channel_to_archive.name} has been successfully archived.", ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"An error occurred while archiving the channel: {e}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(ArchiveChannel(bot))