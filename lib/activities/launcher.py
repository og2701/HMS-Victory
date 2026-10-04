"""Typed slash commands that open the activity, answered by the UK Place Activities bot.

An activity can only be launched by its own app, so HMS Victory can't do it. Discord's
built-in launch command only lives in the App Launcher, which nobody thinks to open, so
this logs the activities app's own bot in alongside HMS Victory (same process, no message
intents) just to answer its commands and Play buttons with "launch the activity".

A launch can't carry any data into the page, so each command (and each Play button) notes
which game this person asked for; the page's sign-in collects it a moment later.

Commands are registered one upsert at a time rather than with tree.sync(): a bulk sync
would have to include the app's Entry Point command, which this library doesn't know how
to send, and Discord refuses a bulk update that drops it.
"""

import asyncio
import logging
import time

import discord
from discord import app_commands

import config
from lib.activities import auth

log = logging.getLogger(__name__)

_client: "Launcher | None" = None
_requested: dict[int, tuple[str, float]] = {}
REQUEST_TTL = 120   # seconds between asking for a game and the page signing in
GAMES = ("wordle", "crossword", "climb")


def _commands() -> dict:
    return getattr(config, "ACTIVITIES_LAUNCH_COMMANDS", {
        "test-wordle": ("wordle", "Play HMS Wordle as an activity (testing)"),
    })


def want(uid: int, game: str) -> None:
    _requested[int(uid)] = (game, time.time())


def take_requested(uid: int) -> str | None:
    """The game this person just asked for, if it was recent. Consumed once read."""
    game, at = _requested.pop(int(uid), (None, 0))
    return game if game and time.time() - at < REQUEST_TTL else None


def _allowed(channel_id) -> bool:
    allowed = {int(c) for c in getattr(config, "ACTIVITIES_ALLOWED_CHANNELS", []) or []}
    return not allowed or (channel_id is not None and int(channel_id) in allowed)


def _refusal() -> str:
    channels = " ".join(f"<#{c}>" for c in getattr(config, "ACTIVITIES_ALLOWED_CHANNELS", []))
    return f"This is still being tested and only opens in {channels} for now."


async def _launch(interaction: discord.Interaction, game: str) -> None:
    if not _allowed(interaction.channel_id):
        await interaction.response.send_message(_refusal(), ephemeral=True)
        return
    want(interaction.user.id, game)
    await interaction.response.launch_activity()


def _play_id(game: str) -> str:
    return "ukplace:play" if game == "wordle" else f"ukplace:play:{game}"


class PlayView(discord.ui.View):
    """The Play button under a solve message. Persistent, so it keeps working after a restart."""

    def __init__(self, game: str = "wordle"):
        super().__init__(timeout=None)
        button = discord.ui.Button(label="Play", style=discord.ButtonStyle.success, custom_id=_play_id(game))

        async def play(interaction: discord.Interaction):
            await _launch(interaction, game)
        button.callback = play
        self.add_item(button)


class CasinoPlay(discord.ui.DynamicItem[discord.ui.Button], template=r"ukplace:play:casino:(?P<key>[a-z]+)"):
    """The Play button on a #casino post: opens the activity at that game's table."""

    def __init__(self, key: str):
        super().__init__(discord.ui.Button(label="Play", style=discord.ButtonStyle.success,
                                           custom_id=f"ukplace:play:casino:{key}"))
        self.key = key

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["key"])

    async def callback(self, interaction: discord.Interaction):
        await _launch(interaction, f"casino:{self.key}")


class CasinoWatch(discord.ui.DynamicItem[discord.ui.Button],
                  template=r"ukplace:watch:(?P<uid>[0-9]+):(?P<key>[a-z]+)"):
    """The Spectate button on a #casino line: opens the activity watching that player's table."""

    def __init__(self, uid: int, key: str):
        super().__init__(discord.ui.Button(label="Spectate", style=discord.ButtonStyle.secondary,
                                           custom_id=f"ukplace:watch:{uid}:{key}"))
        self.uid, self.key = int(uid), key

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["uid"]), match["key"])

    async def callback(self, interaction: discord.Interaction):
        mine = interaction.user.id == self.uid          # watching yourself is just playing
        await _launch(interaction, f"casino:{self.key}" if mine else f"watch:{self.uid}:{self.key}")


class Launcher(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.none())
        self.tree = app_commands.CommandTree(self)
        for name, (game, description) in _commands().items():
            self.tree.add_command(self._command(name, game, description))

    @staticmethod
    def _command(name: str, game: str, description: str) -> app_commands.Command:
        async def launch(interaction: discord.Interaction):
            await _launch(interaction, game)
        return app_commands.Command(name=name, description=description, callback=launch)

    async def setup_hook(self):
        for game in GAMES:
            self.add_view(PlayView(game))
        self.add_dynamic_items(CasinoPlay, CasinoWatch)


async def _register(session) -> None:
    """Create or update each typed command and delete any the config no longer lists
    (the /test- ones, say), leaving the Entry Point command alone."""
    app_id = auth._env("ACTIVITIES_CLIENT_ID")
    base = f"{auth.API}/applications/{app_id}/commands"
    headers = {"Authorization": f"Bot {auth._env('ACTIVITIES_BOT_TOKEN')}"}
    wanted = _commands()
    for name, (_game, description) in wanted.items():
        body = {"name": name, "description": description, "type": 1,
                "integration_types": [0],   # installed to a server
                "contexts": [0]}            # used in a server channel
        async with session.post(base, json=body, headers=headers) as r:
            log.info("activity launch command /%s registered (%s)", name, r.status)
    async with session.get(base, headers=headers) as r:
        existing = await r.json() if r.status == 200 else []
    for c in existing:
        if c.get("type") == 1 and c.get("name") not in wanted:
            async with session.delete(f"{base}/{c['id']}", headers=headers) as r:
                log.info("old activity command /%s removed (%s)", c.get("name"), r.status)


async def announce(channel_id: int, text: str, game: str = "wordle") -> None:
    """Post a line in the channel the game was opened in, with a Play button under it.
    Best-effort: needs the bot in the server with permission to post there."""
    if _client is None or not _client.is_ready():
        return
    try:
        await _client.get_partial_messageable(int(channel_id)).send(
            text, view=PlayView(game), allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        log.warning("couldn't post the activity message in %s", channel_id, exc_info=True)


async def post_view(channel_id: int, view: discord.ui.LayoutView, files=None) -> int | None:
    """Post a Components V2 message as the activities bot; returns its id."""
    if _client is None or not _client.is_ready():
        return None
    msg = await _client.get_partial_messageable(int(channel_id)).send(
        view=view, files=files or [], allowed_mentions=discord.AllowedMentions.none())
    return msg.id


async def edit_view(channel_id: int, message_id: int, view: discord.ui.LayoutView, files=None) -> None:
    """Edit a message posted with post_view. ``files`` replaces its attachments; without
    them it keeps none, so a line that loses its picture doesn't show a stale one."""
    if _client is None or not _client.is_ready():
        return
    await _client.get_partial_messageable(int(channel_id)).get_partial_message(int(message_id)).edit(
        view=view, attachments=files or [], allowed_mentions=discord.AllowedMentions.none())


async def start(session) -> None:
    global _client
    if _client is not None or not auth.configured():
        return
    try:
        await _register(session)
    except Exception:
        log.warning("couldn't register the activity launch commands", exc_info=True)
    _client = Launcher()
    asyncio.create_task(_client.start(auth._env("ACTIVITIES_BOT_TOKEN")))


async def stop() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
