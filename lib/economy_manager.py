import sqlite3
import json
import os
from typing import Optional, Dict

SHUTCOIN_ENABLED = True
SHOP = {"shutcoin": 1000}

class ShutcoinManager:
    @staticmethod
    def get_balance(user_id: int) -> int:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM shutcoins WHERE user_id = ?", (str(user_id),))
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0

    @staticmethod
    def set_balance(user_id: int, amount: int) -> None:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO shutcoins (user_id, balance) VALUES (?, ?)", (str(user_id), amount))
        conn.commit()
        conn.close()

    @staticmethod
    def add_amount(user_id: int, amount: int) -> None:
        current = ShutcoinManager.get_balance(user_id)
        ShutcoinManager.set_balance(user_id, current + amount)

    @staticmethod
    def remove_amount(user_id: int, amount: int = 1) -> bool:
        current = ShutcoinManager.get_balance(user_id)
        if current >= amount:
            ShutcoinManager.set_balance(user_id, current - amount)
            return True
        return False

    @staticmethod
    def can_afford(user_id: int, amount: int = 1) -> bool:
        return ShutcoinManager.get_balance(user_id) >= amount

class UKPenceManager:
    @staticmethod
    def get_all_balances() -> dict:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT user_id, balance FROM ukpence")
        balances = {str(row[0]): row[1] for row in c.fetchall()}
        conn.close()
        return balances

    @staticmethod
    def ensure_user(user_id: int) -> None:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(user_id),))
        if c.fetchone() is None:
            c.execute("INSERT INTO ukpence (user_id, balance) VALUES (?, ?)", (str(user_id), 20))
        conn.commit()
        conn.close()

    @staticmethod
    def get_balance(user_id: int) -> int:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT balance FROM ukpence WHERE user_id = ?", (str(user_id),))
        result = c.fetchone()
        conn.close()
        return result[0] if result else 0

    @staticmethod
    def set_balance(user_id: int, amount: int) -> None:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)", (str(user_id), amount))
        conn.commit()
        conn.close()

    @staticmethod
    def add_amount(user_id: int, amount: int) -> None:
        UKPenceManager.set_balance(user_id, UKPenceManager.get_balance(user_id) + amount)

    @staticmethod
    def remove_amount(user_id: int, amount: int) -> bool:
        balance = UKPenceManager.get_balance(user_id)
        if amount > balance:
            return False
        UKPenceManager.set_balance(user_id, balance - amount)
        return True

class EconomyMetrics:
    ECONOMY_METRICS_FILE = "economy_metrics.json"

    @staticmethod
    def update_daily_metric(date_str: str, key: str, value_to_add_or_set: int, is_total_value: bool = False) -> None:
        metrics_data = {}
        if os.path.exists(EconomyMetrics.ECONOMY_METRICS_FILE):
            with open(EconomyMetrics.ECONOMY_METRICS_FILE, "r") as f:
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

        with open(EconomyMetrics.ECONOMY_METRICS_FILE, "w") as f:
            json.dump(metrics_data, f, indent=4)

    @staticmethod
    def get_daily_metrics(date_str: str) -> dict:
        if os.path.exists(EconomyMetrics.ECONOMY_METRICS_FILE):
            with open(EconomyMetrics.ECONOMY_METRICS_FILE, "r") as f:
                data = json.load(f)
                return data.get(date_str, {})
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

def set_bb(user_id: int, amount: int) -> None:
    UKPenceManager.set_balance(user_id, amount)

def add_bb(user_id: int, amount: int) -> None:
    UKPenceManager.add_amount(user_id, amount)

def remove_bb(user_id: int, amount: int) -> bool:
    return UKPenceManager.remove_amount(user_id, amount)

def ensure_bb(user_id: int) -> None:
    UKPenceManager.ensure_user(user_id)

def get_all_balances() -> dict:
    return UKPenceManager.get_all_balances()