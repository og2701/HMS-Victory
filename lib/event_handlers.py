import discord
from discord import Interaction, InteractionType
from summary import initialize_summary_data, update_summary_data, post_summary
from utils import restrict_channel_for_new_members
from log_functions import create_message_image, create_edited_message_image
import logging
import os
import aiohttp
import io

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_CACHE_CHANNEL = 1271188365244497971
POLITICS_CHANNEL_ID = 1141097424849481799
POLITICS_WHITELISTED_USER_IDS = []

async def on_ready(client, tree, scheduler):
    global POLITICS_WHITELISTED_USER_IDS
    if not client.synced:
        await tree.sync()
        client.synced = True
    logger.info(f"Logged in as {client.user}")
    for command in tree.get_commands():
        logger.info(f"Command loaded: {command.name}")

    scheduler.start()

async def on_message(client, message):
    if message.author.bot:
        return

    if not await restrict_channel_for_new_members(message, POLITICS_CHANNEL_ID, 7, POLITICS_WHITELISTED_USER_IDS):
        return

    initialize_summary_data()
    update_summary_data("messages", channel_id=message.channel.id)
    update_summary_data("active_members", user_id=message.author.id)

    if message.attachments:
        cache_channel = client.get_channel(IMAGE_CACHE_CHANNEL)
        if cache_channel:
            async with aiohttp.ClientSession() as session:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        if attachment.size <= MAX_IMAGE_SIZE:
                            async with session.get(attachment.url) as response:
                                if response.status == 200:
                                    image_data = await response.read()
                                    image_filename = attachment.filename
                                    file = discord.File(io.BytesIO(image_data), filename=image_filename)
                                    embed = discord.Embed(
                                        title="Image Cached",
                                        description=f"Image by {message.author.mention} in {message.channel.mention}",
                                        color=discord.Color.blue()
                                    )
                                    embed.add_field(name="Message Link", value=f"[Click here](https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id})")
                                    embed.set_image(url=f"attachment://{image_filename}") 
                                    cached_message = await cache_channel.send(embed=embed, file=file)
                                    if cached_message.embeds[0].image.url:
                                        if message.id not in client.image_cache:
                                            client.image_cache[message.id] = {}
                                        client.image_cache[message.id][attachment.url] = cached_message.embeds[0].image.url
                        else:
                            logger.info(f"Skipped downloading {attachment.filename} as it exceeds the size limit of {MAX_IMAGE_SIZE / (1024 * 1024)} MB.")

async def on_interaction(interaction: Interaction):
    if (
        interaction.type == InteractionType.component
        and "custom_id" in interaction.data
    ):
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            await handleRoleButtonInteraction(interaction)

async def on_member_join(member):
    initialize_summary_data()
    update_summary_data("members_joined")

async def on_member_remove(member):
    initialize_summary_data()
    update_summary_data("members_left")

async def on_member_ban(guild, user):
    initialize_summary_data()
    update_summary_data("members_banned")

async def on_message_delete(client, message):
    if message.author.bot:
        return

    async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=1):
        if entry.target.id == message.author.id and entry.extra.channel.id == message.channel.id:
            deleter = entry.user
            break
    else:
        deleter = None

    log_channel = client.get_channel(959723562892144690)
    channel_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"
    if log_channel is not None:
        if message.content:
            image_file_path = await create_message_image(message, "Deleted Message")

            description = f"Message by {message.author.mention} ({message.author.id}) deleted in {message.channel.mention}."
            if deleter and deleter != message.author:
                description += f"\nDeleted by {deleter.mention} ({deleter.id})."
            
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

        for attachment in message.attachments:
            attachment_link = client.image_cache.get(message.id, {}).get(attachment.url)
            if attachment_link:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    image_embed = discord.Embed(
                        title="Image Deleted",
                        description=f"An image by {message.author.mention} ({message.author.id}) was deleted in {message.channel.mention}.",
                        color=discord.Color.red()
                    )
                    image_embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
                    image_embed.add_field(name="Image Link", value=f"{attachment_link}")
                    image_embed.set_image(url=attachment_link)
                    await log_channel.send(embed=image_embed)
                else:
                    attachment_embed = discord.Embed(
                        title="Attachments Deleted",
                        description=f"The following attachments by {message.author.mention} ({message.author.id}) were deleted in {message.channel.mention}:\n{attachment.filename}",
                        color=discord.Color.red()
                    )
                    attachment_embed.add_field(name="Channel Link", value=f"[Click here]({attachment_link})")
                    await log_channel.send(embed=attachment_embed)

async def on_message_edit(client, before, after):
    if before.author.bot:
        return

    log_channel = client.get_channel(959723562892144690)
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

async def on_reaction_add(reaction, user):
    if user.bot:
        return
    initialize_summary_data()
    update_summary_data("reactions_added")
    update_summary_data("reacting_members", user_id=user.id)

async def on_reaction_remove(reaction, user):
    if user.bot:
        return
    initialize_summary_data()
    update_summary_data("reactions_removed")
    update_summary_data("reacting_members", user_id=user.id, remove=True)
