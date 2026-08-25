"""Bouncy Yaris chain reaction in #general.

If 3 or more distinct members post the :Bouncy_Yaris: custom emoji in #general
within a 2-minute window, the bot joins the chain and sends :Bouncy_Yaris: too.
"""

import logging
import re
import time
from typing import List, Tuple

import discord
import config

logger = logging.getLogger(__name__)

# Matches <:Bouncy_Yaris:123>, <a:Bouncy_Yaris:123>, or standard :Bouncy_Yaris: shortcode
YARIS_PATTERN = re.compile(r"<a?:bouncy_yaris:\d+>|:bouncy_yaris:", re.IGNORECASE)
BOUNCY_YARIS_EMOJI = "<a:Bouncy_Yaris:1540334892317933599>"
WINDOW_SECONDS = 120
THRESHOLD_USERS = 3

# In-memory tracking: [(user_id, timestamp), ...]
_recent_posts: List[Tuple[int, float]] = []
_last_bot_post: float = 0.0


def has_yaris_emoji(content: str) -> bool:
    """Check if message content contains the Bouncy_Yaris custom emoji or shortcode."""
    if not content:
        return False
    return bool(YARIS_PATTERN.search(content))


def record_and_check_yaris(user_id: int, now: float = None) -> bool:
    """Record a user posting Bouncy_Yaris and return True if 3+ distinct users posted within 2 mins."""
    global _recent_posts, _last_bot_post
    now = now if now is not None else time.time()

    # Prune posts older than WINDOW_SECONDS
    _recent_posts = [(uid, ts) for uid, ts in _recent_posts if now - ts <= WINDOW_SECONDS]

    # Update or add this user's latest post timestamp
    _recent_posts = [(uid, ts) for uid, ts in _recent_posts if uid != user_id]
    _recent_posts.append((user_id, now))

    # Check distinct users count
    distinct_users = {uid for uid, _ in _recent_posts}
    if len(distinct_users) >= THRESHOLD_USERS:
        # Prevent spamming bot replies if already triggered recently
        if now - _last_bot_post >= WINDOW_SECONDS:
            _last_bot_post = now
            _recent_posts.clear()  # reset chain once triggered
            return True

    return False


def reset_yaris_state() -> None:
    """Reset the tracker state (useful for tests)."""
    global _recent_posts, _last_bot_post
    _recent_posts.clear()
    _last_bot_post = 0.0


async def handle_yaris_chain(client, message: discord.Message) -> bool:
    """Check message in #general for Bouncy_Yaris and respond if threshold is met."""
    if getattr(message.author, "bot", False):
        return False

    general_id = getattr(config.CHANNELS, "GENERAL", None)
    if general_id and getattr(message.channel, "id", None) != general_id:
        return False

    if not has_yaris_emoji(getattr(message, "content", "")):
        return False

    if record_and_check_yaris(message.author.id):
        try:
            await message.channel.send(BOUNCY_YARIS_EMOJI)
            logger.info("Joined Bouncy Yaris chain in #general")
            return True
        except Exception:
            logger.exception("Failed to send Bouncy Yaris in #general")

    return False
