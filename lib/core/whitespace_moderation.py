"""Moderation filter for chat-clearing whitespace, invisible character floods, and newline nukes.

Detects and automatically deletes messages that disrupt chat by flooding screens with
excessive vertical blank lines, markdown spacers (e.g. '_ _'), or zero-width character floods.
"""
import logging
import re
from typing import Tuple

import discord

from config import CHANNELS

logger = logging.getLogger(__name__)

# Feature toggle
WHITESPACE_FILTER_ENABLED: bool = True

# Spacers regex: whitespace, markdown spacers ('_'), and Unicode zero-width/invisible characters
# ​: zero-width space
# ‌: zero-width non-joiner
# ‍: zero-width joiner
# ﻿: zero-width no-break space / BOM
# ⠀: braille pattern blank
SPACER_REGEX = re.compile(r"[\s_​‌‍﻿⠀]")

# Thresholds
MAX_CONSECUTIVE_BLANK_LINES = 15
MAX_TOTAL_EMPTY_LINES = 25
MIN_SUBSTANTIVE_TEXT_FOR_LINE_FLOOD = 40
MIN_FLOOD_LENGTH = 150


def is_excessive_whitespace(content: str) -> Tuple[bool, str]:
    """Check if message content contains chat-clearing whitespace or newline floods."""
    if not content or len(content) < 15:
        return False, ""

    # 1. Check for excessive consecutive blank / invisible spacer lines
    # Match sequences of newlines separated only by whitespace or invisible spacers
    consecutive_blank_lines = re.search(
        r"(?:\r?\n[\s_\u200b\u200c\u200d\ufeff\u2800]*){" + str(MAX_CONSECUTIVE_BLANK_LINES) + r",}",
        content,
    )
    if consecutive_blank_lines:
        line_count = consecutive_blank_lines.group(0).count("\n")
        return True, f"Excessive consecutive blank lines ({line_count} lines)"

    # 2. Check total newlines with low substantive text
    total_newlines = content.count("\n")
    if total_newlines >= MAX_TOTAL_EMPTY_LINES:
        substantive = SPACER_REGEX.sub("", content)
        if len(substantive) < MIN_SUBSTANTIVE_TEXT_FOR_LINE_FLOOD:
            return True, f"Excessive newlines ({total_newlines} lines) with low substantive text ({len(substantive)} chars)"

    # 3. Massive whitespace or invisible character flood
    if len(content) >= MIN_FLOOD_LENGTH:
        substantive = SPACER_REGEX.sub("", content)
        if len(substantive) < 10 and (len(substantive) / len(content)) < 0.05:
            return True, f"Massive invisible/whitespace character flood ({len(content)} chars, {len(substantive)} visible)"

    return False, ""


def is_exempt(member: discord.Member) -> bool:
    """Staff with manage_messages or administrator permissions are exempt."""
    perms = getattr(member, "guild_permissions", None)
    if perms and (perms.administrator or perms.manage_messages):
        return True
    return False


async def check_whitespace_spam(client: discord.Client, message: discord.Message) -> bool:
    """Inspects a message for chat-clearing whitespace and deletes if detected."""
    if not WHITESPACE_FILTER_ENABLED:
        return False

    if message.author.bot or message.guild is None or getattr(message, "webhook_id", None):
        return False

    # Check staff exemption
    author = message.author
    if isinstance(author, discord.Member) and is_exempt(author):
        return False

    content = message.content or ""
    is_blocked, reason = is_excessive_whitespace(content)
    if not is_blocked:
        return False

    # Delete message
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
        logger.warning(f"Could not delete whitespace spam message {message.id}: {e}")

    logger.info(
        f"Deleted whitespace spam message from {author} ({author.id}) in #{getattr(message.channel, 'name', message.channel.id)}: {reason}"
    )

    # Log to logs channel
    log_ch = client.get_channel(CHANNELS.LOGS) or client.get_channel(CHANNELS.POLICE_STATION)
    if log_ch:
        try:
            embed = discord.Embed(
                title="🚫 Chat-Clearing Whitespace Deleted",
                description=(
                    f"Deleted a message from {author.mention} (`{author.id}`) in {message.channel.mention}.\n\n"
                    f"**Reason**: {reason}\n"
                    f"**Total Length**: {len(content):,} chars\n"
                    f"**Total Newlines**: {content.count(chr(10)):,}"
                ),
                color=discord.Color.dark_orange(),
                timestamp=discord.utils.utcnow(),
            )
            embed.set_footer(text="Anti-Nuke / Whitespace Auto-Moderation")
            await log_ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except Exception as le:
            logger.debug(f"Failed to log whitespace deletion: {le}")

    return True
