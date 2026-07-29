"""Bank reserve policy: the throttle, the mint ceiling and the demurrage dividend pot.

Three related jobs, kept in one module because they all answer the same question - how much
of the bank is the server allowed to give away right now.

**The throttle.** Passive rewards (chat, tree, welcome, bump, HoF, stage, benefits, boosters)
are paid out of the bank; taxes are what refill it. When reserves fall the discretionary half
scales down so the tax flow can catch up. What is NOT scaled: casino wins, refunds, bond
maturities, prediction settlements - anything already promised. A won bet is a debt, not a
gift, and quietly paying 75% of it would be worse than any reserve problem.

**The mint ceiling.** Casino insolvency mints rather than robbing a legitimate winner
(``credit_casino_payout``). MAX_TOTAL_SUPPLY caps how far that can ever go. Past the ceiling
the mint refuses and logs CRITICAL - at that point the currency is in enough trouble that
silently inflating it further is the worse option.

**The dividend pot.** An earmark, not a wallet. Demurrage keeps flowing into the bank exactly
as before; this only records how much of that balance is spoken for by chat rewards. No UKP is
created or destroyed by the pot, so the fixed-supply invariant cannot drift no matter what the
counter says. Empty pot means chat rewards stop until the next demurrage run refills it.

Everything here is read-mostly and fails OPEN (no throttling, rewards paid in full) if the
database misbehaves: a broken reserve read must never silently stop paying the server.
"""

import logging

logger = logging.getLogger(__name__)


def _cfg(name, default):
    import config
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# Reserves + the discretionary throttle
# ---------------------------------------------------------------------------
def bank_reserves() -> int:
    from lib.economy.bank_manager import BankManager
    return int(BankManager.get_balance() or 0)


def total_supply() -> int:
    """Every UKP in existence, the bank's own float included."""
    from database import DatabaseManager
    row = DatabaseManager.fetch_one("SELECT SUM(balance) FROM ukpence")
    return int(row[0]) if row and row[0] is not None else 0


def throttle_multiplier(reserves: int = None) -> float:
    """How much of a discretionary reward the bank will currently pay (0.25 - 1.0)."""
    if not _cfg("RESERVE_POLICY_ENABLED", True):
        return 1.0
    try:
        if reserves is None:
            reserves = bank_reserves()
        for ceiling, mult in _cfg("RESERVE_THROTTLE_TIERS", ()):
            if reserves <= ceiling:
                return float(mult)
    except Exception:
        logger.error("reserve throttle read failed; paying in full", exc_info=True)
    return 1.0


def scale_reward(amount: int, reserves: int = None) -> int:
    """Shrink a discretionary reward to what reserves can currently afford.

    Never rounds a real reward away to nothing: while the bank holds anything at all, a
    payout that was going to happen still pays at least 1 UKP. Silently paying zero would
    read as a broken feature rather than a tightened belt.
    """
    if amount <= 0:
        return amount
    mult = throttle_multiplier(reserves)
    if mult >= 1.0:
        return amount
    return max(1, int(amount * mult))


def reserve_state() -> dict:
    """Everything the /bank-status tax panel needs, in one read."""
    try:
        reserves, supply = bank_reserves(), total_supply()
    except Exception:
        logger.error("reserve_state read failed", exc_info=True)
        return {}
    floor = int(_cfg("RESERVE_FLOOR", 80_000))
    cap = int(_cfg("MAX_TOTAL_SUPPLY", 1_000_000))
    return {
        "reserves": reserves,
        "supply": supply,
        "circulating": supply - reserves,
        "pct_of_supply": (100.0 * reserves / supply) if supply else 0.0,
        "floor": floor,
        "above_floor": reserves - floor,
        "multiplier": throttle_multiplier(reserves),
        "throttled": throttle_multiplier(reserves) < 1.0,
        "supply_cap": cap,
        "mint_headroom": max(0, cap - supply),
    }


# ---------------------------------------------------------------------------
# The mint ceiling
# ---------------------------------------------------------------------------
def mint_headroom() -> int:
    """UKP the emergency mint may still create before hitting MAX_TOTAL_SUPPLY."""
    try:
        return max(0, int(_cfg("MAX_TOTAL_SUPPLY", 1_000_000)) - total_supply())
    except Exception:
        logger.error("mint headroom read failed; refusing to mint", exc_info=True)
        return 0        # fails CLOSED: an unreadable supply must not authorise minting


def may_mint(amount: int) -> bool:
    return amount > 0 and mint_headroom() >= amount


# ---------------------------------------------------------------------------
# The demurrage dividend pot (an earmark on the bank's balance)
# ---------------------------------------------------------------------------
def dividend_pot() -> int:
    from database import DatabaseManager
    try:
        row = DatabaseManager.fetch_one("SELECT chat_reward_pot FROM bank WHERE id = 1")
        return max(0, int(row[0])) if row and row[0] is not None else 0
    except Exception:
        logger.error("dividend pot read failed", exc_info=True)
        return 0


def fund_dividend_pot(reclaimed: int) -> int:
    """Earmark a share of a demurrage run for chat rewards. Returns the amount added.

    The UKP itself already sits in the bank (demurrage deposited it); this only moves the
    accounting marker, so it can never affect total supply.
    """
    if reclaimed <= 0 or not _cfg("DEMURRAGE_DIVIDEND_ENABLED", True):
        return 0
    share = int(reclaimed * float(_cfg("DEMURRAGE_DIVIDEND_PCT", 0.50)))
    if share <= 0:
        return 0
    from database import DatabaseManager
    try:
        DatabaseManager.execute(
            "UPDATE bank SET chat_reward_pot = MAX(0, COALESCE(chat_reward_pot, 0) + ?) WHERE id = 1",
            (share,))
        logger.info("[ECONOMY] Dividend pot +%s from demurrage (now %s).", share, dividend_pot())
        return share
    except Exception:
        logger.error("dividend pot credit failed", exc_info=True)
        return 0


def dividend_rate() -> float:
    """Multiplier on chat-reward frequency, from how full the pot is (0.0 when empty).

    Returns 1.0 when the dividend is switched off entirely, so chat rewards behave exactly
    as they did before the pot existed rather than silently stopping.
    """
    if not _cfg("DEMURRAGE_DIVIDEND_ENABLED", True):
        return 1.0
    pot = dividend_pot()
    if pot <= 0:
        return 0.0
    full = max(1, int(_cfg("DEMURRAGE_DIVIDEND_FULL_POT", 2_500)))
    floor = float(_cfg("DEMURRAGE_DIVIDEND_MIN_RATE", 0.25))
    return max(floor, min(1.0, pot / full))


def spend_dividend(amount: int) -> bool:
    """Draw a paid chat reward down from the pot. False if it can't cover it.

    Conditional in SQL so two concurrent rewards can't both pass a read-then-write check
    and overdraw the earmark.
    """
    if amount <= 0:
        return False
    if not _cfg("DEMURRAGE_DIVIDEND_ENABLED", True):
        return True                     # pot disabled: rewards aren't gated on it
    from database import DatabaseManager
    try:
        changed = DatabaseManager.execute(
            "UPDATE bank SET chat_reward_pot = chat_reward_pot - ? "
            "WHERE id = 1 AND chat_reward_pot >= ?", (amount, amount))
        return bool(changed)
    except Exception:
        logger.error("dividend pot debit failed", exc_info=True)
        return False
