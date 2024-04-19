import discord
from discord import app_commands
import json

from lib.commands import myCommand
from config import token

ROLE_MANAGEMENT_ROLE_ID = 1142491622563643442


class aclient(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.synced = False

    async def on_ready(self):
        if not self.synced:
            await tree.sync()
            self.synced = True
        print(f"Logged in as {self.user}")
        for command in tree.get_commands():
            print(command.name)


client = aclient()
tree = app_commands.CommandTree(client)

@tree.command(name="role-manage", description="some command")
async def role_management(interaction: discord.Interaction):
    await myCommand(interaction, ROLE_MANAGEMENT_ROLE_ID)

client.run(token)
