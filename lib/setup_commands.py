from discord import app_commands, Interaction, Member
from ..utils import has_any_role, has_role
from ..commands import *
import json

MINISTER_ROLE_ID = 1250190944502943755
CABINET_ROLE_ID = 959493505930121226
BORDER_FORCE_ROLE_ID = 959500686746345542
POLITICS_BAN_ROLE_ID = 1265295557115510868
POLITICS_WHITELISTED_USER_IDS = []

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
        if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        await persistantRoleButtons(interaction)

    @tree.command(name="screenshot-canvas", description="Takes a screenshot of the current canvas")
    async def screenshot_canvas(interaction: Interaction, x: int = -770, y: int = 7930):
        await screenshotCanvas(interaction, x, y)

    @tree.command(name="add-to-iceberg", description="Adds text to the iceberg image")
    async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
        if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        await add_iceberg_text(interaction, text, level)

    @tree.command(name="show-iceberg", description="Shows the iceberg image")
    async def show_iceberg_command(interaction: Interaction):
        await show_iceberg(interaction)

    @tree.command(name="add-whitelist", description="Adds a user to the whitelist for the politics channel")
    async def add_whitelist_command(interaction: Interaction, user: Member):
        if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        if user.id not in POLITICS_WHITELISTED_USER_IDS:
            POLITICS_WHITELISTED_USER_IDS.append(user.id)
            save_whitelist(POLITICS_WHITELISTED_USER_IDS)
            await interaction.response.send_message(f"{user.mention} has been added to the whitelist.", ephemeral=True)
        else:
            await interaction.response.send_message(f"{user.mention} is already in the whitelist.", ephemeral=True)

    @tree.command(name="post-daily-summary", description="Posts the daily summary in the current channel")
    async def post_daily_summary(interaction: Interaction):
        if not has_role(interaction, MINISTER_ROLE_ID):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return
        
        await post_summary(client, interaction.channel.id, "daily", interaction.channel)

    @tree.command(name="politics-ban", description="Toggles politics ban for a member")
    async def manage_role_command(interaction: Interaction, user: Member):
        if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID, BORDER_FORCE_ROLE_ID]):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        role = interaction.guild.get_role(POLITICS_BAN_ROLE_ID)
        if not role:
            await interaction.response.send_message(f"Role with ID {role_id} not found.", ephemeral=True)
            return
        
        if role in user.roles:
            await user.remove_roles(role)
            await interaction.response.send_message(f"Role {role.name} has been removed from {user.mention}.", ephemeral=True)
        else:
            await user.add_roles(role)
            await interaction.response.send_message(f"Role {role.name} has been assigned to {user.mention}.", ephemeral=True)

def save_whitelist(whitelist):
    with open("whitelist.json", "w") as f:
        json.dump(whitelist, f)
