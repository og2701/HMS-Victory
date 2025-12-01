import discord
from discord.ui import Modal, TextInput, View, Button
from datetime import datetime, timedelta
import asyncio
from typing import Optional
from lib.economy.auction_manager import AuctionManager
from lib.economy.economy_manager import get_bb, ensure_bb
from config import ROLES

class CreateAuctionModal(Modal, title="Create New Auction"):
    def __init__(self):
        super().__init__()

    item_name = TextInput(
        label="Item Name",
        placeholder="e.g., Amazon Gift Card",
        required=True,
        max_length=100
    )

    description = TextInput(
        label="Description",
        placeholder="Detailed description of the item...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    starting_bid = TextInput(
        label="Starting Bid (UKPence)",
        placeholder="e.g., 500",
        required=True,
        max_length=10
    )

    duration = TextInput(
        label="Duration (hours)",
        placeholder="e.g., 24",
        required=True,
        max_length=3
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            starting_bid = int(self.starting_bid.value)
            duration = int(self.duration.value)

            if starting_bid <= 0:
                await interaction.response.send_message("❌ Starting bid must be positive!", ephemeral=True)
                return

            if duration <= 0 or duration > 168:  # Max 1 week
                await interaction.response.send_message("❌ Duration must be between 1 and 168 hours!", ephemeral=True)
                return

            # Create auction
            auction_id = AuctionManager.create_auction(
                self.item_name.value,
                self.description.value,
                starting_bid,
                duration,
                str(interaction.user.id)
            )

            # Create auction embed and view
            embed = create_auction_embed(auction_id)
            view = AuctionView(auction_id)

            await interaction.response.send_message(
                f"✅ Auction created! (ID: {auction_id})",
                embed=embed,
                view=view
            )

            # Update auction with message details
            message = await interaction.original_response()
            AuctionManager.update_auction_message(auction_id, str(interaction.channel.id), str(message.id))

            # Schedule auction end (if using scheduler)
            # You could integrate with apscheduler here

        except ValueError:
            await interaction.response.send_message("❌ Please enter valid numbers for bid and duration!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error creating auction: {str(e)}", ephemeral=True)

class BidModal(Modal, title="Place Bid"):
    def __init__(self, auction_id: int, current_bid: int):
        super().__init__()
        self.auction_id = auction_id
        self.current_bid = current_bid

    bid_amount = TextInput(
        label="Your Bid (UKPence)",
        placeholder="Enter amount higher than current bid...",
        required=True,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            bid_amount = int(self.bid_amount.value)

            if bid_amount <= self.current_bid:
                await interaction.response.send_message(
                    f"❌ Your bid must be higher than the current bid of {self.current_bid} UKPence!",
                    ephemeral=True
                )
                return

            ensure_bb(interaction.user.id)
            success, message = AuctionManager.place_bid(self.auction_id, str(interaction.user.id), bid_amount)

            if success:
                # Update the auction embed
                embed = create_auction_embed(self.auction_id)
                view = AuctionView(self.auction_id)

                await interaction.response.edit_message(embed=embed, view=view)

                # Send confirmation to bidder
                await interaction.followup.send(f"✅ {message}", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ {message}", ephemeral=True)

        except ValueError:
            await interaction.response.send_message("❌ Please enter a valid number!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Error placing bid: {str(e)}", ephemeral=True)

class AuctionView(View):
    def __init__(self, auction_id: int):
        super().__init__(timeout=None)  # Persistent view
        self.auction_id = auction_id

    @discord.ui.button(label="Place Bid", style=discord.ButtonStyle.primary, emoji="💰")
    async def place_bid(self, interaction: discord.Interaction, button: discord.ui.Button):
        auction = AuctionManager.get_auction(self.auction_id)
        if not auction:
            await interaction.response.send_message("❌ Auction not found!", ephemeral=True)
            return

        if not auction['is_active']:
            await interaction.response.send_message("❌ This auction has ended!", ephemeral=True)
            return

        if auction['end_time'] <= datetime.now().timestamp():
            await interaction.response.send_message("❌ This auction has expired!", ephemeral=True)
            return

        # Check if user won recently
        if AuctionManager.user_won_recently(str(interaction.user.id)):
            await interaction.response.send_message(
                "❌ You have won an auction in the last 7 days. Please wait before bidding again!",
                ephemeral=True
            )
            return

        modal = BidModal(self.auction_id, auction['current_bid'])
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="View History", style=discord.ButtonStyle.secondary, emoji="📊")
    async def view_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        history = AuctionManager.get_auction_history(self.auction_id)

        if not history:
            await interaction.response.send_message("No bids have been placed yet.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Bid History",
            color=0x0099ff
        )

        history_text = []
        for i, bid in enumerate(history[:10]):  # Show last 10 bids
            user_mention = f"<@{bid['user_id']}>"
            timestamp = discord.utils.format_dt(datetime.fromtimestamp(bid['bid_time']), style='R')
            history_text.append(f"{i+1}. {user_mention} - {bid['bid_amount']} UKPence {timestamp}")

        embed.description = "\n".join(history_text) if history_text else "No bids yet."
        await interaction.response.send_message(embed=embed, ephemeral=True)

def create_auction_embed(auction_id: int) -> Optional[discord.Embed]:
    """Create an embed for an auction."""
    auction = AuctionManager.get_auction(auction_id)
    if not auction:
        return None

    embed = discord.Embed(
        title=f"🎯 Auction: {auction['item_name']}",
        description=auction['description'],
        color=0x00ff00 if auction['is_active'] else 0xff0000
    )

    end_time = datetime.fromtimestamp(auction['end_time'])
    embed.add_field(
        name="⏰ Ends",
        value=discord.utils.format_dt(end_time, style='R'),
        inline=True
    )

    embed.add_field(
        name="💰 Current Bid",
        value=f"{auction['current_bid']} UKPence",
        inline=True
    )

    if auction['current_bidder_id']:
        embed.add_field(
            name="🏆 Leading Bidder",
            value=f"<@{auction['current_bidder_id']}>",
            inline=True
        )
    else:
        embed.add_field(
            name="🏆 Leading Bidder",
            value="No bids yet",
            inline=True
        )

    status = "🟢 Active" if auction['is_active'] and auction['end_time'] > datetime.now().timestamp() else "🔴 Ended"
    embed.add_field(
        name="📊 Status",
        value=status,
        inline=True
    )

    embed.add_field(
        name="🆔 Auction ID",
        value=auction['id'],
        inline=True
    )

    embed.set_footer(text="Note: You cannot bid if you won an auction in the last 7 days")
    return embed

async def handle_auction_create_command(interaction: discord.Interaction):
    """
    Create a new auction (Staff only).

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """
    # Check if user has permission (staff roles)
    staff_roles = [ROLES.DEPUTY_PM, ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
    if not any(role.id in staff_roles for role in interaction.user.roles):
        await interaction.response.send_message("❌ Only staff members can create auctions!", ephemeral=True)
        return

    modal = CreateAuctionModal()
    await interaction.response.send_modal(modal)

async def handle_auction_list_command(interaction: discord.Interaction):
    """
    List all active auctions.

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.

    Returns:
        None
    """
    active_auctions = AuctionManager.get_active_auctions()

    embed = discord.Embed(
        title="🎯 Active Auctions",
        color=0x0099ff
    )

    if not active_auctions:
        embed.description = "No active auctions at the moment."
        await interaction.response.send_message(embed=embed)
        return

    auction_list = []
    for auction in active_auctions[:10]:  # Show up to 10 auctions
        end_time = datetime.fromtimestamp(auction['end_time'])
        time_left = discord.utils.format_dt(end_time, style='R')

        bidder_info = f"<@{auction['current_bidder_id']}>" if auction['current_bidder_id'] else "No bids"

        auction_list.append(
            f"**{auction['item_name']}** (ID: {auction['id']})\n"
            f"Current Bid: {auction['current_bid']} UKPence\n"
            f"Leading: {bidder_info}\n"
            f"Ends: {time_left}\n"
        )

    embed.description = "\n".join(auction_list)
    await interaction.response.send_message(embed=embed)

async def handle_auction_end_command(interaction: discord.Interaction, auction_id: int):
    """
    Manually end an auction (Staff only).

    Args:
        interaction (discord.Interaction): The interaction that triggered the command.
        auction_id (int): The ID of the auction to end.

    Returns:
        None
    """
    # Check if user has permission
    staff_roles = [ROLES.DEPUTY_PM, ROLES.MINISTER, ROLES.CABINET, ROLES.BORDER_FORCE]
    if not any(role.id in staff_roles for role in interaction.user.roles):
        await interaction.response.send_message("❌ Only staff members can end auctions!", ephemeral=True)
        return

    auction = AuctionManager.get_auction(auction_id)
    if not auction:
        await interaction.response.send_message("❌ Auction not found!", ephemeral=True)
        return

    if not auction['is_active']:
        await interaction.response.send_message("❌ This auction has already ended!", ephemeral=True)
        return

    success, winner_id, winning_bid = AuctionManager.end_auction(auction_id)

    if success:
        embed = discord.Embed(
            title="🎯 Auction Ended",
            color=0xff9900
        )

        if winner_id:
            embed.add_field(name="🏆 Winner", value=f"<@{winner_id}>", inline=True)
            embed.add_field(name="💰 Winning Bid", value=f"{winning_bid} UKPence", inline=True)
            embed.add_field(name="🎁 Item", value=auction['item_name'], inline=True)

            # Notify winner
            try:
                winner = interaction.guild.get_member(int(winner_id))
                if winner:
                    await winner.send(f"🎉 Congratulations! You won the auction for **{auction['item_name']}** with a bid of {winning_bid} UKPence. Staff will contact you about your prize!")
            except:
                pass  # Ignore if can't DM
        else:
            embed.description = f"No bids were placed on **{auction['item_name']}**."

        await interaction.response.send_message(embed=embed)
    else:
        await interaction.response.send_message("❌ Failed to end auction!", ephemeral=True)