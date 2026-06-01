import discord
from discord import Interaction, Forbidden
from config import GUILD_ID

async def handleRoleButtonInteraction(interaction: Interaction):
    if interaction.type == discord.InteractionType.component and "custom_id" in interaction.data:
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            role_id = custom_id.split("_")[1]
            guild = interaction.guild if interaction.guild_id else interaction._client.get_guild(GUILD_ID)
            member = interaction.user if hasattr(interaction.user, "guild") else guild.get_member(interaction.user.id)
            if member is None:
                return await interaction.response.send_message("Failed to find member.", ephemeral=True)
            role = guild.get_role(int(role_id))
            if role:
                try:
                    if role in member.roles:
                        await member.remove_roles(role)
                        await interaction.response.send_message(f"Role **{role.name}** removed.", ephemeral=True)
                    else:
                        await member.add_roles(role)
                        await interaction.response.send_message(f"Role **{role.name}** assigned.", ephemeral=True)
                except Forbidden:
                    await interaction.response.send_message("I do not have permission to assign this role.", ephemeral=True)
                except Exception as e:
                    await interaction.response.send_message("An error occurred while assigning the role.", ephemeral=True)
                    print(e)
            else:
                await interaction.response.send_message("Role not found.", ephemeral=True)

