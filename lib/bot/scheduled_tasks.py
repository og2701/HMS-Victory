import discord
import logging
import os
import json
import pytz
import asyncio
from datetime import datetime, timedelta
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import *
from lib.features.summary import initialize_summary_data, update_summary_data, post_summary
from lib.economy.economy_manager import add_bb, get_all_balances as load_ukpence_data
from lib.economy.economy_stats_html import create_economy_stats_image
from lib.bot.backup_manager import zip_and_send_folder, backup_database, backup_bot
from lib.core.file_operations import load_webhook_deletions, save_webhook_deletions
from lib.economy.prediction_system import prediction_embed, _save, _load, Prediction
from commands.moderation.overnight_mute import mute_visitors, unmute_visitors

logger = logging.getLogger(__name__)

STAGE_UKPENCE_MULTIPLIER = 1
SERVER_BOOSTER_UKP_DAILY_BONUS = 10
MAX_THREAD_USERS = 990
FORUM_CHANNEL_ID = 1341451323249266711

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


async def daily_summary(client):
    uk_timezone = pytz.timezone("Europe/London")
    yesterday_dt = datetime.now(uk_timezone) - timedelta(days=1)
    yesterday_str = yesterday_dt.strftime("%Y-%m-%d")

    summary_file_path = f"daily_summaries/daily_summary_{yesterday_str}.json"
    awarded_users_for_log = []
    total_chat_rewards_this_cycle = 0
    num_to_reward = 5
    flat_reward_amount = 50

    if os.path.exists(summary_file_path):
        try:
            with open(summary_file_path, "r") as file:
                daily_data_content = json.load(file)
            active_members_data = daily_data_content.get("active_members", {})

            if active_members_data:
                sorted_active_members = sorted(active_members_data.items(), key=lambda item: item[1], reverse=True)
                log_channel = client.get_channel(CHANNELS.LOGS)
                num_rewarded_actually = 0
                for i, (user_id_str, message_count) in enumerate(sorted_active_members):
                    if i < num_to_reward:
                        user_id = int(user_id_str)
                        add_bb(user_id, flat_reward_amount)
                        num_rewarded_actually += 1
                        awarded_user_info = f"User ID {user_id} (Top {i+1} chatter, {message_count} messages): +{flat_reward_amount} UKPence"
                        awarded_users_for_log.append(awarded_user_info)
                    else:
                        break
                total_chat_rewards_this_cycle = num_rewarded_actually * flat_reward_amount

                if awarded_users_for_log and log_channel:
                    log_message = f"Top {num_rewarded_actually} Chatter Rewards for {yesterday_str} ({flat_reward_amount} UKP each):\n" + "\n".join(awarded_users_for_log)
                    logger.info(log_message)
                    try:
                        await log_channel.send(f"```{log_message}```")
                    except Exception as e:
                        logger.error(f"Failed to send top chatter reward log to Discord: {e}")
                elif active_members_data:
                        logger.info(f"Fewer than {num_to_reward} chatters on {yesterday_str}. Total chat rewards: {total_chat_rewards_this_cycle} UKP")
            else:
                logger.info(f"No active members data in {summary_file_path} for {yesterday_str}. No chat rewards.")
        except json.JSONDecodeError:
            logger.error(f"Could not decode JSON from {summary_file_path}. Skipping top chatter rewards for {yesterday_str}.")
        except Exception as e:
            logger.error(f"Error processing chat rewards for {yesterday_str}: {e}", exc_info=True)
    else:
        logger.warning(f"No summary data file at {summary_file_path} for {yesterday_str}. Skipping top chatter rewards.")

    metrics_data = {}
    if os.path.exists(ECONOMY_METRICS_FILE):
        with open(ECONOMY_METRICS_FILE, "r") as f:
            try:
                metrics_data = json.load(f)
            except json.JSONDecodeError:
                logger.error(f"Error decoding {ECONOMY_METRICS_FILE}. Data for {yesterday_str} might be incomplete.")

    day_metrics = metrics_data.get(yesterday_str, {})
    day_metrics["chat_rewards_total"] = total_chat_rewards_this_cycle

    current_ukpence_balances = load_ukpence_data()
    total_circulation_at_eod = sum(current_ukpence_balances.values())
    day_metrics["total_circulation_end_of_day"] = total_circulation_at_eod

    metrics_data[yesterday_str] = day_metrics

    with open(ECONOMY_METRICS_FILE, "w") as f:
        json.dump(metrics_data, f, indent=4)
    logger.info(f"Finalized economy metrics for {yesterday_str}: ChatRewards={day_metrics.get('chat_rewards_total', 'N/A')}, TotalCircEOD={total_circulation_at_eod}")

    if not os.path.exists(BALANCE_SNAPSHOT_DIR):
        try:
            os.makedirs(BALANCE_SNAPSHOT_DIR)
            logger.info(f"Created balance snapshot directory: {BALANCE_SNAPSHOT_DIR}")
        except OSError as e:
            logger.error(f"Could not create balance snapshot directory {BALANCE_SNAPSHOT_DIR}: {e}")

    if os.path.exists(BALANCE_SNAPSHOT_DIR):
        snapshot_filename = f"ukpence_balances_{yesterday_str}.json"
        snapshot_path = os.path.join(BALANCE_SNAPSHOT_DIR, snapshot_filename)
        with open(snapshot_path, "w") as f_snap:
            json.dump(current_ukpence_balances, f_snap, indent=4)
        logger.info(f"Saved UKPence balance snapshot for {yesterday_str} to {snapshot_path}")

    await post_summary(client, CHANNELS.COMMONS, "daily", date=yesterday_str)

    await zip_and_send_folder(
        client=client,
        folder_path="./daily_summaries",
        channel_id=CHANNELS.DATA_BACKUP,
        zip_filename_prefix=f"daily_summaries_as_of_{yesterday_str}",
    )


