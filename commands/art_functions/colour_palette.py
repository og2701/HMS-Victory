from discord import Embed, File
from PIL import Image, ImageDraw
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

    initial_embed = Embed(
        title="Processing Image",
        description="Analyzing the image to extract colour palette...",
        color=0xFFA500,
    )

    await interaction.response.send_message(embed=initial_embed)

    async with aiohttp.ClientSession() as session:
        async with session.get(
            attachment_url, headers={"User-Agent": "YourBotName"}
        ) as resp:
            if resp.status != 200:
                error_embed = Embed(
                    title="Error",
                    description=f"Failed to download image. HTTP Status: {resp.status}",
                    color=0xFF0000,
                )
                await interaction.followup.send(embed=error_embed)
                return
            image_bytes = io.BytesIO(await resp.read())

    with Image.open(image_bytes) as img:
        img = img.convert("P", palette=Image.ADAPTIVE, colors=16)
        palette = img.getpalette()
        colours = [tuple(palette[i : i + 3]) for i in range(0, len(palette), 3)]

    palette_img = Image.new("RGB", (400, 50 * len(colours)), "white")
    draw = ImageDraw.Draw(palette_img)
    for i, colour in enumerate(colours):
        hex_colour = f"#{colour[0]:02x}{colour[1]:02x}{colour[2]:02x}"
        draw.rectangle([0, i * 50, 50, (i + 1) * 50], fill=colour)
        draw.text(
            (60, i * 50 + 10),
            f"{hex_colour} RGB({colour[0]}, {colour[1]}, {colour[2]})",
            fill="black",
        )

    buffer = io.BytesIO()
    palette_img.save(buffer, format="PNG")
    buffer.seek(0)

    most_significant_colour = colours[0]
    rgb_int = (
        most_significant_colour[0] * 65536
        + most_significant_colour[1] * 256
        + most_significant_colour[2]
    )
    result_embed = Embed(
        title="Colour Palette",
        description="Here is the extracted colour palette.",
        color=rgb_int,
    )
    file = File(buffer, filename="palette_image.png")
    result_embed.set_image(url=f"attachment://{file.filename}")
    result_embed.set_thumbnail(url=attachment_url)

    await interaction.edit_original_response(embed=result_embed, attachments=[file])
