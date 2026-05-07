import json
import discord
from discord import app_commands, Interaction, Member, TextChannel, Embed
from datetime import datetime, timedelta
import os
import pytz
import inspect
import asyncio
from functools import wraps
from config import *
from lib.bot.commands import *
from lib.core.utils import post_summary_helper, validate_and_format_date, generate_rank_card
from lib.core.discord_helpers import has_role, has_any_role, toggle_user_role, restrict_channel_for_new_members, send_embed_to_channels, edit_voice_channel_members
from lib.core.file_operations import load_whitelist, save_whitelist, set_file_status, is_file_status_active
from lib.features.summary import post_summary
from lib.economy.economy_manager import get_shutcoins, set_shutcoins
from lib.economy.prediction_system import PredAdminView, PredSelectView, PredictionCreateModal, PredictionScheduleModal
from lib.economy.economy_manager import get_bb, set_bb, add_bb, remove_bb
from typing import Optional
from commands.economy.shop import handle_shop_command
from commands.economy.auction import handle_auction_create_command, handle_auction_list_command, handle_auction_end_command
from commands.economy.bank_commands import (
    handle_bank_status_command
)
from commands.social.medal_table import handle_medal_table_command
try:
    from commands.social.rank_equip import handle_rank_equip_command
except ImportError:
    pass
try:
    from commands.economy.wager import handle_wager_command
except ImportError:
    pass
from commands.economy.wager import handle_wager_command

