"""Graded economy restrictions and the audit trail behind them.

A flag used to be one blunt thing: a row in `detection_flags` that blocked `/pay` and
nothing else, while the review panel told staff the member was "restricted from economy
commands". They were not - they could still gamble, wager, claim dailies and buy from the
shop with money the detector had just decided was probably stolen.

This module replaces that with three graded tiers and, crucially, one place that knows what
each of them actually blocks. Every tier is defined as a set of command names, the gate in
`setup_commands` reads those sets, and the `/flags` panel renders the same sets back to
staff - so what a reviewer is told matches what the bot enforces, because both come from
this dict rather than from someone's memory of what the flag used to do.

Tiers are ordered, and a member holding several rows resolves to the strictest. That keeps
an older `pay_only` from silently weakening a later `full`.

Nothing here raises. A restriction lookup runs on the interaction path of every single
command, so a failed database read must let the command through rather than take the bot
down with it; a detector that cannot read its own state is a bug to fix, not a reason to
stop the server playing blackjack.
"""

import logging
import time

from database import DatabaseManager

log = logging.getLogger(__name__)

# --- what each area is, in command names as registered on the tree ------------------
_PAY = {"pay"}
_CASINO = {
    "blackjack", "blockade", "casino", "chest", "darts", "higher-lower", "mines",
    "poker", "red-dog", "roulette", "slots", "three-card-poker", "video-poker",
    "penalty",
}
_PVP = {"battleship", "connect4", "wager", "bets"}
_MARKET = {"shop", "bond", "lottery"}
# Handouts straight from the bank. Someone under review for moving money around
# should not still be drawing a daily from the treasury while it is looked at.
_PAYOUTS = {"benefits"}
_DAILIES = {"wordle", "crossword"}
_GAMES = {"skyrim", "county-give", "county-sell", "county-spawn"}

TIERS: dict[str, dict] = {
    "pay_only": {
        "rank": 1,
        "label": "Pay only",
        "blocks": frozenset(_PAY),
        "summary": "sending UKPence with /pay",
        "note": "Can still gamble, wager, use the shop and claim dailies.",
    },
    "economy": {
        "rank": 2,
        "label": "Economy",
        "blocks": frozenset(_PAY | _CASINO | _PVP | _MARKET | _PAYOUTS | _DAILIES),
        "summary": "/pay, all casino games, wagers and PvP, the shop, bonds, "
                   "the lottery, benefits, and daily puzzles",
        "note": "Can still chat, rank up and play Skyrim.",
    },
    "full": {
        "rank": 3,
        "label": "Full",
        "blocks": frozenset(_PAY | _CASINO | _PVP | _MARKET | _PAYOUTS | _DAILIES | _GAMES),
        "summary": "every economy command, plus Skyrim and counties",
        "note": "Chat, rank and XP are untouched - this is not a mute.",
    },
}

# Rows written before tiers existed. Treated as the tier they actually behaved like,
# so nobody's restriction silently changes meaning on deploy.
LEGACY_FLAGS = {"flagged_alt": "pay_only"}

DEFAULT_TIER = "pay_only"


def tier_label(tier: str) -> str:
    return TIERS.get(tier, {}).get("label", tier)


def blocks(tier: str) -> frozenset:
    return TIERS.get(tier, {}).get("blocks", frozenset())


def summary(tier: str) -> str:
    """One line naming what the member cannot do, for the panel and the refusal message."""
    return TIERS.get(tier, {}).get("summary", "some economy commands")


def footnote(tier: str) -> str:
    """What they can still do - the half reviewers forget when judging severity."""
    return TIERS.get(tier, {}).get("note", "")


def tier_of(user_id) -> str | None:
    """The strictest tier held by this member, or None."""
    try:
        rows = DatabaseManager.fetch_all(
            "SELECT flag FROM detection_flags WHERE user_id = ?", (str(user_id),)) or []
    except Exception:
        log.exception("restriction lookup failed for %s", user_id)
        return None
    best, best_rank = None, 0
    for (flag,) in rows:
        tier = flag if flag in TIERS else LEGACY_FLAGS.get(flag)
        rank = TIERS.get(tier, {}).get("rank", 0)
        if tier and rank > best_rank:
            best, best_rank = tier, rank
    return best


def is_blocked(user_id, command_name: str) -> str | None:
    """The tier stopping this command, or None to let it through.

    Deliberately fails open: see the module docstring.
    """
    try:
        tier = tier_of(user_id)
        if tier and command_name in blocks(tier):
            return tier
    except Exception:
        log.exception("restriction check failed for %s on /%s", user_id, command_name)
    return None


def refusal_message(tier: str) -> str:
    line = f"Your account is under review, so you can't use {summary(tier)} right now."
    tail = footnote(tier)
    return f"{line} Speak to a member of staff.\n-# {tail}" if tail else f"{line} Speak to a member of staff."


# --- mutation, always audited ------------------------------------------------------
def _log(user_id, action: str, tier: str | None, by_id, note: str = "") -> None:
    try:
        DatabaseManager.execute(
            "INSERT INTO restriction_log (ts, user_id, action, tier, by_id, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(time.time()), str(user_id), action, tier or "", str(by_id) if by_id else None,
             note[:300]),
        )
    except Exception:
        # An unwritable audit row must not stop the restriction itself landing.
        log.exception("could not write restriction audit row for %s", user_id)


def apply(user_id, tier: str, by_id=None, note: str = "") -> str:
    """Put a member on a tier, replacing any tier they already held."""
    if tier not in TIERS:
        tier = DEFAULT_TIER
    DatabaseManager.execute(
        "DELETE FROM detection_flags WHERE user_id = ?", (str(user_id),))
    DatabaseManager.execute(
        "INSERT INTO detection_flags (user_id, flag, ts, by_id, note) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, flag) DO UPDATE SET ts = excluded.ts, by_id = excluded.by_id, "
        "note = excluded.note",
        (str(user_id), tier, int(time.time()), str(by_id) if by_id else None, note),
    )
    _log(user_id, "applied", tier, by_id, note)
    return tier


def lift(user_id, by_id=None, note: str = "") -> None:
    """Remove every restriction row for a member."""
    previous = tier_of(user_id)
    DatabaseManager.execute("DELETE FROM detection_flags WHERE user_id = ?", (str(user_id),))
    _log(user_id, "lifted", previous, by_id, note)


def history(user_id=None, limit: int = 20) -> list:
    """[(ts, user_id, action, tier, by_id, note), ...], newest first."""
    try:
        if user_id is None:
            return DatabaseManager.fetch_all(
                "SELECT ts, user_id, action, tier, by_id, note FROM restriction_log "
                "ORDER BY ts DESC LIMIT ?", (int(limit),)) or []
        return DatabaseManager.fetch_all(
            "SELECT ts, user_id, action, tier, by_id, note FROM restriction_log "
            "WHERE user_id = ? ORDER BY ts DESC LIMIT ?", (str(user_id), int(limit))) or []
    except Exception:
        log.exception("could not read restriction history")
        return []


def restricted_members() -> list:
    """[(user_id, tier, ts, note), ...] for everyone currently restricted, newest first."""
    rows = DatabaseManager.fetch_all(
        "SELECT user_id, flag, ts, note FROM detection_flags ORDER BY ts DESC") or []
    out = []
    for uid, flag, ts, note in rows:
        tier = flag if flag in TIERS else LEGACY_FLAGS.get(flag)
        if tier:
            out.append((uid, tier, ts, note))
    return out
