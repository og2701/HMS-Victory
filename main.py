import discord
from discord.ext import tasks
from apscheduler.schedulers.asyncio import AsyncIOScheduler
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
        self.scheduler = AsyncIOScheduler()
        self.image_cache = {}
        self.stage_events=set()
        self.stage_join_times={}
        self.reply_chains = {} # user_id -> count
        self.last_reply_user = None
        self.message_repliers = {} # message_id -> set(user_ids)
        self.predictions={int(k):Prediction.from_dict(v) for k,v in load_predictions().items()}
        self._pending_uploads = {}  # For custom emoji/sticker uploads
        self.session: Optional[aiohttp.ClientSession] = None

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        from lib.economy.prediction_system import BetButtons
        for p in self.predictions.values():
            if not p.locked:
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

        logger.info("Persistent prediction views registered in setup_hook.")

    async def on_ready(self):
        await on_ready(self, tree, self.scheduler)
        # Proactively update existing predictions to show the new button
        import asyncio
        from lib.economy.prediction_system import BetButtons, prediction_embed
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

                    embed, bar = prediction_embed(p, self)
                    await msg.edit(embed=embed, attachments=[bar], view=BetButtons(p))
                    logger.info(f"Updated live prediction {p.msg_id} with new view.")
                    await asyncio.sleep(1) # Small delay to be polite to the API
                except Exception as e:
                    logger.warning(f"Could not update prediction {p.msg_id}: {e}")

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
            await handle_ticket_closed_message(self, message)
            return

        if message.author.bot:
            return

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
        if message.reference and message.reference.message_id:
            try:
                referenced_msg = message.reference.cached_message or await message.channel.fetch_message(message.reference.message_id)
                if referenced_msg and referenced_msg.author.id != message.author.id:
                    # 1. Reply Chain (A -> B -> A -> B)
                    if self.last_reply_user == referenced_msg.author.id:
                        self.reply_chains[message.author.id] = self.reply_chains.get(message.author.id, 0) + 1
                        if self.reply_chains[message.author.id] >= 3:
                            await award_badge_with_notify(self, message.author.id, 'reply_chain')
                            # Reset chain for this user after awarding to prevent spam
                            self.reply_chains[message.author.id] = 0
                    else:
                        self.reply_chains[message.author.id] = 1
                    self.last_reply_user = message.author.id

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
            # Not a reply, so break the active chain
            self.last_reply_user = None
        
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

    async def on_message_edit(self, before, after):
        await on_message_edit(self, before, after)

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

async def main():
    async with client:
        await restore_database_if_missing()
        await restore_json_if_missing()
        init_db()
        await client.start(os.getenv("DISCORD_TOKEN"))
