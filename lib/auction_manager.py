import sqlite3
import discord
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from lib.economy_manager import get_bb, remove_bb, add_bb

class AuctionManager:
    @staticmethod
    def create_auction(item_name: str, description: str, starting_bid: int, duration_hours: int, created_by: str) -> int:
        """Create a new auction and return its ID."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        end_time = int((datetime.now() + timedelta(hours=duration_hours)).timestamp())

        c.execute('''
            INSERT INTO auctions (item_name, description, starting_bid, current_bid, end_time, created_by)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (item_name, description, starting_bid, starting_bid, end_time, created_by))

        auction_id = c.lastrowid
        conn.commit()
        conn.close()
        return auction_id

    @staticmethod
    def get_auction(auction_id: int) -> Optional[Dict[str, Any]]:
        """Get auction details by ID."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        c.execute('''
            SELECT id, item_name, description, starting_bid, current_bid, current_bidder_id,
                   end_time, created_by, is_active, channel_id, message_id
            FROM auctions WHERE id = ?
        ''', (auction_id,))

        result = c.fetchone()
        conn.close()

        if result:
            return {
                'id': result[0],
                'item_name': result[1],
                'description': result[2],
                'starting_bid': result[3],
                'current_bid': result[4],
                'current_bidder_id': result[5],
                'end_time': result[6],
                'created_by': result[7],
                'is_active': bool(result[8]),
                'channel_id': result[9],
                'message_id': result[10]
            }
        return None

    @staticmethod
    def get_active_auctions() -> List[Dict[str, Any]]:
        """Get all active auctions."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        current_time = int(datetime.now().timestamp())
        c.execute('''
            SELECT id, item_name, description, starting_bid, current_bid, current_bidder_id,
                   end_time, created_by, channel_id, message_id
            FROM auctions
            WHERE is_active = 1 AND end_time > ?
            ORDER BY end_time ASC
        ''', (current_time,))

        results = c.fetchall()
        conn.close()

        auctions = []
        for result in results:
            auctions.append({
                'id': result[0],
                'item_name': result[1],
                'description': result[2],
                'starting_bid': result[3],
                'current_bid': result[4],
                'current_bidder_id': result[5],
                'end_time': result[6],
                'created_by': result[7],
                'channel_id': result[8],
                'message_id': result[9]
            })
        return auctions

    @staticmethod
    def place_bid(auction_id: int, user_id: str, bid_amount: int) -> Tuple[bool, str]:
        """Place a bid on an auction. Returns (success, message)."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        # Get auction details
        auction = AuctionManager.get_auction(auction_id)
        if not auction:
            conn.close()
            return False, "Auction not found."

        if not auction['is_active']:
            conn.close()
            return False, "This auction has ended."

        if auction['end_time'] <= datetime.now().timestamp():
            conn.close()
            return False, "This auction has expired."

        if bid_amount <= auction['current_bid']:
            conn.close()
            return False, f"Bid must be higher than current bid of {auction['current_bid']} UKPence."

        # Check if user has enough balance
        user_balance = get_bb(int(user_id))
        if user_balance < bid_amount:
            conn.close()
            return False, f"Insufficient funds. You have {user_balance} UKPence but need {bid_amount}."

        # Check if user won an auction in the last 7 days
        if AuctionManager.user_won_recently(user_id, days=7):
            conn.close()
            return False, "You have won an auction in the last 7 days. Please wait before bidding again."

        # Refund previous bidder if there was one
        if auction['current_bidder_id']:
            add_bb(int(auction['current_bidder_id']), auction['current_bid'])

        # Take payment from new bidder
        if not remove_bb(int(user_id), bid_amount):
            conn.close()
            return False, "Payment failed. Please try again."

        # Update auction
        c.execute('''
            UPDATE auctions
            SET current_bid = ?, current_bidder_id = ?
            WHERE id = ?
        ''', (bid_amount, user_id, auction_id))

        # Add to bid history
        c.execute('''
            INSERT INTO auction_history (auction_id, user_id, bid_amount, bid_time)
            VALUES (?, ?, ?, ?)
        ''', (auction_id, user_id, bid_amount, int(datetime.now().timestamp())))

        conn.commit()
        conn.close()
        return True, f"Bid placed successfully! You are now the highest bidder with {bid_amount} UKPence."

    @staticmethod
    def end_auction(auction_id: int) -> Tuple[bool, Optional[str], Optional[int]]:
        """End an auction and return (success, winner_id, winning_bid)."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        auction = AuctionManager.get_auction(auction_id)
        if not auction:
            conn.close()
            return False, None, None

        # Mark auction as inactive
        c.execute('UPDATE auctions SET is_active = 0 WHERE id = ?', (auction_id,))

        # If there was a winner, record it
        if auction['current_bidder_id']:
            c.execute('''
                INSERT INTO auction_winners (user_id, won_time, auction_id, item_name, winning_bid)
                VALUES (?, ?, ?, ?, ?)
            ''', (auction['current_bidder_id'], int(datetime.now().timestamp()),
                  auction_id, auction['item_name'], auction['current_bid']))

        conn.commit()
        conn.close()
        return True, auction['current_bidder_id'], auction['current_bid']

    @staticmethod
    def user_won_recently(user_id: str, days: int = 7) -> bool:
        """Check if user won an auction in the last N days."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        cutoff_time = int((datetime.now() - timedelta(days=days)).timestamp())
        c.execute('''
            SELECT COUNT(*) FROM auction_winners
            WHERE user_id = ? AND won_time > ?
        ''', (user_id, cutoff_time))

        count = c.fetchone()[0]
        conn.close()
        return count > 0

    @staticmethod
    def update_auction_message(auction_id: int, channel_id: str, message_id: str):
        """Update the channel and message ID for an auction."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        c.execute('''
            UPDATE auctions
            SET channel_id = ?, message_id = ?
            WHERE id = ?
        ''', (channel_id, message_id, auction_id))

        conn.commit()
        conn.close()

    @staticmethod
    def get_auction_history(auction_id: int) -> List[Dict[str, Any]]:
        """Get bid history for an auction."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        c.execute('''
            SELECT user_id, bid_amount, bid_time
            FROM auction_history
            WHERE auction_id = ?
            ORDER BY bid_time DESC
        ''', (auction_id,))

        results = c.fetchall()
        conn.close()

        history = []
        for result in results:
            history.append({
                'user_id': result[0],
                'bid_amount': result[1],
                'bid_time': result[2]
            })
        return history

    @staticmethod
    def get_expired_auctions() -> List[Dict[str, Any]]:
        """Get auctions that have expired but are still active."""
        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        current_time = int(datetime.now().timestamp())
        c.execute('''
            SELECT id, item_name, current_bidder_id, current_bid, channel_id, message_id
            FROM auctions
            WHERE is_active = 1 AND end_time <= ?
        ''', (current_time,))

        results = c.fetchall()
        conn.close()

        expired = []
        for result in results:
            expired.append({
                'id': result[0],
                'item_name': result[1],
                'current_bidder_id': result[2],
                'current_bid': result[3],
                'channel_id': result[4],
                'message_id': result[5]
            })
        return expired