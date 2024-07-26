import discord
from discord import app_commands, Intents, Interaction, Client, InteractionType, Member
from typing import Optional
from html2image import Html2Image
from io import BytesIO
from PIL import Image, ImageChops
import uuid
import requests
import base64
import html
import os
import difflib


from lib.commands import (
    updateRoleAssignments,
    colourPalette,
    gridify,
    persistantRoleButtons,
    handleRoleButtonInteraction,
    screenshotCanvas,
    add_iceberg_text,
    show_iceberg
)

MINISTER_ROLE_ID = 1250190944502943755
CABINET_ROLE_ID = 959493505930121226
LOG_CHANNEL_ID = 959723562892144690

hti = Html2Image(output_path='.')

class AClient(Client):
    def __init__(self):
        intents = Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True
        intents.reactions = True
        intents.typing = True
        intents.voice_states = True
        intents.webhooks = True

        super().__init__(intents=intents)
        self.synced = False

    async def on_ready(self):
        global tree
        if not self.synced:
            await tree.sync()
            self.synced = True
        print(f"Logged in as {self.user}")
        for command in tree.get_commands():
            print(command.name)

    async def on_interaction(self, interaction: Interaction):
        if (
            interaction.type == InteractionType.component
            and "custom_id" in interaction.data
        ):
            custom_id = interaction.data["custom_id"]
            if custom_id.startswith("role_"):
                await handleRoleButtonInteraction(interaction)

    async def on_message_delete(self, message):
        if message.author.bot:
            return

        async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=1):
            if entry.target.id == message.author.id and entry.extra.channel.id == message.channel.id:
                deleter = entry.user
                break
        else:
            deleter = None

        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            image_file_path = await create_message_image(message, "Deleted Message")

            channel_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"
            description = f"Message deleted in {message.channel.mention} by {message.author.mention} ({message.author.id})."
            if deleter and deleter != message.author:
                description += f" Deleted by {deleter.mention} ({deleter.id})."
            
            embed = discord.Embed(
                title="Message Deleted",
                description=description,
                color=discord.Color.red()
            )
            embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
            embed.set_image(url="attachment://deleted_message.png")
            if image_file_path is not None:
                with open(image_file_path, "rb") as f:
                    await log_channel.send(file=discord.File(f, "deleted_message.png"), embed=embed)
                os.remove(image_file_path)
        else:
            print(f"Error: Channel with ID {LOG_CHANNEL_ID} not found.")

    async def on_message_edit(self, before, after):
        if before.author.bot:
            return

        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if log_channel is not None:
            image_file_path = await create_edited_message_image(before, after)

            message_link = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{after.id}"
            embed = discord.Embed(
                title="Message Edited",
                description=f"Message edited in {before.channel.mention} by {before.author.mention} ({before.author.id}).",
                color=discord.Color.orange()
            )
            embed.add_field(name="Message Link", value=f"[Click here]({message_link})")
            embed.set_image(url="attachment://edited_message.png")
            if image_file_path is not None:
                with open(image_file_path, "rb") as f:
                    await log_channel.send(file=discord.File(f, "edited_message.png"), embed=embed)
                os.remove(image_file_path)
        else:
            print(f"Error: Channel with ID {LOG_CHANNEL_ID} not found.")

client = AClient()
tree = app_commands.CommandTree(client)

def has_role(interaction: Interaction, role_id: int) -> bool:
    return any(role.id == role_id for role in interaction.user.roles)

def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in interaction.user.roles)

@tree.command(
    name="role-manage",
    description="Manages user roles by assigning a specified role to members who don't have it",
)
async def role_management(interaction: Interaction, role_name: str):
    await updateRoleAssignments(interaction, role_name)

@tree.command(
    name="colour-palette", description="Generates a colour palette from an image"
)
async def colour_palette(interaction: Interaction, attachment_url: str):
    await colourPalette(interaction, attachment_url)

@tree.command(name="gridify", description="Adds a pixel art grid overlay to an image")
async def gridify_command(interaction: Interaction, attachment_url: str):
    await gridify(interaction, attachment_url)

@tree.command(name="role-react", description="Adds a reaction role to a message")
async def role_react_command(interaction: Interaction):
    if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    await persistantRoleButtons(interaction)

