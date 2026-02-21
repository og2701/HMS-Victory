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

    shop_embed = discord.Embed(
        title="🛒 UKPlace Shop",
        description="Purchase items using your UKPence. Select an item below to see details and purchase.",
        color=0x0099ff
    )

    shop_embed.add_field(
        name="💰 Your Balance",
        value=f"{user_balance} UKPence",
        inline=True
    )

    shop_embed.set_footer(
        text="ℹ️ How to Earn UKPence: Daily chat rewards (top chatters), Server boosting bonus, Participating in voice stages"
    )

    items = get_shop_items()
    if not items:
        shop_embed.add_field(
            name="🔒 Shop Status",
            value="The shop is currently empty. Check back later!",
            inline=False
        )
        await interaction.response.send_message(embed=shop_embed, ephemeral=True)
        return

    # Add a sample of items to the embed
    item_list = []
    for item in items[:10]:  # Show first 10 items
        affordable = "✅" if user_balance >= item.price else "❌"
        quantity = item.get_quantity()

        display_name = item.get_display_name()
        item_list.append(f"{affordable} **{display_name}** - {item.price} UKPence - {quantity} remaining")

    shop_embed.add_field(
        name="🛍️ Available Items (Select below for more)",
        value="\n".join(item_list),
        inline=False
    )

    view = ShopMainView(items)
    await interaction.response.send_message(embed=shop_embed, view=view, ephemeral=True)