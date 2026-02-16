import discord
from discord import Interaction, Member, TextChannel
from datetime import datetime, timezone, timedelta
import pytz
from typing import Optional

async def restrict_channel_for_new_members(
    message: discord.Message,
    channel_id: int,
    days_required: int = 7,
    whitelisted_user_ids: list[int] = [],
) -> bool:
    if message.channel.id == channel_id:
        if message.author.id in whitelisted_user_ids:
            return True
        join_date = message.author.joined_at
        if (join_date is None) or ((datetime.now(timezone.utc) - join_date).days < days_required):
            await message.delete()
            await message.channel.send(
                f"{message.author.mention}, you need to be in the server for at least {days_required} days to use this channel. If you believe you should be whitelisted, please <#1143560594138595439>",
                delete_after=10,
            )
            return False
    return True

def has_role(interaction: Interaction, role_id: int) -> bool:
    return any(role.id == role_id for role in interaction.user.roles)

def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in interaction.user.roles)

async def toggle_user_role(interaction: Interaction, user: Member, role: discord.Role) -> None:
    if role in user.roles:
        await user.remove_roles(role)
        await interaction.response.send_message(
            f"Role {role.name} has been removed from {user.mention}."
        )
    else:
        await user.add_roles(role)
        await interaction.response.send_message(
            f"Role {role.name} has been assigned to {user.mention}."
            )

async def validate_and_format_date(interaction: Interaction, date_str: Optional[str] = None) -> Optional[str]:
    if date_str is None:
        uk_timezone = pytz.timezone("Europe/London")
        return datetime.now(uk_timezone).strftime("%Y-%m-%d")
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%Y-%m-%d")
    except ValueError:
        await interaction.response.send_message("Invalid date format. Please use YYYY-MM-DD.", ephemeral=True)
        return None

async def send_embed_to_channels(guild: discord.Guild, embed: discord.Embed, channel_ids: list[int]) -> None:
    for cid in channel_ids:
        channel = guild.get_channel(cid)
        if channel:
            await channel.send(embed=embed)

async def edit_voice_channel_members(
    guild: discord.Guild,
    mute: bool,
    deafen: bool,
    whitelist: Optional[list[int]] = None
) -> None:
    for channel in guild.voice_channels:
        for member in channel.members:
            if whitelist:
                if any(role.id in whitelist for role in member.roles):
                    continue
            await member.edit(mute=mute, deafen=deafen)

async def fetch_messages_with_context(
    channel: TextChannel,
    user: Member,
    user_messages: list,
    total_limit: int = 100,
    context_depth: int = 2
) -> None:
    try:
        user_message_count = 0
        message_history = []
        async for message in channel.history(limit=None, after=datetime.utcnow() - timedelta(days=7), oldest_first=True):
            if message.author.bot:
                continue
            message_history.append(message)
            if message.author == user:
                user_message_count += 1
                if user_message_count >= total_limit:
                    break

        i = 0
        while i < len(message_history):
            message = message_history[i]
            if message.author == user:
                context = []
                context_count = 0
                j = i - 1
                while context_count < context_depth and j >= 0:
                    if (not message_history[j].author.bot) and (message_history[j].author != user):
                        context.append(message_history[j])
                        context_count += 1
                    j -= 1
                context.reverse()

                user_message_block = []
                while i < len(message_history) and message_history[i].author == user:
                    user_message_block.append(
                        f"{message_history[i].created_at.strftime('%Y-%m-%d %H:%M:%S')} - {user.display_name}: {message_history[i].content}"
                    )
                    i += 1

                user_message_block_text = "\n".join(user_message_block)
                if context:
                    context_text = "\n".join(
                        [f"{m.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {m.author.display_name}: {m.content}" for m in context]
                    )
                    user_messages.append(f"Context:\n{context_text}\n{user_message_block_text}")
                else:
                    user_messages.append(user_message_block_text)
            else:
                i += 1
    except discord.Forbidden:
        pass

def estimate_tokens(text: str) -> int:
    return len(text.split())