import unittest
from unittest.mock import patch, AsyncMock
from discord import InteractionType

# Import the client class and functions to be tested
from main import (
    AClient,
    role_management,
    colour_palette,
    gridify_command,
    role_react_command,
)


class TestAClient(unittest.TestCase):
    def test_client_initialization(self):
        client = AClient()
        self.assertTrue(client.intents.presences)
        self.assertTrue(client.intents.members)
        self.assertTrue(client.intents.messages)
        self.assertTrue(client.intents.guild_messages)
        self.assertTrue(client.intents.dm_messages)

    @patch("main.tree")
    async def test_on_ready(self, mock_tree):
        client = AClient()
        client.synced = False
        client.user = AsyncMock()
        client.user.id = 123
        client.on_ready()
        mock_tree.sync.assert_called_once()

    @patch("main.handleRoleButtonInteraction")
    async def test_on_interaction(self, mock_handle_role_interaction):
        client = AClient()
        interaction = AsyncMock()
        interaction.type = InteractionType.component
        interaction.data = {"custom_id": "role_123"}

        await client.on_interaction(interaction)
        mock_handle_role_interaction.assert_awaited_once_with(interaction)

    @patch("main.updateRoleAssignments")
    async def test_role_management_command(self, mock_update_role_assignments):
        interaction = AsyncMock()
        await role_management(interaction, "admin")
        mock_update_role_assignments.assert_awaited_once_with(interaction, "admin")

    @patch("main.colourPalette")
    async def test_colour_palette_command(self, mock_colour_palette):
        interaction = AsyncMock()
        await colour_palette(interaction, "http://example.com/image.png")
        mock_colour_palette.assert_awaited_once_with(
            interaction, "http://example.com/image.png"
        )

    @patch("main.gridify")
    async def test_gridify_command(self, mock_gridify):
        interaction = AsyncMock()
        await gridify_command(interaction, "http://example.com/image.png")
        mock_gridify.assert_awaited_once_with(
            interaction, "http://example.com/image.png"
        )

    @patch("main.persistantRoleButtons")
    async def test_role_react_command(self, mock_persistant_role_buttons):
        interaction = AsyncMock()
        await role_react_command(interaction)
        mock_persistant_role_buttons.assert_awaited_once_with(interaction)


if __name__ == "__main__":
    unittest.main()