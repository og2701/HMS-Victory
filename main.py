import discord
from discord.ext import tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import logging

from lib.event_handlers import *
from lib.setup_commands import define_commands

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.synced = False
        self.scheduler = AsyncIOScheduler()
        self.image_cache = {}

    async def on_ready(self):
        await on_ready(self, tree, self.scheduler)

    async def on_message(self, message):
        await on_message(self, message)

    async def on_interaction(self, interaction):
        await on_interaction(interaction)

    async def on_member_join(self, member):
        await on_member_join(member)

    async def on_member_remove(self, member):
        await on_member_remove(member)

    async def on_member_ban(self, guild, user):
        await on_member_ban(guild, user)

    async def on_message_delete(self, message):
        await on_message_delete(self, message)

    async def on_message_edit(self, before, after):
        await on_message_edit(self, before, after)

    async def on_reaction_add(self, reaction, user):
        await on_reaction_add(reaction, user)

    async def on_reaction_remove(self, reaction, user):
        await on_reaction_remove(reaction, user)

client = AClient()
tree = discord.app_commands.CommandTree(client)

define_commands(tree, client)