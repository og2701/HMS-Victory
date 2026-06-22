import re
import time
import sqlite3
import logging
from typing import Dict, Any
from database import DatabaseManager

logger = logging.getLogger(__name__)

# Amount inside a bank-ledger log line, e.g. "🏦 Bank deposit: `1,234` UKP|...".
_LEDGER_AMOUNT = re.compile(r"`([\d,]+(?:\.\d+)?)`")

class BankManager:
    """Manages the server's bank balance from shop purchases"""

    @staticmethod
    def deposit(amount: float, description: str = "Shop purchase") -> bool:
        """Deposit UKPence into the bank"""
        if amount <= 0:
            return False

        from config import BOT_ID
        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                c = conn.cursor()
                c.execute('BEGIN TRANSACTION')
                
                # Get current bot user balance
                c.execute('SELECT balance FROM ukpence WHERE user_id = ?', (str(BOT_ID),))
                res = c.fetchone()
                current_bot_balance = res[0] if res else 0
                new_bot_balance = current_bot_balance + amount
                
                # Update bot balance in ukpence
                c.execute('''
                    INSERT OR REPLACE INTO ukpence (user_id, balance)
                    VALUES (?, ?)
                ''', (str(BOT_ID), new_bot_balance))
                
                # Sync bank table statistics (stakes entering the bank, by game)
                bj_in_add = amount if "Blackjack" in description else 0
                hl_in_add = amount if "Higher-Lower" in description else 0
                slots_in_add = amount if "Slots" in description else 0
                vp_in_add = amount if "Video Poker" in description else 0
                reddog_in_add = amount if "Red Dog" in description else 0
                tcp_in_add = amount if "Three Card Poker" in description else 0
                roulette_in_add = amount if "Roulette" in description else 0
                mines_in_add = amount if "Mines" in description else 0
                penalty_in_add = amount if "Penalty" in description else 0
                tax_add = amount if "Wealth tax" in description else 0
                c.execute('''
                    UPDATE bank
                    SET balance = ?, total_revenue = total_revenue + ?,
                        total_blackjack_in = total_blackjack_in + ?,
                        total_higherlower_in = total_higherlower_in + ?,
                        total_slots_in = total_slots_in + ?,
                        total_videopoker_in = total_videopoker_in + ?,
                        total_reddog_in = total_reddog_in + ?,
                        total_tcp_in = total_tcp_in + ?,
                        total_roulette_in = total_roulette_in + ?,
                        total_mines_in = total_mines_in + ?,
                        total_penalty_in = total_penalty_in + ?,
                        total_tax_collected = total_tax_collected + ?, last_updated = ?
                    WHERE id = 1
                ''', (new_bot_balance, amount, bj_in_add, hl_in_add, slots_in_add,
                      vp_in_add, reddog_in_add, tcp_in_add, roulette_in_add, mines_in_add,
                      penalty_in_add, tax_add, now))
                
                c.execute('COMMIT')

            log_text = f"🏦 Bank deposit: `{amount:,}` UKP|{description}"
            DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
            logger.info(f"Bank deposit: {amount} UKP. Reason: {description}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error depositing into bank: {e}")
            return False

    @staticmethod
    def deposit_tax(amount: float, description: str = "Tax collection") -> bool:
        """Deposit UKPence into the bank from tax collection, tracking it separately"""
        if amount <= 0:
            return False

        from config import BOT_ID
        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                c = conn.cursor()
                c.execute('BEGIN TRANSACTION')
                
                # Get current bot user balance
                c.execute('SELECT balance FROM ukpence WHERE user_id = ?', (str(BOT_ID),))
                res = c.fetchone()
                current_bot_balance = res[0] if res else 0
                new_bot_balance = current_bot_balance + amount
                
                # Update bot balance in ukpence
                c.execute('''
                    INSERT OR REPLACE INTO ukpence (user_id, balance)
                    VALUES (?, ?)
                ''', (str(BOT_ID), new_bot_balance))
                
                # Everything routed through deposit_tax IS a tax (wealth tax, inactivity tax,
                # wealth demurrage), so the full amount always counts toward total_tax_collected -
                # not just descriptions containing "Wealth tax".
                c.execute('''
                    UPDATE bank
                    SET balance = ?, total_revenue = total_revenue + ?, total_tax_collected = total_tax_collected + ?, last_updated = ?
                    WHERE id = 1
                ''', (new_bot_balance, amount, amount, now))
                
                c.execute('COMMIT')

            log_text = f"🏦 Bank deposit: `{amount:,}` UKP|{description}"
            DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
            logger.info(f"Bank tax deposit: {amount} UKP. Reason: {description}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error depositing tax into bank: {e}")
            return False

    @staticmethod
    def withdraw(amount: float, description: str = "Unspecified Withdrawal") -> bool:
        if amount < 0:
            logger.warning(f"Attempted to withdraw negative amount from bank: {amount}")
            return False

        from config import BOT_ID
        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                c = conn.cursor()
                c.execute('BEGIN TRANSACTION')
                
                # Check bot's balance in ukpence
                c.execute('SELECT balance FROM ukpence WHERE user_id = ?', (str(BOT_ID),))
                res = c.fetchone()
                current_balance = res[0] if res else 0

                if current_balance >= amount:
                    new_balance = current_balance - amount
                    
                    # Update bot's balance in ukpence
                    c.execute('''
                        INSERT OR REPLACE INTO ukpence (user_id, balance)
                        VALUES (?, ?)
                    ''', (str(BOT_ID), new_balance))
                    
                    # Update bank table stats (payouts leaving the bank, by game)
                    bj_out_add = amount if "Blackjack" in description else 0
                    hl_out_add = amount if "Higher-Lower" in description else 0
                    slots_out_add = amount if "Slots" in description else 0
                    vp_out_add = amount if "Video Poker" in description else 0
                    reddog_out_add = amount if "Red Dog" in description else 0
                    tcp_out_add = amount if "Three Card Poker" in description else 0
                    roulette_out_add = amount if "Roulette" in description else 0
                    mines_out_add = amount if "Mines" in description else 0
                    penalty_out_add = amount if "Penalty" in description else 0
                    c.execute('''
                        UPDATE bank
                        SET balance = ?,
                            total_blackjack_out = total_blackjack_out + ?,
                            total_higherlower_out = total_higherlower_out + ?,
                            total_slots_out = total_slots_out + ?,
                            total_videopoker_out = total_videopoker_out + ?,
                            total_reddog_out = total_reddog_out + ?,
                            total_tcp_out = total_tcp_out + ?,
                            total_roulette_out = total_roulette_out + ?,
                            total_mines_out = total_mines_out + ?,
                            total_penalty_out = total_penalty_out + ?,
                            last_updated = ?
                        WHERE id = 1
                    ''', (new_balance, bj_out_add, hl_out_add, slots_out_add,
                          vp_out_add, reddog_out_add, tcp_out_add, roulette_out_add,
                          mines_out_add, penalty_out_add, now))
                    
                    c.execute('COMMIT')

                    log_text = f"📉 Bank withdrawal: `{amount:,}` UKP|{description}"
                    DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
                    logger.info(f"Bank withdrawal: {amount} UKP. Reason: {description}")
                    return True
                else:
                    c.execute('ROLLBACK')
                    logger.warning(f"Insufficient funds in bank for withdrawal of {amount} UKP.")
                    return False
        except sqlite3.Error as e:
            logger.error(f"Error withdrawing from bank: {e}")
            return False

    @staticmethod
    def get_balance() -> float:
        """Get current bank balance"""
        from config import BOT_ID
        result = DatabaseManager.fetch_one('SELECT balance FROM ukpence WHERE user_id = ?', (str(BOT_ID),))
        return result[0] if result else 0

    @staticmethod
    def get_bank_info() -> Dict[str, Any]:
        """Get complete bank information"""
        from config import BOT_ID
        
        # Fetch balance from ukpence for BOT_ID
        balance = BankManager.get_balance()
        
        # Fetch total_revenue, total_tax_collected, last_updated from bank table
        result = DatabaseManager.fetch_one('SELECT total_revenue, total_tax_collected, last_updated FROM bank WHERE id = 1')

        if result:
            return {
                'balance': balance,
                'total_revenue': result[0],
                'total_tax_collected': result[1],
                'last_updated': result[2]
            }
        else:
            return {
                'balance': balance,
                'total_revenue': 0,
                'total_tax_collected': 0,
                'last_updated': 0
            }

    @staticmethod
    def get_ledger_stats() -> Dict[str, int]:
        """Get bank metrics directly from the bank table.

        Returns tax_collected plus per-game house P/L (wagered in, paid out, net) for
        blackjack, higher-lower and slots, and a combined casino total. A positive net
        means the house (bank) is ahead.
        """
        cols = ("total_tax_collected, "
                "total_blackjack_in, total_blackjack_out, "
                "total_higherlower_in, total_higherlower_out, "
                "total_slots_in, total_slots_out, "
                "total_videopoker_in, total_videopoker_out, "
                "total_reddog_in, total_reddog_out, "
                "total_tcp_in, total_tcp_out, "
                "total_roulette_in, total_roulette_out, "
                "total_mines_in, total_mines_out, "
                "total_penalty_in, total_penalty_out")
        result = DatabaseManager.fetch_one(f"SELECT {cols} FROM bank WHERE id = 1")
        if result:
            (tax, bj_in, bj_out, hl_in, hl_out, sl_in, sl_out,
             vp_in, vp_out, rd_in, rd_out, tcp_in, tcp_out, ro_in, ro_out,
             mi_in, mi_out, pen_in, pen_out) = result
        else:
            tax = bj_in = bj_out = hl_in = hl_out = sl_in = sl_out = 0
            vp_in = vp_out = rd_in = rd_out = tcp_in = tcp_out = ro_in = ro_out = 0
            mi_in = mi_out = pen_in = pen_out = 0

        casino_in = bj_in + hl_in + sl_in + vp_in + rd_in + tcp_in + ro_in + mi_in + pen_in
        casino_out = bj_out + hl_out + sl_out + vp_out + rd_out + tcp_out + ro_out + mi_out + pen_out
        return {
            "tax_collected": tax,
            "blackjack_in": bj_in, "blackjack_out": bj_out, "blackjack_net": bj_in - bj_out,
            "higherlower_in": hl_in, "higherlower_out": hl_out, "higherlower_net": hl_in - hl_out,
            "slots_in": sl_in, "slots_out": sl_out, "slots_net": sl_in - sl_out,
            "videopoker_in": vp_in, "videopoker_out": vp_out, "videopoker_net": vp_in - vp_out,
            "reddog_in": rd_in, "reddog_out": rd_out, "reddog_net": rd_in - rd_out,
            "tcp_in": tcp_in, "tcp_out": tcp_out, "tcp_net": tcp_in - tcp_out,
            "roulette_in": ro_in, "roulette_out": ro_out, "roulette_net": ro_in - ro_out,
            "mines_in": mi_in, "mines_out": mi_out, "mines_net": mi_in - mi_out,
            "penalty_in": pen_in, "penalty_out": pen_out, "penalty_net": pen_in - pen_out,
            "casino_in": casino_in, "casino_out": casino_out, "casino_net": casino_in - casino_out,
        }

    @staticmethod
    def set_balance(amount: float, description: str = "Administrative adjustment") -> bool:
        if amount < 0:
            logger.warning(f"Attempted to set bank balance to negative: {amount}")
            return False
            
        from config import BOT_ID
        now = int(time.time())
        try:
            old_balance = BankManager.get_balance()
            with DatabaseManager.locked_connection() as conn:
                c = conn.cursor()
                c.execute('BEGIN TRANSACTION')
                
                # Update bot's balance in ukpence
                c.execute('''
                    INSERT OR REPLACE INTO ukpence (user_id, balance)
                    VALUES (?, ?)
                ''', (str(BOT_ID), amount))
                
                # Update bank table stats
                c.execute('''
                    UPDATE bank 
                    SET balance = ?, last_updated = ?
                    WHERE id = 1
                ''', (amount, now))
                
                c.execute('COMMIT')
                
            log_text = f"⚖️ Bank balance set to `{amount:,}` UKP (was `{old_balance:,}`)|{description}"
            DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
            
            logger.info(f"Bank balance reset to {amount} UKP")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error resetting bank balance: {e}")
            return False