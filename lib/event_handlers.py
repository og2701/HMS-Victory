import discord
from discord import Interaction, InteractionType
from datetime import timedelta, datetime
import logging, os, aiohttp, io, json, asyncio, pytz
from collections import defaultdict
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from lib.translation import translate_and_send
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.utils import *
from lib.log_functions import create_message_image, create_edited_message_image
from lib.settings import *
from lib.shutcoin import can_use_shutcoin, remove_shutcoin, SHUTCOIN_ENABLED
from lib.prediction_system import prediction_embed, _save
from lib.ukpence import add_bb, remove_bb, ensure_bb, _load as load_ukpence_data
from lib.prediction_system import prediction_embed, _save, _load, Prediction, BetButtons

from commands.mod_commands.persistant_role_buttons import (
    persistantRoleButtons,
    handleRoleButtonInteraction,
)
from commands.mod_commands.anti_raid import handle_new_member_anti_raid
from commands.mod_commands.archive_channel import (
    ArchiveButtonView,
    schedule_archive_move,
)
from commands.mod_commands.overnight_mute import mute_visitors, unmute_visitors

logger = logging.getLogger(__name__)
logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)

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
SERVER_BOOSTER_UKP_DAILY_BONUS = 30

MAX_THREAD_USERS = 990

def _update_daily_metric_file(date_str, key, value_to_add_or_set, is_total_value=False):
    metrics_data = {}
    if os.path.exists(ECONOMY_METRICS_FILE):
        with open(ECONOMY_METRICS_FILE, "r") as f:
            try:
                metrics_data = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Error decoding {ECONOMY_METRICS_FILE} while updating {key}.")
    
    day_metrics = metrics_data.get(date_str, {})
    if is_total_value:
        day_metrics[key] = value_to_add_or_set
    else: 
        current_value = day_metrics.get(key, 0)
        day_metrics[key] = current_value + value_to_add_or_set
    
    metrics_data[date_str] = day_metrics
    
    with open(ECONOMY_METRICS_FILE, "w") as f:
        json.dump(metrics_data, f, indent=4)


async def sweep_predictions(client):
    now = discord.utils.utcnow().timestamp()
    dirty = False
    for p in client.predictions.values():
        if not p.locked and p.end_ts and p.end_ts <= now:
            p.locked = True
            try:
                ch = client.get_channel(p.channel_id) if p.channel_id else client.get_channel(CHANNELS.BOT_SPAM)
                if ch:
                    msg = await ch.fetch_message(p.msg_id)
                    embed, bar = prediction_embed(p, client)
                    await msg.edit(embed=embed, attachments=[bar], view=None)
            except Exception:
                pass
            dirty = True
    if dirty:
        _save({k: v.to_dict() for k, v in client.predictions.items()})

