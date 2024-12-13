import requests
import base64
import html
import uuid
from PIL import Image, ImageChops
import difflib
from html2image import Html2Image

hti = Html2Image(output_path=".", browser_executable="/usr/bin/google-chrome")


def trim(im):
    bg = Image.new(im.mode, im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg)
    diff = ImageChops.add(diff, diff, 2.0, -100)
    bbox = diff.getbbox()
    if bbox:
        return im.crop(bbox)
    return im


def read_html_template(file_path):
    try:
        with open(file_path, "r") as file:
            return file.read()
    except Exception as e:
        print(f"Error reading HTML template {file_path}: {e}")
        return ""


def calculate_estimated_height(content, line_height=20, base_height=100):
    message_lines = content.split("\n")
    total_lines = sum(len(line) // 80 + 1 for line in message_lines)
    content_height = line_height * total_lines
    estimated_height = max(base_height, content_height + 100)
    return estimated_height


async def create_message_image(message, title):
    avatar_url = (
        message.author.avatar.url
        if message.author.avatar
        else message.author.default_avatar.url
    )
    response = requests.get(avatar_url)
    avatar_base64 = base64.b64encode(response.content).decode("utf-8")
    avatar_data_url = f"data:image/png;base64,{avatar_base64}"

    escaped_content = html.escape(message.content)
    estimated_height = calculate_estimated_height(escaped_content)

    border_color = message.author.color.to_rgb()
    display_name = message.author.display_name
    created_at = message.created_at.strftime("%H:%M")

    html_content = read_html_template("templates/deleted_message.html").format(
        title=title,
        border_color=border_color,
        avatar_data_url=avatar_data_url,
        display_name=display_name,
        created_at=created_at,
        content=escaped_content,
    )

    output_path = f"{uuid.uuid4()}.png"
    hti.screenshot(
        html_str=html_content, save_as=output_path, size=(800, estimated_height)
    )
    image = Image.open(output_path)
    image = trim(image)
    image.save(output_path)
    return output_path


def highlight_diff(before, after):
    sm = difflib.SequenceMatcher(None, before, after)
    highlighted_before = []
    highlighted_after = []
    changes_detected = False
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "replace":
            highlighted_before.append(
                f'<span style="background-color: red;">{html.escape(before[i1:i2])}</span>'
            )
            highlighted_after.append(
                f'<span style="background-color: green;">{html.escape(after[j1:j2])}</span>'
            )
            changes_detected = True
        elif tag == "delete":
            highlighted_before.append(
                f'<span style="background-color: red;">{html.escape(before[i1:i2])}</span>'
            )
            changes_detected = True
        elif tag == "insert":
            highlighted_after.append(
                f'<span style="background-color: green;">{html.escape(after[j1:j2])}</span>'
            )
            changes_detected = True
        elif tag == "equal":
            highlighted_before.append(html.escape(before[i1:i2]))
            highlighted_after.append(html.escape(after[j1:j2]))
    return "".join(highlighted_before), "".join(highlighted_after), changes_detected


async def create_edited_message_image(before, after):
    avatar_url = (
        before.author.avatar.url
        if before.author.avatar
        else before.author.default_avatar.url
    )
    response = requests.get(avatar_url)
    avatar_base64 = base64.b64encode(response.content).decode("utf-8")
    avatar_data_url = f"data:image/png;base64,{avatar_base64}"

    escaped_before_content = html.escape(before.content)
    escaped_after_content = html.escape(after.content)
    highlighted_before_content, highlighted_after_content, changes_detected = (
        highlight_diff(before.content, after.content)
    )
    if not changes_detected:
        return None

    before_height = calculate_estimated_height(highlighted_before_content)
    after_height = calculate_estimated_height(highlighted_after_content)
    content_height = before_height + after_height + 60
    estimated_height = max(150, content_height + 100)

    border_color = before.author.color.to_rgb()
    display_name = before.author.display_name
    before_created_at = before.created_at.strftime("%H:%M")
    after_created_at = after.created_at.strftime("%H:%M")

    html_content = read_html_template("templates/edited_message.html").format(
        border_color=border_color,
        avatar_data_url=avatar_data_url,
        display_name=display_name,
        before_created_at=before_created_at,
        before_content=highlighted_before_content,
        after_created_at=after_created_at,
        after_content=highlighted_after_content,
    )

    output_path = f"{uuid.uuid4()}.png"
    hti.screenshot(
        html_str=html_content, save_as=output_path, size=(800, estimated_height)
    )
    image = Image.open(output_path)
    image = trim(image)
    image.save(output_path)
    return output_path
