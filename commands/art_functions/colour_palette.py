from discord import File
from PIL import Image, ImageDraw, ImageFont
import io
import aiohttp

async def colourPalette(interaction, attachment_url: str):
    """
    Processes an image attachment and extracts the colour palette.

    Args:
        interaction (discord.Interaction): The Discord interaction object.
        attachment_url (str): The URL of the image attachment to process.

    Returns:
        None
    """

    initial_message = await interaction.response.send_message("Processing image to extract color palette...")

    async with aiohttp.ClientSession() as session:
        async with session.get(attachment_url, headers={"User-Agent": "YourBotName"}) as resp:
            if resp.status != 200:
                await interaction.followup.send(f"Failed to download image. HTTP Status: {resp.status}")
                return
            image_bytes = io.BytesIO(await resp.read())

    with Image.open(image_bytes) as img:
        img = img.convert("P", palette=Image.ADAPTIVE, colors=16)
        palette = img.getpalette()
        colours = [tuple(palette[i:i + 3]) for i in range(0, len(palette), 3)]
        
        original_img = img.convert("RGB")
        original_img.thumbnail((200, 200))

    palette_img = Image.new("RGB", (400, 50 * len(colours) + 210), "white")
    draw = ImageDraw.Draw(palette_img)
    
    palette_img.paste(original_img, (200, 0))
    
    font = ImageFont.load_default()
    for i, colour in enumerate(colours):
        hex_colour = f"#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}"
        draw.rectangle([0, i * 50 + 210, 50, (i + 1) * 50 + 210], fill=colour)
        draw.text((60, i * 50 + 220), f"{hex_colour} RGB({colour[0]}, {colour[1]}, {colour[2]})", fill="black", font=font)

    buffer = io.BytesIO()
    palette_img.save(buffer, format="PNG")
    buffer.seek(0)

    file = File(buffer, filename="palette_image.png")

    await interaction.edit_original_response(content="Here is the extracted colour palette:", attachments=[file])