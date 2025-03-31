import discord
from discord import Interaction, InteractionType
from datetime import timedelta, datetime
import logging
import os
import aiohttp
import io
import json
import asyncio
from collections import defaultdict
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger


from lib.translation import translate_and_send
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.utils import *
from lib.log_functions import create_message_image, create_edited_message_image
from lib.settings import *
from lib.shutcoin import can_use_shutcoin, remove_shutcoin, SHUTCOIN_ENABLED

from commands.mod_commands.persistant_role_buttons import (
    persistantRoleButtons,
    handleRoleButtonInteraction,
)
from commands.mod_commands.anti_raid import handle_new_member_anti_raid
from commands.mod_commands.archive_channel import (
    ArchiveButtonView,
    schedule_archive_move,
)

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024

sticker_messages = {}
recently_flagged_users = defaultdict(bool)

all_onboarding_roles = {
    ROLES.BRITISH,
    ROLES.ENGLISH,
    ROLES.SCOTTISH,
    ROLES.WELSH,
    ROLES.NORTHERN_IRISH,
    ROLES.COMMONWEALTH,
    ROLES.VISITOR,
}
nationality_onboarding_roles = {
    ROLES.ENGLISH,
    ROLES.SCOTTISH,
    ROLES.WELSH,
    ROLES.NORTHERN_IRISH,
}

FORUM_CHANNEL_ID = 1341451323249266711
THREAD_MESSAGES_FILE = "thread_messages.json"
ADDED_USERS_FILE = "added_users.json"

def reattach_persistent_views(client):
    from commands.mod_commands.announcement_command import RoleButtonView
    persistent_views = load_persistent_views()
    for key, value in persistent_views.items():
        if key.startswith("archive_") and isinstance(value, dict) and "move_timestamp" in value and "msg_id" in value:
            channel_id = int(key.split("_")[1])
            channel = client.get_channel(channel_id)
            if channel:
                client.add_view(ArchiveButtonView(client, channel_id), message_id=value["msg_id"])
                target_timestamp = value["move_timestamp"]
                asyncio.create_task(schedule_archive_move(channel, channel.guild, target_timestamp, client))
        elif isinstance(value, dict):
            view = RoleButtonView(value)
            client.add_view(view, message_id=key)

def schedule_client_jobs(client, scheduler):
    """Schedules periodic summary and cache clearing jobs"""
    scheduler.add_job(client.daily_summary, CronTrigger(hour=0, minute=0, timezone="Europe/London"))
    scheduler.add_job(client.weekly_summary, CronTrigger(day_of_week="mon", hour=0, minute=1, timezone="Europe/London"))
    scheduler.add_job(client.monthly_summary, CronTrigger(day=1, hour=0, minute=2, timezone="Europe/London"))
    scheduler.add_job(client.clear_image_cache, CronTrigger(day_of_week="sun", hour=0, minute=0, timezone="Europe/London"))
    scheduler.add_job(client.backup_bot, IntervalTrigger(minutes=30, timezone="Europe/London"))
    scheduler.start()


async def process_message_attachments(client, message):
    """Caches image attachments from a message to an image cache channel"""
    if message.attachments:
        cache_channel = client.get_channel(CHANNELS.IMAGE_CACHE)
        if cache_channel:
            async with aiohttp.ClientSession() as session:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        if attachment.size <= MAX_IMAGE_SIZE:
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
                        else:
                            logger.info(
                                f"Skipped downloading {attachment.filename} as it exceeds the size limit of {MAX_IMAGE_SIZE / (1024 * 1024)} MB."
                            )


async def process_message_links(client, message):
    """Processes message links and sends a formatted reply with message details"""
    message_links = [part for part in message.content.split() if "discord.com/channels/" in part]
    if message_links:
        for link in message_links:
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
                    if (
                        attachment.content_type
                        and attachment.content_type.startswith("image/")
                        and attachment.size <= MAX_IMAGE_SIZE
                    ):
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
                        reply = await message.channel.send(
                            f"{reply_content}\n[Attachment: {attachment.url}]"
                        )
                elif quoted_message.embeds:
                    embed = quoted_message.embeds[0]
                    embed_copy = discord.Embed.from_dict(embed.to_dict())
                    reply = await message.channel.send(content=reply_content, embed=embed_copy)
                else:
                    reply = await message.channel.send(reply_content)
                await reply.add_reaction("❌")
                def check(reaction, user):
                    return (
                        user == message.author
                        and str(reaction.emoji) == "❌"
                        and reaction.message.id == reply.id
                    )
                try:
                    await client.wait_for("reaction_add", timeout=20.0, check=check)
                    await reply.delete()
                except asyncio.TimeoutError:
                    await reply.clear_reactions()
            except Exception as e:
                logger.error(f"Error processing message link: {e}")


