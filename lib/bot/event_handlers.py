import discord
from discord import Interaction, InteractionType
from datetime import timedelta, datetime
import logging, os, aiohttp, io, json, asyncio, pytz
from collections import defaultdict
from discord import Interaction, InteractionType
from datetime import timedelta, datetime
import logging, os, aiohttp, io, json, asyncio, pytz
from collections import defaultdict

from lib.core.translation import translate_and_send
from lib.features.summary import initialize_summary_data, update_summary_data, post_summary
from lib.core.utils import post_summary_helper, generate_rank_card
from lib.core.discord_helpers import has_role, has_any_role, restrict_channel_for_new_members, send_embed_to_channels, edit_voice_channel_members, fetch_messages_with_context, estimate_tokens
from lib.core.file_operations import load_whitelist, save_whitelist, load_persistent_views, save_persistent_views, load_json_file, save_json_file, set_file_status, is_file_status_active, load_webhook_deletions, save_webhook_deletions
from lib.core.utils import is_lockdown_active
from lib.core.image_processing import trim_image, find_non_overlapping_position, random_color_excluding_blue_and_dark
from lib.core.log_functions import create_message_image, create_edited_message_image
from config import *
from lib.core.constants import FLAG_LANGUAGE_MAPPINGS, TRANSLATION_BLACKLIST_CHANNELS
from lib.economy.economy_manager import can_use_shutcoin, remove_shutcoin, SHUTCOIN_ENABLED
from lib.economy.prediction_system import prediction_embed, _save
from lib.economy.economy_manager import add_bb, remove_bb, ensure_bb, get_all_balances as load_ukpence_data
from lib.economy.prediction_system import prediction_embed, _save, _load, Prediction, BetButtons

from commands.moderation.persistant_role_buttons import handleRoleButtonInteraction
from commands.moderation.anti_raid import handle_new_member_anti_raid
from commands.moderation.archive_channel import ArchiveButtonView, schedule_archive_move
from commands.moderation.overnight_mute import mute_visitors, unmute_visitors

logger = logging.getLogger(__name__)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
POLITICS_WHITELISTED_USER_IDS = load_whitelist()


def set_politics_whitelist(user_ids: list[int]) -> None:
    """Replace the cached politics whitelist with the latest user ids."""
    global POLITICS_WHITELISTED_USER_IDS
    POLITICS_WHITELISTED_USER_IDS = list(user_ids)

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

STAGE_UKPENCE_MULTIPLIER = 1
SERVER_BOOSTER_UKP_DAILY_BONUS = 10

MAX_THREAD_USERS = 990







def reattach_persistent_views(client):
    from commands.moderation.announcement_command import RoleButtonView
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






async def process_pending_emoji_sticker_uploads(client, message):
    """Process pending emoji/sticker uploads from shop purchases."""
    if not message.attachments:
        return False

    # Check if user has pending uploads
    pending_uploads = getattr(client, '_pending_uploads', {})
    user_upload = pending_uploads.get(message.author.id)

    if not user_upload or not user_upload.get('waiting'):
        return False

    # Process the upload
    attachment = message.attachments[0]  # Take the first attachment

    # Validate file type based on upload type
    upload_type = user_upload['type']
    valid_types = []
    max_size = 0

    if upload_type == 'emoji':
        valid_types = ['image/png', 'image/jpeg', 'image/jpg', 'image/gif']
        max_size = 256 * 1024  # 256KB
    else:  # sticker
        valid_types = ['image/png', 'image/gif', 'application/json']  # JSON for Lottie
        max_size = 512 * 1024  # 512KB

    # Check file type
    if attachment.content_type not in valid_types:
        await message.reply(
            f"❌ Invalid file type for {upload_type}. "
            f"Accepted types: {', '.join(valid_types)}"
        )
        return True

    # Check file size
    if attachment.size > max_size:
        await message.reply(
            f"❌ File too large for {upload_type}. "
            f"Maximum size: {max_size // 1024}KB, your file: {attachment.size // 1024}KB"
        )
        return True

    try:
        # Download the file
        async with aiohttp.ClientSession() as session:
            async with session.get(attachment.url) as response:
                if response.status == 200:
                    file_data = await response.read()

                    # Send approval request to cabinet channel
                    cabinet_channel = client.get_channel(CHANNELS.CABINET)
                    if cabinet_channel:
                        from lib.economy.shop_items import EmojiStickerApprovalView

                        embed = discord.Embed(
                            title="🎨 Custom Emoji/Sticker Approval Required",
                            description=f"{message.author.mention} has uploaded a {upload_type} for approval.",
                            color=0xffa500
                        )
                        embed.add_field(name="User", value=message.author.mention, inline=True)
                        embed.add_field(name="Type", value=upload_type.title(), inline=True)
                        embed.add_field(name="Name", value=user_upload['name'], inline=True)

                        if user_upload.get('description'):
                            embed.add_field(name="Description", value=user_upload['description'], inline=True)

                        embed.add_field(name="File Size", value=f"{attachment.size // 1024}KB", inline=True)
                        embed.add_field(name="File Type", value=attachment.content_type, inline=True)

                        embed.set_image(url=attachment.url)
                        embed.set_footer(text="Cabinet members can approve or deny this request.")

                        # Create approval view
                        view = EmojiStickerApprovalView(
                            user=message.author,
                            upload_data=user_upload,
                            file_data=file_data,
                            filename=attachment.filename
                        )

                        await cabinet_channel.send(embed=embed, view=view)

                        # Notify user that their request is pending approval
                        await message.reply(
                            f"✅ Your {upload_type} '{user_upload['name']}' has been submitted for approval! "
                            f"Cabinet members will review it and you'll be notified of the decision."
                        )

                        # Mark as no longer waiting (processed)
                        user_upload['waiting'] = False

                        return True
                    else:
                        await message.reply("❌ Could not find cabinet channel for approval.")
                        return True
                else:
                    await message.reply("❌ Failed to download your file. Please try again.")
                    return True

    except Exception as e:
        logger.error(f"Error processing emoji/sticker upload: {e}")
        await message.reply("❌ An error occurred while processing your upload. Please try again.")
        return True

    return False

