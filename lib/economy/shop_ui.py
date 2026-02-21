import discord
from discord.ui import Select, View, Button
from typing import List, TYPE_CHECKING
import random
import asyncio
from datetime import timedelta

# We use TYPE_CHECKING to avoid circular imports.
# The actual types are only needed for type hinting here.
if TYPE_CHECKING:
    from lib.economy.shop_items import ShopItem

from lib.economy.economy_manager import get_bb, remove_bb, ensure_bb, add_shutcoins, add_bb
from lib.economy.bank_manager import BankManager

class ShopMainView(View):
    """The main view that shows the shop dropdown list."""
    def __init__(self, items: List['ShopItem']):
        super().__init__(timeout=300)
        self.items = items
        self.add_item(ShopItemSelect(items, self))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # Try to edit the message if we still have the context, but pass if we don't
        pass


class ShopItemSelect(Select):
    """The dropdown menu containing shop items."""
    def __init__(self, items: List['ShopItem'], parent_view: ShopMainView):
        options = []
        for item in items[:25]:  # Discord limit of 25 options
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
                description=description[:100],  
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
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        selected_item = self.items[self.values[0]]

        # Check if user can theoretically purchase this item
        can_purchase, reason = selected_item.can_purchase(interaction.user)
        if not can_purchase:
            await interaction.response.send_message(f"❌ Cannot purchase: {reason}", ephemeral=True)
            return

        # Swap the view to the Purchase Detail View directly on the same message
        detail_view = PurchaseConfirmationView(selected_item, self.parent_view)
        
        embed = discord.Embed(
            title="Confirm Purchase",
            description=f"**{selected_item.get_display_name()}**\n{selected_item.description}",
            color=0x00ff00
        )
        embed.add_field(name="Price", value=f"{selected_item.price} UKPence", inline=True)
        embed.add_field(name="Your Balance", value=f"{get_bb(interaction.user.id)} UKPence", inline=True)

        quantity = selected_item.get_quantity()
        if quantity is not None:
            if quantity <= 5:
                embed.add_field(name="⚠️ Stock", value=f"Only {quantity} remaining!", inline=True)
            else:
                embed.add_field(name="📦 Stock", value="In Stock", inline=True)

        await interaction.response.edit_message(embed=embed, view=detail_view)


