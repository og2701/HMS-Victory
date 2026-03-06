import discord
from discord.ui import View, Button
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
import asyncio
import random
import aiohttp
import io
from datetime import timedelta
from config import ROLES, CHANNELS, USERS
from lib.economy.economy_manager import add_shutcoins, add_bb
from lib.economy.shop_inventory import ShopInventory
from database import DatabaseManager

class ShopItem(ABC):
    """Abstract base class for all shop items."""

    def __init__(self, id: str, name: str, description: str, price: int, use_inventory: bool = True, show_in_shop: bool = True):
        self.id = id
        self.name = name
        self.description = description
        self.price = price
        self.use_inventory = use_inventory
        self.show_in_shop = show_in_shop

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

class VIPCaseItem(ShopItem):
    """VIP Role case with CS:GO-style gambling mechanic."""

    def __init__(self, id: str, name: str, description: str, price: int, vip_role_id: int, use_inventory: bool = False):
        super().__init__(id, name, description, price, use_inventory)
        self.vip_role_id = vip_role_id

        # Define possible outcomes with weights (higher weight = more likely)
        # Total weight = 100, so VIP at weight 10 = 10% chance
        self.outcomes = [
            {"type": "vip", "weight": 10, "emoji": "💎", "color": 0x00ff00, "label": "VIP ROLE"},
            {"type": "timeout", "weight": 15, "duration": 1, "emoji": "⏱️", "color": 0xff9900, "label": "1min timeout"},
            {"type": "timeout", "weight": 10, "duration": 5, "emoji": "⏰", "color": 0xff6600, "label": "5min timeout"},
            {"type": "timeout", "weight": 8, "duration": 10, "emoji": "🕐", "color": 0xff3300, "label": "10min timeout"},
            {"type": "timeout", "weight": 5, "duration": 30, "emoji": "🕰️", "color": 0xff0000, "label": "30min timeout"},
            {"type": "shutcoins", "weight": 12, "amount": 5, "emoji": "🪙", "color": 0xffd700, "label": "5 Shutcoins"},
            {"type": "shutcoins", "weight": 8, "amount": 10, "emoji": "💰", "color": 0xffd700, "label": "10 Shutcoins"},
            {"type": "cashback", "weight": 8, "percent": 25, "emoji": "💸", "color": 0x00ffff, "label": "25% cashback"},
            {"type": "cashback", "weight": 6, "percent": 50, "emoji": "💵", "color": 0x00ffff, "label": "50% cashback"},
            {"type": "cashback", "weight": 4, "percent": 75, "emoji": "💴", "color": 0x00ffff, "label": "75% cashback"},
            {"type": "cashback", "weight": 2, "percent": 100, "emoji": "💎💵", "color": 0x00ff88, "label": "100% CASHBACK"},
            {"type": "nothing", "weight": 12, "emoji": "❌", "color": 0x808080, "label": "Nothing"},
        ]

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        # Check if user already has VIP role
        vip_role = user.guild.get_role(self.vip_role_id)
        if vip_role and vip_role in user.roles:
            return False, "You already have the VIP role!"
        return True, ""

    async def execute(self, interaction) -> str:
        # Import here to avoid circular dependencies
        from lib.economy.shop_ui import VIPCaseSpinView
        
        # Create the spinning case view
        view = VIPCaseSpinView(self.outcomes, self.vip_role_id, self.price, interaction.user)

        # Start the spin
        await view.start_spin(interaction)

        # Return a placeholder message (actual result will be in the view)
        return "Case opening started!"

