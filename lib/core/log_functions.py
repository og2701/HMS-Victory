import base64
import difflib
import html
import re
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import aiohttp
import discord

from lib.core.image_processing import screenshot_html, get_avatar_data_uri
from lib.core.file_operations import read_html_template

async def replace_custom_emojis(client, text: str) -> str:
    """Finds Discord custom emojis in the escaped text and replaces them with inline data URI <img> tags."""
    pattern = re.compile(r'&lt;(a?):([a-zA-Z0-9_]+):([0-9]+)&gt;')
    matches = pattern.findall(text)
    if not matches:
        return text
        
    for is_animated, name, emoji_id in set(matches):
        ext = "gif" if is_animated == "a" else "png"
        url = f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}"
        
        data_uri = await get_avatar_data_uri(client, url)
        
        img_tag = f'<img src="{data_uri}" alt=":{name}:" class="discord-emoji" style="width: 1.4em; height: 1.4em; vertical-align: middle; display: inline-block;" />'
        
        original_str = f"&lt;{is_animated}:{name}:{emoji_id}&gt;"
        text = text.replace(original_str, img_tag)
        
    return text



def render_mentions(message, text: str) -> str:
    """Replace escaped Discord mention markup in already-html.escape()d text with
    styled mention pills showing display names, like Discord renders them.

    Operates on the post-escape text (same as replace_custom_emojis), so the forms are:
      user     <@id> / <@!id>  ->  &lt;@id&gt; / &lt;@!id&gt;
      role     <@&id>          ->  &lt;@&amp;id&gt;   (the & becomes &amp;)
      channel  <#id>           ->  &lt;#id&gt;
    @everyone/@here is styled first so a resolved nickname like "everyone" can't be
    re-wrapped. Every inserted display name is html.escaped (names are unescaped API
    data and could otherwise inject markup into the screenshot template).
    """
    guild = message.guild

    # 1. @everyone / @here - only when the message actually pinged everyone.
    if getattr(message, "mention_everyone", False):
        text = re.sub(
            r"@(everyone|here)",
            lambda m: f'<span class="mention">@{m.group(1)}</span>',
            text,
        )

    # 2. Role mentions - escaped &lt;@&amp;id&gt;; tint the pill with the role colour.
    role_map = {r.id: r for r in message.role_mentions}

    def _role_sub(m):
        rid = int(m.group(1))
        role = role_map.get(rid) or (guild.get_role(rid) if guild else None)
        if role is None:
            return m.group(0)
        name = html.escape(role.name)
        color = getattr(role, "color", None)
        if color is not None and color.value:
            r, g, b = color.to_rgb()
            return (
                f'<span class="mention" style="color: rgb({r},{g},{b}); '
                f'background-color: rgba({r},{g},{b},0.15);">@{name}</span>'
            )
        return f'<span class="mention">@{name}</span>'

    text = re.sub(r"&lt;@&amp;([0-9]+)&gt;", _role_sub, text)

    # 3. User mentions - escaped &lt;@id&gt; and &lt;@!id&gt;.
    user_map = {u.id: u.display_name for u in message.mentions}

    def _user_sub(m):
        uid = int(m.group(1))
        name = user_map.get(uid)
        if name is None and guild is not None:
            member = guild.get_member(uid)
            name = member.display_name if member else None
        if name is None:
            return m.group(0)
        return f'<span class="mention">@{html.escape(name)}</span>'

    text = re.sub(r"&lt;@!?([0-9]+)&gt;", _user_sub, text)

    # 4. Channel mentions - escaped &lt;#id&gt;.
    chan_map = {c.id: c for c in message.channel_mentions}

    def _chan_sub(m):
        cid = int(m.group(1))
        chan = chan_map.get(cid) or (guild.get_channel(cid) if guild else None)
        if chan is None:
            return m.group(0)
        return f'<span class="mention">#{html.escape(chan.name)}</span>'

    text = re.sub(r"&lt;#([0-9]+)&gt;", _chan_sub, text)

    return text


