import json
import os
import discord
import aiohttp
import io
import logging
import asyncio
from datetime import datetime, timezone
from discord import Interaction

logger = logging.getLogger(__name__)

PERSISTENT_VIEWS_FILE = "persistent_views.json"
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB size limit for images

def load_json(filename):
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def load_persistent_views():
    return load_json(PERSISTENT_VIEWS_FILE)

def save_persistent_views(data):
    save_json(PERSISTENT_VIEWS_FILE, data)

def load_whitelist():
    return load_json("whitelist.json")

def save_whitelist(whitelist):
    save_json("whitelist.json", whitelist)

def has_role(interaction: Interaction, role_id: int) -> bool:
    return any(role.id == role_id for role in interaction.user.roles)

def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in interaction.user.roles)

async def restrict_channel_for_new_members(
    message: discord.Message,
    channel_id: int,
    days_required: int = 7,
    whitelisted_user_ids: list[int] = [],
):
    if message.channel.id == channel_id:
        if message.author.id in whitelisted_user_ids:
            return True
        join_date = message.author.joined_at
        if (
            join_date is None
            or (datetime.now(timezone.utc) - join_date).days < days_required
        ):
            await message.delete()
            await message.channel.send(
                f"{message.author.mention}, you need to be in the server for at least {days_required} days to use this channel. If you believe you should be whitelisted, please <#1143560594138595439>",
                delete_after=10,
            )
            return False
    return True

async def download_and_cache_image(message, attachment, cache_channel, client):
    if not attachment.content_type or not attachment.content_type.startswith("image/"):
        return

    if attachment.size > MAX_IMAGE_SIZE:
        logger.info(
            f"Skipped downloading {attachment.filename} as it exceeds the size limit of {MAX_IMAGE_SIZE / (1024 * 1024)} MB."
        )
        return

    async with aiohttp.ClientSession() as session:
        async with session.get(attachment.url) as response:
            if response.status == 200:
                image_data = await response.read()
                image_filename = attachment.filename
                file = discord.File(io.BytesIO(image_data), filename=image_filename)
                embed = discord.Embed(
                    title="Image Cached",
                    description=f"Image by {message.author.mention} in {message.channel.mention}",
                    color=discord.Color.blue(),
                )
                embed.add_field(
                    name="Message Link",
                    value=f"[Click here](https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id})",
                )
                embed.set_image(url=f"attachment://{image_filename}")
                cached_message = await cache_channel.send(embed=embed, file=file)
                if cached_message.embeds[0].image.url:
                    if message.id not in client.image_cache:
                        client.image_cache[message.id] = {}
                    client.image_cache[message.id][attachment.url] = cached_message.embeds[0].image.url

async def process_message_link_reply(client, message, link):
    try:
        link_parts = link.split("/")
        guild_id = int(link_parts[4])
        channel_id = int(link_parts[5])
        message_id = int(link_parts[6])

        guild = client.get_guild(guild_id)
        channel = guild.get_channel(channel_id)
        quoted_message = await channel.fetch_message(message_id)

        timestamp_unix = int(quoted_message.created_at.timestamp())
        timestamp_formatted = f"<t:{timestamp_unix}:f>"
        channel_name = channel.name
        reply_content = f"@__{quoted_message.author}__ in *{channel_name}* {timestamp_formatted}:\n"

        filtered_content = quoted_message.content.replace("@everyone", "[everyone]").replace("@here", "[here]")
        if filtered_content:
            reply_content += f"> {filtered_content}"

        if quoted_message.attachments:
            attachment = quoted_message.attachments[0]
            if attachment.content_type and attachment.content_type.startswith("image/") and attachment.size <= MAX_IMAGE_SIZE:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as response:
                        if response.status == 200:
                            image_data = await response.read()
                            image_file = discord.File(io.BytesIO(image_data), filename=attachment.filename)
                            reply = await message.channel.send(content=reply_content, file=image_file)
            elif attachment.size > MAX_IMAGE_SIZE:
                reply = await message.channel.send(
                    f"{reply_content}\nAttachment is too large to display (max {MAX_IMAGE_SIZE / (1024 * 1024)} MB)."
                )
            else:
                reply = await message.channel.send(f"{reply_content}\n[Attachment: {attachment.url}]")
        elif quoted_message.embeds:
            embed = quoted_message.embeds[0]
            embed_copy = discord.Embed.from_dict(embed.to_dict())
            reply = await message.channel.send(content=reply_content, embed=embed_copy)
        else:
            reply = await message.channel.send(reply_content)

        await reply.add_reaction("❌")

        def check(reaction, user):
            return user == message.author and str(reaction.emoji) == "❌" and reaction.message.id == reply.id

        try:
            await client.wait_for("reaction_add", timeout=20.0, check=check)
            await reply.delete()
        except asyncio.TimeoutError:
            await reply.clear_reactions()
    except Exception as e:
        logger.error(f"Error processing message link: {e}")

async def add_user_to_forum_thread(client, message, forum_channel, thread):
    user_id = str(message.author.id)
    thread_id = str(thread.id)

    if thread_id in client.added_users and user_id in client.added_users[thread_id]:
        return

    try:
        if thread_id in client.thread_messages:
            msg_id = client.thread_messages[thread_id]
            try:
                existing_msg = await thread.fetch_message(msg_id)
            except discord.NotFound:
                existing_msg = None
        else:
            existing_msg = None

        if existing_msg is None:
            new_msg = await thread.send(".")
            client.thread_messages[thread_id] = new_msg.id
            save_json("thread_messages.json", client.thread_messages)
            existing_msg = new_msg

        await existing_msg.edit(content=f"{message.author.mention}")
        logger.info(f"Silently added {message.author} to {thread.name}")

        if thread_id not in client.added_users:
            client.added_users[thread_id] = []
        client.added_users[thread_id].append(user_id)
        save_json("added_users.json", client.added_users)

        await asyncio.sleep(1)
        await existing_msg.edit(content=".")

    except discord.HTTPException as e:
        logger.warning(f"Failed to add {message.author} to {thread.name}: {e}")
