from discord import File
from PIL import Image, ImageDraw, ImageFont
import json
import os
import random

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
    6: ((4, 456), (402, 525))
}

def random_color_excluding_blue_and_dark():
    while True:
        r = random.randint(100, 255)
        g = random.randint(100, 255)
        b = random.randint(0, 100)
        if r > 100 or g > 100:
            return (r, g, b)

async def add_iceberg_text(interaction, text: str, level: int):
    """
    Adds text to a specified level on the iceberg image and stores the text in a JSON file.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        text (str): The text to place on the iceberg.
        level (int): The level on the iceberg where the text should be placed.

    Returns:
        None
    """
    if level not in LEVEL_BOUNDS:
        await interaction.response.send_message("Invalid level. Please choose a level between 1 and 6.", ephemeral=True)
        return

    if os.path.exists(TEXT_DATA_FILE):
        with open(TEXT_DATA_FILE, "r") as f:
            iceberg_texts = json.load(f)
    else:
        iceberg_texts = {str(i): [] for i in range(1, 7)}

    iceberg_texts[str(level)].append(text)

    with open(TEXT_DATA_FILE, "w") as f:
        json.dump(iceberg_texts, f)

    img = Image.open(ICEBERG_IMAGE_PATH)
    draw = ImageDraw.Draw(img)
    
    font_size = 14
    try:
        font = ImageFont.truetype(FONT_PATH, font_size)
        print(f"Loaded font with size: {font_size}")
    except IOError:
        print(f"Failed to load custom font. Falling back to default font with size: {font_size}")
        font = ImageFont.load_default()
    
    shadow_color = "black"
    shadow_offset = (2, 2)

    def get_text_position(text, bounds, existing_positions):
        text_bbox = font.getbbox(text)
        text_width, text_height = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        
        if text_width > (bounds[1][0] - bounds[0][0]) or text_height > (bounds[1][1] - bounds[0][1]):
            raise ValueError("Text is too large to fit within the bounds")

        max_attempts = 100
        for _ in range(max_attempts):
            x = random.randint(bounds[0][0], bounds[1][0] - text_width)
            y = random.randint(bounds[0][1], bounds[1][1] - text_height)
            new_position = (x, y, x + text_width, y + text_height)

            if not any(
                pos[0] < new_position[2] and pos[2] > new_position[0] and pos[1] < new_position[3] and pos[3] > new_position[1]
                for pos in existing_positions
            ):
                return x, y
        return None

    positions = []
    for lvl, texts in iceberg_texts.items():
        bounds = LEVEL_BOUNDS[int(lvl)]
        for txt in texts:
            try:
                pos = get_text_position(txt, bounds, positions)
                if pos:
                    shadow_pos = (pos[0] + shadow_offset[0], pos[1] + shadow_offset[1])
                    draw.text(shadow_pos, txt, font=font, fill=shadow_color)
                    text_color = random_color_excluding_blue_and_dark()
                    draw.text(pos, txt, font=font, fill=text_color)
                    text_bbox = font.getbbox(txt)
                    text_width, text_height = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
                    positions.append((pos[0], pos[1], pos[0] + text_width, pos[1] + text_height))
            except ValueError as e:
                print(f"Skipping text '{txt}' because: {e}")

    img.save(UPDATED_IMAGE_PATH)

    file = File(UPDATED_IMAGE_PATH, filename="updated_iceberg.png")
    await interaction.response.send_message(file=file)