async def process_forum_threads(client, message):
    """Adds the message author to forum threads if necessary"""
    guild = message.guild
    if guild is None:
        return
    forum_channel = guild.get_channel(FORUM_CHANNEL_ID)
    if forum_channel and isinstance(forum_channel, discord.ForumChannel):
        user_id = str(message.author.id)
        for thread in forum_channel.threads:
            thread_id = str(thread.id)
            if thread_id in client.added_users and user_id in client.added_users[thread_id]:
                continue
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
                    save_json(THREAD_MESSAGES_FILE, client.thread_messages)
                    existing_msg = new_msg
                await existing_msg.edit(content=f"{message.author.mention}")
                logger.info(f"Silently added {message.author} to {thread.name}")
                if thread_id not in client.added_users:
                    client.added_users[thread_id] = []
                client.added_users[thread_id].append(user_id)
                save_json(ADDED_USERS_FILE, client.added_users)
                await asyncio.sleep(1)
                await existing_msg.edit(content=".")
            except discord.HTTPException as e:
                logger.warning(f"Failed to add {message.author} to {thread.name}: {e}")


async def on_ready(client, tree, scheduler):
    """Initializes the bot on startup by syncing commands, reattaching persistent views, and scheduling jobs"""
    if not hasattr(client, "thread_messages"):
        client.thread_messages = load_json(THREAD_MESSAGES_FILE)
        logger.info("Loaded thread messages")
    if not hasattr(client, "added_users"):
        client.added_users = load_json(ADDED_USERS_FILE)
        logger.info("Loaded added users")
    if not client.synced:
        await tree.sync()
        client.synced = True
        logger.info("Synced client")
    logger.info(f"Logged in as {client.user}")
    if not hasattr(client, "temp_data"):
        client.temp_data = {}
        logger.info("Initialised temp data")
    if not hasattr(client, "xp_system"):
        from lib.xp_system import XPSystem
        client.xp_system = XPSystem()
        logger.info("XP system initialised")
    reattach_persistent_views(client)
    logger.info("Persistent views reattached and loaded.")
    for command in tree.get_commands():
        logger.info(f"Command loaded: {command.name}")
        await asyncio.sleep(0.1)
    schedule_client_jobs(client, scheduler)
    logger.info(f"{client.user} setup complete")
    await client.backup_bot()


async def on_message(client, message):
    """Handles new message events, including attachments, message links, and forum thread onboarding"""
    if not hasattr(client, "xp_system"):
        from lib.xp_system import XPSystem
        client.xp_system = XPSystem()
        logger.info("XP system initialised")

    if not await restrict_channel_for_new_members(message, CHANNELS.POLITICS, 7, POLITICS_WHITELISTED_USER_IDS):
        return

    await client.xp_system.update_xp(message)

    await process_message_attachments(client, message)
    await process_message_links(client, message)
    if message.author.bot:
        return
    await process_forum_threads(client, message)


async def on_interaction(interaction: Interaction):
    """Handles component interactions for role buttons"""
    if interaction.type == InteractionType.component and "custom_id" in interaction.data:
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            await handleRoleButtonInteraction(interaction)


async def on_member_join(member):
    """Handles new member join events, applying anti-raid checks and adding member role"""
    await handle_new_member_anti_raid(member)
    role = member.guild.get_role(ROLES.MEMBER)
    if role:
        await member.add_roles(role)


async def on_member_remove(member):
    """Handles member removal events"""
    pass


async def on_member_ban(guild, user):
    """Handles member ban events"""
    pass


async def on_message_delete(client, message):
    """Logs deleted messages and associated attachments"""
    async for entry in message.guild.audit_logs(
        action=discord.AuditLogAction.message_delete, limit=1
    ):
        if (
            entry.target.id == message.author.id
            and entry.extra.channel.id == message.channel.id
        ):
            deleter = entry.user
            break
    else:
        deleter = None
    log_channel = client.get_channel(CHANNELS.LOGS)
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
                color=discord.Color.red(),
            )
            embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
            embed.set_image(url="attachment://deleted_message.png")
            if image_file_path is not None:
                with open(image_file_path, "rb") as f:
                    await log_channel.send(
                        file=discord.File(f, "deleted_message.png"), embed=embed
                    )
                os.remove(image_file_path)
        for attachment in message.attachments:
            attachment_link = client.image_cache.get(message.id, {}).get(attachment.url)
            if attachment_link:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    image_embed = discord.Embed(
                        title="Image Deleted",
                        description=f"An image by {message.author.mention} ({message.author.id}) was deleted in {message.channel.mention}.",
                        color=discord.Color.red(),
                    )
                    image_embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
                    image_embed.add_field(name="Image Link", value=f"{attachment_link}")
                    image_embed.set_image(url=attachment_link)
                    await log_channel.send(embed=image_embed)
                else:
                    attachment_embed = discord.Embed(
                        title="Attachments Deleted",
                        description=f"The following attachments by {message.author.mention} ({message.author.id}) were deleted in {message.channel.mention}:\n{attachment.filename}",
                        color=discord.Color.red(),
                    )
                    attachment_embed.add_field(name="Channel Link", value=f"[Click here]({attachment_link})")
                    await log_channel.send(embed=attachment_embed)