class PurchaseConfirmationView(View):
    """The view shown when viewing a specific item's details."""
    def __init__(self, item: 'ShopItem', return_view: View):
        super().__init__(timeout=300)
        self.item = item
        self.return_view = return_view

    @discord.ui.button(label="Return to Shop", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
    async def return_to_shop(self, interaction: discord.Interaction, button: discord.ui.Button):
        ensure_bb(interaction.user.id)
        user_balance = get_bb(interaction.user.id)

        shop_embed = discord.Embed(
            title="🛒 UKPlace Shop",
            description="Purchase items using your UKPence. Select an item below to see details and purchase.",
            color=0x0099ff
        )
        shop_embed.add_field(name="💰 Your Balance", value=f"{user_balance} UKPence", inline=True)
        shop_embed.set_footer(text="ℹ️ How to Earn UKPence: Daily chat rewards (top chatters), Server boosting bonus, Participating in voice stages")
        
        from lib.economy.shop_items import get_shop_items
        items = get_shop_items()
        item_list = []
        for item in items[:10]:
            affordable = "✅" if user_balance >= item.price else "❌"
            quantity = item.get_quantity()
            display_name = item.get_display_name()
            item_list.append(f"{affordable} **{display_name}** - {item.price} UKPence - {quantity} remaining")

        shop_embed.add_field(name="🛍️ Available Items (Select below for more)", value="\n".join(item_list), inline=False)
        
        # We spawn a fresh ShopMainView to reset the dropdown state cleanly
        await interaction.response.edit_message(embed=shop_embed, view=ShopMainView(items))

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.green, emoji="✅", row=0)
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        ensure_bb(interaction.user.id)
        user_balance = get_bb(interaction.user.id)

        if user_balance < self.item.price:
            await interaction.response.send_message(
                f"❌ Insufficient funds! You need {self.item.price} UKPence but only have {user_balance}.",
                ephemeral=True
            )
            return

        # Double check if user can purchase
        can_purchase, reason = self.item.can_purchase(interaction.user)
        if not can_purchase:
            await interaction.response.send_message(f"❌ Cannot purchase: {reason}", ephemeral=True)
            return

        # Charge the user
        if remove_bb(interaction.user.id, self.item.price):
            try:
                BankManager.deposit(self.item.price, f"Purchase of {self.item.name}")

                # Execute item purchase logic
                # We do not defer here because `execute` might need to edit the message (like VIPCase)
                success, result_message = await self.item.purchase(str(interaction.user.id), interaction)

                if not success:
                    # Refund if backend purchase logic returned False
                    BankManager.withdraw(self.item.price, f"Refund for failed purchase of {self.item.name}")
                    add_bb(interaction.user.id, self.item.price)
                    
                    if not interaction.response.is_done():
                        await interaction.response.send_message(f"❌ Purchase failed: {result_message}", ephemeral=True)
                    else:
                        await interaction.followup.send(f"❌ Purchase failed: {result_message}", ephemeral=True)
                    return

                # If this is a normal item execution (not taking over UI like VIP Case)...
                if self.item.name not in ["VIP Role Case", "Custom Emoji/Sticker"]:
                    embed = discord.Embed(
                        title="Purchase Successful! ✅",
                        description=result_message,
                        color=0x00ff00
                    )
                    embed.add_field(name="Remaining Balance", value=f"{get_bb(interaction.user.id)} UKPence", inline=False)
                    
                    # Instead of an ephemeral popup, EDIT the shop embed to show success
                    # Disable buttons so they can't double-buy without going back
                    for child in self.children:
                        child.disabled = True
                    
                    if not interaction.response.is_done():
                        await interaction.response.edit_message(embed=embed, view=self)

                # Log the purchase
                log_channel = interaction.guild.get_channel(1197572903294730270)  # BOT_USAGE_LOG
                if log_channel:
                    log_embed = discord.Embed(title="Shop Purchase", color=0x00ff00)
                    log_embed.add_field(name="User", value=interaction.user.mention, inline=True)
                    log_embed.add_field(name="Item", value=self.item.name, inline=True)
                    log_embed.add_field(name="Price", value=f"{self.item.price} UKPence", inline=True)
                    await log_channel.send(embed=log_embed)

            except Exception as e:
                BankManager.withdraw(self.item.price, f"Refund for error in purchase of {self.item.name}")
                add_bb(interaction.user.id, self.item.price)
                
                error_msg = f"❌ An error occurred during purchase. Your UKPence has been refunded.\nError: {str(e)}"
                if not interaction.response.is_done():
                    await interaction.response.send_message(error_msg, ephemeral=True)
                else:
                    await interaction.followup.send(error_msg, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Payment failed. Please try again.", ephemeral=True)

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

        # Send the initial message and store reference
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, view=self, ephemeral=False)
            self.message = await interaction.original_response()
        else:
            self.message = await interaction.followup.send(embed=embed, view=self, ephemeral=False)

    async def spin_callback(self, interaction: discord.Interaction):
        """Handle the spin button press."""
        if self.spinning:
            await interaction.response.defer()
            return

        self.spinning = True
        await interaction.response.defer()

        for item in self.children:
            item.disabled = True

        await self.animate_spin(interaction)

    async def animate_spin(self, interaction: discord.Interaction):
        """Animate the spinning case."""
        vip_role = interaction.guild.get_role(self.vip_role_id)
        current_vips = len(vip_role.members) if vip_role else 0
        
        dynamic_outcomes = []
        for outcome in self.outcomes:
            outcome_copy = outcome.copy()
            if outcome_copy["type"] == "vip":
                new_weight = max(2, int(outcome_copy["weight"] - (current_vips * 0.75)))
                outcome_copy["weight"] = new_weight
            dynamic_outcomes.append(outcome_copy)

        total_weight = sum(outcome["weight"] for outcome in dynamic_outcomes)
        rand = random.random() * total_weight

        current_weight = 0
        selected_outcome = None
        for outcome in dynamic_outcomes:
            current_weight += outcome["weight"]
            if rand <= current_weight:
                selected_outcome = outcome
                break

        if not selected_outcome:
            selected_outcome = dynamic_outcomes[-1] 

        spin_sequence = []
        for _ in range(20): 
            spin_sequence.append(random.choice(dynamic_outcomes))

        win_position = random.randint(16, 19) 
        spin_sequence[win_position] = selected_outcome

        for i in range(len(spin_sequence)):
            item = spin_sequence[i]

            if i < 8: delay = 0.05 
            elif i < 14: delay = 0.08 
            elif i < 17: delay = 0.12  
            else: delay = 0.2 + (i - 17) * 0.1  

            display_items = []
            for j in range(-1, 2):  
                idx = (i + j) % len(spin_sequence)
                curr_item = spin_sequence[idx]

                if j == 0:
                    if i == len(spin_sequence) - 1:
                        display_items.append(f"➤ {curr_item['emoji']} {curr_item['label']} ⬅")
                    else:
                        display_items.append(f"▶ {curr_item['emoji']} {curr_item['label']} ◀")
                else:
                    display_items.append(f"  {curr_item['emoji']} {curr_item['label']}")

            reel_display = "\n".join([
                "━━━━━━━━━━━━━━━━━",
                display_items[0],
                display_items[1],  
                display_items[2],
                "━━━━━━━━━━━━━━━━━"
            ])

            if i == len(spin_sequence) - 1:
                title = f"🎰 WINNER: {selected_outcome['emoji']} {selected_outcome['label']}"
                color = selected_outcome["color"]
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
                progress = "█" * (i * 10 // len(spin_sequence)) + "░" * (10 - (i * 10 // len(spin_sequence)))
                embed.add_field(name="Progress", value=progress, inline=False)

            await self.message.edit(embed=embed, view=self)

            if i < len(spin_sequence) - 1:
                await asyncio.sleep(delay)

        await self.process_result(interaction, selected_outcome)

    async def process_result(self, interaction: discord.Interaction, outcome):
        """Process the winning outcome."""
        result_embed = discord.Embed(
            title=f"🎰 {self.user.display_name}'s VIP Role Case - RESULT",
            color=outcome["color"]
        )

        if outcome["type"] == "vip":
            vip_role = interaction.guild.get_role(self.vip_role_id)
            if vip_role:
                await interaction.user.add_roles(vip_role)
                result_embed.description = f"🎉 **JACKPOT!** 🎉\n\n{self.user.mention} won the **VIP ROLE**! {outcome['emoji']}\n\nCongratulations!"
                log_channel = interaction.guild.get_channel(1197572903294730270)
                if log_channel:
                    log_embed = discord.Embed(title="🎰 VIP Case - JACKPOT WIN", color=0x00ff00)
                    log_embed.add_field(name="Winner", value=interaction.user.mention, inline=True)
                    log_embed.add_field(name="Prize", value="VIP Role", inline=True)
                    await log_channel.send(embed=log_embed)
            else:
                result_embed.description = "Error: VIP role not found. Please contact staff."

        elif outcome["type"] == "timeout":
            duration = timedelta(minutes=outcome["duration"])
            try:
                await interaction.user.timeout(duration, reason="VIP Case outcome")
                result_embed.description = f"{outcome['emoji']} {self.user.mention} got a **{outcome['duration']} minute timeout**!\n\nBetter luck next time!"
            except discord.Forbidden:
                result_embed.description = f"{outcome['emoji']} {self.user.mention} would have gotten a {outcome['duration']} minute timeout, but I don't have permission!"

        elif outcome["type"] == "shutcoins":
            add_shutcoins(interaction.user.id, outcome["amount"])
            result_embed.description = f"{outcome['emoji']} {self.user.mention} won **{outcome['amount']} Shutcoins**!\n\nNot bad!"

        elif outcome["type"] == "cashback":
            refund_amount = int(self.price * outcome["percent"] / 100)
            BankManager.withdraw(refund_amount, f"Cashback payout to {self.user.display_name}")
            add_bb(interaction.user.id, refund_amount)
            result_embed.description = f"{outcome['emoji']} {self.user.mention} got **{outcome['percent']}% cashback**!\n\n+{refund_amount} UKPence returned!"

        else:
            result_embed.description = f"{outcome['emoji']} {self.user.mention} got **nothing**...\n\nBetter luck next time!"

        self.clear_items()

        if outcome["type"] != "vip":
            try_again_button = Button(label="Try Again (3000 UKPence)", style=discord.ButtonStyle.secondary, emoji="🔄")
            try_again_button.callback = self.try_again_callback
            self.add_item(try_again_button)

        await self.message.edit(embed=result_embed, view=self)

    async def try_again_callback(self, interaction: discord.Interaction):
        from lib.economy.shop_items import get_shop_items
        
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Only the original purchaser can use the Try Again button!", ephemeral=True)
            return

        balance = get_bb(interaction.user.id)
        if balance < self.price:
            await interaction.response.send_message(f"❌ Insufficient funds! You need {self.price} UKPence but only have {balance} UKPence.", ephemeral=True)
            return

        items = get_shop_items()
        vip_case_item = next((item for item in items if item.id == "vip_case"), None)

        if not vip_case_item:
            await interaction.response.send_message("❌ Error: VIP Case item not found in shop!", ephemeral=True)
            return

        # Start a new main shop embed context
        shop_embed = discord.Embed(title="🛒 UKPlace Shop", description="Purchase items using your UKPence. Select an item below to see details and purchase.", color=0x0099ff)
        shop_embed.add_field(name="💰 Your Balance", value=f"{balance} UKPence", inline=True)
        shop_embed.set_footer(text="ℹ️ How to Earn UKPence: Daily chat rewards (top chatters), Server boosting bonus, Participating in voice stages")
        
        item_list = []
        for item in items[:10]:
            affordable = "✅" if balance >= item.price else "❌"
            quantity = item.get_quantity()
            display_name = item.get_display_name()
            item_list.append(f"{affordable} **{display_name}** - {item.price} UKPence - {quantity} remaining")

        shop_embed.add_field(name="🛍️ Available Items (Select below for more)", value="\n".join(item_list), inline=False)
        main_view = ShopMainView(items)
        
        detail_view = PurchaseConfirmationView(vip_case_item, main_view)

        embed = discord.Embed(title="Confirm Purchase", description=f"**{vip_case_item.get_display_name()}**\n{vip_case_item.description}", color=0x00ff00)
        embed.add_field(name="Price", value=f"{vip_case_item.price} UKPence", inline=True)
        embed.add_field(name="Your Balance", value=f"{balance} UKPence", inline=True)

        await interaction.response.send_message(embed=embed, view=detail_view, ephemeral=True)



class CustomEmojiStickerView(View):
    """Interactive view for custom emoji/sticker purchase."""

    def __init__(self, user):
        super().__init__(timeout=300)
        self.user = user
        self.choice = None
        self.file_attachment = None

    @discord.ui.select(
        placeholder="Choose what to add to the server...",
        options=[
            discord.SelectOption(
                label="Custom Emoji",
                description="Add a custom emoji to the server",
                emoji="😀",
                value="emoji"
            ),
            discord.SelectOption(
                label="Custom Sticker",
                description="Add a custom sticker to the server",
                emoji="🏷️",
                value="sticker"
            )
        ]
    )
    async def choice_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Only the purchaser can use this!", ephemeral=True)
            return

        self.choice = select.values[0]
        guild = interaction.guild
        emoji_count = len(guild.emojis)
        sticker_count = len(guild.stickers)
        emoji_limit = guild.emoji_limit
        sticker_limit = guild.sticker_limit

        if self.choice == "emoji":
            if emoji_count >= 500:
                await interaction.response.send_message(f"❌ Server emoji limit reached ({emoji_count}/~500 max). Please choose sticker instead.", ephemeral=True)
                return
        else:  
            if sticker_count >= sticker_limit:
                await interaction.response.send_message(f"❌ Server sticker limit reached ({sticker_count}/{sticker_limit}). Please choose emoji instead.", ephemeral=True)
                return

        embed = discord.Embed(
            title=f"Custom {'Emoji' if self.choice == 'emoji' else 'Sticker'} Upload",
            description=f"Please upload your {'emoji' if self.choice == 'emoji' else 'sticker'} file and provide a name.",
            color=0x00ff00
        )

        if self.choice == "emoji":
            embed.add_field(name="Requirements", value="• File must be PNG, JPG, or GIF\n• Max 256KB\n• Recommended: 128x128px\n• Name must be 2-32 characters (alphanumeric + underscores)", inline=False)
        else:
            embed.add_field(name="Requirements", value="• File must be PNG, GIF, or Lottie JSON\n• Max 512KB for static, 512KB for animated\n• Name must be 2-30 characters\n• Description is optional", inline=False)

        self.clear_items()
        upload_button = Button(label=f"Upload {'Emoji' if self.choice == 'emoji' else 'Sticker'}", style=discord.ButtonStyle.primary, emoji="📁")
        upload_button.callback = self.upload_callback
        self.add_item(upload_button)

        await interaction.response.edit_message(embed=embed, view=self)

    async def upload_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("Only the purchaser can use this!", ephemeral=True)
            return

        class UploadModal(discord.ui.Modal):
            def __init__(self, choice):
                super().__init__(title=f"Upload Custom {'Emoji' if choice == 'emoji' else 'Sticker'}")
                self.choice = choice
                self.name_input = discord.ui.TextInput(
                    label=f"{'Emoji' if choice == 'emoji' else 'Sticker'} Name",
                    placeholder=f"Enter name for your {'emoji' if choice == 'emoji' else 'sticker'}",
                    min_length=2, max_length=32 if choice == 'emoji' else 30
                )
                self.add_item(self.name_input)

                if choice == 'sticker':
                    self.description_input = discord.ui.TextInput(
                        label="Sticker Description (Optional)",
                        placeholder="Enter description for your sticker",
                        required=False, max_length=100
                    )
                    self.add_item(self.description_input)

            async def on_submit(self, modal_interaction: discord.Interaction):
                await modal_interaction.response.send_message(
                    f"Now please upload your {'emoji' if self.choice == 'emoji' else 'sticker'} file as an attachment in your next message. I'll process it automatically when you send it.",
                    ephemeral=True
                )
                modal_interaction.client._pending_uploads = getattr(modal_interaction.client, '_pending_uploads', {})
                modal_interaction.client._pending_uploads[modal_interaction.user.id] = {
                    'type': self.choice,
                    'name': self.name_input.value,
                    'description': getattr(self, 'description_input', None) and self.description_input.value,
                    'waiting': True
                }

        await interaction.response.send_modal(UploadModal(self.choice))


class EmojiStickerApprovalView(View):
    """View for cabinet channel approval of custom emoji/sticker requests."""
    def __init__(self, user: discord.Member, upload_data: dict, file_data: bytes, filename: str):
        super().__init__(timeout=86400)  # 24 hours timeout
        self.user = user
        self.upload_data = upload_data  
        self.file_data = file_data
        self.filename = filename

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, emoji="✅")
    async def approve_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        from config import ROLES
        import io

        if not any(role.id == ROLES.CABINET for role in interaction.user.roles):
            await interaction.response.send_message("❌ Only cabinet members can approve this request.", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            guild = interaction.guild
            success = False
            result_message = ""

            if self.upload_data['type'] == 'emoji':
                emoji = await guild.create_custom_emoji(
                    name=self.upload_data['name'],
                    image=self.file_data,
                    reason=f"Approved by {interaction.user.name} - purchased by {self.user.name}"
                )
                success = True
                result_message = f"✅ Custom emoji `:{emoji.name}:` {emoji} has been approved and added to the server!"
            else:
                sticker = await guild.create_sticker(
                    name=self.upload_data['name'],
                    description=self.upload_data.get('description', self.upload_data['name']),
                    emoji='🎨',
                    file=discord.File(io.BytesIO(self.file_data), filename=self.filename),
                    reason=f"Approved by {interaction.user.name} - purchased by {self.user.name}"
                )
                success = True
                result_message = f"✅ Custom sticker '{sticker.name}' has been approved and added to the server!"

            if success:
                embed = discord.Embed(title="✅ Custom Emoji/Sticker - APPROVED", description=result_message, color=0x00ff00)
                embed.add_field(name="Approved by", value=interaction.user.mention, inline=True)
                embed.add_field(name="Purchaser", value=self.user.mention, inline=True)
                embed.add_field(name="Type", value=self.upload_data['type'].title(), inline=True)
                embed.add_field(name="Name", value=self.upload_data['name'], inline=True)
                if self.upload_data.get('description'):
                    embed.add_field(name="Description", value=self.upload_data['description'], inline=True)
                for item in self.children:
                    item.disabled = True
                await interaction.edit_original_response(embed=embed, view=self)
                try:
                    await self.user.send(result_message)
                except discord.Forbidden:
                    pass

        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Failed to create {self.upload_data['type']}: {str(e)}", ephemeral=True)


    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, emoji="❌")
    async def deny_request(self, interaction: discord.Interaction, button: discord.ui.Button):
        from config import ROLES

        if not any(role.id == ROLES.CABINET for role in interaction.user.roles):
            await interaction.response.send_message("❌ Only cabinet members can deny this request.", ephemeral=True)
            return

        class DenyReasonModal(discord.ui.Modal):
            def __init__(self, approval_view):
                super().__init__(title="Deny Custom Emoji/Sticker Request")
                self.approval_view = approval_view
                self.reason_input = discord.ui.TextInput(
                    label="Reason for denial",
                    placeholder="Enter the reason why this request is being denied...",
                    required=True, max_length=200, style=discord.TextStyle.paragraph
                )
                self.add_item(self.reason_input)

            async def on_submit(self, modal_interaction: discord.Interaction):
                await modal_interaction.response.defer()

                from lib.economy.shop_items import get_shop_items
                from lib.economy.economy_manager import add_bb
                from lib.economy.bank_manager import BankManager

                items = get_shop_items()
                refund_amount = 3500  
                for item in items:
                    if item.id == "custom_emoji_sticker":
                        refund_amount = item.price
                        break

                BankManager.withdraw(refund_amount, f"Refund for denied {self.approval_view.upload_data['type']} request")
                add_bb(self.approval_view.user.id, refund_amount)

                embed = discord.Embed(title="❌ Custom Emoji/Sticker - DENIED", description=f"Request has been denied and {refund_amount} UKPence has been refunded.", color=0xff0000)
                embed.add_field(name="Denied by", value=modal_interaction.user.mention, inline=True)
                embed.add_field(name="Purchaser", value=self.approval_view.user.mention, inline=True)
                embed.add_field(name="Type", value=self.approval_view.upload_data['type'].title(), inline=True)
                embed.add_field(name="Name", value=self.approval_view.upload_data['name'], inline=True)
                embed.add_field(name="Reason", value=self.reason_input.value, inline=False)
                embed.add_field(name="Refund", value=f"{refund_amount} UKPence", inline=True)

                for item in self.approval_view.children:
                    item.disabled = True

                await modal_interaction.edit_original_response(embed=embed, view=self.approval_view)

                try:
                    user_embed = discord.Embed(title="❌ Custom Emoji/Sticker Request Denied", description=f"Your request for a custom {self.approval_view.upload_data['type']} has been denied.", color=0xff0000)
                    user_embed.add_field(name="Name", value=self.approval_view.upload_data['name'], inline=True)
                    user_embed.add_field(name="Reason", value=self.reason_input.value, inline=False)
                    user_embed.add_field(name="Refund", value=f"You have been refunded {refund_amount} UKPence", inline=False)
                    await self.approval_view.user.send(embed=user_embed)
                except discord.Forbidden:
                    pass

        await interaction.response.send_modal(DenyReasonModal(self))


