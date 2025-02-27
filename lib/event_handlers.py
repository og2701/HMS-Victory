import discord
from discord import Interaction, InteractionType
from datetime import timedelta, datetime
import logging
import os
import aiohttp
import io
from apscheduler.triggers.cron import CronTrigger
import json
from collections import defaultdict
import asyncio

from lib.translation import translate_and_send
from lib.summary import initialize_summary_data, update_summary_data, post_summary
from lib.utils import *
from lib.log_functions import create_message_image, create_edited_message_image
from lib.settings import *

from commands.mod_commands.persistant_role_buttons import persistantRoleButtons, handleRoleButtonInteraction
from commands.mod_commands.announcement_command import RoleButtonView
from commands.mod_commands.anti_raid import handle_new_member_anti_raid
from commands.mod_commands.archive_channel import ArchiveButtonView, schedule_archive_move

logger = logging.getLogger(__name__)

MAX_IMAGE_SIZE = 5 * 1024 * 1024

sticker_messages = {}

recently_flagged_users = defaultdict(bool)

all_onboarding_roles = {
    ROLES.BRITISH,
    ROLES.ENGLISH,
    ROLES.SCOTTISH,
    ROLES.WELSH,
    ROLES.NORTHERN_IRISH,
    ROLES.COMMONWEALTH,
    ROLES.VISITOR,
}
nationality_onboarding_roles = {
    ROLES.ENGLISH,
    ROLES.SCOTTISH, 
    ROLES.WELSH,
    ROLES.NORTHERN_IRISH,
}

def is_lockdown_active():
    return os.path.exists(VC_LOCKDOWN_FILE)

async def on_ready(client, tree, scheduler):
    global POLITICS_WHITELISTED_USER_IDS

    if not client.synced:
        await tree.sync()
        client.synced = True
    
    logger.info(f"Logged in as {client.user}")

    if not hasattr(client, 'temp_data'):
        client.temp_data = {}

    persistent_views = load_persistent_views()
    for key, value in persistent_views.items():
        if key.startswith("archive_") and isinstance(value, dict) and "move_timestamp" in value and "msg_id" in value:
            channel_id = int(key.split("_")[1])
            channel = client.get_channel(channel_id)
            if channel:
                client.add_view(ArchiveButtonView(client, channel_id), message_id=value["msg_id"])
                target_timestamp = value["move_timestamp"]
                asyncio.create_task(schedule_archive_move(channel, channel.guild, target_timestamp, client))

        elif isinstance(value, dict):
            view = RoleButtonView(value)
            client.add_view(view, message_id=key)


    logger.info("Persistent views reattached and loaded.")

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

    if "discord.com/channels/" in message.content:
        try:
            link_parts = message.content.split("/")
            guild_id = int(link_parts[4])
            channel_id = int(link_parts[5])
            message_id = int(link_parts[6])

            guild = client.get_guild(guild_id)
            channel = guild.get_channel(channel_id)
            quoted_message = await channel.fetch_message(message_id)

            timestamp_unix = int(quoted_message.created_at.timestamp())
            timestamp_formatted = f"<t:{timestamp_unix}:f>"
            channel_name = channel.name
            reply_content = f"@__{quoted_message.author}__ in *{channel_name}* {timestamp_formatted}:\n"

            if quoted_message.content:
                reply_content += f"> {quoted_message.content}"

            if quoted_message.attachments:
                attachment = quoted_message.attachments[0]
                if attachment.content_type and attachment.content_type.startswith("image/") and attachment.size <= MAX_IMAGE_SIZE:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as response:
                            if response.status == 200:
                                image_data = await response.read()
                                image_file = discord.File(io.BytesIO(image_data), filename=attachment.filename)
                                reply = await message.channel.send(content=reply_content, file=image_file)
                elif attachment.size > MAX_IMAGE_SIZE:
                    reply = await message.channel.send(f"{reply_content}\nAttachment is too large to display (max {MAX_IMAGE_SIZE / (1024 * 1024)} MB).")
                else:
                    reply = await message.channel.send(f"{reply_content}\n[Attachment: {attachment.url}]")
            elif quoted_message.embeds:
                embed = quoted_message.embeds[0]
                embed_copy = discord.Embed.from_dict(embed.to_dict())
                reply = await message.channel.send(content=reply_content, embed=embed_copy)
            else:
                reply = await message.channel.send(reply_content)

            await reply.add_reaction("❌")

            def check(reaction, user):
                return (
                    user == message.author
                    and str(reaction.emoji) == "❌"
                    and reaction.message.id == reply.id
                )

            try:
                await client.wait_for("reaction_add", timeout=20.0, check=check)
                await reply.delete()
            except asyncio.TimeoutError:
                await reply.clear_reactions()
        except Exception as e:
            logger.error(f"Error processing message link: {e}")

