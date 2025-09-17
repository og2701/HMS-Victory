import base64
import io
import os
import uuid
from PIL import Image
from html2image import Html2Image
from config import CHROME_PATH

hti = Html2Image(output_path=".", browser_executable=CHROME_PATH)
hti.browser.flags += [
    "--force-device-scale-factor=2",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--no-sandbox"
]

def _normalize_rgb(color) -> tuple[int, int, int]:
    if isinstance(color, int):
        return (color, color, color)
    if len(color) >= 3:
        return (color[0], color[1], color[2])
    if len(color) == 1:
        return (color[0], color[0], color[0])
    raise ValueError("Unexpected color format")


def _collect_edge_colors(image: Image.Image) -> list[tuple[int, int, int]]:
    width, height = image.size
    positions = {
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, 0),
        (width // 2, height - 1),
        (0, height // 2),
        (width - 1, height // 2),
    }
    colors = []
    for pos in positions:
        colors.append(_normalize_rgb(image.getpixel(pos)))
    # Deduplicate while preserving order
    unique = []
    for color in colors:
        if color not in unique:
            unique.append(color)
    return unique


def _is_background(pixel, candidates: list[tuple[int, int, int]], tolerance: int) -> bool:
    pixel_rgb = _normalize_rgb(pixel)
    for bg in candidates:
        if max(abs(pixel_rgb[i] - bg[i]) for i in range(3)) <= tolerance:
            return True
    return False


def trim_image(im: Image.Image, tolerance: int = 6) -> Image.Image:
    """Trim uniform margins from a rendered HTML screenshot."""
    rgb_image = im.convert("RGB")
    width, height = rgb_image.size
    edge_colors = _collect_edge_colors(rgb_image)
    pixels = rgb_image.load()

    def find_top() -> int:
        top = 0
        while top < height:
            if all(_is_background(pixels[x, top], edge_colors, tolerance) for x in range(width)):
                top += 1
            else:
                break
        return top

    def find_bottom() -> int:
        bottom = height - 1
        while bottom >= 0:
            if all(_is_background(pixels[x, bottom], edge_colors, tolerance) for x in range(width)):
                bottom -= 1
            else:
                break
        return bottom

    def find_left(top: int, bottom: int) -> int:
        left = 0
        while left < width:
            if all(_is_background(pixels[left, y], edge_colors, tolerance) for y in range(top, bottom + 1)):
                left += 1
            else:
                break
        return left

    def find_right(top: int, bottom: int) -> int:
        right = width - 1
        while right >= 0:
            if all(_is_background(pixels[right, y], edge_colors, tolerance) for y in range(top, bottom + 1)):
                right -= 1
            else:
                break
        return right

    top = find_top()
    bottom = find_bottom()
    if top >= bottom:
        return im

    left = find_left(top, bottom)
    right = find_right(top, bottom)
    if left >= right:
        return im

    return im.crop((left, top, right + 1, bottom + 1))

def encode_image_to_data_uri(image_path: str) -> str:
    with open(image_path, "rb") as img_file:
        data = img_file.read()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:image/png;base64,{encoded}"

def screenshot_html(
    html_str: str,
    size: tuple[int, int] = (1600, 1000),
    *,
    apply_trim: bool = True
) -> io.BytesIO:
    """Render HTML into a trimmed PNG and return the bytes buffer."""
    requested_name = f"{uuid.uuid4()}.png"
    buffer = io.BytesIO()
    generated_path: str | None = None
    output_root = hti.output_path
    if isinstance(output_root, (list, tuple)):
        output_root = output_root[0] if output_root else "."
    if not output_root:
        output_root = "."
    output_root = os.path.abspath(output_root)
    target_path = os.path.join(output_root, requested_name)
    try:
        hti.screenshot(html_str=html_str, save_as=requested_name, size=size)
        generated_path = target_path

        if not os.path.exists(target_path):
            raise FileNotFoundError(f"Screenshot not found at {target_path}")

        with Image.open(target_path) as image:
            processed = trim_image(image) if apply_trim else image.copy()
            processed.save(buffer, format="PNG")
            buffer.seek(0)
    finally:
        if generated_path and os.path.exists(generated_path):
            os.remove(generated_path)

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
