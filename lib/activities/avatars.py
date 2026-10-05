"""Players' profile pictures, for the activity's live games and their post pictures.

Discord keeps an activity's page from loading images off its CDN, so the page asks the bot's
API for them (GET /avatar/<uid>) and the bot fetches them here. Each is kept for a while, so a
room of six players doesn't fetch the same faces over and over.
"""

from __future__ import annotations

import logging
import time

import config

log = logging.getLogger(__name__)

KEEP = 3600
MAX = 500
_cache: dict[tuple[int, int, str], tuple[float, bytes | None]] = {}


async def get(client, uid: int, size: int = 128, fmt: str = "webp") -> bytes | None:
    """This server member's profile picture (their server one if set), or None."""
    key = (int(uid), size, fmt)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < KEEP:
        return hit[1]
    data = None
    try:
        guild = client.get_guild(config.GUILD_ID) if client is not None else None
        member = guild.get_member(int(uid)) if guild else None
        if member is not None:
            data = await member.display_avatar.replace(size=size, format=fmt).read()
    except Exception:
        log.warning("couldn't fetch the avatar for %s", uid, exc_info=True)
        return None                     # not kept: try again next time
    if len(_cache) >= MAX:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.time(), data)
    return data
