import discord
from discord import Embed, Interaction
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import io

async def profile_card(interaction: Interaction, user: discord.Member):
    """
    Generates a profile card image of a user.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        user (discord.User): The user to generate the profile card for.

    Returns:
        None
    """

    if interaction.guild:
        member = interaction.guild.get_member(user.id)
    else:
        member = None

    username = f"{user.name}#{user.discriminator}"
    user_id = user.id
    created_at = user.created_at.strftime("%Y-%m-%d %H:%M:%S")

    if member is not None and member.joined_at is not None:
        joined_at = member.joined_at.strftime("%Y-%m-%d %H:%M:%S")
    else:
        joined_at = "N/A"

    if member is not None:
        roles = [role.name for role in member.roles if role.name != "@everyone"]
        roles_str = ", ".join(roles) if roles else "No Roles"
        status = str(member.status).title()
        if member.activity:
            activity = f"{member.activity.type.name.title()}: {member.activity.name}"
        else:
            activity = "None"
    else:
        roles_str = "N/A"
        status = "N/A"
        activity = "None"

    avatar_bytes = await user.display_avatar.read()
    avatar_image = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")
    avatar_image = avatar_image.resize((256, 256))

    base_image = Image.new('RGBA', (800, 600), color=(30, 30, 30, 255))

    base_image.paste(avatar_image, (50, 50), avatar_image)

    draw = ImageDraw.Draw(base_image)

    try:
        font = ImageFont.truetype("arial.ttf", size=40)
    except:
        font = ImageFont.load_default()

    user_info_x = 350
    user_info_y = 50
    line_spacing = 50

    draw.text((user_info_x, user_info_y), f"{username}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    draw.text((user_info_x, user_info_y), f"User ID: {user_id}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    draw.text((user_info_x, user_info_y), f"Account Created: {created_at}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    draw.text((user_info_x, user_info_y), f"Joined Server: {joined_at}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    draw.text((user_info_x, user_info_y), f"Roles: {roles_str}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    draw.text((user_info_x, user_info_y), f"Status: {status}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    draw.text((user_info_x, user_info_y), f"Activity: {activity}", font=font, fill=(255,255,255))
    user_info_y += line_spacing

    buffer = io.BytesIO()
    base_image.save(buffer, format="PNG")
    buffer.seek(0)

    file = discord.File(fp=buffer, filename='profile.png')
    await interaction.response.send_message(file=file)
