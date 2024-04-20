import unittest
from unittest.mock import Mock

from commands.art_functions.grid import gridify

class TestGridify(unittest.TestCase):

    async def test_gridify_sends_initial_response(self):
        interaction = Mock()
        interaction.response = Mock()

        await gridify(interaction, "http://example.com/image.png")

        interaction.response.defer.assert_called_once()

    async def test_gridify_handles_invalid_url(self):
        interaction = Mock()
        interaction.followup = Mock()

        await gridify(interaction, "http://invalid.url")

        self.assertEqual(interaction.followup.send.call_count, 0)

    async def test_gridify_sends_grid_image(self):
        interaction = Mock()
        interaction.followup = Mock()

        await gridify(interaction, "http://example.com/image.png")

        sent_file = interaction.followup.send.call_args[1]['files'][0]
        self.assertEqual(sent_file.filename, "pixel.png")

