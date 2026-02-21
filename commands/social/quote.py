import discord
from discord import app_commands

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

    # Create the quote embed
    embed = discord.Embed(
        description=message.content,
        color=discord.Color.gold(),
        timestamp=message.created_at
    )
    
    embed.set_author(
        name=f"{message.author.display_name} said:",
        icon_url=message.author.display_avatar.url if message.author.display_avatar else None
    )
    
    embed.add_field(name="Original Message", value=f"[Jump to Message]({message.jump_url})")

    # Handle attachments
    if message.attachments:
        # If it's an image, set it as the embed image
        if message.attachments[0].content_type and message.attachments[0].content_type.startswith('image/'):
            embed.set_image(url=message.attachments[0].url)
        else:
            embed.add_field(name="Attachment", value=message.attachments[0].url, inline=False)

    await interaction.response.defer(ephemeral=True)
    
    try:
        await thread.send(embed=embed)
        await interaction.followup.send("Message successfully quoted to the quotes thread!", ephemeral=True)
    except Exception as e:
        print(f"Error sending quote: {e}")
        await interaction.followup.send("Failed to send quote. Make sure the bot has permissions in that thread.", ephemeral=True)
