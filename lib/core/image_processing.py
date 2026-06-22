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
import gc
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

import time
_browser = None
_render_count = 0
_browser_started_at = 0.0
# One headless Chrome stays warm for the whole bot process (torn down only when the process exits,
# e.g. on a deploy restart). It is NOT shut down on idle; instead maintain_render_engine() recycles
# it for memory leaks in the background, while no render is in flight, so /rank and the casino games
# almost never pay a cold launch. These thresholds drive that background recycle.
_RECYCLE_AFTER_RENDERS = 15      # background keeper recycles once this many renders have happened
MAX_BROWSER_AGE_SECONDS = 1800   # ...or after 30 min of life (slow-leak backstop), if it rendered
MAX_RENDERS_BEFORE_RESTART = 30  # inline hard cap get_browser uses (also set on a render failure
                                 # to force a relaunch on the next call)
_chromedriver_path = None  # resolved once, reused on every restart


def _get_chromedriver_path():
    """Resolve the chromedriver path once and cache it. ChromeDriverManager().install() does a
    version check (and can hit the network / disk) every call - running it on every idle restart
    was a chunk of the cold-start cost. Resolve it on the first launch only; later restarts reuse
    the path. Falls back to a fresh resolve if the cached binary somehow goes missing."""
    global _chromedriver_path
    if _chromedriver_path is None or not os.path.exists(_chromedriver_path):
        _chromedriver_path = ChromeDriverManager().install()
    return _chromedriver_path


def _sweep_chrome_tmp(max_age_seconds=60):
    """Headless Chrome leaks per-process temp dirs in /tmp (crashpad / shared-memory / scoped
    dirs) whenever it crashes - they pile up and eventually fill the disk, which then breaks
    rendering (half-drawn cards, missing icons). Remove the STALE ones (older than
    max_age_seconds, so an in-flight render's dir is never touched). Runs on each Chrome
    (re)start, so /tmp stays clean on its own."""
    import glob, tempfile
    now = time.time()
    tmp = tempfile.gettempdir()
    removed = 0
    # Chrome names these "org.chromium.XXXXXX" / "com.google.Chrome.XXXXXX" (no leading dot on
    # this build); keep the dotted variants too for older/other builds.
    for pat in ("org.chromium.*", ".org.chromium.*",
                "com.google.Chrome.*", ".com.google.Chrome.*", "scoped_dir*"):
        for path in glob.glob(os.path.join(tmp, pat)):
            try:
                if now - os.path.getmtime(path) > max_age_seconds:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
            except OSError:
                pass
    if removed:
        logging.info(f"Swept {removed} leaked Chrome temp dir(s) from {tmp}.")


def _launch_browser():
    """Tear down any existing Chrome and start a fresh one. The caller MUST hold rendering_lock
    (or otherwise guarantee no screenshot is in flight) so the browser is never quit mid-render."""
    global _browser, _render_count, _browser_started_at

    if _browser is not None:
        logging.info("Recycling headless Chrome engine to clear memory.")
        try:
            _browser.quit()   # kill the underlying Chrome process
        except Exception as e:
            logging.warning(f"Error while quitting Chrome: {e}")
        finally:
            _browser = None

    # Fresh profile + clear leaked /tmp Chrome dirs so the disk can't fill
    if os.path.exists(user_data_dir):
        shutil.rmtree(user_data_dir, ignore_errors=True)
    os.makedirs(user_data_dir, exist_ok=True)
    _sweep_chrome_tmp()

    try:
        _browser = webdriver.Chrome(service=Service(_get_chromedriver_path()), options=chrome_options)
    except Exception as e:
        logging.warning(f"Failed to use ChromeDriverManager, falling back to default driver: {e}")
        _browser = webdriver.Chrome(options=chrome_options)

    try:
        _browser.get("about:blank")   # warm up so the first real render doesn't fail
    except Exception:
        pass

    _render_count = 0
    _browser_started_at = time.time()
    return _browser


