import discord
from discord import app_commands
from lib.core.log_functions import create_quote_image

async def handle_quote_context_menu(interaction: discord.Interaction, message: discord.Message):
    # Process the context menu action
    thread_id = 1193950705355071539
    thread = interaction.guild.get_thread(thread_id)
    
    if not thread:
        await interaction.response.send_message("Could not find the Quotes thread!", ephemeral=True)
        return

    # Check if the message has content or attachments
    if not message.content and not message.attachments:
        await interaction.response.send_message("This message has nothing to quote.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        image_buffer = await create_quote_image(message)
    except Exception as e:
        print(f"Error creating quote image: {e}")
        await interaction.followup.send("Failed to generate quote image.", ephemeral=True)
        return

    # Create the quote embed
    embed = discord.Embed(
        color=discord.Color.gold(),
        timestamp=message.created_at
    )
    
    embed.add_field(name="Original Message", value=f"[Jump to Message]({message.jump_url})")

    # Handle attachments that aren't images (since images are in the screenshot)
    has_image = any(a.content_type and a.content_type.startswith("image/") for a in message.attachments)
    if message.attachments and not has_image:
        embed.add_field(name="Attachment", value=message.attachments[0].url, inline=False)

    embed.set_image(url="attachment://quote.png")

    try:
        await thread.send(
            file=discord.File(image_buffer, filename="quote.png"),
            embed=embed
        )
        await interaction.followup.send("Message successfully quoted to the quotes thread!", ephemeral=True)
    except Exception as e:
        print(f"Error sending quote: {e}")
        await interaction.followup.send("Failed to send quote. Make sure the bot has permissions in that thread.", ephemeral=True)
