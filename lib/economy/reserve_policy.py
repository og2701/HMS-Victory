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

**Pot top-up of throttled rewards.** While the throttle is on, the part of a discretionary
reward it holds back is covered from the pot instead (``scale_reward_with_pot``), so the
player still gets 100% - or as much as the pot can spare above THROTTLE_POT_TOPUP_FLOOR,
which is kept back so chat rewards don't stop. The bank still pays the whole amount; the pot
counter just drops by the covered part. ``scale_reward`` stays pure (display-safe);
``estimate_reward_with_pot`` previews a top-up without debiting anything.

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
def bank_balance() -> int:
    """The bank's whole balance, including bond principal it is holding for savers."""
    from lib.economy.bank_manager import BankManager
    return int(BankManager.get_balance() or 0)


def locked_bond_principal() -> int:
    """Principal locked in active bonds. Fails open (0) so a bad read never throttles."""
    from database import DatabaseManager
    try:
        row = DatabaseManager.fetch_one(
            "SELECT COALESCE(SUM(principal), 0) FROM bonds WHERE status = 'active'")
        return max(0, int(row[0])) if row and row[0] is not None else 0
    except Exception:
        logger.error("locked bond principal read failed; treating as 0", exc_info=True)
        return 0


def bank_reserves() -> int:
    """FREE reserves: the bank's balance minus the bond principal it owes back to savers.

    Savers' locked principal sits in the bank's balance, but it isn't the bank's to spend, so
    counting it would make the bank look healthier the more people bond."""
    return max(0, bank_balance() - locked_bond_principal())


def _casino_reserves() -> int:
    """What the casino's dynamic limits scale with (see CASINO_EXPOSURE_EXCLUDES_BONDS)."""
    if _cfg("CASINO_EXPOSURE_EXCLUDES_BONDS", False):
        return bank_reserves()
    return bank_balance()


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
    """Everything the bank report's tax panel needs, in one read."""
    try:
        balance, bonded, supply = bank_balance(), locked_bond_principal(), total_supply()
    except Exception:
        logger.error("reserve_state read failed", exc_info=True)
        return {}
    reserves = max(0, balance - bonded)
    floor = int(_cfg("RESERVE_FLOOR", 5_000))
    cap = int(_cfg("MAX_TOTAL_SUPPLY", 1_000_000))
    return {
        "reserves": reserves,
        "bank_balance": balance,
        "bonded": bonded,
        "supply": supply,
        "circulating": supply - balance,
        "pct_of_supply": (100.0 * reserves / supply) if supply else 0.0,
        "floor": floor,
        "above_floor": reserves - floor,
        "multiplier": throttle_multiplier(reserves),
        "throttled": throttle_multiplier(reserves) < 1.0,
        "supply_cap": cap,
        "mint_headroom": max(0, cap - supply),
    }


def _alert_level(reserves: int, previous) -> float:
    """The throttle level the ALERT should report, with hysteresis on the way back up.

    Tightening is immediate. Relaxing only counts once free reserves clear the tier's
    ceiling by RESERVE_ALERT_HYSTERESIS (so 60,500 doesn't flip a 60,000 boundary back and
    forth). The reward throttle itself is untouched by this."""
    now = throttle_multiplier(reserves)
    if previous is None or now <= previous:
        return now
    margin = int(_cfg("RESERVE_ALERT_HYSTERESIS", 2_000))
    relaxed = throttle_multiplier(max(0, reserves - margin))
    return relaxed if relaxed > previous else previous


def _level_text(mult: float) -> str:
    return "OFF (rewards paid in full)" if mult >= 1.0 else f"ON, rewards paying {mult:.0%}"


def check_throttle_alert(state: dict, reserves: int, balance: int, bonded: int):
    """Decide whether the throttle level changed since the last announcement.

    `state` is the persisted dict ({"multiplier": float} or empty). Returns
    (new_state, message_or_None). The first ever call only seeds the state (None message), so
    shipping this never announces the current level as news. The caller persists new_state
    only after the message is sent, so a failed post is retried on the next tick."""
    if not _cfg("RESERVE_ALERT_ENABLED", True):
        return state, None
    previous = state.get("multiplier")
    level = _alert_level(reserves, previous)
    new_state = {"multiplier": level}
    if previous is None:
        return new_state, None
    if abs(level - previous) < 1e-9:
        return state, None
    if previous >= 1.0:
        kind = "Reserve throttle ENABLED"
    elif level >= 1.0:
        kind = "Reserve throttle DISABLED"
    else:
        kind = "Reserve throttle tier changed (" + ("tightened" if level < previous else "relaxed") + ")"
    msg = (f"{kind}\n"
           f"Was: {_level_text(previous)}\n"
           f"Now: {_level_text(level)}\n"
           f"Free reserves: {reserves:,} UKP (bank {balance:,} minus {bonded:,} bond principal)")
    return new_state, msg