class RoastAccessItem(ShopItem):
    """Shop item for purchasing roast command access."""

    def __init__(self, id: str, name: str, description: str, price: int, use_inventory: bool = True):
        super().__init__(id, name, description, price, use_inventory)

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        # Check parent class conditions first (including inventory)
        can_purchase, reason = super().can_purchase(user)
        if not can_purchase:
            return False, reason

        # Check if user already has server booster role or any roast access role
        from config import ROLES
        roast_roles = [ROLES.SERVER_BOOSTER, ROLES.BORDER_FORCE, ROLES.CABINET, ROLES.MINISTER, ROLES.PCSO]

        for role_id in roast_roles:
            role = user.guild.get_role(role_id)
            if role and role in user.roles:
                return False, "You already have access to the roast command!"

        return True, ""

    async def execute(self, interaction) -> str:
        # Give them a special "Roast Access" role that grants access
        # First check if the role exists, if not we need to create it
        from config import ROLES

        # Look for existing roast access role
        roast_access_role = None
        for role in interaction.guild.roles:
            if role.name == "Roast Access":
                roast_access_role = role
                break

        # If role doesn't exist, create it
        if not roast_access_role:
            roast_access_role = await interaction.guild.create_role(
                name="Roast Access",
                reason="Purchased roast access from shop",
                color=0xff6600,
                mentionable=False
            )

        # Add role to user
        await interaction.user.add_roles(roast_access_role)
        return f"You now have access to the `/roast` command! Use it wisely..."

class CustomEmojiStickerItem(ShopItem):
    """Shop item for adding custom emoji or sticker to server."""

    def __init__(self, id: str, name: str, description: str, price: int, use_inventory: bool = True):
        super().__init__(id, name, description, price, use_inventory)

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        # Check parent class conditions first (including inventory)
        can_purchase, reason = super().can_purchase(user)
        if not can_purchase:
            return False, reason

        # Check server limits - only warn if both are at practical capacity
        emoji_count = len(user.guild.emojis)
        sticker_count = len(user.guild.stickers)
        emoji_limit = user.guild.emoji_limit
        sticker_limit = user.guild.sticker_limit

        # Use practical limits: ~500 for emojis (Discord's real max), boost limit for stickers
        emoji_at_capacity = emoji_count >= 500
        sticker_at_capacity = sticker_count >= sticker_limit

        if emoji_at_capacity and sticker_at_capacity:
            return False, f"Server has reached both emoji capacity ({emoji_count}/~500) and sticker limit ({sticker_count}/{sticker_limit})"

        return True, ""

    async def execute(self, interaction) -> str:
        # Import here to avoid circular dependencies
        from lib.economy.shop_ui import CustomEmojiStickerView
        
        # Create the selection view
        view = CustomEmojiStickerView(interaction.user)

        guild = interaction.guild
        emoji_count = len(guild.emojis)
        sticker_count = len(guild.stickers)
        emoji_limit = guild.emoji_limit
        sticker_limit = guild.sticker_limit

        embed = discord.Embed(
            title="🎨 Custom Emoji/Sticker Purchase",
            description="Choose whether you want to add a custom emoji or sticker to the server!",
            color=0xff6600
        )

        # Show more realistic emoji capacity info
        emoji_status = f"{emoji_count}/{emoji_limit}"
        if emoji_count > emoji_limit:
            emoji_status += f" (over boost limit, ~500 max)"

        embed.add_field(
            name="📊 Server Capacity",
            value=f"**Emojis:** {emoji_status}\n**Stickers:** {sticker_count}/{sticker_limit}",
            inline=False
        )

        # Send via followup since this is after the purchase
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        return "Custom emoji/sticker purchase initiated! Check the message above to continue."

class RankCustomizationMenuShopItem(ShopItem):
    """A portal item that opens the Rank Customization sub-shop."""
    def __init__(self, id: str, name: str, description: str, price: int):
        super().__init__(id, name, description, price, use_inventory=False, show_in_shop=True)

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        # RESTRICT TO OGGERS AND DEPUTY PM
        from config import USERS, ROLES
        if user.id != USERS.OGGERS and user.get_role(ROLES.DEPUTY_PM) is None:
            return False, "This menu is restricted to the bot owner and Deputy PM."
        return True, ""
        
    async def execute(self, interaction) -> str:
        from lib.economy.shop_ui import RankCustomizationOverviewView
        
        all_items = get_all_shop_items()
        rank_items = [i for i in all_items if isinstance(i, (RankBackgroundItem, RankColorThemeItem, RankResetItem))]
        
        view = RankCustomizationOverviewView(rank_items, interaction.user.id)
        await view.initial_send(interaction)
            
        return "Opened the Rank Customization Menu!"

