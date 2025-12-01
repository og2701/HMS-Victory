from discord import Embed
from config import *
from lib.core.discord_helpers import toggle_user_role

async def toggleMuteDeafenPermissions(interaction, member):
    role_id = ROLES.VOICE_CHAT_WARDEN
    role = interaction.guild.get_role(role_id)
    if role is not None:
        await toggle_user_role(interaction, member, role)
    else:
        error_embed = Embed(title="Error", description="Role not found.", color=0xFF0000)
        await interaction.response.send_message(embed=error_embed)