@tree.command(name="screenshot-canvas", description="Takes a screenshot of the current canvas")
async def screenshot_canvas(interaction: Interaction, x: Optional[int] = -770, y: Optional[int] = 7930):
    await screenshotCanvas(interaction, x, y)

@tree.command(name="user-activity", description="Gets user activity stats to find their most active hour")
async def user_activity_command(interaction: Interaction, month: str, user: Member, channel_name: str):
    await userActivity(interaction, month, user, channel_name)

@tree.command(name="add-to-iceberg", description="Adds text to the iceberg image")
async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
    if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    await add_iceberg_text(interaction, text, level)

@tree.command(name="show-iceberg", description="Shows the iceberg image")
async def show_iceberg_command(interaction: Interaction):
    await show_iceberg(interaction)


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
        with open(file_path, 'r') as file:
            return file.read()
    except Exception as e:
        print(f"Error reading HTML template {file_path}: {e}")
        return ""

async def create_message_image(message, title):
    response = requests.get(message.author.avatar.url)
    avatar_base64 = base64.b64encode(response.content).decode('utf-8')
    avatar_data_url = f"data:image/png;base64,{avatar_base64}"
    escaped_content = html.escape(message.content)
    message_lines = escaped_content.split('\n')
    line_height = 20
    content_height = line_height * (len(message_lines) + 1)
    estimated_height = max(100, content_height + 100)
    border_color = message.author.color.to_rgb()
    display_name = message.author.display_name
    created_at = message.created_at.strftime('%H:%M')
    html_content = read_html_template('templates/deleted_message.html').format(
        title=title,
        border_color=border_color,
        avatar_data_url=avatar_data_url,
        display_name=display_name,
        created_at=created_at,
        content=escaped_content
    )
    output_path = f"{uuid.uuid4()}.png"
    hti.screenshot(html_str=html_content, save_as=output_path, size=(800, estimated_height))
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
        if tag == 'replace':
            highlighted_before.append(f'<span style="background-color: red;">{html.escape(before[i1:i2])}</span>')
            highlighted_after.append(f'<span style="background-color: green;">{html.escape(after[j1:j2])}</span>')
            changes_detected = True
        elif tag == 'delete':
            highlighted_before.append(f'<span style="background-color: red;">{html.escape(before[i1:i2])}</span>')
            changes_detected = True
        elif tag == 'insert':
            highlighted_after.append(f'<span style="background-color: green;">{html.escape(after[j1:j2])}</span>')
            changes_detected = True
        elif tag == 'equal':
            highlighted_before.append(html.escape(before[i1:i2]))
            highlighted_after.append(html.escape(after[j1:j2]))
    return ''.join(highlighted_before), ''.join(highlighted_after), changes_detected

async def create_edited_message_image(before, after):
    response = requests.get(before.author.avatar.url)
    avatar_base64 = base64.b64encode(response.content).decode('utf-8')
    avatar_data_url = f"data:image/png;base64,{avatar_base64}"
    escaped_before_content = html.escape(before.content)
    escaped_after_content = html.escape(after.content)
    highlighted_before_content, highlighted_after_content, changes_detected = highlight_diff(before.content, after.content)
    if not changes_detected:
        return None
    before_lines = highlighted_before_content.split('\n')
    after_lines = highlighted_after_content.split('\n')
    line_height = 20
    before_content_height = line_height * (len(before_lines) + 1)
    after_content_height = line_height * (len(after_lines) + 1)
    content_height = before_content_height + after_content_height + 60
    estimated_height = max(150, content_height + 100)
    border_color = before.author.color.to_rgb()
    display_name = before.author.display_name
    before_created_at = before.created_at.strftime('%H:%M')
    after_created_at = after.created_at.strftime('%H:%M')
    html_content = read_html_template('templates/edited_message.html').format(
        border_color=border_color,
        avatar_data_url=avatar_data_url,
        display_name=display_name,
        before_created_at=before_created_at,
        before_content=highlighted_before_content,
        after_created_at=after_created_at,
        after_content=highlighted_after_content
    )
    output_path = f"{uuid.uuid4()}.png"
    hti.screenshot(html_str=html_content, save_as=output_path, size=(800, estimated_height))
    image = Image.open(output_path)
    image = trim(image)
    image.save(output_path)
    return output_path