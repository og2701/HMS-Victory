"""Read-only answers about the server's own records, driven by a typed Jev verdict.

"Get me the top 10 shutcoin users", "how much XP has steven got", "who's lost the most at
blackjack this week": the numbers for all of these already sit in SQLite, and a language
model asked to answer them will happily invent some. So the model never sees the database.
Jev picks a METRIC (one row per member, computed here) and a SHAPE (a leaderboard, one
person's figure, a comparison of two, or a server total) plus the knobs a request can carry
(how many, lowest instead of highest, a time window, a particular game). Code runs the query
and renders the exact figures; a language model at most adds a dry line on top.

The registry is the catalogue. Adding a metric is one entry: a label, what it means for Jev,
a few example phrasings, and a function returning ``[(user_id, value)]`` for everyone. The
Jev question is built from the registry, so the two never drift apart.

Every query is guarded: a table that does not exist yet on this database (the archive and
county tables came later than the economy ones) simply yields no rows rather than an error.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import config

logger = logging.getLogger(__name__)

Rows = List[Tuple[str, int]]

# Bots and test accounts never belong on a leaderboard.
EXCLUDED_USER_IDS = {"818885214189256714", str(getattr(config, "BOT_ID", ""))}  # ogme02 test account, the bot

WINDOWS: Dict[str, Optional[int]] = {"all_time": None, "today": 86400, "week": 7 * 86400, "month": 30 * 86400}
WINDOW_LABELS = {"all_time": "all time", "today": "last 24 hours", "week": "last 7 days", "month": "last 30 days"}

# Jev's option name -> the string stored in the results tables.
CASINO_GAMES = {
    "blackjack": "blackjack", "higher_lower": "higherlower", "red_dog": "reddog", "roulette": "roulette",
    "slots": "slots", "video_poker": "videopoker", "three_card_poker": "tcp", "mines": "mines", "chest": "chest",
    "penalty": "penalty", "darts": "darts", "glass_bridge": "glass", "blockade": "blockade",
}
PVP_GAMES = {"connect4": "connect4", "battleship": "battleship", "rps": "rps"}
GAME_LABELS = {
    "blackjack": "blackjack", "higher_lower": "higher or lower", "red_dog": "red dog", "roulette": "roulette",
    "slots": "slots", "video_poker": "video poker", "three_card_poker": "three card poker", "mines": "mines",
    "chest": "chest", "penalty": "penalty shootout", "darts": "darts", "glass_bridge": "glass bridge",
    "blockade": "blockade", "connect4": "Connect 4", "battleship": "Battleship", "rps": "rock paper scissors",
}

DEFAULT_LIMIT = 10
MIN_LIMIT, MAX_LIMIT = 3, 20


def _fetch(sql: str, params: Sequence = ()) -> list:
    """All reads go through here so a missing table or a locked file is a no-result, never a crash."""
    try:
        from database import DatabaseManager
        return DatabaseManager.fetch_all(sql, tuple(params)) or []
    except Exception as e:
        logger.debug("data query failed (%s): %s", sql[:80], e)
        return []


def _agg(table: str, value_expr: str, *, user_col: str = "user_id", ts_col: Optional[str] = None,
         since: Optional[int] = None, where: str = "", params: Sequence = ()) -> Rows:
    clauses = [where] if where else []
    p = list(params)
    if since is not None and ts_col:
        clauses.append(f"{ts_col} >= ?")
        p.append(int(since))
    w = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = _fetch(f"SELECT {user_col}, {value_expr} FROM {table} {w} GROUP BY {user_col}", p)
    out: Rows = []
    for u, v in rows:
        if u is None:
            continue
        try:
            out.append((str(u), int(v or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _merge(*parts: Rows, op: Callable[..., int]) -> Rows:
    """Combine per-user row sets by user id, applying `op` to the aligned values (0 where absent)."""
    ids: List[str] = []
    seen = set()
    maps = []
    for rows in parts:
        m = dict(rows)
        maps.append(m)
        for u in m:
            if u not in seen:
                seen.add(u)
                ids.append(u)
    return [(u, int(op(*[m.get(u, 0) for m in maps]))) for u in ids]


def _casino(expr: str, since: Optional[int], game: Optional[str]) -> Rows:
    g = CASINO_GAMES.get(game or "")
    return _agg("casino_results", expr, ts_col="timestamp", since=since,
                where="game = ?" if g else "", params=(g,) if g else ())


def _pvp(kind: str, since: Optional[int], game: Optional[str]) -> Rows:
    g = PVP_GAMES.get(game or "")
    gw, gp = ("game = ?", (g,)) if g else ("", ())
    if kind == "wins":
        return _agg("pvp_results", "COUNT(*)", user_col="winner_id", ts_col="timestamp", since=since,
                    where=" AND ".join(x for x in [gw, "winner_id IS NOT NULL"] if x), params=gp)
    if kind == "losses":
        return _agg("pvp_results", "COUNT(*)", user_col="loser_id", ts_col="timestamp", since=since,
                    where=" AND ".join(x for x in [gw, "outcome != 'draw'"] if x), params=gp)
    # games played: appearances on either side
    wins = _agg("pvp_results", "COUNT(*)", user_col="winner_id", ts_col="timestamp", since=since, where=gw, params=gp)
    losses = _agg("pvp_results", "COUNT(*)", user_col="loser_id", ts_col="timestamp", since=since, where=gw, params=gp)
    return _merge(wins, losses, op=lambda a, b: a + b)


def _badges(rarity: Optional[str], since: Optional[int]) -> Rows:
    clauses = []
    p: list = []
    if rarity:
        clauses.append("b.rarity = ?")
        p.append(rarity)
    if since is not None:
        clauses.append("ub.awarded_at >= ?")
        p.append(int(since))
    w = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = _fetch(f"SELECT ub.user_id, COUNT(*) FROM user_badges ub JOIN badges b ON ub.badge_id = b.id {w} GROUP BY ub.user_id", p)
    return [(str(u), int(v or 0)) for u, v in rows if u is not None]


def _reason_count(pattern: str, since: Optional[int]) -> Rows:
    return _agg("user_transactions", "COUNT(*)", ts_col="ts", since=since, where="reason LIKE ?", params=(pattern,))


@dataclass(frozen=True)
class Metric:
    key: str
    label: str                       # used in headings: "shutcoins held"
    unit: str                        # appended to figures: "shutcoins", "UKP", "XP", "messages", ""
    what: str                        # for Jev: what the number means
    examples: Tuple[str, ...]        # for Jev: how people ask for it
    rows: Callable[[Optional[int], Optional[str]], Rows]   # (since_ts, game) -> [(user_id, value)]
    windowable: bool = False
    games: str = ""                  # "casino" or "pvp" when a game filter applies
    signed: bool = False             # show +/- (profit and loss)
    default_window: str = "all_time"
    note: str = ""                   # shown under the figures when the data has a caveat


def _m(key, label, unit, what, examples, rows, **kw) -> Metric:
    return Metric(key=key, label=label, unit=unit, what=what, examples=tuple(examples), rows=rows, **kw)


METRICS: Dict[str, Metric] = {m.key: m for m in [
    _m("ukpence", "UKP balance", "UKP",
       "Current UKPence (UKP, the server currency) balance",
       ["top 10 richest", "how much ukpence has steven got", "who's the poorest", "how much money is in circulation"],
       lambda since, game: _agg("ukpence", "balance")),
    _m("shutcoins", "shutcoins held", "shutcoins",
       "Shutcoins currently held (a shutcoin buys a 30-second timeout of another member via the :Shut: reaction)",
       ["top 10 shutcoin users", "who has the most shutcoins", "how many shutcoins does johnny have"],
       lambda since, game: _agg("shutcoins", "balance")),
    _m("shutcoins_bought", "shutcoins bought", "shutcoins",
       "Shutcoins bought from the shop",
       ["who's bought the most shutcoins", "how many shutcoins has kim bought this month"],
       lambda since, game: _agg("shop_purchases", "COALESCE(SUM(quantity),0)", ts_col="purchase_time", since=since,
                                where="item_id = 'shutcoin'"),
       windowable=True),
    _m("shutcoins_used", "shutcoins used", "shutcoins",
       "Shutcoins spent shutting people up: bought minus still held",
       ["how many shutcoins has this person used", "who's used the most shutcoins", "biggest shutcoin spender"],
       lambda since, game: _merge(
           _agg("shop_purchases", "COALESCE(SUM(quantity),0)", where="item_id = 'shutcoin'"),
           _agg("shutcoins", "balance"), op=lambda bought, held: max(0, bought - held)),
       note="bought minus held; staff shuts don't cost a coin and aren't counted"),
    _m("times_shut", "times shut", "",
       "How many times a member has been timed out by the :Shut: reaction (the victim, not the shutter)",
       ["who's been shut the most", "how many times has steven been shut", "most shut member"],
       lambda since, game: _agg("shut_counts", "count")),
    _m("xp", "XP", "XP",
       "Chat XP earned from activity, which sets rank roles",
       ["how much xp does this person have", "xp leaderboard", "who's got the most xp", "what rank is johnny"],
       lambda since, game: _agg("xp", "xp")),
    _m("messages", "messages sent", "messages",
       "Messages posted in the server (the archive only goes back about 30 days)",
       ["who's chatted the most this week", "how many messages has steven sent today", "biggest yapper", "most active members"],
       lambda since, game: _agg("message_archive", "COUNT(*)", ts_col="ts",
                                since=(since if since is not None else int(time.time()) - WINDOWS["month"])),
       windowable=True, default_window="month", note="the archive keeps about 30 days"),
    _m("casino_net", "casino profit and loss", "UKP",
       "Net UKP won or lost against the house casino (blackjack, higher or lower, red dog, roulette, slots, video poker, three card poker, mines, chest, penalties, darts, glass bridge, blockade)",
       ["who's lost the most at the casino", "biggest casino winner this week", "how's steven doing at blackjack", "casino profit"],
       lambda since, game: _casino("COALESCE(SUM(net),0)", since, game),
       windowable=True, games="casino", signed=True),
    _m("casino_staked", "UKP wagered at the casino", "UKP",
       "Total UKP staked at the house casino",
       ["who's gambled the most", "biggest gambler", "how much has johnny wagered on slots"],
       lambda since, game: _casino("COALESCE(SUM(staked),0)", since, game),
       windowable=True, games="casino"),
    _m("casino_games", "casino rounds played", "rounds",
       "Number of casino rounds played",
       ["who's played the most blackjack", "how many hands has steven played", "most casino rounds"],
       lambda since, game: _casino("COUNT(*)", since, game),
       windowable=True, games="casino"),
    _m("casino_biggest_win", "biggest single casino win", "UKP",
       "Largest net win in a single casino round",
       ["biggest single win", "what's the most anyone's won in one go", "steven's best casino win"],
       lambda since, game: _casino("COALESCE(MAX(net),0)", since, game),
       windowable=True, games="casino"),
    _m("pvp_wins", "PvP wins", "wins",
       "Wins in player-versus-player wager games (Connect 4, Battleship, rock paper scissors)",
       ["who's won the most connect 4", "how many battleship games has kim won", "pvp leaderboard"],
       lambda since, game: _pvp("wins", since, game),
       windowable=True, games="pvp"),
    _m("pvp_losses", "PvP losses", "losses",
       "Losses in player-versus-player wager games",
       ["who's lost the most connect 4 games", "how many times has steven lost at battleship"],
       lambda since, game: _pvp("losses", since, game),
       windowable=True, games="pvp"),
    _m("pvp_games", "PvP games played", "games",
       "Player-versus-player games played, won or lost",
       ["who's played the most connect 4", "how many pvp games has johnny played"],
       lambda since, game: _pvp("games", since, game),
       windowable=True, games="pvp"),
    _m("badges", "badges", "badges",
       "Badges earned (all rarities)",
       ["who has the most badges", "how many badges has steven got", "badge leaderboard"],
       lambda since, game: _badges(None, since),
       windowable=True),
    _m("gold_badges", "gold badges", "gold",
       "Gold-rarity badges earned",
       ["most gold badges", "how many golds has kim got", "gold medal table"],
       lambda since, game: _badges("Gold", since),
       windowable=True),
    _m("counties", "counties caught", "counties",
       "County balls caught in the county-collecting game",
       ["who's caught the most counties", "how many counties has johnny got", "biggest county collection"],
       lambda since, game: _agg("county_instances", "COUNT(*)", ts_col="caught_at", since=since),
       windowable=True),
    _m("unique_counties", "different counties owned", "counties",
       "Distinct counties in a member's collection",
       ["who has the most different counties", "how many unique counties has steven got"],
       lambda since, game: _agg("county_instances", "COUNT(DISTINCT county)")),
    _m("bonds", "UKP locked in bonds", "UKP",
       "UKP currently locked in active savings bonds",
       ["who has the most in bonds", "how much has kim got in bonds", "biggest bond holder"],
       lambda since, game: _agg("bonds", "COALESCE(SUM(principal),0)", where="status = 'active'")),
    _m("lottery_wins", "lottery wins", "wins",
       "Lottery rounds won",
       ["who's won the lottery", "how many lotteries has steven won", "luckiest lottery player"],
       lambda since, game: _agg("lottery_rounds", "COUNT(*)", user_col="winner_id", ts_col="drawn_at", since=since,
                                where="winner_id IS NOT NULL"),
       windowable=True),
    _m("lottery_tickets", "lottery tickets bought", "tickets",
       "Lottery tickets bought across all rounds",
       ["who buys the most lottery tickets", "how many tickets has johnny bought"],
       lambda since, game: _agg("lottery_entries", "COALESCE(SUM(tickets),0)")),
    _m("paid_out", "UKP paid to others", "UKP",
       "UKP sent to other members with /pay",
       ["most generous member", "who's paid out the most", "how much has steven given away"],
       lambda since, game: _agg("pay_transfers", "COALESCE(SUM(amount),0)", user_col="payer_id", ts_col="timestamp", since=since),
       windowable=True),
    _m("paid_in", "UKP received from others", "UKP",
       "UKP received from other members with /pay",
       ["who's been paid the most", "biggest beggar", "how much has kim been sent"],
       lambda since, game: _agg("pay_transfers", "COALESCE(SUM(amount),0)", user_col="recipient_id", ts_col="timestamp", since=since),
       windowable=True),
    _m("shop_spent", "UKP spent in the shop", "UKP",
       "UKP spent in the server shop (shutcoins, lucky dips, VIP cases and so on)",
       ["who's spent the most in the shop", "biggest shop spender", "how much has steven spent in the shop"],
       lambda since, game: _agg("shop_purchases", "COALESCE(SUM(price_paid),0)", ts_col="purchase_time", since=since),
       windowable=True),
    _m("wordle_solves", "Wordle solves", "solves",
       "HMS Wordle puzzles solved",
       ["who's solved the most wordles", "how many wordles has johnny done", "wordle leaderboard"],
       lambda since, game: _reason_count("HMS Wordle solve%", since),
       windowable=True),
    _m("tree_waterings", "tree waterings", "waterings",
       "Times a member watered the server tree",
       ["who waters the tree most", "how many times has kim watered the tree"],
       lambda since, game: _reason_count("Tree watering%", since),
       windowable=True),
    _m("roasts_received", "times roasted", "",
       "Times a member has been the target of /roast",
       ["who's been roasted the most", "how many times has steven been roasted"],
       lambda since, game: _agg("recent_roasts", "COUNT(*)", user_col="target_id", ts_col="created_at", since=since,
                                where="target_id IS NOT NULL"),
       windowable=True),
    _m("glazes_received", "times glazed", "",
       "Times a member has been the target of /glaze",
       ["who's been glazed the most", "how many glazes has kim had"],
       lambda since, game: _agg("recent_glazes", "COUNT(*)", user_col="target_id", ts_col="created_at", since=since),
       windowable=True),
]}

SHAPES = ("leaderboard", "person", "compare", "total")


@dataclass
class QuerySpec:
    metric: str
    shape: str
    limit: int = DEFAULT_LIMIT
    lowest: bool = False
    window: str = "all_time"
    game: Optional[str] = None
    subjects: List[Tuple[str, str]] = field(default_factory=list)   # (display name, user_id), as many as the shape needs


@dataclass
class QueryResult:
    spec: QuerySpec
    metric: Metric
    rows: Rows                       # the rows to show, in display order: (user_id, value)
    ranks: Dict[str, int]            # competition rank per shown user id
    population: int                  # members with a non-zero figure
    total: int                       # sum over the population
    ids: List[str]                   # every user id the renderer needs a name for


def normalise_limit(n: Optional[int]) -> int:
    if not n:
        return DEFAULT_LIMIT
    return max(MIN_LIMIT, min(int(n), MAX_LIMIT))


def compute(spec: QuerySpec, *, exclude_ids: Optional[set] = None, member_ids: Optional[set] = None) -> QueryResult:
    """Run the metric and cut it to the requested shape. Pure data; names are resolved by the caller."""
    metric = METRICS[spec.metric]
    window = spec.window if metric.windowable and spec.window in WINDOWS else metric.default_window
    since = None if WINDOWS.get(window) is None else int(time.time()) - WINDOWS[window]
    game = spec.game if metric.games and spec.game else None

    excluded = {str(x) for x in (exclude_ids or set())} | EXCLUDED_USER_IDS
    members = {str(x) for x in member_ids} if member_ids else None
    rows = [(u, v) for u, v in metric.rows(since, game)
            if u not in excluded and (members is None or u in members)]
    values = dict(rows)
    # Ties keep a stable, readable order (lowest user id first) whichever way the board runs.
    ordered = sorted(rows, key=lambda r: ((r[1] if spec.lowest else -r[1]), r[0]))
    # A leaderboard of people with nothing is no leaderboard; the lowest-first view keeps zeros because
    # "who's got nothing" is a real question. Profit and loss keeps negatives either way.
    populated = [r for r in ordered if r[1] != 0] if not (spec.lowest or metric.signed) else ordered
    population = len([r for r in rows if r[1] != 0])
    total = sum(v for _, v in rows)

    def rank_of(uid: str) -> int:
        v = values.get(uid, 0)
        better = sum(1 for _, x in rows if (x < v if spec.lowest else x > v))
        return better + 1

    if spec.shape == "leaderboard":
        shown = populated[: normalise_limit(spec.limit)]
    elif spec.shape in ("person", "compare"):
        shown = [(uid, values.get(uid, 0)) for _, uid in spec.subjects]
    else:
        shown = []
    ranks = {uid: rank_of(uid) for uid, _ in shown}
    ids = [uid for uid, _ in shown] + [uid for _, uid in spec.subjects if uid not in {u for u, _ in shown}]
    return QueryResult(spec=spec, metric=metric, rows=shown, ranks=ranks, population=population, total=total, ids=ids)


def _fmt(metric: Metric, v: int) -> str:
    if metric.signed:
        body = f"+{v:,}" if v >= 0 else f"-{abs(v):,}"
    else:
        body = f"{v:,}"
    return f"{body} {metric.unit}".strip()


def _figure(metric: Metric, v: int) -> str:
    """A figure with its meaning attached, for one-person lines: '14 shutcoins held', '+120 UKP (casino profit and loss)'."""
    num = _fmt(metric, v)
    if not metric.unit or metric.unit.lower() in metric.label.lower():
        return f"{num.strip()} {metric.label}" if not metric.unit else f"{_fmt(Metric(**{**metric.__dict__, 'unit': ''}), v)} {metric.label}"
    return f"{num} ({metric.label})"


def _scope(res: QueryResult) -> str:
    metric, spec = res.metric, res.spec
    bits = []
    if metric.games and spec.game and spec.game in GAME_LABELS:
        bits.append(GAME_LABELS[spec.game])
    window = spec.window if metric.windowable and spec.window in WINDOWS else metric.default_window
    if window != "all_time":
        bits.append(WINDOW_LABELS[window])
    return f" ({', '.join(bits)})" if bits else ""


def render(res: QueryResult, names: Dict[str, str]) -> str:
    """The exact figures as Discord text. `names` maps user id -> display name."""
    metric, spec = res.metric, res.spec
    name = lambda uid: names.get(uid) or f"user {uid}"
    scope = _scope(res)
    foot = f"\n{metric.note}" if metric.note else ""

    if spec.shape == "leaderboard":
        if not res.rows:
            return f"Nobody has any {metric.label}{scope} on record."
        head = f"{'Bottom' if spec.lowest else 'Top'} {len(res.rows)} by {metric.label}{scope}"
        width = max(len(name(u)) for u, _ in res.rows)
        lines = [f"{res.ranks[u]:>2}. {name(u):<{width}}  {_fmt(metric, v)}" for u, v in res.rows]
        return f"{head}\n```\n" + "\n".join(lines) + "\n```" + foot

    if spec.shape == "person":
        uid, v = res.rows[0]
        rank = f" (rank {res.ranks[uid]} of {res.population})" if v != 0 and res.population else ""
        return f"**{name(uid)}**: {_figure(metric, v)}{scope}{rank}" + foot

    if spec.shape == "compare":
        (a, va), (b, vb) = res.rows[0], res.rows[1]
        lines = [f"**{name(a)}**: {_figure(metric, va)}", f"**{name(b)}**: {_figure(metric, vb)}"]
        if va == vb:
            verdict = "Dead level."
        else:
            lead, trail = (a, b) if (va > vb) != spec.lowest else (b, a)
            verdict = f"{name(lead)} ahead by {abs(va - vb):,} {metric.unit}".rstrip() + "."
        return "\n".join(lines) + (f"\n{verdict}" if not scope else f"\n{verdict} ({scope.strip(' ()')})") + foot

    # total
    return (f"Total {metric.label}{scope}: {_fmt(metric, res.total)} across {res.population:,} members"
            + foot)


def catalogue_for_jev() -> Dict[str, dict]:
    """The metric options as Jev criteria, straight from the registry."""
    crit: Dict[str, dict] = {
        "none": {"what": "The message is not asking for a figure, count, balance, rank, ranking or leaderboard from the server's records"},
    }
    for m in METRICS.values():
        crit[m.key] = {"what": m.what, "examples": list(m.examples)}
    return crit
