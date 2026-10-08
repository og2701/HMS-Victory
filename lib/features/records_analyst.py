"""Answers questions about the server's records that the fixed menu in data_queries can't.

data_queries is a registry: sixty-odd metrics times a handful of shapes, plus a few dozen lists. It is
exact and cheap, and it answers most questions, but a registry only covers what someone thought to add.
"On what game?" under the biggest-win table, "what happened to it after it was won?", "how much has the
house made off mines this month?" all fall between the entries, and the plain chat reply has no figures,
so it shrugs. This module is the fallback for those: a model gets read-only SQL over the bot's own
database, a map of what each table holds, the member directory and the conversation so far, runs a few
queries of its own, and answers only from what came back.

It can only ever read, and only what's on the map:
- the connection is opened read-only and then switched to query_only;
- an SQLite authorizer refuses every table not in TABLES, every pragma, attach and write, and the
  file-reading and extension functions, so a query the model writes can't reach anything else;
- the message archive is only visible through a view limited to channels @everyone can read, built from
  the live server each time, so staff channels never leak;
- moderation data (detections, restrictions, join watch, tickets) and private game state (Big Brother
  diaries, votes and missions, notifications) aren't on the map at all;
- each query is cut off after a few seconds and a few dozen rows.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

try:
    import config as _config
except Exception:  # pragma: no cover - config always imports in the bot
    _config = None

MODEL = getattr(_config, "RECORDS_ANALYST_MODEL", "gpt-5.4-mini")
MAX_STEPS = 8                 # model turns; each may run several queries
MAX_QUERIES = 12
QUERY_SECONDS = 4.0
MAX_ROWS = 60
CELL_CHARS = 180
RESULT_CHARS = 6000
TOTAL_SECONDS = 60.0

# What each readable table holds. Amounts are UKPence (UKP); ids are Discord ids stored as TEXT; times are unix
# seconds (UTC) unless a column says otherwise. Anything not listed here is refused by the authorizer.
TABLES: Dict[str, str] = {
    "ukpence": "Current UKP balance per member (user_id, balance).",
    "user_transactions": "The UKP ledger: every credit/debit. user_id, ts, amount (+credit/-debit), balance_after, reason (free text such as 'Mines bet', 'Mines cashout', 'Slots win', 'Chatting activity reward', 'Pay', 'Shop purchase: Lucky Dip', 'HMS Wordle solve', 'HMS Crossword solve', 'Tree watering reward', 'DISBOARD bump reward', 'Inactivity tax (60+ days dormant)', 'Reclaimed: left the server'), counterparty_id (the other member for payments), source ('live' or 'backfill'). Use it to follow money: what someone did after a win, where a balance came from.",
    "balance_history": "Balance snapshots over time (user_id, ts, balance).",
    "casino_results": "One row per finished house-casino round: user_id, game (db names below), bet, staked (total risked incl. doubles), payout, net (payout - staked), outcome, result ('win'/'loss'/'push'), timestamp.",
    "bank": "The house bank, one row: balance, total_revenue, total_tax_collected, chat_reward_pot, and total_<game>_in / total_<game>_out per casino game (in = taken from players, out = paid to them).",
    "pvp_results": "Member-vs-member games: game ('connect4', 'battleship', 'rps' = rock paper scissors, 'countdown', 'broadside'), winner_id, loser_id, stake, outcome ('win' or 'draw'), timestamp.",
    "connect4_results": "Older Connect 4 results: winner_id, loser_id, stake, timestamp.",
    "kart_races": "UKP Kart races (kept from 8 Oct 2026): id, mode ('race' vs people or 'practice'), track, laps, stake, pot, outcome ('race' or 'refund'), host_id, channel_id, started, ended.",
    "kart_results": "Every kart in each UKP Kart race: race_id, user_id (NULL for a CPU, which has a name such as 'Mary Berry'), name, car, place, finish_time and best_lap (seconds), payout, left_early.",
    "game_records": "Countdown and Broadside games kept whole (from 8 Oct 2026): game, id, mode, stake, outcome, ended, data (JSON of the rounds, words and moves; use json_extract).",
    "game_record_players": "Each player's place, result and payout in a kept Countdown or Broadside game: game, id, user_id, place, result, payout.",
    "game_transfers": "UKP moved between players by PvP wagers: loser_id, winner_id, amount, timestamp.",
    "pay_transfers": "/pay payments: payer_id, recipient_id, amount, timestamp.",
    "xp": "XP per member (user_id, xp, last_xp_time).",
    "member_profile": "Per member: first_seen, last_seen (unix), messages (all-time message count), link_messages.",
    "badges": "Badge catalogue: id, name, description, icon_path (an emoji), rarity. Secret badges have description '[REDACTED]': never reveal how to earn them.",
    "user_badges": "Who holds which badge: user_id, badge_id, awarded_at.",
    "badge_rewards": "UKP paid for badges: user_id, badge_id, amount, paid_at.",
    "shutcoins": "Shutcoin balance per member (user_id, balance).",
    "shutcoin_ledger": "Shutcoin movements since the ledger began: user_id, ts, amount, reason.",
    "shut_counts": "How many times each member has been shut (user_id, count).",
    "shop_purchases": "Shop buys: user_id, item_id, quantity, price_paid, purchase_time (unix).",
    "shop_inventory": "Shop stock: item_id, quantity, max_quantity, auto_restock, restock_amount, last_restock.",
    "county_instances": "Counties caught or owned: user_id, county, caught_at, channel_id, obtained ('catch', 'gift', ...), clout_bonus, grit_bonus.",
    "county_transfers": "County gifts/trades: instance_id, from_user, to_user, transferred_at.",
    "lottery_rounds": "Lottery draws: status, ticket_price, ticket_cap, rake_pct, draw_ts, drawn_at, winner_id, winning_ticket, pot, prize.",
    "lottery_entries": "Lottery tickets: round_id, user_id, tickets.",
    "bonds": "Savings bonds: user_id, principal, rate_pct, term_days, opened_ts, matures_ts, status.",
    "scheduled_predictions": "Prediction markets: creator_id, title, opt1, opt2, options_json, scheduled_ts, status, created_at.",
    "circulation_snapshots": "Total UKP in circulation over time (timestamp, total_circulation).",
    "daily_summaries": "One row per day (date 'YYYY-MM-DD'): data is a JSON object of that day's server stats (joins, leaves, messages and so on); use json_extract.",
    "climb_runs": "The Climb (activity game) runs: user_id, date, started, ended, height, game_time, bounces, earned.",
    "climb_days": "The Climb daily bests: user_id, date, best, paid, runs.",
    "paperboy_runs": "Paperboy (activity game) runs: user_id, date, started, ended, score, game_time, papers, earned.",
    "paperboy_days": "Paperboy daily bests: user_id, date, best, paid, runs.",
    "paperboy_jobs": "Paperboy daily jobs: user_id, date, progress (JSON), done.",
    "paperboy_kit": "Paperboy wardrobe: user_id, owned (JSON list), wearing (JSON), spent, bonus.",
    "spitfire_runs": "Spitfire (activity game) runs: user_id, date, started, ended, score, game_time, flaps, earned.",
    "spitfire_days": "Spitfire daily bests: user_id, date, best, paid, runs.",
    "pennyfalls_cups": "Davy Jones' Locker (penny falls) sessions: user_id, started, ended, bought, staked, payout, coins_won, golds_won, coins_lost.",
    "iceberg": "The server iceberg meme: text, level (depth tier).",
    "roast_targets": "How often each member has been roasted by the bot (user_id, count).",
    "roast_usage": "Roasts requested per member per day (user_id, date, count).",
    "recent_roasts": "Recent roasts the bot posted: target_id, target_name, roast_text, created_at.",
    "recent_glazes": "Recent glazes (compliments) the bot posted: target_id, target_name, glaze_text, created_at.",
    "user_rank_customization": "Rank card styling: user_id, background, primary_color, secondary_color, tertiary_color, title.",
    "members": "The server's current members (user_id, name = display name, username). Join it to turn ids into names; anyone missing has left the server.",
    "channels": "Text channels everyone can read (channel_id, name).",
    "messages": "The last ~30 days of messages in public channels: message_id, channel_id, user_id, content, ts. Join channels for the channel name.",
}

# casino_results.game values and what members call them.
CASINO_GAME_NAMES = {
    "blackjack": "blackjack", "higherlower": "higher or lower", "reddog": "red dog", "roulette": "roulette",
    "slots": "slots", "videopoker": "video poker", "tcp": "three card poker", "mines": "mines", "chest": "chest",
    "penalty": "penalty shootout", "darts": "darts", "glass": "glass bridge", "blockade": "blockade run",
    "plinko": "plinko", "pennyfalls": "penny falls (Davy Jones' Locker)",
}

# JSON data files the model may read, by config attribute, with what they hold.
JSON_FILES: Dict[str, Tuple[str, str]] = {
    "hall_of_fame": ("HALL_OF_FAME_FILE", "Hall of Fame entries (starred messages)"),
    "predictions": ("PREDICTIONS_FILE", "prediction markets and bets"),
    "prediction_streaks": ("PREDICTION_STREAKS_FILE", "members' prediction win streaks"),
    "skyrim_profiles": ("SKYRIM_PROFILES_FILE", "Skyrim activity characters per member (level/xp, gold/septims, kills, deaths, delves, dragons)"),
    "skyrim_graveyard": ("SKYRIM_GRAVEYARD_FILE", "fallen Skyrim adventurers"),
    "skyrim_daily": ("SKYRIM_DAILY_FILE", "the Skyrim shared dungeon's results by date then member (stone, state, satchel, kills, rooms)"),
    "penny_falls_boards": ("ACTIVITY_PENNYFALLS_FILE", "each member's Davy Jones' Locker coins, golds and today's net"),
    "skyrim_worldboss": ("SKYRIM_WORLDBOSS_FILE", "this week's Skyrim world boss hunt"),
    "wordle": ("WORDLE_STATE_FILE", "TODAY's HMS Wordle: each player's guesses, solved, timings (past solves are in the ledger as 'HMS Wordle solve')"),
    "crossword": ("CROSSWORD_STATE_FILE", "TODAY's HMS Crossword: each player's progress (past solves are in the ledger as 'HMS Crossword solve')"),
    "counties": ("COUNTY_STATE_FILE", "county spawning state"),
    "game_pinnacles": ("GAME_PINNACLE_FILE", "per-member top-tier wins in the newer casino games"),
    "earned_sources": ("EARNED_SOURCES_FILE", "UKP earned per member by source"),
    "economy_metrics": ("ECONOMY_METRICS_FILE", "economy-wide metrics"),
    "tree_watering": ("TREE_WATER_FILE", "tree watering rewards"),
    "bumps": ("BUMP_REWARD_FILE", "DISBOARD bump rewards"),
    "benefits": ("BENEFITS_FILE", "benefits claims"),
    "morning_person": ("MORNING_PERSON_COUNTS_FILE", "early-morning message counts"),
    "night_owl": ("NIGHT_OWL_COUNTS_FILE", "late-night message counts"),
    "weekend_warrior": ("WEEKEND_WARRIOR_COUNTS_FILE", "weekend message counts"),
}

_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer", "zipfile", "sqlar_compress", "sqlar_uncompress"}
# message_archive itself is never on the list: only the `messages` view may read it (see _authorize).
_ALLOWED_TABLES = set(TABLES) - {"messages"}


@dataclass
class Scope:
    """Who and where, from the live server: names for ids, and the channels whose messages are public."""
    members: List[Tuple[str, str, str]] = field(default_factory=list)      # (user_id, display name, username)
    public_channels: List[Tuple[str, str]] = field(default_factory=list)   # (channel_id, name)


@dataclass
class Answer:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    queries: List[str] = field(default_factory=list)


class ReadOnlyRecords:
    """A read-only, allowlisted view of the bot's database for one question."""

    def __init__(self, path: str, scope: Scope):
        self.conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True, timeout=5, check_same_thread=False)
        c = self.conn
        c.execute("CREATE TEMP TABLE members (user_id TEXT PRIMARY KEY, name TEXT, username TEXT)")
        c.executemany("INSERT OR REPLACE INTO temp.members VALUES (?, ?, ?)", [(str(a), b, u) for a, b, u in scope.members])
        c.execute("CREATE TEMP TABLE channels (channel_id TEXT PRIMARY KEY, name TEXT)")
        c.executemany("INSERT OR REPLACE INTO temp.channels VALUES (?, ?)", [(str(a), b) for a, b in scope.public_channels])
        has_archive = c.execute("SELECT 1 FROM main.sqlite_master WHERE type='table' AND name='message_archive'").fetchone()
        if has_archive:
            c.execute("CREATE TEMP VIEW messages AS SELECT m.message_id, m.channel_id, m.user_id, m.content, m.ts "
                      "FROM main.message_archive m WHERE m.channel_id IN (SELECT channel_id FROM temp.channels)")
        else:
            c.execute("CREATE TEMP TABLE messages (message_id TEXT, channel_id TEXT, user_id TEXT, content TEXT, ts INTEGER)")
        c.commit()
        self._real_tables = {r[0].lower() for r in c.execute(
            "SELECT name FROM main.sqlite_master WHERE type IN ('table', 'view') UNION SELECT name FROM temp.sqlite_master WHERE type IN ('table', 'view')")}
        c.execute("PRAGMA query_only = ON")
        c.set_authorizer(self._authorize)
        self._deadline = 0.0
        c.set_progress_handler(self._progress, 5000)

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    def _progress(self) -> int:
        return 1 if self._deadline and time.monotonic() > self._deadline else 0

    def _authorize(self, action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = (arg1 or "").lower()
            if (dbname or "main") not in ("main", "temp") or table.startswith(("sqlite_", "pragma_")):
                return sqlite3.SQLITE_DENY
            if table == "message_archive":
                return sqlite3.SQLITE_OK if (source or "").lower() == "messages" else sqlite3.SQLITE_DENY
            if table in _ALLOWED_TABLES or table == "messages":
                return sqlite3.SQLITE_OK
            # A name that isn't a real table is the query's own WITH clause; a real one has to be on the map.
            return sqlite3.SQLITE_DENY if table in self._real_tables else sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
        if action == getattr(sqlite3, "SQLITE_RECURSIVE", -1):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def run(self, sql: str) -> str:
        """Run one SELECT and render the rows as text the model can read. Errors come back as text too."""
        q = (sql or "").strip().rstrip(";").strip()
        if not q:
            return "error: empty query"
        if ";" in q:
            return "error: one statement at a time"
        if not re.match(r"(?is)^(select|with)\b", q):
            return "error: only SELECT (or WITH ... SELECT) queries are allowed"
        self._deadline = time.monotonic() + QUERY_SECONDS
        try:
            cur = self.conn.execute(q)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchmany(MAX_ROWS + 1)
        except sqlite3.DatabaseError as e:
            msg = str(e)
            if "interrupted" in msg:
                msg = f"query took longer than {QUERY_SECONDS:.0f}s; narrow it down"
            elif "not authorized" in msg or "prohibited" in msg:
                msg = "not authorized: that table or function isn't available (see the table map)"
            return f"error: {msg}"
        finally:
            self._deadline = 0.0
        more = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        if not rows:
            return " | ".join(cols) + "\n(no rows)" if cols else "(no rows)"

        def cell(v: Any) -> str:
            s = "NULL" if v is None else str(v)
            s = " ".join(s.split())
            return s if len(s) <= CELL_CHARS else s[: CELL_CHARS - 1] + "…"
        lines = [" | ".join(cols)] + [" | ".join(cell(v) for v in r) for r in rows]
        if more:
            lines.append(f"(more rows not shown; at most {MAX_ROWS} come back, so aggregate or add LIMIT/WHERE)")
        out = "\n".join(lines)
        return out if len(out) <= RESULT_CHARS else out[:RESULT_CHARS] + "\n(cut off: too much text, narrow the query)"


def read_data_file(name: str, path: str = "") -> str:
    """One allowed JSON file, optionally drilled into by a dot path, as truncated JSON with its keys listed."""
    entry = JSON_FILES.get((name or "").strip())
    if entry is None:
        return f"error: unknown file; choose from {', '.join(sorted(JSON_FILES))}"
    try:
        from lib.features.data_queries import _json
        data = _json(entry[0])
    except Exception as e:
        return f"error: couldn't read it ({e})"
    if data is None:
        return "error: the file is missing or empty"
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(data, dict) and part in data:
            data = data[part]
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return f"error: no '{part}' at that point"
    head = ""
    if isinstance(data, dict):
        keys = list(data)
        head = f"({len(keys)} keys: {', '.join(map(str, keys[:40]))}{' ...' if len(keys) > 40 else ''})\n"
    elif isinstance(data, list):
        head = f"(list of {len(data)})\n"
    body = json.dumps(data, ensure_ascii=False, default=str)
    return head + (body if len(body) <= RESULT_CHARS else body[:RESULT_CHARS] + " ...(cut off; drill in with a path)")


TOOLS = [
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Run ONE read-only SQLite SELECT (or WITH ... SELECT) against the server's records and get the rows back. At most 60 rows return, so aggregate, ORDER BY and LIMIT. Join `members` for names.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "read_data_file",
        "description": "Read one of the bot's JSON data files (see the list), optionally drilling in with a dot path such as '1234567890.level'. Big files are cut off, so drill in.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": sorted(JSON_FILES)},
                                                        "path": {"type": "string"}}, "required": ["name"]},
    }},
]


