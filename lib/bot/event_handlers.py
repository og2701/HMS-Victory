import discord
from discord import Interaction, InteractionType
from datetime import timedelta, datetime
import logging, os, aiohttp, io, json, asyncio, pytz, time, re
from collections import defaultdict

from lib.core.translation import translate_and_send
from lib.features.summary import initialize_summary_data, update_summary_data, post_summary
from lib.core.utils import post_summary_helper, generate_rank_card
from lib.core.discord_helpers import has_role, has_any_role, send_embed_to_channels, edit_voice_channel_members, fetch_messages_with_context, estimate_tokens
from lib.core.file_operations import load_persistent_views, save_persistent_views, load_json_file, save_json_file, set_file_status, is_file_status_active, load_webhook_deletions, save_webhook_deletions
from lib.core.utils import is_lockdown_active
from lib.core.image_processing import trim_image, find_non_overlapping_position, random_color_excluding_blue_and_dark
from lib.core.log_functions import create_message_image, create_edited_message_image
from lib.core.moderation_text import ModerationMatch, find_blocked_moderation_match
from config import *
from database import DatabaseManager, award_badge
from lib.core.constants import FLAG_LANGUAGE_MAPPINGS, TRANSLATION_BLACKLIST_CHANNELS
from lib.economy.economy_manager import can_use_shutcoin, remove_shutcoin, SHUTCOIN_ENABLED
from lib.economy.prediction_system import prediction_embed, _save
from lib.economy.economy_manager import add_bb, remove_bb, ensure_bb, get_bb, get_all_balances as load_ukpence_data
from lib.economy.bank_manager import BankManager
from lib.economy.prediction_system import prediction_embed, _save, _load, Prediction, BetButtons

from commands.moderation.persistant_role_buttons import handleRoleButtonInteraction
from commands.moderation.anti_raid import handle_new_member_anti_raid
from commands.moderation.archive_channel import ArchiveButtonView, schedule_archive_move
from commands.moderation.overnight_mute import mute_visitors, unmute_visitors

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


def _moderation_embed_value(value: str, limit: int = 1000) -> str:
    value = discord.utils.escape_mentions(value or "")
    value = value.replace("`", "'").strip()
    if not value:
        return "_(empty)_"
    if len(value) > limit:
        return value[: limit - 3].rstrip() + "..."
    return value


async def _get_channel(client, channel_id: int):
    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await client.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden):
        return None


