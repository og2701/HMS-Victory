import discord
import asyncio
from datetime import datetime
from typing import Optional
from lib.economy.auction_manager import AuctionManager

class AuctionScheduler:
    """Handles automatic ending of expired auctions."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self.running = False

    async def start_scheduler(self):
        """Start the auction scheduler task."""
        if self.running:
            return

        self.running = True
        while self.running:
            try:
                await self.process_expired_auctions()
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                print(f"Error in auction scheduler: {e}")
                await asyncio.sleep(60)

    def stop_scheduler(self):
        """Stop the auction scheduler."""
        self.running = False

    async def process_expired_auctions(self):
        """Process all expired auctions."""
        expired_auctions = AuctionManager.get_expired_auctions()

        for auction in expired_auctions:
            await self.end_auction(auction)

    async def end_auction(self, auction: dict):
        """End a single auction and notify participants."""
        try:
            success, winner_id, winning_bid = AuctionManager.end_auction(auction['id'])

            if not success:
                return

            # Create result embed
            embed = discord.Embed(
                title="🎯 Auction Ended",
                color=0xff9900
            )

            embed.add_field(name="🎁 Item", value=auction['item_name'], inline=True)

            if winner_id:
                embed.add_field(name="🏆 Winner", value=f"<@{winner_id}>", inline=True)
                embed.add_field(name="💰 Winning Bid", value=f"{winning_bid} UKPence", inline=True)

                # Try to notify the winner
                try:
                    winner = self.bot.get_user(int(winner_id))
                    if winner:
                        await winner.send(
                            f"🎉 Congratulations! You won the auction for **{auction['item_name']}** "
                            f"with a bid of {winning_bid} UKPence. Staff will contact you about your prize!"
                        )
                except:
                    pass  # Ignore if can't DM

            else:
                embed.description = f"No bids were placed on **{auction['item_name']}**."

            # Update the original auction message if possible
            if auction['channel_id'] and auction['message_id']:
                try:
                    channel = self.bot.get_channel(int(auction['channel_id']))
                    if channel:
                        message = await channel.fetch_message(int(auction['message_id']))
                        if message:
                            # Disable all buttons
                            view = discord.ui.View()
                            for item in message.components:
                                for component in item.children:
                                    component.disabled = True
                                    view.add_item(component)

                            await message.edit(embed=embed, view=view)
                except:
                    # If we can't edit the original message, send a new one
                    if auction['channel_id']:
                        channel = self.bot.get_channel(int(auction['channel_id']))
                        if channel:
                            await channel.send(embed=embed)

        except Exception as e:
            print(f"Error ending auction {auction['id']}: {e}")

# Function to be called from the main bot file
async def setup_auction_scheduler(bot: discord.Client) -> AuctionScheduler:
    """Set up and start the auction scheduler."""
    scheduler = AuctionScheduler(bot)
    asyncio.create_task(scheduler.start_scheduler())
    return scheduler