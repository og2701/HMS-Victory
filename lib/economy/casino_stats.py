"""Per-user casino statistics.

Every finished round of every house game writes one row to the ``casino_results``
table via :func:`record_result` (called at each game's single settle point). From
that we can answer per-user questions (games played, win/loss/push, net P/L, biggest
win, per-game breakdown) and build leaderboards - all from indexed columns rather than
parsing the free-text economy ledger.

This module deliberately depends only on ``DatabaseManager`` so any game module can
import it without pulling in rendering/economy code or risking a circular import.
"""

import time
import logging

from database import DatabaseManager

logger = logging.getLogger(__name__)

# Canonical game keys (stored in casino_results.game) -> human label.
GAME_LABELS = {
    "blackjack": "Blackjack",
    "higherlower": "Higher/Lower",
    "slots": "Fruit Machine",
    "videopoker": "Video Poker",
    "reddog": "Red Dog",
    "tcp": "3-Card Poker",
}


def record_result(user_id, game: str, bet, staked, payout, outcome=None) -> None:
    """Append one finished casino round.

    ``staked`` is the total put at risk (base bet plus any double/raise), ``payout``
    the total returned (0 on a loss, the stake back on a push). ``net`` and the
    normalised ``result`` are derived here so callers can't get them inconsistent.

    Best-effort: a stats failure must never break a payout, so this swallows and logs
    any error rather than raising into the game flow.
    """
    try:
        bet = int(bet)
        staked = int(staked)
        payout = int(payout)
        net = payout - staked
        result = "win" if net > 0 else ("loss" if net < 0 else "push")
        DatabaseManager.execute(
            "INSERT INTO casino_results "
            "(user_id, game, bet, staked, payout, net, outcome, result, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(user_id), str(game), bet, staked, payout, net,
             (str(outcome) if outcome is not None else None), result, int(time.time())),
        )
    except Exception:
        logger.error("Failed to record casino result (%s/%s)", user_id, game, exc_info=True)


def _blank_totals() -> dict:
    return {"games": 0, "wins": 0, "losses": 0, "pushes": 0,
            "staked": 0, "payout": 0, "net": 0, "biggest_win": 0, "biggest_loss": 0}


def get_user_casino_stats(user_id) -> dict:
    """Return ``{"total": {...}, "per_game": {game: {...}}}`` for one user.

    ``net`` positive means the player is up overall; ``biggest_loss`` is the most
    negative single-round net (0 if they've never lost). Empty/zeroed if no rounds.
    """
    rows = DatabaseManager.fetch_all(
        "SELECT game, COUNT(*), "
        "SUM(CASE WHEN result='win' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN result='push' THEN 1 ELSE 0 END), "
        "COALESCE(SUM(staked),0), COALESCE(SUM(payout),0), COALESCE(SUM(net),0), "
        "COALESCE(MAX(net),0), COALESCE(MIN(net),0) "
        "FROM casino_results WHERE user_id = ? GROUP BY game",
        (str(user_id),),
    ) or []

    total = _blank_totals()
    per_game = {}
    for game, n, wins, losses, pushes, staked, payout, net, mx, mn in rows:
        per_game[game] = {
            "games": n, "wins": wins, "losses": losses, "pushes": pushes,
            "staked": staked, "payout": payout, "net": net,
            "biggest_win": max(0, mx), "biggest_loss": min(0, mn),
        }
        total["games"] += n
        total["wins"] += wins
        total["losses"] += losses
        total["pushes"] += pushes
        total["staked"] += staked
        total["payout"] += payout
        total["net"] += net
        total["biggest_win"] = max(total["biggest_win"], mx)
        total["biggest_loss"] = min(total["biggest_loss"], mn)
    return {"total": total, "per_game": per_game}


