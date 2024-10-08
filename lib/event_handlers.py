import discord
from discord import Interaction, InteractionType
import logging
import os
import aiohttp
import io
from apscheduler.triggers.cron import CronTrigger

from lib.translation import translate_and_send
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.utils import restrict_channel_for_new_members
from lib.log_functions import create_message_image, create_edited_message_image
from lib.settings import *

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024

async def on_ready(client, tree, scheduler):
    global POLITICS_WHITELISTED_USER_IDS
    if not client.synced:
        await tree.sync()
        client.synced = True
    logger.info(f"Logged in as {client.user}")
    for command in tree.get_commands():
        logger.info(f"Command loaded: {command.name}")

    scheduler.add_job(client.daily_summary, CronTrigger(hour=0, minute=0, timezone="Europe/London"))
    scheduler.add_job(client.weekly_summary, CronTrigger(day_of_week="mon", hour=0, minute=1, timezone="Europe/London"))
    scheduler.add_job(client.monthly_summary, CronTrigger(day=1, hour=0, minute=2, timezone="Europe/London"))
    scheduler.add_job(client.clear_image_cache, CronTrigger(day_of_week="sun", hour=0, minute=0, timezone="Europe/London"))

    scheduler.start()

async def on_message(client, message):
    if not await restrict_channel_for_new_members(message, CHANNELS.POLITICS, 7, POLITICS_WHITELISTED_USER_IDS):
        return

    if message.attachments:
        cache_channel = client.get_channel(CHANNELS.IMAGE_CACHE)
        if cache_channel:
            async with aiohttp.ClientSession() as session:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith('image/'):
                        if attachment.size <= MAX_IMAGE_SIZE:
                            async with session.get(attachment.url) as response:
                                if response.status == 200:
                                    image_data = await response.read()
                                    image_filename = attachment.filename
                                    file = discord.File(io.BytesIO(image_data), filename=image_filename)
                                    embed = discord.Embed(
                                        title="Image Cached",
                                        description=f"Image by {message.author.mention} in {message.channel.mention}",
                                        color=discord.Color.blue()
                                    )
                                    embed.add_field(name="Message Link", value=f"[Click here](https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id})")
                                    embed.set_image(url=f"attachment://{image_filename}") 
                                    cached_message = await cache_channel.send(embed=embed, file=file)
                                    if cached_message.embeds[0].image.url:
                                        if message.id not in client.image_cache:
                                            client.image_cache[message.id] = {}
                                        client.image_cache[message.id][attachment.url] = cached_message.embeds[0].image.url
                        else:
                            logger.info(f"Skipped downloading {attachment.filename} as it exceeds the size limit of {MAX_IMAGE_SIZE / (1024 * 1024)} MB.")

async def on_interaction(interaction: Interaction):
    if (
        interaction.type == InteractionType.component
        and "custom_id" in interaction.data
    ):
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            await handleRoleButtonInteraction(interaction)

async def on_member_join(member):
    pass

async def on_member_remove(member):
    pass

async def on_member_ban(guild, user):
    pass

async def on_message_delete(client, message):
    async for entry in message.guild.audit_logs(action=discord.AuditLogAction.message_delete, limit=1):
        if entry.target.id == message.author.id and entry.extra.channel.id == message.channel.id:
            deleter = entry.user
            break
    else:
        deleter = None

    log_channel = client.get_channel(959723562892144690)
    channel_link = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}"
    if log_channel is not None:
        if message.content:
            image_file_path = await create_message_image(message, "Deleted Message")

            description = f"Message by {message.author.mention} ({message.author.id}) deleted in {message.channel.mention}."
            if deleter and deleter != message.author:
                description += f"\nDeleted by {deleter.mention} ({deleter.id})."
            
            embed = discord.Embed(
                title="Message Deleted",
                description=description,
                color=discord.Color.red()
            )
            embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
            embed.set_image(url="attachment://deleted_message.png")
            if image_file_path is not None:
                with open(image_file_path, "rb") as f:
                    await log_channel.send(file=discord.File(f, "deleted_message.png"), embed=embed)
                os.remove(image_file_path)

        for attachment in message.attachments:
            attachment_link = client.image_cache.get(message.id, {}).get(attachment.url)
            if attachment_link:
                if attachment.content_type and attachment.content_type.startswith('image/'):
                    image_embed = discord.Embed(
                        title="Image Deleted",
                        description=f"An image by {message.author.mention} ({message.author.id}) was deleted in {message.channel.mention}.",
                        color=discord.Color.red()
                    )
                    image_embed.add_field(name="Channel Link", value=f"[Click here]({channel_link})")
                    image_embed.add_field(name="Image Link", value=f"{attachment_link}")
                    image_embed.set_image(url=attachment_link)
                    await log_channel.send(embed=image_embed)
                else:
                    attachment_embed = discord.Embed(
                        title="Attachments Deleted",
                        description=f"The following attachments by {message.author.mention} ({message.author.id}) were deleted in {message.channel.mention}:\n{attachment.filename}",
                        color=discord.Color.red()
                    )
                    attachment_embed.add_field(name="Channel Link", value=f"[Click here]({attachment_link})")
                    await log_channel.send(embed=attachment_embed)

