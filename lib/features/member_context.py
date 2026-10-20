"""Bounded, attributable chat evidence and image payloads for member writing commands."""

import asyncio
import base64
import json
import logging
import mimetypes
import re
from datetime import datetime, timedelta, timezone
import os
import sqlite3
import types
from io import BytesIO

from discord import Thread

from config import CHRONICLE_DB_FILE

HISTORY_DAYS = 30
TARGET_MESSAGE_LIMIT = 80
CONTEXT_MESSAGE_LIMIT = 12
HISTORY_CHAR_LIMIT = 48000
IMAGE_LIMIT = 4
IMAGE_BYTE_LIMIT = 8 * 1024 * 1024
# A month of the busiest poster in #general is ~11k rows, which loads in well under a second.
CHRONICLE_ROW_LIMIT = 20000
RECENT_KEEP = 20       # the latest messages always make it in, for what's going on right now
QUIET_THRESHOLD = 30   # fewer usable messages than this here, and other public channels top them up
CHRONICLE_TIMEOUT = 10

logger = logging.getLogger(__name__)
_FILLER = {"lol", "lmao", "lmfao", "ok", "okay", "yeah", "yep", "haha", "hahaha", "xd", "kk"}
_DECORATION = re.compile(r"https?://\S+|<a?:\w+:\d+>|<[@#][!&]?\d+>")
# The "not X, it's Y" reframe both writers keep reaching for, even when told not to.
REFRAME = re.compile(
    r"(?:\bnot|n't)\s+(?:just\s+|merely\s+|simply\s+|only\s+)?[^.!?\n,;]{1,80}[,;.:]\s*(?:but\s+)?"
    r"(?:you|he|she|they|it|that|this|i|we)(?:'re|'s|'m|\s+are|\s+is|\s+was|\s+were|\s+am|\s+did)?\b",
    re.IGNORECASE,
)


def undashed(text):
    """House style has no em or en dashes."""
    return re.sub(r"\s*[\u2014\u2013]\s*", " - ", text)


def channel_problem(interaction, source):
    """Why the requester may not quote `source` into this channel, or None when they may."""
    perms = source.permissions_for(interaction.user)
    if not (perms.view_channel and perms.read_message_history):
        return "You can only use channels you can read."
    here = interaction.permissions
    if not (here.send_messages_in_threads if isinstance(interaction.channel, Thread) else here.send_messages):
        return "You can't post in this channel, so neither can I."
    return None


def _clean_content(content):
    text = " ".join((content or "").split())
    meaningful = _DECORATION.sub("", text).strip()
    words = re.findall(r"\w+", meaningful.casefold())
    if text.startswith("/") or not any(any(c.isalpha() for c in w) for w in words):
        return None
    if words and all(word in _FILLER for word in words):
        return None
    return text[:800]


def _message_record(message, text):
    return {
        "message_id": str(message.id),
        "author_id": str(message.author.id),
        "display_name": message.author.display_name,
        "created_at": message.created_at.isoformat(),
        "text": text,
        "reply_to_message_id": str(message.reference.message_id) if message.reference else None,
        "reactions": [{"emoji": str(r.emoji), "count": r.count} for r in message.reactions[:5]],
    }


def _image_attachments(message):
    return [a for a in message.attachments if (
        (a.content_type or mimetypes.guess_type(a.filename)[0])
        in {"image/png", "image/jpeg", "image/webp", "image/gif"}
        and 0 < a.size <= IMAGE_BYTE_LIMIT
    )]


