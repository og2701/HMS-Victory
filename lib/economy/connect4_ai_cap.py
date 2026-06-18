"""Daily net-profit cap on Connect 4 vs-AI winnings (anti-farming guard).

A user's Connect 4 vs-AI NET profit is capped at ``CONNECT4_AI_DAILY_WIN_CAP`` per UK day:

  - a win pays profit only up to the cap; at/over the cap a win just returns the stake.
  - a loss counts against the day's running total, freeing the budget back up.

So a player can still play unlimited games, but can't net more than the cap per day from beating
the AI - closing the "perfect AI is farmable" hole. Resets at UK midnight. Stored as a small
JSON map {user_id: {date, net}} alongside the other daily counters.
"""
import logging
import os
from datetime import datetime

import pytz

import config
from lib.core.file_operations import load_json_file, save_json_file

logger = logging.getLogger(__name__)
_UK = pytz.timezone("Europe/London")
_FILE = os.path.join(config.JSON_DATA_DIR, "connect4_ai_daily.json")


def _today() -> str:
    return datetime.now(_UK).strftime("%Y-%m-%d")


def _record_for(store: dict, user_id) -> dict:
    rec = store.get(str(user_id))
    if not isinstance(rec, dict) or rec.get("date") != _today():
        rec = {"date": _today(), "net": 0}    # new day -> reset
    return rec


def _cap() -> int:
    return int(getattr(config, "CONNECT4_AI_DAILY_WIN_CAP", 4000))


def win_profit(user_id, stake: int) -> int:
    """Profit to pay ON TOP of the returned stake for a vs-AI win, capped so the user's net daily
    profit can't exceed the cap; records it. Returns 0 once the cap is reached (a win then just
    returns the stake). Fails OPEN (pays full profit) if the counter can't be read/written, so a
    bug here never withholds a legitimate payout."""
    try:
        store = load_json_file(_FILE) or {}
        rec = _record_for(store, user_id)
        profit = min(int(stake), max(0, _cap() - rec["net"]))
        rec["net"] += profit
        store[str(user_id)] = rec
        save_json_file(_FILE, store)
        return profit
    except Exception:
        logger.error("connect4 AI daily-cap (win) failed; paying uncapped.", exc_info=True)
        return int(stake)


def record_loss(user_id, stake: int) -> None:
    """A vs-AI loss counts against the day's total, freeing the win budget back up."""
    try:
        store = load_json_file(_FILE) or {}
        rec = _record_for(store, user_id)
        rec["net"] -= int(stake)
        store[str(user_id)] = rec
        save_json_file(_FILE, store)
    except Exception:
        logger.error("connect4 AI daily-cap (loss) failed.", exc_info=True)
