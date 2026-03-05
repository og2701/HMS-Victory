import discord
from discord.ui import Select, View, Button
from typing import List
from lib.economy.shop_items import get_shop_items, get_shop_item_by_id, ShopItem
from lib.economy.economy_manager import get_bb, remove_bb, ensure_bb

from lib.economy.shop_ui import ShopMainView

async def handle_shop_command(interaction: discord.Interaction):
    """
    Display the server shop where users can purchase items with UKPence.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """
    ensure_bb(interaction.user.id)
    user_balance = get_bb(interaction.user.id)

    items = get_shop_items()
    if not items:
        shop_embed = discord.Embed(
            title="🛒 UKPlace Shop",
            description="The shop is currently empty. Check back later!",
            color=0x0099ff
        )
        await interaction.response.send_message(embed=shop_embed, ephemeral=True)
        return

    from lib.economy.shop_ui import ShopBrowserView
    view = ShopBrowserView(items, interaction.user.id)
    await interaction.response.send_message(embed=view._create_embed(), view=view, ephemeral=True)