def get_video_poster_url(message, width: int = 640):
    """Return a still poster-image URL for a video in `message`, or None.

    Covers link-embedded videos (TikTok/YouTube live in message.embeds - use the
    embed thumbnail) and uploaded video attachments (no poster field, so ask
    Discord's media proxy to freeze the first frame via format=jpeg). The URL is
    signed and short-lived, so download it promptly - create_quote_image inlines it
    as base64 right away, so expiry doesn't matter afterwards.
    """
    for embed in message.embeds:
        # EmbedProxy returns None for missing attributes, so this never raises.
        if embed.type in ("video", "gifv") or embed.video.url:
            poster = embed.thumbnail.proxy_url or embed.thumbnail.url
            if poster:
                return poster

    for att in message.attachments:
        ct = (att.content_type or "").lower()
        name = (att.filename or "").lower()
        is_video = ct.startswith("video/") or name.endswith((".mp4", ".mov", ".webm"))
        if not is_video or not att.proxy_url:
            continue
        parts = urlparse(att.proxy_url)
        params = parse_qs(parts.query)   # preserve the ex/is/hm signing params
        params["format"] = ["jpeg"]       # media proxy freezes the first frame
        params["width"] = [str(width)]
        return urlunparse(parts._replace(query=urlencode(params, doseq=True)))

    return None


