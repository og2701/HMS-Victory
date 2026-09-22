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
    python scripts/chronicle_backfill.py --concurrency 6    # channels walked in parallel
    python scripts/chronicle_backfill.py --no-audit         # skip the audit log sweep

Rate limits: history pages are 100 messages per request, so a few million messages is a
few hours. Reaction users cost one extra request *per distinct emoji per message*, which
is why they are opt-in - on a busy server that turns hours into days.

Parallelism is across CHANNELS, inside ONE process, and that is deliberate. Discord
buckets message history per channel, so several channels at once is genuinely faster,
while splitting one channel into date ranges is not - the pages share a bucket either
way. Running several *processes* on the same token is worse than useless: each has its
own idea of the rate limit, they cannot see each other's 429s, and the global 50/s cap
is per token, so the usual outcome is a Cloudflare ban that takes the bot offline with
it. One process, one HTTPClient, a handful of channels at a time.

Only one run at a time: a lock file stops a second invocation from racing the first on
the same channels. Safe to run while the bot is live - SQLite WAL plus busy_timeout
handles both writers, and inserts are INSERT OR IGNORE keyed on message_id, so re-runs
never duplicate.
"""

import argparse
import asyncio
import errno
import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp  # noqa: E402
import discord  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from config import GUILD_ID  # noqa: E402
from lib.chronicle.db import ChronicleDB  # noqa: E402
from lib.chronicle.recorder import REACTION_SQL, audit_row, message_rows  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("chronicle-backfill")

BATCH = 500          # messages per write transaction
PROGRESS_EVERY = 5_000
LOCK_PATH = "/tmp/chronicle_backfill.lock"


class _Lock:
    """One backfill at a time. Two runs would walk the same channels in parallel and
    burn the token's rate limit on work the other is already doing."""

    def __enter__(self):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise
            existing = open(LOCK_PATH).read().strip()
            sys.exit(f"A backfill is already running (pid {existing}, lock {LOCK_PATH}). "
                     f"If it is not, delete the lock file and rerun.")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
        return False


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


async def backfill_channel(channel, after=None, want_reactions=False, resume=True,
                           attempts=6):
    """Walk one channel, retrying through Discord's 5xx blips.

    A 503 mid-walk used to abandon the channel for the rest of the run, which on the
    two busiest channels cost more than half the server's history. Progress is saved
    per batch, so a retry just resumes below the oldest id already stored.
    """
    total = 0
    for attempt in range(1, attempts + 1):
        got, interrupted = await _walk(channel, after, want_reactions,
                                       resume or attempt > 1)
        total += got
        if not interrupted:
            return total
        if attempt == attempts:
            log.error("#%s: giving up after %d attempts, %s messages stored - rerun to resume",
                      getattr(channel, "name", channel.id), attempts, f"{total:,}")
            return total
        delay = min(60, 2 ** attempt)
        log.warning("#%s: Discord blipped, resuming in %ds (attempt %d/%d)",
                    getattr(channel, "name", channel.id), delay, attempt, attempts)
        await asyncio.sleep(delay)
    return total


async def _walk(channel, after=None, want_reactions=False, resume=True):
    """One pass over a channel. Returns (messages stored, whether it was interrupted)."""
    row = _progress_row(channel.id)
    before = None
    if resume and row:
        oldest_seen, done, complete = row
        if complete:
            log.info("skip #%s (already complete, %s messages)", channel.name, f"{done:,}")
            return 0, False
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
        # Only a full walk earns the complete flag. A date-limited run reached the
        # bottom of its window, not the bottom of the channel, and marking it done
        # would make every later --resume skip everything older.
        _save_progress(channel, oldest, newest, 0, complete=after is None)
        log.info("done #%s: %s messages%s", channel.name, f"{total:,}",
                 "" if after is None else " (date-limited, channel not marked complete)")
    except discord.Forbidden:
        log.warning("no access to #%s, skipping", getattr(channel, "name", channel.id))
    except discord.NotFound:
        # Deleted or archived out from under us mid-walk; retrying would never help.
        log.warning("#%s vanished mid-walk, keeping the %s messages stored",
                    getattr(channel, "name", channel.id), f"{total:,}")
    except (discord.DiscordServerError, discord.HTTPException, aiohttp.ClientError,
            asyncio.TimeoutError) as e:
        # Transient: Discord 5xx, a dropped connection, a timeout. Worth resuming.
        if pending:
            total += await _flush(pending, channel, want_reactions, oldest, newest)
        log.warning("#%s interrupted after %s messages: %r",
                    getattr(channel, "name", channel.id), f"{total:,}", e)
        return total, True
    except Exception:
        log.error("backfill of #%s failed - progress is saved, rerun to resume",
                  getattr(channel, "name", channel.id), exc_info=True)
    return total, False


