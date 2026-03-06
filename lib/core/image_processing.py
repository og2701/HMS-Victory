import base64
import asyncio
import io
import os
import uuid
import random
from functools import lru_cache
from PIL import Image, ImageChops
import tempfile
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from config import CHROME_PATH

import shutil
import atexit

# Use a persistent user data directory to prevent Selenium from leaking temp profiles in /tmp
user_data_dir = os.path.abspath(os.path.join(os.getcwd(), ".chrome_data"))
if not os.path.exists(user_data_dir):
    os.makedirs(user_data_dir, exist_ok=True)

chrome_options = Options()
chrome_options.add_argument(f"--user-data-dir={user_data_dir}")

if CHROME_PATH and os.path.exists(CHROME_PATH):
    chrome_options.binary_location = CHROME_PATH

chrome_options.add_argument("--headless")
# Removed --force-device-scale-factor=2 as it quadruples memory usage on rendering
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-software-rasterizer")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-logging")
chrome_options.add_argument("--log-level=3")
chrome_options.add_argument("--mute-audio")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--disable-background-networking")
chrome_options.add_argument("--no-first-run")
chrome_options.add_argument("--disable-sync")
chrome_options.add_argument("--remote-debugging-pipe") # More stable than port for headless

# Aggressive memory and disk optimizations for t3.micro
chrome_options.add_argument("--disable-site-isolation-trials") # Saves significant per-tab memory
chrome_options.add_argument("--js-flags=--max-old-space-size=256") # Cap JS heap
chrome_options.add_argument("--disk-cache-size=1") # Prevent disk bloat
chrome_options.add_argument("--disable-application-cache")
chrome_options.add_argument("--disable-background-timer-throttling")
chrome_options.add_argument("--incognito") # Don't persist session data to disk

_browser = None
_render_count = 0
MAX_RENDERS_BEFORE_RESTART = 50

def get_browser():
    """Get the persistent browser instance, restarting it periodically to clear memory leaks."""
    global _browser, _render_count
    
    if _browser is None or _render_count >= MAX_RENDERS_BEFORE_RESTART:
        if _browser is not None:
            logging.info(f"Restarting headless Chrome engine (reached {MAX_RENDERS_BEFORE_RESTART} renders) to clear memory.")
            try:
                _browser.quit()
            except:
                pass
                
        # Fast disk cleanup of Chrome user data
        if os.path.exists(user_data_dir):
            shutil.rmtree(user_data_dir, ignore_errors=True)
        os.makedirs(user_data_dir, exist_ok=True)
            
        try:
            chrome_service = Service(ChromeDriverManager().install())
            _browser = webdriver.Chrome(service=chrome_service, options=chrome_options)
        except Exception as e:
            logging.warning(f"Failed to use ChromeDriverManager, falling back to default driver: {e}")
            _browser = webdriver.Chrome(options=chrome_options)
            
        _render_count = 0
        
    _render_count += 1
    return _browser

def cleanup_browser():
    global _browser
    try:
        if _browser:
            _browser.quit()
        if os.path.exists(user_data_dir):
            shutil.rmtree(user_data_dir, ignore_errors=True)
    except:
        pass

atexit.register(cleanup_browser)

# Global lock to prevent concurrent heavy image processing (critical for t3.micro)
rendering_lock = asyncio.Semaphore(1)

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
    if client is None:
        return url

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
    ext = os.path.splitext(image_path)[1].lower()
    mime_type = "image/png"  # Default
    if ext == ".svg":
        mime_type = "image/svg+xml"
    elif ext == ".gif":
        mime_type = "image/gif"
    elif ext == ".webp":
        mime_type = "image/webp"
    elif ext in [".jpg", ".jpeg"]:
        mime_type = "image/jpeg"

    with open(image_path, "rb") as img_file:
        data = img_file.read()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"

from typing import Tuple

def _screenshot_html_sync(
    html_str: str,
    size: Tuple[int, int] = (1600, 1000),
    apply_trim: bool = True
) -> io.BytesIO:
    """Synchronous implementation of screenshot_html."""
    buffer = io.BytesIO()
    
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp.write(html_str)
        tmp_path = tmp.name

    try:
        browser = get_browser()
        browser.set_window_size(size[0], size[1])
        browser.get(f"file://{os.path.abspath(tmp_path)}")
        
        png_bytes = browser.get_screenshot_as_png()

        with Image.open(io.BytesIO(png_bytes)) as image:
            processed = trim_image(image) if apply_trim else image.copy()
            processed.save(buffer, format="PNG")
            buffer.seek(0)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return buffer

async def screenshot_html(
    html_str: str,
    size: Tuple[int, int] = (1600, 1000),
    *,
    apply_trim: bool = True
) -> io.BytesIO:
    """Render HTML into a trimmed PNG (non-blocking, queued)."""
    async with rendering_lock:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _screenshot_html_sync, html_str, size, apply_trim)

def calculate_text_dimensions(font, text: str) -> Tuple[int, int]:
    text_bbox = font.getbbox(text)
    width = text_bbox[2] - text_bbox[0]
    height = text_bbox[3] - text_bbox[1]
    return width, height

from typing import Tuple, Optional

def find_non_overlapping_position(
    font,
    text: str,
    bounds: Tuple[Tuple[int, int], Tuple[int, int]],
    existing_positions: list,
    max_attempts: int = 100
) -> Optional[Tuple[int, int]]:
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