def system_prompt(now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    tables = "\n".join(f"- {t}: {d}" for t, d in TABLES.items())
    games = ", ".join(f"{k} = {v}" for k, v in CASINO_GAME_NAMES.items())
    files = "\n".join(f"- {k}: {d}" for k, (_, d) in JSON_FILES.items())
    return f"""You are the records desk of HMS Victory ("Vic"), the bot of UK Place, a British Discord server with its own economy.
A member has asked something about the server that needs its records. Find the answer with the tools, then reply.

RULES
- Every figure, name, date and game you state must come from a query result. Never guess, estimate or round up a story.
- If the records can't answer it, say so plainly in one line and give the nearest thing they DO show. Don't apologise at length.
- Resolve follow-ups from the conversation: "it", "that win", "them", "on what game?" refer to what was just discussed, usually the bot's last answer in `replied_to`.
- Use names, never raw user ids. Get names by joining `members` (user_id is TEXT). Someone with no `members` row has left the server: say "someone who's left".
- Money is UKPence, written like "40,080 UKP". Timestamps are unix seconds UTC; write dates like "21 Jul 2026" and times in UK time.
- Moderation (warnings, bans, mutes, detections, staff actions) and private game data are not available: say that isn't something you can share.
- Never reveal how to earn a secret badge.
- Be efficient: plan, then query. Aggregate in SQL rather than pulling raw rows.

REPLY STYLE
- Dry, British, a touch of wit, but the facts come first. One to three sentences.
- For a ranked list or several figures, put them in a ``` code block (at most 10 lines) under one short line.
- No preamble, no mention of SQL, queries, tables or tools. Don't open by addressing the asker.

TABLES (amounts in UKP, ids are TEXT Discord ids, times are unix seconds UTC unless noted)
{tables}

casino_results.game values: {games}.

JSON FILES (read_data_file)
{files}

NOW: {now.strftime('%A %d %B %Y, %H:%M UTC')} (unix {int(now.timestamp())})."""


def user_message(question: str, *, asker: Tuple[str, str], mentioned: Sequence[Tuple[str, str]] = (),
                 replied_to: Optional[Tuple[str, str]] = None, recent_chat: str = "") -> str:
    payload: Dict[str, Any] = {
        "question": question,
        "asked_by": {"name": asker[0], "user_id": str(asker[1])},
    }
    if mentioned:
        payload["mentioned_users"] = [{"name": n, "user_id": str(i)} for n, i in mentioned]
    if replied_to:
        payload["replied_to"] = {"author": replied_to[0], "text": (replied_to[1] or "")[:1500]}
    if recent_chat:
        payload["recent_chat"] = recent_chat[-1500:]
    return json.dumps(payload, ensure_ascii=False)


# complete(messages, tools) -> {"content": str|None, "tool_calls": [{"id", "name", "arguments"}], "usage": (prompt, completion)}
Completer = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Awaitable[Dict[str, Any]]]


