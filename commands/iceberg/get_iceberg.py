import os
from discord import File
from PIL import Image, ImageDraw, ImageFont
import json
from config import *
from lib.utils import random_color_excluding_blue_and_dark, get_text_position

ICEBERG_IMAGE_PATH = "data/image.png"
UPDATED_IMAGE_PATH = "data/updated_iceberg.png"
TEXT_DATA_FILE = "data/iceberg_texts.json"
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
    if not os.path.exists(ICEBERG_IMAGE_PATH):
        await interaction.response.send_message("The base iceberg image does not exist.", ephemeral=True)
        return

    img = Image.open(ICEBERG_IMAGE_PATH)
    draw = ImageDraw.Draw(img)
    font_size = 14
    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
    except IOError:
        font = ImageFont.load_default()

    shadow_color = "black"
    shadow_offset = (2, 2)
    if os.path.exists(TEXT_DATA_FILE):
        with open(TEXT_DATA_FILE, "r") as f:
            iceberg_texts = json.load(f)
    else:
        iceberg_texts = {str(i): [] for i in range(1, 7)}

    positions = []
    for lvl, texts in iceberg_texts.items():
        bounds = LEVEL_BOUNDS[int(lvl)]
        for txt in texts:
            try:
                pos = get_text_position(font, txt, bounds, positions)
                if pos:
                    shadow_pos = (pos[0] + shadow_offset[0], pos[1] + shadow_offset[1])
                    draw.text(shadow_pos, txt, font=font, fill=shadow_color)
                    text_color = random_color_excluding_blue_and_dark()
                    draw.text(pos, txt, font=font, fill=text_color)
                    text_bbox = font.getbbox(txt)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_height = text_bbox[3] - text_bbox[1]
                    positions.append((pos[0], pos[1], pos[0] + text_width, pos[1] + text_height))
            except ValueError as e:
                print(f"Skipping text '{txt}' because: {e}")
    img.save(UPDATED_IMAGE_PATH)
    file = File(UPDATED_IMAGE_PATH, filename="current_iceberg.png")
    await interaction.response.send_message(file=file)
