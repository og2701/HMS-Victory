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
from database import DatabaseManager
from lib.core.translation import _already_translated, _claim_translation, _release_translation

logger = logging.getLogger(__name__)

DEDUP_TARGET = "📝vn"          # translation_log target; the note's id is the message id
MAX_CHARS = 3800               # TextDisplay allows 4000; leave room for the header lines
SUMMARY_MIN_CHARS = 400        # roughly 20s of speech; shorter than that is its own summary
SUMMARY_PROMPT = (
    "You summarise a transcribed Discord voice note for people who can't listen to it. "
    "Write one to three short sentences in plain British English, in the third person "
    "(\"They think...\"), keeping the speaker's actual point and any question or request "
    "they made. No preamble, no bullet points, no quotes around it."
)


def _enabled() -> bool:
    return bool(getattr(config, "VOICE_NOTE_TRANSCRIBE_ENABLED", True))


def _model() -> str:
    # gpt-4o-mini-transcribe was accurate on real notes from the server where the larger
    # gpt-4o-transcribe dropped a clause, and it is the cheaper of the two.
    return str(getattr(config, "VOICE_NOTE_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"))


def _max_bytes() -> int:
    return int(getattr(config, "VOICE_NOTE_MAX_BYTES", 2 * 1024 * 1024))


def _is_audio(a) -> bool:
    ctype = (getattr(a, "content_type", None) or "").lower()
    name = (getattr(a, "filename", None) or "").lower()
    return ctype.startswith("audio/") or name.endswith((".ogg", ".mp3", ".m4a", ".wav", ".opus"))


def voice_attachment(message):
    """The audio clip on a message, if it is the size of a voice note.

    A native Discord voice message carries flags.voice and a duration, but people also
    download a note and re-send it, and that arrives as a plain "voice-message (1).ogg"
    with no flag at all. Both should get the button. The size cap is what keeps a shared
    song out: a two-minute note is around 700KB, a track is several MB.
    """
    for a in getattr(message, "attachments", None) or []:
        if not _is_audio(a):
            continue
        size = getattr(a, "size", None) or 0
        if getattr(a, "duration", None) or size <= _max_bytes():
            return a
    return None


def is_voice_note(message) -> bool:
    """Native voice message, or a small audio file that is one re-uploaded."""
    return voice_attachment(message) is not None


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


async def summarise(text: str) -> str | None:
    """A two-line gist of a long transcript, or None when it is short enough to read as is
    or the model fails. A None just means the reply shows the full words with no toggle."""
    if len(text) < SUMMARY_MIN_CHARS:
        return None
    import os
    from openai import AsyncOpenAI
    try:
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_TOKEN"), max_retries=2, timeout=30.0)
        response = await client.chat.completions.create(
            model=str(getattr(config, "VOICE_NOTE_SUMMARY_MODEL", "gpt-5.4-nano")),
            messages=[
                {"role": "system", "content": SUMMARY_PROMPT},
                {"role": "user", "content": text},
            ],
            max_completion_tokens=400,
        )
        summary = (response.choices[0].message.content or "").strip()
        return summary or None
    except Exception:
        logger.warning("voice note summary failed", exc_info=True)
        return None


def _save(message_id: int, author_mention: str, text: str, summary: str | None,
          requested_by: str, seconds: float | None) -> None:
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO voice_note_transcripts "
        "(message_id, author_mention, full_text, summary, requested_by, seconds) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (str(message_id), author_mention, text, summary, requested_by, seconds))


def _load(message_id: int):
    return DatabaseManager.fetch_one(
        "SELECT author_mention, full_text, summary, requested_by, seconds "
        "FROM voice_note_transcripts WHERE message_id = ?", (str(message_id),))


def _result_view(author_mention: str, text: str, requested_by: str,
                 seconds: float | None, summary: str | None = None,
                 show_full: bool = False, message_id: int = 0) -> discord.ui.LayoutView:
    """The transcript reply. With a summary it opens on the summary, and a button swaps to
    the full words and back; without one it is just the words."""
    text = text.strip() or "*(nothing audible)*"
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS].rstrip() + "…"
    length = f" · {int(seconds)}s" if seconds else ""
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_colour=0x5865F2)
    if summary and not show_full:
        box.add_item(discord.ui.TextDisplay(f"📝 **TL;DR** of {author_mention}'s note:\n{summary}"))
    else:
        box.add_item(discord.ui.TextDisplay(f"📝 {author_mention} said:\n{text}"))
    box.add_item(discord.ui.TextDisplay(f"-# Transcribed{length} · requested by {requested_by}"))
    if summary:
        box.add_item(discord.ui.ActionRow(ToggleButton(message_id, not show_full)))
    view.add_item(box)
    return view


class ToggleButton(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"vnt:(?P<mid>\d+):(?P<to>full|tldr)"):
    """Swaps a transcript reply between its summary and the full words. The words live in
    voice_note_transcripts, so this works across restarts and never calls the model."""

    def __init__(self, message_id: int = 0, to_full: bool = True):
        self.message_id, self.to_full = int(message_id), bool(to_full)
        super().__init__(discord.ui.Button(
            label="Full transcript" if to_full else "Back to summary",
            emoji="📜" if to_full else "📝",
            style=discord.ButtonStyle.secondary,
            custom_id=f"vnt:{self.message_id}:{'full' if to_full else 'tldr'}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["mid"]), match["to"] == "full")

    async def callback(self, interaction: discord.Interaction) -> None:
        row = _load(self.message_id)
        if row is None:
            await interaction.response.send_message(
                "I've lost the transcript for that one, sorry.", ephemeral=True)
            return
        author, text, summary, requested_by, seconds = row
        await interaction.response.edit_message(
            view=_result_view(author, text, requested_by, seconds, summary,
                              show_full=self.to_full, message_id=self.message_id),
            allowed_mentions=discord.AllowedMentions.none())


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
            summary = await summarise(text)
            seconds = getattr(att, "duration", None)
            requested_by = interaction.user.display_name
            if summary:
                _save(self.message_id, note.author.mention, text, summary,
                      requested_by, seconds)
            await interaction.message.edit(
                view=_result_view(note.author.mention, text, requested_by, seconds,
                                  summary, message_id=self.message_id),
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
