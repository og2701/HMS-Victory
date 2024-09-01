import discord
from discord import Interaction, InteractionType
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.utils import restrict_channel_for_new_members
from lib.log_functions import create_message_image, create_edited_message_image
from lib.settings import POLITICS_WHITELISTED_USER_IDS
import logging
import os
import aiohttp
import io
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_CACHE_CHANNEL = 1271188365244497971
POLITICS_CHANNEL_ID = 1141097424849481799
PORT_OF_DOVER_CHANNEL_ID = 1131633452022767698


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
    if not await restrict_channel_for_new_members(message, POLITICS_CHANNEL_ID, 7, POLITICS_WHITELISTED_USER_IDS):
        return

    if message.attachments:
        cache_channel = client.get_channel(IMAGE_CACHE_CHANNEL)
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
    pass

async def on_reaction_remove(reaction, user):
    pass

async def on_member_update(before, after):
    updates_channel_id = 1279873633602244668
    updates_channel = after.guild.get_channel(updates_channel_id)

    if updates_channel is None:
        return

    embed = discord.Embed(
        title="Member Update",
        description=f"Changes for {after.mention}",
        color=discord.Color.blue()
    )

    changes_detected = False

    if before.name != after.name or before.discriminator != after.discriminator:
        embed.add_field(name="Username Changed", value=f"**Before:** {before.name}#{before.discriminator}\n**After:** {after.name}#{after.discriminator}", inline=False)
        changes_detected = True

    if before.nick != after.nick:
        embed.add_field(name="Nickname Changed", value=f"**Before:** {before.nick or 'None'}\n**After:** {after.nick or 'None'}", inline=False)
        changes_detected = True

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

    if before.avatar != after.avatar:
        embed.add_field(name="Avatar Changed", value=f"[View Avatar](https://cdn.discordapp.com/avatars/{after.id}/{after.avatar}.png?size=1024)" if after.avatar else "Removed avatar", inline=False)
        if after.avatar:
            embed.set_thumbnail(url=after.avatar.url)
        changes_detected = True

    if before.status != after.status:
        embed.add_field(name="Status Changed", value=f"**Before:** {before.status}\n**After:** {after.status}", inline=False)
        changes_detected = True

    if before.activity != after.activity:
        before_activity = before.activity.name if before.activity else "None"
        after_activity = after.activity.name if after.activity else "None"
        embed.add_field(name="Activity Changed", value=f"**Before:** {before_activity}\n**After:** {after_activity}", inline=False)
        changes_detected = True

    if before.mute != after.mute:
        embed.add_field(name="Server Mute Changed", value=f"**Before:** {'Muted' if before.mute else 'Unmuted'}\n**After:** {'Muted' if after.mute else 'Unmuted'}", inline=False)
        changes_detected = True

    if before.deaf != after.deaf:
        embed.add_field(name="Server Deafen Changed", value=f"**Before:** {'Deafened' if before.deaf else 'Undeafened'}\n**After:** {'Deafened' if after.deaf else 'Undeafened'}", inline=False)
        changes_detected = True

    if before.voice and after.voice and before.voice.channel != after.voice.channel:
        embed.add_field(name="Voice Channel Changed", value=f"**Before:** {before.voice.channel.name}\n**After:** {after.voice.channel.name}", inline=False)
        changes_detected = True

    if before.pending != after.pending:
        embed.add_field(name="Pending Status Changed", value=f"**Before:** {'Pending' if before.pending else 'Not Pending'}\n**After:** {'Pending' if after.pending else 'Not Pending'}", inline=False)
        changes_detected = True

    if before.premium_since != after.premium_since:
        if before.premium_since is None and after.premium_since is not None:
            embed.add_field(name="Boost Status", value="Started Boosting", inline=False)
        elif before.premium_since is not None and after.premium_since is None:
            embed.add_field(name="Boost Status", value="Stopped Boosting", inline=False)
        changes_detected = True

    if before.timed_out_until != after.timed_out_until:
        if after.timed_out_until is not None:
            embed.add_field(name="Timeout Status", value=f"Timed out until {after.timed_out_until.strftime('%B %d, %Y at %H:%M UTC')}", inline=False)
        else:
            embed.add_field(name="Timeout Status", value="Timeout cleared", inline=False)
        changes_detected = True

    if changes_detected:
        await updates_channel.send(embed=embed)

    # if before.premium_since is None and after.premium_since is not None:
    #     port_of_dover_channel = after.guild.get_channel(PORT_OF_DOVER_CHANNEL_ID)
    #     if port_of_dover_channel:
    #         embed = discord.Embed(
    #             title="🎉 New Server Boost! 🎉",
    #             description=f"{after.mention} has just boosted the server!",
    #             color=discord.Color.purple(),
    #             timestamp=after.premium_since
    #         )
    #         embed.set_thumbnail(url=after.avatar.url if after.avatar else after.default_avatar.url)

    #         embed.add_field(name="👤 Booster:", value=f"{after.name}#{after.discriminator} ({after.id})", inline=False)
    #         embed.add_field(name="📅 Boosted On:", value=after.premium_since.strftime("%B %d, %Y at %H:%M UTC"), inline=False)
    #         embed.add_field(name="🔢 Total Boosts:", value=f"{after.guild.premium_subscription_count}", inline=True)
    #         embed.add_field(name="🎉 Total Boosters:", value=f"{len(after.guild.premium_subscribers)}", inline=True)
    #         tier_info = {
    #             0: "Tier 0 (No Level)",
    #             1: "Tier 1 (Level 1)",
    #             2: "Tier 2 (Level 2)",
    #             3: "Tier 3 (Level 3)"
    #         }
    #         current_tier = after.guild.premium_tier
    #         embed.add_field(name="🏆 Current Boost Level:", value=tier_info.get(current_tier, "Unknown"), inline=False)
    #         embed.add_field(name="🔗 Profile:", value=f"[View Profile](https://discord.com/users/{after.id})", inline=False)
    #         embed.set_image(url="https://i.redd.it/qq911bvdqwu51.gif")

    #         await port_of_dover_channel.send(embed=embed)
    
    # elif before.premium_since is not None and after.premium_since is None:
    #     port_of_dover_channel = after.guild.get_channel(PORT_OF_DOVER_CHANNEL_ID)
    #     if port_of_dover_channel:
    #         embed = discord.Embed(
    #             title="⚠️ Server Boost Lost ⚠️",
    #             description=f"{after.mention} has stopped boosting the server.",
    #             color=discord.Color.red(),
    #             timestamp=discord.utils.utcnow()
    #         )
    #         embed.set_thumbnail(url=after.avatar.url if after.avatar else after.default_avatar.url)

    #         embed.add_field(name="👤 Former Booster:", value=f"{after.name}#{after.discriminator} ({after.id})", inline=False)
    #         embed.add_field(name="📅 Boost Started On:", value=before.premium_since.strftime("%B %d, %Y at %H:%M UTC"), inline=False)
    #         embed.add_field(name="❌ Boost Ended On:", value=discord.utils.utcnow().strftime("%B %d, %Y at %H:%M UTC"), inline=False)
    #         embed.add_field(name="🔢 Total Boosts Now:", value=f"{after.guild.premium_subscription_count}", inline=True)
    #         embed.add_field(name="🎉 Total Boosters Now:", value=f"{len(after.guild.premium_subscribers)}", inline=True)

    #         tier_info = {
    #             0: "Tier 0 (No Level)",
    #             1: "Tier 1 (Level 1)",
    #             2: "Tier 2 (Level 2)",
    #             3: "Tier 3 (Level 3)"
    #         }
    #         current_tier = after.guild.premium_tier
    #         embed.add_field(name="🏆 Current Boost Level:", value=tier_info.get(current_tier, "Unknown"), inline=False)
    #         embed.add_field(name="🔗 Profile:", value=f"[View Profile](https://discord.com/users/{after.id})", inline=False)

    #         await port_of_dover_channel.send(embed=embed)



