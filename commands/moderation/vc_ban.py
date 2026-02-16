from discord import Embed
from config import ROLES
from lib.core.discord_helpers import toggle_user_role

async def vc_ban(interaction, member):
    role_id = ROLES.VC_BAN
    role = interaction.guild.get_role(role_id)
    if role is not None:
        is_adding = role not in member.roles
        await toggle_user_role(interaction, member, role)
        # If the ban was applied and the user is in a VC, kick them
        if is_adding and member.voice:
            await member.move_to(None)
    else:
        error_embed = Embed(title="Error", description="Role not found.", color=0xFF0000)
        await interaction.response.send_message(embed=error_embed)