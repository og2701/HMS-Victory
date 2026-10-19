import asyncio
import os
import random
from discord import File
from PIL import Image
from config import *
from lib.core.image_processing import random_color_excluding_blue_and_dark
from lib.features.iceberg_layout import LEVEL_BOUNDS, draw_entries, lay_out

ICEBERG_IMAGE_PATH = "data/image.png"
ICEBERG_CACHE_PATH = "data/iceberg_cache.png"
from database import DatabaseManager


def _iceberg_rows(where: str = "", params: tuple = ()):
    rows = DatabaseManager.fetch_all(
        f"SELECT id, text, level, x, y, color, rotation FROM iceberg WHERE x IS NOT NULL {where}", params)
    keys = ("id", "text", "level", "x", "y", "color", "rotation")
    return [dict(zip(keys, row)) for row in rows or []]

async def add_iceberg_text(interaction, text: str, level: int, show_image: bool = True):
    if level not in LEVEL_BOUNDS:
        if interaction.response.is_done():
            await interaction.followup.send("Invalid level. Please choose a level between 1 and 6.", ephemeral=True)
        else:
            await interaction.response.send_message("Invalid level. Please choose a level between 1 and 6.", ephemeral=True)
        return False

    # 1. Pick a random spot and tilt, then let the layout move it to the nearest free spot
    # in its tier (shrinking the tier's text if it's full) - the same layout the render uses.
    (x0, y0), (x1, y1) = LEVEL_BOUNDS[level]
    rotation = random.randint(-5, 5)
    new = {"id": float("inf"), "text": text, "level": level, "x": random.randint(x0, x1),
           "y": random.randint(y0, y1), "rotation": rotation, "color": None}
    laid = await asyncio.to_thread(lay_out, _iceberg_rows("AND level = ?", (level,)) + [new])
    pos = next((e["x"], e["y"]) for e in laid if e["id"] == new["id"])

    color_tuple = random_color_excluding_blue_and_dark()
    color_hex = '#%02x%02x%02x' % color_tuple

    # 2. Store in database
    DatabaseManager.execute(
        "INSERT INTO iceberg (text, level, x, y, color, rotation) VALUES (?, ?, ?, ?, ?, ?)",
        (text, level, pos[0], pos[1], color_hex, rotation)
    )

    # 3. Render the master cache image
    await render_iceberg_cache()

    # 4. Optionally send the file
    if show_image:
        if os.path.exists(ICEBERG_CACHE_PATH):
            file = File(ICEBERG_CACHE_PATH, filename="iceberg.png")
            await interaction.followup.send(file=file)
        else:
            await interaction.followup.send("✅ Added to iceberg, but cache rendering failed. Use /iceberg to view.")

    return True

async def render_iceberg_cache():
    """Renders all iceberg entries and saves to ICEBERG_CACHE_PATH."""
    if not os.path.exists(ICEBERG_IMAGE_PATH):
        return

    rows = _iceberg_rows()

    def render():
        img = Image.open(ICEBERG_IMAGE_PATH).convert("RGBA")
        draw_entries(img, lay_out(rows))
        img.save(ICEBERG_CACHE_PATH)

    # Laying out a crowded tier can take a moment, so keep it off the event loop.
    await asyncio.to_thread(render)
