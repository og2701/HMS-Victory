"""A typed slash command that opens the activity, answered by the UK Place Activities bot.

An activity can only be launched by its own app, so HMS Victory can't do it. Discord's
built-in launch command only lives in the App Launcher, which nobody thinks to open, so
this logs the activities app's own bot in alongside HMS Victory (same process, no message
intents) just to answer /test-wordle with "launch the activity".

The command is registered with a single upsert rather than tree.sync(): a bulk sync would
have to include the app's Entry Point command, which this library doesn't know how to send,
and Discord refuses a bulk update that drops it.
"""

import logging

import discord
from discord import app_commands

import config
from lib.activities import auth

log = logging.getLogger(__name__)

_client: "Launcher | None" = None


def _allowed(channel_id) -> bool:
    allowed = {int(c) for c in getattr(config, "ACTIVITIES_ALLOWED_CHANNELS", []) or []}
    return not allowed or (channel_id is not None and int(channel_id) in allowed)


def _refusal() -> str:
    channels = " ".join(f"<#{c}>" for c in getattr(config, "ACTIVITIES_ALLOWED_CHANNELS", []))
    return f"HMS Wordle is still being tested and only opens in {channels} for now."


class Launcher(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.none())
        self.tree = app_commands.CommandTree(self)
        name = getattr(config, "ACTIVITIES_LAUNCH_COMMAND", "test-wordle")
        description = getattr(config, "ACTIVITIES_LAUNCH_DESCRIPTION", "Play HMS Wordle")

        @self.tree.command(name=name, description=description)
        async def launch(interaction: discord.Interaction):
            if not _allowed(interaction.channel_id):
                await interaction.response.send_message(_refusal(), ephemeral=True)
                return
            await interaction.response.launch_activity()


async def _register(session) -> None:
    """Create or update the typed command, leaving the Entry Point command alone."""
    body = {
        "name": getattr(config, "ACTIVITIES_LAUNCH_COMMAND", "test-wordle"),
        "description": getattr(config, "ACTIVITIES_LAUNCH_DESCRIPTION", "Play HMS Wordle"),
        "type": 1,
        "integration_types": [0],   # installed to a server
        "contexts": [0],            # used in a server channel
    }
    app_id = auth._env("ACTIVITIES_CLIENT_ID")
    async with session.post(f"{auth.API}/applications/{app_id}/commands", json=body,
                            headers={"Authorization": f"Bot {auth._env('ACTIVITIES_BOT_TOKEN')}"}) as r:
        log.info("activity launch command /%s registered (%s)", body["name"], r.status)


async def start(session) -> None:
    global _client
    if _client is not None or not auth.configured():
        return
    try:
        await _register(session)
    except Exception:
        log.warning("couldn't register the activity launch command", exc_info=True)
    _client = Launcher()
    import asyncio
    asyncio.create_task(_client.start(auth._env("ACTIVITIES_BOT_TOKEN")))


async def stop() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
