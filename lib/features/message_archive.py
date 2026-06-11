"""Rolling message archive + bulk-delete logging.

Discord's bulk-delete event (ban purges, mod sweeps, Dyno-style purges) only carries
message IDs - content is recoverable solely from the bot's in-memory cache, which has
usually evicted anything older than a few hundred messages. So every user message is
copied into the ``message_archive`` table as it arrives (content + attachment URLs,
retained ~30 days, purged daily), and the raw bulk/single delete handlers look the IDs
up there to post a proper log with author, channel and content.

Attribution comes from the audit log: a fresh ``ban`` entry means a ban purge, a fresh
``message_bulk_delete`` entry means a mod/bot purge.
"""

import json
import logging
import time
from datetime import datetime

import discord
import pytz

from config import CHANNELS
from database import DatabaseManager

log = logging.getLogger(__name__)

# Discord's ban delete-window maxes out at 7 days, so 10 days of archive covers every
# possible ban purge with margin while keeping the retained-content footprint small.
RETENTION_DAYS = 10
_UK = pytz.timezone("Europe/London")


def archive_message(message) -> None:
    """Store one user message. Cheap single INSERT; called from on_message."""
    try:
        attachments = json.dumps([a.url for a in message.attachments]) if message.attachments else None
        DatabaseManager.execute(
            "INSERT OR REPLACE INTO message_archive (message_id, channel_id, user_id, content, attachments, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(message.id), str(message.channel.id), str(message.author.id),
             message.content or "", attachments, int(time.time())))
    except Exception:
        log.debug("message archive insert failed", exc_info=True)


def purge_old() -> int:
    """Delete archive rows past the retention window. Returns rows removed."""
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    try:
        with DatabaseManager.locked_connection() as conn:
            c = conn.cursor()
            c.execute("DELETE FROM message_archive WHERE ts < ?", (cutoff,))
            conn.commit()
            return c.rowcount
    except Exception:
        log.error("message archive purge failed", exc_info=True)
        return 0


def fetch_archived(message_ids):
    """Rows for the given ids as dicts, oldest first. Missing ids are simply absent."""
    ids = [str(m) for m in message_ids]
    if not ids:
        return []
    rows = []
    for i in range(0, len(ids), 500):       # chunk to stay under SQLite's param limit
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        rows += DatabaseManager.fetch_all(
            f"SELECT message_id, channel_id, user_id, content, attachments, ts "
            f"FROM message_archive WHERE message_id IN ({ph})", tuple(chunk)) or []
    rows.sort(key=lambda r: int(r[5]))
    return [dict(zip(("message_id", "channel_id", "user_id", "content", "attachments", "ts"), r))
            for r in rows]


async def _attribute(guild):
    """Best-effort 'who did this': a ban or bulk-delete audit entry from the last ~15s."""
    try:
        now = datetime.now(pytz.utc)
        async for entry in guild.audit_logs(limit=6):
            if (now - entry.created_at).total_seconds() > 15:
                break
            if entry.action == discord.AuditLogAction.ban:
                return f"ban of {entry.target.mention} by {entry.user.mention}"
            if entry.action == discord.AuditLogAction.message_bulk_delete:
                return f"purge by {entry.user.mention}"
    except Exception:
        log.debug("bulk delete attribution failed", exc_info=True)
    return None


def _format_line(row, client):
    user = client.get_user(int(row["user_id"]))
    who = discord.utils.escape_markdown(user.display_name) if user else f"<@{row['user_id']}>"
    when = datetime.fromtimestamp(row["ts"], _UK).strftime("%d %b %H:%M")
    content = (row["content"] or "").replace("\n", " ").strip()
    if not content and row["attachments"]:
        content = "(attachment only)"
    if len(content) > 180:
        content = content[:177] + "…"
    line = f"`{when}` **{who}**: {content}"
    if row["attachments"]:
        try:
            urls = json.loads(row["attachments"])
            line += "".join(f"\n-# 📎 {u}" for u in urls[:3])
        except Exception:
            pass
    return line


async def handle_raw_bulk_delete(client, payload) -> None:
    """Log a bulk delete: recover content from the archive, attribute via audit log."""
    log_channel = client.get_channel(CHANNELS.LOGS)
    if log_channel is None:
        return
    channel = client.get_channel(payload.channel_id)
    ch_label = channel.mention if channel else f"<#{payload.channel_id}>"

    rows = fetch_archived(payload.message_ids)
    guild = client.get_guild(payload.guild_id) if payload.guild_id else None
    cause = await _attribute(guild) if guild else None

    header = f"**{len(payload.message_ids)}** messages bulk-deleted in {ch_label}."
    if cause:
        header += f"\nLikely cause: {cause}."
    missing = len(payload.message_ids) - len(rows)
    if missing > 0:
        header += f"\n-# {missing} message(s) predate the {RETENTION_DAYS}-day archive (or were bot messages) and can't be recovered."

    lines = [_format_line(r, client) for r in rows]
    # Chunk into embeds (4096-char description cap); first embed carries the header.
    chunks, cur = [], header
    for line in lines:
        if len(cur) + len(line) + 1 > 3900:
            chunks.append(cur)
            cur = line
        else:
            cur += "\n" + line
    chunks.append(cur)
    for i, desc in enumerate(chunks[:10]):
        embed = discord.Embed(
            title="Bulk Delete" if i == 0 else f"Bulk Delete (cont. {i + 1})",
            description=desc, color=discord.Color.dark_red())
        await log_channel.send(embed=embed)
    if len(chunks) > 10:
        await log_channel.send(embed=discord.Embed(
            title="Bulk Delete (truncated)",
            description=f"…and {len(chunks) - 10} more pages of recovered messages not shown.",
            color=discord.Color.dark_red()))


async def handle_raw_single_delete(client, payload) -> None:
    """Fallback for single deletes of UNCACHED messages (cached ones are handled by the
    richer on_message_delete with its image snapshot - skip those to avoid double logs)."""
    if payload.cached_message is not None:
        return
    rows = fetch_archived([payload.message_id])
    if not rows:
        return
    row = rows[0]
    log_channel = client.get_channel(CHANNELS.LOGS)
    if log_channel is None:
        return
    channel = client.get_channel(payload.channel_id)
    ch_label = channel.mention if channel else f"<#{payload.channel_id}>"
    embed = discord.Embed(
        title="Message Deleted (recovered from archive)",
        description=f"Message by <@{row['user_id']}> deleted in {ch_label}.\n\n{_format_line(row, client)}",
        color=discord.Color.red())
    await log_channel.send(embed=embed)
