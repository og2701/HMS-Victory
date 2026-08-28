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

# --- Feature Toggles & Target Scope ---
MEDIA_FILTER_ENABLED: bool = True           # Master switch for blocked dog media moderation
AI_VISION_FALLBACK_ENABLED: bool = False    # AI Vision fallback via Gemini (OFF: 100% local dHash only)
TARGET_USER_IDS: set[int] = {285860055570579457}  # Enforced exclusively on Shuto (285860055570579457)


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
    0xECE1C4D2B2F0E484,
    0xA6E1CCD2B2F0E084,
    0xDCE2E1D0B2B0E0C4,
    0xCC62C4D0B2B060F4,
    0xECE0C4DAB2B060FC,
    0xE6E1C4DAB2B0E0E8,
    0xF0E2E0D8BAB060FC,
    0x60C8A486317878E8,
    0xC884A496317970F8,
    # Discord Sticker variation
    0x6BE08CB6B2F0E084,  # "sigh" Discord Sticker
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
    "1542886924174360719",
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


async def async_ai_vision_check(data: bytes) -> Tuple[bool, str]:
    """AI Vision fallback for novel crops/edits using Gemini Flash Lite."""
    if not AI_VISION_FALLBACK_ENABLED:
        return False, ""
    if not data:
        return False, ""
    try:
        import base64
        from lib.core.gemini import gemini_generate

        try:
            im = Image.open(io.BytesIO(data))
        except Exception:
            frames = extract_frames_from_video_bytes(data)
            if not frames:
                return False, ""
            im = frames[0]

        im.seek(0)
        out = io.BytesIO()
        im.resize((320, 320)).convert("RGB").save(out, format="JPEG", quality=80)
        b64 = base64.b64encode(out.getvalue()).decode()

        prompt = (
            "You are a strict Discord auto-moderation filter. "
            "Determine if this image contains the viral meme of a golden retriever dog looking directly down into the camera "
            "with a tilted head, sleepy/squinted/honest expression, or blinking eyes (regardless of cropping, text captions, zoom, or filters). "
            "Reply ONLY with YES if it is this specific dog meme, or NO if it is anything else."
        )
        user_parts = [
            {"text": "Is this the blacklisted golden retriever meme?"},
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
        ]
        ans, err = await gemini_generate(None, prompt, user_parts, max_output_tokens=10, temperature=0.0)
        if ans and "YES" in ans.strip().upper():
            # Dynamically learn this dHash
            try:
                new_h = compute_frame_dhash(im)
                if new_h not in BLOCKED_DOG_GIF_DHASHES:
                    BLOCKED_DOG_GIF_DHASHES.append(new_h)
            except Exception:
                pass
            return True, "Banned dog GIF AI Vision match"
    except Exception as e:
        logger.debug("AI vision fallback check failed: %s", e)
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

    if not MEDIA_FILTER_ENABLED:
        return False

    # Only enforce against targeted users (e.g. Shuto)
    if TARGET_USER_IDS and message.author.id not in TARGET_USER_IDS:
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

    # Check stickers
    sticker_items = getattr(message, "stickers", []) or getattr(message, "sticker_items", [])
    for sticker in sticker_items:
        s_id = str(getattr(sticker, "id", ""))
        s_name = str(getattr(sticker, "name", ""))
        s_url = getattr(sticker, "url", f"https://media.discordapp.net/stickers/{s_id}.png") if s_id else ""
        if s_id in BLOCKED_MEDIA_PATTERNS or is_blocked_url_or_text(s_name) or is_blocked_url_or_text(s_url):
            try:
                await message.delete()
            except Exception:
                pass
            return True

        if s_url:
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(s_url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 200:
                            s_data = await resp.read()
                            blocked, reason = is_blocked_image_bytes(s_data)
                            if blocked:
                                try:
                                    await message.delete()
                                except Exception:
                                    pass
                                return True
            except Exception as se:
                logger.debug("Failed checking sticker: %s", se)

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
            if not blocked:
                blocked, reason = await async_ai_vision_check(data)

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
