import discord
from discord.ext import commands
from lib.shop_inventory import ShopInventory
from lib.shop_items import get_shop_items, get_shop_item_by_id
from config import ROLES, CHANNELS
from datetime import datetime
from typing import Optional

def is_staff(ctx):
    """Check if user has staff permissions"""
    staff_roles = [ROLES.DEPUTY_PM, ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
    return any(role.id in staff_roles for role in ctx.author.roles)

class InventoryCommands:
    """Staff commands for managing shop inventory"""

    @staticmethod
    async def inventory_status(ctx):
        """Show current inventory status for all items"""
        if not is_staff(ctx):
            await ctx.send("❌ Only staff members can view inventory status!", delete_after=5)
            return

        inventory = ShopInventory.get_all_inventory()
        shop_items = {item.id: item for item in get_shop_items()}

        if not inventory:
            await ctx.send("📦 No items in inventory database yet.")
            return

        embed = discord.Embed(
            title="📦 Shop Inventory Status",
            color=0x0099ff
        )

        status_lines = []
        for item_data in inventory:
            item_id = item_data['item_id']
            quantity = item_data['quantity']
            max_qty = item_data['max_quantity']
            auto_restock = item_data['auto_restock']

            # Get item details
            shop_item = shop_items.get(item_id)
            item_name = shop_item.name if shop_item else item_id

            # Status indicators
            if quantity <= 0:
                status = "🔴 SOLD OUT"
            elif quantity <= 5:
                status = f"⚠️ LOW ({quantity})"
            else:
                status = f"✅ {quantity}"

            max_info = f"/{max_qty}" if max_qty else "/∞"
            restock_info = "🔄" if auto_restock else ""

            status_lines.append(f"**{item_name}** `{item_id}`\n{status}{max_info} {restock_info}")

        # Split into chunks for embed fields
        chunk_size = 5
        for i in range(0, len(status_lines), chunk_size):
            chunk = status_lines[i:i+chunk_size]
            field_name = f"Items {i+1}-{min(i+chunk_size, len(status_lines))}"
            embed.add_field(name=field_name, value="\n\n".join(chunk), inline=False)

        embed.set_footer(text="🔄 = Auto-restock enabled | Use inventory commands to manage stock")
        await ctx.send(embed=embed)

    @staticmethod
    async def add_stock(ctx, item_id: str, quantity: int):
        """Add stock to an item"""
        if not is_staff(ctx):
            await ctx.send("❌ Only staff members can manage inventory!", delete_after=5)
            return

        if quantity <= 0:
            await ctx.send("❌ Quantity must be positive!", delete_after=5)
            return

        # Check if item exists in shop
        shop_item = get_shop_item_by_id(item_id)
        if not shop_item:
            await ctx.send(f"❌ Item `{item_id}` not found in shop!", delete_after=5)
            return

        # Initialize item if not in inventory
        if ShopInventory.get_quantity(item_id) == 0 and not ShopInventory.get_item_info(item_id):
            ShopInventory.initialize_item(item_id, 0)

        success = ShopInventory.add_stock(item_id, quantity)
        if success:
            new_quantity = ShopInventory.get_quantity(item_id)
            embed = discord.Embed(
                title="✅ Stock Added",
                description=f"Added **{quantity}** to **{shop_item.name}**",
                color=0x00ff00
            )
            embed.add_field(name="New Quantity", value=str(new_quantity), inline=True)

            # Log the action
            log_channel = ctx.guild.get_channel(CHANNELS.BOT_USAGE_LOG)
            if log_channel:
                log_embed = discord.Embed(
                    title="Inventory Management",
                    color=0x0099ff
                )
                log_embed.add_field(name="Action", value="Stock Added", inline=True)
                log_embed.add_field(name="Staff", value=ctx.author.mention, inline=True)
                log_embed.add_field(name="Item", value=f"{shop_item.name} (`{item_id}`)", inline=True)
                log_embed.add_field(name="Quantity Added", value=str(quantity), inline=True)
                log_embed.add_field(name="New Total", value=str(new_quantity), inline=True)
                await log_channel.send(embed=log_embed)

            await ctx.send(embed=embed)
        else:
            await ctx.send(f"❌ Failed to add stock to `{item_id}`", delete_after=5)

    @staticmethod
    async def set_stock(ctx, item_id: str, quantity: int):
        """Set exact stock quantity for an item"""
        if not is_staff(ctx):
            await ctx.send("❌ Only staff members can manage inventory!", delete_after=5)
            return

        if quantity < 0:
            await ctx.send("❌ Quantity cannot be negative!", delete_after=5)
            return

        # Check if item exists in shop
        shop_item = get_shop_item_by_id(item_id)
        if not shop_item:
            await ctx.send(f"❌ Item `{item_id}` not found in shop!", delete_after=5)
            return

        # Initialize item if not in inventory
        if not ShopInventory.get_item_info(item_id):
            ShopInventory.initialize_item(item_id, quantity)
        else:
            ShopInventory.set_stock(item_id, quantity)

        embed = discord.Embed(
            title="✅ Stock Set",
            description=f"Set **{shop_item.name}** stock to **{quantity}**",
            color=0x00ff00
        )

        # Log the action
        log_channel = ctx.guild.get_channel(CHANNELS.BOT_USAGE_LOG)
        if log_channel:
            log_embed = discord.Embed(
                title="Inventory Management",
                color=0x0099ff
            )
            log_embed.add_field(name="Action", value="Stock Set", inline=True)
            log_embed.add_field(name="Staff", value=ctx.author.mention, inline=True)
            log_embed.add_field(name="Item", value=f"{shop_item.name} (`{item_id}`)", inline=True)
            log_embed.add_field(name="New Quantity", value=str(quantity), inline=True)
            await log_channel.send(embed=log_embed)

        await ctx.send(embed=embed)

    @staticmethod
    async def setup_item_inventory(ctx, item_id: str, initial_qty: int, max_qty: Optional[int] = None,
                                 auto_restock: bool = False, restock_amount: int = 0):
        """Set up complete inventory configuration for an item"""
        if not is_staff(ctx):
            await ctx.send("❌ Only staff members can manage inventory!", delete_after=5)
            return

        # Check if item exists in shop
        shop_item = get_shop_item_by_id(item_id)
        if not shop_item:
            await ctx.send(f"❌ Item `{item_id}` not found in shop!", delete_after=5)
            return

        ShopInventory.initialize_item(item_id, initial_qty, max_qty, auto_restock, restock_amount)

        embed = discord.Embed(
            title="✅ Inventory Setup Complete",
            description=f"Configured inventory for **{shop_item.name}**",
            color=0x00ff00
        )
        embed.add_field(name="Initial Quantity", value=str(initial_qty), inline=True)
        embed.add_field(name="Max Quantity", value=str(max_qty) if max_qty else "Unlimited", inline=True)
        embed.add_field(name="Auto-Restock", value="Yes" if auto_restock else "No", inline=True)
        if auto_restock:
            embed.add_field(name="Restock Amount", value=str(restock_amount), inline=True)

        await ctx.send(embed=embed)

    @staticmethod
    async def purchase_history(ctx, item_id: Optional[str] = None, user: Optional[discord.Member] = None):
        """View purchase history"""
        if not is_staff(ctx):
            await ctx.send("❌ Only staff members can view purchase history!", delete_after=5)
            return

        user_id = str(user.id) if user else None
        purchases = ShopInventory.get_purchase_history(user_id, item_id, 20)

        if not purchases:
            await ctx.send("📜 No purchase history found.")
            return

        embed = discord.Embed(
            title="📜 Purchase History",
            color=0x0099ff
        )

        if user:
            embed.description = f"Purchases by {user.mention}"
        if item_id:
            shop_item = get_shop_item_by_id(item_id)
            item_name = shop_item.name if shop_item else item_id
            embed.description = f"Purchases of {item_name}"

        history_lines = []
        for purchase in purchases[:15]:  # Show last 15
            timestamp = datetime.fromtimestamp(purchase['purchase_time'])
            time_str = timestamp.strftime("%m/%d %H:%M")

            shop_item = get_shop_item_by_id(purchase['item_id'])
            item_name = shop_item.name if shop_item else purchase['item_id']

            history_lines.append(
                f"`{time_str}` <@{purchase['user_id']}> bought **{item_name}** for {purchase['price_paid']} UKPence"
            )

        embed.description = (embed.description or "") + "\n\n" + "\n".join(history_lines)
        embed.set_footer(text=f"Showing last {len(history_lines)} purchases")

        await ctx.send(embed=embed)

    @staticmethod
    async def manual_restock(ctx):
        """Manually trigger auto-restock for all eligible items"""
        if not is_staff(ctx):
            await ctx.send("❌ Only staff members can trigger restocking!", delete_after=5)
            return

        restocked_items = ShopInventory.auto_restock_items()

        if not restocked_items:
            await ctx.send("📦 No items needed restocking.")
            return

        embed = discord.Embed(
            title="✅ Manual Restock Complete",
            description=f"Restocked {len(restocked_items)} items:",
            color=0x00ff00
        )

        shop_items = {item.id: item for item in get_shop_items()}
        restock_list = []
        for item_id in restocked_items:
            shop_item = shop_items.get(item_id)
            item_name = shop_item.name if shop_item else item_id
            quantity = ShopInventory.get_quantity(item_id)
            restock_list.append(f"• **{item_name}** - Now has {quantity} in stock")

        embed.description += "\n" + "\n".join(restock_list)
        await ctx.send(embed=embed)

# Command functions for older discord.py versions
async def inventory_status_command(ctx):
    """Show inventory status"""
    await InventoryCommands.inventory_status(ctx)

async def add_stock_command(ctx, item_id: str, quantity: int):
    """Add stock to item"""
    await InventoryCommands.add_stock(ctx, item_id, quantity)

async def set_stock_command(ctx, item_id: str, quantity: int):
    """Set stock for item"""
    await InventoryCommands.set_stock(ctx, item_id, quantity)

async def setup_inventory_command(ctx, item_id: str, initial_qty: int, max_qty: int = None):
    """Setup item inventory"""
    auto_restock = max_qty is not None  # Auto-restock if max is set
    restock_amount = max(1, initial_qty // 4) if auto_restock else 0  # Restock 25% of initial
    await InventoryCommands.setup_item_inventory(ctx, item_id, initial_qty, max_qty, auto_restock, restock_amount)

async def purchase_history_command(ctx, target: str = None):
    """View purchase history"""
    user = None
    item_id = None

    if target:
        # Try to parse as user mention or item ID
        if target.startswith('<@') and target.endswith('>'):
            user_id = target[2:-1].replace('!', '')
            try:
                user = ctx.guild.get_member(int(user_id))
            except:
                pass
        else:
            item_id = target

    await InventoryCommands.purchase_history(ctx, item_id, user)

async def restock_command(ctx):
    """Manually trigger restock"""
    await InventoryCommands.manual_restock(ctx)