async def process_message_attachments(client, message):
    # First check for pending emoji/sticker uploads
    if await process_pending_emoji_sticker_uploads(client, message):
        return  # Upload was processed, don't continue with normal image caching

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
    if not hasattr(client, "thread_messages"):
        client.thread_messages = load_json_file(THREAD_MESSAGES_FILE)
        logger.info("Loaded thread messages")
    if not hasattr(client, "added_users"):
        client.added_users = load_json_file(ADDED_USERS_FILE)
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
        from lib.features.xp_system import XPSystem
        client.xp_system = XPSystem()
        logger.info("XP system initialised")
    reattach_persistent_views(client)
    loaded = _load()
    client.predictions = {}
    for msg_id_str, pd in loaded.items():
        p = Prediction.from_dict(pd)
        client.predictions[p.msg_id] = p
        if not p.locked:
            client.add_view(BetButtons(p), message_id=p.msg_id)
    logger.info("Persistent views reattached and loaded.")
    for command in tree.get_commands():
        logger.info(f"Command loaded: {command.name}")
        await asyncio.sleep(0.1)
    from lib.bot.scheduled_tasks import schedule_client_jobs
    schedule_client_jobs(client, scheduler)
    logger.info(f"{client.user} setup complete")
    await refresh_live_stages(client)
    from lib.bot.backup_manager import backup_database
    await backup_database(client)


async def on_message(client, message):
    if not hasattr(client, "xp_system"):
        from lib.features.xp_system import XPSystem
        client.xp_system = XPSystem()
        logger.info("XP system initialised")

    if not await restrict_channel_for_new_members(message, CHANNELS.POLITICS, 7, POLITICS_WHITELISTED_USER_IDS):
        return

    await client.xp_system.update_xp(message)

    if not message.author.bot and message.type != discord.MessageType.new_member:
        ensure_bb(message.author.id)
    await process_message_attachments(client, message)
    await process_message_links(client, message)
    if message.author.bot:
        return
    # await process_forum_threads(client, message)


async def on_interaction(interaction: Interaction):
    if interaction.type == InteractionType.component and "custom_id" in interaction.data:
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            await handleRoleButtonInteraction(interaction)


async def on_member_join(member):
    await handle_new_member_anti_raid(member)
    role = member.guild.get_role(ROLES.MEMBER)
    if role:
        await member.add_roles(role)


async def on_member_remove(member):
    pass


async def on_member_ban(guild, user):
    pass


