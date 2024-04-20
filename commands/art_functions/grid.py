import discord
import aiohttp
from io import BytesIO
from PIL import Image, ImageDraw


async def gridify(interaction, image_url):
    """
    Adds a pixel art grid overlay to an image and sends the resulting image as a Discord file.

    Args:
        interaction (discord.Interaction): The Discord interaction object.
        image_url (str): The URL of the image to be processed.

    Returns:
        None
    """

    await interaction.response.defer()

    async with aiohttp.ClientSession() as session:
        async with session.get(image_url) as resp:
            image_data = await resp.read()

    img = Image.open(BytesIO(image_data))

    img = img.resize((int(img.width / 10), int(img.height / 10)), Image.NEAREST)
    img = img.resize((img.width * 10, img.height * 10), Image.NEAREST)

    for x in range(0, img.width, 10):
        ImageDraw.Draw(img).line([(x, 0), (x, img.height)], fill="black", width=1)
    for y in range(0, img.height, 10):
        ImageDraw.Draw(img).line([(0, y), (img.width, y)], fill="black", width=1)

    with BytesIO() as image_binary:
        img.save(image_binary, "PNG")
        image_binary.seek(0)
        file = discord.File(image_binary, filename="pixel.png")
        await interaction.followup.send(files=[file])
