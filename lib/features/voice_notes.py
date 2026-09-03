"""Transcribe Discord voice notes on request.

A voice note is an accessibility gap: anyone on mute, at work, or deaf is simply left out
of that message. So when one is posted the bot replies with a single button, and pressing
it puts the words underneath. Anyone can press it - the point is that the person who needs
the transcript is rarely the person who sent the note.

It is a button rather than automatic because most notes are never going to be read by
anybody who cannot hear them, and transcribing every one of them is spend for nothing.
Each note is transcribed at most once: the first press claims it in translation_log (the
same table the flag translations use), and later presses just see the result.

The button is a DynamicItem whose custom_id carries the channel and message id, so it keeps
working across restarts with no state held anywhere.
"""

import io
import logging

import discord

import config
from lib.core.constants import TRANSLATION_BLACKLIST_CHANNELS
from lib.core.translation import _already_translated, _claim_translation, _release_translation

logger = logging.getLogger(__name__)

DEDUP_TARGET = "📝vn"          # translation_log target; the note's id is the message id
MAX_CHARS = 3800               # TextDisplay allows 4000; leave room for the header lines


def _enabled() -> bool:
    return bool(getattr(config, "VOICE_NOTE_TRANSCRIBE_ENABLED", True))


def _model() -> str:
    # gpt-4o-mini-transcribe was accurate on real notes from the server where the larger
    # gpt-4o-transcribe dropped a clause, and it is the cheaper of the two.
    return str(getattr(config, "VOICE_NOTE_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"))


def is_voice_note(message) -> bool:
    """A Discord voice message: the flag is set and there is an audio attachment."""
    if not getattr(getattr(message, "flags", None), "voice", False):
        return False
    return any(getattr(a, "duration", None) or (a.content_type or "").startswith("audio/")
               for a in (getattr(message, "attachments", None) or []))


def voice_attachment(message):
    for a in getattr(message, "attachments", None) or []:
        if getattr(a, "duration", None) or (a.content_type or "").startswith("audio/"):
            return a
    return None


async def transcribe(data: bytes, filename: str) -> str:
    """The words in an audio clip. Raises on failure so the caller can release its claim."""
    import os
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=os.getenv("OPENAI_TOKEN"), max_retries=2, timeout=60.0)
    f = io.BytesIO(data)
    f.name = filename or "voice-message.ogg"
    result = await client.audio.transcriptions.create(model=_model(), file=f)
    return (result.text or "").strip()


def _offer_view(channel_id: int, message_id: int) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.ActionRow(TranscribeButton(channel_id, message_id)))
    return view


def _result_view(author_mention: str, text: str, requested_by: str,
                 seconds: float | None) -> discord.ui.LayoutView:
    text = text.strip() or "*(nothing audible)*"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip() + "…"
    length = f" · {int(seconds)}s" if seconds else ""
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_colour=0x5865F2)
    box.add_item(discord.ui.TextDisplay(f"📝 {author_mention} said:\n{text}"))
    box.add_item(discord.ui.TextDisplay(f"-# Transcribed{length} · requested by {requested_by}"))
    view.add_item(box)
    return view


class TranscribeButton(discord.ui.DynamicItem[discord.ui.Button],
                       template=r"vn:(?P<cid>\d+):(?P<mid>\d+)"):
    """The button on the reply. Carries the note's location, needs no other state."""

    def __init__(self, channel_id: int = 0, message_id: int = 0):
        self.channel_id, self.message_id = int(channel_id), int(message_id)
        super().__init__(discord.ui.Button(
            label="Transcribe this voice note", emoji="📝",
            style=discord.ButtonStyle.secondary,
            custom_id=f"vn:{self.channel_id}:{self.message_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["cid"]), int(match["mid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _enabled():
            await interaction.response.send_message("Transcription is switched off.",
                                                    ephemeral=True)
            return
        # Claim before any await so two people pressing together cannot both pay for it.
        if _already_translated(self.message_id, DEDUP_TARGET) or \
                not _claim_translation(self.message_id, DEDUP_TARGET):
            await interaction.response.send_message(
                "That one's already been transcribed - it's on its way.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            channel = interaction.client.get_channel(self.channel_id) \
                or await interaction.client.fetch_channel(self.channel_id)
            note = await channel.fetch_message(self.message_id)
            att = voice_attachment(note)
            if att is None:
                raise RuntimeError("no audio attachment on the message")
            text = await transcribe(await att.read(), att.filename)
            await interaction.message.edit(
                view=_result_view(note.author.mention, text,
                                  interaction.user.display_name,
                                  getattr(att, "duration", None)),
                allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            # Hand the claim back so the next press can try again rather than being told
            # it is done when it never happened.
            _release_translation(self.message_id, DEDUP_TARGET)
            logger.warning("voice note transcription failed for %s", self.message_id,
                           exc_info=True)
            try:
                await interaction.followup.send(
                    "Couldn't transcribe that one - give it another go in a moment.",
                    ephemeral=True)
            except Exception:
                pass


async def offer_transcription(client, message) -> bool:
    """Reply to a freshly posted voice note with the button. Called from on_message."""
    if not _enabled() or getattr(message.author, "bot", False):
        return False
    if message.guild is None or message.channel.id in TRANSLATION_BLACKLIST_CHANNELS:
        return False
    if not is_voice_note(message):
        return False
    try:
        await message.reply(view=_offer_view(message.channel.id, message.id),
                            mention_author=False)
        return True
    except discord.HTTPException as e:
        logger.warning("could not offer transcription on %s: %s", message.id, e)
        return False
