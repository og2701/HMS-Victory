from discord import Embed
from config import ROLES
from lib.utils import toggle_user_role

async def video_ban(interaction, member):
    role_id = ROLES.VIDEO_BAN
    role = interaction.guild.get_role(role_id)
    if role is not None:
        await toggle_usesr_role(interaction, member, role)
    else:
        error_embed = Embed(title="Error", description="Role not found.", color=0xFF0000)
        await interaction.response.send_message(embed=error_embed)