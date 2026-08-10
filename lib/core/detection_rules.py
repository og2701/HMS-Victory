"""The detection rules themselves, kept apart from the plumbing in detection.py.

Every rule here is a pure decision over data already gathered: given these timings, these
transfers, these events, is this worth a human looking at? Nothing here touches Discord, so
each threshold can be tested directly rather than through a mocked interaction - which
matters, because a rule that fires on honest players is worse than no rule at all and the
edges are exactly where that happens.

The call sites are thin on purpose: a feature records what it saw and asks; the answer is a
flag or nothing.
"""

import logging
import time

import config
from database import DatabaseManager
from lib.core import detection as D

log = logging.getLogger(__name__)


def _cfg(name, default):
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# Module A - Wordle
# ---------------------------------------------------------------------------
def wordle_solve_findings(guess_times, opened_at, solved: bool, now=None) -> list:
    """[(kind, triggers), ...] for one finished Wordle.

    `guess_times` is the epoch second of each guess in order, `opened_at` when the board was
    first shown. Times can be missing on games started before this shipped, and a missing
    time is not evidence of anything, so anything unmeasurable is simply not judged.
    """
    if not solved or not guess_times:
        return []
    now = float(now if now is not None else time.time())
    out = []

    # Reading the grid and typing a word has a floor. Under it, the answer came from
    # somewhere other than the puzzle.
    if opened_at:
        elapsed = float(guess_times[-1]) - float(opened_at)
        floor = _cfg("WORDLE_MIN_SOLVE_SECONDS", 3.5)
        if 0 <= elapsed < floor:
            out.append((D.WORDLE_FAST_SOLVE, {
                "elapsed": f"{elapsed:.1f}s (floor {floor}s)",
                "guesses": len(guess_times),
            }))

    # The dodge for a consecutive-first-try penalty is to throw one word away and then enter
    # the answer. A human who has just guessed wrong needs time to think; someone typing a
    # known answer does not, so the gap between the two is the tell rather than the words.
    if len(guess_times) == 2:
        gap = float(guess_times[1]) - float(guess_times[0])
        limit = _cfg("WORDLE_DUMMY_GUESS_SECONDS", 4.0)
        if 0 <= gap < limit:
            out.append((D.WORDLE_DUMMY_GUESS, {
                "gap between guess 1 and the winning guess": f"{gap:.1f}s (floor {limit}s)",
            }))
    return out


def wordle_one_guess_rate(user_id, now=None) -> dict | None:
    """Triggers if first-try solves inside the window exceed what luck explains.

    A five-letter answer from a list of thousands is not guessed first time twice in a
    fortnight by chance, so the count is the whole rule - no timing needed, which makes this
    the one Wordle check that still works on a player who paces themselves.
    """
    days = _cfg("WORDLE_ONE_GUESS_WINDOW_DAYS", 14)
    limit = _cfg("WORDLE_ONE_GUESS_MAX", 2)
    events = D.recent_events(user_id, D.WORDLE_ONE_GUESS_STREAK, days * 86400, now=now)
    if len(events) <= limit:
        return None
    return {
        "first-try solves": f"{len(events)} in {days} days (allowed {limit})",
        "dates": ", ".join(sorted({e[1].get("date", "?") for e in events}))[:200],
    }


# ---------------------------------------------------------------------------
# Module B - Crossword
# ---------------------------------------------------------------------------
def crossword_solve_findings(opened_at, finished_at, hints: int, wrong: int,
                             order: list, entry_count: int) -> list:
    """[(kind, triggers), ...] for one completed grid."""
    out = []
    floor = _cfg("CROSSWORD_MIN_SOLVE_SECONDS", 60)
    if opened_at and finished_at:
        elapsed = float(finished_at) - float(opened_at)
        # Hints or wrong answers exempt it: a genuine solve this fast would have neither, so
        # their presence is evidence of someone actually working the grid.
        if 0 <= elapsed < floor and not hints and not wrong:
            out.append((D.CROSSWORD_FAST_SOLVE, {
                "elapsed": f"{elapsed:.0f}s (floor {floor}s)",
                "hints used": hints, "wrong answers": wrong,
                "entries": entry_count,
            }))
    return out


def crossword_sequence_findings(user_id, order: list, now=None) -> tuple:
    """(matched_user_id, triggers) when someone just solved in another's exact order.

    Solving order is close to a fingerprint - people jump about, following whichever crossing
    just opened up. Two accounts producing the same order minutes apart are reading from the
    same place. Short orders are ignored because they collide honestly.
    """
    min_entries = _cfg("CROSSWORD_SEQUENCE_MIN_ENTRIES", 8)
    if not order or len(order) < min_entries:
        return None, None
    window = _cfg("CROSSWORD_SEQUENCE_MATCH_WINDOW", 30 * 60)
    for ts, other_id, meta in D.events_in_window(D.CROSSWORD_SEQUENCE_COPY, window, now=now):
        if str(other_id) == str(user_id):
            continue
        if meta.get("order") == list(order) and meta.get("date") == _today_key():
            return other_id, {
                "identical solve order": " → ".join(map(str, order))[:300],
                "matched": f"<@{other_id}>",
                "apart": f"{int((now or time.time()) - ts)}s",
            }
    return None, None