async def on_interaction(interaction: Interaction):
    if (
        interaction.type == InteractionType.component
        and "custom_id" in interaction.data
    ):
        custom_id = interaction.data["custom_id"]
        if custom_id.startswith("role_"):
            await handleRoleButtonInteraction(interaction)

async def on_member_join(member):
    await handle_new_member_anti_raid(member)
    role = member.guild.get_role(ROLES.MEMBER)
    if role:
        await member.add_roles(role)

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

    log_channel = client.get_channel(CHANNELS.LOGS)
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

    log_channel = client.get_channel(CHANNELS.LOGS)
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
    is_in_mapping = str(reaction.emoji) in FLAG_LANGUAGE_MAPPINGS
    try:
        if is_in_mapping:
            message = reaction.message
            target_language = FLAG_LANGUAGE_MAPPINGS[str(reaction.emoji)]

            users = [u async for u in reaction.users()]

            if len(users) > 1:
                logger.info("Message has already been reacted to with this flag. Skipping translation.")
                return

            if message.content:
                await translate_and_send(reaction, message, target_language, message.author, user)

        if ":Shut:" in str(reaction.emoji):
            has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
            if has_role:
                message_author = reaction.message.author

                if message_author.is_timed_out():
                    logger.info(f"User {message_author} is already timed out. Skipping further actions.")
                    return

                try:
                    reason = f"Timed out due to ':Shut:' reaction by {user.name}#{user.discriminator}."
                    duration = timedelta(minutes=5)
                    await message_author.timeout(discord.utils.utcnow() + duration, reason=reason)

                    sticker_message = await reaction.message.reply(stickers=[discord.Object(id=1298758779428536361)])
                    sticker_messages[reaction.message.id] = (sticker_message.id, user.id)
                    logger.info(f"User {message_author} was timed out for 5 minutes due to ':Shut:' reaction by {user}.")
                except Exception as e:
                    logger.error(f"Failed to time out user {message_author}: {e}")

    except Exception as e:
        logger.error(f"Error in on_reaction_add: {e}")


async def on_reaction_remove(reaction, user):
    if ":Shut:" in str(reaction.emoji):
        has_role = any(role.id in [ROLES.CABINET, ROLES.BORDER_FORCE] for role in user.roles)
        if has_role:
            message_author = reaction.message.author
            try:
                sticker_message_info = sticker_messages.get(reaction.message.id)
                if not sticker_message_info:
                    return

                sticker_message_id, initiating_mod_id = sticker_message_info

                if initiating_mod_id != user.id:
                    logger.info(f"Reaction removal ignored as {user} did not initiate the timeout.")
                    return

                reason = f"Timeout removed due to ':Shut:' reaction being removed by {user.name}#{user.discriminator}."
                await message_author.timeout(None, reason=reason)
                logger.info(f"Timeout for user {message_author} was removed due to ':Shut:' reaction being removed by {user}.")

                sticker_message = await reaction.message.channel.fetch_message(sticker_message_id)
                await sticker_message.delete()
                del sticker_messages[reaction.message.id]
                logger.info(f"Deleted sticker message with ID {sticker_message_id} due to reaction being removed.")

            except Exception as e:
                logger.error(f"Failed to remove timeout or delete sticker message for user {message_author}: {e}")

async def on_member_update(client, before, after):
    updates_channel = client.get_channel(CHANNELS.MEMBER_UPDATES)
    mod_channel = client.get_channel(CHANNELS.POLICE_STATION)
    user_id = after.id

    before_roles = {role.id for role in before.roles}
    after_roles = {role.id for role in after.roles}
    newly_assigned_roles = after_roles - before_roles

    if recently_flagged_users[user_id]:
        return

    if all_onboarding_roles.issubset(after_roles) and all_onboarding_roles.intersection(newly_assigned_roles):
        if mod_channel:
            await mod_channel.send(
                f"🚩 **Potential bot detected:** {after.mention}\n"
                f"Assigned themselves all onboarding roles: British, English, Scottish, Welsh, Northern Irish, Commonwealth, and Visitor. Please monitor."
            )
            recently_flagged_users[user_id] = True
            return

    if nationality_onboarding_roles.issubset(after_roles) and nationality_onboarding_roles.intersection(newly_assigned_roles):
        if mod_channel:
            await mod_channel.send(
                f"🚩 **Potential bot detected:** {after.mention}\n"
                f"Assigned themselves all nationality onboarding roles: English, Scottish, Welsh, and Northern Irish. Please monitor."
            )
            recently_flagged_users[user_id] = True


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


async def on_voice_state_update(member, before, after):
    if not is_lockdown_active():
        return

    if after.channel and not before.channel:
        if not any(role.id in VC_LOCKDOWN_WHITELIST for role in member.roles):
            await member.edit(mute=True, deafen=True)



