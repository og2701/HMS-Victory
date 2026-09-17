"""Read-only answers about the server's own records, driven by a typed Jev verdict.

"Get me the top 10 shutcoin users", "how much XP has steven got", "who's lost the most at
blackjack this week", "what badges has kim got", "what's in the house bank": the answers to
all of these already sit in SQLite and a few JSON files, and a language model asked for them
will happily invent some. So the model never sees the data. Jev picks what is being asked
for and code fetches it:

  METRIC   one number per member (a balance, a count, a date), cut to a SHAPE: a leaderboard,
           one person's figure with their rank, two people compared, or a server total; with
           the knobs a request can carry (how many, lowest instead of highest, a time window,
           a particular game, an income or spending source).
  LIST     things rather than numbers: a member's badges, counties, bonds, purchases, recent
           messages, casino breakdown or PvP record, or a server fact sheet (house bank, money
           supply, lottery, shop stock, iceberg, open predictions, the Skyrim graveyard). Some
           lists are about one named thing (who holds a badge, who owns a county); the thing is
           chosen by a second Jev call from the real catalogue so it can never be invented.

Code renders the exact figures; a language model at most adds a dry line on top.

The registries are the catalogue. Adding a metric or a list is one entry: a label, what it
means for Jev, a few example phrasings, and a function over the data. The Jev questions are
built from the registries, so the two never drift apart.

Every read is guarded: a table or file that does not exist on this database (the archive and
county tables came later than the economy ones) yields no rows rather than an error.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import config

logger = logging.getLogger(__name__)

Rows = List[Tuple[str, int]]
Lines = List[Tuple[Optional[str], str]]   # (user id to name at the start of the line, or None; text)

# Bots and test accounts never belong on a leaderboard.
EXCLUDED_USER_IDS = {"818885214189256714", str(getattr(config, "BOT_ID", ""))}  # ogme02 test account, the bot

WINDOWS: Dict[str, Optional[int]] = {"all_time": None, "today": 86400, "week": 7 * 86400, "month": 30 * 86400}
WINDOW_LABELS = {"all_time": "all time", "today": "last 24 hours", "week": "last 7 days", "month": "last 30 days"}
AS_OF_LABELS = {"today": "as of yesterday", "week": "as of a week ago", "month": "as of a month ago"}

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

# The ledger (user_transactions) records a free-text reason with every credit and debit. These
# patterns turn it into sources, for "how much has X earned from chatting" and "who's spent the
# most in the shop". Casino reasons come in two spellings: live bets ("Blackjack bet") and the
# reconstructed history (just "blackjack"), so the patterns are loose on purpose.
_CASINO_PATTERNS = ["%lackjack%", "%igher%ower%", "%slots%", "%oulette%", "%ed dog%", "reddog%", "%ideo%oker%",
                    "tcp%", "%hree card%", "%ines %", "mines%", "%hest %", "chest%", "%enalty%", "%arts %", "darts%",
                    "%lass%ridge%", "glass%", "%lockade%"]
SOURCES: Dict[str, Tuple[str, List[str]]] = {
    "chatting": ("chatting", ["Chatting activity reward%"]),
    "top_chatter": ("top chatter of the day", ["Top chatter daily reward%"]),
    "booster": ("server boosting", ["Server booster daily bonus%"]),
    "benefits": ("benefits", ["Benefits payment%"]),
    "welcoming": ("welcoming newcomers", ["%elcom%"]),
    "tree": ("tree watering", ["Tree watering%"]),
    "wordle": ("Wordle", ["HMS Wordle%"]),
    "crossword": ("Crossword", ["HMS Crossword%"]),
    "hall_of_fame": ("Hall of Fame", ["Hall of Fame%"]),
    "badges": ("badge rewards", ["Badge reward%"]),
    "stage": ("stage participation", ["Stage Participation%"]),
    "lottery": ("the lottery", ["Lottery%"]),
    "predictions": ("predictions", ["Prediction%"]),
    "lucky_dip": ("lucky dips and VIP cases", ["Lucky Dip%", "VIP Case%", "Shop: lucky_dip%", "Shop: vip_case%"]),
    "counties": ("selling county balls", ["Sold %county ball%"]),
    "casino": ("the casino", _CASINO_PATTERNS),
    "pvp": ("PvP wagers", ["%stake%", "Wager%"]),
    "shop": ("the shop", ["Shop%", "Purchased%", "Custom rank%", "Iceberg submission%"]),
    "pay": ("/pay", ["Pay%", "/pay%"]),
    "grants": ("staff grants", ["ukpadd%", "roleadd%"]),
    "fines": ("fines, taxes and clawbacks", ["%fraud fine%", "%exemption tax%", "%clawback%", "Lucky Dip penalty%"]),
}

DEFAULT_LIMIT = 10
MIN_LIMIT, MAX_LIMIT = 3, 20
RARITY_ORDER = {"Secret": 0, "Gold": 1, "Silver": 2, "Bronze": 3}
TIER_ORDER = {"legendary": 0, "epic": 1, "rare": 2, "uncommon": 3, "common": 4}


# --- data access ---------------------------------------------------------------------------

def _fetch(sql: str, params: Sequence = ()) -> list:
    """All reads go through here so a missing table or a locked file is a no-result, never a crash."""
    try:
        from database import DatabaseManager
        return DatabaseManager.fetch_all(sql, tuple(params)) or []
    except Exception as e:
        logger.debug("data query failed (%s): %s", sql[:80], e)
        return []


def _json(path_attr: str) -> Any:
    """A JSON data file by its config name, or None. Missing, unreadable and unset all read the same."""
    try:
        from lib.core.file_operations import load_json_file
        path = getattr(config, path_attr, None)
        return load_json_file(path) if path else None
    except Exception as e:
        logger.debug("data file %s unreadable: %s", path_attr, e)
        return None


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


def _ledger(sign: int, since: Optional[int], source: Optional[str]) -> Rows:
    """Sum of credits (sign=+1) or debits (sign=-1) in the ledger, optionally for one source."""
    clauses = ["amount > 0" if sign > 0 else "amount < 0"]
    p: list = []
    patterns = SOURCES.get(source or "", (None, []))[1]
    if patterns:
        clauses.append("(" + " OR ".join("reason LIKE ?" for _ in patterns) + ")")
        p.extend(patterns)
    expr = "COALESCE(SUM(amount),0)" if sign > 0 else "COALESCE(-SUM(amount),0)"
    return _agg("user_transactions", expr, ts_col="ts", since=since, where=" AND ".join(clauses), params=p)


_WITHHELD_TAX_RE = re.compile(r"tax:\s*-\s*([\d,]+)")


def _tax_paid(since: Optional[int]) -> Rows:
    """Tax per member: the wealth tax withheld from taxable income (recorded only inside the ledger's reason
    text, "[gross: 100, tax: -60 (60%)]") plus outright tax debits (exemption taxes, the Council Tax dip)."""
    clauses, p = ["reason LIKE '%tax%'"], []
    if since is not None:
        clauses.append("ts >= ?")
        p.append(int(since))
    rows = _fetch(f"SELECT user_id, amount, reason FROM user_transactions WHERE {' AND '.join(clauses)}", p)
    totals: Dict[str, int] = {}
    for uid, amount, reason in rows:
        if uid is None:
            continue
        text = str(reason or "")
        m = _WITHHELD_TAX_RE.search(text)
        if m:
            try:
                totals[str(uid)] = totals.get(str(uid), 0) + int(m.group(1).replace(",", ""))
            except ValueError:
                pass
        elif int(amount or 0) < 0:
            totals[str(uid)] = totals.get(str(uid), 0) + -int(amount)
    return list(totals.items())


def _balance_as_of(ts: int) -> Rows:
    """Each member's UKP balance as it stood at `ts`, from the balance history."""
    rows = _fetch(
        "SELECT h.user_id, h.balance FROM balance_history h "
        "WHERE h.ts = (SELECT MAX(ts) FROM balance_history WHERE user_id = h.user_id AND ts <= ?)",
        (int(ts),),
    )
    return [(str(u), int(v or 0)) for u, v in rows if u is not None]


def _json_counts(path_attr: str, nested: bool = False) -> Rows:
    """{user_id: count} from a JSON file, or {period: {user_id: count}} summed when nested."""
    data = _json(path_attr)
    if not isinstance(data, dict):
        return []
    totals: Dict[str, int] = {}
    buckets = data.values() if nested else [data]
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        for uid, n in bucket.items():
            try:
                totals[str(uid)] = totals.get(str(uid), 0) + int(n)
            except (TypeError, ValueError):
                continue
    return list(totals.items())