async def on_message_edit(client, before, after):
    """Logs message edits with before and after images"""
    if before.author.bot:
        return
    log_channel = client.get_channel(CHANNELS.LOGS)
    if log_channel is not None:
        image_file_path = await create_edited_message_image(before, after)
        message_link = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{after.id}"
        embed = discord.Embed(
            title="Message Edited",
            description=f"Message edited in {before.channel.mention} by {before.author.mention} ({before.author.id}).",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Message Link", value=f"[Click here]({message_link})")
        embed.set_image(url="attachment://edited_message.png")
        if image_file_path is not None:
            with open(image_file_path, "rb") as f:
                await log_channel.send(
                    file=discord.File(f, "edited_message.png"), embed=embed
                )
            os.remove(image_file_path)


async def handle_flag_reaction(reaction, message, user):
    """Handles translation flag reactions"""
    target_language = FLAG_LANGUAGE_MAPPINGS.get(str(reaction.emoji))
    if not target_language:
        return
    users = [u async for u in reaction.users()]
    if len(users) > 1:
        logger.info("Message has already been reacted to with this flag. Skipping translation.")
        return
    if message.content:
        await translate_and_send(reaction, message, target_language, message.author, user)

async def handle_shut_reaction(reaction, user):
    """Handles ':Shut:' reaction for timeout"""
    has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
    message_author = reaction.message.author
    if message_author.is_timed_out():
        logger.info(f"User {message_author} is already timed out. Skipping further actions.")
        return

    try:
        reason = f"Timed out due to ':Shut:' reaction by {user.name}#{user.discriminator}."
        
        if not SHUTCOIN_ENABLED:
            if has_role:
                duration = timedelta(minutes=5)
                await message_author.timeout(discord.utils.utcnow() + duration, reason=reason)
                sticker_message = await reaction.message.reply(stickers=[discord.Object(id=1298758779428536361)])
                sticker_messages[reaction.message.id] = (sticker_message.id, user.id)
                logger.info(f"User {message_author} timed out for {duration} by {user} (Shutcoin disabled).")
            return
        
        if has_role:
            duration = timedelta(minutes=5)
        else:
            if not can_use_shutcoin(user.id): return
            removed = remove_shutcoin(user.id)
            if not removed: return
            duration = timedelta(seconds=30)

        await message_author.timeout(discord.utils.utcnow() + duration, reason=reason)
        sticker_message = await reaction.message.reply(stickers=[discord.Object(id=1298758779428536361)])
        sticker_messages[reaction.message.id] = (sticker_message.id, user.id)
        logger.info(f"User {message_author} was timed out for {duration} due to ':Shut:' reaction by {user}.")
    except Exception as e:
        logger.error(f"Failed to time out user {message_author}: {e}")



async def on_reaction_add(reaction, user):
    """Handles added reactions for translation and timeout"""
    try:
        if str(reaction.emoji) in FLAG_LANGUAGE_MAPPINGS:
            await handle_flag_reaction(reaction, reaction.message, user)
        if ":Shut:" in str(reaction.emoji):
            await handle_shut_reaction(reaction, user)
    except Exception as e:
        logger.error(f"Error in on_reaction_add: {e}")


async def on_reaction_remove(reaction, user):
    """Handles removal of ':Shut:' reaction and cancels timeout if applicable"""
    if ":Shut:" in str(reaction.emoji):
        has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
        if has_role:
            message_author = reaction.message.author
            try:
                sticker_message_info = sticker_messages.get(reaction.message.id)
                if not sticker_message_info:
                    return
                sticker_message_id, initiating_mod_id = sticker_message_info
                if initiating_mod_id != user.id:
                    logger.info(f"Reaction removal ignored as {user} did not initiate the timeout.")
                    return
                reason = f"Timeout removed due to ':Shut:' reaction being removed by {user.name}#{user.discriminator}."
                await message_author.timeout(None, reason=reason)
                logger.info(f"Timeout for user {message_author} was removed due to ':Shut:' reaction being removed by {user}.")
                sticker_message = await reaction.message.channel.fetch_message(sticker_message_id)
                await sticker_message.delete()
                del sticker_messages[reaction.message.id]
                logger.info(f"Deleted sticker message with ID {sticker_message_id} due to reaction being removed.")
            except Exception as e:
                logger.error(f"Failed to remove timeout or delete sticker message for user {message_author}: {e}")


async def on_voice_state_update(member, before, after):
    """Mutes and deafens members joining a voice channel during lockdown"""
    if not is_lockdown_active():
        return
    if after.channel and not before.channel:
        if not any(role.id in VC_LOCKDOWN_WHITELIST for role in member.roles):
            await member.edit(mute=True, deafen=True)
