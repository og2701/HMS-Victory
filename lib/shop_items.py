import discord
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from config import ROLES, CHANNELS
from lib.economy_manager import add_shutcoins
from lib.shop_inventory import ShopInventory

class ShopItem(ABC):
    """Abstract base class for all shop items."""

    def __init__(self, id: str, name: str, description: str, price: int, use_inventory: bool = True):
        self.id = id
        self.name = name
        self.description = description
        self.price = price
        self.use_inventory = use_inventory

    @abstractmethod
    async def execute(self, interaction) -> str:
        """Execute the purchase action and return a success message."""
        pass

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        """Check if user can purchase this item. Returns (can_purchase, reason)."""
        # Check inventory if this item uses it
        if self.use_inventory:
            quantity = ShopInventory.get_quantity(self.id)
            if quantity <= 0:
                return False, "This item is out of stock."

        return True, ""

    def get_quantity(self) -> Optional[int]:
        """Get current quantity. Returns None if item doesn't use inventory."""
        if self.use_inventory:
            return ShopInventory.get_quantity(self.id)
        return None

    def get_display_name(self) -> str:
        """Get display name with quantity info"""
        if self.use_inventory:
            quantity = ShopInventory.get_quantity(self.id)
            if quantity <= 0:
                return f"{self.name} (SOLD OUT)"
            elif quantity <= 5:
                return f"{self.name} ({quantity} left)"
            else:
                return f"{self.name} (In Stock)"
        return self.name

    async def purchase(self, user_id: str, interaction) -> Tuple[bool, str]:
        """Handle the complete purchase process including inventory management"""
        # Check inventory before purchase
        if self.use_inventory:
            if not ShopInventory.consume_item(self.id, 1):
                return False, "Item is out of stock or purchase failed."

        try:
            # Execute the item-specific logic
            result_message = await self.execute(interaction)

            # Record the purchase
            if self.use_inventory:
                ShopInventory.record_purchase(user_id, self.id, 1, self.price)

            return True, result_message
        except Exception as e:
            # Rollback inventory on error
            if self.use_inventory:
                ShopInventory.add_stock(self.id, 1)
            raise e

class RoleItem(ShopItem):
    """Shop item for purchasing Discord roles."""

    def __init__(self, id: str, name: str, description: str, price: int, role_id: int, use_inventory: bool = True):
        super().__init__(id, name, description, price, use_inventory)
        self.role_id = role_id

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        # Check parent class conditions first (including inventory)
        can_purchase, reason = super().can_purchase(user)
        if not can_purchase:
            return False, reason

        role = user.guild.get_role(self.role_id)
        if not role:
            return False, "This role no longer exists."
        if role in user.roles:
            return False, "You already have this role."
        return True, ""

    async def execute(self, interaction) -> str:
        role = interaction.guild.get_role(self.role_id)
        if role and role not in interaction.user.roles:
            await interaction.user.add_roles(role)
            return f"You have successfully purchased the **{self.name}** role!"
        return "You already have this role or it no longer exists."

class ShutcoinItem(ShopItem):
    """Shop item for purchasing Shutcoins."""

    def __init__(self, id: str, name: str, description: str, price: int, amount: int, use_inventory: bool = True):
        super().__init__(id, name, description, price, use_inventory)
        self.amount = amount

    async def execute(self, interaction) -> str:
        add_shutcoins(interaction.user.id, self.amount)
        return f"You have successfully purchased **{self.amount} Shutcoins**!"

class PersonalVCItem(ShopItem):
    """Shop item for requesting a personal voice channel."""

    async def execute(self, interaction) -> str:
        staff_channel = interaction.guild.get_channel(CHANNELS.POLICE_STATION)
        if staff_channel:
            embed = discord.Embed(
                title="Personal VC Request",
                description=f"User {interaction.user.mention} has purchased a Personal Voice Channel.",
                color=0x00ff00
            )
            embed.add_field(name="User ID", value=interaction.user.id, inline=False)
            embed.add_field(name="Purchase Time", value=discord.utils.format_dt(discord.utils.utcnow()), inline=False)
            await staff_channel.send(embed=embed)
        return "Your request for a Personal Voice Channel has been sent to the staff team. They will contact you shortly!"

class CustomStatusItem(ShopItem):
    """Shop item for requesting a custom status/nickname."""

    async def execute(self, interaction) -> str:
        staff_channel = interaction.guild.get_channel(CHANNELS.POLICE_STATION)
        if staff_channel:
            embed = discord.Embed(
                title="Custom Status Request",
                description=f"User {interaction.user.mention} has purchased a Custom Status.",
                color=0x00ff00
            )
            embed.add_field(name="User ID", value=interaction.user.id, inline=False)
            embed.add_field(name="Purchase Time", value=discord.utils.format_dt(discord.utils.utcnow()), inline=False)
            embed.add_field(name="Instructions", value="Please DM the user to discuss their custom status.", inline=False)
            await staff_channel.send(embed=embed)
        return "Your request for a Custom Status has been sent to the staff team. They will DM you to discuss options!"

class MessageHighlightItem(ShopItem):
    """Shop item for highlighting a message in announcements."""

    async def execute(self, interaction) -> str:
        staff_channel = interaction.guild.get_channel(CHANNELS.POLICE_STATION)
        if staff_channel:
            embed = discord.Embed(
                title="Message Highlight Request",
                description=f"User {interaction.user.mention} has purchased a Message Highlight.",
                color=0x00ff00
            )
            embed.add_field(name="User ID", value=interaction.user.id, inline=False)
            embed.add_field(name="Purchase Time", value=discord.utils.format_dt(discord.utils.utcnow()), inline=False)
            embed.add_field(name="Instructions", value="User can submit a message to be highlighted in announcements.", inline=False)
            await staff_channel.send(embed=embed)
        return "Your Message Highlight purchase has been processed! Staff will contact you about submitting your message."

# Shop Items Registry
SHOP_ITEMS: List[ShopItem] = [
    # Currency Items
    ShutcoinItem("shutcoin", "1 Shutcoin", "Get a Shutcoin for the ability to silence a member for 30s", 100, 1),

    # Role Items (using actual role IDs from config)
    # RoleItem("ball_inspector", "Ball Inspector", "Get the prestigious Ball Inspector role", 200, ROLES.BALL_INSPECTOR),

    # Service Items
    # PersonalVCItem("personal_vc", "Personal Voice Channel", "Get your own private voice channel for 30 days", 1000),
    # CustomStatusItem("custom_status", "Custom Status", "Get a custom status/title displayed with your name", 500),
    # MessageHighlightItem("message_highlight", "Message Highlight", "Have your message highlighted in server announcements", 300),
]

def get_shop_items() -> List[ShopItem]:
    """Get all available shop items."""
    return SHOP_ITEMS

def get_shop_item_by_id(item_id: str) -> Optional[ShopItem]:
    """Get a shop item by its ID."""
    for item in SHOP_ITEMS:
        if item.id == item_id:
            return item
    return None