"""Instagram and X links posted in chat, turned back into something you can actually watch.

Both sites break Discord's link crawler, and they break it differently, so the fix differs
too.

X shows a card but will not play the video in it, and fxtwitter.com serves the crawler a
proper one - author, full text, the counts, and a player that works - so an x.com link is
answered with the same link on fxtwitter and Discord does the rest. The original card is
suppressed so the same tweet is not sat in the channel twice.

Instagram serves the crawler a login wall, so a reel or photo arrives as a bare blue URL
with no thumbnail and no player at all, and everyone has to leave the server to see what
was posted. kkinstagram.com (the instafix service) answers that crawler with a redirect
straight to the underlying media file, which is the whole trick behind swapping the domain
by hand. Swapping is not enough on its own here: unlike fxtwitter it serves no card, so the
link would buy a bare video player and a click through kkclip's "Open in App" page. The bot
follows the redirect itself instead and posts the file.

Posting the file rather than the link also leaves the member's own link untouched above it,
and the video keeps working if kkinstagram goes down later.

Reels routinely land between the guild's upload limit and ~50MB, so a video too big to
post as it stands is re-encoded to fit rather than given up on: ffmpeg is told how many
bytes it has, works the bitrate back from the clip's duration, and drops the picture to
720p (then 480p on a second go) if that is what it takes. Only when even that overshoots,
or the source is bigger than the bot is willing to pull down, does it fall back to posting
the kkinstagram link, which unfurls the same media through Discord's crawler.

Posts kkinstagram cannot resolve - deleted, private or age-gated - redirect back to
instagram.com instead of to the media, and those are left alone: nothing the bot could
post would beat the link the member already sent.
"""

import asyncio
import io
import logging
import os
import re
import shutil
import tempfile
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

# x.com/<user>/status/<id>, plus the /i/web/status/ form the app sometimes hands out and
# the archaic /statuses/. The username is missing on the /i/ forms, hence the optional group.
TWITTER_LINK = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:twitter|x)\.com/"
    r"(?:i/web/status|i/status|([A-Za-z0-9_]{1,15})/status(?:es)?)/"
    r"(\d{5,25})",
    re.IGNORECASE,
)

# Any .../status/<id> link, whatever the host - used to spot a tweet someone has already
# fixed by hand, so the bot does not put a third copy of it in the channel.
ANY_STATUS_LINK = re.compile(r"https?://([A-Za-z0-9.-]+)/[^\s]*?status(?:es)?/(\d{5,25})", re.IGNORECASE)
X_HOSTS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}

MAX_LINKS_PER_MESSAGE = 2
REQUEST_TIMEOUT = 20            # kkinstagram scrapes Instagram live, so it is not instant
UPLOAD_HEADROOM = 512 * 1024    # multipart overhead, kept clear of the guild's hard limit
RATE_LIMIT_PER_USER = 4         # fixes per member per minute; a paste of ten reels is spam
RATE_WINDOW = 60.0

# Re-encoding budget. The bitrate is worked back from the clip's duration, so a long reel
# gets a lower one; the ladder is the fallback when that alone does not bring it under.
ENCODE_TIMEOUT = 180
AUDIO_BITRATE = 96_000
BITRATE_SAFETY = 0.92           # container overhead ffmpeg's -b:v does not account for
ENCODE_LADDER = (720, 480)      # max output height per attempt

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


def _source_max_bytes() -> int:
    """How big a reel the bot will pull down before deciding it is not worth the bandwidth."""
    return int(getattr(config, "INSTAGRAM_EMBED_SOURCE_MAX_BYTES", 60 * 1024 * 1024))


def _compress_enabled() -> bool:
    return bool(getattr(config, "INSTAGRAM_EMBED_COMPRESS", True))


def _twitter_enabled() -> bool:
    return bool(getattr(config, "TWITTER_EMBED_FIX_ENABLED", True))


def _twitter_mirror() -> str:
    return str(getattr(config, "TWITTER_EMBED_HOST", "fxtwitter.com")).strip("/")


def _suppress_original() -> bool:
    return bool(getattr(config, "TWITTER_EMBED_SUPPRESS_ORIGINAL", True))


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


def find_twitter_posts(content: str) -> List[Tuple[Optional[str], str]]:
    """Return [(username, status id)] for every X link worth fixing in the message."""
    if not content:
        return []

    # A tweet someone has already run through fxtwitter (or vxtwitter, or any of the other
    # mirrors) is left alone - the embed they wanted is already there.
    fixed = {
        status for host, status in ANY_STATUS_LINK.findall(content)
        if host.lower() not in X_HOSTS
    }

    found: List[Tuple[Optional[str], str]] = []
    seen = set()
    for match in TWITTER_LINK.finditer(content):
        username, status = match.group(1), match.group(2)
        if _suppressed(content, match.start(), match.end()) or status in fixed or status in seen:
            continue
        seen.add(status)
        found.append((username, status))
        if len(found) >= MAX_LINKS_PER_MESSAGE:
            break
    return found


def fixed_twitter_url(username: Optional[str], status: str) -> str:
    return f"https://{_twitter_mirror()}/{username or 'i'}/status/{status}"


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