class RankBackgroundItem(ShopItem):
    """Shop item for purchasing a custom rank background."""
    def __init__(self, id: str, name: str, description: str, price: int, bg_filename: str):
        super().__init__(id, name, description, price, use_inventory=False, show_in_shop=False)
        self.bg_filename = bg_filename

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        can_purchase, reason = super().can_purchase(user)
        if not can_purchase:
            return False, reason
        # RESTRICT TO OGGERS AND DEPUTY PM
        if user.id != USERS.OGGERS and user.get_role(ROLES.DEPUTY_PM) is None:
            return False, "This item is restricted to the bot owner and Deputy PM."

        # Check if they already have this background active
        current = DatabaseManager.fetch_one("SELECT background FROM user_rank_customization WHERE user_id = ?", (str(user.id),))
        if current and current[0] == self.bg_filename:
            return False, "You already have this background equipped!"
        return True, ""

    async def execute(self, interaction) -> str:
        user_id_str = str(interaction.user.id)
        # Check if row exists, insert or update
        exists = DatabaseManager.fetch_one("SELECT 1 FROM user_rank_customization WHERE user_id = ?", (user_id_str,))
        if exists:
            DatabaseManager.execute("UPDATE user_rank_customization SET background = ? WHERE user_id = ?", (self.bg_filename, user_id_str))
        else:
            DatabaseManager.execute("INSERT INTO user_rank_customization (user_id, background) VALUES (?, ?)", (user_id_str, self.bg_filename))
        
        return f"Successfully equipped the **{self.name}** rank background!"

class RankColorThemeItem(ShopItem):
    """Shop item for purchasing a custom rank color theme."""
    def __init__(self, id: str, name: str, description: str, price: int, primary: str, secondary: str, tertiary: str):
        super().__init__(id, name, description, price, use_inventory=False, show_in_shop=False)
        self.primary = primary
        self.secondary = secondary
        self.tertiary = tertiary

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        can_purchase, reason = super().can_purchase(user)
        if not can_purchase:
            return False, reason
        # RESTRICT TO OGGERS AND DEPUTY PM
        if user.id != USERS.OGGERS and user.get_role(ROLES.DEPUTY_PM) is None:
            return False, "This item is restricted to the bot owner and Deputy PM."

        current = DatabaseManager.fetch_one("SELECT primary_color, secondary_color, tertiary_color FROM user_rank_customization WHERE user_id = ?", (str(user.id),))
        if current and current[0] == self.primary and current[1] == self.secondary and current[2] == self.tertiary:
            return False, "You already have this color theme equipped!"
        return True, ""

    async def execute(self, interaction) -> str:
        user_id_str = str(interaction.user.id)
        exists = DatabaseManager.fetch_one("SELECT 1 FROM user_rank_customization WHERE user_id = ?", (user_id_str,))
        if exists:
            DatabaseManager.execute("UPDATE user_rank_customization SET primary_color = ?, secondary_color = ?, tertiary_color = ? WHERE user_id = ?", 
                                    (self.primary, self.secondary, self.tertiary, user_id_str))
        else:
            DatabaseManager.execute("INSERT INTO user_rank_customization (user_id, primary_color, secondary_color, tertiary_color) VALUES (?, ?, ?, ?)", 
                                    (user_id_str, self.primary, self.secondary, self.tertiary))
        
        return f"Successfully equipped the **{self.name}** color theme!"

class RankResetItem(ShopItem):
    """Shop item to reset rank customization back to default."""
    def __init__(self, id: str, name: str, description: str, price: int):
        super().__init__(id, name, description, price, use_inventory=False, show_in_shop=False)

    def can_purchase(self, user: discord.Member) -> Tuple[bool, str]:
        can_purchase, reason = super().can_purchase(user)
        if not can_purchase:
            return False, reason
        
        # RESTRICT TO OGGERS AND DEPUTY PM
        if user.id != USERS.OGGERS and user.get_role(ROLES.DEPUTY_PM) is None:
            return False, "This item is restricted to the bot owner and Deputy PM."
            
        current = DatabaseManager.fetch_one("SELECT * FROM user_rank_customization WHERE user_id = ?", (str(user.id),))
        if not current:
            return False, "You are already using your default rank card."
        return True, ""

    async def execute(self, interaction) -> str:
        user_id_str = str(interaction.user.id)
        # Delete from DB completely to fall back to default logic 
        # (which inherently respects CUSTOM_RANK_BACKGROUNDS in utils.py)
        DatabaseManager.execute("DELETE FROM user_rank_customization WHERE user_id = ?", (user_id_str,))
        
        return "Your rank card has been reset to your original default!"

