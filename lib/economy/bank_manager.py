import re
import time
import sqlite3
import logging
from typing import Dict, Any
from database import DatabaseManager

logger = logging.getLogger(__name__)

# Amount inside a bank-ledger log line, e.g. "🏦 Bank deposit: `1,234` UKP|...".
_LEDGER_AMOUNT = re.compile(r"`([\d,]+(?:\.\d+)?)`")


class BankStorageError(RuntimeError):
    """The bank transfer could not complete because durable storage failed."""


class BankManager:
    """Manages the server's bank balance from shop purchases"""

    @staticmethod
    def _bot_balance_in_transaction(cursor, *, required: bool = True):
        from config import BOT_ID
        cursor.execute('SELECT balance FROM ukpence WHERE user_id = ?', (str(BOT_ID),))
        row = cursor.fetchone()
        if row is None:
            if required:
                raise sqlite3.IntegrityError("bank BOT_ID balance row is missing")
            return 0
        return row[0]

    @staticmethod
    def _set_bot_balance_in_transaction(cursor, amount) -> None:
        from config import BOT_ID
        cursor.execute(
            'INSERT OR REPLACE INTO ukpence (user_id, balance) VALUES (?, ?)',
            (str(BOT_ID), amount),
        )

    @staticmethod
    def _require_bank_row(cursor) -> None:
        if cursor.rowcount != 1:
            raise sqlite3.IntegrityError("bank accounting row id=1 is missing")

    @staticmethod
    def _game_amounts(amount, description: str):
        return (
            amount if "Blackjack" in description else 0,
            amount if "Higher-Lower" in description else 0,
            amount if "Slots" in description else 0,
            amount if "Video Poker" in description else 0,
            amount if "Red Dog" in description else 0,
            amount if "Three Card Poker" in description else 0,
            amount if "Roulette" in description else 0,
            amount if "Mines" in description else 0,
            amount if "Penalty" in description else 0,
            amount if "Chest" in description else 0,
            amount if "Blockade" in description else 0,
            amount if "Darts" in description else 0,
            amount if "Glass Bridge" in description else 0,
        )

    @staticmethod
    def _deposit_in_transaction(cursor, amount, description: str, now: int,
                                *, tax_deposit: bool = False) -> None:
        current_balance = BankManager._bot_balance_in_transaction(cursor)
        new_balance = current_balance + amount
        BankManager._set_bot_balance_in_transaction(cursor, new_balance)

        if tax_deposit:
            # Everything routed through deposit_tax is a tax, irrespective of its label.
            cursor.execute('''
                UPDATE bank
                SET balance = ?, total_revenue = total_revenue + ?,
                    total_tax_collected = total_tax_collected + ?, last_updated = ?
                WHERE id = 1
            ''', (new_balance, amount, amount, now))
        else:
            game_amounts = BankManager._game_amounts(amount, description)
            tax_add = amount if "Wealth tax" in description else 0
            cursor.execute('''
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
                    total_chest_in = total_chest_in + ?,
                    total_blockade_in = total_blockade_in + ?,
                    total_darts_in = total_darts_in + ?,
                    total_glass_in = total_glass_in + ?,
                    total_tax_collected = total_tax_collected + ?, last_updated = ?
                WHERE id = 1
            ''', (new_balance, amount, *game_amounts, tax_add, now))
        BankManager._require_bank_row(cursor)
        cursor.execute(
            "INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)",
            (now, f"🏦 Bank deposit: `{amount:,}` UKP|{description}"),
        )

    @staticmethod
    def _withdraw_in_transaction(cursor, amount, description: str, now: int) -> bool:
        cursor.execute("SELECT balance FROM bank WHERE id = 1")
        bank_row = cursor.fetchone()
        if bank_row is None:
            raise sqlite3.IntegrityError("bank accounting row id=1 is missing")
        current_balance = BankManager._bot_balance_in_transaction(cursor)
        if current_balance < 0 or bank_row[0] != current_balance:
            raise sqlite3.IntegrityError(
                "bank balance and BOT_ID balance are invalid or out of sync"
            )
        if current_balance < amount:
            return False
        new_balance = current_balance - amount
        BankManager._set_bot_balance_in_transaction(cursor, new_balance)
        game_amounts = BankManager._game_amounts(amount, description)
        cursor.execute('''
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
                total_chest_out = total_chest_out + ?,
                total_blockade_out = total_blockade_out + ?,
                total_darts_out = total_darts_out + ?,
                total_glass_out = total_glass_out + ?,
                last_updated = ?
            WHERE id = 1
        ''', (new_balance, *game_amounts, now))
        BankManager._require_bank_row(cursor)
        cursor.execute(
            "INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)",
            (now, f"📉 Bank withdrawal: `{amount:,}` UKP|{description}"),
        )
        return True

    @staticmethod
    def deposit(amount: float, description: str = "Shop purchase") -> bool:
        """Deposit UKPence into the bank, including its stats and log atomically."""
        if amount <= 0:
            return False
        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                BankManager._deposit_in_transaction(
                    conn.cursor(), amount, description, now,
                )
            logger.info(f"Bank deposit: {amount} UKP. Reason: {description}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error depositing into bank: {e}")
            return False

    @staticmethod
    def deposit_tax(amount: float, description: str = "Tax collection") -> bool:
        """Deposit tax into the bank, including its stats and log atomically."""
        if amount <= 0:
            return False
        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                BankManager._deposit_in_transaction(
                    conn.cursor(), amount, description, now, tax_deposit=True,
                )
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
        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                success = BankManager._withdraw_in_transaction(
                    conn.cursor(), amount, description, now,
                )
            if not success:
                logger.warning(f"Insufficient funds in bank for withdrawal of {amount} UKP.")
                return False
            logger.info(f"Bank withdrawal: {amount} UKP. Reason: {description}")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error withdrawing from bank: {e}")
            return False

    @staticmethod
    def transfer_to_user(user_id: int, amount: int, description: str = "Unspecified",
                         *, taxable: bool = True) -> bool:
        """Atomically pay one user from the bank, including tax and every ledger.

        For a valid positive payout, ``False`` means only that the bank genuinely
        lacks the gross amount. SQLite/storage failures raise ``BankStorageError``
        so callers must never mistake a broken commit for insolvency.
        """
        if amount < 0:
            return False
        now = int(time.time())
        change = None
        try:
            from lib.economy.economy_manager import (
                UKPenceManager,
                _effective_wealth_in_transaction,
                compute_wealth_tax,
            )
            with DatabaseManager.locked_connection() as conn:
                cursor = conn.cursor()
                current_user_balance = UKPenceManager._get_balance_in_transaction(
                    cursor, user_id,
                )
                tax_amount = 0
                if taxable and amount > 0:
                    effective_balance = _effective_wealth_in_transaction(
                        cursor, user_id, balance=current_user_balance,
                    )
                    tax_amount = compute_wealth_tax(effective_balance, amount)

                if not BankManager._withdraw_in_transaction(
                    cursor, amount, description, now,
                ):
                    return False

                net_amount = amount - tax_amount
                user_reason = description
                if tax_amount > 0:
                    effective_rate = tax_amount / amount
                    tax_description = (
                        f"Wealth tax on '{description}' "
                        f"(gross: {amount:,}, rate: {effective_rate:.0%})"
                    )
                    BankManager._deposit_in_transaction(
                        cursor, tax_amount, tax_description, now, tax_deposit=True,
                    )
                    user_reason = (
                        f"{description} [gross: {amount:,}, tax: -{tax_amount:,} "
                        f"({effective_rate:.0%})]"
                    )

                change = UKPenceManager._add_amount_in_transaction(
                    cursor, user_id, net_amount, user_reason, now,
                )
            UKPenceManager._finish_change(change, high_roller=True)
            return True
        except sqlite3.Error as e:
            message = f"Error transferring {amount} UKP from bank to {user_id}: {e}"
            logger.error(message)
            raise BankStorageError(message) from e

    @staticmethod
    def transfer_to_users(recipient_ids, amount: int,
                          description: str = "Administrative handout",
                          *, user_reason: str = None,
                          welcome_new_users: bool = True) -> bool:
        """Atomically pay the same positive integer amount to unique users.

        The bank is debited once for the full handout cost; every recipient
        balance and durable user ledger is then written in that same transaction.
        Duplicate recipient ids are coalesced so one user can never be paid twice
        by a malformed selection. By default, recipients without a balance row
        also receive the existing 10-UKP welcome bonus when the bank can cover it.
        """
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            return False
        from config import BOT_ID
        try:
            recipients = []
            seen = set()
            for user_id in recipient_ids:
                uid = str(user_id)
                if uid == str(BOT_ID):
                    return False
                if uid not in seen:
                    seen.add(uid)
                    recipients.append(uid)
        except (TypeError, ValueError):
            return False
        if not recipients:
            return False

        now = int(time.time())
        total = amount * len(recipients)
        changes = []
        welcome_total = 0
        reason = user_reason if user_reason is not None else description
        try:
            from lib.economy.economy_manager import UKPenceManager
            with DatabaseManager.locked_connection() as conn:
                cursor = conn.cursor()
                new_users = set()
                if welcome_new_users:
                    for user_id in recipients:
                        cursor.execute(
                            "SELECT 1 FROM ukpence WHERE user_id = ?",
                            (user_id,),
                        )
                        if cursor.fetchone() is None:
                            new_users.add(user_id)
                if not BankManager._withdraw_in_transaction(
                    cursor, total, description, now,
                ):
                    return False
                for user_id in recipients:
                    if user_id in new_users and BankManager._withdraw_in_transaction(
                        cursor, 10, "New member welcome bonus", now,
                    ):
                        changes.append(UKPenceManager._add_amount_in_transaction(
                            cursor,
                            user_id,
                            10,
                            "New member welcome bonus",
                            now,
                        ))
                        welcome_total += 10
                    changes.append(UKPenceManager._add_amount_in_transaction(
                        cursor, user_id, amount, reason, now,
                    ))
            for change in changes:
                UKPenceManager._finish_change(change, high_roller=True)
            if welcome_total:
                try:
                    from lib.features.income_badges import bump_daily_income
                    bump_daily_income("welcome_total", welcome_total)
                except Exception:
                    pass
            return True
        except sqlite3.Error as e:
            logger.error(
                "Error transferring %s UKP each from bank to %s users: %s",
                amount, len(recipients), e,
            )
            return False

    @staticmethod
    def transfer_from_user(user_id: int, amount: int,
                           description: str = "Unspecified",
                           *, tax_deposit: bool = False,
                           record_pay_transfer: bool = False) -> bool:
        """Atomically move UKP from one user into the bank and all its ledgers."""
        if amount <= 0:
            return False
        now = int(time.time())
        change = None
        try:
            from lib.economy.economy_manager import UKPenceManager
            with DatabaseManager.locked_connection() as conn:
                cursor = conn.cursor()
                change = UKPenceManager._remove_amount_in_transaction(
                    cursor, user_id, amount, description, now,
                )
                if change is None:
                    return False
                BankManager._deposit_in_transaction(
                    cursor, amount, description, now, tax_deposit=tax_deposit,
                )
                if record_pay_transfer:
                    from config import BOT_ID
                    cursor.execute(
                        "INSERT INTO pay_transfers "
                        "(timestamp, payer_id, recipient_id, amount) VALUES (?, ?, ?, ?)",
                        (now, str(user_id), str(BOT_ID), amount),
                    )
            UKPenceManager._finish_change(change, bankrupt=True)
            return True
        except sqlite3.Error as e:
            logger.error(f"Error transferring {amount} UKP from {user_id} to bank: {e}")
            return False

    @staticmethod
    def collect_tax_batch(charges, description: str = "Tax collection",
                          *, bank_description: str = None,
                          record_public_log: bool = True):
        """Atomically collect a planned batch of user taxes into the bank.

        ``charges`` is an iterable of ``(user_id, planned_amount)`` pairs. Plans
        for the same user are combined, balances are re-read under the database
        lock, and each charge is clamped to the user's current balance. The bank
        is credited once with the actual total and records it as tax revenue.

        Returns ``[(user_id, actual_amount, new_balance), ...]`` after a commit,
        ``[]`` when nobody is chargeable, or ``None`` after a SQLite failure.
        """
        from config import BOT_ID
        planned = {}
        try:
            for user_id, amount in charges:
                if user_id is None or isinstance(amount, bool) or not isinstance(amount, int):
                    return None
                if amount <= 0:
                    continue
                uid = str(user_id)
                if uid == str(BOT_ID):
                    continue
                planned[uid] = planned.get(uid, 0) + amount
        except (TypeError, ValueError):
            return None
        if not planned:
            return []

        now = int(time.time())
        changes = []
        charged_rows = []
        try:
            from lib.economy.economy_manager import UKPenceManager
            with DatabaseManager.locked_connection() as conn:
                cursor = conn.cursor()
                for user_id, planned_amount in planned.items():
                    balance = UKPenceManager._get_balance_in_transaction(
                        cursor, user_id,
                    )
                    actual_amount = min(planned_amount, max(0, int(balance)))
                    if actual_amount <= 0:
                        continue
                    change = UKPenceManager._remove_amount_in_transaction(
                        cursor, user_id, actual_amount, description, now,
                        force_history=True,
                        record_public_log=record_public_log,
                    )
                    if change is None:
                        raise sqlite3.IntegrityError(
                            f"tax balance changed unexpectedly for user {user_id}"
                        )
                    changes.append(change)
                    charged_rows.append(
                        (user_id, actual_amount, change[2])
                    )

                if charged_rows:
                    total = sum(row[1] for row in charged_rows)
                    BankManager._deposit_in_transaction(
                        cursor,
                        total,
                        bank_description if bank_description is not None else description,
                        now,
                        tax_deposit=True,
                    )
            for change in changes:
                UKPenceManager._finish_change(change)
            return charged_rows
        except sqlite3.Error as e:
            logger.error(f"Error collecting tax batch: {e}")
            return None

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
                "total_penalty_in, total_penalty_out, "
                "total_chest_in, total_chest_out, "
                "total_blockade_in, total_blockade_out, "
                "total_darts_in, total_darts_out, "
                "total_glass_in, total_glass_out")
        result = DatabaseManager.fetch_one(f"SELECT {cols} FROM bank WHERE id = 1")
        if result:
            (tax, bj_in, bj_out, hl_in, hl_out, sl_in, sl_out,
             vp_in, vp_out, rd_in, rd_out, tcp_in, tcp_out, ro_in, ro_out,
             mi_in, mi_out, pen_in, pen_out, ch_in, ch_out, bl_in, bl_out, da_in, da_out,
             gl_in, gl_out) = result
        else:
            tax = bj_in = bj_out = hl_in = hl_out = sl_in = sl_out = 0
            vp_in = vp_out = rd_in = rd_out = tcp_in = tcp_out = ro_in = ro_out = 0
            mi_in = mi_out = pen_in = pen_out = ch_in = ch_out = bl_in = bl_out = 0
            da_in = da_out = gl_in = gl_out = 0

        casino_in = (bj_in + hl_in + sl_in + vp_in + rd_in + tcp_in + ro_in + mi_in
                     + pen_in + ch_in + bl_in + da_in + gl_in)
        casino_out = (bj_out + hl_out + sl_out + vp_out + rd_out + tcp_out + ro_out + mi_out
                      + pen_out + ch_out + bl_out + da_out + gl_out)
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
            "chest_in": ch_in, "chest_out": ch_out, "chest_net": ch_in - ch_out,
            "blockade_in": bl_in, "blockade_out": bl_out, "blockade_net": bl_in - bl_out,
            "darts_in": da_in, "darts_out": da_out, "darts_net": da_in - da_out,
            "glass_in": gl_in, "glass_out": gl_out, "glass_net": gl_in - gl_out,
            "casino_in": casino_in, "casino_out": casino_out, "casino_net": casino_in - casino_out,
        }

    @staticmethod
    def set_balance(amount: float, description: str = "Administrative adjustment") -> bool:
        if amount < 0:
            logger.warning(f"Attempted to set bank balance to negative: {amount}")
            return False

        now = int(time.time())
        try:
            with DatabaseManager.locked_connection() as conn:
                cursor = conn.cursor()
                old_balance = BankManager._bot_balance_in_transaction(
                    cursor, required=False,
                )
                BankManager._set_bot_balance_in_transaction(cursor, amount)
                cursor.execute('''
                    UPDATE bank
                    SET balance = ?, last_updated = ?
                    WHERE id = 1
                ''', (amount, now))
                BankManager._require_bank_row(cursor)
                log_text = (
                    f"⚖️ Bank balance set to `{amount:,}` UKP "
                    f"(was `{old_balance:,}`)|{description}"
                )
                cursor.execute(
                    "INSERT INTO economy_transactions (timestamp, log_text) VALUES (?, ?)",
                    (now, log_text),
                )
            logger.info(f"Bank balance reset to {amount} UKP")
            return True
        except sqlite3.Error as e:
            logger.error(f"Error resetting bank balance: {e}")
            return False
