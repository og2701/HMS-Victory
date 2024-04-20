import unittest
from unittest.mock import patch, AsyncMock
from discord import Client, Interaction, InteractionType, app_commands

from main import AClient, role_management, colour_palette, gridify_command, role_react_command

class TestDiscordBot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = AClient()

    @patch('discord_bot.Client')
    async def test_init(self, mock_client):
        self.assertTrue(self.client.intents.presences)
        self.assertTrue(self.client.intents.members)
        self.assertTrue(self.client.intents.messages)
        self.assertTrue(self.client.intents.guild_messages)
        self.assertTrue(self.client.intents.dm_messages)

    @patch('discord_bot.tree.sync')
    async def test_on_ready(self, mock_sync):
        self.client.user = AsyncMock()
        self.client.user.id = 123  # Mock user ID
        await self.client.on_ready()
        mock_sync.assert_awaited_once()

    @patch('discord_bot.handleRoleButtonInteraction')
    async def test_on_interaction(self, mock_handle_role_interaction):
        interaction = AsyncMock()
        interaction.type = InteractionType.component
        interaction.data = {'custom_id': 'role_123'}
        await self.client.on_interaction(interaction)
        mock_handle_role_interaction.assert_awaited_once_with(interaction)

    @patch('discord_bot.updateRoleAssignments', new_callable=AsyncMock)
    async def test_role_management_command(self, mock_update_role_assignments):
        interaction = AsyncMock()
        await role_management(interaction, "admin")
        mock_update_role_assignments.assert_awaited_once_with(interaction, "admin")

    @patch('discord_bot.colourPalette', new_callable=AsyncMock)
    async def test_colour_palette_command(self, mock_colour_palette):
        interaction = AsyncMock()
        await colour_palette(interaction, "http://example.com/image.png")
        mock_colour_palette.assert_awaited_once_with(interaction, "http://example.com/image.png")

    @patch('discord_bot.gridify', new_callable=AsyncMock)
    async def test_gridify_command(self, mock_gridify):
        interaction = AsyncMock()
        await gridify_command(interaction, "http://example.com/image.png")
        mock_gridify.assert_awaited_once_with(interaction, "http://example.com/image.png")

    @patch('discord_bot.persistantRoleButtons', new_callable=AsyncMock)
    async def test_role_react_command(self, mock_persistent_role_buttons):
        interaction = AsyncMock()
        await role_react_command(interaction)
        mock_persistent_role_buttons.assert_awaited_once_with(interaction)

if __name__ == "__main__":
    unittest.main()