def calculate_estimated_height(content, line_height=20, base_height=250):
    message_lines = content.split("\n")
    total_lines = sum(len(line) // 80 + 1 for line in message_lines)
    content_height = line_height * total_lines
    estimated_height = max(base_height, content_height + 100)
    return estimated_height


async def create_message_image(client, message, title):
    avatar_url = (
        message.author.display_avatar.url
        if message.author.display_avatar
        else message.author.default_avatar.url
    )
    avatar_data_url = await get_avatar_data_uri(client, avatar_url)

    escaped_content = html.escape(message.content)
    escaped_content = await replace_custom_emojis(client, escaped_content)

    attached_image_html = ""
    extra_height = 0
    if message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image/"):
            try:
                async with client.session.get(att.url) as resp:
                    if resp.status == 200:
                        att_content = await resp.read()
                        att_base64 = base64.b64encode(att_content).decode("utf-8")
                        content_type = att.content_type
                        img_data_url = f"data:{content_type};base64,{att_base64}"
                        attached_image_html = f'<img src="{img_data_url}" class="attached-image" />'
                        extra_height = 400
            except Exception as e:
                print(f"Error downloading attachment for deleted message log: {e}")

    estimated_height = calculate_estimated_height(escaped_content) + extra_height + 50

    border_color = message.author.color.to_rgb()
    display_name = message.author.display_name
    created_at = message.created_at.strftime("%H:%M")

    css_styles = f"""
        body {{
            margin: 0;
            padding: 0;
            background-color: white;
        }}
        .container {{
            border: 2px solid rgb{border_color};
            padding: 10px;
            width: fit-content;
            display: inline-block;
        }}
        .title {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        .message {{
            display: flex;
            align-items: flex-start;
            padding: 10px;
            background-color: #36393f;
            border-radius: 5px;
            color: white;
            font-family: Arial, sans-serif;
        }}
        .avatar {{
            width: 50px;
            height: 50px;
            border-radius: 50%;
            margin-right: 10px;
        }}
        .username {{
            color: rgb{border_color};
            font-weight: bold;
            margin-right: 5px;
        }}
        .timestamp {{
            color: #72767d;
            font-size: 12px;
        }}
        .content {{
            margin-top: 5px;
            white-space: pre-wrap;
        }}
        .attached-image {{
            max-width: 100%;
            border-radius: 4px;
            margin-top: 8px;
        }}
    """

    html_content = read_html_template("templates/deleted_message.html")
    html_content = html_content.replace("{title}", title)
    html_content = html_content.replace("{css_styles}", css_styles)
    html_content = html_content.replace("{\n            css_styles\n        }", css_styles)
    html_content = html_content.replace("{\n    css_styles\n}", css_styles)
    html_content = html_content.replace("{avatar_data_url}", avatar_data_url)
    html_content = html_content.replace("{display_name}", display_name)
    html_content = html_content.replace("{created_at}", created_at)
    html_content = html_content.replace("{content}", escaped_content)
    html_content = html_content.replace("{attached_image_html}", attached_image_html)

    return await screenshot_html(html_content, size=(800, estimated_height), element_selector=".container")


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


async def create_edited_message_image(client, before, after):
    avatar_url = (
        before.author.display_avatar.url
        if before.author.display_avatar
        else before.author.default_avatar.url
    )
    avatar_data_url = await get_avatar_data_uri(client, avatar_url)

    escaped_before_content = html.escape(before.content)
    escaped_after_content = html.escape(after.content)
    highlighted_before_content, highlighted_after_content, changes_detected = (
        highlight_diff(before.content, after.content)
    )
    
    highlighted_before_content = await replace_custom_emojis(client, highlighted_before_content)
    highlighted_after_content = await replace_custom_emojis(client, highlighted_after_content)
    
    if not changes_detected:
        return None

    async def get_attached_image_html(message, error_context):
        if message.attachments:
            att = message.attachments[0]
            if att.content_type and att.content_type.startswith("image/"):
                try:
                    async with client.session.get(att.url) as resp:
                        if resp.status == 200:
                            att_content = await resp.read()
                            att_base64 = base64.b64encode(att_content).decode("utf-8")
                            content_type = att.content_type
                            img_data_url = f"data:{content_type};base64,{att_base64}"
                            return f'<img src="{img_data_url}" class="attached-image" />', 400
                except Exception as e:
                    print(f"Error downloading attachment for {error_context}: {e}")
        return "", 0

    before_attached_image_html, before_extra_height = await get_attached_image_html(before, "edited message (before)")
    after_attached_image_html, after_extra_height = await get_attached_image_html(after, "edited message (after)")

    before_height = calculate_estimated_height(highlighted_before_content) + before_extra_height
    after_height = calculate_estimated_height(highlighted_after_content) + after_extra_height
    content_height = before_height + after_height + 60
    estimated_height = max(150, content_height + 100)

    border_color = before.author.color.to_rgb()
    display_name = before.author.display_name
    before_created_at = before.created_at.strftime("%H:%M")
    after_created_at = after.created_at.strftime("%H:%M")

    css_styles = f"""
        body {{
            margin: 0;
            padding: 0;
            background-color: white;
        }}
        .container {{
            border: 2px solid rgb{border_color};
            padding: 10px;
            width: fit-content;
            display: inline-block;
        }}
        .title {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
        }}
        .message {{
            display: flex;
            align-items: flex-start;
            padding: 10px;
            background-color: #36393f;
            border-radius: 5px;
            color: white;
            font-family: Arial, sans-serif;
            margin-bottom: 10px;
        }}
        .avatar {{
            width: 50px;
            height: 50px;
            border-radius: 50%;
            margin-right: 10px;
        }}
        .username {{
            color: rgb{border_color};
            font-weight: bold;
            margin-right: 5px;
        }}
        .timestamp {{
            color: #72767d;
            font-size: 12px;
        }}
        .content {{
            margin-top: 5px;
            white-space: pre-wrap;
        }}
        .label {{
            font-size: 14px;
            margin-bottom: 5px;
        }}
        .attached-image {{
            max-width: 100%;
            border-radius: 4px;
            margin-top: 8px;
        }}
    """

    html_content = read_html_template("templates/edited_message.html")
    html_content = html_content.replace("{css_styles}", css_styles)
    html_content = html_content.replace("{\n            css_styles\n        }", css_styles)
    html_content = html_content.replace("{\n    css_styles\n}", css_styles)
    html_content = html_content.replace("{avatar_data_url}", avatar_data_url)
    html_content = html_content.replace("{display_name}", display_name)
    html_content = html_content.replace("{before_created_at}", before_created_at)
    html_content = html_content.replace("{before_content}", highlighted_before_content)
    html_content = html_content.replace("{before_attached_image_html}", before_attached_image_html)
    html_content = html_content.replace("{after_created_at}", after_created_at)
    html_content = html_content.replace("{after_content}", highlighted_after_content)
    html_content = html_content.replace("{after_attached_image_html}", after_attached_image_html)

    return await screenshot_html(html_content, size=(800, estimated_height), element_selector=".container")

async def create_quote_image(client, message):
    avatar_url = (
        message.author.display_avatar.url
        if message.author.display_avatar
        else message.author.default_avatar.url
    )
    avatar_data_url = await get_avatar_data_uri(client, avatar_url)

    escaped_content = html.escape(message.content)
    escaped_content = await replace_custom_emojis(client, escaped_content)
    escaped_content = render_mentions(message, escaped_content)

    attached_image_html = ""
    extra_height = 0
    if message.attachments:
        att = message.attachments[0]
        if att.content_type and att.content_type.startswith("image/"):
            try:
                async with client.session.get(att.url) as resp:
                    if resp.status == 200:
                        att_content = await resp.read()
                        att_base64 = base64.b64encode(att_content).decode("utf-8")
                        content_type = att.content_type
                        img_data_url = f"data:{content_type};base64,{att_base64}"
                        attached_image_html = f'<img src="{img_data_url}" class="attached-image" />'
                        extra_height = 400
            except Exception as e:
                print(f"Error downloading attachment for quote: {e}")

    # No static image - if the message has a video (uploaded file or a link-embedded
    # TikTok/YouTube), bake its poster frame into the card with a play-button overlay
    # so the card isn't just text. The playable copy still rides along in the HOF post.
    if not attached_image_html:
        poster_url = get_video_poster_url(message)
        if poster_url:
            try:
                async with client.session.get(poster_url) as resp:
                    if resp.status == 200:
                        poster_bytes = await resp.read()
                        poster_b64 = base64.b64encode(poster_bytes).decode("utf-8")
                        poster_mime = resp.content_type or "image/jpeg"
                        poster_data_url = f"data:{poster_mime};base64,{poster_b64}"
                        attached_image_html = (
                            '<div class="video-preview">'
                            f'<img src="{poster_data_url}" class="attached-image" />'
                            '<div class="play-button"></div>'
                            '</div>'
                        )
                        extra_height = 400
            except Exception as e:
                print(f"Error downloading video poster for quote: {e}")

    reply_html = ""
    replied = None
    if message.reference and message.reference.message_id:
        resolved = message.reference.resolved
        if isinstance(resolved, discord.Message):
            replied = resolved
        else:
            try:
                replied = await message.channel.fetch_message(message.reference.message_id)
            except Exception:
                replied = None
    if isinstance(replied, discord.Message):
        reply_avatar_url = (
            replied.author.display_avatar.url
            if replied.author.display_avatar
            else replied.author.default_avatar.url
        )
        reply_avatar_data = await get_avatar_data_uri(client, reply_avatar_url)
        reply_author_color = replied.author.color.to_rgb() if hasattr(replied.author, "color") else (114, 118, 125)
        reply_text = replied.content or ("[attachment]" if replied.attachments else "[embed]")
        reply_text = html.escape(reply_text).replace("\n", " ")
        if len(reply_text) > 120:
            reply_text = reply_text[:117] + "&hellip;"
        reply_text = await replace_custom_emojis(client, reply_text)
        reply_text = render_mentions(replied, reply_text)
        reply_html = (
            f'<div class="reply-preview">'
            f'<span class="reply-spine"></span>'
            f'<img src="{reply_avatar_data}" class="reply-avatar" />'
            f'<span class="reply-username" style="color: rgb{reply_author_color};">{html.escape(replied.author.display_name)}</span>'
            f'<span class="reply-content">{reply_text}</span>'
            f'</div>'
        )
        extra_height += 30

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
        .mention {{
            background-color: rgba(88, 101, 242, 0.3);
            color: #dee0fd;
            font-weight: 500;
            border-radius: 3px;
            padding: 0 2px;
            white-space: nowrap;
        }}
        .video-preview {{
            position: relative;
            display: inline-block;
            margin-top: 8px;
        }}
        .video-preview .attached-image {{
            margin-top: 0;
            display: block;
        }}
        .play-button {{
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: rgba(0, 0, 0, 0.55);
        }}
        .play-button::after {{
            content: "";
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-40%, -50%);
            border-style: solid;
            border-width: 11px 0 11px 19px;
            border-color: transparent transparent transparent #ffffff;
        }}
        .reply-preview {{
            display: flex;
            align-items: center;
            font-size: 13px;
            color: #b9bbbe;
            margin-bottom: 6px;
            padding-left: 8px;
            border-left: 2px solid #4f545c;
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
        }}
        .reply-avatar {{
            width: 16px;
            height: 16px;
            border-radius: 50%;
            margin-right: 6px;
        }}
        .reply-username {{
            font-weight: bold;
            margin-right: 6px;
        }}
        .reply-content {{
            color: #b9bbbe;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
    """

    html_content = read_html_template("templates/quote_message.html")
    html_content = html_content.replace("{css_styles}", css_styles)
    html_content = html_content.replace("{\n            css_styles\n        }", css_styles)
    html_content = html_content.replace("{avatar_data_url}", avatar_data_url)
    html_content = html_content.replace("{display_name}", display_name)
    html_content = html_content.replace("{created_at}", created_at)
    html_content = html_content.replace("{content}", escaped_content)
    html_content = html_content.replace("{attached_image_html}", attached_image_html)
    html_content = html_content.replace("{reply_html}", reply_html)

    return await screenshot_html(html_content, size=(650, estimated_height), element_selector=".container")
