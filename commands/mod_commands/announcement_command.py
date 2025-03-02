import discord
from discord.ui import Button, View, Modal, TextInput
from discord import ButtonStyle, Interaction, Forbidden
import re
from lib.utils import load_persistent_views, save_persistent_views

persistent_views = load_persistent_views()

async def handle_role_button_interaction(interaction: Interaction):
    custom_id = interaction.data.get("custom_id", "")
    if custom_id.startswith("role_"):
        role_id = custom_id.split("_")[1]
        role = interaction.guild.get_role(int(role_id))
        if role:
            try:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role)
                    await interaction.response.send_message(
                        f"Role {role.name} removed.", ephemeral=True, delete_after=3
                    )
                else:
                    await interaction.user.add_roles(role)
                    await interaction.response.send_message(
                        f"Role {role.name} assigned.", ephemeral=True, delete_after=3
                    )
            except Forbidden:
                await interaction.response.send_message(
                    "I do not have permission to assign this role.", ephemeral=True, delete_after=3
                )
            except Exception as e:
                await interaction.response.send_message(
                    "An error occurred while assigning the role.", ephemeral=True, delete_after=3
                )
                print(e)
        else:
            await interaction.response.send_message(
                "Role not found.", ephemeral=True, delete_after=3
            )

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

class MessageLinkModal(Modal):
    message_input = TextInput(
        label="Message Link or ID",
        placeholder="Enter the message link or ID",
        style=discord.TextStyle.short,
    )

    def __init__(self, interaction: Interaction):
        super().__init__(title="Set Announcement Content")
        self.interaction = interaction

    async def on_submit(self, interaction: Interaction):
        message_input = self.message_input.value.strip()
        message_link_regex = re.compile(
            r"https://discord.com/channels/(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)"
        )
        match = message_link_regex.match(message_input)
        try:
            if match:
                channel_id = int(match.group("channel_id"))
                message_id = int(match.group("message_id"))
                channel = interaction.guild.get_channel(channel_id)
                message = await channel.fetch_message(message_id)
            else:
                message_id = int(message_input)
                message = await interaction.channel.fetch_message(message_id)
            if message:
                interaction.client.temp_data[interaction.user.id]["content"] = message.content
                await interaction.response.send_message(
                    "Message content set successfully!", ephemeral=True, delete_after=3
                )
            else:
                await interaction.response.send_message(
                    "Message not found. Please try again.", ephemeral=True, delete_after=3
                )
        except (ValueError, discord.NotFound):
            await interaction.response.send_message(
                "Invalid message ID or link. Please try again.", ephemeral=True, delete_after=3
            )

class RoleSelectionModal(Modal):
    role_input = TextInput(
        label="Role Name or ID",
        placeholder="Enter the role name or ID",
        style=discord.TextStyle.short,
    )

    def __init__(self, interaction: Interaction):
        super().__init__(title="Add Role Reaction")
        self.interaction = interaction

    async def on_submit(self, interaction: Interaction):
        role_input = self.role_input.value
        guild = interaction.guild
        role = discord.utils.get(guild.roles, name=role_input)
        try:
            role = role or discord.utils.get(guild.roles, id=int(role_input))
        except ValueError:
            role = None
        if not role:
            await interaction.response.send_message(
                f"Role '{role_input}' not found. Please try again.",
                ephemeral=True,
                delete_after=3,
            )
            return
        interaction.client.temp_data[interaction.user.id].setdefault("roles", {})[role.id] = {"name": role.name}
        roles = interaction.client.temp_data[interaction.user.id]["roles"]
        view = interaction.client.temp_data[interaction.user.id]["view"]
        content = interaction.client.temp_data[interaction.user.id].get("content", "No content set.")
        message_content = f"Announcement: {content}\nRoles: {', '.join([r['name'] for r in roles.values()])}"
        await interaction.response.edit_message(content=message_content, view=view)

class AnnouncementSetupView(View):
    def __init__(self, interaction: Interaction):
        super().__init__(timeout=None)
        self.interaction = interaction
        self.user_id = interaction.user.id

    @discord.ui.button(label="Set Content", style=ButtonStyle.primary)
    async def set_content(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("You are not authorised to use this button.", ephemeral=True, delete_after=3)
            return
        await interaction.response.send_modal(MessageLinkModal(interaction))

    @discord.ui.button(label="Add Role Reaction", style=ButtonStyle.secondary)
    async def add_role_reaction(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("You are not authorised to use this button.", ephemeral=True, delete_after=3)
            return
        await interaction.response.send_modal(RoleSelectionModal(interaction))

    @discord.ui.button(label="Preview", style=ButtonStyle.success)
    async def preview(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("You are not authorised to use this button.", ephemeral=True, delete_after=3)
            return
        content = interaction.client.temp_data.get(interaction.user.id, {}).get("content", "No content set.")
        roles = interaction.client.temp_data.get(interaction.user.id, {}).get("roles", {})
        view = PreviewView(
            channel=interaction.client.temp_data[interaction.user.id]["channel"],
            roles=roles,
            content=content,
            user_id=self.user_id,
        )
        message_content = f"**Preview** {content}\nRoles: {', '.join([r['name'] for r in roles.values()])}"
        await interaction.response.edit_message(content=message_content, view=view)

class PreviewView(View):
    def __init__(self, channel, roles, content, user_id):
        super().__init__(timeout=None)
        self.channel = channel
        self.roles = roles
        self.content = content
        self.user_id = user_id

    @discord.ui.button(label="Confirm and Send", style=ButtonStyle.primary)
    async def confirm_and_send(self, interaction: Interaction, button: Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("You are not authorised to use this button.", ephemeral=True, delete_after=3)
            return
        view = RoleButtonView(self.roles)
        try:
            message = await self.channel.send(content=self.content, view=view)
            persistent_views[message.id] = self.roles
            save_persistent_views(persistent_views)
            interaction.client.add_view(view, message_id=message.id)
            await interaction.response.edit_message(content="Announcement sent successfully!", view=None)
        except discord.errors.NotFound as e:
            await interaction.followup.send("Failed to send the announcement due to an unknown webhook or interaction. Please try again.", ephemeral=True, delete_after=3)
            print(e)
        except Exception as e:
            await interaction.followup.send("Failed to send the announcement. Please try again.")
            print(e)

async def setup_announcement_command(interaction: Interaction, channel):
    if not hasattr(interaction.client, "temp_data"):
        interaction.client.temp_data = {}
    interaction.client.temp_data[interaction.user.id] = {"channel": channel, "roles": {}}
    setup_view = AnnouncementSetupView(interaction)
    interaction.client.temp_data[interaction.user.id]["view"] = setup_view
    await interaction.response.send_message("Announcement setup started. Use the buttons below to configure.", view=setup_view)
