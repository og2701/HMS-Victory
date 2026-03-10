from discord import Embed, File
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
import asyncio
import tempfile
import os
import logging

logging.basicConfig(level=logging.INFO)

async def screenshotCanvas(interaction, x: int = -770, y: int = 7930):
    initial_embed = Embed(
        title="Processing your request...",
        description="Please wait while I capture the screenshot.",
        color=0xFFA500,
    )
    await interaction.followup.send(embed=initial_embed)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        try:
            screenshot_path = await capture_screenshot(x, y, tmp.name)
            file = File(screenshot_path, filename="screenshot.png")
            embed = Embed(
                title="Screenshot from Pixelcanvas",
                description=f"Here is the screenshot from the coordinates ({x}, {y}).",
                color=0xFFA500,
            )
            embed.set_image(url="attachment://screenshot.png")
            await interaction.delete_original_response()
            await interaction.followup.send(file=file, embed=embed)
        except Exception as e:
            logging.error(f"Error capturing screenshot: {e}")
            error_embed = Embed(
                title="Error",
                description="An error occurred while capturing the screenshot. Please try again later.",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=error_embed)
        finally:
            os.remove(tmp.name)

async def capture_screenshot(x, y, filepath):
    from lib.core.image_processing import get_browser, rendering_lock
    import time
    
    async with rendering_lock:
        loop = asyncio.get_event_loop()
        
        def _capture_sync():
            try:
                browser = get_browser()
                # Store original window size
                original_size = browser.get_window_size()
                browser.set_window_size(1920, 1080)
                
                url = f"https://pixelcanvas.io/@{x},{y},2"
                browser.get(url)
                
                # Sleep to allow canvas to render
                time.sleep(5) 
                
                browser.save_screenshot(filepath)
                # Restore size
                browser.set_window_size(original_size['width'], original_size['height'])
                return filepath
            except Exception as e:
                logging.error(f"Error in capture_screenshot_sync: {e}")
                raise
                
        # Run the synchronous webdriver code in a thread pool so it doesn't block the bot
        return await loop.run_in_executor(None, _capture_sync)
