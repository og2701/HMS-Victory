from discord import Embed, utils, ButtonStyle, ui
import asyncio

async def updateRoleAssignments(interaction, role_name: str):
  """
  Updates the role assignments for members in the guild.

  Args:
      interaction (discord.Interaction): The interaction that triggered the command.
      role_name (str): The name of the role to update.

  Returns:
      None
  """
  # check if user has permission to manage roles
  if not interaction.user.guild_permissions.manage_guild:
      await interaction.response.send_message("You do not have permission to use this command.")
      return

  guild = interaction.guild
  role = utils.get(guild.roles, name=role_name)
  if role is None:
      await interaction.response.send_message(f"Role '{role_name}' not found.")
      return
      
  # get members without specified role
  members_without_role = [member for member in guild.members if role not in member.roles]
  member_count = len(members_without_role)

  if member_count == 0:
      await interaction.response.send_message("All members already have this role.")
      return

  if member_count > 50:
      initial_description = f"There are {member_count} members without the role {role_name}."
  else:
      initial_description = " | ".join([str(member) for member in members_without_role])
      if len(initial_description) > 4096:
          initial_description = f"There are {member_count} members without the role {role_name}."

  initial_embed = Embed(
      title=f"Members without role __{role.name}__",
      description=initial_description,
      color=0xFFA500
  )
  
  button = ui.Button(label="Give Role", style=ButtonStyle.green)

  async def button_callback(interaction):
      await interaction.response.defer()

      button.label = "Processing..."
      button.style = ButtonStyle.grey
      button.disabled = True
      
      processing_view = ui.View()
      processing_view.add_item(button)
      
      processing_embed = Embed(
          title="Processing...",
          description="Assigning roles, please wait...",
          color=0x808080
      )
      
      await interaction.followup.edit_message(
          message_id=interaction.message.id,
          embed=processing_embed,
          view=processing_view
      )

      batch_size = 10
      delay = 1.2
      members_given_role = []

      # batch add roles
      for i in range(0, len(members_without_role), batch_size):
          batch = members_without_role[i:i + batch_size]
          
          for member in batch:
              await member.add_roles(role)
              members_given_role.append(member)
              
          await asyncio.sleep(delay)

      final_description = f"Done. Given role __{role.name}__ to {len(members_given_role)} members."
      
      final_embed = Embed(
          title="Role Assignment Complete",
          description=final_description,
          color=0x00FF00
      )
      
      await interaction.followup.edit_message(
          message_id=interaction.message.id,
          embed=final_embed,
          view=None
      )

  button.callback = button_callback
  
  view = ui.View()
  view.add_item(button)
  
  await interaction.response.send_message(embed=initial_embed, view=view)