def get_browser():
    """Return the live headless Chrome, launching it if it is down or has hit the inline render
    cap. There is no idle shutdown: the engine is kept warm for the bot's whole life and recycled
    for memory leaks in the background by maintain_render_engine(), so a user render rarely waits
    on a launch."""
    global _render_count
    if _browser is None or _render_count >= MAX_RENDERS_BEFORE_RESTART:
        _launch_browser()
    _render_count += 1
    return _browser

def cleanup_browser():
    global _browser
    try:
        if _browser:
            _browser.quit()
    except Exception as e:
        logging.warning(f"Error while cleaning up Chrome on exit: {e}")
    finally:
        _browser = None
        if os.path.exists(user_data_dir):
            shutil.rmtree(user_data_dir, ignore_errors=True)

atexit.register(cleanup_browser)

# Global lock to prevent concurrent heavy image processing (critical for t3.micro)
rendering_lock = asyncio.Semaphore(1)


def _maintain_render_engine_sync():
    """Pre-warm or leak-recycle the engine. Caller holds rendering_lock, so no render is in flight."""
    if _browser is None:
        _launch_browser()                       # pre-warm (just after boot, or after a crash)
        return "warmed"
    # Only recycle a browser that has actually rendered (leaks accumulate with use); a browser that
    # is simply sitting idle at 0 renders is left warm rather than needlessly relaunched.
    if _render_count >= _RECYCLE_AFTER_RENDERS or (
            _render_count > 0 and (time.time() - _browser_started_at) > MAX_BROWSER_AGE_SECONDS):
        _launch_browser()                       # proactive leak recycle, off the user path
        return "recycled"
    return None


async def maintain_render_engine():
    """Keep one headless Chrome warm for the bot's lifetime. Scheduled every ~60s: it pre-warms the
    engine and recycles it for memory leaks HERE, in the background while no render is in flight, so
    /rank and the casino games almost never pay a cold launch. Holding rendering_lock guarantees we
    never quit a browser mid-screenshot (and a recycle only ever delays a render that arrives during
    the ~2s relaunch, which would have paid that cost as a cold start anyway)."""
    loop = asyncio.get_running_loop()
    async with rendering_lock:
        try:
            result = await loop.run_in_executor(None, _maintain_render_engine_sync)
            if result:
                logging.info(f"Render engine {result} by the background keeper.")
        except Exception:
            logging.warning("Render engine keeper tick failed", exc_info=True)

def trim_image(im: Image.Image, tolerance: int = 6) -> Image.Image:
    """Trim near-white margins from a rendered HTML screenshot."""
    rgb_image = im.convert("RGB")

    white_bg = Image.new("RGB", rgb_image.size, (255, 255, 255))
    diff_white = ImageChops.difference(rgb_image, white_bg).convert("L")
    if tolerance > 0:
        diff_white = diff_white.point(lambda p: 255 if p > tolerance else 0)
    bbox = diff_white.getbbox()
    full_bbox = (0, 0, im.width, im.height)

    # If original bbox is too large or invalid, or if the background is solid non-white
    is_mostly_white = diff_white.histogram()[255] < (rgb_image.size[0] * rgb_image.size[1] * 0.1)
    if not bbox or bbox == full_bbox or not is_mostly_white:
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
    apply_trim: bool = True,
    element_selector: str = None
) -> io.BytesIO:
    """Synchronous implementation of screenshot_html."""
    global _browser, _render_count

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp.write(html_str)
        tmp_path = tmp.name

    last_err = None
    for attempt in range(2):
        try:
            buffer = io.BytesIO()
            browser = get_browser()
            browser.set_window_size(size[0], size[1])
            browser.get(f"file://{os.path.abspath(tmp_path)}")

            if element_selector:
                from selenium.webdriver.common.by import By
                from selenium.webdriver.support.ui import WebDriverWait
                import base64 as _b64
                # Wait for images inside the page to finish loading so layout is final
                try:
                    WebDriverWait(browser, 5).until(
                        lambda b: b.execute_script(
                            "return Array.from(document.images).every(i => i.complete)"
                        )
                    )
                except Exception:
                    pass
                element = browser.find_element(By.CSS_SELECTOR, element_selector)
                rect = browser.execute_script(
                    "const r = arguments[0].getBoundingClientRect();"
                    "return {x: r.left + window.scrollX, y: r.top + window.scrollY,"
                    " w: r.width, h: r.height};",
                    element,
                )
                # CDP capture with captureBeyondViewport handles elements taller
                # than the viewport without clipping.
                cdp_result = browser.execute_cdp_cmd(
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "clip": {
                            "x": rect["x"],
                            "y": rect["y"],
                            "width": rect["w"],
                            "height": rect["h"],
                            "scale": 1,
                        },
                        "captureBeyondViewport": True,
                    },
                )
                png_bytes = _b64.b64decode(cdp_result["data"])
            else:
                png_bytes = browser.get_screenshot_as_png()

            with Image.open(io.BytesIO(png_bytes)) as image:
                if element_selector:
                    processed = image.copy()
                else:
                    processed = trim_image(image) if apply_trim else image.copy()
                processed.save(buffer, format="PNG")
                buffer.seek(0)

            # Aggressive memory cleanup for t3.micro
            gc.collect()

            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            return buffer

        except Exception as e:
            last_err = e
            if attempt == 0:
                logging.warning(f"Screenshot attempt failed ({e}), restarting Chrome and retrying...")
                # Force browser restart on next get_browser() call
                _render_count = MAX_RENDERS_BEFORE_RESTART
            else:
                logging.error(f"Screenshot retry also failed: {e}")

    # Cleanup temp file if both attempts failed
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    raise last_err

