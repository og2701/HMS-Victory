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
                f"{message.author.mention}, you need to be in the server for at least {days_required} days to use this channel.",
                delete_after=10,
            )
            return False
    return True

def has_role(interaction: Interaction, role_id: int) -> bool:
    return any(role.id == role_id for role in interaction.user.roles)

def has_any_role(interaction: Interaction, role_ids: list[int]) -> bool:
    return any(role.id in role_ids for role in interaction.user.roles)

def can_moderate_target(interaction: Interaction, target: Member) -> Optional[str]:
    """Return None if `interaction.user` may apply a punitive role-toggle to
    `target`, else a human-readable refusal. Guards against targeting the bot,
    the guild owner, oneself, or anyone with an equal/higher top role (the owner
    is exempt from the hierarchy check)."""
    guild = interaction.guild
    invoker = interaction.user
    if target.bot:
        return "You can't target a bot."
    if guild and target.id == guild.owner_id:
        return "You can't target the server owner."
    if target.id == invoker.id:
        return "You can't target yourself."
    if guild and invoker.id != guild.owner_id and target.top_role >= invoker.top_role:
        return "You can't target someone with an equal or higher role than you."
    return None

async def toggle_user_role(interaction: Interaction, user: Member, role: discord.Role) -> None:
    try:
        if role in user.roles:
            await user.remove_roles(role)
            message = f"Role {role.name} has been removed from {user.mention}."
        else:
            await user.add_roles(role)
            message = f"Role {role.name} has been assigned to {user.mention}."
    except discord.Forbidden:
        message = f"I don't have permission to manage the **{role.name}** role for {user.mention} (it may sit above my highest role)."
    except discord.HTTPException as e:
        message = f"Failed to update {user.mention}'s roles: {e}"

    if interaction.response.is_done():
        await interaction.followup.send(message)
    else:
        await interaction.response.send_message(message)

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
    context_depth: int = 8,
    history_limit: int = 1000
) -> None:
    try:
        user_message_count = 0
        message_history = []
        async for message in channel.history(limit=history_limit):
            if message.author.bot:
                continue
            message_history.append(message)
            if message.author == user:
                user_message_count += 1
                if user_message_count >= total_limit:
                    break

        message_history.reverse()

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
                    msg = message_history[i]
                    reactions_text = ""
                    if msg.reactions:
                        reactions = [f"{str(r.emoji)}x{r.count}" for r in msg.reactions]
                        reactions_text = f" [Reactions: {', '.join(reactions)}]"
                    
                    user_message_block.append(
                        f"{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {user.display_name}: {msg.content}{reactions_text}"
                    )
                    i += 1

                user_message_block_text = "\n".join(user_message_block)

                context_after = []
                context_after_count = 0
                k = i
                while context_after_count < context_depth and k < len(message_history) and message_history[k].author != user:
                    if not message_history[k].author.bot:
                        context_after.append(message_history[k])
                        context_after_count += 1
                    k += 1

                parts = []
                if context:
                    context_lines = []
                    for m in context:
                        r_text = f" [Reactions: {', '.join([f'{str(r.emoji)}x{r.count}' for r in m.reactions])}]" if m.reactions else ""
                        context_lines.append(f"{m.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {m.author.display_name}: {m.content}{r_text}")
                    parts.append(f"Context (Before):\n" + "\n".join(context_lines))
                
                parts.append(f"Target User ({user.display_name}) in #{channel.name}:\n{user_message_block_text}")
                
                if context_after:
                    after_lines = []
                    for m in context_after:
                        r_text = f" [Reactions: {', '.join([f'{str(r.emoji)}x{r.count}' for r in m.reactions])}]" if m.reactions else ""
                        after_lines.append(f"{m.created_at.strftime('%Y-%m-%d %H:%M:%S')} - {m.author.display_name}: {m.content}{r_text}")
                    parts.append(f"Context (After/Reactions):\n" + "\n".join(after_lines))
                
                user_messages.append("\n\n---\n".join(parts))
            else:
                i += 1
    except discord.Forbidden:
        pass

def estimate_tokens(text: str) -> int:
    return len(text.split())