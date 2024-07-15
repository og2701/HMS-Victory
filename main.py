from discord import app_commands, Intents, Interaction, Client, InteractionType, Member
from typing import Optional

from lib.commands import (
    updateRoleAssignments,
    colourPalette,
    gridify,
    persistantRoleButtons,
    handleRoleButtonInteraction,
    screenshotCanvas,
    add_iceberg_text,
    show_iceberg
)

MINISTER_ROLE_ID = 1250190944502943755
CABINET_ROLE_ID = 959493505930121226

class AClient(Client):
    def __init__(self):
        intents = Intents.default()
        intents.presences = True
        intents.members = True
        intents.messages = True
        intents.guild_messages = True
        intents.dm_messages = True

        super().__init__(intents=intents)
        self.synced = False

    async def on_ready(self):
        global tree
        if not self.synced:
            await tree.sync()
            self.synced = True
        print(f"Logged in as {self.user}")
        for command in tree.get_commands():
            print(command.name)

    async def on_interaction(self, interaction: Interaction):
        if (
            interaction.type == InteractionType.component
            and "custom_id" in interaction.data
        ):
            custom_id = interaction.data["custom_id"]
            if custom_id.startswith("role_"):
                await handleRoleButtonInteraction(interaction)

client = AClient()
tree = app_commands.CommandTree(client)

def has_role(interaction: Interaction, role_id: int) -> bool:
    """Check if the user has the specified role."""
    return any(role.id == role_id for role in interaction.user.roles)

def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    """Check if the user has any of the specified roles."""
    return any(role.id in role_ids for role in interaction.user.roles)

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
async def screenshot_canvas(interaction: Interaction, x: Optional[int] = -770, y: Optional[int] = 7930):
    await screenshotCanvas(interaction, x, y)

@tree.command(name="user-activity", description="Gets user activity stats to find their most active hour")
async def user_activity_command(interaction: Interaction, month: str, user: Member, channel_name: str):
    await userActivity(interaction, month, user, channel_name)

@tree.command(name="add-to-iceberg", description="Adds text to the iceberg image")
async def add_to_iceberg_command(interaction: Interaction, text: str, level: int):
    if not has_any_role(interaction, [MINISTER_ROLE_ID, CABINET_ROLE_ID]):
        await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
        return
    await add_iceberg_text(interaction, text, level)

@tree.command(name="show-iceberg", description="Shows the iceberg image")
async def show_iceberg_command(interaction: Interaction):
    await show_iceberg(interaction)