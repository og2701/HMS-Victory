import discord
from typing import Optional

async def send_as_webhook(channel: discord.TextChannel, member: discord.Member, content: str) -> Optional[discord.Message]:
    """
    Sends a message to a channel via a webhook, impersonating a member.
    """
    webhooks = await channel.webhooks()
    webhook = next((wh for wh in webhooks if wh.name == "HMS-Victory Webhook"), None)
    
    if not webhook:
        webhook = await channel.create_webhook(name="HMS-Victory Webhook")
    
    return await webhook.send(
        content=content,
        username=member.display_name,
        avatar_url=member.display_avatar.url,
        wait=True
    )