async def screenshot_html(
    html_str: str,
    size: Tuple[int, int] = (1600, 1000),
    *,
    apply_trim: bool = True,
    element_selector: str = None
) -> io.BytesIO:
    """Render HTML into a trimmed PNG (non-blocking, queued)."""
    async with rendering_lock:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, _screenshot_html_sync, html_str, size, apply_trim, element_selector
        )


def _screenshot_html_sequence_sync(
    html_strings: list,
    size: Tuple[int, int] = (1600, 1000),
    element_selector: str = None,
    durations: list = None,
    loop: int = None
) -> io.BytesIO:
    """Synchronous implementation of screenshot_html_sequence."""
    global _browser, _render_count

    if not html_strings:
        raise ValueError("html_strings list cannot be empty")

    with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp.write(html_strings[0])
        tmp_path = tmp.name

    last_err = None
    for attempt in range(2):
        try:
            browser = get_browser()
            browser.set_window_size(size[0], size[1])
            browser.get(f"file://{os.path.abspath(tmp_path)}")

            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            import base64 as _b64

            # Wait for images to complete on first load
            try:
                WebDriverWait(browser, 5).until(
                    lambda b: b.execute_script(
                        "return Array.from(document.images).every(i => i.complete)"
                    )
                )
            except Exception:
                pass

            png_frames = []

            for idx, html_str in enumerate(html_strings):
                if idx > 0:
                    # Update body innerHTML in-place to avoid reloading files
                    escaped_html = html_str.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
                    browser.execute_script(
                        f"document.body.innerHTML = `{escaped_html}`;"
                        # Re-run Twemoji if it was loaded with the page (emoji img replacement)
                        "if (window.twemoji) { twemoji.parse(document.body, {folder: 'svg', ext: '.svg'}); }"
                    )
                    # Wait for any Twemoji SVG imgs to finish loading
                    try:
                        WebDriverWait(browser, 3).until(
                            lambda b: b.execute_script(
                                "return Array.from(document.querySelectorAll('img.emoji')).every(i => i.complete)"
                            )
                        )
                    except Exception:
                        pass
                    # Brief extra pause for DOM rendering
                    time.sleep(0.05)
                else:
                    # Frame 0: wait for Twemoji SVG images to fully load after window.onload
                    try:
                        WebDriverWait(browser, 5).until(
                            lambda b: b.execute_script(
                                "return Array.from(document.querySelectorAll('img.emoji')).every(i => i.complete)"
                            )
                        )
                    except Exception:
                        pass

                if element_selector:
                    element = browser.find_element(By.CSS_SELECTOR, element_selector)
                    rect = browser.execute_script(
                        "const r = arguments[0].getBoundingClientRect();"
                        "return {x: r.left + window.scrollX, y: r.top + window.scrollY,"
                        " w: r.width, h: r.height};",
                        element,
                    )
                    cdp_result = browser.execute_cdp_cmd(
                        "Page.captureScreenshot",
                        {
                            "format": "png",
                            "clip": {
                                "x": rect["x"],
                                "y": rect["y"],
                                "width": rect["w"],
                                "height": rect["h"],
                                "scale": 1,
                            },
                            "captureBeyondViewport": True,
                        },
                    )
                    png_bytes = _b64.b64decode(cdp_result["data"])
                else:
                    png_bytes = browser.get_screenshot_as_png()

                png_frames.append(png_bytes)

            # Compile into animated GIF using Pillow
            images = []
            for p_bytes in png_frames:
                images.append(Image.open(io.BytesIO(p_bytes)))

            buffer = io.BytesIO()
            # Default to 180ms per frame if durations is not provided
            frame_durations = durations if durations else [180] * len(images)

            # Save the sequence as an animated GIF
            save_kwargs = {
                "format": "GIF",
                "save_all": True,
                "append_images": images[1:],
                "duration": frame_durations,
            }
            if loop is not None:
                save_kwargs["loop"] = loop

            images[0].save(buffer, **save_kwargs)
            buffer.seek(0)

            gc.collect()

            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            return buffer

        except Exception as e:
            last_err = e
            if attempt == 0:
                logging.warning(f"Screenshot sequence attempt failed ({e}), restarting Chrome and retrying...")
                _render_count = MAX_RENDERS_BEFORE_RESTART
            else:
                logging.error(f"Screenshot sequence retry also failed: {e}")

    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    raise last_err


