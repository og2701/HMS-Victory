"""Instagram links posted in chat, turned back into something you can actually watch.

Instagram serves Discord's link crawler a login wall, so a reel or photo someone drops in
chat arrives as a bare blue URL with no thumbnail and no player, and everyone has to leave
the server to see what was posted. kkinstagram.com (the instafix service) answers that
crawler with a redirect straight to the underlying fbcdn file, which is the whole trick
behind swapping the domain by hand.

The bot does that lookup itself rather than rewriting the member's link, and posts the
media as an attachment under their message. Two reasons: the reel then plays inline with
no third party between a member and the video (kkinstagram sends humans who click it to
kkclip.com, an "Open in App" interstitial), and the member's own link is left untouched
above it. When the file is bigger than the guild's upload limit it falls back to posting
the kkinstagram link, which unfurls the same media through Discord's crawler.

Posts kkinstagram cannot resolve - deleted, private or age-gated - redirect back to
instagram.com instead of to the media, and those are left alone: nothing the bot could
post would beat the link the member already sent.
"""

import asyncio
import io
import logging
import re
import time
from collections import deque
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import discord

import config

logger = logging.getLogger(__name__)

MIRROR = "kkinstagram.com"

# kkinstagram only serves the media to a crawler; a browser user agent gets bounced to the
# kkclip "Open in App" page instead, so the bot has to ask as Discord would.
CRAWLER_UA = "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)"

# The username segment is optional because both instagram.com/reel/<code> and
# instagram.com/<user>/reel/<code> are handed out by the app's share sheet.
INSTAGRAM_LINK = re.compile(
    r"https?://(?:www\.|m\.)?instagram\.com/"
    r"(?:([A-Za-z0-9_.]{1,30})/)?"
    r"(p|reel|reels|tv)/"
    r"([A-Za-z0-9_-]{5,32})",
    re.IGNORECASE,
)

# Where a resolved post's media actually lives. Which of the two Meta hands back varies by
# post and by region - the first build only knew about fbcdn.net and quietly dropped every
# reel served from cdninstagram.com, which was most of them.
MEDIA_CDNS = ("fbcdn.net", "cdninstagram.com")

# instagram.com/reels/audio/<id> and friends match the shape above but are not posts.
NOT_A_SHORTCODE = {"audio", "explore", "highlights", "stories", "video"}

MAX_LINKS_PER_MESSAGE = 2
REQUEST_TIMEOUT = 20            # kkinstagram scrapes Instagram live, so it is not instant
UPLOAD_HEADROOM = 512 * 1024    # multipart overhead, kept clear of the guild's hard limit
RATE_LIMIT_PER_USER = 4         # fixes per member per minute; a paste of ten reels is spam
RATE_WINDOW = 60.0

EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
}

_recent: deque = deque()        # (user_id, timestamp) of fixes already posted


def _enabled() -> bool:
    return bool(getattr(config, "INSTAGRAM_EMBED_FIX_ENABLED", True))


def _max_bytes() -> int:
    return int(getattr(config, "INSTAGRAM_EMBED_MAX_BYTES", 25 * 1024 * 1024))


def _suppressed(content: str, start: int, end: int) -> bool:
    """<https://...> is Discord's own "don't preview this" syntax, so honour it.

    The match ends at the shortcode rather than at the end of the URL, so the brackets are
    looked for either side of the whole unbroken token.
    """
    opening = content.rfind("<", 0, start)
    if opening == -1 or any(character.isspace() for character in content[opening + 1:start]):
        return False
    closing = content.find(">", end)
    if closing == -1:
        return False
    return not any(character.isspace() for character in content[end:closing])


def find_instagram_posts(content: str) -> List[Tuple[str, str]]:
    """Return [(kind, shortcode)] for every fixable Instagram post link in the message."""
    if not content:
        return []

    found: List[Tuple[str, str]] = []
    seen = set()
    for match in INSTAGRAM_LINK.finditer(content):
        username, kind, code = match.group(1), match.group(2).lower(), match.group(3)
        # The link ends at the shortcode, but the raw URL may carry /?igsh=... after it;
        # only the surrounding angle brackets matter for suppression.
        if _suppressed(content, match.start(), match.end()):
            continue
        if code.lower() in NOT_A_SHORTCODE or (username or "").lower() in NOT_A_SHORTCODE:
            continue
        if kind == "reels":
            kind = "reel"
        if code in seen:
            continue
        seen.add(code)
        found.append((kind, code))
        if len(found) >= MAX_LINKS_PER_MESSAGE:
            break
    return found


