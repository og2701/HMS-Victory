import asyncio
import base64
import logging
import os
import re

import aiohttp
import discord

from config import *

log = logging.getLogger(__name__)

GEMINI_API_KEY = os.getenv("GEMINI_TOKEN")
GEMINI_MODEL = "gemini-2.5-flash-lite"
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

IMAGE_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 5 * 1024 * 1024
TICKET_TOOL_BOT_ID = 557628352828014614

STAFF_ROLE_IDS = {
    ROLES.DEPUTY_PM,
    ROLES.MINISTER,
    ROLES.CABINET,
    ROLES.BORDER_FORCE,
    ROLES.PCSO,
}


async def _identify_ticket_creator(channel):
    """Find the ticket creator's user ID using multiple strategies."""
    if channel.topic:
        m = re.search(r"User ID:\s*(\d{10,25})", channel.topic)
        if m:
            return int(m.group(1))

    async for msg in channel.history(limit=10, oldest_first=True):
        if msg.author.id != TICKET_TOOL_BOT_ID:
            continue
        sources = [msg.content or ""]
        for embed in msg.embeds:
            sources.append(embed.description or "")
            sources.append(embed.title or "")
            for field in embed.fields:
                sources.append(field.value or "")
        for source in sources:
            mentions = re.findall(r"<@!?(\d{10,25})>", source)
            if mentions:
                return int(mentions[0])

    return None


async def _fetch_image_part(attachment):
    ext = (attachment.filename or "").rsplit(".", 1)[-1].lower()
    mime = IMAGE_MIME_TYPES.get(ext)
    if not mime:
        return None
    if attachment.size and attachment.size > MAX_IMAGE_BYTES:
        return None
    try:
        data = await attachment.read()
    except (discord.HTTPException, discord.NotFound):
        return None
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.standard_b64encode(data).decode("ascii"),
        }
    }


async def _call_gemini(session, system_prompt, user_parts):
    if not GEMINI_API_KEY:
        return None, "GEMINI_TOKEN not configured"

    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": user_parts}],
        "generationConfig": {
            "temperature": 0.4,
            "maxOutputTokens": 600,
        },
    }
    try:
        async with session.post(
            GEMINI_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            body = await resp.json()
            if resp.status != 200:
                return None, f"HTTP {resp.status}: {body}"
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        return None, f"request failed: {exc}"

    try:
        return body["candidates"][0]["content"]["parts"][0]["text"].strip(), None
    except (KeyError, IndexError, TypeError):
        return None, f"unexpected response: {body}"


def _log_task_exception(task):
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        log.exception("Ticket summary task failed", exc_info=exc)


def handle_ticket_closed_message(bot, message):
    if not message.embeds:
        return
    embed = message.embeds[0]
    if not (
        embed.description
        and "Ticket Closed by" in embed.description
        and message.channel.category_id == CATEGORIES.TICKETS
    ):
        return

    task = asyncio.create_task(_summarise_closed_ticket(bot, message))
    task.add_done_callback(_log_task_exception)


async def _summarise_closed_ticket(bot, message):
    embed = message.embeds[0]
    ticket_creator_id = await _identify_ticket_creator(message.channel)
    ticket_creator_name = None
    closer_match = re.search(r"Ticket Closed by\s+<@!?(\d{10,25})>", embed.description)
    closer_id = int(closer_match.group(1)) if closer_match else None
    closer_name = None

    transcript_lines = []
    users_involved = []
    seen_users = set()
    image_parts = []

    async for msg in message.channel.history(limit=1000, oldest_first=True):
        author = msg.author
        if author.bot:
            role_tag = "Bot"
        elif ticket_creator_id and author.id == ticket_creator_id:
            role_tag = "Ticket Creator"
            ticket_creator_name = author.display_name
        elif hasattr(author, "roles") and any(r.id in STAFF_ROLE_IDS for r in author.roles):
            role_tag = "Staff"
        else:
            role_tag = "User"

        if closer_id and author.id == closer_id:
            closer_name = author.display_name

        text = (msg.content or "").strip()

        attach_notes = []
        for attachment in msg.attachments:
            if len(image_parts) < MAX_IMAGES:
                part = await _fetch_image_part(attachment)
                if part:
                    image_parts.append(part)
                    attach_notes.append(
                        f"[screenshot #{len(image_parts)}: {attachment.filename}]"
                    )
                    continue
            attach_notes.append(f"[attachment: {attachment.filename}]")

        if attach_notes:
            text = f"{text} {' '.join(attach_notes)}".strip()

        if text:
            transcript_lines.append(f"[{role_tag}] {author.display_name}: {text}")

        if not author.bot and author.id not in seen_users:
            seen_users.add(author.id)
            users_involved.append(author.display_name)

    if not ticket_creator_name and ticket_creator_id:
        member = message.guild.get_member(ticket_creator_id) if message.guild else None
        if member:
            ticket_creator_name = member.display_name

    chat_text = "\n".join(transcript_lines) or "(no text messages)"
    creator_descriptor = ticket_creator_name or "the ticket creator (name unknown)"

    system_prompt = (
        "You are an expert community manager summarising Discord support tickets. "
        "You will receive a chat transcript between the ticket creator and server staff, "
        "followed by any screenshots that were posted in chronological order. "
        "Read the transcript AND the contents of every screenshot, then write a tight summary "
        "(maximum 4 sentences) covering: (1) the core issue or question raised, "
        "(2) what staff did or said in response, and (3) the final outcome / resolution.\n\n"
        "Hard rules:\n"
        "- Refer to people by their actual display names exactly as they appear in the transcript "
        "(e.g. 'crayfishhh', 'oggers', 'Hadidas'). NEVER use bracket tags such as "
        "[Ticket Creator], [Staff], [Bot], [User], or generic phrases like 'the staff member'/"
        "'the user' when a name is available.\n"
        "- The bracket tags in the transcript are role hints for you only; they MUST NOT appear "
        "in your output.\n"
        "- If a screenshot drives the issue (e.g. evidence of rule-breaking, a bug screenshot, "
        "a chat log), briefly say what it showed in plain language.\n"
        "- Do not greet, do not quote the transcript verbatim, do not add disclaimers or meta "
        "commentary. Output the summary text only.\n\n"
        f"Ticket opened by: {creator_descriptor}.\n"
        f"Ticket channel: #{message.channel.name}."
    )

    user_parts = [{"text": f"Transcript:\n{chat_text}\n\nSummary:"}]
    user_parts.extend(image_parts)

    session = getattr(bot, "session", None) or aiohttp.ClientSession()
    own_session = session is not getattr(bot, "session", None)
    try:
        summary, err = await _call_gemini(session, system_prompt, user_parts)
    finally:
        if own_session:
            await session.close()

    if err:
        log.warning("Ticket summary generation failed: %s", err)
        summary = f"(Failed to generate summary — {err})"

    e = discord.Embed(
        title=f"Support ticket ({message.channel.name}) summary",
        description=summary,
        color=0x00FF00,
    )
    if ticket_creator_name:
        e.add_field(name="Opened By", value=ticket_creator_name, inline=True)
    if closer_name:
        e.add_field(name="Closed By", value=closer_name, inline=True)
    if image_parts:
        e.add_field(name="Screenshots", value=str(len(image_parts)), inline=True)
    e.add_field(
        name="Users Involved",
        value=", ".join(users_involved) if users_involved else "—",
        inline=False,
    )
    e.timestamp = message.created_at

    destination_channel = bot.get_channel(CHANNELS.POLICE_STATION)
    if destination_channel:
        await destination_channel.send(embed=e)