async def screenshot_html_sequence(
    html_strings: list,
    size: Tuple[int, int] = (1600, 1000),
    *,
    element_selector: str = None,
    durations: list = None,
    loop: int = None
) -> io.BytesIO:
    """Render a sequence of HTML strings into an animated GIF (non-blocking, queued)."""
    async with rendering_lock:
        async_loop = asyncio.get_event_loop()
        return await async_loop.run_in_executor(
            None, _screenshot_html_sequence_sync, html_strings, size, element_selector, durations, loop
        )



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
    max_attempts: int = 100,
    padding: int = 8
) -> Optional[Tuple[int, int]]:
    text_width, text_height = calculate_text_dimensions(font, text)
    
    # Apply safety margin to bounds to prevent line crossing
    safety_margin = 2
    inner_bounds_min = (bounds[0][0] + safety_margin, bounds[0][1] + safety_margin)
    inner_bounds_max = (bounds[1][0] - safety_margin, bounds[1][1] - safety_margin)

    if text_width > (inner_bounds_max[0] - inner_bounds_min[0]) or text_height > (inner_bounds_max[1] - inner_bounds_min[1]):
        raise ValueError("Text is too large to fit within the bounds")

    for _ in range(max_attempts):
        x = random.randint(inner_bounds_min[0], inner_bounds_max[0] - text_width)
        y = random.randint(inner_bounds_min[1], inner_bounds_max[1] - text_height)
        
        # New rect with padding for collision check
        new_position = (x - padding, y - padding, x + text_width + padding, y + text_height + padding)

        if not any(
            pos[0] < new_position[2] and pos[2] > new_position[0] and
            pos[1] < new_position[3] and pos[3] > new_position[1]
            for pos in existing_positions
        ):
            return (x, y)

    return None

def random_color_excluding_blue_and_dark():
    """Pick a random RGB colour that is neither too blue nor too dark, so iceberg
    text stays legible on the dark background. (The old `r > 100 or g > 100` guard
    was effectively a no-op - it only rejected exact (100, 100, b) - and did not
    actually exclude dark colours; this uses a real luminance threshold.)"""
    while True:
        r = random.randint(100, 255)
        g = random.randint(100, 255)
        b = random.randint(0, 100)
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        if luminance >= 120:
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