async def award_stage_bonuses(client):
    now_utc = discord.utils.utcnow()
    if not hasattr(client, 'stage_join_times'):
        client.stage_join_times = {}
    
    uk_timezone = pytz.timezone("Europe/London")

    current_date_str = datetime.now(uk_timezone).strftime("%Y-%m-%d")
    total_awarded_this_call = 0

    for uid, start_time_utc in list(client.stage_join_times.items()):
        minutes = int((now_utc - start_time_utc).total_seconds() // 60)
        if minutes > 0: 
            bonus_awarded = minutes * STAGE_UKPENCE_MULTIPLIER
            add_bb(uid, bonus_awarded)
            client.stage_join_times[uid] = now_utc - timedelta(seconds=((now_utc - start_time_utc).total_seconds() % 60))
            logger.info(f"[STAGE CRON] +{bonus_awarded} UKP → User {uid} for {minutes} full mins.")
            total_awarded_this_call += bonus_awarded
    
    if total_awarded_this_call > 0:
        _update_daily_metric_file(current_date_str, "stage_rewards_total", total_awarded_this_call)
        logger.info(f"[STAGE CRON] Added {total_awarded_this_call} to stage_rewards_total for {current_date_str}.")



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


async def cleanup_thread_members(client):
    cutoff = discord.utils.utcnow() - timedelta(days=30)
    guild = client.get_guild(GUILD_ID)
    if not guild:
        return

    forum_channel = guild.get_channel(FORUM_CHANNEL_ID)
    if not isinstance(forum_channel, discord.ForumChannel):
        return

    bot_id = client.user.id

    for thread in forum_channel.threads:
        try:
            members = await thread.fetch_members()
        except discord.HTTPException:
            continue

        total = len(members)
        logger.info(f"[CLEANUP] {thread.name} has {total} members")
        if total <= MAX_THREAD_USERS:
            continue

        active_ids = set()
        async for msg in thread.history(limit=None, oldest_first=False):
            if msg.created_at < cutoff:
                break
            active_ids.add(msg.author.id)

        inactive_ids = [m.id for m in members if m.id not in active_ids]
        remove_quota = total - MAX_THREAD_USERS + 1
        targets = inactive_ids[:remove_quota]

        logger.info(f"[CLEANUP] Removing {len(targets)} users from {thread.name}")

        for uid in targets:
            try:
                await thread.remove_user(discord.Object(id=uid))
                await asyncio.sleep(0.6)
                async for sys_msg in thread.history(limit=4):
                    if sys_msg.author.id == bot_id and str(uid) in sys_msg.content:
                        try:
                            await sys_msg.delete()
                        except Exception:
                            pass
                        break
            except discord.HTTPException:
                continue

async def award_booster_bonus(client):
    total_booster_rewards_awarded_this_cycle = 0
    guild = client.get_guild(GUILD_ID)
    if not guild:
        logger.error("award_booster_bonus: Guild not found.")
        return

    for member in guild.members:
        if any(role.id == ROLES.SERVER_BOOSTER for role in member.roles):
            add_bb(member.id, SERVER_BOOSTER_UKP_DAILY_BONUS)
            total_booster_rewards_awarded_this_cycle += SERVER_BOOSTER_UKP_DAILY_BONUS
            
    logger.info(f"Total UKPence from booster bonuses awarded: {total_booster_rewards_awarded_this_cycle}")

    uk_timezone = pytz.timezone("Europe/London")
    now = datetime.now(uk_timezone)
    yesterday_str_for_bonus = (now - timedelta(days=1)).strftime("%Y-%m-%d") 
    today_str_for_sod_snapshot = now.strftime("%Y-%m-%d") 
    
    _update_daily_metric_file(yesterday_str_for_bonus, "booster_rewards_total", total_booster_rewards_awarded_this_cycle, is_total_value=True)
    
    current_balances_after_booster = load_ukpence_data()
    sod_circulation_today = sum(current_balances_after_booster.values())
    _update_daily_metric_file(today_str_for_sod_snapshot, "total_circulation_start_of_day", sod_circulation_today, is_total_value=True)
    
    logger.info(f"Logged booster rewards for {yesterday_str_for_bonus} ({total_booster_rewards_awarded_this_cycle} UKP) and SOD circulation for {today_str_for_sod_snapshot} ({sod_circulation_today} UKP).")


def schedule_client_jobs(client, scheduler):
    scheduler.add_job(award_booster_bonus, CronTrigger(hour=0, minute=0, timezone="Europe/London"), args=[client], id="award_booster_bonus_job", name="Award Daily Booster UKPence & Log SOD Circulation")
    scheduler.add_job(client.daily_summary, CronTrigger(hour=0, minute=1, timezone="Europe/London"), id="daily_summary_job", name="Daily Summary, Chat Rewards & Economy Metrics")
    scheduler.add_job(client.post_daily_economy_stats, CronTrigger(hour=0, minute=5, timezone="Europe/London"), id="post_daily_economy_stats_job", name="Post Daily UKPence Economy Stats")
    
    scheduler.add_job(client.weekly_summary, CronTrigger(day_of_week="mon", hour=0, minute=2, timezone="Europe/London"))
    scheduler.add_job(client.monthly_summary, CronTrigger(day=1, hour=0, minute=3, timezone="Europe/London"))
    scheduler.add_job(client.clear_image_cache, CronTrigger(day_of_week="sun", hour=0, minute=4, timezone="Europe/London"))
    scheduler.add_job(client.backup_bot, IntervalTrigger(minutes=30, timezone="Europe/London"))
    scheduler.add_job(sweep_predictions, IntervalTrigger(seconds=30), args=[client])
    scheduler.add_job(award_stage_bonuses, IntervalTrigger(minutes=1), args=[client], id="award_stage_bonuses_interval", name="Award Stage UKPence (Interval)") # Runs every minute
    scheduler.add_job(cleanup_thread_members, IntervalTrigger(days=1, timezone="Europe/London"), args=[client], next_run_time=discord.utils.utcnow() + timedelta(minutes=5))

    scheduler.add_job(mute_visitors, CronTrigger(hour=3, minute=0, timezone="Europe/London"), args=[client.get_guild(GUILD_ID)], id="mute_visitors_job", name="Mute visitors overnight")
    scheduler.add_job(unmute_visitors, CronTrigger(hour=6, minute=0, timezone="Europe/London"), args=[client.get_guild(GUILD_ID)], id="unmute_visitors_job", name="Unmute visitors in the morning")

    scheduler.start()

async def process_message_attachments(client, message):
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
    schedule_client_jobs(client, scheduler)
    logger.info(f"{client.user} setup complete")
    await refresh_live_stages(client)
    await client.backup_bot()


async def on_message(client, message):
    if not hasattr(client, "xp_system"):
        from lib.xp_system import XPSystem
        client.xp_system = XPSystem()
        logger.info("XP system initialised")

    if not await restrict_channel_for_new_members(message, CHANNELS.POLITICS, 7, POLITICS_WHITELISTED_USER_IDS):
        return

    await client.xp_system.update_xp(message)
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