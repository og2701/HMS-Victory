import os
import random
from discord import File
from PIL import Image, ImageDraw, ImageFont
import json
from config import *
from lib.core.image_processing import random_color_excluding_blue_and_dark, get_text_position

ICEBERG_IMAGE_PATH = "data/image.png"
ICEBERG_CACHE_PATH = "data/iceberg_cache.png"
from database import DatabaseManager
FONT_PATH = "data/fluff.ttf"
LEVEL_BOUNDS = {
    1: ((4, 2), (404, 81)),
    2: ((4, 89), (404, 163)),
    3: ((4, 174), (404, 266)),
    4: ((7, 276), (402, 353)),
    5: ((5, 364), (404, 446)),
    6: ((4, 456), (402, 525)),
}

async def add_iceberg_text(interaction, text: str, level: int, show_image: bool = True):
    if level not in LEVEL_BOUNDS:
        if interaction.response.is_done():
            await interaction.followup.send("Invalid level. Please choose a level between 1 and 6.", ephemeral=True)
        else:
            await interaction.response.send_message("Invalid level. Please choose a level between 1 and 6.", ephemeral=True)
        return

    # 1. Fetch all existing entries to find a non-overlapping position
    rows = DatabaseManager.fetch_all("SELECT x, y, text, level FROM iceberg WHERE x IS NOT NULL")
    existing_positions = []
    
    # We need a font to calculate dimensions for current entries
    font_size = 14
    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except IOError:
        font = ImageFont.load_default()

    for x, y, t, l in rows:
        text_bbox = font.getbbox(t)
        w = text_bbox[2] - text_bbox[0]
        h = text_bbox[3] - text_bbox[1]
        existing_positions.append((x, y, x + w, y + h))

    # 2. Calculate attributes for the NEW entry
    bounds = LEVEL_BOUNDS[level]
    pos = get_text_position(font, text, bounds, existing_positions)
    if not pos:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Could not find a free spot for this text at this level. It might be too crowded!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find a free spot for this text at this level. It might be too crowded!", ephemeral=True)
        return

    color_tuple = random_color_excluding_blue_and_dark()
    color_hex = '#%02x%02x%02x' % color_tuple
    rotation = random.randint(-5, 5)

    # 3. Store in database
    DatabaseManager.execute(
        "INSERT INTO iceberg (text, level, x, y, color, rotation) VALUES (?, ?, ?, ?, ?, ?)",
        (text, level, pos[0], pos[1], color_hex, rotation)
    )

    # 4. Render the master cache image
    await render_iceberg_cache()

    # 5. Optionally send the file
    if show_image:
        if os.path.exists(ICEBERG_CACHE_PATH):
            file = File(ICEBERG_CACHE_PATH, filename="iceberg.png")
            await interaction.followup.send(file=file)
        else:
            await interaction.followup.send("✅ Added to iceberg, but cache rendering failed. Use /iceberg to view.")

async def render_iceberg_cache():
    """Renders all iceberg entries and saves to ICEBERG_CACHE_PATH."""
    if not os.path.exists(ICEBERG_IMAGE_PATH):
        return

    img = Image.open(ICEBERG_IMAGE_PATH).convert("RGBA")
    font_size = 14
    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except IOError:
        font = ImageFont.load_default()

    rows = DatabaseManager.fetch_all("SELECT text, x, y, color, rotation FROM iceberg WHERE x IS NOT NULL")
    shadow_offset = (2, 2)
    
    for text, x, y, color, rotation in rows:
        # Create a temporary transparent layer for the rotated text
        text_bbox = font.getbbox(text)
        w = text_bbox[2] - text_bbox[0] + 10 # Buffer for rotation
        h = text_bbox[3] - text_bbox[1] + 10 # Buffer for rotation
        
        # Shadow
        txt_img = Image.new("RGBA", (w*2, h*2), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_img)
        d.text((w/2, h/2), text, font=font, fill="black")
        rot_shadow = txt_img.rotate(rotation, resample=Image.BICUBIC, expand=1)
        
        # Color text
        txt_img = Image.new("RGBA", (w*2, h*2), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_img)
        d.text((w/2, h/2), text, font=font, fill=color)
        rot_text = txt_img.rotate(rotation, resample=Image.BICUBIC, expand=1)
        
        # Paste shadow then text
        img.paste(rot_shadow, (x - int(w/2) + shadow_offset[0], y - int(h/2) + shadow_offset[1]), rot_shadow)
        img.paste(rot_text, (x - int(w/2), y - int(h/2)), rot_text)

    img.save(ICEBERG_CACHE_PATH)