async def on_message_delete(client, message):
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
            image_buffer = await create_message_image(message, "Deleted Message")
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
            if image_buffer is not None:
                await log_channel.send(
                    file=discord.File(image_buffer, filename="deleted_message.png"),
                    embed=embed,
                )
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
    if before.author.bot:
        return
    log_channel = client.get_channel(CHANNELS.LOGS)
    if log_channel is not None:
        image_buffer = await create_edited_message_image(before, after)
        message_link = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{after.id}"
        embed = discord.Embed(
            title="Message Edited",
            description=f"Message edited in {before.channel.mention} by {before.author.mention} ({before.author.id}).",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Message Link", value=f"[Click here]({message_link})")
        embed.set_image(url="attachment://edited_message.png")
        if image_buffer is not None:
            await log_channel.send(
                file=discord.File(image_buffer, filename="edited_message.png"),
                embed=embed,
            )


async def handle_flag_reaction(reaction, message, user):
    if message.channel.id in TRANSLATION_BLACKLIST_CHANNELS:
        return
    target_language = FLAG_LANGUAGE_MAPPINGS.get(str(reaction.emoji))
    if not target_language:
        return
    users = [u async for u in reaction.users()]
    if len(users) > 1:
        logger.info("Message has already been reacted to with this flag. Skipping translation.")
        return
    if message.content:
        await translate_and_send(reaction, message, target_language, message.author, user)


def save_shut_count(user_id):
    data = load_json("shut_counts.json")
    data[str(user_id)] = data.get(str(user_id), 0) + 1
    save_json("shut_counts.json", data)


async def handle_shut_reaction(reaction, user):
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
                save_shut_count(message_author.id)
            return
        if has_role:
            duration = timedelta(minutes=5)
        else:
            if not can_use_shutcoin(user.id):
                return
            removed = remove_shutcoin(user.id)
            if not removed:
                return
            duration = timedelta(seconds=30)
        await message_author.timeout(discord.utils.utcnow() + duration, reason=reason)
        sticker_message = await reaction.message.reply(stickers=[discord.Object(id=1298758779428536361)])
        sticker_messages[reaction.message.id] = (sticker_message.id, user.id)
        logger.info(f"User {message_author} was timed out for {duration} due to ':Shut:' reaction by {user}.")
        save_shut_count(message_author.id)
    except Exception as e:
        logger.error(f"Failed to time out user {message_author}: {e}")


async def on_reaction_add(reaction, user):
    try:
        # Check for Americanism correction deletion
        if str(reaction.emoji) == "❌":
            deletions = load_webhook_deletions()
            message_id_str = str(reaction.message.id)
            if message_id_str in deletions:
                owner_id = deletions[message_id_str]
                if user.id == owner_id:
                    try:
                        await reaction.message.delete()
                        del deletions[message_id_str]
                        save_webhook_deletions(deletions)
                        logger.info(f"Deleted webhook message {message_id_str} on request from {user}")
                    except discord.Forbidden:
                        logger.warning(f"Could not delete webhook message {message_id_str} - lack of permissions.")
                    except discord.NotFound:
                        pass
                    return

        if str(reaction.emoji) in FLAG_LANGUAGE_MAPPINGS:
            await handle_flag_reaction(reaction, reaction.message, user)
        if ":Shut:" in str(reaction.emoji):
            await handle_shut_reaction(reaction, user)
    except Exception as e:
        logger.error(f"Error in on_reaction_add: {e}")


async def on_reaction_remove(reaction, user):
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
    if after.channel and not before.channel and is_lockdown_active():
        if not any(role.id in VC_LOCKDOWN_WHITELIST for role in member.roles):
            await member.edit(mute=True, deafen=True)

    client = member._state._get_client()
    stage_events = getattr(client, 'stage_events', set())
    if not hasattr(client, 'stage_join_times'):
        client.stage_join_times = {}
    stage_join_times = client.stage_join_times

    if after.channel and after.channel.id in stage_events and before.channel != after.channel:
        stage_join_times[member.id] = discord.utils.utcnow()
        logger.info(f"[STAGE] join: {member} at {after.channel.name}")

    if before.channel and before.channel.id in stage_events and (not after.channel or after.channel.id not in stage_events):
        start = stage_join_times.pop(member.id, None)
        if start:
            elapsed = (discord.utils.utcnow() - start).total_seconds()
            bonus = (int(elapsed) // 60) * STAGE_UKPENCE_MULTIPLIER
            if bonus:
                add_bb(member.id, bonus)


async def refresh_live_stages(client):
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return
    now = discord.utils.utcnow()
    for ch in guild.stage_channels:
        if ch.instance is not None:
            client.stage_events.add(ch.id)
            for member in ch.members:
                if not hasattr(client, "stage_join_times"):
                    client.stage_join_times = {}
                if member.id not in client.stage_join_times:
                    client.stage_join_times[member.id] = now
                    logger.info(f"[STAGE] backfilled join: {member} in {ch.name}")



async def on_stage_instance_create(stage_instance):
    stage_instance.guild._state._get_client().stage_events.add(stage_instance.channel.id)


async def on_stage_instance_delete(stage_instance):
    client = stage_instance.guild._state._get_client()
    ch_id = stage_instance.channel.id

    uk_timezone = pytz.timezone("Europe/London")
    current_date_str = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    total_awarded_on_delete = 0

    if not hasattr(client, 'stage_join_times'):
        client.stage_join_times = {}

    now_utc = discord.utils.utcnow()
    for m in stage_instance.channel.members:
        start_time_utc = client.stage_join_times.pop(m.id, None)
        if start_time_utc:
            secs = (now_utc - start_time_utc).total_seconds()
            bonus = (int(secs) // 60) * STAGE_UKPENCE_MULTIPLIER
            if bonus > 0:
                add_bb(m.id, bonus)
                logger.info(f"[STAGE END] +{bonus} UKP → User {m.id} for stage end in {stage_instance.channel.name}.")
                total_awarded_on_delete += bonus

    if total_awarded_on_delete > 0:
        _update_daily_metric_file(current_date_str, "stage_rewards_total", total_awarded_on_delete)
        logger.info(f"[STAGE END] Added {total_awarded_on_delete} to stage_rewards_total for {current_date_str} from instance delete.")

    client.stage_events.discard(ch_id)
