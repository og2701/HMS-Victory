import time
import sqlite3
import logging
from typing import Dict, Any
from database import DatabaseManager

logger = logging.getLogger(__name__)

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
            with DatabaseManager.get_connection() as conn:
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
                
                # Sync bank table statistics
                c.execute('''
                    UPDATE bank
                    SET balance = ?, total_revenue = total_revenue + ?, last_updated = ?
                    WHERE id = 1
                ''', (new_bot_balance, amount, now))
                
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
            with DatabaseManager.get_connection() as conn:
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
                
                # Sync bank table stats and tax collected
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
            with DatabaseManager.get_connection() as conn:
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
                    
                    # Update bank table stats
                    c.execute('''
                        UPDATE bank
                        SET balance = ?, last_updated = ?
                        WHERE id = 1
                    ''', (new_balance, now))
                    
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
    def set_balance(amount: float, description: str = "Administrative adjustment") -> bool:
        if amount < 0:
            logger.warning(f"Attempted to set bank balance to negative: {amount}")
            return False
            
        from config import BOT_ID
        now = int(time.time())
        try:
            old_balance = BankManager.get_balance()
            with DatabaseManager.get_connection() as conn:
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