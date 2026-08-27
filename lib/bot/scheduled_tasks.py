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
from lib.features.dm_spam_watch import sweep as sweep_dm_spam_flags
from lib.features.crossword import prewarm_board as prewarm_crossword_board
from lib.economy.economy_manager import add_bb, get_bb, get_all_balances as load_ukpence_data
from lib.economy.bank_manager import BankManager
from lib.economy.economy_stats_html import create_economy_stats_image
from database import award_badge
from lib.bot.backup_manager import zip_and_send_folder, backup_database, backup_bot, backup_json_data
from lib.core.file_operations import load_webhook_deletions, save_webhook_deletions, atomic_write_json
from lib.economy.prediction_system import _save, _load, Prediction
from commands.moderation.overnight_mute import mute_visitors, unmute_visitors

logger = logging.getLogger(__name__)

STAGE_UKPENCE_MULTIPLIER = 1
SERVER_BOOSTER_UKP_DAILY_BONUS = 100
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

    atomic_write_json(ECONOMY_METRICS_FILE, metrics_data, indent=4)


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
                num_rewarded_actually = min(len(sorted_active_members), num_to_reward)
                total_chat_rewards_this_cycle = num_rewarded_actually * flat_reward_amount
                
                if total_chat_rewards_this_cycle > 0:
                    from lib.bot.event_handlers import award_badge_with_notify
                    for i, (user_id_str, message_count) in enumerate(sorted_active_members):
                        user_id = int(user_id_str)
                        
                        # Award Active Chatter badge to EVERYONE with 200+ messages
                        if message_count >= 200:
                            await award_badge_with_notify(client, user_id, 'active_chatter')
                        
                        # Award Top Chatter rewards ONLY to top 5
                        if i < num_to_reward:
                            if add_bb(user_id, flat_reward_amount, reason="Top chatter daily reward",
                                      discretionary=True):
                                await award_badge_with_notify(client, user_id, 'top_chatter')
                                awarded_user_info = f"User ID {user_id} (Top {i+1} chatter, {message_count} messages): +{flat_reward_amount} UKPence"
                                awarded_users_for_log.append(awarded_user_info)
                            else:
                                logger.error(f"Bank insufficient to pay top chatter reward for {user_id}.")
                        
                        # Break early if we've handled the top 5 AND there are no more potential active chatters
                        if i >= num_to_reward and message_count < 200:
                            break
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

    atomic_write_json(ECONOMY_METRICS_FILE, metrics_data, indent=4)
    logger.info(f"Finalised economy metrics for {yesterday_str}: ChatRewards={day_metrics.get('chat_rewards_total', 'N/A')}, TotalCircEOD={total_circulation_at_eod}")

    if not os.path.exists(BALANCE_SNAPSHOT_DIR):
        try:
            os.makedirs(BALANCE_SNAPSHOT_DIR)
            logger.info(f"Created balance snapshot directory: {BALANCE_SNAPSHOT_DIR}")
        except OSError as e:
            logger.error(f"Could not create balance snapshot directory {BALANCE_SNAPSHOT_DIR}: {e}")

    if os.path.exists(BALANCE_SNAPSHOT_DIR):
        snapshot_filename = f"ukpence_balances_{yesterday_str}.json"
        snapshot_path = os.path.join(BALANCE_SNAPSHOT_DIR, snapshot_filename)
        atomic_write_json(snapshot_path, current_ukpence_balances, indent=4)
        logger.info(f"Saved UKPence balance snapshot for {yesterday_str} to {snapshot_path}")

    # Keep the granular balance history bounded (older history is covered by daily snapshots).
    try:
        from database import DatabaseManager
        cutoff = int(datetime.now(pytz.utc).timestamp()) - 90 * 86400
        DatabaseManager.execute("DELETE FROM balance_history WHERE ts < ?", (cutoff,))
    except Exception:
        logger.error("balance_history prune failed", exc_info=True)

    # Keep the statement ledger to ~13 months so a full year back is always navigable.
    try:
        from database import DatabaseManager
        txn_cutoff = int(datetime.now(pytz.utc).timestamp()) - 400 * 86400
        DatabaseManager.execute("DELETE FROM user_transactions WHERE ts < ?", (txn_cutoff,))
    except Exception:
        logger.error("user_transactions prune failed", exc_info=True)

    # Daily summaries post in the dedicated thread (weekly/monthly stay in COMMONS).
    # get_channel misses archived/uncached threads, so fall back to fetch_channel;
    # sending to an archived thread auto-unarchives it. If the thread can't be reached
    # at all, fall back to COMMONS so the summary is never silently dropped.
    daily_target = client.get_channel(CHANNELS.DAILY_SUMMARY_THREAD)
    if daily_target is None:
        try:
            daily_target = await client.fetch_channel(CHANNELS.DAILY_SUMMARY_THREAD)
        except Exception as e:
            logger.warning(
                f"Daily summary thread {CHANNELS.DAILY_SUMMARY_THREAD} unreachable ({e}); "
                f"falling back to COMMONS."
            )
            daily_target = client.get_channel(CHANNELS.COMMONS)
    await post_summary(
        client, CHANNELS.DAILY_SUMMARY_THREAD, "daily",
        channel_override=daily_target, date=yesterday_str,
    )

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

        image_buffer = await create_economy_stats_image(guild, client)

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
    from lib.economy.prediction_system import award_indecisive_badges, _edit_prediction_message
    now = discord.utils.utcnow().timestamp()
    dirty = False
    for p in client.predictions.values():
        if not p.locked and p.end_ts and p.end_ts <= now:
            p.locked = True
            try:
                ch = client.get_channel(p.channel_id) if p.channel_id else client.get_channel(CHANNELS.BOT_SPAM)
                if ch:
                    msg = await ch.fetch_message(p.msg_id)
                    await _edit_prediction_message(msg, p, client, interactive=False)
            except Exception:
                pass
            # Award 'indecisive' on auto-lock too, matching the manual Lock button.
            try:
                await award_indecisive_badges(client, p)
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
        if minutes <= 0:
            continue
        bal = get_bb(uid)
        if bal >= 20000:
            client.stage_join_times[uid] = now_utc   # over the cap: no reward, don't bank the time
            continue
        # Taper the Stage faucet by wealth: 1/min under 10k, 1 per 5 min from 10k-20k, 0 over 20k.
        period = 5 if bal >= 10000 else 1
        units = minutes // period
        if units <= 0:
            continue                                  # not enough time for this tier yet; keep accruing
        bonus_awarded = units * STAGE_UKPENCE_MULTIPLIER
        if add_bb(uid, bonus_awarded, reason=f"Stage Participation Reward ({units * period}m)",
                  discretionary=True):
            from lib.bot.event_handlers import award_badge_with_notify
            await award_badge_with_notify(client, uid, 'stage_fan')
            client.stage_join_times[uid] = start_time_utc + timedelta(seconds=units * period * 60)
            logger.info(f"[STAGE CRON] +{bonus_awarded} UKP → User {uid} ({units * period}m, bal {bal:,}).")
            total_awarded_this_call += bonus_awarded
        else:
            logger.error(f"[STAGE CRON] Bank insufficient for {bonus_awarded} UKP to User {uid}.")
            # We do NOT update their client.stage_join_times[uid] so they keep their accumulated time.

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

    booster_ids = [member.id for member in guild.members if any(role.id == ROLES.SERVER_BOOSTER for role in member.roles)]
    total_reward_needed = len(booster_ids) * SERVER_BOOSTER_UKP_DAILY_BONUS

    for member_id in booster_ids:
        if add_bb(member_id, SERVER_BOOSTER_UKP_DAILY_BONUS, reason="Server booster daily bonus",
                  discretionary=True):
            total_booster_rewards_awarded_this_cycle += SERVER_BOOSTER_UKP_DAILY_BONUS
        else:
            logger.error(f"Bank insufficient for booster reward for User {member_id}.")
            log_channel = client.get_channel(CHANNELS.LOGS)
            if log_channel:
                 await log_channel.send(f"⚠️ **Economy Alert**: Server Bank is empty - could not pay daily booster reward to <@{member_id}>.")

    if total_booster_rewards_awarded_this_cycle > 0:
        logger.info(f"Total UKPence from booster bonuses awarded: {total_booster_rewards_awarded_this_cycle}")
    else:
        logger.info("No server boosters to reward this cycle (or bank empty).")

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
    Removes the ❌ reaction from corrected messages older than 1 minute.
    """
    deletions = load_webhook_deletions()
    if not deletions:
        return

    now = datetime.now().timestamp()
    one_min_secs = 60
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
            
        if now - timestamp > one_min_secs:
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


async def sweep_orphaned_gate_messages(client):
    """Delete leftover new-member gate notices in politics. They're sent with
    delete_after=10, but that timer is in-process — a restart inside the window
    orphans the message, and we restart often (deploys)."""
    try:
        channel = client.get_channel(CHANNELS.POLITICS)
        if channel is None:
            return
        cutoff = discord.utils.utcnow() - timedelta(hours=48)
        async for msg in channel.history(limit=200):
            if msg.created_at < cutoff:
                break  # newest-first; orphans older than this were already seen by a prior boot
            if msg.author.id == client.user.id and "you need to be in the server for at least" in msg.content:
                try:
                    await msg.delete()
                    logger.info(f"Deleted orphaned gate message {msg.id} in #politics.")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                    logger.warning(f"Failed to delete orphaned gate message {msg.id}: {e}")
    except Exception:
        logger.error("Orphaned gate message sweep failed", exc_info=True)


async def auto_restock_shop(client):
    """Restock any auto-restock items whose 12h cycle is due (DB-driven via
    last_restock, so it survives restarts — a plain 12h IntervalTrigger never
    fired because the bot restarts more often than that)."""
    try:
        from lib.economy.shop_inventory import ShopInventory
        from lib.economy.shop_items import get_shop_items

        restocked_items = ShopInventory.auto_restock_items(due_only=True)
        
        if restocked_items:
            # Log the restock
            log_channel = client.get_channel(CHANNELS.BOT_USAGE_LOG)
            if log_channel:
                shop_items = {item.id: item for item in get_shop_items()}
                
                embed = discord.Embed(
                    title="🔄 Automated Shop Restock",
                    description=f"Restocked {len(restocked_items)} items.",
                    color=0x00ff00
                )
                
                restock_list = []
                for item_id in restocked_items:
                    shop_item = shop_items.get(item_id)
                    item_name = shop_item.name if shop_item else item_id
                    quantity = ShopInventory.get_quantity(item_id)
                    restock_list.append(f"• **{item_name}** - New Quantity: {quantity}")
                
                # Truncate if too long (Discord limits)
                description = "\n".join(restock_list)
                if len(description) > 4000:
                    description = description[:3990] + "..."
                    
                embed.description += "\n\n" + description
                await log_channel.send(embed=embed)
                logger.info(f"Automated restock completed for {len(restocked_items)} items.")
    except Exception as e:
        logger.error(f"Error during automated shop restock: {e}", exc_info=True)


async def _bond_maturity_tick(client):
    """Pay out any bonds that have matured (DB-driven, so it catches up after a restart)."""
    try:
        from lib.economy.bonds import mature_due
        await mature_due(client)
    except Exception:
        logger.error("Bond maturity tick failed", exc_info=True)


async def _purge_message_archive(client):
    """Trim the rolling message archive (bulk-delete logging) past its retention window."""
    try:
        from lib.features.message_archive import purge_old
        removed = purge_old()
        if removed:
            logger.info(f"Message archive purge removed {removed} rows.")
    except Exception:
        logger.error("Message archive purge failed", exc_info=True)


def schedule_client_jobs(client, scheduler):
    """Register/start process jobs once across repeated gateway ready events."""
    if not getattr(client, "_scheduler_jobs_registered", False):
        _register_client_jobs(client, scheduler)
        client._scheduler_jobs_registered = True
        logger.info("APScheduler jobs registered.")
    else:
        logger.info("APScheduler jobs already registered; skipping duplicate registration.")

    if not getattr(scheduler, "running", False):
        scheduler.start()
        logger.info("APScheduler started.")
    else:
        logger.info("APScheduler already running; skipping duplicate start.")


def _add_process_job(scheduler, function, trigger=None, *, id, **kwargs):
    """Add a stable process job so a partial registration pass is retry-safe."""
    kwargs.update(id=id, replace_existing=True)
    if trigger is None:
        return scheduler.add_job(function, **kwargs)
    return scheduler.add_job(function, trigger, **kwargs)


def _register_client_jobs(client, scheduler):
    # Keep one headless Chrome warm for the whole process: pre-warms it ~15s after boot and
    # recycles it for memory leaks in the background, so /rank and casino renders rarely cold-start.
    from lib.core.image_processing import maintain_render_engine
    _add_process_job(scheduler, maintain_render_engine, IntervalTrigger(seconds=60),
                     id="render_engine_keeper", name="Keep headless Chrome warm",
                     next_run_time=discord.utils.utcnow() + timedelta(seconds=15))

    _add_process_job(scheduler, _bond_maturity_tick, IntervalTrigger(minutes=2), args=[client], id="bond_maturity_job", name="Pay matured bonds")
    _add_process_job(scheduler, _purge_message_archive, CronTrigger(hour=4, minute=30, timezone="Europe/London"), args=[client], id="purge_message_archive_job", name="Purge old message archive rows")
    _add_process_job(scheduler, award_booster_bonus, CronTrigger(hour=0, minute=0, timezone="Europe/London"), args=[client], id="award_booster_bonus_job", name="Award Daily Booster UKPence & Log SOD Circulation")
    _add_process_job(scheduler, daily_summary, CronTrigger(hour=0, minute=1, timezone="Europe/London"), args=[client], id="daily_summary_job", name="Daily Summary, Chat Rewards & Economy Metrics")
    _add_process_job(scheduler, post_daily_economy_stats, CronTrigger(hour=0, minute=5, timezone="Europe/London"), args=[client], id="post_daily_economy_stats_job", name="Post Daily UKPence Economy Stats")

    _add_process_job(scheduler, weekly_summary, CronTrigger(day_of_week="mon", hour=0, minute=2, timezone="Europe/London"), args=[client], id="weekly_summary_job")
    _add_process_job(scheduler, monthly_summary, CronTrigger(day=1, hour=0, minute=3, timezone="Europe/London"), args=[client], id="monthly_summary_job")
    _add_process_job(scheduler, client.clear_image_cache, CronTrigger(day_of_week="sun", hour=0, minute=4, timezone="Europe/London"), id="clear_image_cache_job")
    # scheduler.add_job(backup_bot, IntervalTrigger(minutes=30, timezone="Europe/London"), args=[client])
    _add_process_job(scheduler, sweep_predictions, IntervalTrigger(seconds=30), args=[client], id="sweep_predictions_interval")
    _add_process_job(scheduler, award_stage_bonuses, IntervalTrigger(minutes=1), args=[client], id="award_stage_bonuses_interval", name="Award Stage UKPence (Interval)") # Runs every minute
    _add_process_job(scheduler, cleanup_thread_members, IntervalTrigger(days=1, timezone="Europe/London"), args=[client], id="cleanup_thread_members_job", next_run_time=discord.utils.utcnow() + timedelta(minutes=5))

    _add_process_job(scheduler, mute_visitors, CronTrigger(hour=3, minute=0, timezone="Europe/London"), args=[client.get_guild(GUILD_ID)], id="mute_visitors_job", name="Mute visitors overnight")
    _add_process_job(scheduler, unmute_visitors, CronTrigger(hour=7, minute=0, timezone="Europe/London"), args=[client.get_guild(GUILD_ID)], id="unmute_visitors_job", name="Unmute visitors in the morning")
    
    _add_process_job(scheduler, backup_database, IntervalTrigger(minutes=5, timezone="Europe/London"), args=[client], id="backup_database_job", name="Backup SQLite Database")
    _add_process_job(scheduler, backup_json_data, IntervalTrigger(minutes=5, timezone="Europe/London"), args=[client], id="backup_json_data_job", name="Backup JSON State")
    _add_process_job(scheduler, cleanup_webhook_reactions, IntervalTrigger(minutes=1), args=[client], id="cleanup_webhook_reactions_job", name="Cleanup Webhook Deletion Reactions")

    _add_process_job(scheduler, process_economy_logs, IntervalTrigger(seconds=15), args=[client], id="process_economy_logs_interval", name="Process Economy Log Queue")
    _add_process_job(scheduler, process_voice_activity_log, IntervalTrigger(seconds=30), args=[client], id="process_voice_activity_log_interval", name="Post Voice Activity Log")
    # Discord's unusual-DM flag is not in discord.py, so this is a raw member sweep - 13
    # requests over a 12k guild. The flag lasts 24h, so 15 minutes is far finer than it
    # needs to be; the point is mods hearing about it while the account is still there.
    # The board's bytes are identical for everyone until somebody answers something, so
    # hosting today's empty grid up front turns the first /crossword of the day from an
    # upload into a cache hit. Once just after boot, and again just after the puzzle rolls.
    _add_process_job(scheduler, prewarm_crossword_board, args=[client],
                     id="crossword_prewarm_boot", name="Prewarm Crossword Board",
                     next_run_time=discord.utils.utcnow() + timedelta(seconds=45))
    _add_process_job(scheduler, prewarm_crossword_board,
                     CronTrigger(hour=0, minute=1, timezone="Europe/London"), args=[client],
                     id="crossword_prewarm_daily", name="Prewarm Crossword Board (daily)")
    _add_process_job(scheduler, sweep_dm_spam_flags, IntervalTrigger(minutes=15), args=[client],
                     id="dm_spam_flag_sweep", name="Sweep Unusual DM Activity Flags",
                     next_run_time=discord.utils.utcnow() + timedelta(seconds=90))
    # One-shot on boot: clean up gate notices orphaned by a restart mid-delete_after
    _add_process_job(scheduler, sweep_orphaned_gate_messages, args=[client], id="sweep_gate_orphans_boot", name="Sweep Orphaned Gate Messages", next_run_time=discord.utils.utcnow() + timedelta(seconds=20))
    # Frequent tick, not a 12h interval: the 12h cycle lives in the DB (last_restock),
    # so it survives the frequent deploy restarts that reset in-process interval timers.
    # next_run_time so the first catch-up fires right after boot rather than one
    # full interval later (the APScheduler default that broke the original 12h job).
    _add_process_job(scheduler, auto_restock_shop, IntervalTrigger(minutes=10), args=[client], id="auto_restock_shop_interval", name="Automated Shop Restock", next_run_time=discord.utils.utcnow() + timedelta(seconds=30))

    _add_process_job(scheduler, apply_inactivity_tax, CronTrigger(day_of_week="fri", hour=0, minute=0, timezone="Europe/London"), args=[client], id="apply_inactivity_tax_job", name="Weekly Inactivity Tax")

    _add_process_job(scheduler, apply_wealth_demurrage, CronTrigger(day_of_week="fri", hour=0, minute=5, timezone="Europe/London"), args=[client], id="apply_wealth_demurrage_job", name="Weekly Wealth Demurrage")

    # Runs last of the three so the two wealth taxes have already taken their cut - a hoarder
    # who is also economy-dormant is charged on what's left, not on the same UKP twice.
    _add_process_job(scheduler, apply_economy_dormant_tax, CronTrigger(day_of_week="fri", hour=0, minute=10, timezone="Europe/London"), args=[client], id="apply_economy_dormant_tax_job", name="Weekly Economy-Dormant Tax")

    # Lottery tick (every 2 min): while a round is open - started manually by staff via
    # /lottery-start - this posts the periodic reminders and draws a sold-out round once
    # it passes its minimum runtime. No weekly auto-draw/auto-open: the lottery is manual.
    _add_process_job(scheduler, _lottery_tick, IntervalTrigger(minutes=2), args=[client],
                     id="lottery_tick_job", name="Lottery tick (reminders + sellout)")

    _register_pending_scheduled_predictions(client, scheduler)


async def _lottery_tick(client):
    from lib.economy.lottery import lottery_tick
    await lottery_tick(client)


def _register_pending_scheduled_predictions(client, scheduler):
    """On boot, re-register every pending scheduled prediction with APScheduler.
    Past-due rows fire shortly after startup via misfire_grace_time."""
    try:
        from database import DatabaseManager
        from apscheduler.triggers.date import DateTrigger
        from datetime import datetime, timezone
        from lib.economy.prediction_system import post_scheduled_prediction

        rows = DatabaseManager.fetch_all(
            "SELECT id, scheduled_ts FROM scheduled_predictions WHERE status = 'pending'"
        )
        now_ts = int(discord.utils.utcnow().timestamp())
        for sched_id, scheduled_ts in rows:
            run_ts = max(scheduled_ts, now_ts + 30)  # if past due, run shortly after startup
            run_at = datetime.fromtimestamp(run_ts, tz=timezone.utc)
            scheduler.add_job(
                post_scheduled_prediction,
                DateTrigger(run_date=run_at),
                args=[client, sched_id],
                id=f"scheduled_pred_{sched_id}",
                name=f"Scheduled Prediction #{sched_id}",
                misfire_grace_time=3600,
                replace_existing=True,
            )
        logger.info(f"Registered {len(rows)} pending scheduled predictions.")
    except Exception as e:
        logger.error(f"Failed to register pending scheduled predictions: {e}", exc_info=True)
        # The outer registration pass must remain incomplete so a reconnect can
        # retry. Every process/prediction job has a stable replacing ID, making
        # the full retry safe even when some jobs were added before this failure.
        raise

async def apply_inactivity_tax(client):
    try:
        from database import DatabaseManager
        import time
        import config
        
        now = int(time.time())
        limit = now - (60 * 24 * 60 * 60) # 60 days
        
        # Anyone holding UKP whose last chat was over 60 days ago - INCLUDING anyone with no
        # xp row at all. The old inner JOIN silently exempted never-chatted accounts forever
        # (152 of them, holding ~20k), which is exactly the dormant hoard this tax exists for.
        query = """
            SELECT u.user_id, u.balance
            FROM ukpence u
            LEFT JOIN xp x ON u.user_id = x.user_id
            WHERE u.balance > 0
              AND COALESCE(x.last_xp_time, 0) < ?
        """
        dormant_users = DatabaseManager.fetch_all(query, (limit,))
        # BOT_ID holds the bank's own float - never tax the bank into itself.
        from config import BOT_ID
        dormant_users = [(u, b) for u, b in (dormant_users or []) if str(u) != str(BOT_ID)]
        
        if not dormant_users:
            logger.info("[ECONOMY] No dormant users found for inactivity tax.")
            return

        rate = float(getattr(config, "INACTIVITY_TAX_RATE", 0.20))

        # Charge on effective wealth (balance + recently sent − recently received) so a dormant
        # hoarder can't escape by shoving their stash onto another account just before the run.
        # effective_wealth opens its own connections, so compute the plan BEFORE taking the lock;
        # the tax is clamped to the real balance so it never takes more than is there.
        from lib.economy.economy_manager import effective_wealth
        plans = []  # (uid, tax_amount)
        for uid, balance in dormant_users:
            tax_amount = min(int(effective_wealth(uid, balance) * rate), int(balance))
            if tax_amount > 0:
                plans.append((uid, tax_amount))

        charged = BankManager.collect_tax_batch(
            plans,
            description="Inactivity tax (60+ days dormant)",
            bank_description=f"Inactivity tax reclaimed from {len(plans)} users",
            record_public_log=False,
        )
        if charged is None:
            logger.error("[ECONOMY] Inactivity tax batch failed; no charges were committed.")
            return

        total_reclaimed = sum(amount for _, amount, _ in charged)
        taxed_count = len(charged)

        if total_reclaimed > 0:
            logger.info(f"[ECONOMY] Inactivity Tax reclaimed {total_reclaimed} UKP from {taxed_count} users.")
            
            # Update specific metric if needed
            current_date_str = datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d")
            _update_daily_metric_file(current_date_str, "inactivity_tax_total", total_reclaimed)
        else:
            logger.info("[ECONOMY] Inactivity tax run: no significant tax amounts to collect.")
            
    except Exception as e:
        logger.error(f"Error applying inactivity tax: {e}", exc_info=True)


async def apply_wealth_demurrage(client):
    """Weekly demurrage on hoarded wealth: charge a small % of the portion of each balance ABOVE
    the configured threshold, paid to the bank. Unlike the income wealth-tax this taxes the
    STOCK, so it can't be dodged by earning through untaxed channels (gambling/predictions) - a
    soft cap on hoarding. Supply is conserved (the charge goes to the bank).

    Shuffle-proof: the charge is based on effective wealth = current balance + UKP sent out in
    the window − UKP received (so money parked on an alt/split across accounts still counts as
    yours, and money just passing through isn't double-counted). It is NOT based on a peak
    balance: money genuinely lost gambling or spent in the shop lowers your balance for real, so
    it shouldn't be taxed - only money you still hold or have shuffled out should. The charge is
    clamped to the real balance, so it never drives anyone negative."""
    try:
        import config, time
        if not getattr(config, "WEALTH_DEMURRAGE_ENABLED", True):
            return
        threshold = int(getattr(config, "WEALTH_DEMURRAGE_THRESHOLD", 20000))
        rate = float(getattr(config, "WEALTH_DEMURRAGE_RATE", 0.01))
        window_days = int(getattr(config, "TRANSFER_LOOKBACK_DAYS", 7))
        if rate <= 0 or threshold < 0:
            return

        from database import DatabaseManager
        from config import BOT_ID
        from lib.economy.economy_manager import recent_transfer_io

        now = int(time.time())
        window_start = now - window_days * 86400

        # Candidates: anyone currently over the threshold, or anyone who SENT UKP in the window
        # (a possible splitter now sitting under it - their outflow still counts as theirs).
        # Union, then judge each by effective wealth. Every chargeable user is covered by one of
        # these two: to have effective wealth over the threshold you must either hold more than it
        # now, or have sent the difference out recently.
        cand = set()
        for src, params in (
            ("SELECT user_id FROM ukpence WHERE balance > ? AND user_id != ?", (threshold, str(BOT_ID))),
            ("SELECT DISTINCT payer_id FROM pay_transfers WHERE timestamp > ?", (window_start,)),
        ):
            for row in (DatabaseManager.fetch_all(src, params) or []):
                if row and row[0] is not None:
                    cand.add(str(row[0]))
        cand.discard(str(BOT_ID))
        if not cand:
            logger.info("[ECONOMY] Demurrage: no candidates above the %s threshold.", threshold)
            return

        # Phase 1 (no lock - these helpers open their own connections): work out what each
        # candidate owes from a read-only snapshot.
        plans = []  # (uid, amount)
        for uid in cand:
            bal_row = DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (uid,))
            bal = int(bal_row[0]) if bal_row and bal_row[0] is not None else 0
            if bal <= 0:
                continue
            # Effective wealth = what you hold + what you've shuffled out − what you've been sent.
            # No peak term: money lost/spent genuinely left, so it isn't taxed.
            inflow, outflow = recent_transfer_io(uid, window_days)
            effective = max(0, bal + outflow - inflow)
            amount = int((effective - threshold) * rate)
            if amount > 0:
                plans.append((uid, amount))
        if not plans:
            logger.info("[ECONOMY] Demurrage: nobody's effective excess rounded above 0.")
            return

        # Phase 2: re-read/clamp every balance, write each user ledger, and credit
        # the bank as tax in one all-or-nothing transaction.
        label = f"Wealth demurrage ({rate:.0%}/wk over {threshold:,})"
        charged = BankManager.collect_tax_batch(
            plans,
            description=label,
            bank_description=f"Weekly wealth demurrage from {len(plans)} users",
            record_public_log=False,
        )
        if charged is None:
            logger.error("[ECONOMY] Demurrage batch failed; no charges were committed.")
            return

        if not charged:
            logger.info("[ECONOMY] Demurrage: nobody's excess rounded above 0.")
            return

        total = sum(a for _, a, _ in charged)
        logger.info(f"[ECONOMY] Demurrage reclaimed {total} UKP from {len(charged)} users.")
        current_date_str = datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d")
        _update_daily_metric_file(current_date_str, "demurrage_total", total)

        # Earmark a share of the take for chat activity rewards - hoarded money reclaimed
        # from the richest players goes back out to the most active ones. The UKP already
        # sits in the bank; this only moves the accounting marker, so supply is untouched.
        from lib.economy.reserve_policy import fund_dividend_pot
        added = fund_dividend_pot(total)
        if added:
            _update_daily_metric_file(current_date_str, "dividend_pot_in", added)

        # Demurrage runs silently in the background - no DM / notification to users.
    except Exception as e:
        logger.error(f"Error applying wealth demurrage: {e}", exc_info=True)


async def apply_economy_dormant_tax(client):
    """Weekly charge on people who hold UKP but never USE the economy.

    The inactivity tax above keys off chat activity, so it only catches people who stopped
    talking. This catches the other half: still chatting (so exempt from that one), under the
    demurrage threshold (so exempt from that one too), but not having gambled, shopped, /paid,
    bonded or entered the lottery in ECONOMY_DORMANT_DAYS. They accumulate from the faucets
    and never return anything, which is most of why the bank drains.

    Charged only on the excess above ECONOMY_DORMANT_FLOOR, exactly like demurrage: the
    population is overwhelmingly small holders, and a flat percentage would take a fifth of
    a casual chatter's 200 UKP in order to reach the handful of accounts that matter.

    Silent - no DM, no notification, same as every other tax here.
    """
    try:
        import config, time
        if not getattr(config, "ECONOMY_DORMANT_TAX_ENABLED", True):
            return
        floor = int(getattr(config, "ECONOMY_DORMANT_FLOOR", 100))
        rate = float(getattr(config, "ECONOMY_DORMANT_RATE", 0.10))
        days = int(getattr(config, "ECONOMY_DORMANT_DAYS", 60))
        if rate <= 0 or days <= 0:
            return

        from database import DatabaseManager
        from config import BOT_ID
        cut = int(time.time()) - days * 86400

        # Last time each user actively SPENT or RISKED money. Passive receipts deliberately
        # don't count - being paid a chat reward is not using the economy, it's the thing
        # this tax exists to recycle. Each source is tried independently so a schema change
        # in one table degrades the aim rather than skipping the run.
        last_active = {}
        for sql in (
            "SELECT user_id, MAX(timestamp) FROM casino_results GROUP BY user_id",
            "SELECT user_id, MAX(purchase_time) FROM shop_purchases GROUP BY user_id",
            "SELECT payer_id, MAX(timestamp) FROM pay_transfers GROUP BY payer_id",
            "SELECT user_id, MAX(opened_ts) FROM bonds GROUP BY user_id",
            "SELECT winner_id, MAX(timestamp) FROM pvp_results GROUP BY winner_id",
            "SELECT loser_id, MAX(timestamp) FROM pvp_results GROUP BY loser_id",
            "SELECT winner_id, MAX(timestamp) FROM connect4_results GROUP BY winner_id",
            "SELECT winner_id, MAX(timestamp) FROM game_transfers GROUP BY winner_id",
            "SELECT loser_id, MAX(timestamp) FROM game_transfers GROUP BY loser_id",
            "SELECT e.user_id, MAX(r.created_at) FROM lottery_entries e "
            "JOIN lottery_rounds r ON r.id = e.round_id GROUP BY e.user_id",
        ):
            try:
                for uid, ts in (DatabaseManager.fetch_all(sql) or []):
                    if uid is None or ts is None:
                        continue
                    uid = str(uid)
                    if int(ts) > last_active.get(uid, 0):
                        last_active[uid] = int(ts)
            except Exception:
                logger.warning("[ECONOMY] Dormant-tax source unavailable, skipping it: %s",
                               sql.split(" FROM ")[-1].split(" ")[0], exc_info=True)

        holders = DatabaseManager.fetch_all(
            "SELECT user_id, balance FROM ukpence WHERE balance > ? AND user_id != ?",
            (floor, str(BOT_ID))) or []

        plans = []
        for uid, balance in holders:
            uid = str(uid)
            if last_active.get(uid, 0) >= cut:
                continue                                  # used the economy recently
            amount = int((int(balance) - floor) * rate)
            if amount > 0:
                plans.append((uid, amount))
        if not plans:
            logger.info("[ECONOMY] Economy-dormant tax: nobody chargeable this run.")
            return

        charged = BankManager.collect_tax_batch(
            plans,
            description=f"Dormant-account tax ({days}d without using the economy)",
            bank_description=f"Economy-dormant tax from {len(plans)} users",
            record_public_log=False,
        )
        if charged is None:
            logger.error("[ECONOMY] Economy-dormant tax batch failed; nothing was committed.")
            return
        total = sum(a for _, a, _ in charged)
        logger.info("[ECONOMY] Economy-dormant tax reclaimed %s UKP from %s users.",
                    total, len(charged))
        if total > 0:
            _update_daily_metric_file(
                datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d"),
                "economy_dormant_tax_total", total)
    except Exception as e:
        logger.error(f"Error applying economy-dormant tax: {e}", exc_info=True)


def _voice_spell(seconds):
    """How long they were in there, in the coarsest unit that still says something."""
    if seconds is None:
        return ""
    if seconds < 60:
        return f" after {seconds}s"
    if seconds < 3600:
        return f" after {seconds // 60}m"
    hours, minutes = divmod(seconds // 60, 60)
    return f" after {hours}h {minutes}m" if minutes else f" after {hours}h"


async def process_voice_activity_log(client):
    """Flush the buffered voice joins, leaves and moves into the log thread.

    Batched rather than posted per event: a busy call is hundreds of state updates, and one
    message each would bury the thread and run into the rate limit. The buffer is swapped
    out before anything is sent, so events arriving mid-send land in the next digest instead
    of being dropped.
    """
    try:
        import discord
        buf = getattr(client, "voice_activity_log", None)
        if not buf:
            return
        events, client.voice_activity_log = buf[:], []

        thread = client.get_channel(CHANNELS.VOICE_ACTIVITY_THREAD)
        if thread is None:
            try:
                # Threads fall out of the cache once auto-archived; fetching revives them.
                thread = await client.fetch_channel(CHANNELS.VOICE_ACTIVITY_THREAD)
            except Exception:
                logger.warning("[VOICE] activity thread unreachable; dropping %s events", len(events))
                return

        def _where(event, side):
            """Channel name, or its id when the channel was not in the cache."""
            name = event.get(side)
            if name:
                return f"**{discord.utils.escape_markdown(name)}**"
            cid = event.get(f"{side}_id")
            return f"**<#{cid}>**" if cid else "**an unknown channel**"

        lines = []
        for e in events:
            when, who = f"<t:{e['at']}:T>", f"**{discord.utils.escape_markdown(e['who'])}**"
            if e["kind"] == "join":
                lines.append(f"{when} 🟢 {who} joined {_where(e, 'to')}")
            elif e["kind"] == "leave":
                lines.append(f"{when} 🔴 {who} left {_where(e, 'from')}{_voice_spell(e['held'])}")
            else:
                lines.append(f"{when} 🔁 {who} moved {_where(e, 'from')} → {_where(e, 'to')}")

        # Embed descriptions cap at 4096, so a long stretch goes out as several.
        chunk, size = [], 0
        for line in lines + [None]:
            if line is None or size + len(line) + 1 > 3900:
                if chunk:
                    await thread.send(embed=discord.Embed(
                        title="🎧 Voice Activity",
                        description="\n".join(chunk),
                        color=0x5865F2,
                    ))
                chunk, size = [], 0
            if line is not None:
                chunk.append(line)
                size += len(line) + 1
    except Exception as e:
        logger.error(f"Error posting voice activity log: {e}", exc_info=True)


async def process_economy_logs(client):
    try:
        from database import DatabaseManager
        import discord
        logs = DatabaseManager.fetch_all("SELECT id, timestamp, log_text FROM economy_transactions ORDER BY id ASC LIMIT 10")
        if not logs:
            return
            
        async def _thread(channel_id):
            # Threads fall out of the cache once auto-archived; fetching revives them.
            channel = client.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await client.fetch_channel(channel_id)
                except Exception:
                    return None
            return channel

        economy_log_thread = await _thread(CHANNELS.ECONOMY_LOG_THREAD)
        passive_rewards_thread = await _thread(CHANNELS.PASSIVE_CHAT_REWARDS_THREAD)

        usage_embed = discord.Embed(title="💰 Economy Activity", color=0x2ecc71)
        economy_embed = discord.Embed(title="📈 Chat Activity Rewards", color=0x3498db)
        
        usage_count = 0
        economy_count = 0
        ids_to_delete = []
        
        for log_id, timestamp, text in logs:
            # Parse log_text format: "emoji description|reason"
            if "|" in text:
                description_part, reason_part = text.rsplit("|", 1)
            else:
                description_part = text
                reason_part = "Unspecified"
            
            reason_stripped = reason_part.strip()
            field_name = f"<t:{timestamp}:T> - {reason_stripped}"
            field_value = description_part.strip()
            
            if reason_stripped == "Chatting activity reward" and passive_rewards_thread:
                economy_embed.add_field(name=field_name, value=field_value, inline=False)
                economy_count += 1
            elif economy_log_thread:
                usage_embed.add_field(name=field_name, value=field_value, inline=False)
                usage_count += 1
                
            ids_to_delete.append(log_id)
        
        if ids_to_delete:
            if usage_count > 0 and economy_log_thread:
                await economy_log_thread.send(embed=usage_embed)
            if economy_count > 0 and passive_rewards_thread:
                await passive_rewards_thread.send(embed=economy_embed)
                
            placeholders = ",".join(["?"] * len(ids_to_delete))
            DatabaseManager.execute(f"DELETE FROM economy_transactions WHERE id IN ({placeholders})", tuple(ids_to_delete))
            
    except Exception as e:
        logger.error(f"Error processing economy logs queue: {e}")