# Shop Items Registry
SHOP_ITEMS: List[ShopItem] = [
    # Currency Items
    ShutcoinItem("shutcoin", "1 Shutcoin", "Get a Shutcoin for the ability to silence a member for 30s", 100, 1),

    # VIP Case - Gambling item (with inventory tracking)
    VIPCaseItem("vip_case", "VIP Role Case", "Open a case for a chance to win the VIP role! Contains various rewards and risks.", 3000, ROLES.VIP, use_inventory=True),

    # Service Items
    RoastAccessItem("roast_access", "Roast Access", "Get access to the /roast command (if not already a server booster)", 500, use_inventory=True),
    CustomEmojiStickerItem("custom_emoji_sticker", "Custom Emoji/Sticker", "Add a custom emoji or sticker to the server", 3500, use_inventory=True),

    # Rank Customizations (Temporarily 1 UKP for Testing)
    RankCustomizationMenuShopItem("rank_custom_menu", "Customise Rank Card", "Preview and choose different custom backgrounds and color themes for your rank card.", 0),
    RankResetItem("rank_custom_reset", "Reset Rank Card", "Reset your rank card background and colors to default", 0),
    RankBackgroundItem("rank_bg_space", "Cosmic Space Background", "A highly detailed cosmic space scene", 0, "rank_bg_space_1772807793835.png"),
    RankBackgroundItem("rank_bg_cyberpunk", "Cyberpunk Background", "A dark and rainy neon city street", 0, "rank_bg_cyberpunk_1772807811666.png"),
    RankBackgroundItem("rank_bg_anime", "Anime Blossom Background", "Tranquil cherry blossom grove at twilight", 0, "rank_bg_anime_1772807827201.png"),
    RankBackgroundItem("rank_bg_pirate", "Pirate Ship Background", "Dramatic pirate ship at sea during a storm", 0, "rank_bg_pirate_1772807841224.png"),
    RankColorThemeItem("rank_color_neon", "Neon Matrix Theme", "Green and black progress bar colors", 0, "#00FF00", "#003300", "#FFFFFF"),
    RankColorThemeItem("rank_color_gold", "Imperial Gold Theme", "Gold and white progress bar colors", 0, "#FFD700", "#B8860B", "#FFFFFF"),
    RankColorThemeItem("rank_color_synth", "Synthwave Theme", "Purple and pink progress bar colors", 0, "#FF00FF", "#800080", "#00FFFF"),
    RankColorThemeItem("rank_color_mono", "Monochrome Theme", "Black, white, and gray progress bar colors", 0, "#FFFFFF", "#333333", "#AAAAAA"),

    # Role Items (using actual role IDs from config)
    # RoleItem("ball_inspector", "Ball Inspector", "Get the prestigious Ball Inspector role", 200, ROLES.BALL_INSPECTOR),

    # Other Service Items
    # PersonalVCItem("personal_vc", "Personal Voice Channel", "Get your own private voice channel for 30 days", 1000),
    # CustomStatusItem("custom_status", "Custom Status", "Get a custom status/title displayed with your name", 500),
    # MessageHighlightItem("message_highlight", "Message Highlight", "Have your message highlighted in server announcements", 300),
]

def get_all_shop_items() -> List[ShopItem]:
    """Get literally all shop items including hidden ones."""
    return SHOP_ITEMS

def get_shop_items() -> List[ShopItem]:
    """Get all available shop items intended to be shown in the main shop view."""
    return [item for item in SHOP_ITEMS if item.show_in_shop]

def get_shop_item_by_id(item_id: str) -> Optional[ShopItem]:
    """Get a shop item by its ID."""
    for item in SHOP_ITEMS:
        if item.id == item_id:
            return item
    return None