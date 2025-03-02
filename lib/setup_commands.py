from discord import app_commands, Interaction, Member, TextChannel
from datetime import datetime, timedelta
import os
import pytz
import inspect
from functools import wraps
from lib.settings import *
from lib.commands import *
from lib.utils import (
    has_any_role,
    has_role,
    toggle_user_role,
    validate_and_format_date,
    handle_roast_command,
    post_summary_helper,
    save_whitelist,
)

def log_usage(func):
    """Decorator to log command usage for HMS Victory."""
    @wraps(func)
    async def wrapper(interaction: Interaction, *args, **kwargs):
        signature = inspect.signature(func)
        bound_args = signature.bind(interaction, *args, **kwargs)
        bound_args.apply_defaults()
        param_str = ", ".join(f"{name}={value}" for name, value in bound_args.arguments.items() if name != "interaction")
        channel = interaction.client.get_channel(CHANNELS.BOT_USAGE_LOG)
        if channel:
            uk_tz = pytz.timezone("Europe/London")
            now = datetime.now(uk_tz).strftime("%Y-%m-%d %H:%M:%S")
            await channel.send(f"{now} - {interaction.user} (ID {interaction.user.id}) used /{interaction.command.name} in {interaction.channel.mention} with args: {param_str}")
        return await func(interaction, *args, **kwargs)
    return wrapper

def define_commands(tree, client):
    """Defines slash commands for HMS Victory."""
    def command(name, description, checks=None):
        def decorator(func):
            async def wrapper(interaction: Interaction, *args, **kwargs):
                if checks:
                    for check in checks:
                        if not check(interaction):
                            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
                            return
                return await func(interaction, *args, **kwargs)
            return tree.command(name=name, description=description)(log_usage(wrapper))
        return decorator

    @command("role-manage", "Manages user roles by assigning a specified role to members who don't have it")
    async def role_management(interaction: Interaction, role_name: str):
        if interaction.user.id != USERS.OGGERS:
            return
        await updateRoleAssignments(interaction, role_name)

    @command("colour-palette", "Generates a colour palette from an image")
    async def colour_palette(interaction: Interaction, attachment_url: str):
        await colourPalette(interaction, attachment_url)

    @command("gridify", "Adds a pixel art grid overlay to an image")
    async def gridify_command(interaction: Interaction, attachment_url: str):
        await gridify(interaction, attachment_url)

    @command("role-react", "Adds a reaction role to a message", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def role_react_command(interaction: Interaction):
        await persistantRoleButtons(interaction)

    @command("screenshot-canvas", "Takes a screenshot of the current canvas")
    async def screenshot_canvas(interaction: Interaction, x: int = -770, y: int = 7930):
        await screenshotCanvas(interaction, x, y)

    @command("add-to-iceberg", "Adds text to the iceberg image", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
        await add_iceberg_text(interaction, text, level)

    @command("show-iceberg", "Shows the iceberg image")
    async def show_iceberg_command(interaction: Interaction):
        await show_iceberg(interaction)

    @command("add-whitelist", "Adds a user to the whitelist for the politics channel", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def add_whitelist_command(interaction: Interaction, user: Member):
        from lib.settings import POLITICS_WHITELISTED_USER_IDS
        if user.id not in POLITICS_WHITELISTED_USER_IDS:
            POLITICS_WHITELISTED_USER_IDS.append(user.id)
            await save_whitelist(POLITICS_WHITELISTED_USER_IDS)
            await interaction.response.send_message(f"{user.mention} has been added to the whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.mention} is already in the whitelist.", ephemeral=True)

    @command("post-daily-summary", "Posts the daily summary in the current channel for a specific date", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_daily_summary(interaction: Interaction, date: str = None):
        formatted_date = await validate_and_format_date(interaction, date)
        if formatted_date is None:
            return
        summary_file_path = f"daily_summaries/daily_summary_{formatted_date}.json"
        if not os.path.exists(summary_file_path):
            await interaction.response.send_message(f"No summary available for {formatted_date}.", ephemeral=True)
            return
        await post_summary(client, interaction.channel.id, "daily", interaction.channel, formatted_date)
        await interaction.response.send_message(f"Posted daily summary for {formatted_date}.", ephemeral=True)

    @command("post-last-weekly-summary", "Posts the most recently completed Monday–Sunday.", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_last_weekly_summary(interaction: Interaction):
        await post_summary_helper(interaction, "weekly")

    @command("post-last-monthly-summary", "Posts last month's monthly summary.", checks=[lambda i: has_role(i, ROLES.MINISTER)])
    async def post_last_monthly_summary(interaction: Interaction):
        await post_summary_helper(interaction, "monthly")

    @command("politics-ban", "Toggles politics ban for a member", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE])])
    async def politics_ban_command(interaction: Interaction, user: Member):
        from lib.settings import ROLES
        role = interaction.guild.get_role(ROLES.POLITICS_BAN)
        if not role:
            await interaction.response.send_message(f"Role with ID {ROLES.POLITICS_BAN} not found.", ephemeral=True)
            return
        if role in user.roles:
            await user.remove_roles(role)
            await interaction.response.send_message(f"Role {role.name} has been removed from {user.mention}.", ephemeral=True)
        else:
            await user.add_roles(role)
            await interaction.response.send_message(f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True)

    @command("roast", "Roast a user based on recent messages in a channel", checks=[lambda i: has_any_role(i, [ROLES.SERVER_BOOSTER, ROLES.BORDER_FORCE, ROLES.CABINET, ROLES.MINISTER, ROLES.PCSO])])
    async def roast_command(interaction: Interaction, channel: TextChannel = None, user: Member = None):
        await handle_roast_command(interaction, channel, user)

    @command("vc-control", "Toggles server mute/deafen perms for a user", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def vc_control(interaction: Interaction, user: Member):
        await toggleMuteDeafenPermissions(interaction, user)

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
        from lib.settings import ROLES
        role = interaction.guild.get_role(ROLES.EMBED_PERMS)
        if not role:
            await interaction.response.send_message(f"Role with ID {ROLES.EMBED_PERMS} not found.", ephemeral=True)
            return
        await toggle_user_role(interaction, user, role)

    @command("archive-channel", "Archive the current channel.", checks=[lambda i: has_any_role(i, [ROLES.MINISTER, ROLES.CABINET])])
    async def archive_channel_command(interaction: Interaction, seconds: int = 86400):
        await archive_channel(interaction, interaction.client, seconds)