def _encode_image(data):
    # Normalise locally: bounded size, no metadata, and a still frame for animated uploads.
    from PIL import Image, ImageOps

    if len(data) > IMAGE_BYTE_LIMIT:
        raise ValueError("Member image exceeds download limit")
    with Image.open(BytesIO(data)) as original:
        if original.width * original.height > 20_000_000:
            raise ValueError("Member image exceeds pixel limit")
        image = ImageOps.exif_transpose(original).convert("RGB")
        image.thumbnail((1600, 1600))
        output = BytesIO()
        image.save(output, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


async def _load_image(attachment):
    try:
        data = await asyncio.wait_for(attachment.read(), timeout=10)
        return await asyncio.to_thread(_encode_image, data)
    except Exception:
        logger.warning("Skipping unreadable member attachment %s", attachment.id)
        return None


class _ChronicleAttachment:
    """An image the chronicle recorded. Its stored CDN link expires, so reading it refetches the message."""

    def __init__(self, channel, message_id, meta):
        self.id = f"{message_id}/{meta.get('filename')}"
        self.filename = meta.get("filename") or ""
        self.content_type = meta.get("content_type")
        self.size = int(meta.get("size") or 0)
        self._channel, self._message_id = channel, message_id

    async def read(self):
        message = await self._channel.fetch_message(self._message_id)
        return await next(a for a in message.attachments if a.filename == self.filename).read()


def _in_chunks(conn, sql, ids, *extra):
    rows = []
    for i in range(0, len(ids), 500):       # stay under SQLite's parameter limit
        chunk = ids[i:i + 500]
        rows += conn.execute(sql.format(",".join("?" * len(chunk))), (*chunk, *extra)).fetchall()
    return rows


def _score(message, replies):
    # What the chat noticed: reactions, replies from others, and anything they posted a picture with.
    return sum(r.count for r in message.reactions) + 2 * replies.get(message.id, 0) + bool(message.attachments)


def _spread(own, replies):
    """The latest messages first, then the rest of the month a day at a time, the most noticed first."""
    usable = [m for m in own if _clean_content(m.content) is not None or m.attachments]
    newest = sorted(usable, key=lambda m: m.created_at, reverse=True)
    days = {}
    for message in newest[RECENT_KEEP:]:
        days.setdefault(message.created_at.date(), []).append(message)
    for messages in days.values():
        messages.sort(key=lambda m: (-_score(m, replies), -m.created_at.timestamp()))
    queues = sorted(days.values(), key=lambda messages: -_score(messages[0], replies))
    spread = []
    while any(queues):
        spread.extend(queue.pop(0) for queue in queues if queue)
    return newest[:RECENT_KEEP] + spread


def _chronicle_messages(channel, target_id, cutoff, others):
    """Context first, then the target's live messages in priority order.

    `others` maps the ids of other public channels to their objects. They only count when the
    target is quiet here, and then only messages with something to say.
    """
    if not os.path.exists(CHRONICLE_DB_FILE):
        return []
    channels = {channel.id: channel, **others}
    ids = list(channels)
    columns = "message_id, channel_id, user_id, is_bot, content, ts, reply_to, msg_type, attachments"
    conn = sqlite3.connect(f"file:{CHRONICLE_DB_FILE}?mode=ro", uri=True, timeout=5)
    # The unary + keeps SQLite off idx_messages_channel_ts: walking a busy channel's whole history
    # takes seconds, while the user, reply and primary-key indexes answer in milliseconds.
    try:
        own = _in_chunks(conn, f"SELECT {columns} FROM messages WHERE +channel_id IN ({{}}) AND user_id = ? "
                               "AND ts >= ? AND deleted_ts IS NULL ORDER BY ts DESC LIMIT ?",
                         ids, target_id, int(cutoff.timestamp()), CHRONICLE_ROW_LIMIT)
        here = [row for row in own if row[1] == channel.id]
        if sum(_clean_content(row[4]) is not None or bool(row[8]) for row in here) >= QUIET_THRESHOLD:
            own = here
        else:
            own = here + [row for row in own if row[1] != channel.id and len((row[4] or "").split()) >= 3]
        if not here:
            return []
        own_ids = [row[0] for row in own]
        # Pins, forwards and thread starters carry a reference too; only real replies count.
        parent_ids = sorted({row[6] for row in own if row[6] and row[7] in ("reply", None)})
        in_reach = ",".join("?" * len(ids))
        around = _in_chunks(conn, f"SELECT {columns} FROM messages WHERE reply_to IN ({{}}) AND deleted_ts IS NULL "
                                  f"AND +channel_id IN ({in_reach}) AND +user_id != ?", own_ids, *ids, target_id)
        around += _in_chunks(conn, f"SELECT {columns} FROM messages WHERE message_id IN ({{}}) AND deleted_ts IS NULL "
                                   f"AND +channel_id IN ({in_reach}) AND +user_id != ?", parent_ids, *ids, target_id)
        # Latest action per reacting member, so removals and backfilled duplicates cancel out.
        reactions = _in_chunks(conn, "SELECT message_id, emoji, COUNT(*) FROM (SELECT message_id, emoji, action, "
                                     "MAX(id) FROM reactions WHERE message_id IN ({}) GROUP BY message_id, emoji, "
                                     "user_id) WHERE action = 'add' GROUP BY message_id, emoji", own_ids)
    finally:
        conn.close()

    counts = {}
    for message_id, emoji, count in reactions:
        counts.setdefault(message_id, []).append(types.SimpleNamespace(emoji=emoji, count=count))
    guild = getattr(channel, "guild", None)
    messages = {}
    for message_id, channel_id, user_id, is_bot, content, ts, reply_to, msg_type, attachments in [*own, *around]:
        member = guild.get_member(user_id) if guild is not None else None
        where = channels[channel_id]
        try:
            files = json.loads(attachments) if attachments else []
        except ValueError:
            files = []
        messages[message_id] = types.SimpleNamespace(
            id=message_id, content=content or "", channel=where,
            created_at=datetime.fromtimestamp(ts, timezone.utc),
            author=types.SimpleNamespace(id=user_id, bot=bool(is_bot), display_name=(
                member.display_name if member else "Unknown member")),
            reactions=sorted(counts.get(message_id, []), key=lambda r: -r.count),
            attachments=[_ChronicleAttachment(where, message_id, meta) for meta in files if isinstance(meta, dict)],
            reference=types.SimpleNamespace(message_id=reply_to, resolved=None)
            if reply_to and msg_type in ("reply", None) else None,
        )
    for message in messages.values():
        if message.reference:
            message.reference.resolved = messages.get(message.reference.message_id)
    replies = {}
    for row in around:
        if row[6] in messages:
            replies[row[6]] = replies.get(row[6], 0) + 1
    # Context comes first so the evidence loop has seen every reply before it stops taking messages.
    context = [messages[row[0]] for row in around]
    return context + _spread([messages[row[0]] for row in own], replies)


def _public_channels(channel, requester):
    """Other text channels everyone can read and the requester can too: never staff or private ones."""
    guild = getattr(channel, "guild", None)
    if guild is None or requester is None:
        return {}
    everyone = guild.default_role
    found = {}
    for other in guild.text_channels:
        if other.id == channel.id or other.is_nsfw():
            continue
        public, theirs = other.permissions_for(everyone), other.permissions_for(requester)
        if public.view_channel and public.read_message_history and theirs.view_channel and theirs.read_message_history:
            found[other.id] = other
    return found


async def _recent_messages(channel, target, cutoff, requester):
    """From the chronicle when it has the target here; otherwise a slow live history scan, newest first."""
    try:
        recorded = await asyncio.wait_for(asyncio.to_thread(
            _chronicle_messages, channel, target.id, cutoff, _public_channels(channel, requester)),
            timeout=CHRONICLE_TIMEOUT)
    except Exception:
        logger.warning("Chronicle evidence unavailable; scanning channel history", exc_info=True)
        recorded = []
    if recorded:
        for message in recorded:
            yield message
        return
    async for message in channel.history(limit=4000, after=cutoff, oldest_first=False):
        yield message


async def collect_evidence(channel, target, *, now=None, requester=None):
    """Collect a bounded recent history; images never trigger a separate history scan.

    With a requester, a target who is quiet in `channel` is topped up from public channels they can read.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=HISTORY_DAYS)
    scanned, duplicates = {}, {}
    targets, image_jobs = [], []
    used_chars = 0
    async for message in _recent_messages(channel, target, cutoff, requester):
        if message.created_at < cutoff:
            continue
        scanned[message.id] = message
        if message.author.id != target.id or message.author.bot or message.content.startswith("/"):
            continue
        text = _clean_content(message.content)
        attachments = _image_attachments(message)[:IMAGE_LIMIT - len(image_jobs)]
        if text is None and not attachments:
            continue
        key = text.casefold() if text else None
        if not attachments and key in duplicates:
            duplicates[key]["occurrences"] += 1
            continue
        record = _message_record(message, text or "")
        if message.channel.id != channel.id:
            record["channel"] = getattr(message.channel, "name", None)
        record["occurrences"] = 1
        record["image_labels"] = []
        size = len(json.dumps(record, ensure_ascii=False))
        if used_chars + size > HISTORY_CHAR_LIMIT:
            break
        used_chars += size
        targets.append(record)
        if key is not None and not attachments:
            duplicates[key] = record
        image_jobs.extend((record, attachment) for attachment in attachments)
        if len(targets) >= TARGET_MESSAGE_LIMIT:
            break

    images = []
    loaded = await asyncio.gather(*(_load_image(attachment) for _, attachment in image_jobs))
    for (record, _), data_url in zip(image_jobs, loaded):
        if data_url:
            label = f"image_{len(images) + 1}"
            record["image_labels"].append(label)
            images.append({"label": label, "message_id": record["message_id"], "data_url": data_url})
    # Broken image-only posts provide no evidence; don't encourage guessing from filenames.
    targets = [record for record in targets if record["text"] or record["image_labels"]]
    context = {}
    target_ids = {record["message_id"] for record in targets}
    reach = {channel.id} | {scanned[int(record["message_id"])].channel.id for record in targets}

    def add_reply(message, target_message_id):
        if message is None or not hasattr(message, "author"):
            return
        if (message.author.bot or message.author.id == target.id
                or message.channel.id not in reach or message.created_at < cutoff):
            return
        if len(context) >= CONTEXT_MESSAGE_LIMIT or message.id in context:
            return
        text = _clean_content(message.content)
        if text is not None:
            record = _message_record(message, text)
            record["target_message_id"] = target_message_id
            context[message.id] = record

    for record in targets:
        original = scanned[int(record["message_id"])]
        reference = original.reference
        if reference:
            parent = scanned.get(reference.message_id) or reference.resolved
            add_reply(parent, record["message_id"])
    for message in scanned.values():
        reference = message.reference
        if reference and str(reference.message_id) in target_ids:
            add_reply(message, str(reference.message_id))

    evidence = {
        "target": {"user_id": str(target.id), "display_name": target.display_name},
        "since": cutoff.isoformat(),
        "target_messages": sorted(targets, key=lambda row: row["created_at"]),
        "other_member_replies": sorted(context.values(), key=lambda row: row["created_at"]),
    }
    return evidence, images


def model_content(evidence, images, **metadata):
    content = [{"type": "text", "text": json.dumps({
        **evidence, **metadata,
    }, ensure_ascii=False)}]
    for image in images:
        content.extend([
            {"type": "text", "text": f"{image['label']}: image posted by the target in message {image['message_id']}."},
            {"type": "image_url", "image_url": {"url": image["data_url"], "detail": "high"}},
        ])
    return content