def max_casino_net_payout(static_max_net: int = 250_000) -> int:
    """Calculate maximum net profit allowed on a single outcome (e.g. 80% of Bank balance)."""
    if not _cfg("DYNAMIC_MAX_BET_ENABLED", True):
        return static_max_net
    try:
        reserves = _casino_reserves()
        if reserves <= 0:
            return 500
        pct = float(_cfg("MAX_EXPOSURE_PCT", 0.80))
        return int(reserves * pct)
    except Exception:
        logger.error("max_casino_net_payout read failed", exc_info=True)
        return static_max_net


def max_casino_bet(game_max_multiplier: float = 3.0, static_max: int = 250_000) -> int:
    """Calculate dynamic max bet scaled to liquid Bank reserves and game risk exposure.

    Protects Bank reserves by limiting single-win max exposure to a percentage of Bank
    reserves (default 80% max single payout), scaled by game max multiplier.
    """
    if not _cfg("DYNAMIC_MAX_BET_ENABLED", True):
        return static_max
    try:
        reserves = _casino_reserves()
        if reserves <= 0:
            return 100
        pct = float(_cfg("MAX_EXPOSURE_PCT", 0.80))
        multiplier = max(1.0, float(game_max_multiplier))
        dynamic_cap = int((reserves * pct) / multiplier)
        floor_bet = int(_cfg("DYNAMIC_MIN_MAX_BET", 500))
        return max(floor_bet, min(static_max, dynamic_cap))
    except Exception:
        logger.error("max_casino_bet read failed; using static max", exc_info=True)
        return static_max


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


# ---------------------------------------------------------------------------
# Pot top-up: the dividend pot covers what the throttle holds back
# ---------------------------------------------------------------------------
def _topup_enabled() -> bool:
    return bool(_cfg("THROTTLE_POT_TOPUP_ENABLED", False)) and \
        bool(_cfg("DEMURRAGE_DIVIDEND_ENABLED", True))


def _topup_floor() -> int:
    return max(0, int(_cfg("THROTTLE_POT_TOPUP_FLOOR", 2_500)))


def _planned_cover(amount: int, throttled: int) -> int:
    """How much of the throttle's cut the pot could cover right now (reads only)."""
    shortfall = amount - throttled
    if shortfall <= 0:
        return 0
    return max(0, min(shortfall, dividend_pot() - _topup_floor()))


def estimate_reward_with_pot(amount: int, reserves: int = None) -> int:
    """What ``scale_reward_with_pot`` would pay right now, WITHOUT touching the pot.

    For display only. Never less than ``scale_reward``; any error falls back to it."""
    throttled = scale_reward(amount, reserves)
    if amount <= 0 or throttled >= amount or not _topup_enabled():
        return throttled
    try:
        return throttled + _planned_cover(amount, throttled)
    except Exception:
        logger.error("pot top-up estimate failed; showing throttled amount", exc_info=True)
        return throttled


def scale_reward_with_pot(amount: int, reserves: int = None) -> tuple:
    """The real-payout version of ``scale_reward``: throttle, then let the pot cover the cut.

    Returns ``(pay, cover)``: ``pay`` is what to give the player, ``cover`` is what was
    debited from the pot (0 if none). The debit is conditional in SQL so concurrent rewards
    can't take the pot below the floor; a lost race or any error falls back to the plain
    throttled amount, so this never pays less than ``scale_reward`` would. Call it exactly
    once per real payout, and ``refund_pot_cover(cover)`` if that payout then fails.
    """
    throttled = scale_reward(amount, reserves)
    if amount <= 0 or throttled >= amount or not _topup_enabled():
        return throttled, 0
    try:
        cover = _planned_cover(amount, throttled)
        if cover <= 0:
            return throttled, 0
        floor = _topup_floor()
        from database import DatabaseManager
        changed = DatabaseManager.execute(
            "UPDATE bank SET chat_reward_pot = chat_reward_pot - ? "
            "WHERE id = 1 AND chat_reward_pot - ? >= ?", (cover, cover, floor))
        if not changed:
            logger.info("[ECONOMY] Pot top-up lost a race (%s wanted); paying throttled %s of %s.",
                        cover, throttled, amount)
            return throttled, 0
        logger.info("[ECONOMY] Pot covered %s of a throttled reward: paying %s of %s "
                    "(throttle alone %s).", cover, throttled + cover, amount, throttled)
        return throttled + cover, cover
    except Exception:
        logger.error("pot top-up failed; paying throttled amount", exc_info=True)
        return throttled, 0


def refund_pot_cover(cover: int) -> None:
    """Put a top-up back in the pot when the payout it was meant for didn't happen."""
    if cover <= 0:
        return
    from database import DatabaseManager
    try:
        DatabaseManager.execute(
            "UPDATE bank SET chat_reward_pot = COALESCE(chat_reward_pot, 0) + ? WHERE id = 1",
            (cover,))
    except Exception:
        logger.error("pot top-up refund of %s failed", cover, exc_info=True)