def generate_shop_preview_grid(items: list, cols: int = 2) -> io.BytesIO:
    """
    Generate a stitched grid of previews for rank items.
    Items should be a list of objects with type, name and relevant data.
    """
    from PIL import ImageDraw, ImageFont, Image
    
    # High resolution (600x300 per item) to make the image smaller for faster Discord uploads
    preview_width = 600
    preview_height = 300
    padding = 30
    
    rows = (len(items) + cols - 1) // cols
    grid_width = (preview_width * cols) + (padding * (cols + 1))
    grid_height = (preview_height * rows) + (padding * (rows + 1))
    
    canvas = Image.new("RGB", (grid_width, grid_height), (30, 31, 34)) # Discord dark bg
    draw = ImageDraw.Draw(canvas)
    
    from config import BASE_DIR

    # Try to load a font for numbering
    font = None
    try:
        font_path = os.path.join(BASE_DIR, "data", "fluff.ttf")
        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, 55) # Scale font down 50%
    except:
        pass
        
    if not font:
        try:
            font = ImageFont.load_default(size=55)
        except:
            font = ImageFont.load_default()

    for idx, item in enumerate(items):
        row = idx // cols
        col = idx % cols
        
        x = padding + col * (preview_width + padding)
        y = padding + row * (preview_height + padding)
        
        # Create preview based on item type
        preview = None
        
        # Detect item type by attributes
        if hasattr(item, 'bg_filename'): # Background
            bg_path = os.path.join(BASE_DIR, "data", "rank_cards", item.bg_filename)
            if os.path.exists(bg_path):
                with Image.open(bg_path) as img:
                    preview = img.convert("RGBA").resize((preview_width, preview_height), Image.Resampling.LANCZOS)
            else:
                preview = Image.new("RGBA", (preview_width, preview_height), (100, 100, 100))
                p_draw = ImageDraw.Draw(preview)
                p_draw.text((40, 250), f"MISSING:\n{item.bg_filename}", fill="white", font=font)
        elif hasattr(item, 'primary'): # Color Theme
            preview = Image.new("RGBA", (preview_width, preview_height), (40, 44, 52))
            p_draw = ImageDraw.Draw(preview)
            # Draw swatches (scaled for 600x300)
            p_draw.rectangle([30, 60, 180, 210], fill=item.primary, outline="white", width=3)
            p_draw.rectangle([210, 60, 360, 210], fill=item.secondary, outline="white", width=3)
            p_draw.rectangle([390, 60, 540, 210], fill=item.tertiary, outline="white", width=3)
            p_draw.text((30, 230), item.name, fill="white", font=font)
        else: # Reset or unknown
             # For reset, maybe show the standard union jack or a generic label
             preview = Image.new("RGBA", (preview_width, preview_height), (60, 60, 60))
             p_draw = ImageDraw.Draw(preview)
             p_draw.text((preview_width//2 - 250, preview_height//2 - 50), "DEFAULT / RESET", fill="white", font=font)

        if preview:
            canvas.paste(preview, (x, y), preview if preview.mode == 'RGBA' else None)
        
        # Draw number badge (scaled for double digits at 50% size)
        badge_width = 80 if idx >= 9 else 70
        badge_height = 70
        badge_x = x + 15
        badge_y = y + 15
        draw.ellipse([badge_x, badge_y, badge_x + badge_width, badge_y + badge_height], fill=(255, 0, 0))
        
        # Center the text slightly better for double digits
        text_x = badge_x + 12 if idx >= 9 else badge_x + 20
        draw.text((text_x, badge_y + 5), str(idx + 1), fill="white", font=font)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer

# Use LRU cache to keep the grid in memory for the life of the bot process
# We convert items to a string representation for the cache key since objects aren't hashable
@lru_cache(maxsize=4)
def _getCachedShopPreview(items_str: str, cols: int) -> bytes:
    """Helper to cache the raw bytes of the generated grid."""
    import ast
    # Reconstruct a simplified list of objects just for rendering
    class DummyItem:
        pass
        
    items = []
    for item_dict in ast.literal_eval(items_str):
        obj = DummyItem()
        for k, v in item_dict.items():
            setattr(obj, k, v)
        items.append(obj)
        
    buffer = generate_shop_preview_grid(items, cols)
    return buffer.getvalue()

async def generate_shop_preview_grid_async(items: list, cols: int = 4) -> io.BytesIO:
    """Queued async wrapper for grid generation with process-level caching."""
    async with rendering_lock:
        loop = asyncio.get_event_loop()
        
        # Create a cacheable string representation of the items we care about for rendering
        cacheable_items = []
        for item in items:
            cache_dict = {'name': item.name}
            if hasattr(item, 'bg_filename'):
                cache_dict['bg_filename'] = item.bg_filename
            elif hasattr(item, 'primary'):
                cache_dict['primary'] = item.primary
                cache_dict['secondary'] = item.secondary
                cache_dict['tertiary'] = item.tertiary
            cacheable_items.append(cache_dict)
            
        items_str = str(cacheable_items)
        
        # Run the cached generation in executor
        img_bytes = await loop.run_in_executor(None, _getCachedShopPreview, items_str, cols)
        return io.BytesIO(img_bytes)
