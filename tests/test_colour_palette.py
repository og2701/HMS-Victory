import unittest
from unittest.mock import Mock

from commands.art_functions.colour_palette import colourPalette


class TestColourPalette(unittest.TestCase):
    async def test_colourPalette_sends_initial_embed(self):
        interaction = Mock()
        interaction.response = Mock()

        await colourPalette(interaction, "http://example.com/image.png")

        interaction.response.send_message.assert_called_once()
        sent_embed = interaction.response.send_message.call_args[0][0]
        self.assertEqual(sent_embed.title, "Processing Image")

    async def test_colourPalette_handles_invalid_url(self):
        interaction = Mock()
        interaction.followup = Mock()

        await colourPalette(interaction, "http://invalid.url")

        error_embed = interaction.followup.send.call_args[0][0]
        self.assertEqual(error_embed.title, "Error")
        self.assertIn("Failed to download image", error_embed.description)

    async def test_colourPalette_sends_result_embed(self):
        interaction = Mock()
        interaction.edit_original_response = Mock()

        await colourPalette(interaction, "http://example.com/image.png")

        interaction.edit_original_response.assert_called_once()
        sent_embed = interaction.edit_original_response.call_args[1]["embed"]
        self.assertEqual(sent_embed.title, "Colour Palette")

    async def test_colourPalette_sends_palette_image(self):
        interaction = Mock()
        interaction.edit_original_response = Mock()

        await colourPalette(interaction, "http://example.com/image.png")

        sent_file = interaction.edit_original_response.call_args[1]["attachments"][0]
        self.assertEqual(sent_file.filename, "palette_image.png")


if __name__ == "__main__":
    unittest.main()
