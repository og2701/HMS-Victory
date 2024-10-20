import discord
from discord.ui import Modal, TextInput, Button, View
from discord import ButtonStyle, Interaction, Forbidden
import json

persistent_views = {}

def load_persistent_views():
    try:
        with open("persistent_views.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_persistent_views():
    with open("persistent_views.json", "w") as f:
        json.dump(persistent_views, f)

async def handle_role_button_interaction(interaction: Interaction):
    custom_id = interaction.data["custom_id"]
    if custom_id.startswith("role_"):
        role_id = custom_id.split("_")[1]
        role = interaction.guild.get_role(int(role_id))
        if role:
            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    await interaction.response.send_message(f"Role {role.name} removed.", ephemeral=True)
                else:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(f"Role {role.name} assigned.", ephemeral=True)
            except Forbidden:
                await interaction.response.send_message("I do not have permission to assign this role.", ephemeral=True)
            except Exception as e:
                await interaction.response.send_message("An error occurred while assigning the role.", ephemeral=True)
                print(e)
        else:
            await interaction.response.send_message("Role not found.", ephemeral=True)

class RoleButton(Button):
    def __init__(self, role_id: int, label: str):
        super().__init__(label=label, style=ButtonStyle.primary, custom_id=f"role_{role_id}")
        self.role_id = role_id

    async def callback(self, interaction: Interaction):
        await handle_role_button_interaction(interaction)

class RoleButtonView(View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        if isinstance(roles, dict):
            for role_id, role_info in roles.items():
                button = RoleButton(role_id=role_id, label=role_info["name"])
                self.add_item(button)

class RoleSelectionModal(Modal):
    role_input = TextInput(label="Role Name or ID", placeholder="Enter the role name or ID", style=discord.TextStyle.short)

    def __init__(self, interaction: discord.Interaction):
        super().__init__(title="Add Role Reaction")
        self.interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        role_input = self.role_input.value
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=role_input) or discord.utils.get(guild.roles, id=int(role_input))

        if not role:
            await interaction.response.send_message(f"Role '{role_input}' not found. Please try again.", ephemeral=True)
            return

        interaction.client.temp_data[interaction.user.id].setdefault("roles", {})[role.id] = {"name": role.name}

        roles = interaction.client.temp_data[interaction.user.id]["roles"]
        view = interaction.client.temp_data[interaction.user.id]["view"]
        content = interaction.client.temp_data[interaction.user.id].get("content", "No content set.")

        message_content = f"Announcement: {content}\nRoles: {', '.join([r['name'] for r in roles.values()])}"
        await interaction.response.edit_message(content=message_content, view=view)

class AnnouncementSetupView(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=None)
        self.interaction = interaction

    @discord.ui.button(label="Set Content", style=ButtonStyle.primary)
    async def set_content(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(AnnouncementContentModal(interaction))

    @discord.ui.button(label="Add Role Reaction", style=ButtonStyle.secondary)
    async def add_role_reaction(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(RoleSelectionModal(interaction))

    @discord.ui.button(label="Confirm", style=ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        content = interaction.client.temp_data.get(interaction.user.id, {}).get("content", "No content set.")
        roles = interaction.client.temp_data.get(interaction.user.id, {}).get("roles", {})
        view = RoleButtonView(roles)
        message = await interaction.client.temp_data[interaction.user.id]["channel"].send(content=f"{content}", view=view)

        persistent_views[message.id] = roles
        save_persistent_views()

        interaction.client.add_view(view, message_id=message.id)
        await interaction.response.send_message("Announcement sent successfully!", ephemeral=True)

class AnnouncementContentModal(Modal):
    content_input = TextInput(label="Announcement Content", placeholder="Enter the announcement content", style=discord.TextStyle.long)

    def __init__(self, interaction: discord.Interaction):
        super().__init__(title="Set Announcement Content")
        self.interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        content = self.content_input.value
        interaction.client.temp_data[interaction.user.id]["content"] = content

        roles = interaction.client.temp_data[interaction.user.id].get("roles", {})
        view = interaction.client.temp_data[interaction.user.id]["view"]
        message_content = f"Announcement: {content}\nRoles: {', '.join([r['name'] for r in roles.values()])}"
        await interaction.response.edit_message(content=message_content, view=view)

async def setup_announcement_command(interaction, channel):
    interaction.client.temp_data[interaction.user.id] = {"channel": channel, "roles": {}}

    setup_view = AnnouncementSetupView(interaction)
    interaction.client.temp_data[interaction.user.id]["view"] = setup_view

    await interaction.response.send_message("Announcement setup started. Use the buttons below to configure.", view=setup_view, ephemeral=True)
