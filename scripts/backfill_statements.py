"""Backfill the /balance statement ledger (user_transactions) from durable history.

The live ledger only fills going forward, but three tables survive the economy_transactions
drain and let us reconstruct the bulk of past activity accurately:

  - casino_results  -> per-round net (one Casino row each; the statement aggregates per day)
  - pay_transfers   -> both legs of every /pay, with counterparty
  - shop_purchases  -> shop debits

Rewards/tax/benefits/etc. were never stored per-user, so they aren't itemised here - the
statement derives them as a single reconciling "Rewards & other (net)" line per month from
the exact end-of-day balance snapshots, so every backfilled month still balances.

Backfilled rows are tagged source='backfill'. Only events BEFORE the live ledger began are
backfilled (so live and backfill never double-count). Idempotent: re-running clears prior
backfill rows first.

Usage (run from the repo root, on the box with database.db):
    python3 scripts/backfill_statements.py --dry-run   # report only, write nothing
    python3 scripts/backfill_statements.py             # perform the backfill
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import BOT_ID
from database import DatabaseManager


def _cutoff():
    """The instant the live ledger began; backfill only covers events strictly before it."""
    row = DatabaseManager.fetch_one(
        "SELECT MIN(ts) FROM user_transactions WHERE source = 'live'")
    if row and row[0]:
        return int(row[0])
    return int(time.time())


def build_rows(cutoff):
    """Return a list of (user_id, ts, amount, balance_after, reason, counterparty_id) to insert."""
    bot = str(BOT_ID)
    rows = []

    # Casino: one row per finished round (net = payout - staked). Pushes (net 0) are skipped.
    casino = DatabaseManager.fetch_all(
        "SELECT user_id, game, net, timestamp FROM casino_results WHERE timestamp < ? AND net != 0",
        (cutoff,)) or []
    for user_id, game, net, ts in casino:
        if str(user_id) == bot:
            continue
        rows.append((str(user_id), int(ts), int(net), 0, str(game or "Casino"), None))

    # Pay: both legs, with counterparty. Skip any leg that is the bank itself.
    pays = DatabaseManager.fetch_all(
        "SELECT timestamp, payer_id, recipient_id, amount FROM pay_transfers WHERE timestamp < ?",
        (cutoff,)) or []
    for ts, payer, recipient, amount in pays:
        amount = int(amount)
        if str(payer) != bot:
            rows.append((str(payer), int(ts), -amount, 0, "Pay", str(recipient)))
        if str(recipient) != bot:
            rows.append((str(recipient), int(ts), amount, 0, "Pay", str(payer)))

    # Shop: a debit of the price paid.
    shop = DatabaseManager.fetch_all(
        "SELECT user_id, item_id, price_paid, purchase_time FROM shop_purchases WHERE purchase_time < ?",
        (cutoff,)) or []
    for user_id, item_id, price, ts in shop:
        if str(user_id) == bot or not price:
            continue
        rows.append((str(user_id), int(ts), -int(price), 0, f"Shop: {item_id}", None))

    return rows


def main():
    dry = "--dry-run" in sys.argv
    cutoff = _cutoff()
    rows = build_rows(cutoff)

    existing = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM user_transactions WHERE source = 'backfill'")
    existing = existing[0] if existing else 0

    casino_n = sum(1 for r in rows if r[5] is None and r[4] != "Shop" and not r[4].startswith("Shop:"))
    pay_n = sum(1 for r in rows if r[5] is not None)
    shop_n = sum(1 for r in rows if r[4].startswith("Shop:"))

    print(f"Cutoff (live ledger start): {cutoff} ({time.strftime('%Y-%m-%d %H:%M', time.localtime(cutoff))})")
    print(f"Existing backfill rows (will be replaced): {existing}")
    print(f"Reconstructed rows to insert: {len(rows)}  "
          f"(casino {casino_n}, pay {pay_n}, shop {shop_n})")

    if dry:
        print("\n--dry-run: nothing written.")
        return

    DatabaseManager.execute("DELETE FROM user_transactions WHERE source = 'backfill'")
    inserted = 0
    for user_id, ts, amount, bal, reason, cp in rows:
        DatabaseManager.execute(
            "INSERT INTO user_transactions "
            "(user_id, ts, amount, balance_after, reason, counterparty_id, source) "
            "VALUES (?, ?, ?, ?, ?, ?, 'backfill')",
            (user_id, ts, amount, bal, str(reason)[:200], cp))
        inserted += 1
    print(f"\nDone. Inserted {inserted} backfill rows.")


if __name__ == "__main__":
    main()