async def post_daily_economy_stats(client):
    logger.info("Attempting to post daily UKPence economy stats...")
    try:
        guild = client.get_guild(GUILD_ID)
        if not guild:
            logger.error("Daily economy stats: Primary guild not found.")
            return

        image_buffer = await create_economy_stats_image(guild)

        if image_buffer is not None:
            bot_spam_channel_id = CHANNELS.BOT_SPAM
            bot_spam_channel = client.get_channel(bot_spam_channel_id)

            if bot_spam_channel:
                discord_file = discord.File(
                    image_buffer, filename="ukpeconomy_daily.png"
                )
                await bot_spam_channel.send(file=discord_file)
                logger.info(f"Successfully posted daily economy stats to #{bot_spam_channel.name}")
            else:
                logger.error(f"Daily economy stats: CHANNELS.BOT_SPAM (ID: {bot_spam_channel_id}) not found.")
        else:
            logger.error("Daily economy stats: Failed to generate or find the economy stats image.")
    except Exception as e:
        logger.error(f"Error in post_daily_economy_stats: {e}", exc_info=True)


async def weekly_summary(client):
    await post_summary(client, CHANNELS.COMMONS, "weekly")


async def monthly_summary(client):
    await post_summary(client, CHANNELS.COMMONS, "monthly")


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


async def cleanup_webhook_reactions(client):
    """
    Removes the ❌ reaction from corrected messages older than 1 hour.
    """
    deletions = load_webhook_deletions()
    if not deletions:
        return

    now = datetime.now().timestamp()
    one_hour_secs = 3600
    dirty = False
    
    # We iterate over a copy of keys to allow deletion during iteration
    for msg_id_str in list(deletions.keys()):
        data = deletions[msg_id_str]
        
        # Skip if it's the old format (just an int), we'll let those sit 
        # or handle them if we want, but better to just skip so as not to error.
        if not isinstance(data, dict):
            # Optionally remove old format entries after a while
            continue
            
        timestamp = data.get("timestamp")
        if not timestamp:
            continue
            
        if now - timestamp > one_hour_secs:
            # Try to find the message and remove its reaction
            channel_id = data.get("channel_id")
            if channel_id:
                try:
                    channel = client.get_channel(channel_id)
                    if channel:
                        msg = await channel.fetch_message(int(msg_id_str))
                        await msg.clear_reaction("❌")
                        logger.info(f"Removed ❌ reaction from message {msg_id_str} (expired).")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"Failed to clear reaction from message {msg_id_str}: {e}")
            
            del deletions[msg_id_str]
            dirty = True
            logger.info(f"Cleaned up deletion mapping for message {msg_id_str} (expired).")

    if dirty:
        save_webhook_deletions(deletions)


def schedule_client_jobs(client, scheduler):
    scheduler.add_job(award_booster_bonus, CronTrigger(hour=0, minute=0, timezone="Europe/London"), args=[client], id="award_booster_bonus_job", name="Award Daily Booster UKPence & Log SOD Circulation")
    scheduler.add_job(daily_summary, CronTrigger(hour=0, minute=1, timezone="Europe/London"), args=[client], id="daily_summary_job", name="Daily Summary, Chat Rewards & Economy Metrics")
    scheduler.add_job(post_daily_economy_stats, CronTrigger(hour=0, minute=5, timezone="Europe/London"), args=[client], id="post_daily_economy_stats_job", name="Post Daily UKPence Economy Stats")

    scheduler.add_job(weekly_summary, CronTrigger(day_of_week="mon", hour=0, minute=2, timezone="Europe/London"), args=[client])
    scheduler.add_job(monthly_summary, CronTrigger(day=1, hour=0, minute=3, timezone="Europe/London"), args=[client])
    scheduler.add_job(client.clear_image_cache, CronTrigger(day_of_week="sun", hour=0, minute=4, timezone="Europe/London"))
    # scheduler.add_job(backup_bot, IntervalTrigger(minutes=30, timezone="Europe/London"), args=[client])
    scheduler.add_job(sweep_predictions, IntervalTrigger(seconds=30), args=[client])
    scheduler.add_job(award_stage_bonuses, IntervalTrigger(minutes=1), args=[client], id="award_stage_bonuses_interval", name="Award Stage UKPence (Interval)") # Runs every minute
    scheduler.add_job(cleanup_thread_members, IntervalTrigger(days=1, timezone="Europe/London"), args=[client], next_run_time=discord.utils.utcnow() + timedelta(minutes=5))

    scheduler.add_job(mute_visitors, CronTrigger(hour=3, minute=0, timezone="Europe/London"), args=[client.get_guild(GUILD_ID)], id="mute_visitors_job", name="Mute visitors overnight")
    scheduler.add_job(unmute_visitors, CronTrigger(hour=7, minute=0, timezone="Europe/London"), args=[client.get_guild(GUILD_ID)], id="unmute_visitors_job", name="Unmute visitors in the morning")
    
    scheduler.add_job(backup_database, IntervalTrigger(minutes=120, timezone="Europe/London"), args=[client], id="backup_database_job", name="Backup SQLite Database")
    scheduler.add_job(cleanup_webhook_reactions, IntervalTrigger(minutes=15), args=[client], id="cleanup_webhook_reactions_job", name="Cleanup Webhook Deletion Reactions")

    scheduler.start()
