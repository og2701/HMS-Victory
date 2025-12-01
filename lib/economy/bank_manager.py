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

        # Update bank balance and total revenue
        DatabaseManager.execute('''
            UPDATE bank
            SET balance = balance + ?,
                total_revenue = total_revenue + ?,
                last_updated = ?
            WHERE id = 1
        ''', (amount, amount, current_time))

        return True

    @staticmethod
    def withdraw(amount: int, description: str = "Admin withdrawal") -> bool:
        """Withdraw UKPence from the bank (admin only)"""
        if amount <= 0:
            return False

        # Check current balance
        result = DatabaseManager.fetch_one('SELECT balance FROM bank WHERE id = 1')

        if not result or result[0] < amount:
            return False  # Insufficient funds

        current_time = int(time.time())

        # Update bank balance
        DatabaseManager.execute('''
            UPDATE bank
            SET balance = balance - ?,
                last_updated = ?
            WHERE id = 1
        ''', (amount, current_time))

        return True

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
    def set_balance(amount: int) -> bool:
        """Set bank balance to specific amount (admin only)"""
        if amount < 0:
            return False

        current_time = int(time.time())

        DatabaseManager.execute('''
            UPDATE bank
            SET balance = ?,
                last_updated = ?
            WHERE id = 1
        ''', (amount, current_time))

        return True