"""Shared plumbing for the chronicle's history walks (messages, and reactions).

One lock for all of them: two walks at once would put two processes on the bot token's
rate limit, which is how a Cloudflare ban happens, and the live catch-up waits on the
same file for the same reason.
"""

import errno
import logging
import os
import sys

import discord

log = logging.getLogger(__name__)

LOCK_PATH = "/tmp/chronicle_backfill.lock"


def lock_holder(path=None):
    """pid of the live process holding the lock, or None. A lock whose process has died
    (killed, crashed, the box rebooted) counts as free: otherwise it would block every
    future walk, and the live catch-up, forever."""
    path = path or LOCK_PATH
    try:
        with open(path) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)                  # signal 0: existence check, sends nothing
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid                       # alive, just not ours
    return pid


class RunLock:
    """One backfill at a time. Two runs would walk the same channels in parallel and
    burn the token's rate limit on work the other is already doing."""

    def __enter__(self):
        for _ in range(2):
            try:
                fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except OSError as e:
                if e.errno != errno.EEXIST:
                    raise
                holder = lock_holder()
                if holder is not None:
                    sys.exit(f"A backfill is already running (pid {holder}, lock {LOCK_PATH}).")
                log.warning("removing stale lock %s left by a process that has died", LOCK_PATH)
                try:
                    os.remove(LOCK_PATH)
                except FileNotFoundError:
                    pass
        else:
            sys.exit(f"Could not take the lock {LOCK_PATH}.")
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass
        return False


async def collect_channels(guild, only, include_threads):
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
