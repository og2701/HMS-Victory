import unittest
from unittest.mock import patch, Mock

from commands.canvas.screenshot import screenshotCanvas, capture_screenshot

class TestScreenshotCanvas(unittest.TestCase):

    @patch("commands.canvas.screenshot.capture_screenshot")
    @patch("commands.canvas.screenshot.Embed")
    @patch("commands.canvas.screenshot.File")  
    async def test_screenshotCanvas_sends_initial_embed(self, mock_File, mock_Embed, mock_capture):
        interaction = Mock()
        await screenshotCanvas(interaction)
        
        mock_Embed.assert_called_once()
        interaction.response.send_message.assert_called_once()

    @patch("commands.canvas.screenshot.capture_screenshot")
    @patch("commands.canvas.screenshot.Embed")
    @patch("commands.canvas.screenshot.File")
    async def test_screenshotCanvas_calls_capture_screenshot(self, mock_File, mock_Embed, mock_capture):
        interaction = Mock()
        await screenshotCanvas(interaction)
        
        mock_capture.assert_called_once()

    @patch("commands.canvas.screenshot.capture_screenshot", return_value="screenshot.png") 
    @patch("commands.canvas.screenshot.Embed")
    @patch("commands.canvas.screenshot.File")
    async def test_screenshotCanvas_sends_screenshot_file(self, mock_File, mock_Embed, mock_capture):
        interaction = Mock()
        await screenshotCanvas(interaction)
        
        mock_File.assert_called_once_with("screenshot.png", filename="screenshot.png")
        interaction.followup.send.assert_called_once()

    @patch("commands.canvas.screenshot.capture_screenshot")
    @patch("commands.canvas.screenshot.tempfile.NamedTemporaryFile")
    async def test_capture_screenshot_navigates_to_url(self, mock_tmp, mock_capture):
        await capture_screenshot(123, 456, "screenshot.png")
        
        expected_url = "https://pixelcanvas.io/@123,456,2"
        mock_capture.driver.get.assert_called_once_with(expected_url)

