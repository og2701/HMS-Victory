import time
import logging
import traceback
from datetime import datetime, timedelta, timezone
from os import getenv

import discord
from openai import AsyncOpenAI

client = AsyncOpenAI(api_key=getenv("OPENAI_TOKEN"), max_retries=5, timeout=60.0)

logger = logging.getLogger(__name__)

MODEL = "gpt-5.4-nano"
WINDOW_MINUTES = 10
MAX_MESSAGES = 300
# ~12k tokens of transcript, well under the model's window; keeps cost + latency flat
MAX_TRANSCRIPT_CHARS = 48_000
COOLDOWN_SECONDS = 30

# uid -> monotonic timestamp of last use. In-memory is fine: the worst a restart
# does is reset a 30s cooldown.
_last_use: dict[int, float] = {}

# Written for a screen reader user first: prose over formatting, no emoji (screen
# readers announce every one by name), conversations untangled rather than a
# message-by-message replay.
PLAIN_PROMPT = (
    "You summarise recent Discord chat so someone who just arrived can catch up. "
    "Your reader may be using a screen reader, so follow these rules exactly:\n"
    "- Write flowing prose in short paragraphs. No bullet points, no headers, no tables.\n"
    "- Do NOT use emoji or decorative symbols anywhere.\n"
    "- Untangle interleaved conversations: group each topic together and summarise it as "
    "a thread (who was involved, what was said, where it landed), rather than replaying "
    "messages in order.\n"
    "- Name the participants naturally, e.g. \"Alice and Bob argued about X; Bob reckoned Y.\"\n"
    "- Summarise in your own words. Only quote directly if a short quote is essential.\n"
    "- If images or links were shared, mention that briefly.\n"
    "- Skip noise: bot commands, one-off reactions, and dead-end messages that went nowhere.\n"
    "- Use British English.\n"
    "- Keep the whole summary under 250 words. If the chat was quiet, a sentence or two is fine.\n"
    "Return only the summary text, no preamble."
)


def _clean_content(msg: discord.Message) -> str:
    """One transcript line's worth of content: resolved mentions, attachment/sticker
    markers, embeds noted. Empty string means 'nothing worth summarising'."""
    parts = []
    if msg.clean_content:
        parts.append(msg.clean_content.replace("\n", " ").strip())
    for att in msg.attachments:
        kind = "image" if (att.content_type or "").startswith("image/") else "file"
        parts.append(f"[shared a {kind}]")
    if msg.stickers:
        parts.append(f"[sent a sticker: {msg.stickers[0].name}]")
    if msg.embeds and not msg.attachments and not msg.clean_content:
        parts.append("[shared a link/embed]")
    return " ".join(p for p in parts if p)


async def _build_transcript(channel, cutoff: datetime) -> tuple[str, int, int]:
    """Transcript of human messages since cutoff, oldest first.
    Returns (transcript, message_count, participant_count)."""
    lines = []
    participants = set()
    async for msg in channel.history(limit=MAX_MESSAGES, after=cutoff, oldest_first=True):
        if msg.author.bot:
            continue
        if msg.type not in (discord.MessageType.default, discord.MessageType.reply):
            continue
        content = _clean_content(msg)
        if not content:
            continue
        reply = ""
        ref = msg.reference.resolved if msg.reference else None
        if isinstance(ref, discord.Message) and not ref.author.bot:
            reply = f" (replying to {ref.author.display_name})"
        stamp = msg.created_at.astimezone(timezone.utc).strftime("%H:%M")
        lines.append(f"[{stamp}] {msg.author.display_name}{reply}: {content}")
        participants.add(msg.author.id)

    transcript = "\n".join(lines)
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        # Keep the most recent chat: that's what "what's going on" means.
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
        transcript = transcript[transcript.find("\n") + 1:]
    return transcript, len(lines), len(participants)


async def whatsgoingon(interaction: discord.Interaction):
    now = time.monotonic()
    last = _last_use.get(interaction.user.id)
    if last is not None and now - last < COOLDOWN_SECONDS:
        wait = int(COOLDOWN_SECONDS - (now - last)) + 1
        return await interaction.response.send_message(
            f"Give it a moment - you can use this again in {wait} seconds.", ephemeral=True
        )
    _last_use[interaction.user.id] = now

    await interaction.response.defer(ephemeral=True)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=WINDOW_MINUTES)
    try:
        transcript, msg_count, people = await _build_transcript(interaction.channel, cutoff)
    except discord.Forbidden:
        return await interaction.followup.send(
            "I can't read the history of this channel.", ephemeral=True
        )

    if not transcript:
        return await interaction.followup.send(
            f"It's been quiet - no chat in the last {WINDOW_MINUTES} minutes.", ephemeral=True
        )

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": PLAIN_PROMPT},
                {"role": "user", "content": (
                    f"Chat from the #{interaction.channel.name} channel over the last "
                    f"{WINDOW_MINUTES} minutes (times are UTC):\n\n{transcript}"
                )},
            ],
            max_completion_tokens=600,
        )
        summary = (response.choices[0].message.content or "").strip()
        if not summary:
            raise ValueError("empty completion")
    except Exception as e:
        logger.error(f"Error in whatsgoingon: {e}\n{traceback.format_exc()}")
        return await interaction.followup.send(
            "Something went wrong generating the summary - try again in a minute.",
            ephemeral=True,
        )

    header = (
        f"Catch-up for the last {WINDOW_MINUTES} minutes: "
        f"{msg_count} messages from {people} people.\n\n"
    )
    # Ephemeral messages cap at 2000 chars; the prompt asks for <250 words so this
    # truncation should never fire, but never fail to deliver.
    body = (header + summary)[:2000]
    await interaction.followup.send(body, ephemeral=True)
