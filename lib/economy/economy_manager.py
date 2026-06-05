from database import DatabaseManager
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
        with DatabaseManager.locked_connection() as conn:
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
            else:
                try:
                    from lib.features.income_badges import bump_daily_income
                    bump_daily_income("welcome_total", amount)
                except Exception:
                    pass
    

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

        from config import BOT_ID
        if amount >= 30000 and old_balance < 30000 and str(user_id) != str(BOT_ID):
            from lib.bot.event_handlers import award_badge_notify
            award_badge_notify(user_id, 'high_roller')
            # Self-Made (secret): hit 30k without ever having claimed benefits.
            try:
                from lib.core.file_operations import load_json_file
                import config as _cfg
                _rec = (load_json_file(_cfg.BENEFITS_FILE) or {}).get(str(user_id))
                _claimed = bool(_rec.get("last")) if isinstance(_rec, dict) else bool(_rec)
                if not _claimed:
                    award_badge_notify(user_id, 'self_made')
            except Exception:
                pass
            
        log_text = f"⚖️ <@{user_id}> balance set to `{amount:,}` UKP (was `{old_balance:,}`)|{reason}"
        DatabaseManager.execute("INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)", (now, log_text))
        
    @staticmethod
    def add_amount(user_id: int, amount: int, reason: str = "Unspecified") -> None:
        current = UKPenceManager.get_balance(user_id)
        UKPenceManager.set_balance(user_id, current + amount, reason=reason)

    @staticmethod
    def remove_amount(user_id: int, amount: int, reason: str = "Unspecified") -> bool:
        # Atomic update: only subtract if the balance is sufficient
        with DatabaseManager.locked_connection() as conn:
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
                from config import BOT_ID
                if new_balance == 0 and old_balance >= 1000 and str(user_id) != str(BOT_ID):
                    from lib.bot.event_handlers import award_badge_notify
                    award_badge_notify(user_id, 'bankrupt')
                
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

        from lib.core.file_operations import atomic_write_json
        atomic_write_json(ECONOMY_METRICS_FILE, metrics_data, indent=4)

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

WEALTH_TAX_BRACKETS = [
    (10_000, 0.00),
    (20_000, 0.60),
    (30_000, 0.85),
    (float("inf"), 0.95),
]


def compute_wealth_tax(balance: int, earning: int) -> int:
    """Compute the wealth-tax owed on a passive bank-funded earning.

    Tax kicks in once balance reaches 10k. Each slice of the earning that lands
    within a bracket is taxed at that bracket's rate. Returns an integer UKP
    amount (rounded down) so that tiny per-minute payouts naturally round to 0.
    """
    if earning <= 0:
        return 0
    tax = 0.0
    remaining = earning
    cursor = balance
    for top, rate in WEALTH_TAX_BRACKETS:
        if remaining <= 0:
            break
        if cursor >= top:
            continue
        slice_size = min(remaining, top - cursor)
        tax += slice_size * rate
        cursor += slice_size
        remaining -= slice_size
    return int(tax)


def add_bb(user_id: int, amount: int, reason: str = "Unspecified",
           from_bank: bool = True, taxable: bool = True) -> bool:
    """Credit a user with UKP.

    from_bank=True (default): withdraws from the server bank first - UKP is conserved.
    from_bank=False: pure user credit for p2p transfers (e.g. /pay, wager payout) where
                     the sender's remove_bb already handled the bank side.
    taxable=True (default): applies the progressive wealth tax for balances ≥10k
                            when from_bank is also True. Tax returns to the bank.
                            Set False for refunds and exempt earning types
                            (prediction wins, wager wins).
    Returns True if successful, False if the bank couldn't cover it.
    """
    if from_bank:
        from lib.economy.bank_manager import BankManager
        if not BankManager.withdraw(amount, description=reason):
            return False

        if taxable and amount > 0:
            current_balance = UKPenceManager.get_balance(user_id)
            tax_amount = compute_wealth_tax(current_balance, amount)
            if tax_amount > 0:
                gross = amount
                amount -= tax_amount
                effective_rate = tax_amount / gross
                # deposit_tax (not deposit) so it also increments total_tax_collected;
                # plain deposit only bumped total_revenue, leaving the tax counter at 0.
                BankManager.deposit_tax(
                    tax_amount,
                    description=f"Wealth tax on '{reason}' (gross: {gross:,}, rate: {effective_rate:.0%})",
                )
                reason = f"{reason} [gross: {gross:,}, tax: -{tax_amount:,} ({effective_rate:.0%})]"

    UKPenceManager.add_amount(user_id, amount, reason=reason)
    return True

def remove_bb(user_id: int, amount: int, reason: str = "Unspecified", to_bank: bool = True) -> bool:
    """Debit a user of UKP.
    
    to_bank=True (default): deposits the deducted amount back to the server bank - UKP is conserved.
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
