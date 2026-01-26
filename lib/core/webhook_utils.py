import discord
from typing import Optional

async def send_as_webhook(channel: discord.TextChannel, member: discord.Member, content: str) -> Optional[discord.Message]:
    """
    Sends a message to a channel via a webhook, impersonating a member.
    Uses an embed to match the member's role color.
    """
    webhooks = await channel.webhooks()
    webhook = next((wh for wh in webhooks if wh.name == "HMS-Victory Webhook"), None)
    
    if not webhook:
        webhook = await channel.create_webhook(name="HMS-Victory Webhook")
    
    # Use an embed to provide the role color side-stripe
    color = member.color if member.color != discord.Color.default() else None
    
    embed = discord.Embed(
        description=content,
        color=color
    )
    
    return await webhook.send(
        embed=embed,
        username=member.display_name,
        avatar_url=member.display_avatar.url,
        wait=True
    )
