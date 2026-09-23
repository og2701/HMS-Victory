"""Close the hole a restart or outage leaves in the chronicle.

While the bot is down it hears nothing, so every deploy used to leave a minute or two
missing, and a real outage could lose hours. On each fresh gateway session this pass
asks Discord for whatever it missed:

* **Messages.** The gateway hands every channel's ``last_message_id`` over for free in
  READY, so a channel only costs a request when it actually has something newer than
  the newest message we hold for it. Normally that's a handful of channels, not the
  thousand in the server.
* **Audit log entries** newer than the newest one stored, dedupe on Discord's own id.

Reactions, voice events and poll votes that happened inside the gap are gone: they only
ever existed as gateway events, and Discord has no history endpoint for them. Each pass
writes an ``outages`` row so stats over that window can be read as undercounts rather
than as a quiet day.

Only messages created *before* this session's READY are fetched. Everything after it
reached ``on_message`` and is already being recorded live; walking it again would race
the live writer and double up its mention and emoji rows.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import discord

from lib.chronicle import recorder
from lib.chronicle.db import ChronicleDB
from lib.chronicle.walk import LOCK_PATH

log = logging.getLogger(__name__)

# The manual backfills hold this while they run. Catching up at the same time would put
# two processes on one token's rate limit, so the catch-up waits for it instead.
BACKFILL_LOCK = LOCK_PATH
CONCURRENCY = 4
BATCH = 500
ATTEMPTS = 3
# A channel we hold nothing for is walked in full only if it's this new; anything older
# with no rows is a channel the backfill skipped, and walking all of it on every boot
# would be the backfill's job, not this one's.
NEW_CHANNEL_DAYS = 30

_running = False


def read_last_alive():
    """The last moment a chronicle write committed - where the current gap starts.
    Read before the gateway connects, since the first live write overwrites it."""
    try:
        row = ChronicleDB.fetch_one("SELECT value FROM meta WHERE key = ?",
                                    (recorder.LAST_ALIVE_KEY,))
        return int(row[0]) if row else None
    except Exception:
        log.debug("could not read last_alive_ts", exc_info=True)
        return None


def mark_alive():
    """Stamp last_alive_ts on a clean shutdown, so the next boot's gap starts exactly
    where this process stopped rather than at its last chat message."""
    recorder.enqueue("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                     (recorder.LAST_ALIVE_KEY, str(int(time.time()))))


def _newest_stored(channel_ids):
    """channel_id -> newest message id held. One index seek per channel: ts and the
    snowflake order together, and (channel_id, ts) is indexed where MAX(message_id)
    would scan every row of a two-million-message channel."""
    out = {}
    for cid in channel_ids:
        row = ChronicleDB.fetch_one(
            "SELECT message_id FROM messages WHERE channel_id = ? ORDER BY ts DESC LIMIT 1",
            (cid,))
        if row:
            out[cid] = row[0]
    return out


def _newest_audit_id():
    row = ChronicleDB.fetch_one("SELECT MAX(entry_id) FROM audit_log")
    return row[0] if row and row[0] else None


def _candidates(guild):
    """Everything with a message history the gateway told us about: text, voice and
    stage channels plus active threads. Forums hold no messages of their own."""
    out = [c for c in guild.channels
           if isinstance(c, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel))]
    out += list(guild.threads)
    return out


def plan(channels, stored, now=None):
    """Which channels need fetching, and from where. Pure, so it can be tested without
    a gateway. Returns [(channel, after_id or None)]; None means walk it in full."""
    now = now or datetime.now(timezone.utc)
    new_cutoff = now - timedelta(days=NEW_CHANNEL_DAYS)
    todo = []
    for channel in channels:
        gateway_last = getattr(channel, "last_message_id", None)
        if not gateway_last:
            continue                             # never had a message
        held = stored.get(channel.id)
        if held is not None:
            if gateway_last > held:
                todo.append((channel, held))
        elif getattr(channel, "created_at", None) and channel.created_at > new_cutoff:
            todo.append((channel, None))         # created during/around the gap
    return todo


async def _fetch_channel(channel, after_id, boundary):
    """Store what this channel missed. Returns how many messages were new."""
    after = discord.Object(id=after_id) if after_id else None
    stored = 0
    for attempt in range(1, ATTEMPTS + 1):
        pending = []
        try:
            async for message in channel.history(limit=None, after=after, before=boundary,
                                                 oldest_first=True):
                pending.append(message)
                if len(pending) >= BATCH:
                    stored += await _store(pending)
                    after = discord.Object(id=pending[-1].id)   # resume point on retry
                    pending = []
            stored += await _store(pending)
            return stored
        except (discord.Forbidden, discord.NotFound):
            return stored                        # lost access or deleted; nothing to retry
        except (discord.HTTPException, asyncio.TimeoutError) as e:
            stored += await _store(pending)
            if pending:
                after = discord.Object(id=pending[-1].id)
            if attempt == ATTEMPTS:
                log.warning("chronicle catch-up gave up on #%s: %r",
                            getattr(channel, "name", channel.id), e)
                return stored
            await asyncio.sleep(2 ** attempt)
    return stored


async def _store(messages):
    if not messages:
        return 0
    held = await asyncio.to_thread(recorder.stored_message_ids, [m.id for m in messages])
    rows = []
    fresh = 0
    for m in messages:
        if m.id in held:
            continue
        fresh += 1
        rows += recorder.message_rows(m, source="catchup")
    if rows:
        await asyncio.to_thread(recorder._write, rows)
    return fresh


async def _catch_up_audit(guild):
    newest = await asyncio.to_thread(_newest_audit_id)
    if newest is None:
        return 0                                  # never swept; that's the backfill's job
    rows = []
    try:
        async for entry in guild.audit_logs(limit=None, after=discord.Object(id=newest),
                                            oldest_first=True):
            rows.append(recorder.audit_row(entry))
    except discord.Forbidden:
        return 0
    except discord.HTTPException as e:
        log.warning("chronicle audit catch-up failed: %r", e)
    if rows:
        await asyncio.to_thread(recorder._write, rows)
    return len(rows)


async def catch_up(client, guild_id, gap_start, boundary, reason):
    """Fill whatever the chronicle missed before ``boundary`` (this session's READY).

    ``gap_start`` is only used to label the outage row - the fetch itself works from
    what is actually stored, so a wrong or missing gap_start can never cause a hole.
    """
    global _running
    if _running:
        return
    _running = True
    try:
        waited = False
        while os.path.exists(BACKFILL_LOCK):
            if not waited:
                log.info("chronicle catch-up waiting for the running backfill to finish")
                waited = True
            await asyncio.sleep(60)

        guild = client.get_guild(guild_id)
        if guild is None:
            log.warning("chronicle catch-up: guild %s not available", guild_id)
            return

        # Anything already heard live but still queued must be on disk before we ask
        # what's stored, or the dedupe misses it and its child rows double up.
        await asyncio.to_thread(recorder.flush, 30)

        channels = _candidates(guild)
        stored = await asyncio.to_thread(_newest_stored, [c.id for c in channels])
        todo = plan(channels, stored)

        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(channel, after_id):
            async with gate:
                return await _fetch_channel(channel, after_id, boundary)

        results = await asyncio.gather(*(one(c, a) for c, a in todo), return_exceptions=True)
        messages = sum(r for r in results if isinstance(r, int))
        for (channel, _), result in zip(todo, results):
            if isinstance(result, Exception):
                log.warning("chronicle catch-up failed on #%s: %r",
                            getattr(channel, "name", channel.id), result)

        audit = await _catch_up_audit(guild)

        await asyncio.to_thread(ChronicleDB.execute,
            "INSERT INTO outages (start_ts, end_ts, reason, channels_fetched, "
            "messages_recovered, audit_recovered, finished_ts) VALUES (?,?,?,?,?,?,?)",
            (gap_start, int(boundary.timestamp()), reason, len(todo), messages, audit,
             int(time.time())))
        gap = f"{int(boundary.timestamp()) - gap_start}s gap" if gap_start else "gap unknown"
        log.info("chronicle catch-up (%s, %s): %d of %d channels had news, %d messages "
                 "and %d audit entries recovered", reason, gap, len(todo), len(channels),
                 messages, audit)
    except Exception:
        log.error("chronicle catch-up failed", exc_info=True)
    finally:
        _running = False
