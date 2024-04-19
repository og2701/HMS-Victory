from discord import Embed, utils, ui

async def myCommand(interaction, role_id: int):
  guild = interaction.guild
  
  role = utils.get(guild.roles, id=role_id)
  
  members_without_role = []
  for member in guild.members:
    if role not in member.roles:
      members_without_role.append(member)

  description = "\n".join([str(member) for member in members_without_role])

  embed = Embed(title=f"Members without role __{role.name}__", description=description, color=0xFFA500)

  button = ui.Button(label="Give role")

  members_given_role = []

  async def button_callback(interaction):
    for member in members_without_role:
      await member.add_roles(role)
      members_given_role.append(str(member))
    
    embed = Embed(title=f"Role __{role.name}__ given to:", description="\n".join(members_given_role), color=0x00FF00)
    await interaction.response.edit_message(embed=embed, view=None)

  button.callback = button_callback

  view = ui.View()
  view.add_item(button)

  await interaction.response.send_message(embed=embed, view=view)