async def on_message_edit(client, before, after):
    if before.author.bot:
        return

    log_channel = client.get_channel(959723562892144690)
    if log_channel is not None:
        image_file_path = await create_edited_message_image(before, after)

        message_link = f"https://discord.com/channels/{before.guild.id}/{before.channel.id}/{after.id}"
        embed = discord.Embed(
            title="Message Edited",
            description=f"Message edited in {before.channel.mention} by {before.author.mention} ({before.author.id}).",
            color=discord.Color.orange()
        )
        embed.add_field(name="Message Link", value=f"[Click here]({message_link})")
        embed.set_image(url="attachment://edited_message.png")
        if image_file_path is not None:
            with open(image_file_path, "rb") as f:
                await log_channel.send(file=discord.File(f, "edited_message.png"), embed=embed)
            os.remove(image_file_path)

async def on_reaction_add(reaction, user):
    logger.info(reaction)
    logger.info(str(reaction.emoji))
    is_in_mapping = str(reaction.emoji) in FLAG_LANGUAGE_MAPPINGS
    try:
        if is_in_mapping:
            message = reaction.message
            target_language = FLAG_LANGUAGE_MAPPINGS[str(reaction.emoji)]

            if message.content:
                await translate_and_send(reaction, message, target_language, message.author)
    except Exception as e:
        logger.error(f"Error in on_reaction_add: {e}")

async def on_reaction_remove(reaction, user):
    pass

