from discord import app_commands, Interaction, Member, TextChannel
from datetime import datetime, timedelta
import os
import pytz
import inspect
from functools import wraps
from typing import Any

from lib.settings import *
from lib.utils import *
from lib.commands import *
from lib.summary import post_summary


def log_usage(func):
    """Decorator to log command usage"""

    @wraps(func)
    async def wrapper(interaction: Interaction, *args: Any, **kwargs: Any):
        signature = inspect.signature(func)
        bound_args = signature.bind(interaction, *args, **kwargs)
        bound_args.apply_defaults()

        param_str = ", ".join(
            f"{name}={value}"
            for name, value in bound_args.arguments.items()
            if name != "interaction"
        )

        channel = interaction.client.get_channel(CHANNELS.BOT_USAGE_LOG)
        if channel:
            uk_tz = pytz.timezone("Europe/London")
            now = datetime.now(uk_tz).strftime("%Y-%m-%d %H:%M:%S")
            await channel.send(
                f"{now} - {interaction.user} (ID {interaction.user.id}) "
                f"used /{interaction.command.name} in {interaction.channel.mention} "
                f"with args: {param_str}"
            )
        return await func(interaction, *args, **kwargs)

    return wrapper


def command_group(name, description, checks=None):
    """Decorator to create command groups with optional permission checks"""
    if checks is None:
        checks = []

    def decorator(func):
        @app_commands.command(name=name, description=description)
        @log_usage
        async def wrapper(interaction: Interaction, *args, **kwargs):
            for check in checks:
                if not check(interaction):
                    await interaction.response.send_message(
                        "You do not have permission to use this command.", ephemeral=True
                    )
                    return
            await func(interaction, *args, **kwargs)
        return wrapper
    return decorator



def is_owner(interaction: Interaction):
    return interaction.user.id == USERS.OGGERS

def has_required_roles(interaction: Interaction, roles: list[int]):
    return has_any_role(interaction, roles)



def define_commands(tree, client):
    """Defines all slash commands for HMS Vic"""

    @command_group("colour-palette", "Generates a colour palette from an image")
    async def colour_palette_command(interaction: Interaction, attachment_url: str):
        await colourPalette(interaction, attachment_url)

    @command_group("gridify", "Adds a pixel art grid overlay to an image")
    async def gridify_command(interaction: Interaction, attachment_url: str):
        await gridify(interaction, attachment_url)

    @command_group("add-to-iceberg", "Adds text to the iceberg image", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
        await add_iceberg_text(interaction, text, level)

    @command_group("show-iceberg", "Shows the iceberg image")
    async def show_iceberg_command(interaction: Interaction):
        await show_iceberg(interaction)
    
    @command_group("screenshot-canvas", "Takes a screenshot of the current canvas")
    async def screenshot_canvas(interaction: Interaction, x: int = -770, y: int = 7930):
        await screenshotCanvas(interaction, x, y)

    @command_group("role-manage", "Manages user roles", checks=[is_owner])
    async def role_management(interaction: Interaction, role_name: str):
        await updateRoleAssignments(interaction, role_name)
    
    @command_group("role-react", "Adds a reaction role to a message", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def role_react_command(interaction: Interaction):
        await persistantRoleButtons(interaction)

    @command_group("politics-ban", "Toggles politics ban for a member", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def manage_role_command(interaction: Interaction, user: Member):
        role = interaction.guild.get_role(ROLES.POLITICS_BAN)
        if not role:
            await interaction.response.send_message(f"Role with ID {ROLES.POLITICS_BAN} not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, role)
        
    @command_group("embed-perms", "Toggles embed perms for a member", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def manage_embed_perms_command(interaction: Interaction, user: Member):
        role = interaction.guild.get_role(ROLES.EMBED_PERMS)
        if not role:
            await interaction.response.send_message(f"Role with ID {ROLES.EMBED_PERMS} not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, role)

    @command_group("vc-control", "Toggles server mute/deafen perms for a user", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def vc_control(interaction: Interaction, user: Member):
        await toggleMuteDeafenPermissions(interaction, user)

    @command_group("add-whitelist", "Adds a user to the whitelist for the politics channel", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def add_whitelist_command(interaction: Interaction, user: Member):
        if user.id not in POLITICS_WHITELISTED_USER_IDS:
            POLITICS_WHITELISTED_USER_IDS.append(user.id)
            save_whitelist(POLITICS_WHITELISTED_USER_IDS)
            await interaction.response.send_message(
                f"{user.mention} has been added to the whitelist.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{user.mention} is already in the whitelist.", ephemeral=True
            )

    @command_group("post-daily-summary", "Posts the daily summary", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_daily_summary(interaction: Interaction, date: str = None):
        date = await validate_and_format_date(interaction, date)
        if not date: return

        summary_file_path = f"daily_summaries/daily_summary_{date}.json"
        if not os.path.exists(summary_file_path):
            await interaction.response.send_message(
                f"No summary available for {date}.", ephemeral=True
            )
            return

        await post_summary(client, interaction.channel.id, "daily", interaction.channel, date)
        await interaction.response.send_message(f"Posted daily summary for {date}.", ephemeral=True)

    @command_group("post-last-weekly-summary", "Posts the most recently completed Monday-Sunday", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_last_weekly_summary(interaction: Interaction):
       await post_summary_helper(interaction, "weekly")


    @command_group("post-last-monthly-summary", "Posts last month's summary", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_last_monthly_summary(interaction: Interaction):
        await post_summary_helper(interaction, "monthly")

    @command_group("roast", "Roast a user based on recent messages in a channel", checks=[lambda i: has_required_roles(i,[ROLES.SERVER_BOOSTER,ROLES.BORDER_FORCE,ROLES.CABINET,ROLES.MINISTER,ROLES.PCSO,])])
    async def summarise(interaction: Interaction, channel: TextChannel = None, user: Member = None):
        await handle_roast_command(interaction, channel, user)


    @command_group("setup-announcement", "Setup an announcement with optional role buttons.", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def setup_announcement(interaction: Interaction, channel: TextChannel):
        await setup_announcement_command(interaction, channel)
        

    @command_group("lockdown-vcs", "Locks down all voice channels.", checks=[lambda i: has_required_roles(i, [ROLES.CABINET])])
    async def lockdown_vcs_command(interaction: Interaction):
        await lockdown_vcs(interaction)

    @command_group("end-lockdown-vcs", "Ends the lockdown on all voice channels.", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def end_lockdown_vcs_command(interaction: Interaction):
        await end_lockdown_vcs(interaction)
        
    @command_group("toggle-anti-raid", "Toggles automatic timeout and quarantine for new joins.", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def toggle_anti_raid_command(interaction: Interaction):
        await toggle_anti_raid(interaction)

    @command_group("toggle-quarantine", "Add or remove the quarantine role from a user.", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def toggle_quarantine_command(interaction: Interaction, user: Member):
        quarantine_role = interaction.guild.get_role(962009285116710922)
        if not quarantine_role:
            await interaction.response.send_message("Quarantine role not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, quarantine_role)


    @command_group("archive-channel", "Archive the current channel.", checks=[lambda i: has_required_roles(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def archive_channel_command(interaction: Interaction, seconds: int = 86400):
        await archive_channel(interaction, interaction.client, seconds)
