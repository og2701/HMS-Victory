import unittest
from unittest.mock import Mock
from discord import Embed

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

    async def test_embed_colour_is_orange(self):
        interaction = Mock()
        interaction.response = Mock()

        await placeholderCommandName(interaction, "SomeRole")

        sent_embed = interaction.response.send_message.call_args[0][0]
        self.assertEqual(sent_embed.colour.value, 0xFFA500)


class TestPlaceholderCommandEmbed(unittest.TestCase):
    def test_embed_title(self):
        embed = Embed(title="Placeholder", description="Test", color=0xFFA500)
        self.assertEqual(embed.title, "Placeholder")

    def test_embed_description(self):
        embed = Embed(title="Test", description="Placeholder", color=0xFFA500)
        self.assertEqual(embed.description, "Placeholder")

    def test_embed_color(self):
        embed = Embed(title="Test", description="Test", color=0xFFA500)
        self.assertEqual(embed.colour.value, 0xFFA500)

    def test_embed_fields_empty(self):
        embed = Embed(title="Test", description="Test", color=0xFFA500)
        self.assertEqual(len(embed.fields), 0)


if __name__ == "__main__":
    unittest.main()