def _skyrim_profiles() -> Dict[str, dict]:
    data = _json("SKYRIM_PROFILES_FILE")
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _skyrim(field_path: Tuple[str, ...]) -> Rows:
    out: Rows = []
    for uid, p in _skyrim_profiles().items():
        v: Any = p
        for key in field_path:
            v = v.get(key) if isinstance(v, dict) else None
        try:
            out.append((uid, int(v or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _skyrim_level() -> Rows:
    try:
        from lib.features.skyrim import data as skyrim_data
        return [(uid, int(skyrim_data.level_from_xp(int(p.get("xp") or 0)))) for uid, p in _skyrim_profiles().items()]
    except Exception as e:
        logger.debug("skyrim level unavailable: %s", e)
        return []


_COUNTY_ROSTER: Optional[Dict[str, Any]] = None


def _county_roster() -> Dict[str, Any]:
    """The county catalogue (key -> County), loaded once. The counties package pulls in its Discord views
    on import; the roster module itself is plain data, so it is read straight from its file when the
    package cannot be imported (a stubbed Discord in tests, or a partial checkout)."""
    global _COUNTY_ROSTER
    if _COUNTY_ROSTER is not None:
        return _COUNTY_ROSTER
    roster: Dict[str, Any] = {}
    try:
        from lib.features.counties.data import COUNTIES
        roster = dict(COUNTIES)
    except Exception:
        try:
            import importlib.util
            import os
            path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "features", "counties", "data.py")
            spec = importlib.util.spec_from_file_location("_county_roster_data", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                roster = dict(getattr(mod, "COUNTIES", {}))
        except Exception as e:
            logger.warning("county roster unavailable: %r", e)
    _COUNTY_ROSTER = roster
    return roster


def _county_keys_of_tier(tier: str) -> List[str]:
    return [k for k, c in _county_roster().items() if getattr(c, "tier", None) == tier]


def _legendary_counties(since: Optional[int]) -> Rows:
    keys = _county_keys_of_tier("legendary")
    if not keys:
        return []
    marks = ",".join("?" for _ in keys)
    return _agg("county_instances", "COUNT(*)", ts_col="caught_at", since=since, where=f"county IN ({marks})", params=keys)


# --- metrics --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Metric:
    key: str
    label: str                       # used in headings: "shutcoins held"
    unit: str                        # appended to figures: "shutcoins", "UKP", "XP", "messages", ""
    what: str                        # for Jev: what the number means
    examples: Tuple[str, ...]        # for Jev: how people ask for it
    rows: Callable[..., Rows]        # (since_ts, game[, source]) -> [(user_id, value)]
    windowable: bool = False
    games: str = ""                  # "casino" or "pvp" when a game filter applies
    sources: bool = False            # accepts an income/spending source
    signed: bool = False             # show +/- (profit and loss)
    kind: str = "int"                # "int" or "date" (value is a unix timestamp)
    as_of: bool = False              # a stock, not a flow: a window means "as it stood back then"
    default_window: str = "all_time"
    note: str = ""                   # shown under the figures when the data has a caveat
    pair: Optional[Callable[..., int]] = None   # (from_id, to_id, since, game) -> the figure from one member to another
    pair_verb: str = "to"            # how the pair reads: "paid to", "beat", "gave to"


def _pair_sum(table: str, value_expr: str, from_col: str, to_col: str, ts_col: str, extra_where: str = ""):
    def fn(a: str, b: str, since: Optional[int], game: Optional[str]) -> int:
        clauses, p = [f"{from_col} = ?", f"{to_col} = ?"], [str(a), str(b)]
        if extra_where:
            clauses.append(extra_where)
        if since is not None:
            clauses.append(f"{ts_col} >= ?")
            p.append(int(since))
        if game and table == "pvp_results" and PVP_GAMES.get(game):
            clauses.append("game = ?")
            p.append(PVP_GAMES[game])
        rows = _fetch(f"SELECT {value_expr} FROM {table} WHERE {' AND '.join(clauses)}", tuple(p))
        try:
            return int((rows[0][0] if rows and rows[0] else 0) or 0)
        except (TypeError, ValueError):
            return 0
    return fn


def _m(key, label, unit, what, examples, rows, **kw) -> Metric:
    return Metric(key=key, label=label, unit=unit, what=what, examples=tuple(examples), rows=rows, **kw)


def _ukpence_rows(since, game):
    return _balance_as_of(since) if since is not None else _agg("ukpence", "balance")


METRICS: Dict[str, Metric] = {m.key: m for m in [
    # --- money
    _m("ukpence", "UKP balance", "UKP",
       "Current UKPence (UKP, the server currency) balance; with a time window, the balance as it stood back then",
       ["top 10 richest", "how much ukpence has steven got", "who's the poorest", "how much money is in circulation", "who was richest a month ago"],
       _ukpence_rows, windowable=True, as_of=True),
    _m("ukp_earned", "UKP earned", "UKP",
       "UKP credited to a member, overall or from one source (chatting, top chatter, boosting, benefits, welcoming, tree, Wordle, Crossword, Hall of Fame, badges, stage, lottery, predictions, lucky dips, county sales, casino wins, PvP, /pay, grants)",
       ["who's earned the most from chatting", "how much has kim made from the casino", "top earners this week", "how much has steven been paid in benefits"],
       lambda since, game, source: _ledger(+1, since, source), windowable=True, sources=True),
    _m("ukp_spent", "UKP spent", "UKP",
       "UKP debited from a member, overall or on one source (shop, casino, PvP wagers, predictions, lottery, /pay, fines)",
       ["who's spent the most on lucky dips", "how much has johnny blown on predictions", "biggest spender this month", "what has kim spent on the shop"],
       lambda since, game, source: _ledger(-1, since, source), windowable=True, sources=True),
    _m("paid_out", "UKP paid to others", "UKP",
       "UKP sent to other members with /pay",
       ["most generous member", "who's paid out the most", "how much has steven given away"],
       lambda since, game: _agg("pay_transfers", "COALESCE(SUM(amount),0)", user_col="payer_id", ts_col="timestamp", since=since),
       windowable=True, pair=_pair_sum("pay_transfers", "COALESCE(SUM(amount),0)", "payer_id", "recipient_id", "timestamp"), pair_verb="paid to"),
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
    _m("tax_paid", "tax paid", "UKP",
       "UKP taken in tax: the wealth tax withheld from taxable income (rewards, bonuses) plus exemption taxes and tax penalties",
       ["who has paid the most tax", "how much tax has steven paid", "biggest taxpayer", "tax paid this month"],
       lambda since, game: _tax_paid(since), windowable=True),
    _m("bonds", "UKP locked in bonds", "UKP",
       "UKP currently locked in active savings bonds",
       ["who has the most in bonds", "how much has kim got in bonds", "biggest bond holder"],
       lambda since, game: _agg("bonds", "COALESCE(SUM(principal),0)", where="status = 'active'")),
    # --- shutcoins
    _m("shutcoins", "shutcoins held", "shutcoins",
       "Shutcoins currently HELD, unspent (a shutcoin buys a 30-second timeout of another member via the :Shut: reaction). Not for 'shutcoin users': that is shutcoins_used",
       ["who holds the most shutcoins", "how many shutcoins does johnny have", "shutcoin balances"],
       lambda since, game: _agg("shutcoins", "balance")),
    _m("shutcoins_bought", "shutcoins bought", "shutcoins",
       "Shutcoins bought from the shop",
       ["who's bought the most shutcoins", "how many shutcoins has kim bought this month"],
       lambda since, game: _agg("shop_purchases", "COALESCE(SUM(quantity),0)", ts_col="purchase_time", since=since,
                                where="item_id = 'shutcoin'"),
       windowable=True),
    _m("shutcoins_used", "shutcoins used", "shutcoins",
       "Shutcoins SPENT shutting people up (bought plus recorded wins, minus still held). 'Shutcoin users' means the people who use them: this metric",
       ["top 10 shutcoin users", "how many shutcoins has this person used", "who's used the most shutcoins", "biggest shutcoin users"],
       lambda since, game: _merge(
           _agg("shop_purchases", "COALESCE(SUM(quantity),0)", where="item_id = 'shutcoin'"),
           _agg("shutcoin_ledger", "COALESCE(SUM(amount),0)", where="amount > 0 AND reason != 'shop'"),
           _agg("shutcoins", "balance"), op=lambda bought, won, held: max(0, bought + won - held)),
       note="bought plus recorded lucky-dip/VIP wins, minus held; wins before the ledger began and staff shuts aren't on record"),
    _m("shutcoins_won", "shutcoins won", "shutcoins",
       "Shutcoins won from lucky dips and VIP cases (recorded since the shutcoin ledger began)",
       ["who's won the most shutcoins from lucky dips", "how many shutcoins has kim won", "luckiest shutcoin winner"],
       lambda since, game: _agg("shutcoin_ledger", "COALESCE(SUM(amount),0)", ts_col="ts", since=since, where="amount > 0 AND reason != 'shop'"),
       windowable=True),
    _m("shuts_given", "shuts given", "shuts",
       "Shutcoins actually spent on shutting someone, one per shut (recorded since the shutcoin ledger began)",
       ["who's shut people the most", "how many shuts has steven done", "most trigger-happy shutter"],
       lambda since, game: _agg("shutcoin_ledger", "COUNT(*)", ts_col="ts", since=since, where="amount < 0"),
       windowable=True),
    _m("times_shut", "times shut", "",
       "How many times a member has been timed out by the :Shut: reaction (the victim, not the shutter)",
       ["who's been shut the most", "how many times has steven been shut", "most shut member"],
       lambda since, game: _agg("shut_counts", "count")),
    _m("shut_victims", "different people shut", "",
       "How many different members someone has shut with a shutcoin",
       ["who's shut the most different people", "how many people has kim shut", "biggest warden"],
       lambda since, game: _agg("warden_targets", "COUNT(*)")),
    # --- activity
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
    _m("messages_tracked", "messages since tracking began", "messages",
       "All-time message count since the member profile started counting (longer than the 30-day archive)",
       ["how many messages has kim ever sent", "all-time message count", "most messages ever"],
       lambda since, game: _agg("member_profile", "messages")),
    _m("first_seen", "first seen", "",
       "When a member was first seen posting (earliest = longest-standing)",
       ["when did steven first show up", "who's been here the longest", "longest-standing members", "newest members"],
       lambda since, game: _agg("member_profile", "first_seen"), kind="date"),
    _m("last_seen", "last seen", "",
       "When a member last posted (earliest = gone quiet the longest)",
       ["when was kim last here", "who's gone quiet", "who hasn't posted in ages", "most recently active"],
       lambda since, game: _agg("member_profile", "last_seen"), kind="date"),
    _m("night_owl", "night-owl messages", "messages",
       "Messages posted in the small hours (the night-owl badge counter)",
       ["biggest night owl", "how many night owl messages has steven got"],
       lambda since, game: _json_counts("NIGHT_OWL_COUNTS_FILE")),
    _m("morning_person", "early-morning messages", "messages",
       "Messages posted early in the morning (the morning-person badge counter)",
       ["biggest morning person", "who's up earliest", "morning person count for kim"],
       lambda since, game: _json_counts("MORNING_PERSON_COUNTS_FILE")),
    _m("weekend_warrior", "weekend messages", "messages",
       "Messages posted at weekends (the weekend-warrior badge counter)",
       ["biggest weekend warrior", "who chats most at the weekend"],
       lambda since, game: _json_counts("WEEKEND_WARRIOR_COUNTS_FILE", nested=True)),
    # --- casino
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
    _m("casino_biggest_loss", "biggest single casino loss", "UKP",
       "Largest net loss in a single casino round",
       ["biggest single loss", "worst hand anyone's had", "kim's worst casino loss"],
       lambda since, game: [(u, -v) for u, v in _casino("COALESCE(MIN(net),0)", since, game)],
       windowable=True, games="casino"),
    # --- pvp
    _m("pvp_wins", "PvP wins", "wins",
       "Wins in player-versus-player wager games (Connect 4, Battleship, rock paper scissors)",
       ["who's won the most connect 4", "how many battleship games has kim won", "pvp leaderboard"],
       lambda since, game: _pvp("wins", since, game),
       windowable=True, games="pvp", pair=_pair_sum("pvp_results", "COUNT(*)", "winner_id", "loser_id", "timestamp", "outcome != 'draw'"), pair_verb="beat"),
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
    # --- badges, counties, collections
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
    _m("secret_badges", "secret badges", "secret",
       "Secret-rarity badges earned",
       ["who has secret badges", "how many secrets has johnny found"],
       lambda since, game: _badges("Secret", since),
       windowable=True),
    _m("counties", "counties caught", "counties",
       "County balls caught in the county-collecting game",
       ["who's caught the most counties", "how many counties has johnny got", "biggest county collection"],
       lambda since, game: _agg("county_instances", "COUNT(*)", ts_col="caught_at", since=since),
       windowable=True),
    _m("unique_counties", "different counties owned", "counties",
       "Distinct counties in a member's collection (out of 92)",
       ["who has the most different counties", "how many unique counties has steven got", "closest to the full set"],
       lambda since, game: _agg("county_instances", "COUNT(DISTINCT county)")),
    _m("legendary_counties", "legendary counties", "legendaries",
       "Legendary-tier county balls owned (London, Yorkshire, Lancashire and the like)",
       ["who has the most legendaries", "how many legendary counties has kim got"],
       lambda since, game: _legendary_counties(since), windowable=True),
    _m("county_gifts_given", "counties given away", "counties",
       "County balls gifted to other members",
       ["who's given away the most counties", "how many counties has steven gifted"],
       lambda since, game: _agg("county_transfers", "COUNT(*)", user_col="from_user", ts_col="transferred_at", since=since),
       windowable=True, pair=_pair_sum("county_transfers", "COUNT(*)", "from_user", "to_user", "transferred_at"), pair_verb="gave to"),
    _m("county_gifts_received", "counties received", "counties",
       "County balls received as gifts",
       ["who's been given the most counties", "how many counties has kim been gifted"],
       lambda since, game: _agg("county_transfers", "COUNT(*)", user_col="to_user", ts_col="transferred_at", since=since),
       windowable=True),
    # --- lottery, predictions, shop
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
    _m("predictions_bet", "prediction bets placed", "bets",
       "Bets placed on prediction events",
       ["who bets on predictions the most", "how many predictions has kim bet on"],
       lambda since, game: _reason_count("Prediction bet%", since), windowable=True),
    _m("predictions_won", "predictions won", "wins",
       "Prediction bets that paid out",
       ["who's won the most predictions", "how many predictions has steven called right", "best tipster"],
       lambda since, game: _reason_count("Prediction win%", since), windowable=True),
    _m("prediction_streak", "current prediction win streak", "in a row",
       "Current run of prediction wins",
       ["who's on the longest prediction streak", "what's kim's prediction streak"],
       lambda since, game: [(u, int((v or {}).get("win_streak", 0))) for u, v in ((_json("PREDICTION_STREAKS_FILE") or {}).items()) if isinstance(v, dict)]),
    _m("lucky_dips", "lucky dips bought", "dips",
       "Lucky dips bought from the shop",
       ["who's bought the most lucky dips", "how many lucky dips has johnny done"],
       lambda since, game: _agg("shop_purchases", "COALESCE(SUM(quantity),0)", ts_col="purchase_time", since=since,
                                where="item_id = 'lucky_dip'"),
       windowable=True),
    _m("vip_cases", "VIP cases bought", "cases",
       "VIP cases bought from the shop",
       ["who's opened the most vip cases", "how many vip cases has kim bought"],
       lambda since, game: _agg("shop_purchases", "COALESCE(SUM(quantity),0)", ts_col="purchase_time", since=since,
                                where="item_id = 'vip_case'"),
       windowable=True),
    # --- games and rewards
    _m("wordle_solves", "Wordle solves", "solves",
       "HMS Wordle puzzles solved",
       ["who's solved the most wordles", "how many wordles has johnny done", "wordle leaderboard"],
       lambda since, game: _reason_count("HMS Wordle solve%", since),
       windowable=True),
    _m("crossword_solves", "Crossword solves", "solves",
       "HMS Crossword puzzles solved",
       ["who's done the most crosswords", "how many crosswords has kim solved"],
       lambda since, game: _reason_count("HMS Crossword solve%", since),
       windowable=True),
    _m("tree_waterings", "tree waterings", "waterings",
       "Times a member watered the server tree",
       ["who waters the tree most", "how many times has kim watered the tree"],
       lambda since, game: _reason_count("Tree watering%", since),
       windowable=True),
    _m("hof_entries", "Hall of Fame entries", "entries",
       "Messages that made the Hall of Fame (paid entries)",
       ["who's in the hall of fame the most", "how many hof entries has steven got"],
       lambda since, game: _reason_count("Hall of Fame%", since),
       windowable=True),
    _m("roasts_received", "times roasted", "",
       "Times a member has been the target of /roast",
       ["who's been roasted the most", "how many times has steven been roasted"],
       lambda since, game: _agg("recent_roasts", "COUNT(*)", user_col="target_id", ts_col="created_at", since=since,
                                where="target_id IS NOT NULL"),
       windowable=True),
    _m("roasts_given", "roasts ordered", "roasts",
       "Times a member has used /roast on someone",
       ["who roasts people the most", "how many roasts has kim ordered"],
       lambda since, game: _agg("roast_usage", "COALESCE(SUM(count),0)")),
    _m("glazes_received", "times glazed", "",
       "Times a member has been the target of /glaze",
       ["who's been glazed the most", "how many glazes has kim had"],
       lambda since, game: _agg("recent_glazes", "COUNT(*)", user_col="target_id", ts_col="created_at", since=since),
       windowable=True),
    # --- skyrim
    _m("skyrim_level", "Skyrim level", "", "Level of a member's Dovahkiin in the Skyrim adventure game",
       ["highest level dovahkiin", "what level is steven in skyrim", "skyrim leaderboard"],
       lambda since, game: _skyrim_level()),
    _m("skyrim_septims", "septims", "septims", "Septims (Skyrim gold) a member's character holds",
       ["who has the most septims", "how many septims has kim got"],
       lambda since, game: _skyrim(("septims",))),
    _m("skyrim_dragons", "dragons slain", "dragons", "Dragons killed in the Skyrim game",
       ["who's killed the most dragons", "how many dragons has johnny slain"],
       lambda since, game: _skyrim(("stats", "dragons"))),
    _m("skyrim_kills", "Skyrim kills", "kills", "Enemies killed in the Skyrim game",
       ["most skyrim kills", "how many kills has steven got in skyrim"],
       lambda since, game: _skyrim(("stats", "kills"))),
    _m("skyrim_deaths", "Skyrim deaths", "deaths", "Times a member's character has died in the Skyrim game",
       ["who's died the most in skyrim", "how many times has kim died"],
       lambda since, game: _skyrim(("stats", "deaths"))),
    _m("skyrim_delves", "delves", "delves", "Dungeon delves started in the Skyrim game",
       ["who's delved the most", "how many delves has johnny done"],
       lambda since, game: _skyrim(("stats", "delves"))),
    _m("skyrim_clears", "dungeons cleared", "clears", "Dungeons fully cleared in the Skyrim game",
       ["most dungeons cleared", "how many clears has steven got"],
       lambda since, game: _skyrim(("stats", "clears"))),
    _m("skyrim_sweetrolls", "sweetrolls", "sweetrolls", "Sweetrolls found in the Skyrim game",
       ["who's found the most sweetrolls", "sweetroll count for kim"],
       lambda since, game: _skyrim(("stats", "sweetrolls"))),
]}

SHAPES = ("leaderboard", "person", "compare", "total", "list", "closest", "between")

_NUMBER_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(k|m|bn|b|thousand|million|mil|grand|billion)?(?![\w.])", re.IGNORECASE)
_TARGET_CUE_RE = re.compile(r"\b(?:closest|nearest|close|near|around|about|approx\w*|roughly|to)\s+(?:to\s+)?(?:the\s+)?$", re.IGNORECASE)
_MULTIPLIERS = {"k": 1_000, "thousand": 1_000, "grand": 1_000, "m": 1_000_000, "mil": 1_000_000, "million": 1_000_000,
                "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000}


def parse_target_number(text: str) -> Optional[int]:
    """The figure a "closest to ..." question names, read from the words: 100k, 1.5m, 100,000, "half a million".

    Jev cannot write a number back, so the target is the one thing taken from the text by code. When
    several numbers appear ("top 5 closest to 100k"), the one after a cue word wins; otherwise the
    largest, since a target is rarely the smaller of "5" and "100k".
    """
    if not text:
        return None
    t = text.replace("½", " 0.5 ")
    t = re.sub(r"\bhalf\s+a\s+(million|thousand|grand|billion)", r"0.5 \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(a|one)\s+(million|thousand|grand|billion)\b", r"1 \2", t, flags=re.IGNORECASE)
    found: List[Tuple[bool, int]] = []
    for m in _NUMBER_RE.finditer(t):
        raw, suffix = m.group(1).replace(",", ""), (m.group(2) or "").lower()
        try:
            value = float(raw) * _MULTIPLIERS.get(suffix, 1)
        except ValueError:
            continue
        cued = bool(_TARGET_CUE_RE.search(t[: m.start()]))
        found.append((cued, int(round(value))))
    if not found:
        return None
    cued = [v for c, v in found if c]
    return cued[0] if cued else max(v for _, v in found)


# --- lists ----------------------------------------------------------------------------------

def _dt(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(float(ts)), tz=timezone.utc).strftime("%d %b %Y")
    except (TypeError, ValueError, OverflowError):
        return "?"


def _when(ts: Any) -> str:
    """Date and time, for lines that live inside a code block (Discord's <t:..> stamps would show raw there)."""
    try:
        return datetime.fromtimestamp(int(float(ts)), tz=timezone.utc).strftime("%d %b %Y %H:%M")
    except (TypeError, ValueError, OverflowError):
        return "?"


def _county_name(key: str) -> Tuple[str, str]:
    c = _county_roster().get(key)
    return (c.name, c.tier) if c else (key, "")


def _shop_item_name(item_id: str) -> str:
    return {"shutcoin": "Shutcoin", "lucky_dip": "Lucky Dip", "vip_case": "VIP Case",
            "custom_emoji_sticker": "Custom emoji/sticker", "roast_access": "Roast access",
            "custom_rank_background": "Custom rank background", "iceberg": "Iceberg entry"}.get(item_id, item_id.replace("_", " "))


def _list_badges(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT b.name, b.rarity, ub.awarded_at FROM user_badges ub JOIN badges b ON ub.badge_id = b.id "
                  "WHERE ub.user_id = ? ORDER BY ub.awarded_at DESC", (uid,))
    rows.sort(key=lambda r: (RARITY_ORDER.get(r[1], 9), -int(r[2] or 0)))
    return [(None, f"{n} [{r}] {_dt(t)}") for n, r, t in rows]


def _list_counties(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT county, COUNT(*) FROM county_instances WHERE user_id = ? GROUP BY county", (uid,))
    named = [(_county_name(k), n) for k, n in rows]
    named.sort(key=lambda r: (TIER_ORDER.get(r[0][1], 9), r[0][0]))
    return [(None, f"{name} x{n}" + (f" [{tier}]" if tier else "")) for (name, tier), n in named]


def _list_bonds(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT principal, rate_pct, term_days, matures_ts, status FROM bonds WHERE user_id = ? "
                  "ORDER BY status = 'active' DESC, matures_ts DESC", (uid,))
    return [(None, f"{int(p):,} UKP at {r}% over {d} days, matures {_dt(m)} [{s}]") for p, r, d, m, s in rows]


def _list_purchases(uid, limit, since, pick) -> Lines:
    clauses, params = ["user_id = ?"], [uid]
    if since is not None:
        clauses.append("purchase_time >= ?")
        params.append(int(since))
    rows = _fetch(f"SELECT item_id, quantity, price_paid, purchase_time FROM shop_purchases WHERE {' AND '.join(clauses)} "
                  "ORDER BY purchase_time DESC LIMIT ?", params + [int(limit)])
    return [(None, f"{_shop_item_name(i)}" + (f" x{q}" if int(q or 1) > 1 else "") + f" for {int(p):,} UKP, {_dt(t)}") for i, q, p, t in rows]


def _list_messages(uid, limit, since, pick) -> Lines:
    clauses, params = ["user_id = ?"], [uid]
    if since is not None:
        clauses.append("ts >= ?")
        params.append(int(since))
    rows = _fetch(f"SELECT content, ts FROM message_archive WHERE {' AND '.join(clauses)} "
                  "ORDER BY ts DESC LIMIT ?", params + [int(limit)])
    out: Lines = []
    for content, ts in rows:
        text = " ".join(str(content or "").split())[:140] or "[attachment]"
        out.append((None, f"{_when(ts)}  {text}"))
    return out


def _list_roasts(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT roast_text, created_at FROM recent_roasts WHERE target_id = ? ORDER BY created_at DESC LIMIT ?", (uid, int(limit)))
    return [(None, f"{_dt(t)}: {' '.join(str(x or '').split())[:200]}") for x, t in rows]


def _list_glazes(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT glaze_text, created_at FROM recent_glazes WHERE target_id = ? ORDER BY created_at DESC LIMIT ?", (uid, int(limit)))
    return [(None, f"{_dt(t)}: {' '.join(str(x or '').split())[:200]}") for x, t in rows]


def _list_casino_breakdown(uid, limit, since, pick) -> Lines:
    clauses, params = ["user_id = ?"], [uid]
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(int(since))
    rows = _fetch(f"SELECT game, COUNT(*), COALESCE(SUM(staked),0), COALESCE(SUM(net),0), COALESCE(MAX(net),0) "
                  f"FROM casino_results WHERE {' AND '.join(clauses)} GROUP BY game ORDER BY COUNT(*) DESC", params)
    labels = {v: GAME_LABELS[k] for k, v in CASINO_GAMES.items()}
    out: Lines = []
    for g, n, staked, net, best in rows:
        sign = "+" if int(net) >= 0 else "-"
        out.append((None, f"{labels.get(g, g)}: {int(n):,} rounds, {int(staked):,} UKP staked, {sign}{abs(int(net)):,} UKP net, best +{int(best):,}"))
    return out


def _list_pvp_record(uid, limit, since, pick) -> Lines:
    clauses, params = ["(winner_id = ? OR loser_id = ?)"], [uid, uid]
    if since is not None:
        clauses.append("timestamp >= ?")
        params.append(int(since))
    rows = _fetch(f"SELECT game, winner_id, loser_id, outcome, stake FROM pvp_results WHERE {' AND '.join(clauses)}", params)
    per_game: Dict[str, List[int]] = {}
    per_opp: Dict[str, List[int]] = {}
    for g, w, l, outcome, stake in rows:
        rec = per_game.setdefault(str(g), [0, 0, 0])
        opp = str(l if str(w) == uid else w) if outcome != "draw" else str(l if str(w) == uid else w)
        orec = per_opp.setdefault(opp, [0, 0, 0]) if opp not in ("None", uid) else None
        if outcome == "draw":
            rec[2] += 1
            if orec is not None:
                orec[2] += 1
        elif str(w) == uid:
            rec[0] += 1
            if orec is not None:
                orec[0] += 1
        else:
            rec[1] += 1
            if orec is not None:
                orec[1] += 1
    labels = {v: GAME_LABELS[k] for k, v in PVP_GAMES.items()}
    out: Lines = [(None, f"{labels.get(g, g)}: {w}W {l}L {d}D") for g, (w, l, d) in sorted(per_game.items())]
    opps = sorted(per_opp.items(), key=lambda kv: -(kv[1][0] + kv[1][1] + kv[1][2]))[: int(limit)]
    out += [(opp, f"{w}W {l}L {d}D") for opp, (w, l, d) in opps]
    return out


def _list_skyrim_sheet(uid, limit, since, pick) -> Lines:
    p = _skyrim_profiles().get(str(uid))
    if not p:
        return []
    stats = p.get("stats") or {}
    lvl = dict(_skyrim_level()).get(str(uid), 0)
    skills = sorted(((k, int(v or 0)) for k, v in (p.get("skills") or {}).items()), key=lambda kv: -kv[1])[:3]
    lines = [
        (None, f"{p.get('name', 'unnamed')}, level {lvl}, {int(p.get('septims') or 0):,} septims, stone: {p.get('stone', '?')}"),
        (None, f"weapon tier {p.get('weapon_tier', 0)}, {p.get('armour_style', '?')} armour tier {p.get('armour_tier', 0)}, "
               f"{int(p.get('potions') or 0)} potions, {int(p.get('words') or 0)}/3 shout words, {int(p.get('souls') or 0)} souls"),
        (None, "top skills: " + ", ".join(f"{k} {v}" for k, v in skills)) if skills else (None, "no skills yet"),
        (None, ", ".join(f"{k} {int(v or 0)}" for k, v in stats.items()) or "no stats yet"),
    ]
    return lines


def _list_bank(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT * FROM bank WHERE id = 1")
    if not rows:
        return []
    cols = [c[1] for c in _fetch("PRAGMA table_info(bank)")]
    row = dict(zip(cols, rows[0]))
    out: Lines = [
        (None, f"house balance {int(row.get('balance') or 0):,} UKP"),
        (None, f"total revenue {int(row.get('total_revenue') or 0):,} UKP, tax collected {int(row.get('total_tax_collected') or 0):,} UKP"),
    ]
    games = []
    for k, v in row.items():
        if k.startswith("total_") and k.endswith("_in"):
            g = k[len("total_"):-len("_in")]
            taken, paid = int(v or 0), int(row.get(f"total_{g}_out") or 0)
            if taken or paid:
                games.append((g, taken - paid, taken, paid))
    games.sort(key=lambda r: -abs(r[1]))
    labels = {v: GAME_LABELS[k] for k, v in CASINO_GAMES.items()}
    for g, net, taken, paid in games[: int(limit)]:
        sign = "+" if net >= 0 else "-"
        out.append((None, f"{labels.get(g, g)}: house {sign}{abs(net):,} UKP (took {taken:,}, paid {paid:,})"))
    return out


def _list_money_supply(uid, limit, since, pick) -> Lines:
    ukp = _fetch("SELECT COALESCE(SUM(balance),0), COUNT(*) FROM ukpence WHERE balance > 0")
    coins = _fetch("SELECT COALESCE(SUM(balance),0), COUNT(*) FROM shutcoins WHERE balance > 0")
    bonds = _fetch("SELECT COALESCE(SUM(principal),0), COUNT(*) FROM bonds WHERE status = 'active'")
    snap = _fetch("SELECT total_circulation, timestamp FROM circulation_snapshots ORDER BY timestamp DESC LIMIT 1")
    out: Lines = []
    if ukp:
        out.append((None, f"{int(ukp[0][0]):,} UKP held across {int(ukp[0][1]):,} members"))
    if bonds:
        out.append((None, f"{int(bonds[0][0]):,} UKP locked in {int(bonds[0][1]):,} active bonds"))
    if coins:
        out.append((None, f"{int(coins[0][0]):,} shutcoins held by {int(coins[0][1]):,} members"))
    if snap:
        out.append((None, f"last circulation snapshot: {int(snap[0][0]):,} UKP on {_dt(snap[0][1])}"))
    return out


def _list_lottery_round(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT id, ticket_price, ticket_cap, rake_pct, draw_ts FROM lottery_rounds WHERE status = 'open' ORDER BY id DESC LIMIT 1")
    out: Lines = []
    if rows:
        rid, price, cap, rake, draw = rows[0]
        sold = _fetch("SELECT COALESCE(SUM(tickets),0), COUNT(*) FROM lottery_entries WHERE round_id = ?", (rid,))
        tickets, players = (int(sold[0][0]), int(sold[0][1])) if sold else (0, 0)
        pot = tickets * int(price) * (100 - int(rake or 0)) // 100
        out.append((None, f"round {rid}: {tickets:,} tickets sold to {players:,} players at {int(price):,} UKP each (cap {int(cap):,})"))
        out.append((None, f"pot about {pot:,} UKP after {int(rake or 0)}% rake, draw {_when(draw)} UTC"))
    else:
        out.append((None, "no lottery round is open right now"))
    last = _fetch("SELECT winner_id, pot, drawn_at FROM lottery_rounds WHERE winner_id IS NOT NULL ORDER BY drawn_at DESC LIMIT 1")
    if last:
        out.append((str(last[0][0]), f"won the last draw: {int(last[0][1] or 0):,} UKP on {_dt(last[0][2])}"))
    return out


def _list_lottery_winners(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT winner_id, pot, drawn_at FROM lottery_rounds WHERE winner_id IS NOT NULL ORDER BY drawn_at DESC LIMIT ?", (int(limit),))
    return [(str(w), f"{int(p or 0):,} UKP, {_dt(t)}") for w, p, t in rows]


def _list_shop_stock(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT item_id, quantity, max_quantity, auto_restock FROM shop_inventory ORDER BY item_id")
    out: Lines = []
    for i, q, mx, auto in rows:
        cap = f"/{int(mx):,}" if mx is not None else ""
        out.append((None, f"{_shop_item_name(i)}: {int(q or 0):,}{cap} in stock" + (", restocks" if auto else "")))
    return out


def _list_iceberg(uid, limit, since, pick) -> Lines:
    rows = _fetch("SELECT text, level FROM iceberg ORDER BY level, id")
    out: Lines = []
    by_level: Dict[int, List[str]] = {}
    for text, level in rows:
        by_level.setdefault(int(level or 0), []).append(str(text))
    out.append((None, f"{len(rows)} entries across {len(by_level)} levels"))
    for level in sorted(by_level):
        entries = by_level[level]
        shown = ", ".join(e[:40] for e in entries[:6]) + (f" and {len(entries) - 6} more" if len(entries) > 6 else "")
        out.append((None, f"level {level} ({len(entries)}): {shown}"))
    return out[: int(limit) + 1]


def _list_predictions(uid, limit, since, pick) -> Lines:
    out: Lines = []
    data = _json("PREDICTIONS_FILE")
    if isinstance(data, dict):
        for key, p in data.items():
            if not isinstance(p, dict) or p.get("winner") or p.get("drawn"):
                continue
            options = p.get("options") or []
            bets = p.get("bets") or {}
            totals = []
            for i, opt in enumerate(options, 1):
                pool = bets.get(str(i)) or bets.get(i) or {}
                totals.append(f"{opt} {sum(int(v) for v in pool.values() if isinstance(v, (int, float))):,}")
            state = "locked" if p.get("locked") else "open"
            out.append((None, f"{p.get('title', '?')[:80]} [{state}]: " + " | ".join(totals) + (f", ends {_when(p.get('end_ts'))} UTC" if p.get("end_ts") else "")))
    rows = _fetch("SELECT title, opt1, opt2, scheduled_ts FROM scheduled_predictions WHERE status = 'pending' ORDER BY scheduled_ts LIMIT ?", (int(limit),))
    out += [(None, f"scheduled {_when(ts)} UTC: {str(t)[:80]} ({a} vs {b})") for t, a, b, ts in rows]
    return out[: int(limit)]


def _list_graveyard(uid, limit, since, pick) -> Lines:
    grave = _json("SKYRIM_GRAVEYARD_FILE")
    if not isinstance(grave, list):
        return []
    out: Lines = []
    for row in reversed(grave[-int(limit):]):
        if not isinstance(row, dict):
            continue
        who = str(row.get("user_id")) if row.get("user_id") else None
        out.append((who, f"{row.get('name', 'an adventurer')} fell in {row.get('loc', '?')} room {row.get('room', '?')} "
                         f"with {int(row.get('satchel') or 0):,} septims, {row.get('date', '?')}"))
    return out


def _list_badge_holders(uid, limit, since, pick) -> Lines:
    if not pick:
        return []
    rows = _fetch("SELECT ub.user_id, ub.awarded_at FROM user_badges ub JOIN badges b ON ub.badge_id = b.id "
                  "WHERE b.name = ? ORDER BY ub.awarded_at ASC", (pick,))
    return [(str(u), f"since {_dt(t)}") for u, t in rows]


def _list_county_owners(uid, limit, since, pick) -> Lines:
    if not pick:
        return []
    key = next((k for k, c in _county_roster().items() if c.name == pick), None)
    if not key:
        return []
    rows = _fetch("SELECT user_id, COUNT(*) FROM county_instances WHERE county = ? GROUP BY user_id ORDER BY COUNT(*) DESC", (key,))
    return [(str(u), f"x{int(n)}") for u, n in rows]


def _list_economy_today(uid, limit, since, pick) -> Lines:
    data = _json("ECONOMY_METRICS_FILE")
    if not isinstance(data, dict) or not data:
        return []
    day = sorted(data)[-1]
    todays = data.get(day) or {}
    if not isinstance(todays, dict):
        return []
    lines: Lines = [(None, f"payouts recorded for {day}:")]
    for k, v in sorted(todays.items()):
        try:
            lines.append((None, f"{k.replace('_', ' ')}: {int(v):,} UKP"))
        except (TypeError, ValueError):
            continue
    return lines


PROFILE_METRICS = ("ukpence", "xp", "messages", "shutcoins", "shutcoins_used", "times_shut", "casino_net", "casino_staked",
                   "pvp_wins", "pvp_losses", "badges", "gold_badges", "counties", "bonds", "paid_out", "lottery_wins",
                   "first_seen", "skyrim_level")


# Who counts when a list ranks people: set by compute_list for the duration of one query.
_SCOPE: Tuple[set, Optional[set]] = (set(), None)


def _list_profile(uid, limit, since, pick) -> Lines:
    """A member's main figures with their rank: the answer to "stats about X"."""
    out: Lines = []
    excluded, members = _SCOPE
    excluded = excluded | EXCLUDED_USER_IDS
    for key in PROFILE_METRICS:
        metric = METRICS[key]
        try:
            rows = [(u, v) for u, v in metric.rows(None, None)
                    if u not in excluded and (members is None or u in members or u == str(uid))]
        except Exception as e:
            logger.debug("profile metric %s failed: %s", key, e)
            continue
        window = f" ({WINDOW_LABELS[metric.default_window]})" if metric.default_window != "all_time" else ""
        values = dict(rows)
        v = values.get(str(uid), 0)
        if v == 0 and metric.kind != "date":
            continue
        if metric.kind == "date":
            if not v:
                continue
            out.append((None, _figure(metric, v)))
            continue
        better = sum(1 for _, x in rows if x > v)
        population = sum(1 for _, x in rows if x != 0)
        out.append((None, f"{_figure(metric, v)}{window} (rank {better + 1} of {population})"))
    return out


@dataclass(frozen=True)
class ListKind:
    key: str
    label: str
    what: str
    examples: Tuple[str, ...]
    lines: Callable[[Optional[str], int, Optional[int], Optional[str]], Lines]   # (subject_id, limit, since, pick)
    per_person: bool = True
    pick: str = ""                   # "badge" or "county": the list is about one named thing
    windowable: bool = False
    empty: str = "nothing on record"


def _l(key, label, what, examples, lines, **kw) -> ListKind:
    return ListKind(key=key, label=label, what=what, examples=tuple(examples), lines=lines, **kw)


LISTS: Dict[str, ListKind] = {l.key: l for l in [
    _l("profile", "stats", "A member's main figures at a glance: balance, XP, messages, shutcoins, casino, PvP, badges, counties, bonds, first seen",
       ["fetch me stats about steven", "kim's stats", "what are my numbers", "give me a rundown on johnny", "profile for @X"], _list_profile, empty="nothing on record at all"),
    _l("badges", "badges", "The badges a member has earned, with rarity and date",
       ["what badges has steven got", "show me kim's badges", "list my badges"], _list_badges, empty="no badges yet"),
    _l("counties", "county collection", "The county balls a member owns, with tier and count",
       ["what counties does johnny have", "show kim's county collection", "which counties have I got"], _list_counties, empty="no counties caught yet"),
    _l("bonds", "bonds", "A member's savings bonds: principal, rate, maturity",
       ["what bonds has steven got", "when do my bonds mature", "show kim's bonds"], _list_bonds, empty="no bonds"),
    _l("purchases", "shop purchases", "What a member has bought from the shop, newest first",
       ["what has johnny bought from the shop", "steven's recent purchases", "what did I buy"], _list_purchases, windowable=True, empty="no purchases"),
    _l("messages", "recent messages", "A member's most recent messages from the archive",
       ["what did steven say last", "kim's last 5 messages", "what has johnny been posting"], _list_messages, windowable=True, empty="nothing in the archive"),
    _l("roasts", "roasts received", "The most recent /roast texts aimed at a member",
       ["what was the last roast of steven", "show me kim's roasts", "how was johnny roasted"], _list_roasts, empty="never been roasted"),
    _l("glazes", "glazes received", "The most recent /glaze texts about a member",
       ["what was the last glaze of kim", "show steven's glazes"], _list_glazes, empty="never been glazed"),
    _l("casino_breakdown", "casino record by game", "A member's casino record per game: rounds, staked, net, best win",
       ["how does steven do at each casino game", "kim's casino breakdown", "which game is johnny worst at"], _list_casino_breakdown, windowable=True, empty="never played the casino"),
    _l("pvp_record", "PvP record", "A member's PvP record per game and against their most frequent opponents",
       ["what's steven's connect 4 record", "who does kim play the most", "johnny's head to head record"], _list_pvp_record, windowable=True, empty="no PvP games"),
    _l("skyrim_sheet", "Skyrim character", "A member's Skyrim character: name, level, septims, gear, skills, stats",
       ["show steven's skyrim character", "what's kim's dovahkiin like", "my skyrim sheet"], _list_skyrim_sheet, empty="no Dovahkiin yet"),
    _l("bank", "house bank", "The house bank: balance, revenue, tax, and how the house is doing per casino game",
       ["what's in the house bank", "how much has the house made", "which game makes the bank the most"], _list_bank, per_person=False, empty="no bank record"),
    _l("money_supply", "money supply", "How much UKP, bonds and shutcoins exist across the server",
       ["how much money is there in total", "total ukp in circulation", "how many shutcoins exist"], _list_money_supply, per_person=False, empty="no economy data"),
    _l("lottery", "lottery", "The current lottery round: tickets sold, pot, draw time, and the last winner",
       ["what's the lottery pot", "when's the lottery draw", "who won the last lottery"], _list_lottery_round, per_person=False, empty="no lottery data"),
    _l("lottery_winners", "recent lottery winners", "Recent lottery winners and what they won",
       ["who's won the lottery recently", "last few lottery winners"], _list_lottery_winners, per_person=False, empty="no draws yet"),
    _l("shop_stock", "shop stock", "What the shop has in stock",
       ["what's in stock in the shop", "are there any shutcoins in the shop", "shop inventory"], _list_shop_stock, per_person=False, empty="the shop is empty"),
    _l("iceberg", "iceberg", "The server iceberg: entries by level",
       ["what's on the iceberg", "how many iceberg entries are there", "what's at the bottom of the iceberg"], _list_iceberg, per_person=False, empty="the iceberg is empty"),
    _l("predictions", "open predictions", "Prediction events currently open or scheduled, with the pools on each side",
       ["what predictions are open", "any predictions on", "what's the pool on the prediction"], _list_predictions, per_person=False, empty="no predictions open"),
    _l("graveyard", "Skyrim graveyard", "Adventurers who died recently in the Skyrim game",
       ["who's died in skyrim recently", "show the graveyard", "who fell last"], _list_graveyard, per_person=False, empty="nobody has died lately"),
    _l("economy_today", "today's payouts", "Today's server payouts by type (welcome bonuses and so on)",
       ["how much has the bot paid out today", "today's economy figures"], _list_economy_today, per_person=False, empty="no payouts recorded"),
    _l("badge_holders", "holders of a badge", "Who holds a particular badge (named in the message)",
       ["who has the warden badge", "who's got the shut victim badge", "list everyone with the night owl badge"], _list_badge_holders, per_person=False, pick="badge", empty="nobody holds it"),
    _l("county_owners", "owners of a county", "Who owns a particular county ball (named in the message)",
       ["who owns yorkshire", "who has london", "who's got a cornwall ball"], _list_county_owners, per_person=False, pick="county", empty="nobody owns it"),
]}


def pick_candidates(kind: str) -> List[str]:
    """The real names a pick-list can be about, for Jev to choose from."""
    if kind == "badge":
        return [str(r[0]) for r in _fetch("SELECT name FROM badges ORDER BY name") if r and r[0]]
    if kind == "county":
        return [c.name for c in _county_roster().values()]
    return []


# --- running a query -------------------------------------------------------------------------

@dataclass
class QuerySpec:
    metric: str
    shape: str
    limit: int = DEFAULT_LIMIT
    lowest: bool = False
    window: str = "all_time"
    game: Optional[str] = None
    source: Optional[str] = None
    subjects: List[Tuple[str, str]] = field(default_factory=list)   # (display name, user_id), as many as the shape needs
    list_kind: Optional[str] = None
    pick: Optional[str] = None
    target: Optional[int] = None      # the figure a "closest to" question names


@dataclass
class QueryResult:
    spec: QuerySpec
    metric: Optional[Metric]
    rows: Rows                       # the rows to show, in display order: (user_id, value)
    ranks: Dict[str, int]            # competition rank per shown user id
    population: int                  # members with a non-zero figure
    total: int                       # sum over the population
    ids: List[str]                   # every user id the renderer needs a name for
    lines: Lines = field(default_factory=list)
    list_kind: Optional[ListKind] = None


def normalise_limit(n: Optional[int]) -> int:
    if not n:
        return DEFAULT_LIMIT
    return max(MIN_LIMIT, min(int(n), MAX_LIMIT))


def _since_for(windowable: bool, window: str, default_window: str = "all_time") -> Tuple[str, Optional[int]]:
    w = window if windowable and window in WINDOWS else default_window
    return w, (None if WINDOWS.get(w) is None else int(time.time()) - WINDOWS[w])


def compute(spec: QuerySpec, *, exclude_ids: Optional[set] = None, member_ids: Optional[set] = None) -> QueryResult:
    """Run the metric and cut it to the requested shape. Pure data; names are resolved by the caller."""
    if spec.shape == "list":
        return compute_list(spec, exclude_ids=exclude_ids, member_ids=member_ids)
    metric = METRICS[spec.metric]
    if spec.shape == "between" and (metric.pair is None or len(spec.subjects) < 2):
        spec.shape = "compare"      # no directional figure for this metric: side by side is the nearest thing
    if spec.shape == "between":
        _, since = _since_for(metric.windowable, spec.window, metric.default_window)
        game = spec.game if metric.games and spec.game else None
        (na, a), (nb, b) = spec.subjects[0], spec.subjects[1]
        ab, ba = metric.pair(a, b, since, game), metric.pair(b, a, since, game)
        return QueryResult(spec=spec, metric=metric, rows=[(a, ab), (b, ba)], ranks={}, population=2, total=ab + ba, ids=[a, b])
    window, since = _since_for(metric.windowable, spec.window, metric.default_window)
    game = spec.game if metric.games and spec.game else None
    source = spec.source if metric.sources and spec.source in SOURCES else None

    excluded = {str(x) for x in (exclude_ids or set())} | EXCLUDED_USER_IDS
    members = {str(x) for x in member_ids} if member_ids else None
    raw = metric.rows(since, game, source) if metric.sources else metric.rows(since, game)
    rows = [(u, v) for u, v in raw if u not in excluded and (members is None or u in members)]
    values = dict(rows)
    # Ties keep a stable, readable order (lowest user id first) whichever way the board runs.
    ordered = sorted(rows, key=lambda r: ((r[1] if spec.lowest else -r[1]), r[0]))
    # A leaderboard of people with nothing is no leaderboard; the lowest-first view keeps zeros because
    # "who's got nothing" is a real question. Profit and loss keeps negatives either way.
    populated = [r for r in ordered if r[1] != 0] if not (spec.lowest or metric.signed) else ordered
    population = len([r for r in rows if r[1] != 0])
    total = sum(v for _, v in rows) if metric.kind == "int" else population

    def rank_of(uid: str) -> int:
        v = values.get(uid, 0)
        better = sum(1 for _, x in rows if (x < v if spec.lowest else x > v))
        return better + 1

    if spec.shape == "leaderboard":
        shown = populated[: normalise_limit(spec.limit)]
    elif spec.shape == "closest" and spec.target is not None:
        # Nearest to the target either side, closest first; ties by user id so the order is stable.
        shown = sorted(rows, key=lambda r: (abs(r[1] - spec.target), r[0]))[: min(normalise_limit(spec.limit), 5) if not spec.limit else normalise_limit(spec.limit)]
    elif spec.shape in ("person", "compare"):
        shown = [(uid, values.get(uid, 0)) for _, uid in spec.subjects]
    else:
        shown = []
    ranks = {uid: rank_of(uid) for uid, _ in shown}
    if spec.shape == "closest":
        ranks = {uid: i for i, (uid, _) in enumerate(shown, 1)}
    ids = [uid for uid, _ in shown] + [uid for _, uid in spec.subjects if uid not in {u for u, _ in shown}]
    return QueryResult(spec=spec, metric=metric, rows=shown, ranks=ranks, population=population, total=total, ids=ids)


def compute_list(spec: QuerySpec, *, exclude_ids: Optional[set] = None, member_ids: Optional[set] = None) -> QueryResult:
    global _SCOPE
    kind = LISTS[spec.list_kind or ""]
    _, since = _since_for(kind.windowable, spec.window)
    subject = spec.subjects[0][1] if (kind.per_person and spec.subjects) else None
    limit = normalise_limit(spec.limit)
    excluded = {str(x) for x in (exclude_ids or set())} | EXCLUDED_USER_IDS
    members = {str(x) for x in member_ids} if member_ids else None
    _SCOPE = (excluded, members)
    try:
        lines = kind.lines(subject, limit, since, spec.pick)
    finally:
        _SCOPE = (set(), None)
    if not kind.per_person:
        lines = [(u, t) for u, t in lines if u is None or (u not in excluded and (members is None or u in members))]
    lines = lines[: max(limit, 12)] if kind.per_person and kind.key in ("badges", "counties") else lines[:limit]
    ids = [u for u, _ in lines if u] + [uid for _, uid in spec.subjects]
    return QueryResult(spec=spec, metric=None, rows=[], ranks={}, population=len(lines), total=0, ids=ids,
                       lines=lines, list_kind=kind)


# --- rendering ---------------------------------------------------------------------------------

def _fmt(metric: Metric, v: int) -> str:
    if metric.kind == "date":
        return _dt(v) if v else "never"
    if metric.signed:
        body = f"+{v:,}" if v >= 0 else f"-{abs(v):,}"
    else:
        body = f"{v:,}"
    return f"{body} {metric.unit}".strip()


def _figure(metric: Metric, v: int) -> str:
    """A figure with its meaning attached, for one-person lines: '14 shutcoins held', '+120 UKP (casino profit and loss)'."""
    if metric.kind == "date":
        return f"{metric.label} {_fmt(metric, v)}"
    num = _fmt(metric, v)
    if not metric.unit or metric.unit.lower() in metric.label.lower():
        bare = num if not metric.unit else num[: -len(metric.unit)].strip()
        return f"{bare} {metric.label}"
    return f"{num} ({metric.label})"


def _scope(res: QueryResult) -> str:
    metric, spec = res.metric, res.spec
    bits = []
    if metric is None:
        return ""
    if metric.games and spec.game and spec.game in GAME_LABELS:
        bits.append(GAME_LABELS[spec.game])
    if metric.sources and spec.source in SOURCES:
        bits.append(f"from {SOURCES[spec.source][0]}" if spec.metric == "ukp_earned" else f"on {SOURCES[spec.source][0]}")
    window, _ = _since_for(metric.windowable, spec.window, metric.default_window)
    if window != "all_time":
        bits.append(AS_OF_LABELS[window] if metric.as_of else WINDOW_LABELS[window])
    return f" ({', '.join(bits)})" if bits else ""


def render(res: QueryResult, names: Dict[str, str]) -> str:
    """The exact figures as Discord text. `names` maps user id -> display name."""
    name = lambda uid: names.get(uid) or f"user {uid}"
    if res.list_kind is not None:
        return _render_list(res, name)
    metric, spec = res.metric, res.spec
    scope = _scope(res)
    foot = f"\n{metric.note}" if metric.note else ""

    if spec.shape == "leaderboard":
        if not res.rows:
            return f"Nobody has any {metric.label}{scope} on record."
        if metric.kind == "date":
            head = f"{'Earliest' if spec.lowest else 'Latest'} {len(res.rows)} by {metric.label}{scope}"
        else:
            head = f"{'Bottom' if spec.lowest else 'Top'} {len(res.rows)} by {metric.label}{scope}"
        # Figures first, names last: figures are plain ASCII so the columns line up, while a nickname
        # full of emoji is any width Discord feels like.
        vwidth = max(len(_fmt(metric, v)) for _, v in res.rows)
        lines = [f"{res.ranks[u]:>2}. {_fmt(metric, v):>{vwidth}}  {name(u)}" for u, v in res.rows]
        return f"{head}\n```\n" + "\n".join(lines) + "\n```" + foot

    if spec.shape == "closest":
        if not res.rows or spec.target is None:
            return f"Nobody has any {metric.label}{scope} on record."
        head = f"Closest {len(res.rows)} to {_fmt(metric, spec.target)} {metric.label}{scope}".replace(f"{metric.unit} {metric.label}", metric.label if metric.unit and metric.unit.lower() in metric.label.lower() else f"{metric.unit} {metric.label}")
        vwidth = max(len(_fmt(metric, v)) for _, v in res.rows)
        gaps = []
        for u, v in res.rows:
            diff = v - spec.target
            gaps.append("spot on" if diff == 0 else f"{diff:+,} {metric.unit}".rstrip())
        gwidth = max(len(g) for g in gaps)
        lines = [f"{res.ranks[u]:>2}. {_fmt(metric, v):>{vwidth}}  {g:<{gwidth}}  {name(u)}" for (u, v), g in zip(res.rows, gaps)]
        return f"{head}\n```\n" + "\n".join(lines) + "\n```" + foot

    if spec.shape == "person":
        uid, v = res.rows[0]
        rank = f" (rank {res.ranks[uid]} of {res.population})" if v != 0 and res.population and metric.kind == "int" else ""
        return _block(f"{name(uid)}: {_figure(metric, v)}{scope}{rank}") + foot

    if spec.shape == "between":
        (a, va), (b, vb) = res.rows[0], res.rows[1]
        unit = metric.unit or metric.label
        lines = [f"{name(a)} {metric.pair_verb} {name(b)}: {va:,} {unit}".rstrip(),
                 f"{name(b)} {metric.pair_verb} {name(a)}: {vb:,} {unit}".rstrip()]
        return _block("\n".join(lines) + (f"\n({scope.strip(' ()')})" if scope else "")) + foot

    if spec.shape == "compare":
        (a, va), (b, vb) = res.rows[0], res.rows[1]
        lines = [f"{name(a)}: {_figure(metric, va)}", f"{name(b)}: {_figure(metric, vb)}"]
        if va == vb:
            verdict = "Dead level."
        elif metric.kind == "date":
            earlier, later = (a, b) if va < vb else (b, a)
            verdict = f"{name(earlier)} earlier by {abs(va - vb) // 86400:,} days."
        else:
            lead, trail = (a, b) if (va > vb) != spec.lowest else (b, a)
            verdict = f"{name(lead)} ahead by {abs(va - vb):,} {metric.unit}".rstrip() + "."
        return _block("\n".join(lines) + (f"\n{verdict}" if not scope else f"\n{verdict} ({scope.strip(' ()')})")) + foot

    # total
    if metric.kind == "date":
        return _block(f"{res.population:,} members have a {metric.label} date on record")
    return _block(f"Total {metric.label}{scope}: {_fmt(metric, res.total)} across {res.population:,} members") + foot


def _block(text: str) -> str:
    return f"```\n{text}\n```"


def figures_by_member(res: QueryResult) -> Dict[str, str]:
    """Each shown member's figure in words, for follow-ups ("who is clown"): {user_id: "34 times shut, 5th"}."""
    out: Dict[str, str] = {}
    if res.metric is None:
        return out
    for uid, v in res.rows:
        rank = res.ranks.get(uid)
        place = f", {rank}{'th' if 11 <= rank % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(rank % 10, 'th')}" if rank and res.spec.shape in ("leaderboard", "closest") else ""
        out[uid] = f"{_figure(res.metric, v)}{place}"
    return out


def _render_list(res: QueryResult, name) -> str:
    kind, spec = res.list_kind, res.spec
    who = f"{name(spec.subjects[0][1])}'s " if kind.per_person and spec.subjects else ""
    what = f"{kind.label}" + (f": {spec.pick}" if spec.pick else "")
    window, _ = _since_for(kind.windowable, spec.window)
    scope = f" ({WINDOW_LABELS[window]})" if window != "all_time" else ""
    head = f"{who}{what}{scope}"
    if not res.lines:
        return _block(f"{head}: {kind.empty}.")
    body = "\n".join(f"{name(u) + ': ' if u else ''}{t}" for u, t in res.lines)
    return _block(f"{head}\n{body}")


# --- a digest for opinions -----------------------------------------------------------------------

DIGEST_METRICS = ("messages", "xp", "ukpence", "times_shut", "shutcoins_used", "casino_net", "casino_staked", "badges",
                  "counties", "paid_out", "pvp_wins", "tax_paid")


def stats_digest(*, exclude_ids: Optional[set] = None, member_ids: Optional[set] = None, top: int = 5) -> Tuple[List[Tuple[str, List[Tuple[str, str]]]], List[str]]:
    """The top few members on each headline metric, for a language model asked for an opinion "based on stats".

    Returns ([(heading, [(user_id, figure)])], every user id mentioned). Code picks the facts; the model
    only chooses among them, so it can justify a pick without inventing a number.
    """
    sections: List[Tuple[str, List[Tuple[str, str]]]] = []
    ids: List[str] = []
    for key in DIGEST_METRICS:
        metric = METRICS[key]
        try:
            res = compute(QuerySpec(metric=key, shape="leaderboard", limit=max(MIN_LIMIT, top)), exclude_ids=exclude_ids, member_ids=member_ids)
        except Exception as e:
            logger.debug("digest metric %s failed: %s", key, e)
            continue
        if not res.rows:
            continue
        window = f" ({WINDOW_LABELS[metric.default_window]})" if metric.default_window != "all_time" else ""
        sections.append((f"{metric.label}{window}", [(uid, _fmt(metric, v)) for uid, v in res.rows[:top]]))
        ids += [uid for uid, _ in res.rows[:top]]
    return sections, list(dict.fromkeys(ids))


def render_digest(sections: List[Tuple[str, List[Tuple[str, str]]]], names: Dict[str, str]) -> str:
    lines = [f"{heading}: " + "; ".join(f"{names.get(uid) or f'user {uid}'} {fig}" for uid, fig in rows) for heading, rows in sections]
    return "\n".join(lines)


# --- what Jev is offered -----------------------------------------------------------------------

def catalogue_for_jev() -> Dict[str, dict]:
    """The metric options as Jev criteria, straight from the registry."""
    crit: Dict[str, dict] = {
        "none": {"what": "The message is not asking for a figure, count, balance, rank, ranking or leaderboard from the server's records (a list of things, like someone's badges or the house bank, is a list, not a metric)"},
    }
    for m in METRICS.values():
        crit[m.key] = {"what": m.what, "examples": list(m.examples)}
    return crit


def lists_for_jev() -> Dict[str, dict]:
    crit: Dict[str, dict] = {
        "none": {"what": "The message is not asking for a list of things or a fact sheet from the server's records"},
    }
    for l in LISTS.values():
        crit[l.key] = {"what": l.what, "examples": list(l.examples)}
    return crit


def sources_for_jev() -> Dict[str, Any]:
    crit: Dict[str, Any] = {"all": "No particular source named: everything earned or spent"}
    for k, (label, _) in SOURCES.items():
        crit[k] = label
    return crit
