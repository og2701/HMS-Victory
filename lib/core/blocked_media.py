"""Perceptual visual hash and content filter for blocked media (e.g. banned GIFs/videos).

Detects banned media regardless of re-encoding, filename changes, or missing text by computing
the image's difference hash (dHash) and comparing against known blacklisted fingerprints.
"""
import io
import logging
from typing import Tuple

import discord
from PIL import Image

from config import CHANNELS

logger = logging.getLogger(__name__)

# Known visual dHash fingerprints for blocked media
# Target dog GIF (Golden retriever looking at camera / blinking slowly)
BLOCKED_DOG_GIF_DHASHES = [
    0x120D33D8A4E0F1BC,  # Original frame 0
    0x100F33D8A4E0F1BC,  # Re-encoded frame 0
    0x120F33D8A4E4F1BC,  # Original frame 10
]

MAX_HAMMING_DISTANCE = 5
MAX_ATTACHMENT_SCAN_SIZE = 15 * 1024 * 1024  # 15 MB


def compute_frame_dhash(frame_img: Image.Image) -> int:
    """Compute 64-bit difference hash (dHash) for an image frame."""
    resized = frame_img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    raw = resized.tobytes()
    diff = []
    for row in range(8):
        row_offset = row * 9
        for col in range(8):
            diff.append(raw[row_offset + col] > raw[row_offset + col + 1])
    return int("".join("1" if b else "0" for b in diff), 2)


def hamming_distance(h1: int, h2: int) -> int:
    """Number of differing bits between two 64-bit hashes."""
    return bin(h1 ^ h2).count("1")


def is_blocked_image_bytes(data: bytes) -> Tuple[bool, str]:
    """Inspect raw image/gif data using perceptual hashing."""
    if not data:
        return False, ""
    try:
        im = Image.open(io.BytesIO(data))
        # Test first frame
        im.seek(0)
        h0 = compute_frame_dhash(im)
        for target in BLOCKED_DOG_GIF_DHASHES:
            dist = hamming_distance(h0, target)
            if dist <= MAX_HAMMING_DISTANCE:
                return True, f"Banned dog GIF visual hash match (dist={dist})"

        # If animated, test another frame (e.g. frame 10)
        n_frames = getattr(im, "n_frames", 1)
        if n_frames > 10:
            try:
                im.seek(10)
                h10 = compute_frame_dhash(im)
                for target in BLOCKED_DOG_GIF_DHASHES:
                    dist = hamming_distance(h10, target)
                    if dist <= MAX_HAMMING_DISTANCE:
                        return True, f"Banned dog GIF visual hash match (frame 10 dist={dist})"
            except Exception:
                pass
    except Exception as e:
        logger.debug("Could not parse image for visual hash check: %s", e)
    return False, ""


async def check_blocked_media(client: discord.Client, message: discord.Message) -> bool:
    """Checks message attachments and embeds for blocked media. Deletes and logs if detected."""
    if message.author.bot or message.guild is None:
        return False

    # Check attachments
    for attachment in message.attachments:
        ext = (attachment.filename or "").rsplit(".", 1)[-1].lower()
        content_type = (attachment.content_type or "").lower()
        is_image_or_video = (
            ext in ("gif", "webp", "png", "jpg", "jpeg", "mp4", "mov", "webm")
            or content_type.startswith("image/")
            or content_type.startswith("video/")
        )
        if not is_image_or_video:
            continue

        if attachment.size and attachment.size > MAX_ATTACHMENT_SCAN_SIZE:
            continue

        try:
            data = await attachment.read()
            blocked, reason = is_blocked_image_bytes(data)
            if blocked:
                try:
                    await message.delete()
                except Exception as de:
                    logger.warning("Could not delete message containing blocked media: %s", de)

                logger.info(
                    "Blocked media filter triggered by %s in channel %s: %s",
                    message.author.id,
                    message.channel.id,
                    reason,
                )

                log_ch = client.get_channel(CHANNELS.LOGS) or client.get_channel(CHANNELS.POLICE_STATION)
                if log_ch:
                    embed = discord.Embed(
                        title="🚫 Blocked Media Deleted",
                        description=(
                            f"Deleted a message from {message.author.mention} (`{message.author.id}`) "
                            f"in {message.channel.mention} matching the **blacklisted dog GIF**.\n"
                            f"**Reason**: {reason}\n"
                            f"**Filename**: `{attachment.filename}`"
                        ),
                        color=discord.Color.dark_red(),
                        timestamp=discord.utils.utcnow(),
                    )
                    await log_ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                return True
        except Exception as e:
            logger.debug("Failed reading attachment for blocked media scan: %s", e)

    return False
