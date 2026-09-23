"""Walk the server's whole history again, recording who reacted to what.

The live hook only sees reactions added since it was deployed. Discord does keep the
current reactions on every message, though, so this walks each channel newest to
oldest and, for every message carrying any, asks who is on each emoji.

    python scripts/chronicle_reactions_backfill.py                    # everything
    python scripts/chronicle_reactions_backfill.py --channel 123 456  # just these
    python scripts/chronicle_reactions_backfill.py --concurrency 6

What it stores, per message page:

* ``reaction_counts`` - Discord's own per-emoji totals, exact even for users who have
  since left the server.
* ``reactions`` rows, ``source='backfill'``, one per user per emoji (super reactions
  flagged ``burst``). Their ``ts`` is the MESSAGE's time: Discord doesn't record when a
  reaction was added. They reflect current state, so a reaction added and later removed
  is simply not there. Anything the live hook already recorded is skipped.
* ``messages.msg_type`` for every message it passes, since it's already holding them,
  and any message the chronicle somehow lacks.

Cost is one request per history page plus one per emoji per reacted message (more for
an emoji with over a hundred reactors). History and reaction lookups use separate rate
limit buckets, so the next page is fetched while the current one's reactions are.
Channels run in parallel inside one process and one lock, for the same reasons as
chronicle_backfill.py. Progress is saved per page; rerun to resume.
"""

import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402
import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from config import GUILD_ID  # noqa: E402
from lib.chronicle import recorder  # noqa: E402
from lib.chronicle.db import ChronicleDB  # noqa: E402
from lib.chronicle.walk import RunLock, collect_channels  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chronicle-reactions")

PAGE = 100
PREFETCH = 3          # history pages buffered ahead of the reaction lookups
USER_FETCHES = 4      # concurrent reaction-user lookups per channel (one bucket anyway)
ATTEMPTS = 6
PROGRESS_EVERY = 25_000

_totals = {"messages": 0, "reacted": 0, "rows": 0, "requests": 0}


# ------------------------------------------------------------------------- storage

def _progress(channel_id):
    return ChronicleDB.fetch_one(
        "SELECT oldest_seen, complete FROM reaction_progress WHERE channel_id = ?", (channel_id,))


def _save_progress(channel, oldest, n_messages, n_reactions, complete=False):
    ChronicleDB.execute(
        "INSERT INTO reaction_progress (channel_id, channel_name, oldest_seen, n_messages, "
        "n_reactions, complete, updated_ts) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET channel_name = excluded.channel_name, "
        "oldest_seen = COALESCE(MIN(oldest_seen, excluded.oldest_seen), excluded.oldest_seen, oldest_seen), "
        "n_messages = n_messages + excluded.n_messages, "
        "n_reactions = n_reactions + excluded.n_reactions, "
        "complete = excluded.complete, updated_ts = excluded.updated_ts",
        (channel.id, getattr(channel, "name", None), oldest, n_messages, n_reactions,
         int(complete), int(time.time())))


def _already_recorded(message_ids):
    """(message_id, user_id, emoji, emoji_id, burst) of every reaction already held -
    the live hook's rows and any earlier pass of this script - so neither doubles up."""
    ids = list(message_ids)
    held = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        for row in ChronicleDB.fetch_all(
                f"SELECT message_id, user_id, emoji, emoji_id, burst FROM reactions "
                f"WHERE action = 'add' AND message_id IN ({ph})", tuple(chunk)):
            held.add(tuple(row))
    return held


def _write(rows):
    # stamp=False: this is a separate process, and its writes say nothing about
    # whether the bot itself was hearing the gateway.
    if rows:
        recorder._write(rows, stamp=False)


# --------------------------------------------------------------------------- walking

async def _users(reaction, burst):
    """Everyone on one emoji, retried through 5xx blips."""
    kind = discord.ReactionType.burst if burst else discord.ReactionType.normal
    for attempt in range(1, ATTEMPTS + 1):
        try:
            _totals["requests"] += 1
            return [u.id async for u in reaction.users(limit=None, type=kind)]
        except (discord.Forbidden, discord.NotFound):
            return []
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == ATTEMPTS:
                raise
            await asyncio.sleep(min(60, 2 ** attempt))


async def _process_page(channel, page, gate):
    """Everything one page of history contributes. Returns (rows, reaction rows)."""
    ids = [m.id for m in page]
    rows = []

    # Message type for every message, and any message the chronicle lacks outright.
    stored = await asyncio.to_thread(recorder.stored_message_ids, ids)
    for m in page:
        if m.id in stored:
            rows.append(("UPDATE messages SET msg_type = ? WHERE message_id = ? AND msg_type IS NULL",
                         (m.type.name, m.id)))
        else:
            rows += recorder.message_rows(m, source="backfill")

    reacted = [m for m in page if m.reactions]
    if not reacted:
        return rows, 0
    held = await asyncio.to_thread(_already_recorded, [m.id for m in reacted])
    now = int(time.time())

    async def one(m, reaction):
        emoji = reaction.emoji
        name = getattr(emoji, "name", None) or str(emoji)
        eid = getattr(emoji, "id", None)
        animated = int(bool(getattr(emoji, "animated", False)))
        out = [(recorder.REACTION_COUNT_SQL, (
            m.id, str(eid) if eid else name, name, eid, reaction.count,
            getattr(reaction, "burst_count", 0) or 0, now))]
        ts = int(m.created_at.timestamp())
        guild_id = m.guild.id if m.guild else None
        for burst, n in ((0, getattr(reaction, "normal_count", reaction.count)),
                         (1, getattr(reaction, "burst_count", 0))):
            if not n:
                continue
            async with gate:
                users = await _users(reaction, bool(burst))
            for uid in users:
                if (m.id, uid, name, eid, burst) in held:
                    continue
                out.append((recorder.BACKFILL_REACTION_SQL, (
                    m.id, channel.id, guild_id, uid, m.author.id, name, eid, animated,
                    ts, burst)))
        return out

    results = await asyncio.gather(*(one(m, r) for m in reacted for r in m.reactions))
    n_reactions = 0
    for out in results:
        rows += out
        n_reactions += sum(1 for sql, _ in out if sql is recorder.BACKFILL_REACTION_SQL)
    # Live rows for these messages may lack the author (the remove event never has it).
    for m in reacted:
        rows.append(("UPDATE reactions SET author_id = ? WHERE message_id = ? AND author_id IS NULL",
                     (m.author.id, m.id)))
    _totals["reacted"] += len(reacted)
    return rows, n_reactions


