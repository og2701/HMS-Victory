import base64
import asyncio
import io
import os
import uuid
import random
from functools import lru_cache
from PIL import Image, ImageChops
from html2image import Html2Image
from config import CHROME_PATH

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)
hti.browser.flags += [
    "--force-device-scale-factor=2",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-logging",
    "--log-level=3",
    "--mute-audio"
]

def trim_image(im: Image.Image, tolerance: int = 6) -> Image.Image:
    """Trim near-white margins from a rendered HTML screenshot."""
    rgb_image = im.convert("RGB")

    white_bg = Image.new("RGB", rgb_image.size, (255, 255, 255))
    diff_white = ImageChops.difference(rgb_image, white_bg).convert("L")
    if tolerance > 0:
        diff_white = diff_white.point(lambda p: 255 if p > tolerance else 0)
    bbox = diff_white.getbbox()
    full_bbox = (0, 0, im.width, im.height)

    if not bbox or bbox == full_bbox:
        # Fallback: use the top-left pixel as background reference
        bg_color = rgb_image.getpixel((0, 0))
        bg_image = Image.new("RGB", rgb_image.size, bg_color)
        diff_bg = ImageChops.difference(rgb_image, bg_image).convert("L")
        if tolerance > 0:
            diff_bg = diff_bg.point(lambda p: 255 if p > tolerance else 0)
        bbox = diff_bg.getbbox()

    return im.crop(bbox) if bbox and bbox != full_bbox else im

@lru_cache(maxsize=100)
def _get_cached_avatar_data_uri(url: str, b64_data: str) -> str:
    """Cache the final data URI string."""
    return f"data:image/png;base64,{b64_data}"

async def get_avatar_data_uri(client, url: str) -> str:
    """Fetch and cache avatar data URIs (limited to 100 entries for t3.micro)."""
    # We use a simple hash of the URL to check if we've seen this specific avatar version
    # Discord URLs include a hash, so this is perfect.
    
    # Check if already in cache (we'll use a manual check for the async part)
    if not hasattr(client, "_avatar_cache"):
        client._avatar_cache = {}
    
    if url in client._avatar_cache:
        return client._avatar_cache[url]
    
    try:
        async with client.session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                b64_data = base64.b64encode(data).decode("utf-8")
                data_uri = f"data:image/png;base64,{b64_data}"
                
                # Manual LRU logic to keep it simple and safe for t3.micro
                if len(client._avatar_cache) >= 100:
                    # Pop a random (or first) item if full
                    client._avatar_cache.pop(next(iter(client._avatar_cache)))
                
                client._avatar_cache[url] = data_uri
                return data_uri
    except Exception as e:
        print(f"Error fetching avatar {url}: {e}")
    
    return "https://cdn.discordapp.com/embed/avatars/0.png"

def encode_image_to_data_uri(image_path: str) -> str:
    with open(image_path, "rb") as img_file:
        data = img_file.read()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/png;base64,{encoded}"

def _screenshot_html_sync(
    html_str: str,
    size: tuple[int, int] = (1600, 1000),
    apply_trim: bool = True
) -> io.BytesIO:
    """Synchronous implementation of screenshot_html."""
    output_file = f"{uuid.uuid4()}.png"
    buffer = io.BytesIO()
    try:
        hti.screenshot(html_str=html_str, save_as=output_file, size=size)

        with Image.open(output_file) as image:
            processed = trim_image(image) if apply_trim else image.copy()
            processed.save(buffer, format="PNG")
            buffer.seek(0)
    finally:
        if os.path.exists(output_file):
            os.remove(output_file)

    return buffer

async def screenshot_html(
    html_str: str,
    size: tuple[int, int] = (1600, 1000),
    *,
    apply_trim: bool = True
) -> io.BytesIO:
    """Render HTML into a trimmed PNG (non-blocking)."""
    return await asyncio.to_thread(_screenshot_html_sync, html_str, size, apply_trim)

def calculate_text_dimensions(font, text: str) -> tuple[int, int]:
    text_bbox = font.getbbox(text)
    width = text_bbox[2] - text_bbox[0]
    height = text_bbox[3] - text_bbox[1]
    return width, height

def find_non_overlapping_position(
    font,
    text: str,
    bounds: tuple[tuple[int, int], tuple[int, int]],
    existing_positions: list,
    max_attempts: int = 100
) -> tuple[int, int] | None:
    text_width, text_height = calculate_text_dimensions(font, text)

    if text_width > (bounds[1][0] - bounds[0][0]) or text_height > (bounds[1][1] - bounds[0][1]):
        raise ValueError("Text is too large to fit within the bounds")

    for _ in range(max_attempts):
        x = random.randint(bounds[0][0], bounds[1][0] - text_width)
        y = random.randint(bounds[0][1], bounds[1][1] - text_height)
        new_position = (x, y, x + text_width, y + text_height)

        if not any(
            pos[0] < new_position[2] and pos[2] > new_position[0] and
            pos[1] < new_position[3] and pos[3] > new_position[1]
            for pos in existing_positions
        ):
            return (x, y)

    return None

def random_color_excluding_blue_and_dark():
    while True:
        r = random.randint(100, 255)
        g = random.randint(100, 255)
        b = random.randint(0, 100)
        if r > 100 or g > 100:
            return (r, g, b)

get_text_position = find_non_overlapping_position
