import discord
from discord.ui import View, Button
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
import asyncio
import random
from datetime import timedelta
from config import ROLES, CHANNELS
from lib.economy_manager import add_shutcoins, add_bb
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
        # Create the spinning case view
        view = VIPCaseSpinView(self.outcomes, self.vip_role_id, self.price, interaction.user)

        # Start the spin
        await view.start_spin(interaction)

        # Return a placeholder message (actual result will be in the view)
        return "Case opening started!"

class VIPCaseSpinView(View):
    """Interactive view for the VIP case spinning animation."""

    def __init__(self, outcomes, vip_role_id, price, user):
        super().__init__(timeout=60)
        self.outcomes = outcomes
        self.vip_role_id = vip_role_id
        self.price = price
        self.user = user
        self.result = None
        self.spinning = False
        self.message = None

    async def start_spin(self, interaction):
        """Start the spinning animation."""
        self.spinning = False  # Not spinning yet, just showing the button

        # Create initial embed
        embed = discord.Embed(
            title="🎰 VIP Role Case Opening",
            description=f"{self.user.mention} is opening a VIP Role Case!\n\nPress SPIN to try your luck!",
            color=0xffff00
        )

        # Add the spin button
        spin_button = Button(label="SPIN", style=discord.ButtonStyle.primary, emoji="🎲")
        spin_button.callback = self.spin_callback
        self.add_item(spin_button)

        # Send the initial message and store reference (public so everyone can see)
        self.message = await interaction.followup.send(embed=embed, view=self, ephemeral=False)

    async def spin_callback(self, interaction: discord.Interaction):
        """Handle the spin button press."""
        if self.spinning:
            await interaction.response.defer()
            return

        self.spinning = True

        # Respond to the button interaction
        await interaction.response.defer()

        # Disable the button
        for item in self.children:
            item.disabled = True

        # Start the spinning animation
        await self.animate_spin(interaction)

    async def animate_spin(self, interaction: discord.Interaction):
        """Animate the spinning case."""
        # Calculate weights and select outcome
        total_weight = sum(outcome["weight"] for outcome in self.outcomes)
        rand = random.random() * total_weight

        current_weight = 0
        selected_outcome = None
        for outcome in self.outcomes:
            current_weight += outcome["weight"]
            if rand <= current_weight:
                selected_outcome = outcome
                break

        if not selected_outcome:
            selected_outcome = self.outcomes[-1]  # Fallback to "nothing"

        # Shorter, faster animation with mobile-friendly display
        # Create a sequence of items to spin through (20 items for quicker result)
        spin_sequence = []
        for _ in range(20):  # Reduced from 30 for faster spinning
            spin_sequence.append(random.choice(self.outcomes))

        # Place winning item near end
        win_position = random.randint(16, 19)  # Adjusted for 20 items
        spin_sequence[win_position] = selected_outcome

        for i in range(len(spin_sequence)):
            item = spin_sequence[i]

            # Faster speed control
            if i < 8:
                delay = 0.05  # Very fast start
            elif i < 14:
                delay = 0.08  # Medium speed
            elif i < 17:
                delay = 0.12   # Slowing down
            else:
                delay = 0.2 + (i - 17) * 0.1  # Slower finish

            # Simpler mobile-friendly display
            display_items = []
            for j in range(-1, 2):  # Show only 3 items for mobile
                idx = (i + j) % len(spin_sequence)
                curr_item = spin_sequence[idx]

                if j == 0:
                    # Center item - no extra asterisks
                    if i == len(spin_sequence) - 1:
                        # Final result
                        display_items.append(f"➤ {curr_item['emoji']} {curr_item['label']} ⬅")
                    else:
                        # Spinning
                        display_items.append(f"▶ {curr_item['emoji']} {curr_item['label']} ◀")
                else:
                    # Side items - simpler display
                    display_items.append(f"  {curr_item['emoji']} {curr_item['label']}")

            # Simple mobile-friendly frame
            reel_display = "\n".join([
                "━━━━━━━━━━━━━━━━━",
                display_items[0],
                display_items[1],  # Center item
                display_items[2],
                "━━━━━━━━━━━━━━━━━"
            ])

            # Dynamic title
            if i == len(spin_sequence) - 1:
                title = f"🎰 WINNER: {selected_outcome['emoji']} {selected_outcome['label']}"
                color = selected_outcome["color"]
                # Add final flash effect
                embed = discord.Embed(
                    title=title,
                    description=f"```\n{reel_display}\n```\n**{self.user.mention} got: {selected_outcome['emoji']} {selected_outcome['label']}!**",
                    color=color
                )
            else:
                title = f"🎰 {self.user.display_name}'s Spin"
                color = 0xffff00
                embed = discord.Embed(
                    title=title,
                    description=f"```\n{reel_display}\n```",
                    color=color
                )

                # Simpler progress bar
                progress = "█" * (i * 10 // len(spin_sequence)) + "░" * (10 - (i * 10 // len(spin_sequence)))
                embed.add_field(name="Progress", value=progress, inline=False)

            await self.message.edit(embed=embed, view=self)

            if i < len(spin_sequence) - 1:
                await asyncio.sleep(delay)

        # Process the result
        await self.process_result(interaction, selected_outcome)

    async def process_result(self, interaction: discord.Interaction, outcome):
        """Process the winning outcome."""
        result_embed = discord.Embed(
            title=f"🎰 {self.user.display_name}'s VIP Role Case - RESULT",
            color=outcome["color"]
        )

        if outcome["type"] == "vip":
            # Winner! Give them the VIP role
            vip_role = interaction.guild.get_role(self.vip_role_id)
            if vip_role:
                await interaction.user.add_roles(vip_role)
                result_embed.description = f"🎉 **JACKPOT!** 🎉\n\n{self.user.mention} won the **VIP ROLE**! {outcome['emoji']}\n\nCongratulations!"

                # Log the win
                log_channel = interaction.guild.get_channel(1197572903294730270)  # BOT_USAGE_LOG
                if log_channel:
                    log_embed = discord.Embed(
                        title="🎰 VIP Case - JACKPOT WIN",
                        color=0x00ff00
                    )
                    log_embed.add_field(name="Winner", value=interaction.user.mention, inline=True)
                    log_embed.add_field(name="Prize", value="VIP Role", inline=True)
                    await log_channel.send(embed=log_embed)
            else:
                result_embed.description = "Error: VIP role not found. Please contact staff."

        elif outcome["type"] == "timeout":
            # Apply timeout
            duration = timedelta(minutes=outcome["duration"])
            try:
                await interaction.user.timeout(duration, reason="VIP Case outcome")
                result_embed.description = f"{outcome['emoji']} {self.user.mention} got a **{outcome['duration']} minute timeout**!\n\nBetter luck next time!"
            except discord.Forbidden:
                result_embed.description = f"{outcome['emoji']} {self.user.mention} would have gotten a {outcome['duration']} minute timeout, but I don't have permission!"

        elif outcome["type"] == "shutcoins":
            # Award shutcoins
            add_shutcoins(interaction.user.id, outcome["amount"])
            result_embed.description = f"{outcome['emoji']} {self.user.mention} won **{outcome['amount']} Shutcoins**!\n\nNot bad!"

        elif outcome["type"] == "cashback":
            # Give partial refund from the bank
            refund_amount = int(self.price * outcome["percent"] / 100)
            from lib.bank_manager import BankManager
            BankManager.withdraw(refund_amount, f"Cashback payout to {self.user.display_name}")
            add_bb(interaction.user.id, refund_amount)
            result_embed.description = f"{outcome['emoji']} {self.user.mention} got **{outcome['percent']}% cashback**!\n\n+{refund_amount} UKPence returned!"

        else:  # nothing
            result_embed.description = f"{outcome['emoji']} {self.user.mention} got **nothing**...\n\nBetter luck next time!"

        # Clear the view
        self.clear_items()

        # Add a "Try Again" button if they didn't win VIP
        if outcome["type"] != "vip":
            try_again_button = Button(label="Try Again (3000 UKPence)", style=discord.ButtonStyle.secondary, emoji="🔄")
            try_again_button.callback = self.try_again_callback
            self.add_item(try_again_button)

        await self.message.edit(embed=result_embed, view=self)

    async def try_again_callback(self, interaction: discord.Interaction):
        """Handle try again button."""
        # Import here to avoid circular import
        from commands.economy.shop import PurchaseConfirmationView
        from lib.economy_manager import get_bb

        # Check if user is the original purchaser
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "Only the original purchaser can use the Try Again button!",
                ephemeral=True
            )
            return

        # Check balance first
        balance = get_bb(interaction.user.id)
        if balance < self.price:
            await interaction.response.send_message(
                f"❌ Insufficient funds! You need {self.price} UKPence but only have {balance} UKPence.",
                ephemeral=True
            )
            return

        # Get the VIP Case item from the shop items registry
        vip_case_item = None
        for item in SHOP_ITEMS:
            if item.id == "vip_case":
                vip_case_item = item
                break

        if not vip_case_item:
            await interaction.response.send_message(
                "❌ Error: VIP Case item not found in shop!",
                ephemeral=True
            )
            return

        # Create and send the purchase confirmation view
        view = PurchaseConfirmationView(vip_case_item)

        embed = discord.Embed(
            title="Confirm Purchase",
            color=0xffa500
        )
        embed.add_field(name="Item", value=vip_case_item.name, inline=False)
        embed.add_field(name="Description", value=vip_case_item.description, inline=False)
        embed.add_field(name="Price", value=f"{vip_case_item.price} UKPence", inline=True)
        embed.add_field(name="Your Balance", value=f"{balance} UKPence", inline=True)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

# Shop Items Registry
SHOP_ITEMS: List[ShopItem] = [
    # Currency Items
    ShutcoinItem("shutcoin", "1 Shutcoin", "Get a Shutcoin for the ability to silence a member for 30s", 100, 1),

    # VIP Case - Gambling item (with inventory tracking)
    VIPCaseItem("vip_case", "VIP Role Case", "Open a case for a chance to win the VIP role! Contains various rewards and risks.", 3000, ROLES.VIP, use_inventory=True),

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