async def on_member_update(client, before, after):
    updates_channel = client.get_channel(CHANNELS.MEMBER_UPDATES)

    if updates_channel is None:
        logger.warning("Updates channel not found.")
        return

    embed = discord.Embed(
        title="Member Update",
        description=f"Changes for {after.mention}",
        color=discord.Color.blue(),
        timestamp=discord.utils.utcnow()
    )

    embed.add_field(name="Username", value=after.name, inline=True)
    embed.add_field(name="User ID", value=str(after.id), inline=True)
    embed.add_field(name="Nickname", value=after.nick if after.nick else "None", inline=True)
    embed.add_field(name="Account Creation Date", value=after.created_at.strftime("%B %d, %Y at %H:%M UTC"), inline=True)
    embed.add_field(name="Join Date", value=after.joined_at.strftime("%B %d, %Y at %H:%M UTC"), inline=True)
    embed.add_field(name="Profile Link", value=f"[View Profile](https://discord.com/users/{after.id})", inline=False)
    embed.set_thumbnail(url=after.avatar.url if after.avatar else after.default_avatar.url)

    changes_detected = False

    if before.roles != after.roles:
        before_roles = set(before.roles)
        after_roles = set(after.roles)
        added_roles = after_roles - before_roles
        removed_roles = before_roles - after_roles

        if added_roles:
            embed.add_field(name="Roles Added", value=', '.join([role.name for role in added_roles]), inline=False)
            changes_detected = True
        if removed_roles:
            embed.add_field(name="Roles Removed", value=', '.join([role.name for role in removed_roles]), inline=False)
            changes_detected = True

    if before.voice is not None and after.voice is not None:
        if before.voice.mute != after.voice.mute:
            embed.add_field(
                name="Server Mute Changed",
                value=f"**Before:** {'Muted' if before.voice.mute else 'Unmuted'}\n**After:** {'Muted' if after.voice.mute else 'Unmuted'}",
                inline=False
            )
            changes_detected = True

        if before.voice.deaf != after.voice.deaf:
            embed.add_field(
                name="Server Deafen Changed",
                value=f"**Before:** {'Deafened' if before.voice.deaf else 'Undeafened'}\n**After:** {'Deafened' if after.voice.deaf else 'Undeafened'}",
                inline=False
            )
            changes_detected = True

    if before.premium_since is None and after.premium_since is not None:
        port_of_dover_channel = client.get_channel(CHANNELS.PORT_OF_DOVER)
        if port_of_dover_channel:
            boost_embed = discord.Embed(
                title="🎉 New Server Boost! 🎉",
                description=f"{after.mention} has just boosted the server!",
                color=discord.Color.purple(),
                timestamp=after.premium_since
            )
            boost_embed.set_thumbnail(url=after.avatar.url if after.avatar else after.default_avatar.url)
            boost_embed.add_field(name="👤 Booster:", value=f"{after.name}#{after.discriminator} ({after.id})", inline=False)
            boost_embed.add_field(name="📅 Boosted On:", value=after.premium_since.strftime("%B %d, %Y at %H:%M UTC"), inline=False)
            boost_embed.add_field(name="🔢 Total Boosts:", value=f"{after.guild.premium_subscription_count}", inline=True)
            boost_embed.add_field(name="🎉 Total Boosters:", value=f"{len(after.guild.premium_subscribers)}", inline=True)
            tier_info = {
                0: "Tier 0 (No Level)",
                1: "Tier 1 (Level 1)",
                2: "Tier 2 (Level 2)",
                3: "Tier 3 (Level 3)"
            }
            current_tier = after.guild.premium_tier
            boost_embed.add_field(name="🏆 Current Boost Level:", value=tier_info.get(current_tier, "Unknown"), inline=False)
            boost_embed.add_field(name="🔗 Profile:", value=f"[View Profile](https://discord.com/users/{after.id})", inline=False)
            boost_embed.set_image(url="https://i.redd.it/qq911bvdqwu51.gif")

            await port_of_dover_channel.send(embed=boost_embed)

    elif before.premium_since is not None and after.premium_since is None:
        port_of_dover_channel = client.get_channel(CHANNELS.PORT_OF_DOVER)
        if port_of_dover_channel:
            unboost_embed = discord.Embed(
                title="⚠️ Server Boost Lost ⚠️",
                description=f"{after.mention} has stopped boosting the server.",
                color=discord.Color.red(),
                timestamp=discord.utils.utcnow()
            )
            unboost_embed.set_thumbnail(url=after.avatar.url if after.avatar else after.default_avatar.url)
            unboost_embed.add_field(name="👤 Former Booster:", value=f"{after.name}#{after.discriminator} ({after.id})", inline=False)
            unboost_embed.add_field(name="📅 Boost Started On:", value=before.premium_since.strftime("%B %d, %Y at %H:%M UTC"), inline=False)
            unboost_embed.add_field(name="❌ Boost Ended On:", value=discord.utils.utcnow().strftime("%B %d, %Y at %H:%M UTC"), inline=False)
            unboost_embed.add_field(name="🔢 Total Boosts Now:", value=f"{after.guild.premium_subscription_count}", inline=True)
            unboost_embed.add_field(name="🎉 Total Boosters Now:", value=f"{len(after.guild.premium_subscribers)}", inline=True)
            tier_info = {
                0: "Tier 0 (No Level)",
                1: "Tier 1 (Level 1)",
                2: "Tier 2 (Level 2)",
                3: "Tier 3 (Level 3)"
            }
            current_tier = after.guild.premium_tier
            unboost_embed.add_field(name="🏆 Current Boost Level:", value=tier_info.get(current_tier, "Unknown"), inline=False)
            unboost_embed.add_field(name="🔗 Profile:", value=f"[View Profile](https://discord.com/users/{after.id})", inline=False)

            await port_of_dover_channel.send(embed=unboost_embed)

    if changes_detected:
        await updates_channel.send(embed=embed)
        logger.info(f"Changes detected for {after.name}: {embed.to_dict()}")
    else:
        logger.info("No relevant changes detected.")



