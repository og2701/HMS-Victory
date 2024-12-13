import requests
import base64
import html
import uuid
from PIL import Image, ImageChops
from html2image import Html2Image

hti = Html2Image(output_path=".", browser_executable="/usr/bin/google-chrome")


def trim(im):
    bg = Image.new(im.mode, im.size, im.getpixel((0, 0)))
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


async def create_summary_image(summary_data, title, title_color):
    total_members = summary_data["total_members"]
    members_joined = summary_data["members_joined"]
    members_left = summary_data["members_left"]
    members_banned = summary_data["members_banned"]
    total_messages = summary_data["total_messages"]
    reactions_added = summary_data["reactions_added"]
    reactions_removed = summary_data["reactions_removed"]
    deleted_messages = summary_data["deleted_messages"]
    boosters_gained = summary_data["boosters_gained"]
    boosters_lost = summary_data["boosters_lost"]
    top_channels = summary_data["top_channels"]
    active_members = summary_data["active_members"]
    reacting_members = summary_data["reacting_members"]

    top_channels_str = "\n".join(
        [
            f"<li>{channel_name}: {count} messages</li>"
            for channel_name, count in top_channels
        ]
    )
    active_members_str = "\n".join(
        [
            f"<li>{member_name}: {count} messages</li>"
            for member_name, count in active_members
        ]
    )
    reacting_members_str = "\n".join(
        [
            f"<li>{member_name}: {count} reactions</li>"
            for member_name, count in reacting_members
        ]
    )

    html_content = read_html_template("templates/summary.html").format(
        title=title,
        title_color=title_color,
        total_members=total_members,
        members_joined=members_joined,
        members_left=f"{members_left} ({members_banned} banned)",
        total_messages=total_messages,
        reactions_added=reactions_added,
        reactions_removed=reactions_removed,
        deleted_messages=deleted_messages,
        boosters=f"{boosters_gained} / {boosters_lost}",
        top_channels=top_channels_str,
        active_members=active_members_str,
        reacting_members=reacting_members_str,
    )

    estimated_height = calculate_estimated_height(html_content, base_height=400)

    output_path = f"{uuid.uuid4()}.png"
    hti.screenshot(
        html_str=html_content, save_as=output_path, size=(800, estimated_height)
    )
    image = Image.open(output_path)
    image = trim(image)
    image.save(output_path)
    return output_path