def _today_key():
    import datetime
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Module C - lockstep daily activity
# ---------------------------------------------------------------------------
def co_occurrence_findings(user_id, now=None) -> tuple:
    """(partner_id, triggers) when two accounts keep running dailies together.

    One pairing means nothing - a server has busy hours, and friends play at the same time.
    The rule is repetition on separate DAYS, which is what separates a habit from one hand
    driving both accounts.
    """
    now = int(now if now is not None else time.time())
    gap = _cfg("ALT_CO_OCCURRENCE_SECONDS", 120)
    need_days = _cfg("ALT_CO_OCCURRENCE_MIN_DAYS", 3)
    window = _cfg("ALT_CO_OCCURRENCE_WINDOW_DAYS", 14) * 86400

    events = D.events_in_window(D.ALT_CO_OCCURRENCE, window, now=now)
    mine = [(ts, m) for ts, uid, m in events if str(uid) == str(user_id)]
    if not mine:
        return None, None

    import datetime
    partners = {}
    for ts, uid, meta in events:
        if str(uid) == str(user_id):
            continue
        for my_ts, _my_meta in mine:
            if abs(ts - my_ts) <= gap:
                day = datetime.date.fromtimestamp(ts).isoformat()
                partners.setdefault(str(uid), set()).add(day)
                break

    for partner, days in partners.items():
        if len(days) >= need_days:
            return partner, {
                "paired within": f"{gap}s of each other",
                "on separate days": f"{len(days)} (threshold {need_days})",
                "days": ", ".join(sorted(days))[:200],
            }
    return None, None


# ---------------------------------------------------------------------------
# Module D - wager washing and recycling
# ---------------------------------------------------------------------------
def wager_wash_findings(loser_id, winner_id, now=None) -> dict | None:
    """Triggers when one account loses nearly everything to the same opponent.

    Losing is normal. Losing 80% of a long run of games to one person, and only that person,
    is a transfer with a game wrapped round it to dodge the /pay cap and its tax.
    """
    now = int(now if now is not None else time.time())
    window = _cfg("WASH_WINDOW_DAYS", 14) * 86400
    min_games = _cfg("WASH_MIN_GAMES", 5)
    ratio_limit = _cfg("WASH_LOSS_RATIO", 0.8)

    rows = DatabaseManager.fetch_all(
        "SELECT loser_id, winner_id, amount FROM game_transfers "
        "WHERE timestamp >= ? AND ((loser_id = ? AND winner_id = ?) OR (loser_id = ? AND winner_id = ?))",
        (now - window, str(loser_id), str(winner_id), str(winner_id), str(loser_id)),
    ) or []
    if len(rows) < min_games:
        return None

    losses = [r for r in rows if str(r[0]) == str(loser_id)]
    ratio = len(losses) / len(rows)
    if ratio < ratio_limit:
        return None
    moved = sum(int(r[2]) for r in losses)
    return {
        "games between the pair": len(rows),
        "lost by one side": f"{len(losses)} ({ratio:.0%}, threshold {ratio_limit:.0%})",
        "net moved": f"{moved:,} UKP",
    }


def recycle_findings(user_id, received: int, received_at, now=None) -> dict | None:
    """Triggers when money lands and leaves again almost immediately.

    A player who won something spends it on the game. A mule passes it on, and the giveaway
    is how little time it rests.
    """
    now = int(now if now is not None else time.time())
    horizon = _cfg("RECYCLE_SECONDS", 300)
    fraction = _cfg("RECYCLE_MIN_FRACTION", 0.7)
    if not received:
        return None
    rows = DatabaseManager.fetch_all(
        "SELECT amount FROM pay_transfers WHERE payer_id = ? AND timestamp BETWEEN ? AND ?",
        (str(user_id), int(received_at), int(received_at) + horizon),
    ) or []
    out = sum(int(r[0]) for r in rows)
    if not out or out < received * fraction:
        return None
    return {
        "received": f"{received:,} UKP",
        "sent onward within": f"{horizon}s",
        "amount passed on": f"{out:,} UKP ({out / received:.0%} of it)",
    }


# ---------------------------------------------------------------------------
# Module E - funnelling into one account
# ---------------------------------------------------------------------------
def funnel_findings(recipient_id, member_ages: dict = None, now=None) -> dict | None:
    """Triggers when several low-tenure accounts all pay the same person.

    Each transfer can sit under the audit cap and the shape still be obvious once you count
    senders instead of amounts, which is the point: the cap is per transfer, so a network
    splits the total to stay beneath it.

    `member_ages` maps sender id -> account age in days, supplied by the caller because this
    module has no Discord access. Senders whose age is unknown are counted, since an absent
    lookup should not be a way to disappear from the tally.
    """
    now = int(now if now is not None else time.time())
    window = _cfg("FUNNEL_WINDOW_HOURS", 72) * 3600
    need = _cfg("FUNNEL_MIN_SENDERS", 3)
    new_days = _cfg("FUNNEL_NEW_ACCOUNT_DAYS", 30)

    rows = DatabaseManager.fetch_all(
        "SELECT payer_id, SUM(amount) FROM pay_transfers "
        "WHERE recipient_id = ? AND timestamp >= ? GROUP BY payer_id",
        (str(recipient_id), now - window),
    ) or []
    ages = member_ages or {}
    fresh = [(str(p), int(a)) for p, a in rows if ages.get(str(p), 0) <= new_days]
    if len(fresh) < need:
        return None
    total = sum(a for _, a in fresh)
    return {
        "distinct low-tenure senders": f"{len(fresh)} in {window // 3600}h (threshold {need})",
        "total pooled": f"{total:,} UKP",
        "senders": ", ".join(f"<@{p}>" for p, _ in fresh)[:300],
    }