def mirror_url(kind: str, code: str) -> str:
    return f"https://{MIRROR}/{kind}/{code}"


def filename_for(code: str, content_type: str) -> str:
    extension = EXTENSIONS.get((content_type or "").split(";")[0].strip().lower(), ".mp4")
    return f"{code}{extension}"


def _rate_limited(user_id: int, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    while _recent and now - _recent[0][1] > RATE_WINDOW:
        _recent.popleft()
    if sum(1 for uid, _ in _recent if uid == user_id) >= RATE_LIMIT_PER_USER:
        return True
    _recent.append((user_id, now))
    return False


async def resolve_media(session, kind: str, code: str) -> Optional[str]:
    """Ask kkinstagram where the media lives, or None if it could not find it.

    A resolvable post answers the crawler with a redirect to one of Meta's media CDNs -
    which one varies by post and by region, so both are accepted. Anything else - a
    redirect back to instagram.com, a 404, one of the service's intermittent 502s - means
    there is nothing to post.
    """
    try:
        async with session.get(
            mirror_url(kind, code),
            headers={"User-Agent": CRAWLER_UA},
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as response:
            if response.status not in (301, 302, 303, 307, 308):
                return None
            target = response.headers.get("Location", "")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        logger.debug("kkinstagram lookup failed for %s/%s", kind, code, exc_info=True)
        return None

    host = (urlparse(target).hostname or "").lower()
    if not any(host == cdn or host.endswith("." + cdn) for cdn in MEDIA_CDNS):
        logger.debug("kkinstagram did not resolve %s/%s (sent us to %s)", kind, code, host or "nowhere")
        return None
    return target


async def download_media(session, url: str, limit: int) -> Optional[Tuple[bytes, str]]:
    """Pull the media down, giving up as soon as it is clear it will not fit in `limit`."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": CRAWLER_UA},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT * 3),
        ) as response:
            if response.status != 200:
                return None
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > limit:
                return None
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
            chunks = bytearray()
            async for chunk in response.content.iter_chunked(64 * 1024):
                chunks += chunk
                if len(chunks) > limit:
                    return None
            if not chunks:
                return None
            return bytes(chunks), content_type
    except (aiohttp.ClientError, asyncio.TimeoutError):
        logger.debug("media download failed for %s", url[:80], exc_info=True)
        return None


def upload_limit(message) -> int:
    guild_limit = getattr(getattr(message, "guild", None), "filesize_limit", None) or (8 * 1024 * 1024)
    return max(0, min(int(guild_limit) - UPLOAD_HEADROOM, _max_bytes()))


async def _post(message, payload) -> None:
    """Reply under the member's message, falling back to a plain channel send."""
    kwargs = dict(allowed_mentions=discord.AllowedMentions.none(), **payload)
    try:
        await message.reply(mention_author=False, **kwargs)
    except (discord.NotFound, discord.HTTPException):
        try:
            # The failed reply may have read part of the upload already.
            if "file" in kwargs:
                kwargs["file"].reset(seek=True)
            await message.channel.send(**kwargs)
        except discord.HTTPException:
            logger.debug("could not post fixed Instagram embed", exc_info=True)


async def fix_instagram_links(client, message) -> None:
    """Post the media for any Instagram post links in `message`. Silent when it cannot."""
    if not _enabled() or getattr(message.author, "bot", False):
        return

    posts = find_instagram_posts(getattr(message, "content", "") or "")
    if not posts:
        return

    session = getattr(client, "session", None)
    if session is None or getattr(session, "closed", False):
        return

    limit = upload_limit(message)
    for kind, code in posts:
        if _rate_limited(message.author.id):
            return

        media_url = await resolve_media(session, kind, code)
        if not media_url:
            continue

        downloaded = await download_media(session, media_url, limit) if limit else None
        if downloaded:
            data, content_type = downloaded
            file = discord.File(io.BytesIO(data), filename=filename_for(code, content_type))
            await _post(message, {"file": file})
        else:
            # Too big to re-host, so hand Discord the link and let its crawler follow the
            # same redirect we just did.
            await _post(message, {"content": mirror_url(kind, code)})
