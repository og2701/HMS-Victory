from discord import app_commands, Interaction, Member, TextChannel
from datetime import datetime
import os
import pytz
from collections import defaultdict

from lib.settings import *
from lib.utils import has_any_role, has_role, save_whitelist
from lib.commands import *
from lib.summary import post_summary


def define_commands(tree, client):
    @tree.command(
        name="role-manage",
        description="Manages user roles by assigning a specified role to members who don't have it",
    )
    async def role_management(interaction: Interaction, role_name: str):
        await updateRoleAssignments(interaction, role_name)

    @tree.command(
        name="colour-palette", description="Generates a colour palette from an image"
    )
    async def colour_palette(interaction: Interaction, attachment_url: str):
        await colourPalette(interaction, attachment_url)

    @tree.command(name="gridify", description="Adds a pixel art grid overlay to an image")
    async def gridify_command(interaction: Interaction, attachment_url: str):
        await gridify(interaction, attachment_url)

    @tree.command(name="role-react", description="Adds a reaction role to a message")
    async def role_react_command(interaction: Interaction):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        await persistantRoleButtons(interaction)

    @tree.command(name="screenshot-canvas", description="Takes a screenshot of the current canvas")
    async def screenshot_canvas(interaction: Interaction, x: int = -770, y: int = 7930):
        await screenshotCanvas(interaction, x, y)

    @tree.command(name="add-to-iceberg", description="Adds text to the iceberg image")
    async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        await add_iceberg_text(interaction, text, level)

    @tree.command(name="show-iceberg", description="Shows the iceberg image")
    async def show_iceberg_command(interaction: Interaction):
        await show_iceberg(interaction)

    @tree.command(name="add-whitelist", description="Adds a user to the whitelist for the politics channel")
    async def add_whitelist_command(interaction: Interaction, user: Member):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        if user.id not in POLITICS_WHITELISTED_USER_IDS:
            POLITICS_WHITELISTED_USER_IDS.append(user.id)
            save_whitelist(POLITICS_WHITELISTED_USER_IDS)
            await interaction.response.send_message(f"{user.mention} has been added to the whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.mention} is already in the whitelist.", ephemeral=True)

    @tree.command(name="post-daily-summary", description="Posts the daily summary in the current channel for a specific date")
    async def post_daily_summary(interaction: Interaction, date: str = None):
        if not has_role(interaction, ROLES.MINISTER):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        
        if date is None:
            uk_timezone = pytz.timezone("Europe/London")
            date = datetime.now(uk_timezone).strftime("%Y-%m-%d")
        else:
            try:
                date_obj = datetime.strptime(date, "%Y-%m-%d")
                date = date_obj.strftime("%Y-%m-%d")
            except ValueError:
                await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD.", ephemeral=True)
                return

        summary_file_path = f"daily_summaries/daily_summary_{date}.json"
        if not os.path.exists(summary_file_path):
            await interaction.response.send_message(f"No summary available for {date}.", ephemeral=True)
            return

        await post_summary(client, interaction.channel.id, "daily", interaction.channel, date)

        await interaction.response.send_message(f"Posted daily summary for {date}.", ephemeral=True)

    @tree.command(name="politics-ban", description="Toggles politics ban for a member")
    async def manage_role_command(interaction: Interaction, user: Member):
        if not has_any_role(interaction, [ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        role = interaction.guild.get_role(ROLES.POLITICS_BAN)
        if not role:
            await interaction.response.send_message(f"Role with ID {role_id} not found.", ephemeral=True)
            return
        
        if role in user.roles:
            await user.remove_roles(role)
            await interaction.response.send_message(f"Role {role.name} has been removed from {user.mention}.", ephemeral=True)
        else:
            await user.add_roles(role)
            await interaction.response.send_message(f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True)

    @tree.command(name="summarise", description="Summarise a user's messages with sass")
    async def summarise(interaction: Interaction, channel: TextChannel = None, user: Member = None):
        if not has_any_role(interaction, [ROLES.SERVER_BOOSTER, ROLES.BORDER_FORCE, ROLES.CABINET, ROLES.MINISTER]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        today = datetime.now().date()
        usage_data = command_usage_tracker[interaction.user.id]

        if interaction.user.id == USERS.OGGERS:
            await sassy_summary(interaction, channel, user)
            return

        if usage_data['last_used'] != today:
            usage_data['count'] = 0
            usage_data['last_used'] = today

        if usage_data['count'] >= SUMMARISE_DAILY_LIMIT:
            await interaction.response.send_message(f"You've hit the daily limit of {SUMMARISE_DAILY_LIMIT} usages for this command", ephemeral=True)
            return

        usage_data['count'] += 1

        await sassy_summary(interaction, channel, user)

