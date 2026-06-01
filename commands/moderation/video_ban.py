from discord import Embed
from config import ROLES
from lib.core.discord_helpers import toggle_user_role, can_moderate_target

async def video_ban(interaction, member):
    refusal = can_moderate_target(interaction, member)
    if refusal:
        return await interaction.response.send_message(refusal, ephemeral=True)
    role_id = ROLES.VIDEO_BAN
    role = interaction.guild.get_role(role_id)
    if role is not None:
        await toggle_user_role(interaction, member, role)
    else:
        error_embed = Embed(title="Error", description="Role not found.", color=0xFF0000)
        await interaction.response.send_message(embed=error_embed)