async def _pages(channel, before):
    """History, a page at a time, newest to oldest."""
    page = []
    async for m in channel.history(limit=None, before=before, oldest_first=False):
        page.append(m)
        if len(page) == PAGE:
            yield page
            page = []
    if page:
        yield page


async def _walk_once(channel, resume):
    """One pass. Returns True when the channel's bottom was reached."""
    before = None
    if resume:
        row = await asyncio.to_thread(_progress, channel.id)
        if row and row[1]:
            return True
        if row and row[0]:
            before = discord.Object(id=row[0])

    queue: asyncio.Queue = asyncio.Queue(maxsize=PREFETCH)
    DONE = object()

    async def produce():
        try:
            async for page in _pages(channel, before):
                await queue.put(page)
        finally:
            await queue.put(DONE)

    producer = asyncio.create_task(produce())
    gate = asyncio.Semaphore(USER_FETCHES)
    try:
        while True:
            page = await queue.get()
            if page is DONE:
                break
            rows, n_reactions = await _process_page(channel, page, gate)
            await asyncio.to_thread(_write, rows)
            await asyncio.to_thread(_save_progress, channel, page[-1].id, len(page), n_reactions)
            before_total = _totals["messages"]
            _totals["messages"] += len(page)
            _totals["rows"] += n_reactions
            if before_total // PROGRESS_EVERY != _totals["messages"] // PROGRESS_EVERY:
                log.info("progress: %s messages walked, %s reacted to, %s reaction rows, "
                         "%s user lookups", f"{_totals['messages']:,}", f"{_totals['reacted']:,}",
                         f"{_totals['rows']:,}", f"{_totals['requests']:,}")
        await producer          # surfaces a history error, if that's what ended it
    finally:
        producer.cancel()
    await asyncio.to_thread(_save_progress, channel, None, 0, 0, True)
    return True


async def walk_channel(channel, resume=True):
    name = getattr(channel, "name", channel.id)
    for attempt in range(1, ATTEMPTS + 1):
        try:
            await _walk_once(channel, resume or attempt > 1)
            return
        except (discord.Forbidden, discord.NotFound):
            log.warning("#%s: no access or gone, skipping", name)
            return
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt == ATTEMPTS:
                log.error("#%s: giving up after %d attempts (%r) - rerun to resume", name, ATTEMPTS, e)
                return
            delay = min(60, 2 ** attempt)
            log.warning("#%s: Discord blipped (%r), resuming in %ds", name, e, delay)
            await asyncio.sleep(delay)
        except Exception:
            log.error("#%s failed - progress is saved, rerun to resume", name, exc_info=True)
            return


def _size_order(channels):
    """Biggest first, so the channel that takes longest starts immediately rather than
    being left as a lone straggler at the end."""
    sizes = dict(ChronicleDB.fetch_all("SELECT channel_id, n_messages FROM backfill_progress"))
    return sorted(channels, key=lambda c: -(sizes.get(c.id) or 0))


async def run(args):
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("DISCORD_TOKEN missing from the environment/.env")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    started = time.time()

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(GUILD_ID)
            if guild is None:
                log.error("guild %s not visible to this token", GUILD_ID)
                return
            channels = await collect_channels(guild, set(args.channel or []), True)
            channels = await asyncio.to_thread(_size_order, channels)
            log.info("walking %d channels for reactions, %d at a time", len(channels),
                     args.concurrency)
            gate = asyncio.Semaphore(args.concurrency)

            async def one(channel):
                async with gate:
                    await walk_channel(channel, args.resume)

            await asyncio.gather(*(one(c) for c in channels))
            log.info("reactions backfill finished in %.1f hours: %s messages walked, %s "
                     "reacted to, %s reaction rows added, %s user lookups",
                     (time.time() - started) / 3600, f"{_totals['messages']:,}",
                     f"{_totals['reacted']:,}", f"{_totals['rows']:,}", f"{_totals['requests']:,}")
        except Exception:
            log.error("reactions backfill aborted", exc_info=True)
        finally:
            ChronicleDB.checkpoint()
            await client.close()

    await client.start(token)


def main():
    ap = argparse.ArgumentParser(description="Backfill who reacted to what into chronicle.db")
    ap.add_argument("--channel", nargs="*", type=int, help="only these channel ids")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="channels walked at once (default 6)")
    ap.add_argument("--no-resume", dest="resume", action="store_false", default=True,
                    help="re-walk channels already marked complete (rows are still deduped)")
    args = ap.parse_args()
    with RunLock():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
