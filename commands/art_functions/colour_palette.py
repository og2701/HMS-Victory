from discord import File
import io
import aiohttp
from PIL import Image
from html2image import Html2Image
import base64
import numpy as np
import matplotlib.pyplot as plt
from sklearn.cluster import KMeans

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
        # Convert the image to numpy array
        img_array = np.array(img)

        # Reshape the image array to a 2D array of pixels
        pixels = img_array.reshape(-1, 3)

        # Use KMeans clustering to find 10 dominant colors
        kmeans = KMeans(n_clusters=10)
        kmeans.fit(pixels)
        colors = kmeans.cluster_centers_.astype(int)

    original_img = Image.fromarray(img_array)
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
            display: flex;
            justify-content: center;
            align-items: center;
        }}
        .container {{
            display: inline-block;
            text-align: center;
        }}
        .original-container {{
            margin-bottom: 20px;
        }}
        .original {{
            border-radius: 10px;
        }}
        .palette-container {{
            display: inline-block;
            text-align: left;
        }}
        .color-box {{
            display: inline-block;
            width: 150px;
            margin: 10px;
            padding: 20px;
            border-radius: 10px;
            background-color: #3c3c3c;
            text-align: center;
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
    for color in colors:
        hex_color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
        html_content += f"""
        <div class="color-box">
            <div style="background-color: {hex_color};"></div>
            <p>{hex_color}</p>
            <p>rgb({color[0]}, {color[1]}, {color[2]})</p>
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
