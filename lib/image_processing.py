import base64
import io
import os
import uuid
from PIL import Image, ImageChops
from html2image import Html2Image
from config import CHROME_PATH

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)
hti.browser.flags += [
    "--force-device-scale-factor=2",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-sandbox"
]

def _normalize_color(color) -> tuple[int, int, int]:
    if isinstance(color, int):
        return (color, color, color)
    if len(color) == 1:
        return (color[0], color[0], color[0])
    return tuple(color[:3])


def trim_image(im: Image.Image, tolerance: int = 4) -> Image.Image:
    bg_color = im.getpixel((0, 0))
    rgb_image = im.convert("RGB")
    bg = Image.new("RGB", rgb_image.size, _normalize_color(bg_color))
    diff = ImageChops.difference(rgb_image, bg).convert("L")
    if tolerance > 0:
        diff = diff.point(lambda p: 255 if p > tolerance else 0)
    bbox = diff.getbbox()
    return im.crop(bbox) if bbox else im

def encode_image_to_data_uri(image_path: str) -> str:
    with open(image_path, "rb") as img_file:
        data = img_file.read()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/png;base64,{encoded}"

def screenshot_html(html_str: str, size: tuple[int, int] = (1600, 1000)) -> io.BytesIO:
    """Render HTML into a trimmed PNG and return the bytes buffer."""
    output_file = f"{uuid.uuid4()}.png"
    buffer = io.BytesIO()
    try:
        hti.screenshot(html_str=html_str, save_as=output_file, size=size)

        with Image.open(output_file) as image:
            trimmed = trim_image(image)
            trimmed.save(buffer, format="PNG")
            buffer.seek(0)
    finally:
        if os.path.exists(output_file):
            os.remove(output_file)

    return buffer

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
        import random
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
