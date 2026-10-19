import os
from discord import File
from PIL import Image, ImageDraw, ImageFont
import json
from config import *
from lib.core.image_processing import random_color_excluding_blue_and_dark, get_text_position

ICEBERG_IMAGE_PATH = "data/image.png"
ICEBERG_CACHE_PATH = "data/iceberg_cache.png"
from database import DatabaseManager
from .add_to_iceberg import render_iceberg_cache
from lib.features import iceberg_layout
FONT_PATH = "data/fluff.ttf"
LEVEL_BOUNDS = {
    1: ((4, 2), (404, 81)),
    2: ((4, 89), (404, 163)),
    3: ((4, 174), (404, 266)),
    4: ((7, 276), (402, 353)),
    5: ((5, 364), (404, 446)),
    6: ((4, 456), (402, 525)),
}

async def show_iceberg(interaction):
    # 1. Render the cache if it's missing, or older than the layout code (a deploy that
    # changes how entries are placed should show up without waiting for the next entry).
    if (not os.path.exists(ICEBERG_CACHE_PATH)
            or os.path.getmtime(ICEBERG_CACHE_PATH) < os.path.getmtime(iceberg_layout.__file__)):
        await render_iceberg_cache()

    # 2. Serve the cache if it exists
    if os.path.exists(ICEBERG_CACHE_PATH):
        file = File(ICEBERG_CACHE_PATH, filename="iceberg.png")
        await interaction.followup.send(file=file)
    else:
        # Fallback if both fail
        if os.path.exists(ICEBERG_IMAGE_PATH):
            file = File(ICEBERG_IMAGE_PATH, filename="base_iceberg.png")
            await interaction.followup.send("⚠️ Rendering failed. Here is the base image:", file=file)
        else:
            await interaction.followup.send("❌ Error: Iceberg image files not found.")