def define_commands(tree, client):
    def command(name: str, description: str, checks: list = None):
        def decorator(func):
            async def wrapper(*args, **kwargs):
                interaction: Interaction = args[0]
                if checks:
                    for check in checks:
                        if not check(interaction):
                            await interaction.response.send_message(
                                "You do not have permission to use this command.", ephemeral=True
                            )
                            return
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                param_str = ", ".join(
                    f"{name}={value}" for name, value in bound_args.arguments.items() if name != "interaction"
                )
                channel = interaction.client.get_channel(CHANNELS.BOT_USAGE_LOG)
                if channel:
                    uk_tz = pytz.timezone("Europe/London")
                    now = datetime.now(uk_tz).strftime("%Y-%m-%d %H:%M:%S")
                    await channel.send(
                        f"{now} - {interaction.user} (ID {interaction.user.id}) used /{interaction.command.name} in {interaction.channel.mention} with args: {param_str}"
                    )
                return await func(*args, **kwargs)
            wrapper.__signature__ = inspect.signature(func)
            return tree.command(name=name, description=description)(wrapper)
        return decorator

    @app_commands.context_menu(name="Quote Message")
    async def quote_context_menu(interaction: Interaction, message: discord.Message):
        from commands.social.quote import handle_quote_context_menu
        await handle_quote_context_menu(interaction, message)

    tree.add_command(quote_context_menu)

    @app_commands.context_menu(name="Add to Hall of Fame")
    async def hof_context_menu(interaction: Interaction, message: discord.Message):
        from commands.social.hof import handle_hof_context_menu
        await handle_hof_context_menu(interaction, message)

    tree.add_command(hof_context_menu)

    @command("role-manage", "Manages user roles by assigning a specified role to members who don't have it")
    async def role_management(interaction: Interaction, role_name: str):
        if interaction.user.id != USERS.OGGERS:
            return
        await updateRoleAssignments(interaction, role_name)

    @command("colour-palette", "Generates a colour palette from an image")
    async def colour_palette(interaction: Interaction, attachment_url: str):
        await interaction.response.defer()
        await colourPalette(interaction, attachment_url)

    @command("gridify", "Adds a pixel art grid overlay to an image")
    async def gridify_command(interaction: Interaction, attachment_url: str):
        await interaction.response.defer()
        await gridify(interaction, attachment_url)

    @command("role-react", "Adds a reaction role to a message", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def role_react_command(interaction: Interaction):
        await persistantRoleButtons(interaction)

    @command("screenshot-canvas", "Takes a screenshot of the current canvas")
    async def screenshot_canvas(interaction: Interaction, x: int = -770, y: int = 7930):
        await interaction.response.defer()
        await screenshotCanvas(interaction, x, y)

    @command("iceberg", "Shows the iceberg image")
    async def show_iceberg_command(interaction: Interaction):
        await interaction.response.defer()
        await show_iceberg(interaction)

    @command("politics-permit", "Allows a new user to speak in politics before the 7-day cooldown", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def politics_permit(interaction: Interaction, user: Member):
        from lib.core.utils import load_whitelist, save_whitelist
        current_whitelist = load_whitelist()
        if user.id not in current_whitelist:
            current_whitelist.append(user.id)
            save_whitelist(current_whitelist)
            from lib.bot.event_handlers import set_politics_whitelist
            set_politics_whitelist(current_whitelist)
            await interaction.response.send_message(f"{user.mention} has been permitted to speak in politics.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.mention} is already permitted to speak in politics.", ephemeral=True)

    @command("post-daily-summary", "Posts the daily summary in the current channel for a specific date", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_daily_summary(interaction: Interaction, date: str = None):
        await interaction.response.defer(ephemeral=True)
        formatted_date = await validate_and_format_date(interaction, date)
        if formatted_date is None:
            return
        summary_file_path = f"daily_summaries/daily_summary_{formatted_date}.json"
        if not os.path.exists(summary_file_path):
            await interaction.followup.send(f"No summary available for {formatted_date}.", ephemeral=True)
            return
        await post_summary(client, interaction.channel.id, "daily", interaction.channel, formatted_date)
        await interaction.followup.send(f"Posted daily summary for {formatted_date}.", ephemeral=True)

    @command("post-last-weekly-summary", "Posts the most recently completed Monday–Sunday.", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_last_weekly_summary(interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        await post_summary_helper(interaction, "weekly")

    @command("post-last-monthly-summary", "Posts last month's monthly summary.", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_last_monthly_summary(interaction: Interaction):
        await interaction.response.defer(ephemeral=True)
        await post_summary_helper(interaction, "monthly")

    @command("politics-ban", "Toggles politics ban for a member", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def politics_ban_command(interaction: Interaction, user: Member):
        from config import ROLES
        role = interaction.guild.get_role(ROLES.POLITICS_BAN)
        if not role:
            await interaction.response.send_message(f"Role with ID {ROLES.POLITICS_BAN} not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, role)

    def has_roast_access(interaction):
        """Check if user has roast command access (including purchased access)"""
        user = interaction.user
        # Check standard roles
        standard_roles = [ROLES.SERVER_BOOSTER, ROLES.BORDER_FORCE, ROLES.CABINET, ROLES.MINISTER, ROLES.PCSO]
        if has_any_role(interaction, standard_roles):
            return True

        # Check for purchased "Roast Access" role
        for role in user.roles:
            if role.name == "Roast Access":
                return True

        return False

    @command("roast", "Roast a user based on recent messages in a channel", checks=[has_roast_access])
    async def roast_command(interaction: Interaction, channel: TextChannel = None, user: Member = None):
        await roast(interaction, channel, user)

    @command("glaze", "Overly praise a user based on recent messages (Oggers only)", checks=[lambda i: i.user.id == USERS.OGGERS])
    async def glaze_command(interaction: Interaction, channel: TextChannel = None, user: Member = None):
        await glaze(interaction, channel, user)

    @command("vc-control", "Toggles server mute/deafen perms for a user", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def vc_control(interaction: Interaction, user: Member):
        await toggleMuteDeafenPermissions(interaction, user)

    @command("vc-ban", "Toggles the VC Ban role for a user", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def vc_ban_command(interaction: Interaction, user: Member):
        await vc_ban(interaction, user)

    @command("video-ban", "Toggles the Video Ban role for a user", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def video_ban_command(interaction: Interaction, user: Member):
        await video_ban(interaction, user)

    @command("setup-announcement", "Setup an announcement with optional role buttons", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def setup_announcement(interaction: Interaction, channel: TextChannel):
        await setup_announcement_command(interaction, channel)

    @command("lockdown-vcs", "Locks down all voice channels", checks=[lambda i: has_any_role(i, [ROLES.CABINET])])
    async def lockdown_vcs_command(interaction: Interaction):
        await lockdown_vcs(interaction)

    @command("end-lockdown-vcs", "Ends the lockdown on all voice channels", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def end_lockdown_vcs_command(interaction: Interaction):
        await end_lockdown_vcs(interaction)

    @command("toggle-anti-raid", "Toggles automatic timeout and quarantine for new joins", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def toggle_anti_raid_command(interaction: Interaction):
        await toggle_anti_raid(interaction)

    @command("toggle-quarantine", "Add or remove the quarantine role from a user.", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def toggle_quarantine_command(interaction: Interaction, user: Member):
        quarantine_role = interaction.guild.get_role(962009285116710922)
        if not quarantine_role:
            await interaction.response.send_message("Quarantine role not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, quarantine_role)

    @command("embed-perms", "Toggles embed perms for a member", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def embed_perms_command(interaction: Interaction, user: Member):
        from config import ROLES
        role = interaction.guild.get_role(ROLES.EMBED_PERMS)
        if not role:
            await interaction.response.send_message(f"Role with ID {ROLES.EMBED_PERMS} not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, role)

    @command("archive-channel", "Archive the current channel.", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def archive_channel_command(interaction: Interaction, seconds: int = 86400):
        await interaction.response.defer()
        await archive_channel(interaction, interaction.client, seconds)

    @command("rank", "Displays your XP and rank in the server")
    async def rank_command(interaction: Interaction, member: Member = None):
        await interaction.response.defer()
        if member is None:
            member = interaction.user
        file = await generate_rank_card(interaction, member)
        await interaction.followup.send(file=file)

    @command("leaderboard", "Displays a paginated leaderboard of top XP holders (in increments of 30).")
    async def leaderboard_command(interaction: Interaction):
        await interaction.response.defer()
        if not hasattr(client, "xp_system"):
            from lib.features.xp_system import XPSystem
            client.xp_system = XPSystem(client)

        await client.xp_system.handle_leaderboard_command(interaction)

    @command("rank-equip", "Equip your purchased rank backgrounds and color themes.")
    async def rank_equip_command(interaction: Interaction):
        await handle_rank_equip_command(interaction)

    # if SHUTCOIN_ENABLED:
    #     @command("set-shutcoins", "Sets a user's total Shutcoins.", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    #     async def set_shutcoins_command(interaction: Interaction, user: Member, amount: int):
    #         old_amount = get_shutcoins(user.id)
    #         set_shutcoins(user.id, amount)
    #         new_amount = get_shutcoins(user.id)
    #         embed = Embed(title="Shutcoin Update", description=f"{user.mention}'s Shutcoins were updated from {old_amount} to {new_amount}")
    #         embed.set_footer(text=f"by {interaction.user.display_name}")
    #         await interaction.response.send_message(embed=embed)

    @command("pred-create", "Create a UKPence prediction", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_create(interaction: Interaction):
        await interaction.response.send_modal(PredictionCreateModal())


    @command("pred-admin", "Lock, resolve, or draw an existing UKPence prediction", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_admin(interaction: Interaction, message_id: Optional[str] = None):
        if message_id:
            try:
                mid = int(message_id)
            except ValueError:
                return await interaction.response.send_message("Invalid message ID.", ephemeral=True)
            
            p = interaction.client.predictions.get(mid)
            if not p:
                return await interaction.response.send_message("Unknown prediction ID.", ephemeral=True)
            view = PredAdminView(p, interaction.client)
            await interaction.response.send_message(f"Managing: **{p.title}**", view=view, ephemeral=True)
        else:
            all_preds = list(interaction.client.predictions.values())
            if not all_preds:
                return await interaction.response.send_message("There are no active predictions.", ephemeral=True)
            
            view = PredSelectView(all_preds, interaction.client)
            await interaction.response.send_message("Select a prediction to manage:", view=view, ephemeral=True)

    @command("pred-schedule", "Schedule a UKPence prediction to post later in a chosen channel", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_schedule(interaction: Interaction, channel: TextChannel):
        await interaction.response.send_modal(PredictionScheduleModal(channel.id))

    @command("pred-scheduled-list", "List pending scheduled predictions", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_scheduled_list(interaction: Interaction):
        from database import DatabaseManager
        rows = DatabaseManager.fetch_all(
            "SELECT id, channel_id, title, scheduled_ts, duration_minutes, creator_id FROM scheduled_predictions WHERE status = 'pending' ORDER BY scheduled_ts ASC"
        )
        if not rows:
            return await interaction.response.send_message("No pending scheduled predictions.", ephemeral=True)

        lines = ["**Pending Scheduled Predictions:**"]
        for sched_id, channel_id, title, scheduled_ts, duration, creator_id in rows:
            short_title = title if len(title) <= 60 else title[:57] + "..."
            lines.append(
                f"`#{sched_id}` <t:{scheduled_ts}:F> (<t:{scheduled_ts}:R>) in <#{channel_id}> "
                f"by <@{creator_id}> — duration {duration}m — *{discord.utils.escape_markdown(short_title)}*"
            )
        out = "\n".join(lines)
        if len(out) > 2000:
            out = out[:1990] + "\n…"
        await interaction.response.send_message(out, ephemeral=True)

    @command("pred-scheduled-cancel", "Cancel a pending scheduled prediction by id", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_scheduled_cancel(interaction: Interaction, sched_id: int):
        from database import DatabaseManager
        row = DatabaseManager.fetch_one(
            "SELECT status FROM scheduled_predictions WHERE id = ?", (sched_id,)
        )
        if not row:
            return await interaction.response.send_message(f"No scheduled prediction with id {sched_id}.", ephemeral=True)
        if row[0] != 'pending':
            return await interaction.response.send_message(
                f"Scheduled prediction #{sched_id} is not pending (status={row[0]}).", ephemeral=True
            )
        DatabaseManager.execute(
            "UPDATE scheduled_predictions SET status = 'cancelled' WHERE id = ?", (sched_id,)
        )
        scheduler = getattr(interaction.client, "scheduler", None)
        if scheduler is not None:
            try:
                scheduler.remove_job(f"scheduled_pred_{sched_id}")
            except Exception:
                pass
        await interaction.response.send_message(f"🗑️ Scheduled prediction #{sched_id} cancelled.", ephemeral=True)

    @command("preds-to-resolve", "Shows all unresolved predictions in memory", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def preds_to_resolve(interaction: Interaction):
        unresolved = list(interaction.client.predictions.values())
        if not unresolved:
            await interaction.response.send_message("✅ All predictions have been resolved.", ephemeral=True)
            return

        header = "**Unresolved Predictions:**"
        lines = []
        for p in unresolved:
            status = "🔒 Locked" if p.locked else "🔓 Open"
            guild_id = interaction.guild.id if interaction.guild else GUILD_ID
            link = f"https://discord.com/channels/{guild_id}/{p.channel_id or interaction.channel.id}/{p.msg_id}"
            lines.append(f"`{p.title[:40]}` | `{status}` | [jump]({link})")

        chunks = []
        current = header
        for line in lines:
            if len(current) + len(line) + 1 > 2000:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line
        chunks.append(current)

        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    @command("pay", "Transfer UKPence to another member")
    async def pay_command(interaction: Interaction, recipient: Member, amount: int):
        if amount <= 0:
            return await interaction.response.send_message("Enter a positive amount.", ephemeral=True)
        if recipient.id == interaction.user.id:
            return await interaction.response.send_message("You cannot pay yourself.", ephemeral=True)
        if not remove_bb(interaction.user.id, amount, reason=f"/pay to {recipient.display_name}", to_bank=False):
            return await interaction.response.send_message("Insufficient UKPence.", ephemeral=True)
        add_bb(recipient.id, amount, reason=f"/pay from {interaction.user.display_name}", from_bank=False)
        embed = Embed(
            title="UKPence Transfer",
            description=f"{interaction.user.mention} paid **{amount:,}** UKPence to {recipient.mention}",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

        # Database logging
        from database import DatabaseManager
        import time
        now_ts = int(time.time())
        DatabaseManager.execute(
            "INSERT INTO pay_transfers (timestamp, payer_id, recipient_id, amount) VALUES (?, ?, ?, ?)",
            (now_ts, str(interaction.user.id), str(recipient.id), amount)
        )
        
        # Check philanthropist badge
        total_paid_res = DatabaseManager.fetch_one(
            "SELECT SUM(amount) FROM pay_transfers WHERE payer_id = ?", 
            (str(interaction.user.id),)
        )
        if total_paid_res and total_paid_res[0] and total_paid_res[0] >= 10000:
            from lib.bot.event_handlers import award_badge_with_notify
            await award_badge_with_notify(interaction.client, interaction.user.id, 'philanthropist')

        # Check Valentine badge
        uk_tz = pytz.timezone("Europe/London")
        now = datetime.now(uk_tz)
        if now.month == 2 and now.day == 14:
            from lib.bot.event_handlers import award_badge_with_notify
            await award_badge_with_notify(interaction.client, interaction.user.id, 'valentine')

        pay_log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "payer_id": str(interaction.user.id),
            "recipient_id": str(recipient.id),
            "amount": amount
        }
        from config import PAY_LOG_FILE
        # ... logic for JSON backup ...

    @command("wager", "Wager UKPence against another user on a custom topic")
    async def wager_command(interaction: Interaction, opponent: Member, amount: int, topic: str):
        await handle_wager_command(interaction, opponent, amount, topic)

    @command("richlist", "Displays a leaderboard of users with the most UKPence")
    async def richlist_command(interaction: Interaction):
        await interaction.response.defer()
        if not hasattr(interaction.client, "xp_system"):
            from lib.features.xp_system import XPSystem
            interaction.client.xp_system = XPSystem(interaction.client)
        await interaction.client.xp_system.handle_richlist_command(interaction)


    @command("ukpeconomy", "Shows the current state of the UKPence economy as an image.")
    async def ukpeconomy_command_def(interaction: Interaction):
        await interaction.response.defer()
        
        file_to_send = await handle_ukpeconomy_command(interaction)
        
        if file_to_send:
            await interaction.followup.send(file=file_to_send)
        else:
            await interaction.followup.send("An error occurred while generating the economy stats.", ephemeral=True)


    @command("toggle-visitor-overnight-mute", "Toggles the overnight mute for visitors", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def toggle_overnight_mute_command(interaction: Interaction):
        await toggle_overnight_mute(interaction)

    @command("shop", "Browse and purchase items with UKPence")
    async def shop_command(interaction: Interaction):
        await handle_shop_command(interaction)

    @command("auction-create", "Create a new auction (Staff only)", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def auction_create_command(interaction: Interaction):
        await handle_auction_create_command(interaction)

    @command("auction-list", "List all active auctions")
    async def auction_list_command(interaction: Interaction):
        await handle_auction_list_command(interaction)

    @command("auction-end", "Manually end an auction (Staff only)", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def auction_end_command(interaction: Interaction, auction_id: int):
        await handle_auction_end_command(interaction, auction_id)

    @command("bank-status", "View server bank status (Staff only)", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def bank_status_command(interaction: Interaction):
        await handle_bank_status_command(interaction)

    @command("medal-table", "Olympic-style leaderboard of badge holders by gold/silver/bronze count")
    async def medal_table_command(interaction: Interaction):
        await handle_medal_table_command(interaction)
