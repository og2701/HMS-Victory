from discord import app_commands, Intents, Interaction, Client
import json

from lib.commands import updateRoleAssignments, colourPalette, gridify
from config import token


class AClient(Client):
  def __init__(self):
    intents = Intents.default()
    intents.presences = True
    intents.members = True
    intents.messages = True
    intents.guild_messages = True
    intents.dm_messages = True

    super().__init__(intents=intents)
    self.synced = False

  async def on_ready(self):
    global tree
    if not self.synced:
      await tree.sync()
      self.synced = True
    print(f"Logged in as {self.user}")
    for command in tree.get_commands():
      print(command.name)

client = AClient()
tree = app_commands.CommandTree(client)


@tree.command(name="role-manage", description="Manages user roles by assigning a specified role to members who don't have it")
async def role_management(interaction: Interaction, role_name: str):
  await updateRoleAssignments(interaction, role_name)

@tree.command(name="colour-palette", description="Generates a colour palette from an image")

async def colour_palette(interaction: Interaction, attachment_url: str):
  await colourPalette(interaction, attachment_url)

@tree.command(name="gridify", description="Adds a pixel art grid overlay to an image")
async def gridify_command(interaction: Interaction, attachment_url: str):
  await gridify(interaction, attachment_url)


client.run(token)