async def _run(program: str, *arguments: str, timeout: int) -> Tuple[int, bytes]:
    """Run a tool, never leaving a stuck process behind. Returns (exit code, stdout)."""
    process = await asyncio.create_subprocess_exec(
        program, *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise
    return process.returncode, stdout


async def video_duration(path: str) -> Optional[float]:
    code, stdout = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path, timeout=30,
    )
    try:
        duration = float(stdout.decode().strip())
    except (ValueError, UnicodeDecodeError):
        return None
    return duration if code == 0 and duration > 0 else None


async def compress_video(data: bytes, target_bytes: int) -> Optional[bytes]:
    """Re-encode a video to land under `target_bytes`, or None if it cannot be done.

    The bitrate is the budget divided by the running time, so the encoder is aiming at a
    size rather than a quality, and a long reel simply gets a softer picture. Height is
    stepped down the ladder on each attempt, since a 1080p reel squeezed into a few
    megabytes looks far worse than the same clip at 720p.
    """
    if target_bytes <= 0 or not data:
        return None

    workspace = tempfile.mkdtemp(prefix="ig-embed-")
    source = os.path.join(workspace, "source")
    try:
        with open(source, "wb") as handle:
            handle.write(data)

        duration = await video_duration(source)
        if not duration:
            return None

        for attempt, height in enumerate(ENCODE_LADDER):
            budget = int(target_bytes * BITRATE_SAFETY * (0.85 ** attempt))
            video_bitrate = int(budget * 8 / duration) - AUDIO_BITRATE
            if video_bitrate < 120_000:     # below this it is a slideshow; not worth posting
                return None

            output = os.path.join(workspace, f"out{attempt}.mp4")
            code, _ = await _run(
                "ffmpeg", "-y", "-i", source,
                # Only ever scale down, and keep the height even for H.264.
                "-vf", f"scale=-2:'min({height},ih)'",
                "-c:v", "libx264", "-preset", "veryfast",
                "-b:v", str(video_bitrate),
                "-maxrate", str(int(video_bitrate * 1.25)),
                "-bufsize", str(video_bitrate * 2),
                "-c:a", "aac", "-b:a", str(AUDIO_BITRATE),
                "-movflags", "+faststart",
                output, timeout=ENCODE_TIMEOUT,
            )
            if code != 0 or not os.path.exists(output):
                continue
            if os.path.getsize(output) <= target_bytes:
                with open(output, "rb") as handle:
                    return handle.read()
        return None
    except (asyncio.TimeoutError, FileNotFoundError, OSError):
        logger.debug("re-encode failed", exc_info=True)
        return None
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


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


async def fix_broken_embeds(client, message) -> None:
    """Both fixers, for one message. Neither says anything when it has nothing to add."""
    if getattr(message.author, "bot", False):
        return
    await fix_twitter_links(message)
    await fix_instagram_links(client, message)


async def fix_twitter_links(message) -> None:
    """Re-post X links on fxtwitter, where the card actually plays, and hide the dud one."""
    if not _twitter_enabled():
        return

    posts = find_twitter_posts(getattr(message, "content", "") or "")
    if not posts:
        return

    links = []
    for username, status in posts:
        if _rate_limited(message.author.id):
            break
        links.append(fixed_twitter_url(username, status))
    if not links:
        return

    await _post(message, {"content": "\n".join(links)})
    await _suppress_embeds(message)


async def _suppress_embeds(message) -> None:
    """Hide X's own card so the tweet is not in the channel twice.

    Needs Manage Messages, and it is only tidiness - if it fails, the fixed embed is still
    there underneath, which was the point.
    """
    if not _suppress_original():
        return
    try:
        await message.edit(suppress=True)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        logger.debug("could not suppress the original X embed", exc_info=True)


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

        # Pulled down against the bigger ceiling, not the upload limit: a reel over the
        # limit is a candidate for re-encoding, so it has to be in hand first.
        downloaded = await download_media(session, media_url, _source_max_bytes())
        if not downloaded:
            await _post(message, {"content": mirror_url(kind, code)})
            continue

        data, content_type = downloaded
        if len(data) > limit:
            data = await _shrink(data, content_type, limit)
            if not data:
                # A still image, or a clip that will not come down far enough: hand Discord
                # the link and let its crawler follow the same redirect we just did.
                await _post(message, {"content": mirror_url(kind, code)})
                continue
            content_type = "video/mp4"

        file = discord.File(io.BytesIO(data), filename=filename_for(code, content_type))
        await _post(message, {"file": file})


async def _shrink(data: bytes, content_type: str, limit: int) -> Optional[bytes]:
    """Re-encode an oversized video to fit. Images are left alone - there is nothing
    sensible to trade away on a photo that Discord would not already do itself."""
    if not _compress_enabled() or not (content_type or "").startswith("video/") or limit <= 0:
        return None
    shrunk = await compress_video(data, limit)
    if shrunk:
        logger.info("re-encoded an Instagram video from %.1fMB to %.1fMB to fit the upload limit",
                    len(data) / 1e6, len(shrunk) / 1e6)
    return shrunk