async def backfill_audit_log(guild):
    """Everything Discord still holds: kicks, bans, timeouts, channel and role edits,
    webhooks, pins, thread creation. Only 45 days of it exist, so this is a one-off
    rescue of the window that happened to be open - after this the live hook keeps it.
    """
    rows, n = [], 0
    try:
        async for entry in guild.audit_logs(limit=None, oldest_first=False):
            rows.append(audit_row(entry))
            n += 1
            if len(rows) >= BATCH:
                _write(rows)
                rows = []
        _write(rows)
        log.info("audit log: %s entries (Discord keeps 45 days)", f"{n:,}")
    except discord.Forbidden:
        log.warning("no View Audit Log permission - skipping the audit sweep")
    except Exception:
        log.error("audit log sweep failed", exc_info=True)
    return n


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


async def _collect(guild, only, include_threads):
    """Everything with a message history: text, voice and stage channels, forums, and
    every thread under them - active, archived public, and archived private where the
    bot can see them. Deduped, because an active thread shows up in more than one list."""
    out, seen = [], set()

    def add(channel):
        if channel.id in seen:
            return
        seen.add(channel.id)
        out.append(channel)

    for channel in guild.channels:
        if not isinstance(channel, (discord.TextChannel, discord.VoiceChannel,
                                    discord.ForumChannel, discord.StageChannel)):
            continue
        if only and channel.id not in only:
            continue
        # A forum has no messages of its own - all of its content lives in its threads.
        if not isinstance(channel, discord.ForumChannel):
            add(channel)
        if not include_threads:
            continue
        for thread in getattr(channel, "threads", []):
            add(thread)
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            continue
        # Only text channels have private threads; a forum's archived_threads takes no
        # `private` argument at all, and passing one is a TypeError.
        variants = ({}, {"private": True}) if isinstance(channel, discord.TextChannel) else ({},)
        for kwargs in variants:
            try:
                async for thread in channel.archived_threads(limit=None, **kwargs):
                    add(thread)
            except discord.Forbidden:
                pass          # private archived threads need Manage Threads
            except discord.HTTPException as e:
                log.debug("archived threads %s failed on #%s: %s", kwargs, channel.name, e)
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
        # Anything raised in here would otherwise be swallowed by the gateway's error
        # handling and the process would just exit, looking like a clean finish.
        try:
            guild = client.get_guild(GUILD_ID)
            if guild is None:
                log.error("guild %s not visible to this token", GUILD_ID)
                return
            if args.audit and not args.channel:
                await backfill_audit_log(guild)
            channels = await _collect(guild, set(args.channel or []), args.threads)
            log.info("backfilling %d channels, %d at a time%s", len(channels),
                     args.concurrency, " (with reaction users)" if args.reactions else "")
            # Channels in parallel, one HTTPClient: discord.py serialises each channel's
            # own bucket and the shared global limit, so this speeds things up without
            # the 429 storm that separate processes would cause.
            gate = asyncio.Semaphore(args.concurrency)

            async def one(channel):
                async with gate:
                    return await backfill_channel(channel, after, args.reactions, args.resume)

            done = await asyncio.gather(*(one(c) for c in channels), return_exceptions=True)
            grand = sum(n for n in done if isinstance(n, int))
            for channel, result in zip(channels, done):
                if isinstance(result, Exception):
                    log.error("channel #%s raised: %r", getattr(channel, "name", channel.id), result)
            log.info("backfill finished: %s messages in %.1f minutes, chronicle.db is %s bytes",
                     f"{grand:,}", (time.time() - started) / 60, f"{ChronicleDB.size_bytes():,}")
        except Exception:
            log.error("backfill aborted", exc_info=True)
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
    ap.add_argument("--no-audit", dest="audit", action="store_false", default=True,
                    help="skip the 45-day audit log sweep (it runs first by default)")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="channels walked at once (default 4; past ~8 the global rate "
                         "limit, not the bucket, becomes the wall)")
    args = ap.parse_args()
    with _Lock():
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