async def openai_complete(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    from openai import AsyncOpenAI
    key = os.getenv("OPENAI_TOKEN") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("no OpenAI key")
    client = AsyncOpenAI(api_key=key, max_retries=1, timeout=40.0)
    request = dict(model=MODEL, messages=messages, tools=tools, max_completion_tokens=2500)
    try:
        resp = await client.chat.completions.create(**request, reasoning_effort="low")
    except Exception as e:
        if "reasoning_effort" not in str(e):
            raise
        resp = await client.chat.completions.create(**request)
    msg = resp.choices[0].message
    usage = getattr(resp, "usage", None)
    return {
        "content": msg.content,
        "tool_calls": [{"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments} for tc in (msg.tool_calls or [])],
        "usage": (int(getattr(usage, "prompt_tokens", 0) or 0), int(getattr(usage, "completion_tokens", 0) or 0)),
    }


async def answer(question: str, *, scope: Scope, asker: Tuple[str, str], mentioned: Sequence[Tuple[str, str]] = (),
                 replied_to: Optional[Tuple[str, str]] = None, recent_chat: str = "", db_path: Optional[str] = None,
                 complete: Optional[Completer] = None, now: Optional[datetime] = None) -> Optional[Answer]:
    """Research one question against the records. None when nothing usable came back (the caller falls through)."""
    complete = complete or openai_complete
    if db_path is None:
        from database import DB_FILE
        db_path = DB_FILE
    try:
        db = await asyncio.to_thread(ReadOnlyRecords, db_path, scope)
    except Exception as e:
        logger.warning("Records analyst couldn't open the database: %s", e)
        return None
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt(now)},
        {"role": "user", "content": user_message(question, asker=asker, mentioned=mentioned, replied_to=replied_to, recent_chat=recent_chat)},
    ]
    result = Answer(text="")
    started = time.monotonic()
    try:
        for step in range(MAX_STEPS):
            if time.monotonic() - started > TOTAL_SECONDS:
                logger.warning("Records analyst ran out of time on %r", question[:80])
                return None
            out = await complete(messages, TOOLS if len(result.queries) < MAX_QUERIES else [])
            p, c = out.get("usage") or (0, 0)
            result.prompt_tokens += int(p)
            result.completion_tokens += int(c)
            calls = out.get("tool_calls") or []
            if not calls:
                text = (out.get("content") or "").strip()
                if not text:
                    return None
                result.text = text
                return result
            messages.append({"role": "assistant", "content": out.get("content") or None, "tool_calls": [
                {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": tc["arguments"]}} for tc in calls]})
            for tc in calls:
                try:
                    args = json.loads(tc.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                if tc["name"] == "run_sql" and len(result.queries) < MAX_QUERIES:
                    q = str(args.get("query") or "")
                    result.queries.append(q)
                    reply = await asyncio.to_thread(db.run, q)
                elif tc["name"] == "read_data_file":
                    reply = await asyncio.to_thread(read_data_file, str(args.get("name") or ""), str(args.get("path") or ""))
                else:
                    reply = "error: no more queries this time; answer from what you have"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": reply})
        logger.warning("Records analyst used every step on %r", question[:80])
        return None
    finally:
        db.close()
