import logging
import discord
from typing import Optional

logger = logging.getLogger(__name__)


def _safe_webhook_username(member: discord.Member) -> str:
    """Discord rejects webhook usernames containing 'clyde'/'discord' and caps
    them at 80 chars. Sanitise so an awkward display name can't make the send fail."""
    name = member.display_name or member.name or "Member"
    lowered = name.lower()
    if "clyde" in lowered or "discord" in lowered:
        name = member.name or "Member"
        if "clyde" in name.lower() or "discord" in name.lower():
            name = "Member"
    name = name[:80].strip() or "Member"
    return name


async def send_as_webhook(channel: discord.TextChannel, member: discord.Member, content: str) -> Optional[discord.Message]:
    """
    Sends a message to a channel via a webhook, impersonating a member.

    Mentions are fully neutralised (AllowedMentions.none()): webhooks bypass the
    original author's "Mention Everyone" permission, so re-broadcasting raw content
    would otherwise let a user launder an @everyone/@here/role ping through the bot.
    """
    try:
        webhooks = await channel.webhooks()
        webhook = next((wh for wh in webhooks if wh.name == "HMS-Victory Webhook"), None)

        if not webhook:
            webhook = await channel.create_webhook(name="HMS-Victory Webhook")

        return await webhook.send(
            content=(content or "")[:2000],
            username=_safe_webhook_username(member),
            avatar_url=member.display_avatar.url,
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
        )
    except discord.HTTPException as e:
        logger.warning(f"Failed to send webhook message in #{getattr(channel, 'name', '?')}: {e}")
        return None
