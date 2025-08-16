from discord import app_commands, Interaction, Member, TextChannel, Embed
from datetime import datetime, timedelta
import os
import pytz
import inspect
import asyncio
from functools import wraps
from config import *
from lib.commands import *
from lib.utils import *
from lib.summary import post_summary
from lib.shutcoin import get_shutcoins, set_shutcoins
from lib.prediction_system import Prediction, BetButtons, prediction_embed, _save, PredAdminView
from lib.ukpence import get_bb, set_bb, add_bb, remove_bb
from typing import Optional

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

    @command("add-to-iceberg", "Adds text to the iceberg image", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
        await interaction.response.defer()
        await add_iceberg_text(interaction, text, level)

    @command("show-iceberg", "Shows the iceberg image")
    async def show_iceberg_command(interaction: Interaction):
        await interaction.response.defer()
        await show_iceberg(interaction)

    @command("add-whitelist", "Adds a user to the whitelist for the politics channel", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def add_whitelist_command(interaction: Interaction, user: Member):
        from lib.utils import load_whitelist, save_whitelist
        current_whitelist = load_whitelist()
        if user.id not in current_whitelist:
            current_whitelist.append(user.id)
            save_whitelist(current_whitelist)
            await interaction.response.send_message(f"{user.mention} has been added to the whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.mention} is already in the whitelist.", ephemeral=True)

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

    @command("roast", "Roast a user based on recent messages in a channel", checks=[lambda i: has_any_role(i, [ROLES.SERVER_BOOSTER, ROLES.BORDER_FORCE, ROLES.CABINET, ROLES.MINISTER, ROLES.PCSO])])
    async def roast_command(interaction: Interaction, channel: TextChannel = None, user: Member = None):
        await roast(interaction, channel, user)

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
            from lib.xp_system import XPSystem
            client.xp_system = XPSystem()

        await client.xp_system.handle_leaderboard_command(interaction)

    if SHUTCOIN_ENABLED:
        @command("set-shutcoins", "Sets a user's total Shutcoins.", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
        async def set_shutcoins_command(interaction: Interaction, user: Member, amount: int):
            old_amount = get_shutcoins(user.id)
            set_shutcoins(user.id, amount)
            new_amount = get_shutcoins(user.id)
            embed = Embed(title="Shutcoin Update", description=f"{user.mention}'s Shutcoins were updated from {old_amount} to {new_amount}")
            embed.set_footer(text=f"by {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed)

    @command("pred-create", "Create a UKPence prediction (duration is in minutes)", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_create(interaction: Interaction, title: str, opt1: str, opt2: str, duration: int = 5):
        end_ts = discord.utils.utcnow().timestamp() + duration * 60
        p = Prediction(0, title, opt1, opt2, end_ts)
        embed, bar = prediction_embed(p)
        msg = await interaction.channel.send(embed=embed, files=[bar], view=BetButtons(p))
        p.msg_id = msg.id
        interaction.client.predictions[msg.id] = p
        _save({k: v.to_dict() for k, v in interaction.client.predictions.items()})
        await interaction.response.send_message("Prediction opened.", ephemeral=True)
        p.msg_id = msg.id
        p.channel_id = msg.channel.id
        interaction.client.predictions[msg.id] = p


    @command("pred-admin", "Lock, resolve, or draw an existing UKPence prediction", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def pred_admin(interaction: Interaction, message_id: str):
        mid = int(message_id)
        p = interaction.client.predictions.get(mid)
        if not p:
            return await interaction.response.send_message("Unknown prediction ID.", ephemeral=True)
        view = PredAdminView(p, interaction.client)
        await interaction.response.send_message("Prediction admin controls", view=view, ephemeral=True)

    @command("preds-to-resolve", "Shows all locked predictions still in memory", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.PCSO])])
    async def preds_to_resolve(interaction: Interaction):
        unresolved = [
            p for p in interaction.client.predictions.values()
            if p.locked
        ]
        if not unresolved:
            await interaction.response.send_message("✅ All predictions have been resolved.", ephemeral=True)
            return

        header = "**Preds left to resolve:**"
        lines = []
        for p in unresolved:
            link = f"https://discord.com/channels/{GUILD_ID}/{p.channel_id or interaction.channel.id}/{p.msg_id}"
            lines.append(f"`{p.title}` | `{p.msg_id}` | [jump]({link})")

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
        if not remove_bb(interaction.user.id, amount):
            return await interaction.response.send_message("Insufficient UKPence.", ephemeral=True)
        add_bb(recipient.id, amount)
        embed = Embed(
            title="UKPence Transfer",
            description=f"{interaction.user.mention} paid **{amount:,}** UKPence to {recipient.mention}",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

        pay_log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "payer_id": str(interaction.user.id),
            "recipient_id": str(recipient.id),
            "amount": amount
        }
        PAY_LOG_FILE = "pay_log.json"

        pay_data = []
        if os.path.exists(PAY_LOG_FILE):
            with open(PAY_LOG_FILE, "r") as f_log_read:
                try:
                    pay_data = json.load(f_log_read)
                except json.JSONDecodeError:
                    pass
        pay_data.append(pay_log_entry)
        with open(PAY_LOG_FILE, "w") as f_log_write:
            json.dump(pay_data, f_log_write, indent=4)

    @command("richlist", "Displays a leaderboard of users with the most UKPence")
    async def richlist_command(interaction: Interaction):
        await interaction.response.defer()
        if not hasattr(interaction.client, "xp_system"):
            from lib.xp_system import XPSystem
            interaction.client.xp_system = XPSystem()
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