"""Restart-safe escrow for poker chips.

Buying in moves UKPence to the bank and gives the player chips at a table (1 chip = 1 UKP).
Because tables live in memory, a restart would otherwise strand those chips in the bank. So
we checkpoint each table's current stacks to disk (after every hand, and on join/leave); on
startup we refund whatever's recorded and clear it - which also voids any in-flight hand by
handing every seat back its pre-hand stack.
"""

import logging

import config
from lib.core.file_operations import load_json_file, save_json_file
from lib.economy.economy_manager import credit_from_bank

log = logging.getLogger(__name__)


def _load():
    return load_json_file(config.POKER_ESCROW_FILE) or {}


def checkpoint(channel_id, stacks):
    """Record the table's current {user_id: stack} so a restart can refund it."""
    store = _load()
    store[str(channel_id)] = {str(uid): int(amt) for uid, amt in stacks.items() if amt > 0}
    if not store[str(channel_id)]:
        store.pop(str(channel_id), None)
    save_json_file(config.POKER_ESCROW_FILE, store)


def clear_table(channel_id):
    store = _load()
    if store.pop(str(channel_id), None) is not None:
        save_json_file(config.POKER_ESCROW_FILE, store)


def refund_all():
    """Refund every escrowed stack back to UKPence and clear the store. Call once on startup.
    Returns (players_refunded, total_chips)."""
    store = _load()
    players = total = 0
    for _cid, stacks in store.items():
        for uid, amt in stacks.items():
            try:
                if amt > 0 and credit_from_bank(int(uid), int(amt), reason="Poker table closed (restart refund)"):
                    players += 1
                    total += int(amt)
            except Exception:
                log.error("poker refund failed for %s", uid, exc_info=True)
    if store:
        save_json_file(config.POKER_ESCROW_FILE, {})
    if players:
        log.info("Poker: refunded %s chips to %s players on startup.", total, players)
    return players, total
