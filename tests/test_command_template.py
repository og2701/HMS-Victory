import unittest
from unittest.mock import Mock

from commands.command_template import placeholderCommandName

class TestPlaceholderCommand(unittest.TestCase):

    async def test_sends_initial_embed(self):
        interaction = Mock()
        interaction.response = Mock()

        await placeholderCommandName(interaction, "SomeRole")

        interaction.response.send_message.assert_called_once()
        sent_embed = interaction.response.send_message.call_args[0][0]
        self.assertEqual(sent_embed.title, "Placeholder")

    async def test_embed_description_contains_role_name(self):
        interaction = Mock()
        interaction.response = Mock()
        
        await placeholderCommandName(interaction, "SomeRole")
        
        sent_embed = interaction.response.send_message.call_args[0][0]
        self.assertIn("SomeRole", sent_embed.description)

    async def test_embed_color_is_orange(self):
        interaction = Mock()
        interaction.response = Mock()

        await placeholderCommandName(interaction, "SomeRole")

        sent_embed = interaction.response.send_message.call_args[0][0]
        self.assertEqual(sent_embed.color, 0xFFA500)
