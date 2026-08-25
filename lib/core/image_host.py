"""Host a generated image on Discord's CDN so ephemeral messages can show it quickly.

Discord loads image ATTACHMENTS on ephemeral messages badly - they sit on a grey
placeholder for seconds however good the connection, and a puzzle board that redraws on
every answer pays that cost every single time. An embed pointing at a normal CDN URL has
no such problem, because by then it's just an image on a link like any other.

So: post the PNG once into a quiet channel, keep the attachment URL, and put that in the
ephemeral's embed. The player sees the picture arrive with the message.

The hosting message is left in place on purpose. Discord's attachment URLs are signed and
time-limited, and deleting the message can invalidate them early - so the post stays, in a
channel nobody reads, rather than risking a board that renders as a broken image.

Uploads are cached on the bytes of the picture, because the upload is the leg the player
actually waits on and the same picture comes round constantly - see _url_cache.
"""

import asyncio
import hashlib
import io
import logging
import time

import discord

logger = logging.getLogger(__name__)

# Hosted URLs keyed by the picture's own bytes.
#
# The same image drawn twice is the same PNG byte for byte, and that happens far more than
# it looks: every player's first crossword of the day is the identical empty grid, and
# reopening a board nobody has touched redraws exactly the file we already posted. A hit
# skips the upload entirely, so the picture is simply already there.
#
# Six hours because Discord's signed attachment URLs last about a day - well inside it, so
# a cached link can't go stale on someone mid-puzzle.
_CACHE_TTL = 6 * 3600
_CACHE_MAX = 512
_url_cache: dict[str, tuple[str, float]] = {}
_in_flight: dict[str, "asyncio.Task[str | None]"] = {}

# The draw is tens of milliseconds; the upload is a REST round trip and is the leg anybody
# actually waits on. Counted so "the board is slow" can be answered with a number instead
# of a guess - read it with cache_stats().
SLOW_UPLOAD_MS = 700
_stats = {"hits": 0, "misses": 0, "upload_ms": 0.0}


def cache_stats() -> dict:
    """Hits, misses and total upload time since boot. A high miss rate is expected while
    somebody is solving - every answer changes the picture - and is the thing to look at
    before blaming the renderer."""
    total = _stats["hits"] + _stats["misses"]
    return {**_stats, "hit_rate": (_stats["hits"] / total) if total else 0.0,
            "cached": len(_url_cache)}


def _host_channel_id() -> int:
    import config
    explicit = int(getattr(config, "IMAGE_HOST_CHANNEL_ID", 0) or 0)
    if explicit:
        return explicit
    return int(getattr(getattr(config, "CHANNELS", None), "BOT_USAGE_LOG", 0) or 0)


async def as_embed_or_file(client, data: io.BytesIO, filename: str = "board.png",
                           colour: int = 0x2B2D31):
    """(embed, files) for showing a generated image on an EPHEMERAL message.

    Use this anywhere an image is built on the fly and shown only to one person. An
    attachment on an ephemeral sits on a grey placeholder for seconds; an embed pointing
    at a CDN link arrives with the message. Returns a plain attachment as the fallback,
    so the picture always shows even when hosting isn't available.

    Public messages don't need this - the casino boards attach images directly and are
    fine, because the problem is specific to ephemerals.
    """
    url = await host_image(client, data, filename)
    if url:
        e = discord.Embed(colour=colour)
        e.set_image(url=url)
        return e, []
    data.seek(0)
    return None, [discord.File(data, filename)]


_logged_destination = False


async def host_image(client, data: io.BytesIO, filename: str = "board.png") -> str | None:
    """Return a CDN URL for `data`, or None if it couldn't be hosted.

    Identical bytes are only ever uploaded once (see _url_cache), and if an upload of this
    exact picture is already in flight the caller waits on that one rather than posting a
    second copy - two people opening the same fresh board at the same moment is the normal
    case, not a rare one.

    Falls back to None rather than raising: the caller should then send the image as a
    plain attachment (slower, but a slow board beats no board).
    """
    raw = data.getvalue()
    key = f"{filename}:{hashlib.sha256(raw).hexdigest()}"
    hit = _url_cache.get(key)
    if hit and time.time() - hit[1] < _CACHE_TTL:
        _stats["hits"] += 1
        return hit[0]
    _stats["misses"] += 1

    task = _in_flight.get(key)
    if task is None:
        task = asyncio.create_task(_upload(client, raw, filename, key))
        _in_flight[key] = task
    # shield so one caller giving up (a timed-out interaction, say) doesn't cancel the
    # upload everybody else is waiting on
    return await asyncio.shield(task)


def _remember(key: str, url: str) -> None:
    now = time.time()
    _url_cache[key] = (url, now)
    if len(_url_cache) > _CACHE_MAX:
        for k, (_u, ts) in list(_url_cache.items()):
            if now - ts >= _CACHE_TTL:
                _url_cache.pop(k, None)
    while len(_url_cache) > _CACHE_MAX:
        _url_cache.pop(next(iter(_url_cache)), None)   # oldest first, dicts keep order


async def _upload(client, raw: bytes, filename: str, key: str) -> str | None:
    global _logged_destination
    try:
        cid = _host_channel_id()
        if not cid or client is None:
            return None
        ch = client.get_channel(cid)
        if ch is None:
            ch = await client.fetch_channel(cid)

        # Say once, loudly, where these are actually going. Getting this wrong is silent
        # otherwise - the images just turn up somewhere unexpected and nothing errors.
        if not _logged_destination:
            _logged_destination = True
            parent = getattr(ch, "parent", None)
            logger.info(
                "[IMAGE HOST] id=%s -> %s #%s%s",
                cid, type(ch).__name__, getattr(ch, "name", "?"),
                f" (thread under #{getattr(parent, 'name', '?')})" if parent else
                " (NOT a thread - this is a channel)",
            )

        started = time.perf_counter()
        msg = await ch.send(file=discord.File(io.BytesIO(raw), filename))
        took = (time.perf_counter() - started) * 1000
        _stats["upload_ms"] += took
        if took > SLOW_UPLOAD_MS:
            logger.info("[IMAGE HOST] %s took %.0fms for %.1fKB", filename, took,
                        len(raw) / 1024)
        if msg.attachments:
            _remember(key, msg.attachments[0].url)
            return msg.attachments[0].url
    except Exception:
        logger.warning("image hosting failed; falling back to an attachment", exc_info=True)
    finally:
        _in_flight.pop(key, None)
    return None
