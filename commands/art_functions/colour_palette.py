from discord import File
import io
import aiohttp
from PIL import Image
from html2image import Html2Image
import base64

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
        img = img.convert("P", palette=Image.ADAPTIVE, colors=10)
        palette = img.getpalette()
        colours = [tuple(palette[i:i + 3]) for i in range(0, len(palette), 3)][:10]

        original_img = img.convert("RGB")
        original_img.thumbnail((200, 200))

    buffered = io.BytesIO()
    original_img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()

    html_content = """
    <html>
    <head>
    <style>
        body {{
            font-family: Arial, sans-serif;
            background-color: #2c2c2c;
            color: white;
            margin: 0;
            padding: 20px;
        }}
        .container {{
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .original-container {{
            margin-bottom: 20px;
        }}
        .original {{
            border-radius: 10px;
        }}
        .palette-container {{
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-start;
            width: 100%;
        }}
        .color-box {{
            width: 150px;
            margin: 10px;
            padding: 20px;
            border-radius: 10px;
            background-color: #3c3c3c;
            text-align: center;
            flex: 0 1 auto;
        }}
        .color-box div {{
            height: 100px;
            border-radius: 10px;
        }}
    </style>
    </head>
    <body>
    <div class="container">
        <div class="original-container">
            <img src="data:image/png;base64,{}" class="original" />
        </div>
        <h1>COLOUR PALETTE</h1>
        <div class="palette-container">
    """
    for colour in colours:
        hex_colour = f"#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}"
        html_content += f"""
        <div class="color-box">
            <div style="background-color: {hex_colour};"></div>
            <p>{hex_colour}</p>
            <p>rgb({colour[0]}, {colour[1]}, {colour[2]})</p>
        </div>
        """
    html_content += """
        </div>
    </div>
    </body>
    </html>
    """

    html_content = html_content.format(img_str)

    hti = Html2Image()
    hti.screenshot(html_str=html_content, save_as='palette_image.png')

    with open('palette_image.png', 'rb') as f:
        buffer = io.BytesIO(f.read())

    buffer.seek(0)

    file = File(buffer, filename="palette_image.png")

    await interaction.edit_original_response(content="Here is the extracted colour palette:", attachments=[file])