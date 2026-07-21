"""County Balls engine - spawn pacing, catch/ownership state, UKPence sales.

No Discord imports here: everything message-shaped lives in views.py. State:
  - county_state.json: the qualifying-message counter, current spawn target and
    the active (uncatchable-once-caught) spawn, restart-safe.
  - county_instances / county_transfers tables: who owns what, and an audit
    trail for gifts (same anti-alt-shuffle rationale as pay_transfers).
"""

import logging
import random
import time

import config
from config import COUNTY_SELL_PRICES, COUNTY_SPAWN_WEIGHTS, COUNTY_STATE_FILE
from database import DatabaseManager
from lib.core.file_operations import load_json_file, save_json_file
from lib.features.counties.data import COUNTIES

logger = logging.getLogger(__name__)

_state_cache = None


def _state() -> dict:
    global _state_cache
    if _state_cache is None:
        _state_cache = load_json_file(COUNTY_STATE_FILE) or {}
        _state_cache.setdefault("counter", 0)
        _state_cache.setdefault("target", 0)
        _state_cache.setdefault("last_spawn_ts", 0)
        _state_cache.setdefault("last_author", 0)
        _state_cache.setdefault("active", None)
    return _state_cache


def _save() -> None:
    save_json_file(COUNTY_STATE_FILE, _state())


# ---------------------------------------------------------------------------
# Spawn pacing
# ---------------------------------------------------------------------------
def channel_eligible(channel) -> bool:
    if getattr(channel, "id", None) in config.COUNTY_SPAWN_CHANNELS:
        return True
    category = getattr(channel, "category_id", None)
    return bool(config.COUNTY_SPAWN_CATEGORY) and category == config.COUNTY_SPAWN_CATEGORY


def note_message(author_id: int, content: str) -> bool:
    """Count a qualifying chat message; True when a spawn is due right now.

    Qualifying = 4+ characters and a different author than the previous counted
    message (back-and-forth chat fills the bar; solo spam does not).
    """
    st = _state()
    if not st["target"]:
        st["target"] = random.randint(*config.COUNTY_SPAWN_RANGE)
    if len(content) < 4 or author_id == st["last_author"]:
        return False
    st["counter"] += 1
    st["last_author"] = author_id
    due = (
        st["counter"] >= st["target"]
        and time.time() - st["last_spawn_ts"] >= config.COUNTY_SPAWN_MIN_GAP
    )
    if not due:
        # persist sparingly - every few messages is plenty for restart recovery
        if st["counter"] % 5 == 0:
            _save()
    return due


def pick_county() -> str:
    keys = list(COUNTIES)
    weights = [COUNTY_SPAWN_WEIGHTS[COUNTIES[k].tier] for k in keys]
    return random.choices(keys, weights=weights, k=1)[0]


def begin_spawn(county_key: str, message_id: int, channel_id: int) -> None:
    st = _state()
    st["counter"] = 0
    st["target"] = random.randint(*config.COUNTY_SPAWN_RANGE)
    st["last_spawn_ts"] = time.time()
    st["active"] = {
        "county": county_key,
        "message_id": message_id,
        "channel_id": channel_id,
        "wrong_guesses": 0,
        "hinted": False,
    }
    _save()


def active_spawn() -> dict | None:
    return _state()["active"]


def clear_active() -> dict | None:
    st = _state()
    old, st["active"] = st["active"], None
    _save()
    return old


def note_wrong_guess() -> int:
    """Bump the wrong-guess count on the active spawn; returns the new count."""
    st = _state()
    if not st["active"]:
        return 0
    st["active"]["wrong_guesses"] += 1
    _save()
    return st["active"]["wrong_guesses"]


