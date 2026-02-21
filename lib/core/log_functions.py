import base64
import difflib
import html
import io
from PIL import Image
import requests

from lib.core.image_processing import screenshot_html, trim_image, encode_image_to_data_uri
from lib.core.file_operations import read_html_template


def calculate_estimated_height(content, line_height=20, base_height=1000):
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

    buffer = screenshot_html(html_content, size=(800, estimated_height), apply_trim=False)
    with Image.open(buffer) as img:
        trimmed = trim_image(img)
        output = io.BytesIO()
        trimmed.save(output, format="PNG")
        output.seek(0)
    return output


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

    buffer = screenshot_html(html_content, size=(800, estimated_height), apply_trim=False)
    with Image.open(buffer) as img:
        trimmed = trim_image(img)
        output = io.BytesIO()
        trimmed.save(output, format="PNG")
        output.seek(0)
    return output

async def create_quote_image(message):
    avatar_url = (
        message.author.avatar.url
        if message.author.avatar
        else message.author.default_avatar.url
    )
    response = requests.get(avatar_url)
    avatar_base64 = base64.b64encode(response.content).decode("utf-8")
    avatar_data_url = f"data:image/png;base64,{avatar_base64}"

    escaped_content = html.escape(message.content)

    attached_image_html = ""
    extra_height = 0
    if message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image/"):
            try:
                resp = requests.get(att.url)
                if resp.status_code == 200:
                    att_base64 = base64.b64encode(resp.content).decode("utf-8")
                    content_type = att.content_type
                    img_data_url = f"data:{content_type};base64,{att_base64}"
                    attached_image_html = f'<img src="{img_data_url}" class="attached-image" />'
                    extra_height = 400
            except Exception as e:
                print(f"Error downloading attachment for quote: {e}")

    estimated_height = calculate_estimated_height(escaped_content) + extra_height + 50

    border_color = message.author.color.to_rgb()
    display_name = message.author.display_name
    created_at = message.created_at.strftime("%Y-%m-%d %H:%M")

    # Generate the CSS dynamically so we don't trip over Python's format() braces
    css_styles = f"""
        body {{
            margin: 0;
            padding: 0;
            background-color: white;
            font-family: Arial, sans-serif;
        }}
        .container {{
            border-left: 4px solid rgb{border_color};
            padding: 15px;
            width: fit-content;
            max-width: 600px;
            display: inline-block;
            background-color: #36393f;
            color: #dcddde;
            border-radius: 4px;
        }}
        .message-header {{
            display: flex;
            align-items: center;
            margin-bottom: 8px;
        }}
        .avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            margin-right: 12px;
        }}
        .username {{
            color: rgb{border_color};
            font-weight: bold;
            font-size: 16px;
            margin-right: 8px;
        }}
        .timestamp {{
            color: #72767d;
            font-size: 12px;
        }}
        .content {{
            font-size: 15px;
            line-height: 1.4;
            white-space: pre-wrap;
            margin-bottom: 10px;
        }}
        .attached-image {{
            max-width: 100%;
            border-radius: 4px;
            margin-top: 8px;
        }}
    """

    html_content = read_html_template("templates/quote_message.html").format(
        css_styles=css_styles,
        avatar_data_url=avatar_data_url,
        display_name=display_name,
        created_at=created_at,
        content=escaped_content,
        attached_image_html=attached_image_html
    )

    buffer = screenshot_html(html_content, size=(650, estimated_height), apply_trim=False)
    with Image.open(buffer) as img:
        trimmed = trim_image(img)
        output = io.BytesIO()
        trimmed.save(output, format="PNG")
        output.seek(0)
    return output
