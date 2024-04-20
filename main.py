from discord import app_commands, Intents, Interaction, Client
import json

from lib.commands import updateRoleAssignments
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


@tree.command(name="role-manage", description="some command")
async def role_management(interaction: Interaction, role_name: str):
    await updateRoleAssignments(interaction, role_name)


client.run(token)
