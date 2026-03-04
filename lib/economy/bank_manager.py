from database import DatabaseManager
import time
from typing import Dict, Any

class BankManager:
    """Manages the server's bank balance from shop purchases"""

    @staticmethod
    def deposit(amount: int, description: str = "Shop purchase") -> bool:
        """Deposit UKPence into the bank"""
        if amount <= 0:
            return False

        now = int(time.time())
        try:
            with DatabaseManager.get_connection() as conn:
                c = conn.cursor()
                c.execute('''
                    UPDATE bank
                    SET balance = balance + ?, total_revenue = total_revenue + ?, last_updated = ?
                    WHERE id = 1
                ''', (amount, amount, now))
                conn.commit()

            log_text = f"🏦 **Bank Deposit**: `{amount}` UKP. **Reason**: {description}"
            DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
            logger.info(f"Bank deposit: {amount} UKP. Reason: {description}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error depositing into bank: {e}")
            return False

    @staticmethod
    def withdraw(amount: int, description: str = "Unspecified Withdrawal") -> bool:
        if amount < 0:
            logger.warning(f"Attempted to withdraw negative amount from bank: {amount}")
            return False

        now = int(time.time())
        try:
            with DatabaseManager.get_connection() as conn:
                c = conn.cursor()
                # Use a transaction to ensure balance doesn't go negative concurrently
                c.execute('BEGIN TRANSACTION')
                c.execute('SELECT balance FROM bank WHERE id = 1')
                current_balance = c.fetchone()[0]

                if current_balance >= amount:
                    c.execute('''
                        UPDATE bank
                        SET balance = balance - ?, last_updated = ?
                        WHERE id = 1
                    ''', (amount, now))
                    c.execute('COMMIT')

                    log_text = f"📉 **Bank Withdrawal**: `{amount}` UKP. **Reason**: {description}"
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
    def get_balance() -> int:
        """Get current bank balance"""
        result = DatabaseManager.fetch_one('SELECT balance FROM bank WHERE id = 1')
        return result[0] if result else 0

    @staticmethod
    def get_bank_info() -> Dict[str, Any]:
        """Get complete bank information"""
        result = DatabaseManager.fetch_one('SELECT balance, total_revenue, last_updated FROM bank WHERE id = 1')

        if result:
            return {
                'balance': result[0],
                'total_revenue': result[1],
                'last_updated': result[2]
            }
        else:
            return {
                'balance': 0,
                'total_revenue': 0,
                'last_updated': 0
            }

    @staticmethod
    def set_balance(amount: int, description: str = "Administrative adjustment") -> bool:
        if amount < 0:
            logger.warning(f"Attempted to set bank balance to negative: {amount}")
            return False
            
        now = int(time.time())
        try:
            old_balance = BankManager.get_balance()
            with DatabaseManager.get_connection() as conn:
                c = conn.cursor()
                c.execute('''
                    UPDATE bank 
                    SET balance = ?, last_updated = ?
                    WHERE id = 1
                ''', (amount, now))
                conn.commit()
                
            log_text = f"⚖️ **Bank Balance Set**: `{amount}` UKP (was `{old_balance}`). **Reason**: {description}"
            DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
            
            logger.info(f"Bank balance reset to {amount} UKP")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error resetting bank balance: {e}")
            return False