from discord import app_commands, Interaction, Member, TextChannel
from datetime import datetime, timedelta
import os
import pytz
from collections import defaultdict
import inspect
from functools import wraps

from lib.settings import *
from lib.utils import has_any_role, has_role, save_whitelist
from lib.commands import *
from lib.summary import post_summary


def log_usage(func):
    @wraps(func)
    async def wrapper(interaction: Interaction, *args, **kwargs):
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


def define_commands(tree, client):
    @tree.command(
        name="role-manage",
        description="Manages user roles by assigning a specified role to members who don't have it",
    )
    @log_usage
    async def role_management(interaction: Interaction, role_name: str):
        if interaction.user.id != USERS.OGGERS:
            return
        await updateRoleAssignments(interaction, role_name)

    @tree.command(
        name="colour-palette", description="Generates a colour palette from an image"
    )
    @log_usage
    async def colour_palette(interaction: Interaction, attachment_url: str):
        await colourPalette(interaction, attachment_url)

    @tree.command(
        name="gridify", description="Adds a pixel art grid overlay to an image"
    )
    @log_usage
    async def gridify_command(interaction: Interaction, attachment_url: str):
        await gridify(interaction, attachment_url)

    @tree.command(name="role-react", description="Adds a reaction role to a message")
    @log_usage
    async def role_react_command(interaction: Interaction):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await persistantRoleButtons(interaction)

    @tree.command(
        name="screenshot-canvas", description="Takes a screenshot of the current canvas"
    )
    @log_usage
    async def screenshot_canvas(interaction: Interaction, x: int = -770, y: int = 7930):
        await screenshotCanvas(interaction, x, y)

    @tree.command(name="add-to-iceberg", description="Adds text to the iceberg image")
    @log_usage
    async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await add_iceberg_text(interaction, text, level)

    @tree.command(name="show-iceberg", description="Shows the iceberg image")
    @log_usage
    async def show_iceberg_command(interaction: Interaction):
        await show_iceberg(interaction)

    @tree.command(
        name="add-whitelist",
        description="Adds a user to the whitelist for the politics channel",
    )
    @log_usage
    async def add_whitelist_command(interaction: Interaction, user: Member):
        if not has_any_role(
            interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return

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

    @tree.command(
        name="post-daily-summary",
        description="Posts the daily summary in the current channel for a specific date",
    )
    @log_usage
    async def post_daily_summary(interaction: Interaction, date: str = None):
        if not has_role(interaction, ROLES.MINISTER):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return

        if date is None:
            uk_timezone = pytz.timezone("Europe/London")
            date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
        else:
            try:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
                date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                await interaction.response.send_message(
                    "Invalid date format. Please use YYYY-MM-DD.", ephemeral=True
                )
                return

        summary_file_path = f"daily_summaries/daily_summary_{date}.json"
        if not os.path.exists(summary_file_path):
            await interaction.response.send_message(
                f"No summary available for {date}.", ephemeral=True
            )
            return

        await post_summary(
            client, interaction.channel.id, "daily", interaction.channel, date
        )

        await interaction.response.send_message(
            f"Posted daily summary for {date}.", ephemeral=True
        )

    @tree.command(
        name="post-last-weekly-summary",
        description="Posts the most recently completed Monday–Sunday.",
    )
    @log_usage
    async def post_last_weekly_summary(interaction: Interaction):
        if not has_role(interaction, ROLES.MINISTER):
            await interaction.response.send_message(
                "You do not have permission.", ephemeral=True
            )
            return
        uk_timezone = pytz.timezone("Europe/London")
        now = datetime.now(uk_timezone)
        weekday = now.weekday()
        this_monday = now - timedelta(days=weekday)
        date_str = this_monday.strftime("%Y-%m-%d")
        await post_summary(
            client, interaction.channel.id, "weekly", interaction.channel, date_str
        )
        await interaction.response.send_message(
            f"Posted last week's summary using {date_str} (covers the Monday–Sunday prior).",
            ephemeral=True,
        )

    @tree.command(
        name="post-last-monthly-summary",
        description="Posts last month's monthly summary.",
    )
    @log_usage
    async def post_last_monthly_summary(interaction: Interaction):
        if not has_role(interaction, ROLES.MINISTER):
            await interaction.response.send_message(
                "You do not have permission.", ephemeral=True
            )
            return
        uk_timezone = pytz.timezone("Europe/London")
        now = datetime.now(uk_timezone)
        this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        date_str = this_month_start.strftime("%Y-%m-%d")
        await post_summary(
            client, interaction.channel.id, "monthly", interaction.channel, date_str
        )
        await interaction.response.send_message(
            f"Posted last month's monthly summary ({date_str}).", ephemeral=True
        )

    @tree.command(name="politics-ban", description="Toggles politics ban for a member")
    @log_usage
    async def manage_role_command(interaction: Interaction, user: Member):
        if not has_any_role(
            interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return

        role = interaction.guild.get_role(ROLES.POLITICS_BAN)
        if not role:
            await interaction.response.send_message(
                f"Role with ID {role_id} not found.", ephemeral=True
            )
            return

        if role in user.roles:
            await user.remove_roles(role)
            await interaction.response.send_message(
                f"Role {role.name} has been removed from {user.mention}.",
                ephemeral=True,
            )
        else:
            await user.add_roles(role)
            await interaction.response.send_message(
                f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True
            )

    @tree.command(
        name="roast", description="Roast a user based on recent messages in a channel"
    )
    @log_usage
    async def summarise(
        interaction: Interaction, channel: TextChannel = None, user: Member = None
    ):
        if not has_any_role(
            interaction,
            [
                ROLES.SERVER_BOOSTER,
                ROLES.BORDER_FORCE,
                ROLES.CABINET,
                ROLES.MINISTER,
                ROLES.PCSO,
            ],
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return

        today = datetime.now().date()
        usage_data = command_usage_tracker[interaction.user.id]

        if interaction.user.id == USERS.OGGERS:
            await roast(interaction, channel, user)
            return

        if usage_data["last_used"] != today:
            usage_data["count"] = 0
            usage_data["last_used"] = today

        if usage_data["count"] >= SUMMARISE_DAILY_LIMIT:
            await interaction.response.send_message(
                f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command",
                ephemeral=True,
            )
            return

        usage_data["count"] += 1

        await roast(interaction, channel, user)

    # @tree.command(name="beef", description="Generate a dramatic fight scenario between two users from chat history.")
    # @log_usage
    # async def summarise(interaction: Interaction, channel: TextChannel = None, user: Member = None):
    #     if not has_any_role(interaction, [ROLES.SERVER_BOOSTER, ROLES.BORDER_FORCE, ROLES.CABINET, ROLES.MINISTER]):
    #         await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
    #         return

    #     today = datetime.now().date()
    #     usage_data = command_usage_tracker[interaction.user.id]

    #     if interaction.user.id == USERS.OGGERS:
    #         await origin_story(interaction, channel, user)
    #         return

    #     if usage_data['last_used'] != today:
    #         usage_data['count'] = 0
    #         usage_data['last_used'] = today

    #     if usage_data['count'] >= SUMMARISE_DAILY_LIMIT:
    #         await interaction.response.send_message(f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command", ephemeral=True)
    #         return

    #     usage_data['count'] += 1

    #     await roast(interaction, channel, user)

    @tree.command(
        name="vc-control", description="Toggles server mute/deafen perms for a user"
    )
    @log_usage
    async def vc_control(interaction: Interaction, user: Member):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await toggleMuteDeafenPermissions(interaction, user)

    @tree.command(
        name="setup-announcement",
        description="Setup an announcement with optional role buttons.",
    )
    @log_usage
    async def setup_announcement(interaction: Interaction, channel: TextChannel):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await setup_announcement_command(interaction, channel)

    @tree.command(name="lockdown-vcs", description="Locks down all voice channels.")
    @log_usage
    async def lockdown_vcs_command(interaction: Interaction):
        if not has_any_role(interaction, [ROLES.CABINET]):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await lockdown_vcs(interaction)

    @tree.command(
        name="end-lockdown-vcs", description="Ends the lockdown on all voice channels."
    )
    @log_usage
    async def end_lockdown_vcs_command(interaction: Interaction):
        if not has_any_role(
            interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await end_lockdown_vcs(interaction)

    @tree.command(
        name="toggle-anti-raid",
        description="Toggles automatic timeout and quarantine for new joins.",
    )
    @log_usage
    async def toggle_anti_raid_command(interaction: Interaction):
        if not has_any_role(
            interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await toggle_anti_raid(interaction)

    @tree.command(
        name="toggle-quarantine",
        description="Add or remove the quarantine role from a user.",
    )
    @log_usage
    async def toggle_quarantine_command(interaction: Interaction, user: Member):
        if not has_any_role(
            interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        quarantine_role = interaction.guild.get_role(962009285116710922)
        if not quarantine_role:
            await interaction.response.send_message(
                "Quarantine role not found.", ephemeral=True
            )
            return
        if quarantine_role in user.roles:
            await user.remove_roles(quarantine_role)
            await interaction.response.send_message(
                f"{user.mention} has been removed from quarantine.", ephemeral=True
            )
        else:
            await user.add_roles(quarantine_role)
            await interaction.response.send_message(
                f"{user.mention} has been placed in quarantine.", ephemeral=True
            )

    @tree.command(name="embed-perms", description="Toggles embed perms for a member")
    @log_usage
    async def manage_role_command(interaction: Interaction, user: Member):
        if not has_any_role(
            interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return

        role = interaction.guild.get_role(ROLES.EMBED_PERMS)
        if not role:
            await interaction.response.send_message(
                f"Role with ID {role_id} not found.", ephemeral=True
            )
            return

        if role in user.roles:
            await user.remove_roles(role)
            await interaction.response.send_message(
                f"Role {role.name} has been removed from {user.mention}.",
                ephemeral=True,
            )
        else:
            await user.add_roles(role)
            await interaction.response.send_message(
                f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True
            )

    @tree.command(name="archive-channel", description="Archive the current channel.")
    @log_usage
    async def archive_channel_command(interaction: Interaction):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message(
                "You do not have permission to use this command.", ephemeral=True
            )
            return
        await archive_channel(interaction, interaction.client)
