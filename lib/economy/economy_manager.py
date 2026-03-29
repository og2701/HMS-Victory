from database import DatabaseManager
import sqlite3
import datetime
import logging
import json
import os
from config import ECONOMY_METRICS_FILE

SHUTCOIN_ENABLED = True
SHOP = {"shutcoin": 1000}

class ShutcoinManager:
    @staticmethod
    def get_balance(user_id: int) -> int:
        result = DatabaseManager.fetch_one("SELECT balance FROM shutcoins WHERE user_id = ?", (str(user_id),))
        return result[0] if result else 0

    @staticmethod
    def set_balance(user_id: int, amount: int) -> None:
        DatabaseManager.execute("INSERT OR REPLACE INTO shutcoins (user_id, balance) VALUES (?, ?)", (str(user_id), amount))

    @staticmethod
    def add_amount(user_id: int, amount: int) -> None:
        current = ShutcoinManager.get_balance(user_id)
        ShutcoinManager.set_balance(user_id, current + amount)

    @staticmethod
    def remove_amount(user_id: int, amount: int = 1) -> bool:
        # Atomic update: only subtract if the balance is sufficient
        with DatabaseManager.get_connection() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE shutcoins SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
                (amount, str(user_id), amount)
            )
            conn.commit()
            return c.rowcount > 0

    @staticmethod
    def can_afford(user_id: int, amount: int = 1) -> bool:
        return ShutcoinManager.get_balance(user_id) >= amount

class UKPenceManager:
    @staticmethod
    def get_all_balances() -> dict:
        rows = DatabaseManager.fetch_all("SELECT user_id, balance FROM ukpence")
        balances = {str(row[0]): row[1] for row in rows}
        return balances

    @staticmethod
    def ensure_user(user_id: int) -> None:
        if not DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (str(user_id),)):
            amount = 10
            if not add_bb(user_id, amount, reason=f"New member welcome bonus"):
                # Fallback: create user with 0 if bank is empty
                DatabaseManager.execute("INSERT OR IGNORE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(user_id), 0))
    

    @staticmethod
    def get_balance(user_id: int) -> int:
        result = DatabaseManager.fetch_one("SELECT balance FROM ukpence WHERE user_id = ?", (str(user_id),))
        return result[0] if result else 0

    @staticmethod
    def set_balance(user_id: int, amount: int, reason: str = "Unspecified") -> None:
        import time
        now = int(time.time())
        old_balance = UKPenceManager.get_balance(user_id)
        DatabaseManager.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(user_id), amount))
        
        if amount >= 100000 and old_balance < 100000:
            from database import award_badge
            award_badge(user_id, 'high_roller')
            
        log_text = f"⚖️ <@{user_id}> balance set to `{amount:,}` UKP (was `{old_balance:,}`)|{reason}"
        DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
        
    @staticmethod
    def add_amount(user_id: int, amount: int, reason: str = "Unspecified") -> None:
        current = UKPenceManager.get_balance(user_id)
        UKPenceManager.set_balance(user_id, current + amount, reason=reason)

    @staticmethod
    def remove_amount(user_id: int, amount: int, reason: str = "Unspecified") -> bool:
        # Atomic update: only subtract if the balance is sufficient
        with DatabaseManager.get_connection() as conn:
            c = conn.cursor()
            
            c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(user_id),))
            res = c.fetchone()
            old_balance = res[0] if res else 0

            c.execute(
                "UPDATE ukpence SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
                (amount, str(user_id), amount)
            )
            success = c.rowcount > 0
            if success:
                new_balance = old_balance - amount
                if new_balance == 0 and old_balance >= 1000:
                    from database import award_badge
                    award_badge(user_id, 'bankrupt')
                
                import time
                now = int(time.time())
                log_text = f"💸 <@{user_id}> paid `{amount:,}` UKP|{reason}"
                c.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
            
            conn.commit()
            return success

class EconomyMetrics:
    @staticmethod
    def update_daily_metric(date_str: str, key: str, value_to_add_or_set: int, is_total_value: bool = False) -> None:
        metrics_data = {}
        if os.path.exists(ECONOMY_METRICS_FILE):
            with open(ECONOMY_METRICS_FILE, "r") as f:
                try:
                    metrics_data = json.load(f)
                except json.JSONDecodeError:
                    pass

        day_metrics = metrics_data.get(date_str, {})
        if is_total_value:
            day_metrics[key] = value_to_add_or_set
        else:
            current_value = day_metrics.get(key, 0)
            day_metrics[key] = current_value + value_to_add_or_set

        metrics_data[date_str] = day_metrics

        with open(ECONOMY_METRICS_FILE, "w") as f:
            json.dump(metrics_data, f, indent=4)

    @staticmethod
    def get_daily_metrics(date_str: str) -> dict:
        if os.path.exists(ECONOMY_METRICS_FILE):
            with open(ECONOMY_METRICS_FILE, "r") as f:
                data = json.load(f)
                return data.get(date_str, {})
        return {}

    @staticmethod
    def get_all_metrics() -> dict:
        if os.path.exists(ECONOMY_METRICS_FILE):
            with open(ECONOMY_METRICS_FILE, "r") as f:
                data = json.load(f)
                return data
        return {}

def get_shutcoins(user_id: int) -> int:
    return ShutcoinManager.get_balance(user_id)

def set_shutcoins(user_id: int, amount: int) -> None:
    ShutcoinManager.set_balance(user_id, amount)

def add_shutcoins(user_id: int, amount: int) -> None:
    ShutcoinManager.add_amount(user_id, amount)

def remove_shutcoin(user_id: int) -> bool:
    return ShutcoinManager.remove_amount(user_id, 1)

def can_use_shutcoin(user_id: int) -> bool:
    return ShutcoinManager.can_afford(user_id, 1)

def get_bb(user_id: int) -> int:
    return UKPenceManager.get_balance(user_id)

def set_bb(user_id: int, amount: int, reason: str = "Unspecified") -> None:
    UKPenceManager.set_balance(user_id, amount, reason=reason)

def add_bb(user_id: int, amount: int, reason: str = "Unspecified", from_bank: bool = True) -> bool:
    """Credit a user with UKP.
    
    from_bank=True (default): withdraws from the server bank first — UKP is conserved.
    from_bank=False: pure user credit for p2p transfers (e.g. /pay, wager payout) where
                     the sender's remove_bb already handled the bank side.
    Returns True if successful, False if the bank couldn't cover it.
    """
    if from_bank:
        from lib.economy.bank_manager import BankManager
        if not BankManager.withdraw(amount, description=reason):
            return False
    UKPenceManager.add_amount(user_id, amount, reason=reason)
    return True

def remove_bb(user_id: int, amount: int, reason: str = "Unspecified", to_bank: bool = True) -> bool:
    """Debit a user of UKP.
    
    to_bank=True (default): deposits the deducted amount back to the server bank — UKP is conserved.
    to_bank=False: pure user debit for p2p transfers (e.g. /pay, wager stake) where
                   add_bb on the recipient handles the bank side.
    Returns True if the user had sufficient funds, False otherwise.
    """
    success = UKPenceManager.remove_amount(user_id, amount, reason=reason)
    if success and to_bank:
        from lib.economy.bank_manager import BankManager
        BankManager.deposit(amount, description=reason)
    return success

def ensure_bb(user_id: int) -> None:
    UKPenceManager.ensure_user(user_id)

def get_all_balances() -> dict:
    return UKPenceManager.get_all_balances()
