"""Walk the server's whole history into chronicle.db.

The live recorder only sees messages from the moment it was deployed, so anything a
year-in-review wants to say about the past has to be fetched from Discord once. This
script does that channel by channel, newest to oldest, storing progress as it goes so
an interrupted run picks up where it stopped instead of re-walking millions of messages.

    python scripts/chronicle_backfill.py                    # every readable channel
    python scripts/chronicle_backfill.py --channel 123 456  # just these
    python scripts/chronicle_backfill.py --after 2026-01-01 # only this year
    python scripts/chronicle_backfill.py --threads          # include archived threads
    python scripts/chronicle_backfill.py --reactions        # who reacted (SLOW, see below)
    python scripts/chronicle_backfill.py --resume           # skip channels marked complete

Rate limits: history pages are 100 messages per request, so a 5m-message server is on
the order of a few hours. Reaction users cost one extra request *per distinct emoji per
message*, which is why they are opt-in - on a busy server that turns hours into days.

Safe to run while the bot is live: SQLite WAL plus busy_timeout handles both writers,
and inserts are INSERT OR IGNORE keyed on message_id, so re-running never duplicates.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from config import GUILD_ID  # noqa: E402
from lib.chronicle.db import ChronicleDB  # noqa: E402
from lib.chronicle.recorder import REACTION_SQL, message_rows  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chronicle-backfill")

BATCH = 500          # messages per write transaction
PROGRESS_EVERY = 5_000


def _existing(message_ids):
    """Which of these ids are already stored, so a resumed run skips their child rows."""
    found = set()
    ids = list(message_ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        ph = ",".join("?" * len(chunk))
        rows = ChronicleDB.fetch_all(
            f"SELECT message_id FROM messages WHERE message_id IN ({ph})", tuple(chunk))
        found.update(r[0] for r in rows)
    return found


def _write(rows):
    """Bulk insert, consecutive identical statements collapsed - same shape as the
    live writer, but synchronous because the backfill has nothing to stay responsive for."""
    if not rows:
        return
    with ChronicleDB.transaction() as cur:
        run_sql, run_params = None, []
        for sql, params in rows:
            if sql != run_sql:
                if run_sql is not None:
                    cur.executemany(run_sql, run_params)
                run_sql, run_params = sql, [params]
            else:
                run_params.append(params)
        if run_sql is not None:
            cur.executemany(run_sql, run_params)


def _save_progress(channel, oldest, newest, n, complete=False):
    ChronicleDB.execute(
        "INSERT INTO backfill_progress (channel_id, channel_name, oldest_seen, newest_seen, "
        "n_messages, complete, updated_ts) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(channel_id) DO UPDATE SET channel_name=excluded.channel_name, "
        "oldest_seen=MIN(COALESCE(oldest_seen, excluded.oldest_seen), excluded.oldest_seen), "
        "newest_seen=MAX(COALESCE(newest_seen, excluded.newest_seen), excluded.newest_seen), "
        "n_messages=n_messages+excluded.n_messages, complete=excluded.complete, "
        "updated_ts=excluded.updated_ts",
        (channel.id, getattr(channel, "name", None), oldest, newest, n,
         int(complete), int(time.time())))


def _progress_row(channel_id):
    return ChronicleDB.fetch_one(
        "SELECT oldest_seen, n_messages, complete FROM backfill_progress WHERE channel_id = ?",
        (channel_id,))


async def _reaction_rows(message):
    """One add row per user per emoji. Costs a request per reaction - opt-in only."""
    rows = []
    ts = int(message.created_at.timestamp())
    for reaction in message.reactions:
        emoji = reaction.emoji
        name = getattr(emoji, "name", None) or str(emoji)
        eid = getattr(emoji, "id", None)
        animated = int(bool(getattr(emoji, "animated", False)))
        try:
            async for user in reaction.users(limit=None):
                rows.append((REACTION_SQL, (
                    message.id, message.channel.id,
                    message.guild.id if message.guild else None,
                    user.id, message.author.id, name, eid, animated, "add", ts)))
        except discord.HTTPException as e:
            log.debug("reaction users fetch failed on %s: %s", message.id, e)
    return rows


async def backfill_channel(channel, after=None, want_reactions=False, resume=True):
    row = _progress_row(channel.id)
    before = None
    if resume and row:
        oldest_seen, done, complete = row
        if complete:
            log.info("skip #%s (already complete, %s messages)", channel.name, f"{done:,}")
            return 0
        if oldest_seen:
            # Walking newest-to-oldest, so resume just below the oldest id already stored.
            before = discord.Object(id=oldest_seen)
            log.info("resuming #%s below message %s (%s already stored)",
                     channel.name, oldest_seen, f"{done:,}")

    total, pending, oldest, newest = 0, [], None, None
    try:
        async for message in channel.history(limit=None, before=before, after=after,
                                             oldest_first=False):
            oldest = message.id if oldest is None else min(oldest, message.id)
            newest = message.id if newest is None else max(newest, message.id)
            pending.append(message)
            if len(pending) >= BATCH:
                total += await _flush(pending, channel, want_reactions, oldest, newest)
                pending, oldest, newest = [], None, None
                if total and total % PROGRESS_EVERY < BATCH:
                    log.info("#%s: %s messages", channel.name, f"{total:,}")
        if pending:
            total += await _flush(pending, channel, want_reactions, oldest, newest)
        _save_progress(channel, oldest, newest, 0, complete=True)
        log.info("done #%s: %s messages", channel.name, f"{total:,}")
    except discord.Forbidden:
        log.warning("no access to #%s, skipping", getattr(channel, "name", channel.id))
    except Exception:
        log.error("backfill of #%s failed - progress is saved, rerun to resume",
                  getattr(channel, "name", channel.id), exc_info=True)
    return total


async def _flush(messages, channel, want_reactions, oldest, newest):
    already = _existing(m.id for m in messages)
    rows = []
    fresh = 0
    for m in messages:
        if m.id in already:
            continue
        fresh += 1
        rows += message_rows(m, source="backfill")
        if want_reactions and m.reactions:
            rows += await _reaction_rows(m)
    _write(rows)
    _save_progress(channel, oldest, newest, fresh)
    return fresh


def _channels(guild, only, include_threads):
    out = []
    for channel in guild.channels:
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel,
                                    discord.ForumChannel, discord.StageChannel)):
            continue
        if only and channel.id not in only:
            continue
        if isinstance(channel, discord.ForumChannel):
            out.extend(channel.threads)
            continue
        out.append(channel)
        if include_threads:
            out.extend(channel.threads)
    return out


async def run(args):
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("DISCORD_TOKEN missing from the environment/.env")

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    client = discord.Client(intents=intents)
    after = datetime.fromisoformat(args.after).replace(tzinfo=timezone.utc) if args.after else None
    started = time.time()

    @client.event
    async def on_ready():
        try:
            guild = client.get_guild(GUILD_ID)
            if guild is None:
                log.error("guild %s not visible to this token", GUILD_ID)
                return
            channels = _channels(guild, set(args.channel or []), args.threads)
            if args.threads:
                for parent in list(channels):
                    if isinstance(parent, discord.TextChannel):
                        try:
                            async for thread in parent.archived_threads(limit=None):
                                channels.append(thread)
                        except discord.HTTPException:
                            pass
            log.info("backfilling %d channels%s", len(channels),
                     " (with reaction users)" if args.reactions else "")
            grand = 0
            for channel in channels:
                grand += await backfill_channel(channel, after, args.reactions, args.resume)
            log.info("backfill finished: %s messages in %.1f minutes, chronicle.db is %s bytes",
                     f"{grand:,}", (time.time() - started) / 60, f"{ChronicleDB.size_bytes():,}")
        finally:
            ChronicleDB.checkpoint()
            await client.close()

    await client.start(token)


def main():
    ap = argparse.ArgumentParser(description="Backfill Discord history into chronicle.db")
    ap.add_argument("--channel", nargs="*", type=int, help="only these channel ids")
    ap.add_argument("--after", help="only messages after this UTC date (YYYY-MM-DD)")
    ap.add_argument("--threads", action="store_true", help="include threads, archived ones too")
    ap.add_argument("--reactions", action="store_true",
                    help="also fetch who reacted to each message (very slow)")
    ap.add_argument("--resume", action="store_true", default=True,
                    help="skip channels already marked complete (default)")
    ap.add_argument("--no-resume", dest="resume", action="store_false",
                    help="re-walk channels from the top (rows are still deduped)")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
