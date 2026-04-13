import asyncio
import io
import re
import logging
import discord
from config import CHANNELS, ROLES, HALL_OF_FAME_FILE
from lib.core.log_functions import create_quote_image
from lib.core.file_operations import load_json_file, save_json_file

logger = logging.getLogger(__name__)

JUMP_URL_RE = re.compile(r"https?://(?:\w+\.)?discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)")


def _is_playable_media(att: discord.Attachment) -> bool:
    ct = (att.content_type or "").lower()
    if ct.startswith("video/"):
        return True
    if ct == "image/gif":
        return True
    name = (att.filename or "").lower()
    return name.endswith((".mp4", ".mov", ".webm", ".gif"))


async def collect_media_files(message: discord.Message, size_limit: int) -> list[discord.File]:
    """Download video/gif attachments from `message` and return them as discord.File
    objects so they can be re-uploaded inline in the HOF post."""
    files: list[discord.File] = []
    if not message.attachments:
        return files
    for att in message.attachments:
        if not _is_playable_media(att):
            continue
        if att.size and att.size > size_limit:
            logger.info(f"[HOF] Skipping {att.filename}: {att.size} > limit {size_limit}")
            continue
        try:
            data = await att.read()
        except (discord.HTTPException, discord.NotFound) as e:
            logger.warning(f"[HOF] Failed to download {att.filename}: {e}")
            continue
        files.append(discord.File(io.BytesIO(data), filename=att.filename, spoiler=att.is_spoiler()))
    return files


async def regenerate_hof_images(client):
    """Walk the HOF thread and regenerate the quote image on each bot post so
    messages whose attached images were clipped get redrawn with the new sizing."""
    thread = client.get_channel(CHANNELS.HALL_OF_FAME_THREAD)
    if not thread:
        try:
            thread = await client.fetch_channel(CHANNELS.HALL_OF_FAME_THREAD)
        except discord.NotFound:
            logger.error("[HOF] Thread not found during regenerate.")
            return

    fixed = 0
    async for hof_msg in thread.history(limit=None):
        if hof_msg.author.id != client.user.id:
            continue
        if not hof_msg.embeds:
            continue
        embed = hof_msg.embeds[0]
        jump_url = embed.url or (embed.description or "")
        match = JUMP_URL_RE.search(jump_url)
        if not match:
            continue
        _, src_channel_id, src_message_id = match.groups()
        src_channel = client.get_channel(int(src_channel_id))
        if not src_channel:
            try:
                src_channel = await client.fetch_channel(int(src_channel_id))
            except (discord.NotFound, discord.Forbidden):
                continue
        try:
            original = await src_channel.fetch_message(int(src_message_id))
        except (discord.NotFound, discord.Forbidden):
            continue

        try:
            image_buffer = await create_quote_image(client, original)
        except Exception as e:
            logger.error(f"[HOF] regenerate failed for {original.id}: {e}")
            continue

        try:
            file = discord.File(image_buffer, filename="hof_quote.png")
            new_embed = embed.copy()
            new_embed.set_image(url="attachment://hof_quote.png")
            size_limit = getattr(thread.guild, "filesize_limit", 25 * 1024 * 1024)
            media_files = await collect_media_files(original, size_limit)
            await hof_msg.edit(embed=new_embed, attachments=[file, *media_files])
            fixed += 1
        except Exception as e:
            logger.error(f"[HOF] edit failed for {hof_msg.id}: {e}")
        await asyncio.sleep(1)

    logger.info(f"[HOF] Regenerated {fixed} Hall of Fame images.")


async def handle_hof_context_menu(interaction: discord.Interaction, message: discord.Message):
    if not any(role.id == ROLES.DEPUTY_PM for role in interaction.user.roles):
        await interaction.response.send_message("Only the Deputy PM can use this.", ephemeral=True)
        return

    if message.author.bot:
        await interaction.response.send_message("Bot messages can't be added to the Hall of Fame.", ephemeral=True)
        return

    if not message.content and not message.attachments:
        await interaction.response.send_message("This message has nothing to add.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    hall_of_fame_data = load_json_file(HALL_OF_FAME_FILE) or []
    if str(message.id) in hall_of_fame_data:
        await interaction.followup.send("This message is already in the Hall of Fame.", ephemeral=True)
        return

    client = interaction.client
    thread = client.get_channel(CHANNELS.HALL_OF_FAME_THREAD)
    if not thread:
        try:
            thread = await client.fetch_channel(CHANNELS.HALL_OF_FAME_THREAD)
        except discord.NotFound:
            await interaction.followup.send("Hall of Fame thread not found.", ephemeral=True)
            return

    embed = discord.Embed(
        description=f"[Click here to jump to message]({message.jump_url})",
        color=0xffd700,
        url=message.jump_url,
    )
    embed.set_author(
        name=message.author.display_name,
        icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
        url=message.jump_url,
    )

    size_limit = getattr(thread.guild, "filesize_limit", 25 * 1024 * 1024)
    media_files = await collect_media_files(message, size_limit)

    try:
        image_buffer = await create_quote_image(client, message)
        quote_file = discord.File(image_buffer, filename="hof_quote.png")
        embed.set_image(url="attachment://hof_quote.png")
        await thread.send(
            content=f"🏆 {message.author.mention}'s message made it to the Hall of Fame!",
            embed=embed,
            files=[quote_file, *media_files],
        )
    except Exception as e:
        print(f"[HOF] Error creating quote image: {e}")
        embed.description = f"{embed.description}\n\n{message.content}"
        if message.attachments:
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/") and attachment.content_type != "image/gif":
                    embed.set_image(url=attachment.url)
                    break
        await thread.send(
            content=f"🏆 {message.author.mention}'s message made it to the Hall of Fame!",
            embed=embed,
            files=media_files,
        )

    hall_of_fame_data.append(str(message.id))
    save_json_file(HALL_OF_FAME_FILE, hall_of_fame_data)

    from lib.bot.event_handlers import award_badge_with_notify
    await award_badge_with_notify(client, message.author.id, 'hof')

    await interaction.followup.send("Added to the Hall of Fame!", ephemeral=True)