def mark_hinted() -> None:
    st = _state()
    if st["active"]:
        st["active"]["hinted"] = True
        _save()


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------
def record_catch(user_id: int, county_key: str, channel_id: int) -> tuple[bool, int, int, int]:
    """Insert an instance for the catcher, rolling its stat bonuses.

    Returns (first_of_this_county, owned_count, clout_bonus, grit_bonus)."""
    r = getattr(config, "COUNTY_STAT_BONUS_RANGE", 20)
    clout_b = random.randint(-r, r)
    grit_b = random.randint(-r, r)
    with DatabaseManager.locked_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM county_instances WHERE user_id = ? AND county = ?",
            (str(user_id), county_key),
        )
        owned = c.fetchone()[0]
        c.execute(
            "INSERT INTO county_instances (user_id, county, caught_at, channel_id, obtained, "
            "clout_bonus, grit_bonus) VALUES (?, ?, ?, ?, 'catch', ?, ?)",
            (str(user_id), county_key, int(time.time()), str(channel_id), clout_b, grit_b),
        )
    return owned == 0, owned + 1, clout_b, grit_b


def best_instance(user_id: int, county_key: str):
    """(clout_bonus, grit_bonus) of the user's highest-rolled copy, or None."""
    row = DatabaseManager.fetch_one(
        "SELECT clout_bonus, grit_bonus FROM county_instances "
        "WHERE user_id = ? AND county = ? ORDER BY (clout_bonus + grit_bonus) DESC LIMIT 1",
        (str(user_id), county_key),
    )
    return (row[0], row[1]) if row else None


def collection(user_id: int) -> dict:
    """county key -> count owned by this user."""
    rows = DatabaseManager.fetch_all(
        "SELECT county, COUNT(*) FROM county_instances WHERE user_id = ? GROUP BY county",
        (str(user_id),),
    )
    return {county: n for county, n in rows}


def owned_count(user_id: int, county_key: str) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM county_instances WHERE user_id = ? AND county = ?",
        (str(user_id), county_key),
    )
    return row[0] if row else 0


def server_caught_count(county_key: str) -> int:
    row = DatabaseManager.fetch_one(
        "SELECT COUNT(*) FROM county_instances WHERE county = ?", (county_key,)
    )
    return row[0] if row else 0


def transfer_one(from_user: int, to_user: int, county_key: str) -> bool:
    """Gift the giver's oldest instance of a county. Logged for audit."""
    with DatabaseManager.locked_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM county_instances WHERE user_id = ? AND county = ? "
            "ORDER BY caught_at ASC LIMIT 1",
            (str(from_user), county_key),
        )
        row = c.fetchone()
        if not row:
            return False
        c.execute(
            "UPDATE county_instances SET user_id = ?, obtained = 'give' WHERE id = ?",
            (str(to_user), row[0]),
        )
        c.execute(
            "INSERT INTO county_transfers (instance_id, from_user, to_user, transferred_at) "
            "VALUES (?, ?, ?, ?)",
            (row[0], str(from_user), str(to_user), int(time.time())),
        )
    return True


def sell(user_id: int, county_key: str, quantity: int) -> int | None:
    """Sell instances back to the bank. Returns UKP paid, or None on failure.

    Rows are deleted first (inside the DB lock), then the bank pays; if the bank
    can't cover it the rows are restored and the sale is off.
    """
    tier = COUNTIES[county_key].tier
    price = COUNTY_SELL_PRICES[tier]
    with DatabaseManager.locked_connection() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id, caught_at, channel_id, obtained, clout_bonus, grit_bonus "
            "FROM county_instances WHERE user_id = ? AND county = ? "
            "ORDER BY (clout_bonus + grit_bonus) ASC, caught_at DESC LIMIT ?",
            (str(user_id), county_key, quantity),
        )
        rows = c.fetchall()
        if len(rows) < quantity:
            return None
        c.execute(
            f"DELETE FROM county_instances WHERE id IN ({','.join('?' * len(rows))})",
            [r[0] for r in rows],
        )
    total = price * quantity
    from lib.economy.economy_manager import add_bb
    if not add_bb(user_id, total,
                  reason=f"Sold {quantity}x {COUNTIES[county_key].name} county ball"):
        with DatabaseManager.locked_connection() as conn:
            c = conn.cursor()
            for _id, caught_at, channel_id, obtained, clout_b, grit_b in rows:
                c.execute(
                    "INSERT INTO county_instances (id, user_id, county, caught_at, channel_id, "
                    "obtained, clout_bonus, grit_bonus) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (_id, str(user_id), county_key, caught_at, channel_id, obtained,
                     clout_b, grit_b),
                )
        logger.warning("County sale refunded - bank could not cover %s UKP", total)
        return None
    return total