def get_net_standings(game: str = None, top: int = 5):
    """Return ``(winners, losers)`` for the net-P/L leaderboard.

    ``winners`` are the ``top`` players by highest net, ``losers`` the ``top`` by
    lowest net - with anyone already in ``winners`` removed from ``losers`` so a small
    player pool can't list the same person on both sides. Each entry is
    ``(user_id, net, games)``. Pass ``game`` to scope to one game, or None for overall.
    """
    where = "WHERE game = ?" if game else ""
    gparams = [game] if game else []
    base = ("SELECT user_id, COALESCE(SUM(net),0) AS net, COUNT(*) AS games "
            f"FROM casino_results {where} GROUP BY user_id ")
    winners = DatabaseManager.fetch_all(
        base + "ORDER BY net DESC, games DESC LIMIT ?", tuple(gparams + [int(top)])
    ) or []
    losers = DatabaseManager.fetch_all(
        base + "ORDER BY net ASC, games DESC LIMIT ?", tuple(gparams + [int(top)])
    ) or []
    win_ids = {r[0] for r in winners}
    losers = [r for r in losers if r[0] not in win_ids]
    return winners, losers


def _fmt_signed(n) -> str:
    n = int(n)
    return f"+{n:,}" if n >= 0 else f"-{abs(n):,}"


def session_career(player_id, *, session_count, session_net, current_net=0, over=False):
    """Numbers for the on-game counter: (session_count, session_total, career_games,
    career_net). The current game's net is folded in only once it's ``over`` - at
    result-render time it isn't in casino_results yet (record_result runs in _pay, after
    the render), so we add it here; later renders read it from the DB."""
    stats = get_user_casino_stats(player_id)
    add = int(current_net) if over else 0
    return (int(session_count), int(session_net) + add,
            stats["total"]["games"] + (1 if over else 0),
            stats["total"]["net"] + add)


def session_footer_html(player_id, *, session_count, session_net, current_net=0, over=False) -> str:
    """A fully inline-styled 'This session / Career' footer that can be dropped into any
    casino game's rendered table via a {{SESSION}} placeholder."""
    sc, st, cg, cn = session_career(player_id, session_count=session_count,
                                    session_net=session_net, current_net=current_net, over=over)

    def chip(label, value):
        return (
            '<span style="display:inline-flex;align-items:center;gap:10px;background:rgba(0,0,0,.42);'
            'border:1px solid rgba(214,164,74,.45);border-radius:13px;padding:9px 18px;'
            'font-family:Georgia,\'Times New Roman\',serif;font-weight:700;font-size:20px;color:#fff">'
            f'<span style="font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase;'
            f'color:rgba(255,255,255,.55)">{label}</span>{value}</span>'
        )

    return (
        '<div style="display:flex;gap:16px;justify-content:center;align-items:center;'
        'flex-wrap:wrap;margin-top:10px">'
        + chip("This session", f"Game&nbsp;#{sc} &middot; {_fmt_signed(st)}")
        + chip("Career", f"{cg:,} games &middot; {_fmt_signed(cn)}")
        + "</div>"
    )


def get_casino_leaderboard(metric: str = "net", game: str = None, limit: int = 10) -> list:
    """Top players by a metric across all games (or one ``game``).

    ``metric``: ``net`` (profit), ``payout`` (total won), ``staked`` (total wagered),
    ``games`` (rounds played) or ``biggest_win`` (best single round). Returns a list of
    ``{"user_id", "value", "games"}`` ordered high -> low.
    """
    agg = {
        "net": "COALESCE(SUM(net),0)",
        "payout": "COALESCE(SUM(payout),0)",
        "staked": "COALESCE(SUM(staked),0)",
        "games": "COUNT(*)",
        "biggest_win": "COALESCE(MAX(net),0)",
    }.get(metric, "COALESCE(SUM(net),0)")

    where = "WHERE game = ?" if game else ""
    params = ([game] if game else []) + [int(limit)]
    rows = DatabaseManager.fetch_all(
        f"SELECT user_id, {agg} AS value, COUNT(*) AS games "
        f"FROM casino_results {where} GROUP BY user_id ORDER BY value DESC LIMIT ?",
        tuple(params),
    ) or []
    return [{"user_id": r[0], "value": r[1], "games": r[2]} for r in rows]
