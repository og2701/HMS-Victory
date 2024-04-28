from discord import Embed, File
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
import asyncio
import tempfile
import os

async def screenshotCanvas(interaction, x: int = -770, y: int = 7930):
    """
    Takes a screenshot of a specific coordinate on pixelcanvas.io and sends it in the chat.
    If no coordinates are provided, defaults to (-770, 7930).

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        x (int): The x-coordinate on pixelcanvas.io to navigate to (default -770).
        y (int): The y-coordinate on pixelcanvas.io to navigate to (default 7930).

    Returns:
        None
    """

    initial_embed = Embed(
        title="Processing your request...",
        description="Please wait while I capture the screenshot.",
        color=0xFFA500
    )
    await interaction.response.send_message(embed=initial_embed)

    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        screenshot_path = await capture_screenshot(x, y, tmp.name)
        file = File(screenshot_path, filename="screenshot.png")
        embed = Embed(
            title="Screenshot from Pixelcanvas",
            description=f"Here is the screenshot from the coordinates ({x}, {y}).",
            color=0xFFA500
        )
        embed.set_image(url="attachment://screenshot.png")
        
        await interaction.delete_original_response()
        
        await interaction.followup.send(file=file, embed=embed)
        os.remove(screenshot_path)

async def capture_screenshot(x, y, filepath):
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920x1080")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)

    try:
        url = f"https://pixelcanvas.io/@{x},{y},2"
        driver.get(url)
        await asyncio.sleep(5)
        driver.save_screenshot(filepath)
        return filepath
    finally:
        driver.quit()
