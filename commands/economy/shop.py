import discord
from discord.ui import Select, View, Button
from typing import List
from lib.shop_items import get_shop_items, get_shop_item_by_id, ShopItem
from lib.economy_manager import get_bb, remove_bb, ensure_bb

class ShopItemSelect(Select):
    def __init__(self, items: List[ShopItem]):
        options = []
        for item in items[:25]:  # Discord limit of 25 options
            # Check stock status for description
            quantity = item.get_quantity()
            if quantity is not None and quantity <= 0:
                stock_info = " (SOLD OUT)"
                description = f"{item.price} UKPence - OUT OF STOCK"
            elif quantity is not None and quantity <= 5:
                stock_info = f" ({quantity} left)"
                description = f"{item.price} UKPence - {item.description[:40]}... {stock_info}"
            else:
                stock_info = ""
                description = f"{item.price} UKPence - {item.description[:50]}..."

            options.append(discord.SelectOption(
                label=item.get_display_name(),
                description=description[:100],  # Discord limit
                value=item.id,
                emoji="🔴" if quantity is not None and quantity <= 0 else "✅"
            ))

        super().__init__(
            placeholder="Choose an item to purchase...",
            options=options,
            min_values=1,
            max_values=1
        )
        self.items = {item.id: item for item in items}

    async def callback(self, interaction: discord.Interaction):
        selected_item = self.items[self.values[0]]

        # Check if user can purchase this item
        can_purchase, reason = selected_item.can_purchase(interaction.user)
        if not can_purchase:
            await interaction.response.send_message(f"❌ Cannot purchase: {reason}", ephemeral=True)
            return

        # Show confirmation modal
        view = PurchaseConfirmationView(selected_item)
        embed = discord.Embed(
            title="Confirm Purchase",
            description=f"**{selected_item.get_display_name()}**\n{selected_item.description}",
            color=0x00ff00
        )
        embed.add_field(name="Price", value=f"{selected_item.price} UKPence", inline=True)
        embed.add_field(name="Your Balance", value=f"{get_bb(interaction.user.id)} UKPence", inline=True)

        # Add stock info
        quantity = selected_item.get_quantity()
        if quantity is not None:
            if quantity <= 5:
                embed.add_field(name="⚠️ Stock", value=f"Only {quantity} remaining!", inline=True)
            else:
                embed.add_field(name="📦 Stock", value="In Stock", inline=True)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class PurchaseConfirmationView(View):
    def __init__(self, item: ShopItem):
        super().__init__(timeout=300)
        self.item = item

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.green, emoji="✅")
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        ensure_bb(interaction.user.id)
        user_balance = get_bb(interaction.user.id)

        if user_balance < self.item.price:
            await interaction.response.send_message(
                f"❌ Insufficient funds! You need {self.item.price} UKPence but only have {user_balance}.",
                ephemeral=True
            )
            return

        # Check again if user can purchase (in case something changed)
        can_purchase, reason = self.item.can_purchase(interaction.user)
        if not can_purchase:
            await interaction.response.send_message(f"❌ Cannot purchase: {reason}", ephemeral=True)
            return

        # Process the purchase
        if remove_bb(interaction.user.id, self.item.price):
            try:
                # Use the new purchase method that handles inventory
                success, result_message = await self.item.purchase(str(interaction.user.id), interaction)

                if not success:
                    # Refund if purchase failed
                    from lib.economy_manager import add_bb
                    add_bb(interaction.user.id, self.item.price)
                    await interaction.response.send_message(
                        f"❌ Purchase failed: {result_message}",
                        ephemeral=True
                    )
                    return

                embed = discord.Embed(
                    title="Purchase Successful! ✅",
                    description=result_message,
                    color=0x00ff00
                )
                embed.add_field(
                    name="Remaining Balance",
                    value=f"{get_bb(interaction.user.id)} UKPence",
                    inline=False
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)

                # Log the purchase
                log_channel = interaction.guild.get_channel(1197572903294730270)  # BOT_USAGE_LOG
                if log_channel:
                    log_embed = discord.Embed(
                        title="Shop Purchase",
                        color=0x00ff00
                    )
                    log_embed.add_field(name="User", value=interaction.user.mention, inline=True)
                    log_embed.add_field(name="Item", value=self.item.name, inline=True)
                    log_embed.add_field(name="Price", value=f"{self.item.price} UKPence", inline=True)
                    await log_channel.send(embed=log_embed)

            except Exception as e:
                # Refund on error
                from lib.economy_manager import add_bb
                add_bb(interaction.user.id, self.item.price)
                await interaction.response.send_message(
                    f"❌ An error occurred during purchase. Your UKPence has been refunded.\nError: {str(e)}",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message(
                "❌ Payment failed. Please try again.",
                ephemeral=True
            )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("❌ Purchase cancelled.", ephemeral=True)

class ShopView(View):
    def __init__(self):
        super().__init__(timeout=300)

        items = get_shop_items()
        if items:
            self.add_item(ShopItemSelect(items))

    async def on_timeout(self):
        # Disable all components when the view times out
        for item in self.children:
            item.disabled = True

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

        display_name = item.get_display_name()
        item_list.append(f"{affordable} **{display_name}** - {item.price} UKPence - {quantity} remaining")

    shop_embed.add_field(
        name="🛍️ Available Items (Select below for more)",
        value="\n".join(item_list),
        inline=False
    )

    view = ShopView()
    await interaction.response.send_message(embed=shop_embed, view=view, ephemeral=True)