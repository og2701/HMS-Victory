import discord
from discord.ext import tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import asyncio
import signal
import logging
import pytz
from datetime import datetime, timedelta
import shutil
import os
import zipfile
import io
import json
import aiohttp
from typing import Optional

from lib.bot.event_handlers import *
from lib.features.on_message_functions import *
from lib.bot.setup_commands import define_commands
from config import *
from lib.features.summary import initialize_summary_data, update_summary_data, post_summary
from lib.economy.prediction_system import Prediction, _load as load_predictions, _save as save_predictions
from lib.economy.economy_manager import add_bb, get_all_balances as load_ukpence_data
from lib.economy.economy_stats_html import create_economy_stats_image
from database import init_db
from lib.core.americanisms import correct_americanisms
from lib.core.webhook_utils import send_as_webhook
from lib.core.file_operations import load_webhook_deletions, save_webhook_deletions

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_PART_SIZE = 8 * 1024 * 1024


from lib.bot.backup_manager import restore_database_if_missing, restore_json_if_missing


class AClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.guilds = True
        super().__init__(intents=intents)
        self.synced = False
        self.maintenance_mode = False  # set True during graceful shutdown to refuse new coin-deducting actions
        self._shutting_down = False    # re-entrancy guard for graceful_shutdown
        self._prediction_backfill_done = False  # on_ready button-backfill runs once per process
        self.scheduler = AsyncIOScheduler()
        self.image_cache = {}
        self.stage_events=set()
        self.stage_join_times={}
        self.reply_chains = {} # (channel_id, user_id) -> consecutive reply count
        self.last_reply_user = {} # channel_id -> user_id of the last reply in that channel
        self.message_repliers = {} # message_id -> set(user_ids)
        self.predictions={int(k):Prediction.from_dict(v) for k,v in load_predictions().items()}
        self._pending_uploads = {}  # For custom emoji/sticker uploads
        self.session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        import config
        # Persistent 'Ask a follow-up' buttons on moderation analysis reports.
        try:
            from commands.moderation.user_analysis import FollowupButton
            self.add_dynamic_items(FollowupButton)
        except Exception as e:
            logger.warning(f"Could not register Analyse User follow-up button: {e}")
        from lib.economy.prediction_system import BetButtons, build_prediction_layout
        for p in self.predictions.values():
            if not p.locked:
                if getattr(config, "PREDICTION_CV2_ENABLED", False):
                    view, _ = build_prediction_layout(p, self, interactive=True)
                    self.add_view(view, message_id=p.msg_id)
                else:
                    self.add_view(BetButtons(p), message_id=p.msg_id)
        
        # Load persistent iceberg approval views
        from database import DatabaseManager
        from lib.economy.shop_items import IcebergApprovalView
        try:
            rows = DatabaseManager.fetch_all("SELECT id FROM pending_iceberg_submissions WHERE status = 'pending'")
            for row in rows:
                self.add_view(IcebergApprovalView(row[0]))
            logger.info(f"Registered {len(rows)} persistent iceberg approval views.")
        except Exception as e:
            logger.warning(f"Could not load persistent iceberg views: {e}")

        # Load persistent custom rank-background approval views
        from lib.economy.shop_items import RankBgApprovalView
        try:
            bg_rows = DatabaseManager.fetch_all("SELECT id FROM pending_rank_background_submissions WHERE status = 'pending'")
            for row in bg_rows:
                self.add_view(RankBgApprovalView(row[0]))
            logger.info(f"Registered {len(bg_rows)} persistent rank-background approval views.")
        except Exception as e:
            logger.warning(f"Could not load persistent rank-background views: {e}")

        # Load persistent scheduled-prediction cancel views
        from lib.economy.prediction_system import CancelScheduledPredView
        try:
            sched_rows = DatabaseManager.fetch_all(
                "SELECT id, cm_message_id FROM scheduled_predictions WHERE status = 'pending' AND cm_message_id IS NOT NULL"
            )
            for sched_id, cm_msg_id in sched_rows:
                self.add_view(CancelScheduledPredView(sched_id), message_id=int(cm_msg_id))
            logger.info(f"Registered {len(sched_rows)} persistent scheduled-pred cancel views.")
        except Exception as e:
            logger.warning(f"Could not load persistent scheduled-pred views: {e}")

        # Global persistent casino lobby view so /casino menu buttons survive restarts.
        try:
            from commands.economy.casino import build_casino_menu, CasinoLeaderboardView
            self.add_view(build_casino_menu())
            self.add_view(CasinoLeaderboardView())  # leaderboard dropdown (public, persistent)
            logger.info("Registered persistent casino lobby + leaderboard views.")
        except Exception as e:
            logger.warning(f"Could not register casino lobby view: {e}")

        # Reattach the open lottery board's buttons so Buy/My-Tickets survive restarts.
        try:
            from lib.economy.lottery import get_open_round, build_board_controls
            rnd = get_open_round()
            if rnd and rnd.get("message_id"):
                self.add_view(build_board_controls(rnd), message_id=int(rnd["message_id"]))
                logger.info("Registered persistent lottery board view.")
        except Exception as e:
            logger.warning(f"Could not register lottery board view: {e}")

        logger.info("Persistent prediction views registered in setup_hook.")

    async def on_ready(self):
        await on_ready(self, tree, self.scheduler)
        # The two loops below are a one-off cosmetic backfill of buttons onto
        # already-posted prediction / scheduled-pred messages. on_ready fires on
        # every gateway reconnect, so gate it to run once per process (mirrors the
        # self.synced convention) - otherwise every reconnect re-edits every card.
        # Flag is set BEFORE the loops so a reconnect mid-sleep can't start a 2nd pass.
        if self._prediction_backfill_done:
            return
        self._prediction_backfill_done = True
        # Proactively update existing predictions to show the new button
        import asyncio
        from lib.economy.prediction_system import _edit_prediction_message
        for p in list(self.predictions.values()):
            if not p.locked:
                try:
                    channel = None
                    if p.channel_id:
                        channel = self.get_channel(p.channel_id) or await self.fetch_channel(p.channel_id)
                    else:
                        # Fallback: try to find it in the Polls channel or other likely places
                        # For now, let's just try CHANNELS.POLLS if it exists in config
                        if hasattr(CHANNELS, 'POLLS'):
                            channel = self.get_channel(CHANNELS.POLLS) or await self.fetch_channel(CHANNELS.POLLS)
                    
                    if not channel:
                        continue

                    msg = await channel.fetch_message(p.msg_id)
                    # Update channel_id if it was missing
                    if not p.channel_id:
                        p.channel_id = msg.channel.id
                        from lib.economy.prediction_system import _save as _save_preds
                        _save_preds({k: v.to_dict() for k, v in self.predictions.items()})

                    await _edit_prediction_message(msg, p, self, interactive=True)
                    logger.info(f"Updated live prediction {p.msg_id} with new view.")
                    await asyncio.sleep(1) # Small delay to be polite to the API
                except Exception as e:
                    logger.warning(f"Could not update prediction {p.msg_id}: {e}")

        # (Removed the on-ready scheduled-prediction button backfill: it re-edited every
        # pending CM announcement on each restart, 429-storming the API and stalling the
        # event loop. The cancel buttons already work via the persistent views registered
        # in __init__, so the backfill was redundant.)

        # Open the first-ever lottery round (no-op once one exists; the weekly job opens
        # subsequent rounds). Safe across reconnects - it only acts if none has existed.
        try:
            from lib.economy.lottery import ensure_started
            await ensure_started(self)
        except Exception as e:
            logger.warning(f"Could not start lottery: {e}")

    async def on_message(self, message):
        if (
            message.author.id == USERS.COUNTRYBALL_BOT
            and "A wild countryball" in message.content
        ):
            channel = client.get_channel(CHANNELS.BOT_SPAM)
            if channel:
                await channel.send(
                    f"<@&{ROLES.BALL_INSPECTOR}> A wild countryball appeared!"
                )
            return

        if message.author.id == 557628352828014614 and message.embeds:
            handle_ticket_closed_message(self, message)
            return

        # Grow-a-Tree watering reward (the tree bot is a bot, so handle before the filter).
        if message.channel.id == TREE_CHANNEL_ID and message.author.id == GROW_A_TREE_BOT_ID:
            from lib.features.ukp_rewards import handle_tree_watering
            asyncio.create_task(handle_tree_watering(self, message))
            return

        if message.author.bot:
            return

        # Rolling archive so bulk deletes (ban purges / mod sweeps) can be logged later.
        from lib.features.message_archive import archive_message
        archive_message(message)

        if message.type == discord.MessageType.auto_moderation_action:
            target_user_id_str = None
            if message.embeds:
                embed = message.embeds[0]
                for field in embed.fields:
                    if field.name.lower() == "user":
                        import re
                        match = re.search(r"<@!?(\d+)>", field.value)
                        if match:
                            target_user_id_str = match.group(1)
                        break

            target_user = None
            if target_user_id_str:
                try:
                    target_user = await self.fetch_user(int(target_user_id_str))
                except (discord.NotFound, ValueError):
                    logger.warning(f"Could not find user for automod DM: {target_user_id_str}")

            if target_user:
                member = message.guild.get_member(target_user.id)
                if member and any(role.id == ROLES.DONT_DM_WHEN_MESSAGE_BLOCKED for role in member.roles):
                    return

                rule_name = embed.fields[0].value
                channel_mention = embed.fields[1].value
                bad_word = embed.fields[4].value

                button = discord.ui.Button(
                        custom_id = f"role_{ROLES.DONT_DM_WHEN_MESSAGE_BLOCKED}",
                        label = "Toggle DMs when a message is blocked",
                        style = discord.ButtonStyle.primary
                    )

                view = discord.ui.View(timeout=None)
                view.add_item(button)

                try:
                    await target_user.send(
                        f"Your message in {channel_mention} was blocked due to it triggering **{rule_name}** filter. The flagged word/phrase was ||{bad_word}||.",
                        view=view
                    )
                except discord.Forbidden:
                    logger.warning(f"Cannot DM user {target_user.id} (automod notification).")
            return

        if await handle_hate_speech_message(self, message):
            return

        initialize_summary_data()
        update_summary_data("messages", channel_id=message.channel.id)
        update_summary_data("active_members", user_id=message.author.id)

        # Holiday badges
        now = datetime.now()
        from lib.bot.event_handlers import award_badge_with_notify
        if now.month == 12 and now.day == 25:
            await award_badge_with_notify(self, message.author.id, 'christmas')
        elif now.month == 10 and now.day == 31:
            await award_badge_with_notify(self, message.author.id, 'halloween')

        # Reply logic (Chain and Popular)
        ch_id = message.channel.id
        if message.reference and message.reference.message_id:
            try:
                referenced_msg = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
                if referenced_msg and referenced_msg.author.id != message.author.id:
                    # 1. Reply Chain (A -> B -> A -> B), tracked PER CHANNEL so
                    #    interleaved replies in unrelated channels can't spuriously
                    #    advance or reset someone's chain.
                    chain_key = (ch_id, message.author.id)
                    if self.last_reply_user.get(ch_id) == referenced_msg.author.id:
                        self.reply_chains[chain_key] = self.reply_chains.get(chain_key, 0) + 1
                        if self.reply_chains[chain_key] >= 3:
                            await award_badge_with_notify(self, message.author.id, 'reply_chain')
                            # Reset chain for this user after awarding to prevent spam
                            self.reply_chains[chain_key] = 0
                    else:
                        self.reply_chains[chain_key] = 1
                    self.last_reply_user[ch_id] = message.author.id

                    # 2. Popular Badge (3 people reply to one message)
                    ref_id = referenced_msg.id
                    if ref_id not in self.message_repliers:
                        self.message_repliers[ref_id] = set()
                    self.message_repliers[ref_id].add(message.author.id)
                    if len(self.message_repliers[ref_id]) >= 3:
                        await award_badge_with_notify(self, referenced_msg.author.id, 'triple_reply')
                        # Clean up to prevent multi-award
                        del self.message_repliers[ref_id]
            except Exception:
                pass
        else:
            # Not a reply, so break the active chain in THIS channel only
            self.last_reply_user.pop(ch_id, None)
        
        # Cleanup old reply tracking dictionaries to prevent memory leaks
        if len(self.message_repliers) > 1000:
            # Simple LRU-ish cleanup: remove oldest 200 items
            keys_to_del = list(self.message_repliers.keys())[:200]
            for k in keys_to_del:
                del self.message_repliers[k]
                
        if len(self.reply_chains) > 1000:
            keys_to_del = list(self.reply_chains.keys())[:200]
            for k in keys_to_del:
                del self.reply_chains[k]

        await on_message(self, message)

    async def on_automod_action(self, payload: discord.AutoModAction):
        """
        Handles automod actions, specifically for Americanism correction.
        """
        guild = self.get_guild(payload.guild_id)
        if not guild:
            return

        # Specifically target the Americanism Block rule by ID
        if payload.rule_id == 1465347564978311242:
            # Only respond to the block_message action to avoid duplicates if 
            # there are multiple actions (e.g. block and alert)
            if payload.action.type != discord.AutoModRuleActionType.block_message:
                return

            channel = guild.get_channel(payload.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            
            member = guild.get_member(payload.user_id)
            if not member:
                try:
                    member = await guild.fetch_member(payload.user_id)
                except discord.HTTPException:
                    return

            if not payload.content:
                return

            # Check if the user is timed out to prevent bypass
            if member.is_timed_out():
                return

            corrected_content = correct_americanisms(payload.content)
            
            # If nothing changed, don't send anything (shouldn't happen if rule triggered correctly)
            if corrected_content == payload.content:
                return

            from lib.bot.event_handlers import award_badge_with_notify
            await award_badge_with_notify(self, member.id, 'americanism_victim')

            # Security: Prevent server invites from being sent via webhook
            invite_patterns = [r"discord\.gg/\S+", r"discord\.com/invite/\S+"]
            import re
            if any(re.search(pattern, corrected_content.lower()) for pattern in invite_patterns):
                logger.info(f"Blocked invite link in corrected Americanism from {member.display_name}")
                return

            webhook_msg = await send_as_webhook(channel, member, corrected_content)
            if webhook_msg:
                # Add a reaction so the user can delete it
                await webhook_msg.add_reaction("❌")
                
                # Store the deletion mapping
                deletions = load_webhook_deletions()
                deletions[str(webhook_msg.id)] = {
                    "user_id": payload.user_id,
                    "channel_id": payload.channel_id,
                    "timestamp": discord.utils.utcnow().timestamp()
                }
                save_webhook_deletions(deletions)

            logger.info(f"[PID {os.getpid()}] Corrected Americanism for {member.display_name} in {channel.name}")

    async def on_interaction(self, interaction):
        await on_interaction(interaction)

    async def on_member_update(self, before, after):
        from lib.bot.event_handlers import on_member_update
        await on_member_update(before, after)

    async def on_member_join(self, member):
        initialize_summary_data()
        update_summary_data("members_joined")
        await on_member_join(member)

    async def on_member_remove(self, member):
        initialize_summary_data()
        update_summary_data("members_left")
        await on_member_remove(member)

    async def on_member_ban(self, guild, user):
        initialize_summary_data()
        update_summary_data("members_banned")
        await on_member_ban(guild, user)

    async def on_message_delete(self, message):
        if message.author.bot:
            return
        initialize_summary_data()
        update_summary_data("deleted_messages")
        await on_message_delete(self, message)

    async def on_raw_bulk_message_delete(self, payload):
        # Bulk deletes (ban purges, mod/bot sweeps) never reach on_message_delete and the
        # cache rarely still holds them - recover content from the message archive instead.
        try:
            initialize_summary_data()
            for _ in payload.message_ids:
                update_summary_data("deleted_messages")
        except Exception:
            pass
        try:
            from lib.features.message_archive import handle_raw_bulk_delete
            await handle_raw_bulk_delete(self, payload)
        except Exception:
            logger.error("bulk delete logging failed", exc_info=True)

    async def on_raw_message_delete(self, payload):
        # Uncached single deletes (the cached path is handled by on_message_delete).
        try:
            from lib.features.message_archive import handle_raw_single_delete
            await handle_raw_single_delete(self, payload)
        except Exception:
            logger.debug("raw single delete logging failed", exc_info=True)

    async def on_message_edit(self, before, after):
        await on_message_edit(self, before, after)

    async def on_raw_message_edit(self, payload):
        # Grow-a-Tree usually EDITS the tree message in place when someone waters (instead of
        # posting a new one), and that old message is often out of the cache - so use the RAW
        # edit event (fires regardless of cache) and fetch the current message. Dedup is by
        # tree height inside the handler, so this can't double-pay vs the on_message path.
        try:
            if payload.channel_id != TREE_CHANNEL_ID:
                return
            author = ((payload.data or {}).get("author") or {}).get("id")
            if str(author) != str(GROW_A_TREE_BOT_ID):
                return
            channel = self.get_channel(TREE_CHANNEL_ID) or await self.fetch_channel(TREE_CHANNEL_ID)
            msg = await channel.fetch_message(payload.message_id)
            from lib.features.ukp_rewards import handle_tree_watering
            asyncio.create_task(handle_tree_watering(self, msg))
        except Exception:
            logger.debug("tree raw-edit handler failed", exc_info=True)

    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
        initialize_summary_data()
        update_summary_data("reactions_added")
        update_summary_data("reacting_members", user_id=user.id)
        await on_reaction_add(reaction, user)

    async def on_raw_reaction_add(self, payload):
        if payload.member and payload.member.bot:
            return
        await on_raw_reaction_add(self, payload)

    async def on_reaction_remove(self, reaction, user):
        if user.bot:
            return
        initialize_summary_data()
        update_summary_data("reactions_removed")
        update_summary_data("reacting_members", user_id=user.id, remove=True)
        await on_reaction_remove(reaction, user)

    async def on_voice_state_update(self, member, before, after):
        await on_voice_state_update(member, before, after)

    async def on_stage_instance_create(self, stage_instance):
        await on_stage_instance_create(stage_instance)

    async def on_stage_instance_delete(self, stage_instance):
        await on_stage_instance_delete(stage_instance)

    async def clear_image_cache(self):
        self.image_cache.clear()
        logger.info("Image cache cleared.")


client = AClient()
tree = discord.app_commands.CommandTree(client)

define_commands(tree, client)

async def graceful_shutdown(client, sig_name):
    """Drain cleanly on SIGTERM/SIGINT (systemctl restart, ./update_bot.sh, Ctrl-C).

    In-flight games already survive restarts via persistent_views.json (predictions,
    accepted wagers, in-play blackjack hands all reattach on boot), so this sequence
    is about a *tidy* exit: stop scheduled work, close our HTTP session, flush the
    SQLite WAL into the .db file, then log the bot out of the gateway so it shows
    offline immediately instead of timing out.
    """
    if client._shutting_down:
        return
    client._shutting_down = True
    logger.info(f"Received {sig_name}; starting graceful shutdown.")

    # 1. Maintenance mode: refuse new coin-deducting commands during the drain.
    client.maintenance_mode = True

    # 2. In-memory games (poker sessions, roulette rounds) do NOT reattach on boot, so a
    #    restart would silently destroy them. Resolve them cleanly up front: spin any open
    #    roulette betting round now so its already-debited bets pay out instead of vanishing.
    try:
        from commands.economy.roulette import drain_for_shutdown as _drain_roulette
        await _drain_roulette()
    except Exception as e:
        logger.error(f"Roulette shutdown drain failed: {e}", exc_info=True)

    # Wait (up to 2 min) for active games to wind down before we tear down. Under maintenance
    # poker deals no new hands and auto-closes finished sessions; persistent-view games
    # (blackjack/HL/video poker/red dog/TCP) reattach on boot regardless.
    def _count_active_games():
        n = 0
        try:
            from lib.core.file_operations import load_persistent_views
            views = load_persistent_views()
            n += sum(1 for v in views.values()
                     if isinstance(v, dict) and v.get("type") in
                     ("blackjack", "higherlower", "videopoker", "reddog", "tcp"))
        except Exception:
            pass
        try:  # poker tables mid-hand or between hands (a live session still winding down)
            from commands.economy.poker import _TABLES as _pt
            n += sum(1 for t in _pt.values()
                     if getattr(t, "status", None) in ("playing", "between")
                     and not getattr(t, "closed", False))
        except Exception:
            pass
        try:  # roulette tables with a live round (betting or spinning)
            from commands.economy.roulette import _TABLES as _rt
            n += sum(1 for t in _rt.values() if getattr(t, "status", None) in ("betting", "spinning"))
        except Exception:
            pass
        try:  # single-player casino clicks mid-redraw — wait so the result lands
            from lib.economy.casino_drain import in_flight_actions
            n += in_flight_actions()
        except Exception:
            pass
        return n

    try:
        wait_seconds, max_wait = 0, 120  # up to 2 minutes
        while wait_seconds < max_wait:
            active = _count_active_games()
            if active == 0:
                logger.info("All active games finished; proceeding with shutdown.")
                break
            logger.info(f"Waiting for {active} active game(s) to finish... ({wait_seconds}/{max_wait}s)")
            await asyncio.sleep(2)
            wait_seconds += 2
        else:
            logger.warning(f"Reached max wait ({max_wait}s) for active games; proceeding with shutdown.")
    except Exception as e:
        logger.error(f"Error waiting for active games: {e}")

    # 3. Final sweep: cash out any poker table still open (a lobby holding a lone winner's
    #    stack, or a session that ran past the wait cap) so a restart never strands chips.
    try:
        from commands.economy.poker import drain_for_shutdown as _drain_poker
        await _drain_poker(max_wait=0)
    except Exception as e:
        logger.error(f"Poker shutdown drain failed: {e}", exc_info=True)

    # 2. Stop scheduled jobs so nothing new writes to the DB while we checkpoint.
    #    wait=False: never block the event loop waiting on a job that needs it.
    try:
        if getattr(client, "scheduler", None) and client.scheduler.running:
            client.scheduler.shutdown(wait=False)
            logger.info("APScheduler stopped.")
    except Exception as e:
        logger.error(f"Scheduler shutdown error: {e}")

    # 3. Close our aiohttp session (avoids 'Unclosed client session' warnings).
    try:
        if client.session and not client.session.closed:
            await client.session.close()
            logger.info("aiohttp session closed.")
    except Exception as e:
        logger.error(f"Session close error: {e}")

    # 4. Flush the WAL into database.db so the file is self-contained for backups.
    try:
        from database import DatabaseManager
        DatabaseManager.shutdown_checkpoint()
    except Exception as e:
        logger.error(f"WAL checkpoint error: {e}")

    # 5. Log out of the Discord gateway (returns from client.start()).
    try:
        await client.close()
        logger.info("Discord gateway closed cleanly.")
    except Exception as e:
        logger.error(f"Gateway close error: {e}")


async def main():
    async with client:
        # Intercept systemd's SIGTERM and a terminal's SIGINT so we drain via
        # graceful_shutdown instead of being killed mid-write. add_signal_handler
        # is the asyncio-safe path (works on Linux/macOS; absent on Windows).
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(graceful_shutdown(client, s.name)),
                )
            except (NotImplementedError, RuntimeError):
                pass  # platform without add_signal_handler support

        await restore_database_if_missing()
        await restore_json_if_missing()
        init_db()
        await client.start(os.getenv("DISCORD_TOKEN"))
