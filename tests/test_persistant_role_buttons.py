import unittest
from unittest.mock import AsyncMock, patch
from commands.role_management.persistant_role_buttons import persistantRoleButtons, handleRoleButtonInteraction

class TestDiscordBot(unittest.TestCase):
    def setUp(self):
        self.interaction = AsyncMock()
        self.interaction.response.send_message = AsyncMock()

    @patch('your_module.ROLE_BUTTONS', {'123': {'name': 'Tester', 'description': 'A test role'}})
    @patch('your_module.Embed')
    @patch('your_module.View')
    @patch('your_module.Button')
    async def test_persistantRoleButtons(self, mock_button, mock_view, mock_embed):
        await persistantRoleButtons(self.interaction)
        mock_embed.assert_called_once()
        mock_view.return_value.add_item.assert_called_once()
        self.interaction.response.send_message.assert_awaited_once()

    @patch('your_module.InteractionType.component', new_callable=AsyncMock)
    async def test_handleRoleButtonInteraction_component(self, mock_interaction_type):
        self.interaction.type = mock_interaction_type
        self.interaction.data = {'custom_id': 'role_123'}
        self.interaction.guild.get_role = AsyncMock(return_value=AsyncMock())
        self.interaction.guild.get_role.return_value.name = 'Tester'
        self.interaction.user.roles = []
        self.interaction.user.add_roles = AsyncMock()
        
        await handleRoleButtonInteraction(self.interaction)
        
        self.interaction.response.send_message.assert_awaited_once_with("Role Tester assigned.", ephemeral=True)
        self.interaction.user.add_roles.assert_awaited_once()

    @patch('your_module.InteractionType.component', new_callable=AsyncMock)
    async def test_handleRoleButtonInteraction_no_role(self, mock_interaction_type):
        self.interaction.type = mock_interaction_type
        self.interaction.data = {'custom_id': 'role_999'}
        self.interaction.guild.get_role = AsyncMock(return_value=None)
        
        await handleRoleButtonInteraction(self.interaction)
        
        self.interaction.response.send_message.assert_awaited_once_with("Role not found.", ephemeral=True)

if __name__ == '__main__':
    unittest.main()