async def _report_hate_speech_timeout(
    client,
    message,
    match: ModerationMatch,
    timeout_status: str,
    delete_status: str,
) -> None:
    police_channel = await _get_channel(client, CHANNELS.POLICE_STATION)
    if police_channel is None:
        logger.warning("Police Station channel not found for hate-speech moderation report.")
        return

    embed = discord.Embed(
        title="Automated hate-speech timeout",
        description=(
            f"{message.author.mention} (`{message.author.id}`) triggered the "
            f"normalized moderation filter in {message.channel.mention}."
        ),
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Matched", value=match.label, inline=True)
    embed.add_field(name="Timeout", value=timeout_status, inline=True)
    embed.add_field(name="Message deletion", value=delete_status, inline=True)
    embed.add_field(name="Original message", value=_moderation_embed_value(message.content), inline=False)
    embed.add_field(name="Normalized message", value=_moderation_embed_value(match.normalized_text), inline=False)
    embed.add_field(name="Message link", value=f"[Jump to message]({message.jump_url})", inline=False)

    await police_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def handle_hate_speech_message(client, message) -> bool:
    if message.author.bot or message.guild is None or not message.content:
        return False

    match = find_blocked_moderation_match(message.content)
    if match is None:
        return False

    member = message.author
    if not isinstance(member, discord.Member):
        member = message.guild.get_member(message.author.id)
        if member is None:
            try:
                member = await message.guild.fetch_member(message.author.id)
            except discord.HTTPException:
                member = None

    _record_mute_trigger(client, message.author.id, message)

    delete_status = "not attempted"
    try:
        await message.delete()
        delete_status = "deleted"
    except discord.NotFound:
        delete_status = "already deleted"
    except discord.Forbidden:
        delete_status = "failed: missing permission"
    except discord.HTTPException as e:
        delete_status = f"failed: {e.__class__.__name__}"

    timeout_status = "failed: member not found"
    if member is not None:
        until = discord.utils.utcnow() + timedelta(minutes=HATE_SPEECH_TIMEOUT_MINUTES)
        try:
            await member.timeout(until, reason=f"Automated hate-speech filter: {match.label}")
            timeout_status = f"{HATE_SPEECH_TIMEOUT_MINUTES // 60}h"
        except discord.Forbidden:
            timeout_status = "failed: missing permission or role hierarchy"
        except discord.HTTPException as e:
            timeout_status = f"failed: {e.__class__.__name__}"

    await _report_hate_speech_timeout(client, message, match, timeout_status, delete_status)
    logger.info(
        "Automated hate-speech moderation for user %s in channel %s: %s, %s",
        message.author.id,
        message.channel.id,
        timeout_status,
        delete_status,
    )
    return True

async def on_member_update(before, after):
    if not before.premium_since and after.premium_since:
        await award_badge_with_notify(after._state._get_client(), after.id, 'server_booster')

    if after.premium_since:
        days_boosting = (discord.utils.utcnow() - after.premium_since).days
        if days_boosting >= 365:
            await award_badge_with_notify(after._state._get_client(), after.id, 'yearly_booster')

    if before.timed_out_until != after.timed_out_until and after.timed_out_until and after.timed_out_until > discord.utils.utcnow():
        asyncio.create_task(notify_mute(after._state._get_client(), after))


def _summarise_message(message) -> str:
    """Turn a discord.Message into a short preview string (content + attachment/sticker hints)."""
    parts = []
    content = (message.content or "").strip()
    if content:
        if len(content) > 500:
            content = content[:500].rstrip() + "…"
        parts.append(content)
    extras = []
    if getattr(message, "attachments", None):
        extras.append(f"{len(message.attachments)} attachment(s)")
    if getattr(message, "stickers", None):
        extras.append(f"{len(message.stickers)} sticker(s)")
    if getattr(message, "embeds", None):
        extras.append(f"{len(message.embeds)} embed(s)")
    if extras:
        parts.append(f"_({', '.join(extras)})_")
    return "\n".join(parts) or "_(no text content)_"


def _record_mute_trigger(client, user_id: int, message) -> None:
    """Stash the message that triggered a bot-issued mute so notify_mute can include it."""
    store = getattr(client, "_mute_trigger_messages", None)
    if store is None:
        store = {}
        client._mute_trigger_messages = store
    store[user_id] = (message.jump_url, _summarise_message(message), time.time())


def _consume_mute_trigger(client, user_id: int) -> tuple[str, str] | None:
    store = getattr(client, "_mute_trigger_messages", None)
    if not store:
        return None
    entry = store.pop(user_id, None)
    now = time.time()
    for uid in [k for k, v in store.items() if now - v[2] > 60]:
        store.pop(uid, None)
    if not entry:
        return None
    url, preview, ts = entry
    if now - ts > 60:
        return None
    return (url, preview)


def _find_recent_user_message(client, user_id: int) -> tuple[str, str] | None:
    """Fall back to the muted user's most recent cached message (jump_url, preview)."""
    try:
        latest = None
        for msg in client.cached_messages:
            if msg.author.id == user_id and (latest is None or msg.created_at > latest.created_at):
                latest = msg
        if latest is None:
            return None
        return (latest.jump_url, _summarise_message(latest))
    except Exception:
        return None


def _classify_mute(entry, client) -> tuple[str, str]:
    """Returns (mute_type_label, moderator_display) for an audit log entry."""
    actor = entry.user
    reason = (entry.reason or "").strip()

    if actor and client.user and actor.id == client.user.id:
        if reason.startswith("Timed out due to ':Shut:'"):
            return ("Shut reaction", reason.split(" by ", 1)[-1].rstrip(".") if " by " in reason else "(via shut reaction)")
        if reason.startswith("Timed out due to ':oggersglare:'"):
            return ("Oggersglare shut", reason.split(" by ", 1)[-1].rstrip(".") if " by " in reason else "(via oggersglare reaction)")
        if reason.startswith("Bedtime!"):
            return ("Bedtime shut", reason.split(" by ", 1)[-1].rstrip(".") if " by " in reason else "(via bedtime reaction)")
        if "VIP Case" in reason:
            return ("VIP Case (self-inflicted)", str(actor))
        return ("Bot timeout", str(actor))

    if actor and actor.id == USERS.WICK_BOT:
        return ("Wick mute", str(actor))

    if actor:
        return ("Native timeout", str(actor))

    return ("Timeout", "(unknown)")


async def notify_mute(client, member):
    """DM mute notification recipients whenever a member is timed out."""
    try:
        await asyncio.sleep(1)  # let audit log catch up

        guild = member.guild
        entry = None
        try:
            cutoff = discord.utils.utcnow() - timedelta(seconds=30)
            async for e in guild.audit_logs(action=discord.AuditLogAction.member_update, limit=10):
                if e.target and e.target.id == member.id and e.created_at >= cutoff:
                    after_until = getattr(e.after, "timed_out_until", None)
                    if after_until is not None:
                        entry = e
                        break
        except discord.Forbidden:
            logger.warning("Missing audit log permission; mute notification will be partial.")
        except Exception as e:
            logger.error(f"Failed to read audit log for mute on {member}: {e}")

        if entry:
            mute_type, moderator = _classify_mute(entry, client)
            reason = (entry.reason or "").strip() or "(no reason given)"
        else:
            mute_type = "Timeout"
            moderator = "(unknown - not in audit log)"
            reason = "(no reason given)"

        until = member.timed_out_until
        now = discord.utils.utcnow()
        if until and until > now:
            total_seconds = int((until - now).total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours:
                duration_str = f"{hours}h{minutes}m"
            elif minutes:
                duration_str = f"{minutes}m{seconds}s"
            else:
                duration_str = f"{seconds}s"
            until_str = discord.utils.format_dt(until, style="t")
        else:
            duration_str = "?"
            until_str = "?"

        msg_info = _consume_mute_trigger(client, member.id)
        is_trigger = msg_info is not None
        if msg_info is None:
            msg_info = _find_recent_user_message(client, member.id)

        lines = [
            f"{member.mention} `{member}` · **{mute_type}** · {duration_str} (→ {until_str})",
            f"By **{moderator}** - {reason[:300]}",
        ]
        if msg_info:
            url, preview = msg_info
            tag = "trigger" if (is_trigger and mute_type in ("Shut reaction", "Bedtime shut", "Oggersglare shut")) else "recent"
            preview_line = preview.replace("\n", " ")
            if len(preview_line) > 200:
                preview_line = preview_line[:200].rstrip() + "…"
            lines.append(f"[{tag} msg]({url}) - {preview_line}")

        embed = discord.Embed(
            description="\n".join(lines)[:4000],
            color=0xE67E22,
            timestamp=now,
        )
        embed.set_author(name=f"🔇 {member} muted", icon_url=member.display_avatar.url)

        for uid in MUTE_NOTIFY_USER_IDS:
            try:
                user = client.get_user(uid) or await client.fetch_user(uid)
                if user:
                    await user.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Cannot DM mute notification to {uid} (DMs closed).")
            except Exception as e:
                logger.error(f"Failed to send mute notification to {uid}: {e}")
    except Exception as e:
        logger.error(f"notify_mute failed for {member}: {e}")

# Set once the bot is ready so award sites that have no client in scope (the
# economy/prediction managers) can still DM + log badge awards instead of awarding
# them silently. See award_badge_notify().
_BADGE_NOTIFY_CLIENT = None


def set_badge_notify_client(client):
    global _BADGE_NOTIFY_CLIENT
    _BADGE_NOTIFY_CLIENT = client


def award_badge_notify(user_id: int, badge_id: str):
    """Award a badge from a synchronous, client-less context (e.g. economy or
    prediction managers) while still notifying the user. Schedules the full
    DM+log path on the running loop when the bot client is available; otherwise
    falls back to a silent award so the badge is never lost."""
    client = _BADGE_NOTIFY_CLIENT
    if client is not None:
        try:
            import asyncio
            asyncio.get_running_loop().create_task(
                award_badge_with_notify(client, user_id, badge_id)
            )
            return
        except RuntimeError:
            pass  # No running loop (e.g. offline script) - award silently below.
    from database import award_badge
    if award_badge(user_id, badge_id):
        try:
            from lib.economy.badge_rewards import pay_badge_reward
            pay_badge_reward(user_id, badge_id)
        except Exception:
            pass


async def award_badge_with_notify(client, user_id: int, badge_id: str):
    """Awards a badge and notifies the user via DM and logs to bot-usage-log."""
    from database import award_badge, DatabaseManager
    
    newly_awarded = award_badge(user_id, badge_id)
    if newly_awarded:
        # One-time UKPence reward for the badge (idempotent, paid from the bank).
        reward_paid = 0
        try:
            from lib.economy.badge_rewards import pay_badge_reward
            reward_paid = pay_badge_reward(user_id, badge_id)
        except Exception:
            logger.error(f"Badge reward payment failed for {user_id}/{badge_id}", exc_info=True)

        # Get badge info
        badge_info = DatabaseManager.fetch_one("SELECT name, description, icon_path, rarity FROM badges WHERE id = ?", (badge_id,))
        if not badge_info:
            logger.warning(f"Badge ID '{badge_id}' not found in database.")
            return

        badge_name, badge_desc, badge_icon, badge_rarity = badge_info

        # Log to bot-usage-log
        log_channel = client.get_channel(CHANNELS.BOT_USAGE_LOG)
        user_mention = f"<@{user_id}>"
        if log_channel:
            reward_str = f" (+{reward_paid:,} UKPence)" if reward_paid else ""
            await log_channel.send(f"🎖️ **Badge Awarded**: {user_mention} just earned the **{badge_name}** {badge_icon} badge!{reward_str}")

        # Notify User via DM
        try:
            user = client.get_user(user_id) or await client.fetch_user(user_id)
            if user:
                color_map = {"Gold": 0xFFD700, "Silver": 0xC0C0C0, "Bronze": 0xCD7F32}
                embed = discord.Embed(
                    title="🎖️ New Badge Earned!",
                    description=f"Congratulations! You've just earned a new badge.",
                    color=color_map.get(badge_rarity, 0x3498db)
                )
                embed.add_field(name="Badge", value=f"{badge_icon} **{badge_name}**", inline=True)
                embed.add_field(name="How to earn", value=badge_desc, inline=True)
                embed.add_field(name="Rarity", value=badge_rarity, inline=True)
                if reward_paid:
                    embed.add_field(name="Reward", value=f"+{reward_paid:,} UKPence", inline=True)
                embed.set_footer(text="Check your /rank to see all your badges!")
                
                await user.send(embed=embed)
                logger.info(f"Sent badge DM to {user_id} for '{badge_id}'")
        except discord.Forbidden:
            logger.warning(f"Could not DM user {user_id} about their new badge '{badge_id}' (DMs closed).")
        except Exception as e:
            logger.error(f"Error notifying user {user_id} about badge '{badge_id}': {e}")

FORUM_CHANNEL_ID = 1341451323249266711
THREAD_MESSAGES_FILE = THREAD_MESSAGES_FILE
ADDED_USERS_FILE = ADDED_USERS_FILE

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
                from commands.moderation.archive_channel import ArchiveButtonView
                client.add_view(ArchiveButtonView(client, channel_id), message_id=value["msg_id"])
                target_timestamp = value["move_timestamp"]
                private = value.get("private", False)
                asyncio.create_task(schedule_archive_move(channel, channel.guild, target_timestamp, client, private))
        elif key.startswith("unarchive_") and isinstance(value, dict) and "msg_id" in value:
            channel_id = int(key.split("_")[1])
            channel = client.get_channel(channel_id)
            if channel:
                from commands.moderation.archive_channel import UnarchiveButtonView
                client.add_view(UnarchiveButtonView(client, channel_id), message_id=value["msg_id"])
        elif isinstance(value, dict) and value.get("type") == "wager":
            try:
                from commands.economy.wager import WagerDecisionView
                view = WagerDecisionView(value["challenger_id"], value["opponent_id"], value["amount"], value["topic"], value.get("challenger_name", "User A"), value.get("opponent_name", "User B"))
                client.add_view(view, message_id=int(key))
            except ImportError as e:
                logger.error(f"Failed to import WagerDecisionView: {e}")
        elif isinstance(value, dict) and value.get("type") == "roulette":
            try:
                from commands.economy.roulette import reattach_roulette_round
                reattach_roulette_round(client, key, value)
            except Exception as e:
                logger.error(f"Failed to recover roulette round {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "blackjack":
            try:
                from commands.economy.blackjack import reattach_blackjack_view
                reattach_blackjack_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach blackjack view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "higherlower":
            try:
                from commands.economy.higher_lower import reattach_hl_view
                reattach_hl_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach higher-lower view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "videopoker":
            try:
                from commands.economy.video_poker import reattach_videopoker_view
                reattach_videopoker_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach video-poker view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "reddog":
            try:
                from commands.economy.red_dog import reattach_reddog_view
                reattach_reddog_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach red-dog view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "tcp":
            try:
                from commands.economy.three_card_poker import reattach_tcp_view
                reattach_tcp_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach three-card-poker view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "mines":
            try:
                from commands.economy.mines import reattach_mines_view
                reattach_mines_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach mines view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "chest":
            try:
                from commands.economy.chest import reattach_chest_view
                reattach_chest_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach chest view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "crash":
            try:
                from commands.economy.blockade import reattach_crash_view
                reattach_crash_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach blockade view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "penalty":
            try:
                from commands.economy.penalty import reattach_penalty_view
                reattach_penalty_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to reattach penalty view {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "connect4":
            try:
                from commands.economy.connect4 import reattach_connect4_view
                reattach_connect4_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to recover connect4 game {key}: {e}")
        elif isinstance(value, dict) and value.get("type") == "battleship":
            try:
                from commands.economy.battleship import reattach_battleship_view
                reattach_battleship_view(client, key, value)
            except Exception as e:
                logger.error(f"Failed to recover battleship game {key}: {e}")
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
                        from lib.economy.shop_ui import EmojiStickerApprovalView

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


async def _delete_fletcher_duplicate(client, trigger_message, quoted_message):
    """The Fletcher bot posts its own summary for the same message link, so we get a double.
    Find Fletcher's summary (a bot message right after the link-post that references the same
    quoted message) and delete it. Conservative: only deletes when both signals match."""
    import config
    if not getattr(config, "FLETCHER_DEDUPE_ENABLED", False):
        return
    fid = getattr(config, "FLETCHER_BOT_ID", None)
    names = [n.lower() for n in getattr(config, "FLETCHER_BOT_NAMES", ["fletcher"])]

    def is_fletcher(m):
        if not m.author.bot:
            return False
        if fid and m.author.id == fid:
            return True
        tags = " ".join(filter(None, [m.author.name, getattr(m.author, "display_name", None),
                                       getattr(m.author, "global_name", None)])).lower()
        return any(n in tags for n in names)

    # Signals that a Fletcher message is *this* link's summary: it names the quoted author or
    # repeats a chunk of the quoted content.
    author_tags = [t.lower() for t in filter(None, [
        quoted_message.author.name, getattr(quoted_message.author, "display_name", None),
        getattr(quoted_message.author, "global_name", None)])]
    snippet = (quoted_message.content or "").strip()[:40].lower()

    def looks_like_dup(m):
        text = (m.content or "").lower()
        for e in m.embeds:
            text += " " + " ".join(filter(None, [e.title, e.description, e.author.name if e.author else None])).lower()
        return any(t and t in text for t in author_tags) or (len(snippet) >= 8 and snippet in text)

    for _ in range(6):  # Fletcher may post a beat after us; poll briefly
        try:
            async for m in trigger_message.channel.history(limit=10, after=trigger_message.created_at):
                if is_fletcher(m) and looks_like_dup(m):
                    await m.delete()
                    return
        except Exception:
            logger.debug("Fletcher dedupe scan failed", exc_info=True)
        await asyncio.sleep(1.0)


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
                asyncio.create_task(_delete_fletcher_duplicate(client, message, quoted_message))
                timestamp_unix = int(quoted_message.created_at.timestamp())
                timestamp_formatted = f"<t:{timestamp_unix}:f>"
                channel_name = channel.name
                reply_content = f"@__{quoted_message.author.display_name}__ in *{channel_name}* {timestamp_formatted}:\n"
                filtered_content = re.sub(r"<@&?\d+>", "", quoted_message.content).replace("@everyone", "[everyone]").replace("@here", "[here]").strip()
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
                    save_json_file(THREAD_MESSAGES_FILE, client.thread_messages)
                    existing_msg = new_msg
                await existing_msg.edit(content=f"{message.author.mention}")
                logger.info(f"Silently added {message.author} to {thread.name}")
                if thread_id not in client.added_users:
                    client.added_users[thread_id] = []
                client.added_users[thread_id].append(user_id)
                save_json_file(ADDED_USERS_FILE, client.added_users)
                await asyncio.sleep(1)
                await existing_msg.edit(content=".")
            except discord.HTTPException as e:
                logger.warning(f"Failed to add {message.author} to {thread.name}: {e}")


async def on_ready(client, tree, scheduler):
    set_badge_notify_client(client)
    try:
        from lib.economy.poker import escrow as _poker_escrow
        _poker_escrow.refund_all()  # void+refund any poker hands a restart interrupted
    except Exception:
        logger.error("poker escrow refund on startup failed", exc_info=True)
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
        client.xp_system = XPSystem(client)
        logger.info("XP system initialised")
    # Run exactly once per process: on_ready can re-fire on a gateway reconnect, and
    # in-process add_view registrations survive that, so re-running this would bind a
    # second game object to each live casino message and defeat the busy-flag guard.
    if not getattr(client, "_views_reattached", False):
        reattach_persistent_views(client)
        client._views_reattached = True
        logger.info("Persistent views reattached.")
    for command in tree.get_commands():
        logger.info(f"Command loaded: {command.name}")
        await asyncio.sleep(0.1)
    from lib.bot.scheduled_tasks import schedule_client_jobs
    schedule_client_jobs(client, scheduler)
    logger.info(f"{client.user} setup complete")
    await refresh_live_stages(client)
    from lib.bot.backup_manager import backup_database, backup_json_data
    await backup_database(client)
    await backup_json_data(client)


async def mirror_voice_message(client, message):
    """Re-upload any voice-note attachment to the deletion log channel so it survives deletion."""
    if message.author.bot or not message.attachments:
        return
    if not getattr(message.flags, "voice", False):
        return
    log_channel = client.get_channel(CHANNELS.VOICE_LOG_THREAD)
    if log_channel is None:
        try:
            log_channel = await client.fetch_channel(CHANNELS.VOICE_LOG_THREAD)
        except (discord.NotFound, discord.Forbidden):
            return
    size_limit = getattr(message.guild, "filesize_limit", 25 * 1024 * 1024) if message.guild else 25 * 1024 * 1024
    files = []
    for att in message.attachments:
        if att.size and att.size > size_limit:
            continue
        try:
            data = await att.read()
        except (discord.HTTPException, discord.NotFound) as e:
            logger.warning(f"[voice-mirror] download failed for {att.filename}: {e}")
            continue
        import io
        files.append(discord.File(io.BytesIO(data), filename=att.filename))
    if not files:
        return
    try:
        await log_channel.send(
            content=(
                f"🎙 Voice message from {message.author.mention} in {message.channel.mention} "
                f"([jump]({message.jump_url}))"
            ),
            files=files,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException as e:
        logger.warning(f"[voice-mirror] send failed: {e}")


# "piggyreact" toggle (oggers): react to every PIGGY message with H-O-G. Cached on the
# client, persisted to PIGGY_REACT_FILE so it survives restarts.
_PIGGY_REACT_EMOJIS = ("\U0001F1ED", "\U0001F1F4", "\U0001F1EC")  # 🇭 🇴 🇬


def _piggy_react_enabled(client) -> bool:
    if not hasattr(client, "_piggy_react"):
        from lib.core.file_operations import load_json_file
        data = load_json_file(PIGGY_REACT_FILE) or {}
        client._piggy_react = bool(data.get("enabled", False))
    return client._piggy_react


def _set_piggy_react(client, enabled: bool):
    from lib.core.file_operations import save_json_file
    client._piggy_react = bool(enabled)
    save_json_file(PIGGY_REACT_FILE, {"enabled": bool(enabled)})


async def on_message(client, message):
    if await handle_hate_speech_message(client, message):
        return

    # New-member welcome rewards. The join "X joined" system message is always captured
    # (cheap type check); for normal messages we only do work while a welcome window is
    # open, and fire-and-forget so it never blocks the rest of on_message.
    try:
        from lib.features.ukp_rewards import (
            note_join_system_message,
            handle_welcome_reward,
            welcome_window_open,
        )
        if message.type == discord.MessageType.new_member:
            note_join_system_message(message)
        elif welcome_window_open() and not message.author.bot:
            asyncio.create_task(handle_welcome_reward(client, message))
    except Exception:
        logger.debug("welcome reward hook failed", exc_info=True)

    # Battleship threads are "pseudo-locked": real locking blocks button interactions, so any
    # message posted there by anyone other than the bot (chat, or Fletcher's auto-summon) is
    # deleted to keep the board + ephemerals clean.
    if isinstance(message.channel, discord.Thread):
        try:
            from commands.economy.battleship import ACTIVE_GAME_THREADS
            if message.channel.id in ACTIVE_GAME_THREADS and message.author.id != client.user.id:
                try:
                    await message.delete()
                except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                    pass
                return
        except Exception:
            pass
        # Fletcher auto-posts a "Summoning ... use_threads ..." message on every new thread -
        # bin that everywhere (e.g. poker threads), without touching normal chat.
        if (message.author.bot and "use_threads" in message.content.lower()
                and "summoning" in message.content.lower()):
            try:
                await message.delete()
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                pass
            return

    # Deputy PM unlock/lock of the politics channel for @everyone (prefix command + confirm).
    from lib.features.politics_access import handle_politics_control_command
    if await handle_politics_control_command(client, message):
        return

    # Staff warning command to deter political discussion in general chat.
    trigger = message.content.lower().strip()
    if trigger in ["polwarn", "!polwarn"]:
        is_staff = hasattr(message.author, "roles") and any(
            role.id in [ROLES.DEPUTY_PM, ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE, ROLES.PCSO]
            for role in message.author.roles
        )
        if message.author.id == USERS.OGGERS or is_staff:
            ref = message.reference
            try:
                await message.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
            
            embed = discord.Embed(
                title="🚫 Politics in General Chat",
                description=(
                    "We don't want political discussions on this server.\n\n"
                    "General chat is kept strictly non-political to keep the environment relaxed. "
                    "We do have a private politics channel, but it is restricted to active, established "
                    "members to limit the drama. Please keep all political topics out of chat."
                ),
                color=0xE74C3C
            )
            await message.channel.send(embed=embed, reference=ref, mention_author=False)
            return

    # oggers toggles the piggy-react feature on/off; the command message is removed.
    if message.author.id == USERS.OGGERS and message.content.lower().strip() == "piggyreact":
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        _set_piggy_react(client, not _piggy_react_enabled(client))
        return

    # When enabled, spell H-O-G on every message PIGGY sends (order matters).
    if _piggy_react_enabled(client) and message.author.id == USERS.PIGGY:
        for emoji in _PIGGY_REACT_EMOJIS:
            try:
                await message.add_reaction(emoji)
            except (discord.HTTPException, discord.Forbidden):
                break

    if not hasattr(client, "xp_system"):
        from lib.features.xp_system import XPSystem
        client.xp_system = XPSystem(client)
        logger.info("XP system initialised")

    await mirror_voice_message(client, message)

    await client.xp_system.update_xp(message)

    # Per-message activity badges (night owl, morning person, holiday badges, echo,
    # town crier, global citizen, weekend warrior, periodic pillar check). The old
    # disabled "MegaShut"/tung auto-timeout block that wrapped this has been removed
    # (see git history); these badges were unreachable while sat inside `if False`.
    if not message.author.bot and message.type != discord.MessageType.new_member:
        ensure_bb(message.author.id)
        try:
            from lib.bot.event_handlers import track_night_owl, track_morning_person, award_badge_with_notify
            if track_night_owl(message.author.id) >= 100:
                await award_badge_with_notify(client, message.author.id, 'night_owl')
                
            if track_morning_person(message.author.id) >= 50:
                await award_badge_with_notify(client, message.author.id, 'morning_person')
                
            import datetime
            import pytz
            uk_tz = pytz.timezone("Europe/London")
            now = datetime.datetime.now(uk_tz)
            if now.month == 1 and now.day == 1 and now.hour == 0 and now.minute < 5:
                await award_badge_with_notify(client, message.author.id, 'new_year_new_me')
                
            if now.month == 4 and now.day == 1:
                await award_badge_with_notify(client, message.author.id, 'april_fools')
                
            if now.month == 11 and now.day == 5:
                await award_badge_with_notify(client, message.author.id, 'guy_fawkes')
                
            # Echo Badge Logic
            if not hasattr(client, "echo_tracking"):
                client.echo_tracking = {} # channel_id -> {"content": str, "author_id": int, "count": int}
            
            channel_id = message.channel.id
            current_content = message.content
            if current_content:
                last_echo = client.echo_tracking.get(channel_id)
                if last_echo and last_echo["content"] == current_content and last_echo["author_id"] != message.author.id:
                    last_echo["count"] += 1
                    last_echo["author_id"] = message.author.id # Update to current author for next echo
                    from lib.economy import secret_config as _sc
                    _echo_n = _sc.param("a1")
                    if _echo_n is not None and last_echo["count"] >= _echo_n and (_b := _sc.bid("a1")):
                        await award_badge_with_notify(client, message.author.id, _b)
                else:
                    client.echo_tracking[channel_id] = {
                        "content": current_content,
                        "author_id": message.author.id,
                        "count": 1
                    }
            
            # --- New Automatic Badges ---
            
            # 1. Town Crier (First message of the day)
            today_str = now.strftime("%Y-%m-%d")
            town_crier_data = load_json_file(TOWN_CRIER_TRACKING_FILE) or {}
            if today_str not in town_crier_data:
                town_crier_data[today_str] = str(message.author.id)
                save_json_file(TOWN_CRIER_TRACKING_FILE, town_crier_data)
                
                # Only award if it's reasonably early (before 5 AM)
                # This prevents awarding it to the first chatter when the bot starts mid-day
                if now.hour < 5:
                    await award_badge_with_notify(client, message.author.id, 'town_crier')
                else:
                    logger.debug(f"Town Crier for {today_str} recorded but not awarded as it's past 5 AM ({now.hour}:{now.minute:02d})")
            
            # 2. Global Citizen (5 channels in 5 mins)
            if not hasattr(client, "global_citizen_tracking"):
                client.global_citizen_tracking = {} # user_id -> {timestamp: [channel_ids]}
            
            gc_user = client.global_citizen_tracking.get(message.author.id, {"timestamp": time.time(), "channels": set()})
            # Reset if more than 5 mins have passed since first tracked message in window
            if time.time() - gc_user["timestamp"] > 300:
                gc_user = {"timestamp": time.time(), "channels": set()}
            
            gc_user["channels"].add(message.channel.id)
            client.global_citizen_tracking[message.author.id] = gc_user
            
            if len(gc_user["channels"]) >= 5:
                await award_badge_with_notify(client, message.author.id, 'global_citizen')
                # Optional: reset window after award to avoid multiple triggers
                client.global_citizen_tracking[message.author.id] = {"timestamp": 0, "channels": set()}

            # 3. Weekend Warrior (800 messages on a weekend)
            # now.weekday() is 5 for Saturday, 6 for Sunday
            if now.weekday() in (5, 6):
                # We need a unique key for the specific weekend (e.g., Year-WeekNumber)
                weekend_key = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]}"
                ww_data = load_json_file(WEEKEND_WARRIOR_COUNTS_FILE) or {}
                if weekend_key not in ww_data:
                    ww_data[weekend_key] = {}
                
                uid_str = str(message.author.id)
                ww_data[weekend_key][uid_str] = ww_data[weekend_key].get(uid_str, 0) + 1
                save_json_file(WEEKEND_WARRIOR_COUNTS_FILE, ww_data)
                
                if ww_data[weekend_key][uid_str] >= 800:
                    await award_badge_with_notify(client, message.author.id, 'weekend_warrior')

            # 4. Periodic Pillar Check (Every 50 messages)
            if not hasattr(client, "message_since_pillar_check"):
                client.message_since_pillar_check = defaultdict(int)
            
            client.message_since_pillar_check[message.author.id] += 1
            if client.message_since_pillar_check[message.author.id] >= 50:
                client.message_since_pillar_check[message.author.id] = 0
                if hasattr(message.author, "joined_at") and message.author.joined_at:
                    member_duration = discord.utils.utcnow() - message.author.joined_at
                    days = member_duration.days
                    if days >= 1825:
                        await award_badge_with_notify(client, message.author.id, 'pillar_5')
                    elif days >= 1095:
                        await award_badge_with_notify(client, message.author.id, 'pillar_3')
                    elif days >= 365:
                        await award_badge_with_notify(client, message.author.id, 'pillar_1')

        except Exception as e:
            logger.error(f"Error tracking message activity badges: {e}")
    await process_message_attachments(client, message)
    await process_message_links(client, message)
    
    if message.content.lower().startswith("ukpadd"):
        if hasattr(message.author, "roles") and any(role.id == ROLES.DEPUTY_PM for role in message.author.roles):
            try:
                from lib.economy.bank_commands_ui import (
                    UKPAddAmountLaunchView,
                    UKPAddUserSelectView,
                )

                mentioned = [m for m in message.mentions if not m.bot]
                # Deduplicate while preserving order
                seen = set()
                unique_mentions = []
                for m in mentioned:
                    if m.id not in seen:
                        seen.add(m.id)
                        unique_mentions.append(m)

                if unique_mentions:
                    if len(unique_mentions) > 100:
                        await message.reply(
                            f"❌ Too many recipients ({len(unique_mentions)}). Maximum is 100."
                        )
                    else:
                        recipients_str = ", ".join(m.mention for m in unique_mentions)
                        await message.reply(
                            f"Hand out UKPence to {recipients_str}?",
                            view=UKPAddAmountLaunchView(message.author.id, unique_mentions),
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                else:
                    await message.reply(
                        "Please select the members you want to give UKPence to:",
                        view=UKPAddUserSelectView(message.author.id)
                    )
            except Exception as e:
                logger.error(f"Error launching ukpadd flow: {e}")

    if message.content.lower().startswith("roleadd") and message.guild is not None:
        if hasattr(message.author, "roles") and any(role.id == ROLES.DEPUTY_PM for role in message.author.roles):
            try:
                from lib.features.role_admin_ui import RoleAddView, _bot_can_assign

                seen = set()
                mentioned = []
                for m in message.mentions:
                    if not m.bot and m.id not in seen:
                        seen.add(m.id)
                        mentioned.append(m)
                # A role can be pre-picked by mentioning it too (e.g. "roleadd @user @Role").
                pre_role = next((r for r in message.role_mentions if _bot_can_assign(message.guild, r) is None), None)

                # Mentioned recipients aren't limited (no select menu involved); only the
                # no-mention path uses the UserSelect, which Discord hard-caps at 25.
                if not mentioned:
                    prompt = "Select members and a role to add:"
                elif len(mentioned) == 1:
                    prompt = f"Pick a role to add to {mentioned[0].mention}:"
                else:
                    who = ", ".join(m.mention for m in mentioned[:20])
                    if len(mentioned) > 20:
                        who += f" and {len(mentioned) - 20} more"
                    prompt = f"Pick a role to add to these {len(mentioned)} members:\n{who}"
                await message.reply(
                    prompt,
                    view=RoleAddView(message.author.id, mentioned or None, pre_role),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except Exception as e:
                logger.error(f"Error launching roleadd flow: {e}", exc_info=True)

    is_deputy_pm = hasattr(message.author, "roles") and any(role.id == ROLES.DEPUTY_PM for role in message.author.roles)
    if message.content.lower().strip() == "shopadmin" and message.author.id == USERS.OGGERS:
        try:
            await message.delete()
            from lib.economy.admin_shop_ui import AdminShopLaunchView
            await message.channel.send(
                "**Shop Admin** - pick an item:",
                view=AdminShopLaunchView(message.author.id),
            )
        except Exception as e:
            logger.error(f"Error launching AdminShopLaunchView: {e}")

    if message.content.lower().strip() == "titleadd" and (message.author.id == USERS.OGGERS or is_deputy_pm):
        try:
            await message.delete()
            from lib.features.titles import TitleLaunchView
            await message.channel.send(
                "HMS Victory Title Management System",
                view=TitleLaunchView()
            )
        except Exception as e:
            logger.error(f"Error launching TitleLaunchView: {e}")

    if message.content.lower().startswith("hmsql") and message.author.id == USERS.OGGERS:
        query = message.content[len("hmsql"):].strip()
        if not query:
            return
        # Only allow read-only queries
        first_word = query.strip().split()[0].upper() if query.strip() else ""
        if first_word not in ("SELECT", "PRAGMA"):
            await message.reply("❌ Only read-only queries (SELECT/PRAGMA) are allowed.", mention_author=False)
            return
        try:
            rows = DatabaseManager.fetch_all(query)
            if not rows:
                await message.reply("✅ Query returned 0 rows.", mention_author=False)
                return
            # Format results as a code block
            header = " | ".join(str(i) for i in range(len(rows[0])))
            lines = [header, "-" * len(header)]
            for row in rows[:50]:  # Cap at 50 rows
                lines.append(" | ".join(str(v) for v in row))
            result_text = "\n".join(lines)
            if len(result_text) > 1900:
                result_text = result_text[:1900] + "\n... (truncated)"
            await message.reply(f"```\n{result_text}\n```\n*{len(rows)} row(s) returned*", mention_author=False)
        except Exception as e:
            await message.reply(f"❌ SQL Error: `{e}`", mention_author=False)

    if message.author.bot:
        return
    # await process_forum_threads(client, message)


async def on_interaction(interaction: Interaction):
    if interaction.type == InteractionType.component and "custom_id" in interaction.data:
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            await handleRoleButtonInteraction(interaction)


async def on_member_join(member):
    try:
        await handle_new_member_anti_raid(member)
        # Open the welcome window so members who greet this newcomer can earn UKPence.
        try:
            from lib.features.ukp_rewards import register_new_member_join
            register_new_member_join(member)
        except Exception:
            logger.debug("register_new_member_join failed", exc_info=True)
        role = member.guild.get_role(ROLES.MEMBER)
        if role:
            await member.add_roles(role)
    except discord.NotFound:
        logger.info(f"Member {member.id} left before roles could be assigned.")
    except discord.Forbidden:
        logger.warning(f"Insufficient permissions to add role to {member.id}.")
    except Exception as e:
        logger.error(f"Error in on_member_join for {member.id}: {e}")


async def on_member_remove(member):
    try:
        current_balance = get_bb(member.id)
        if current_balance > 0:
            success = remove_bb(member.id, current_balance, reason="Left server - balance reclaimed")
            if success:
                logger.info(f"Reclaimed {current_balance} UKPence from leaving member {member} and returned to the server bank.")
    except Exception as e:
        logger.error(f"Error handling UKPence extraction for leaving member {member}: {e}")


async def on_member_ban(guild, user):
    pass


async def on_voice_state_update(member, before, after):
    client = member._state._get_client()
    if member.bot:
        return

    if not hasattr(client, "lurker_tracking"):
        client.lurker_tracking = {} # user_id -> start_time

    is_muted = after.self_mute or after.mute or after.self_deaf or after.deaf
    was_muted = before.self_mute or before.mute or before.self_deaf or before.deaf
    
    # Joined or switched to muted state
    if after.channel and is_muted:
        if member.id not in client.lurker_tracking:
            client.lurker_tracking[member.id] = datetime.datetime.now()
            logger.debug(f"Started lurker tracking for {member.display_name}")
    
    # Left channel or unmuted
    elif (not after.channel or not is_muted) and member.id in client.lurker_tracking:
        start_time = client.lurker_tracking.pop(member.id)
        duration = (datetime.datetime.now() - start_time).total_seconds()
        
        from lib.economy import secret_config as _sc
        _lurk = _sc.param("a2")
        if _lurk is not None and duration >= _lurk and (_b := _sc.bid("a2")):
            await award_badge_with_notify(client, member.id, _b)
        logger.debug(f"Stopped lurker tracking for {member.display_name}. Duration: {duration}s")

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
            image_buffer = await create_message_image(client, message, "Deleted Message")
            description = f"Message by {message.author.mention} ({message.author.id}) deleted in {message.channel.mention}."
            if deleter and deleter != message.author:
                description += f"\nDeleted by {deleter.mention} ({deleter.id})."
            description += f"\n\n>>> {message.content}"
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
        image_buffer = await create_edited_message_image(client, before, after)
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
    DatabaseManager.execute(
        "INSERT INTO shut_counts (user_id, count) VALUES (?, 1) ON CONFLICT(user_id) DO UPDATE SET count = count + 1",
        (str(user_id),)
    )

def track_warden(user_id: int, victim_id: int):
    uid = str(user_id)
    vid = str(victim_id)
    # INSERT OR IGNORE so duplicates are silently skipped
    DatabaseManager.execute(
        "INSERT OR IGNORE INTO warden_targets (user_id, victim_id) VALUES (?, ?)",
        (uid, vid)
    )
    row = DatabaseManager.fetch_one("SELECT COUNT(*) FROM warden_targets WHERE user_id = ?", (uid,))
    return row[0] if row else 0

def track_morning_person(user_id: int) -> int:
    import datetime
    import pytz
    uk_tz = pytz.timezone("Europe/London")
    now = datetime.datetime.now(uk_tz)
    
    # 6 AM to 9 AM UK Time
    if 6 <= now.hour < 9:
        data = load_json_file(MORNING_PERSON_COUNTS_FILE) or {}
        uid = str(user_id)
        data[uid] = data.get(uid, 0) + 1
        save_json_file(MORNING_PERSON_COUNTS_FILE, data)
        return data[uid]
    return 0

def track_night_owl(user_id: int):
    import datetime
    import pytz
    uk_tz = pytz.timezone("Europe/London")
    now = datetime.datetime.now(uk_tz)
    if 2 <= now.hour < 5:
        data = load_json_file(NIGHT_OWL_COUNTS_FILE) or {}
        uid = str(user_id)
        data[uid] = data.get(uid, 0) + 1
        save_json_file(NIGHT_OWL_COUNTS_FILE, data)
        return data[uid]
    return 0

def track_party_animal(user_id: int):
    import datetime
    import pytz
    data = load_json_file(PARTY_ANIMAL_TARGETS_FILE) or {}
    uid = str(user_id)
    uk_tz = pytz.timezone("Europe/London")
    date_str = datetime.datetime.now(uk_tz).strftime("%Y-%m-%d")
    if uid not in data:
        data[uid] = []
    if date_str not in data[uid]:
        data[uid].append(date_str)
        save_json_file(PARTY_ANIMAL_TARGETS_FILE, data)
    return len(data[uid])
async def handle_shut_reaction(reaction, user):
    client = reaction.message._state._get_client()
    has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
    message_author = reaction.message.author
    if message_author.is_timed_out():
        logger.info(f"User {message_author} is already timed out. Skipping further actions.")
        return
    try:
        reason = f"Timed out due to ':Shut:' reaction by {user.name}#{user.discriminator}."
        _record_mute_trigger(client, message_author.id, reaction.message)
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
        await award_badge_with_notify(client, message_author.id, 'shut_victim')
        
        # Track warden badge logic here
        warden_count = track_warden(user.id, message_author.id)
        if warden_count >= 10:
            await award_badge_with_notify(client, user.id, 'warden')
            
        if not has_role:
            await award_badge_with_notify(client, user.id, 'shutcoin_user')
    except Exception as e:
        logger.error(f"Failed to time out user {message_author}: {e}")


async def handle_bedtime_reaction(reaction, user):
    """Timeout until next 7 AM UK time, triggered by :bedtime: reaction. Cabinet/Border Force only."""
    has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
    if not has_role:
        return
    client = reaction.message._state._get_client()
    message_author = reaction.message.author
    if message_author.is_timed_out():
        logger.info(f"User {message_author} is already timed out. Skipping bedtime.")
        return
    try:
        uk_tz = pytz.timezone("Europe/London")
        now_uk = datetime.now(uk_tz)
        next_7am = now_uk.replace(hour=7, minute=0, second=0, microsecond=0)
        if now_uk >= next_7am:
            next_7am += timedelta(days=1)
        duration = next_7am - now_uk
        reason = f"Bedtime! Timed out until 7 AM UK by {user.name}#{user.discriminator}."
        _record_mute_trigger(client, message_author.id, reaction.message)
        await message_author.timeout(discord.utils.utcnow() + duration, reason=reason)
        sticker_message = await reaction.message.reply(stickers=[discord.Object(id=1500885911293136916)])
        sticker_messages[reaction.message.id] = (sticker_message.id, user.id)
        hours, remainder = divmod(int(duration.total_seconds()), 3600)
        mins = remainder // 60
        logger.info(f"User {message_author} sent to bed for {hours}h{mins}m (until 7 AM UK) by {user}.")
        save_shut_count(message_author.id)
        await award_badge_with_notify(client, message_author.id, 'shut_victim')
    except Exception as e:
        logger.error(f"Failed to bedtime user {message_author}: {e}")


# :oggersglare: reaction - a 30-second 'shut' with its own sticker. Cabinet/Border Force only.
OGGERSGLARE_EMOJI_ID = 1514618204784431194
OGGERSGLARE_STICKER_ID = 1514616284707426384


async def handle_oggersglare_reaction(reaction, user):
    """30-second shut triggered by the :oggersglare: reaction, replying with the matching
    sticker. Mirrors the :Shut:/:bedtime: flows (staff-only, undoable by un-reacting)."""
    has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
    if not has_role:
        return
    client = reaction.message._state._get_client()
    message_author = reaction.message.author
    if message_author.is_timed_out():
        logger.info(f"User {message_author} is already timed out. Skipping oggersglare.")
        return
    try:
        duration = timedelta(seconds=30)
        reason = f"Timed out due to ':oggersglare:' reaction by {user.name}#{user.discriminator}."
        _record_mute_trigger(client, message_author.id, reaction.message)
        await message_author.timeout(discord.utils.utcnow() + duration, reason=reason)
        sticker_message = await reaction.message.reply(stickers=[discord.Object(id=OGGERSGLARE_STICKER_ID)])
        sticker_messages[reaction.message.id] = (sticker_message.id, user.id)
        logger.info(f"User {message_author} shut for 30s due to ':oggersglare:' reaction by {user}.")
        save_shut_count(message_author.id)
        await award_badge_with_notify(client, message_author.id, 'shut_victim')
        warden_count = track_warden(user.id, message_author.id)
        if warden_count >= 10:
            await award_badge_with_notify(client, user.id, 'warden')
    except Exception as e:
        logger.error(f"Failed to oggersglare-shut user {message_author}: {e}")


hof_lock = asyncio.Lock()


async def check_hall_of_fame(client, payload):
    async with hof_lock:
        hall_of_fame_data = load_json_file(HALL_OF_FAME_FILE) or []
        
        already_in_hof = str(payload.message_id) in hall_of_fame_data

        channel = client.get_channel(payload.channel_id)
        if not channel:
            try:
                channel = await client.fetch_channel(payload.channel_id)
            except discord.NotFound:
                logger.error(f"[HOF] Channel {payload.channel_id} not found.")
                return
                
        try:
            message = await channel.fetch_message(payload.message_id)
            if message.author.bot:
                return
            
            # Prevent old messages from qualifying
            if (discord.utils.utcnow() - message.created_at).days > 7:
                logger.debug(f"[HOF] Skipping message {message.id} as it is too old (created {message.created_at}).")
                return
        except discord.NotFound:
            logger.error(f"[HOF] Message {payload.message_id} not found.")
            return

        # If the message is already in the Hall of Fame, the only thing left to
        # award is local_legend (10 unique reactors) - which used to be unreachable
        # because the old early-return fired first. Skip the expensive reactor
        # recount only when the author already holds that one-time badge.
        if already_in_hof:
            from database import DatabaseManager
            if DatabaseManager.fetch_one(
                "SELECT 1 FROM user_badges WHERE user_id = ? AND badge_id = 'local_legend'",
                (str(message.author.id),),
            ):
                return

        total_reactions = sum(r.count for r in message.reactions)
        # logger.info(f"[HOF] Checking message {message.id}. Total reactions: {total_reactions}")

        # Quick filter to avoid iterating through users if total reactions are less than 6
        if total_reactions < 6:
            return

        # Hall of Fame is for organic community posts. Skip bot/webhook posts and
        # announcement channels (which naturally rack up reactions but aren't HoF-worthy).
        ch = message.channel
        if (message.author.bot or getattr(message, "webhook_id", None)
                or getattr(ch, "type", None) == discord.ChannelType.news
                or ch.id in HOF_EXCLUDED_CHANNELS):
            return

        unique_reactors = set()
        for r in message.reactions:
            async for u in r.users():
                unique_reactors.add(u.id)
                
        # logger.info(f"[HOF] Unique reactors for {message.id}: {len(unique_reactors)}")

        if not already_in_hof and len(unique_reactors) >= 6:
            logger.info(f"[HOF] Message {message.id} qualified for Hall of Fame! Relevant client: {client}")
            hall_of_fame_data.append(str(message.id))
            save_json_file(HALL_OF_FAME_FILE, hall_of_fame_data)
            
            thread = client.get_channel(CHANNELS.HALL_OF_FAME_THREAD)
            if not thread:
                try:
                    thread = await client.fetch_channel(CHANNELS.HALL_OF_FAME_THREAD)
                except discord.NotFound:
                    logger.error("Hall of Fame thread not found.")
                    return
                    
            # Shared with the manual context menu so videos/gifs get re-uploaded
            # inline instead of being dropped from the quote card.
            from commands.social.hof import send_hof_post
            await send_hof_post(client, thread, message)

            logger.info(f"Message {message.id} sent to Hall of Fame.")
            await award_badge_with_notify(client, message.author.id, 'hof')
            # UKP reward (from the bank), DM'd to the author.
            try:
                if not message.author.bot:
                    from lib.features.ukp_rewards import award_hof_reward
                    await award_hof_reward(client, message.author.id)
            except Exception:
                logger.error("HoF UKP reward failed", exc_info=True)
        
        # Local Legend Check (10 unique reactors)
        if len(unique_reactors) >= 10:
            await award_badge_with_notify(client, message.author.id, 'local_legend')


async def on_raw_reaction_add(client, payload):
    try:
        await check_hall_of_fame(client, payload)
        
        # Announcement speed badges
        is_announcement_channel = False
        announcement_channel_id = None

        if payload.channel_id in [CHANNELS.ANNOUNCEMENTS, CHANNELS.MINOR_ANNOUNCEMENTS]:
            is_announcement_channel = True
            announcement_channel_id = payload.channel_id
        else:
            # Check for Forum threads (parents)
            channel = client.get_channel(payload.channel_id)
            if not channel: 
                try:
                    channel = await client.fetch_channel(payload.channel_id)
                except discord.NotFound:
                    channel = None
            
            if channel and hasattr(channel, "parent_id") and channel.parent_id in [CHANNELS.ANNOUNCEMENTS, CHANNELS.MINOR_ANNOUNCEMENTS]:
                is_announcement_channel = True
                announcement_channel_id = channel.parent_id

        if is_announcement_channel:
            channel = client.get_channel(payload.channel_id)
            if not channel: channel = await client.fetch_channel(payload.channel_id)
            
            message = await channel.fetch_message(payload.message_id)
            
            # 1. Don't award for reacting to your own message
            if payload.user_id == message.author.id:
                return

            time_diff = (discord.utils.utcnow() - message.created_at).total_seconds()
            if time_diff <= 600: # 10 minutes
                badge_id = 'announcement_fast' if announcement_channel_id == CHANNELS.ANNOUNCEMENTS else 'minor_announcement_fast'
                await award_badge_with_notify(client, payload.user_id, badge_id)
            else:
                logger.debug(f"User {payload.user_id} reacted too late for badge ({time_diff:.1f}s > 600s)")
    except Exception as e:
        logger.error(f"Error in on_raw_reaction_add: {e}")


async def on_reaction_add(reaction, user):
    try:
        # Check for Americanism correction deletion
        if str(reaction.emoji) == "❌":
            deletions = load_webhook_deletions()
            message_id_str = str(reaction.message.id)
            if message_id_str in deletions:
                data = deletions[message_id_str]
                # Support both old format (int) and new format (dict)
                owner_id = data["user_id"] if isinstance(data, dict) else data
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
                else:
                    # Not the owner, remove their reaction
                    try:
                        await reaction.remove(user)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    return

        if str(reaction.emoji) in FLAG_LANGUAGE_MAPPINGS:
            await handle_flag_reaction(reaction, reaction.message, user)
        if ":bedtime:" in str(reaction.emoji):
            await handle_bedtime_reaction(reaction, user)
        elif getattr(reaction.emoji, "id", None) == OGGERSGLARE_EMOJI_ID:
            await handle_oggersglare_reaction(reaction, user)
        elif ":Shut:" in str(reaction.emoji):
            await handle_shut_reaction(reaction, user)
            
    except Exception as e:
        logger.error(f"Error in on_reaction_add: {e}")


async def on_reaction_remove(reaction, user):
    emoji_str = str(reaction.emoji)

    # bedtime undo - Cabinet/Border Force only, same mod who applied it
    if ":bedtime:" in emoji_str:
        has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
        if has_role:
            message_author = reaction.message.author
            try:
                sticker_message_info = sticker_messages.get(reaction.message.id)
                if not sticker_message_info:
                    return
                sticker_message_id, initiating_mod_id = sticker_message_info
                if initiating_mod_id != user.id:
                    logger.info(f"Bedtime reaction removal ignored as {user} did not initiate the timeout.")
                    return
                reason = f"Bedtime timeout removed by {user.name}#{user.discriminator}."
                await message_author.timeout(None, reason=reason)
                logger.info(f"Bedtime timeout for user {message_author} was removed by {user}.")
                sticker_message = await reaction.message.channel.fetch_message(sticker_message_id)
                await sticker_message.delete()
                del sticker_messages[reaction.message.id]
            except Exception as e:
                logger.error(f"Failed to remove bedtime timeout for user {message_author}: {e}")
        return

    if getattr(reaction.emoji, "id", None) == OGGERSGLARE_EMOJI_ID:
        has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
        if has_role:
            message_author = reaction.message.author
            try:
                sticker_message_info = sticker_messages.get(reaction.message.id)
                if not sticker_message_info:
                    return
                sticker_message_id, initiating_mod_id = sticker_message_info
                if initiating_mod_id != user.id:
                    return
                reason = f"Oggersglare timeout removed by {user.name}#{user.discriminator}."
                await message_author.timeout(None, reason=reason)
                sticker_message = await reaction.message.channel.fetch_message(sticker_message_id)
                await sticker_message.delete()
                del sticker_messages[reaction.message.id]
            except Exception as e:
                logger.error(f"Failed to remove oggersglare timeout/sticker for {message_author}: {e}")
        return

    if ":Shut:" in emoji_str:
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
            if bonus > 0:
                if add_bb(member.id, bonus, reason=f"Stage Participation Reward ({int(elapsed)//60}m)"):
                    await award_badge_with_notify(member._state._get_client(), member.id, 'stage_fan')
                    
                    if track_party_animal(member.id) >= 5:
                        await award_badge_with_notify(member._state._get_client(), member.id, 'party_animal')
                        
                    logger.info(f"[STAGE] +{bonus} UKP → User {member} for leaving stage {before.channel.name}")
                else:
                    logger.error(f"[STAGE] Bank insufficient for {bonus} UKP reward for User {member}.")
                    logger.error(f"[STAGE] Failed to withdraw {bonus} UKP from BankManager for User {member}. Insufficient funds or database error.")
                    # Keep their time accumulated so they don't lose it if the bank is broke
                    stage_join_times[member.id] = start

    # --- VC and Screenshare Badges ---
    if not hasattr(client, 'vc_starts'): client.vc_starts = {}
    if not hasattr(client, 'stream_starts'): client.stream_starts = {}

    # VC Legend: 1 hour in VC with others
    if after.channel and not before.channel:
        if len(after.channel.members) > 1:
            client.vc_starts[member.id] = discord.utils.utcnow()
    elif before.channel and not after.channel:
        start = client.vc_starts.pop(member.id, None)
        if start:
            if (discord.utils.utcnow() - start).total_seconds() >= 3600:
                await award_badge_with_notify(client, member.id, 'vc_legend')
    
    # Screensharer: 30 mins screenshare
    if after.self_video and not before.self_video:
        client.stream_starts[member.id] = discord.utils.utcnow()
    elif before.self_video and not after.self_video:
        start = client.stream_starts.pop(member.id, None)
        if start:
            if (discord.utils.utcnow() - start).total_seconds() >= 1800:
                await award_badge_with_notify(client, member.id, 'screensharer')


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
    client = stage_instance.guild._state._get_client()
    client.stage_events.add(stage_instance.channel.id)
    
    # Backfill join times for everyone already in the channel
    now = discord.utils.utcnow()
    if not hasattr(client, 'stage_join_times'):
        client.stage_join_times = {}
        
    for member in stage_instance.channel.members:
        if member.id not in client.stage_join_times:
            client.stage_join_times[member.id] = now
            logger.info(f"[STAGE] Start-up backfill: {member} in {stage_instance.channel.name}")


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
                if add_bb(m.id, bonus, reason=f"Stage Participation Reward ({int(secs)//60}m)"):
                    await award_badge_with_notify(client, m.id, 'stage_fan')
                    
                    if track_party_animal(m.id) >= 5:
                        await award_badge_with_notify(client, m.id, 'party_animal')
                        
                    logger.info(f"[STAGE END] +{bonus} UKP → User {m.id} for stage end in {stage_instance.channel.name}.")
                    total_awarded_on_delete += bonus
                else:
                    logger.error(f"[STAGE END] Bank insufficient for {bonus} UKP reward for User {m}.")
                    # Reinsert them into the cache so they retain their stage time
                    client.stage_join_times[m.id] = start_time_utc

    if total_awarded_on_delete > 0:
        _update_daily_metric_file(current_date_str, "stage_rewards_total", total_awarded_on_delete)
        logger.info(f"[STAGE END] Added {total_awarded_on_delete} to stage_rewards_total for {current_date_str} from instance delete.")

    client.stage_events.discard(ch_id)
