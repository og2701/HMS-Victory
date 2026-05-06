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
        return False

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
        # Account for shadow (2px) and general padding (5px) in collision detection
        existing_positions.append((x - 2, y - 2, x + w + 7, y + h + 7))

    # 2. Calculate attributes for the NEW entry — retry with smaller padding if crowded
    bounds = LEVEL_BOUNDS[level]
    pos = None
    for padding in [10, 5, 2, 0]:
        pos = get_text_position(font, text, bounds, existing_positions, padding=padding)
        if pos:
            break

    if not pos:
        if interaction.response.is_done():
            await interaction.followup.send("❌ Could not find a free spot for this text at this level. It might be too crowded!", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Could not find a free spot for this text at this level. It might be too crowded!", ephemeral=True)
        return False

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

    return True

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
        txt_w = text_bbox[2] - text_bbox[0]
        txt_h = text_bbox[3] - text_bbox[1]
        
        # Buffer for rotation to avoid clipping
        buffer = 20
        w = txt_w + buffer * 2
        h = txt_h + buffer * 2
        
        # Shadow
        txt_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_img)
        d.text((buffer, buffer), text, font=font, fill="black")
        rot_shadow = txt_img.rotate(rotation, resample=Image.BICUBIC, expand=1)
        
        # Color text
        txt_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(txt_img)
        d.text((buffer, buffer), text, font=font, fill=color)
        rot_text = txt_img.rotate(rotation, resample=Image.BICUBIC, expand=1)
        
        # Paste shadow then text
        # Subtract the rotation expansion/shift to align (buffer, buffer) to (x, y)
        shift_x = (rot_text.width - w) // 2 + buffer
        shift_y = (rot_text.height - h) // 2 + buffer
        
        img.paste(rot_shadow, (x - shift_x + shadow_offset[0], y - shift_y + shadow_offset[1]), rot_shadow)
        img.paste(rot_text, (x - shift_x, y - shift_y), rot_text)

    img.save(ICEBERG_CACHE_PATH)
