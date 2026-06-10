import asyncio
import base64
import logging
import re

import discord

from config import *
from lib.core.gemini import gemini_generate

log = logging.getLogger(__name__)

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


def _clean_discord_text(text, guild):
    """Resolve raw <@id>/<#id>/<@&id> tokens to readable names (for embed/snapshot
    text, which has no clean_content equivalent)."""
    if not text:
        return ""

    def _member(m):
        member = guild.get_member(int(m.group(1))) if guild else None
        return f"@{member.display_name}" if member else "@unknown-user"

    def _channel(m):
        channel = guild.get_channel_or_thread(int(m.group(1))) if guild else None
        return f"#{channel.name}" if channel else "#unknown-channel"

    def _role(m):
        role = guild.get_role(int(m.group(1))) if guild else None
        return f"@{role.name}" if role else "@unknown-role"

    text = re.sub(r"<@!?(\d{10,25})>", _member, text)
    text = re.sub(r"<#(\d{10,25})>", _channel, text)
    text = re.sub(r"<@&(\d{10,25})>", _role, text)
    return text


def _embed_as_text(embed, guild):
    """Flatten an embed's title/description/fields into one transcript fragment."""
    parts = []
    if embed.title:
        parts.append(embed.title)
    if embed.description:
        parts.append(embed.description)
    for field in embed.fields:
        parts.append(f"{field.name}: {field.value}")
    text = " | ".join(p.strip() for p in parts if p and p.strip())
    return _clean_discord_text(text, guild)[:500]


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

    # Seed with the creator so an opener who never typed in the ticket still
    # shows up in Users Involved (and as the prompt's "opened by").
    if ticket_creator_id and message.guild:
        member = message.guild.get_member(ticket_creator_id)
        if member:
            ticket_creator_name = member.display_name
            seen_users.add(ticket_creator_id)
            users_involved.append(member.display_name)

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

        # clean_content resolves <@id>/<#id> mention tokens to readable names;
        # the extra pass catches tokens it leaves raw (e.g. pasted non-pinging mentions)
        text = _clean_discord_text((msg.clean_content or "").strip(), msg.guild)
        attachments = list(msg.attachments)

        # Forwarded messages carry their text/attachments/embeds in snapshots, not content
        for snapshot in getattr(msg, "message_snapshots", None) or []:
            snap_text = _clean_discord_text((snapshot.content or "").strip(), msg.guild)
            for snap_embed in snapshot.embeds:
                if str(snap_embed.type or "rich") != "rich":
                    continue
                embed_text = _embed_as_text(snap_embed, msg.guild)
                if embed_text:
                    snap_text = f"{snap_text} [embed: {embed_text}]".strip()
            if snap_text:
                text = f"{text} [forwarded message: {snap_text}]".strip()
            attachments.extend(snapshot.attachments)

        # Embeds (Ticket Tool notices, bot reports) are invisible via content —
        # flatten them into the transcript. Only 'rich' (authored) embeds: auto
        # link previews would read as if the member said the page's title text.
        for msg_embed in msg.embeds:
            if str(msg_embed.type or "rich") != "rich":
                continue
            embed_text = _embed_as_text(msg_embed, msg.guild)
            if embed_text:
                text = f"{text} [embed: {embed_text}]".strip()

        attach_notes = []
        for attachment in attachments:
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

    chat_text = "\n".join(transcript_lines) or "(no text messages)"
    creator_descriptor = ticket_creator_name or "the ticket creator (name unknown)"

    system_prompt = (
        "You are an expert community manager summarising Discord support tickets. "
        "You will receive a chat transcript between the ticket creator and server staff, "
        "followed by any screenshots that were posted in chronological order. "
        "Read the transcript AND the contents of every screenshot, then write a tight summary "
        "(maximum 4 sentences) covering: (1) the core issue or question raised, "
        "(2) what staff did or said in response, and (3) the final outcome / resolution.\n\n"
        "How to read the transcript:\n"
        "- [Bot] lines from 'HMS Victory' that start '@__name__ in *channel* <t:...>:' (the "
        "<t:...> token is a timestamp), usually followed on the next line by '> quoted text', "
        "are quotes of another member's message from elsewhere in the server, posted into the "
        "ticket as context or evidence. Attribute the quoted words to the quoted member (not "
        "the bot, not whoever posted the link) — they often state the actual reason the ticket "
        "exists. Names wrapped in @__...__ are plain member names: drop the @ and underscores "
        "when referring to them.\n"
        "- '[forwarded message: ...]' and '[embed: ...]' fragments are content carried inside "
        "that message. Treat boilerplate ticket-bot embeds (welcome/close notices) as noise.\n"
        "- The ticket creator may never type inside the ticket at all; the reason can live "
        "entirely in quoted/forwarded messages or screenshots. Never invent or assume what "
        "someone said.\n\n"
        "Hard rules:\n"
        "- Refer to people by their actual display names exactly as they appear in the transcript "
        "(e.g. 'crayfishhh', 'oggers', 'Hadidas'). NEVER use bracket tags such as "
        "[Ticket Creator], [Staff], [Bot], [User], or generic phrases like 'the staff member'/"
        "'the user' when a name is available.\n"
        "- The bracket tags in the transcript are role hints for you only; they MUST NOT appear "
        "in your output.\n"
        "- Be specific about the nature of the issue when the transcript is specific: name the "
        "exact concern, rule, or request rather than a vague paraphrase like 'a user's "
        "behaviour' or 'an issue'. Use only specifics that actually appear in this transcript "
        "or its screenshots.\n"
        "- If the matter was handled outside the ticket, say so plainly, naming where only if "
        "the transcript states it. Do not pad with empty closing sentences like 'the ticket "
        "was closed with the understanding that the situation had been resolved'.\n"
        "- If a screenshot drives the issue (e.g. evidence of rule-breaking, a bug screenshot, "
        "a chat log), briefly say what it showed in plain language.\n"
        "- Do not greet, do not quote the transcript verbatim, do not add disclaimers or meta "
        "commentary. Output the summary text only.\n\n"
        f"Ticket opened by: {creator_descriptor}.\n"
        f"Ticket channel: #{message.channel.name}."
    )

    user_parts = [{"text": f"Transcript:\n{chat_text}\n\nSummary:"}]
    user_parts.extend(image_parts)

    summary, err = await gemini_generate(
        getattr(bot, "session", None),
        system_prompt,
        user_parts,
        temperature=0.2,  # factual recap, keep it grounded
    )

    if err:
        log.warning("Ticket summary generation failed: %s", err)
        summary = f"(Failed to generate summary - {err})"

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
        value=", ".join(users_involved) if users_involved else "-",
        inline=False,
    )
    e.timestamp = message.created_at

    destination_channel = bot.get_channel(CHANNELS.POLICE_STATION)
    if destination_channel:
        from lib.features.ukp_rewards import TicketRewardView
        view = TicketRewardView(ticket_creator_id, ticket_creator_name) if ticket_creator_id else None
        sent = await destination_channel.send(embed=e, view=view)
        if view:
            try:
                bot.add_view(view, message_id=sent.id)
            except Exception:
                pass
