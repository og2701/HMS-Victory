"""Perceptual visual hash and content filter for blocked media (e.g. banned GIFs/videos).

Detects banned media regardless of re-encoding, filename changes, or missing text by computing
the image's difference hash (dHash) and comparing against known blacklisted fingerprints.
"""
import io
import logging
import subprocess
from typing import Tuple

import discord
from PIL import Image

from config import CHANNELS

logger = logging.getLogger(__name__)

# Known visual dHash fingerprints for blocked media
# Target dog GIF (Golden retriever looking at camera / blinking slowly)
BLOCKED_DOG_GIF_DHASHES = [
    # Full frame / wide golden retriever
    0x120D33D8A4E0F1BC,
    0x100F33D8A4E0F1BC,
    0x120F33D8A4E4F1BC,
    0x1C0D1359A4F0DCFC,
    0xAC0B075088E4F0BC,
    0xAC1B075088E4F0BC,
    0x73D989E4E0F194BC,  # WhatsApp / phone screenshot still JPEG
    0x0F0F4CC495949CD4,  # Letterboxed widescreen screenshot
    0x74EAD6B233F1F0F8,  # Cropped square screenshot
    0x0F1370D89924E4F0,  # Center square crop
    0x0A1C0F137098A4E4,  # Top 70% crop
    0x341F0F13D09C2462,  # Face region crop
    0xF08817596171D8A4,  # Top meme caption full frame
    0x0F71496571518461,  # Top meme caption body crop
    # Phone / iOS Screen Recording captures
    0x35E4C799B8E0B283,
    0x63E192B2606C82C8,
    0x2BE5DABA602482C8,
    # Zoomed / cropped head golden retriever
    0x21E4C799B8E0F482,
    0x27E1D8B2E0AC86C8,
    0x63C8B6F06084C8D8,
    0x23E0C69BB86086C0,
]

BLOCKED_MEDIA_PATTERNS = [
    "golden-retriever-dog",
    "dog-195",
    "live-jamie-reaction",
    "39f2394ae36df6e199be9eb7c9fa1012",
    "f87f46a2c5aeaeed4c68910815f73eaf",
    "ui8KxohqjmmiPf7gBGj",
    "dMgAnsrp",
    "LGLBS5P7",
    "zi7PimsR",
]

MAX_HAMMING_DISTANCE = 6
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


def trim_borders(im: Image.Image) -> Image.Image:
    """Auto-crop solid borders / letterboxing black bars."""
    try:
        from PIL import ImageChops
        bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
        diff = ImageChops.difference(im, bg)
        diff = ImageChops.add(diff, diff, 2.0, -100)
        bbox = diff.getbbox()
        if bbox:
            return im.crop(bbox)
    except Exception:
        pass
    return im


def generate_multi_scale_crops(im: Image.Image) -> list[Image.Image]:
    """Generate multi-scale sub-windows (center, quadrants, top, face) to catch arbitrary crops."""
    w, h = im.size
    crops = [im]
    if w < 50 or h < 50:
        return crops
    # 1. Center crop
    min_dim = min(w, h)
    cx, cy = (w - min_dim) // 2, (h - min_dim) // 2
    crops.append(im.crop((cx, cy, cx + min_dim, cy + min_dim)))
    # 2. Quadrants / Halves
    crops.append(im.crop((0, 0, w, int(h * 0.65))))  # Top 65%
    crops.append(im.crop((0, int(h * 0.30), w, h)))  # Bottom 70% (below top captions)
    crops.append(im.crop((0, 0, int(w * 0.65), h)))  # Left 65%
    crops.append(im.crop((int(w * 0.35), 0, w, h)))  # Right 65%
    # 3. Inner 80% window
    crops.append(im.crop((int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9))))
    # 4. Top-center face region
    crops.append(im.crop((int(w * 0.15), 0, int(w * 0.85), int(h * 0.75))))
    return crops


def extract_frames_from_video_bytes(data: bytes) -> list[Image.Image]:
    """Extract frames across multiple timestamps from a video buffer using ffmpeg."""
    frames = []
    for ss in ["0.2", "1.0", "1.8"]:
        try:
            proc = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    ss,
                    "-i",
                    "pipe:0",
                    "-vframes",
                    "1",
                    "-f",
                    "image2",
                    "-c:v",
                    "png",
                    "pipe:1",
                ],
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=2.0,
            )
            if proc.returncode == 0 and proc.stdout:
                frames.append(Image.open(io.BytesIO(proc.stdout)))
        except Exception as e:
            logger.debug("ffmpeg frame extraction failed: %s", e)
    return frames


def is_blocked_image_bytes(data: bytes) -> Tuple[bool, str]:
    """Inspect raw image/gif/video data using multi-scale perceptual hashing."""
    if not data:
        return False, ""
    try:
        try:
            im = Image.open(io.BytesIO(data))
            frames_to_test = [im]
            n_frames = getattr(im, "n_frames", 1)
            if n_frames > 10:
                try:
                    im2 = Image.open(io.BytesIO(data))
                    im2.seek(10)
                    frames_to_test.append(im2)
                except Exception:
                    pass
        except Exception:
            frames_to_test = extract_frames_from_video_bytes(data)
            if not frames_to_test:
                return False, ""

        for frame_idx, frame_img in enumerate(frames_to_test):
            trimmed = trim_borders(frame_img)
            sub_crops = generate_multi_scale_crops(trimmed)
            if trimmed.size != frame_img.size:
                sub_crops.extend(generate_multi_scale_crops(frame_img))

            for crop_img in sub_crops:
                h = compute_frame_dhash(crop_img)
                for target in BLOCKED_DOG_GIF_DHASHES:
                    dist = hamming_distance(h, target)
                    if dist <= MAX_HAMMING_DISTANCE:
                        return True, f"Banned dog GIF visual hash match (frame {frame_idx} dist={dist})"
    except Exception as e:
        logger.debug("Could not parse image for visual hash check: %s", e)
    return False, ""


def is_blocked_url_or_text(text: str) -> bool:
    """Check text/URL against known blocked media URL identifiers."""
    if not text:
        return False
    lower = text.lower()
    return any(p.lower() in lower for p in BLOCKED_MEDIA_PATTERNS)


async def check_blocked_media(client: discord.Client, message: discord.Message) -> bool:
    """Checks message content, embeds, and attachments for blocked media."""
    if message.author.bot or message.guild is None:
        return False

    # Check content
    if is_blocked_url_or_text(message.content):
        try:
            await message.delete()
        except Exception:
            pass
        return True

    # Check embeds
    for emb in message.embeds:
        texts = [emb.title or "", emb.description or "", emb.url or ""]
        if emb.video and emb.video.url:
            texts.append(emb.video.url)
        if emb.image and emb.image.url:
            texts.append(emb.image.url)
        if emb.thumbnail and emb.thumbnail.url:
            texts.append(emb.thumbnail.url)
        if any(is_blocked_url_or_text(t) for t in texts):
            try:
                await message.delete()
            except Exception:
                pass
            return True

    # Check attachments
    for attachment in message.attachments:
        if is_blocked_url_or_text(attachment.filename) or is_blocked_url_or_text(attachment.url):
            try:
                await message.delete()
            except Exception:
                pass
            return True

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
