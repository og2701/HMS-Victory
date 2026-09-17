"""Big Brother - a temporary two-week house event run by a host from a control panel.

Everything here is gated on config.BIG_BROTHER_ENABLED and reads the rest of its
settings from the BIG_BROTHER_* block in config.py, so the event can be switched off
without touching code once it's over.

The one design rule that shapes the whole module: the control channel is visible to
staff, and some staff are playing. So the panel posted there only ever shows phase and
counts. Anything that would give a player an edge (who nominated whom, vote tallies,
diary entries, mission briefs and outcomes, exposures) is sent to the host's DMs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable, Iterable, Optional

import discord

import config
from database import DatabaseManager

log = logging.getLogger(__name__)

ACCENT = 0x8E44AD  # Big Brother purple
EYE = "👁️"
STATUS_IN = "in"
STATUS_EVICTED = "evicted"
STATUS_WINNER = "winner"
KIND_NOMINATIONS = "nominations"
KIND_VOTE = "vote"
STATE_PANEL_MSG = "panel_message_id"
STATE_HOUSE_PANEL_MSG = "house_panel_message_id"
STATE_LAST_NOM_TALLY = "last_nomination_tally"
STATE_LAST_VOTE_RESULT = "last_vote_result"
STATE_GAME_STARTED_AT = "game_started_at"
STATE_HOUSE_MSGS_SINCE_PANEL = "house_msgs_since_panel"
STATE_HOUSE_SILENT = "house_silent"
STATE_HOUSE_PANEL_SIG = "house_panel_signature"  # what the house panel last showed; a change re-posts it
HOUSE_PANEL_REPOST_EVERY = 20  # chat messages in the house before the panel is re-posted at the bottom
_repost_lock = asyncio.Lock()

_tables_ready = False
_client_ref: Optional[discord.Client] = None  # set on ready; lets channel sends echo to the host


# ---------------------------------------------------------------------------
# Config accessors (read live so a config reload or test override takes effect)
# ---------------------------------------------------------------------------

def enabled() -> bool:
    return bool(getattr(config, "BIG_BROTHER_ENABLED", False))


def host_id() -> int:
    return int(getattr(config, "BIG_BROTHER_HOST_ID", 0))


def operator_ids() -> set[int]:
    return {int(x) for x in getattr(config, "BIG_BROTHER_OPERATOR_IDS", set())}


def is_operator(user_id: int) -> bool:
    return int(user_id) in operator_ids()


def house_channel_id() -> int:
    return int(getattr(config, "BIG_BROTHER_HOUSE_CHANNEL", 0))


def control_channel_id() -> int:
    return int(getattr(config, "BIG_BROTHER_CONTROL_CHANNEL", 0))


def vote_channel_id() -> int:
    return int(getattr(config, "BIG_BROTHER_VOTE_CHANNEL", 0) or house_channel_id())


def housemate_role_id() -> Optional[int]:
    rid = getattr(config, "BIG_BROTHER_HOUSEMATE_ROLE", None)
    return int(rid) if rid else None


def nominations_each() -> int:
    return max(1, int(getattr(config, "BIG_BROTHER_NOMINATIONS_PER_HOUSEMATE", 2)))


def prize_ukp() -> int:
    return int(getattr(config, "BIG_BROTHER_PRIZE_UKP", 0))


def quiet_hours() -> int:
    return int(getattr(config, "BIG_BROTHER_QUIET_HOURS", 48))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def ensure_tables() -> None:
    """Create the event's tables on first use. Kept here rather than in database.init_db
    so the whole feature is one file that can be deleted after the event."""
    global _tables_ready
    if _tables_ready:
        return
    with DatabaseManager.transaction() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS bb_housemates (
            user_id TEXT PRIMARY KEY, joined_at INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'in', evicted_at INTEGER,
            immune INTEGER NOT NULL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', opened_at INTEGER NOT NULL,
            closed_at INTEGER, channel_id TEXT, message_id TEXT, nominees TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_nominations (
            round_id INTEGER NOT NULL, nominator_id TEXT NOT NULL, nominee_id TEXT NOT NULL,
            created_at INTEGER NOT NULL, UNIQUE(round_id, nominator_id, nominee_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_votes (
            round_id INTEGER NOT NULL, voter_id TEXT NOT NULL, nominee_id TEXT NOT NULL,
            created_at INTEGER NOT NULL, UNIQUE(round_id, voter_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_diary (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            anonymous INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL, created_at INTEGER NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_missions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, brief TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active', created_at INTEGER NOT NULL, resolved_at INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL,
            answer TEXT, status TEXT NOT NULL DEFAULT 'open', winner_id TEXT, message_id TEXT,
            created_at INTEGER NOT NULL, resolved_at INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_activity (
            user_id TEXT PRIMARY KEY, last_message_at INTEGER NOT NULL,
            messages INTEGER NOT NULL DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS bb_state (
            key TEXT PRIMARY KEY, value TEXT)""")
        # Unified timeline of everything that happened, for the post-event rundown.
        c.execute("""CREATE TABLE IF NOT EXISTS bb_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, kind TEXT NOT NULL,
            actor_id TEXT, target_id TEXT, payload TEXT)""")
        # Full transcript of the house channel (the message archive elsewhere is rolling).
        c.execute("""CREATE TABLE IF NOT EXISTS bb_messages (
            message_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT,
            at INTEGER NOT NULL, attachments INTEGER NOT NULL DEFAULT 0, reply_to TEXT,
            thread_id TEXT)""")
        # Private "snug" threads: two or more housemates talking strategy with Big Brother listening.
        c.execute("""CREATE TABLE IF NOT EXISTS bb_snugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT NOT NULL, opened_by TEXT,
            members TEXT NOT NULL, created_at INTEGER NOT NULL)""")
        # Read receipts for DMs that carry an "I've seen this" button.
        c.execute("""CREATE TABLE IF NOT EXISTS bb_acks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, label TEXT NOT NULL,
            sent_at INTEGER NOT NULL, acked_at INTEGER)""")
        # Columns added after the first deploy; harmless when they already exist.
        for stmt in ("ALTER TABLE bb_housemates ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0",
                     "ALTER TABLE bb_messages ADD COLUMN thread_id TEXT"):
            try:
                c.execute(stmt)
            except Exception:
                pass
    _tables_ready = True


def _now() -> int:
    return int(time.time())


def get_state(key: str, default=None):
    ensure_tables()
    row = DatabaseManager.fetch_one("SELECT value FROM bb_state WHERE key = ?", (key,))
    if not row or row[0] is None:
        return default
    try:
        value = json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]
    return default if value is None else value


def set_state(key: str, value) -> None:
    ensure_tables()
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO bb_state (key, value) VALUES (?, ?)",
        (key, json.dumps(value)))


def log_event(kind: str, actor: Optional[int] = None, target: Optional[int] = None, **payload) -> None:
    """Append to the timeline. Never raises: logging must not break the game."""
    try:
        ensure_tables()
        DatabaseManager.execute(
            "INSERT INTO bb_events (at, kind, actor_id, target_id, payload) VALUES (?, ?, ?, ?, ?)",
            (_now(), kind, str(actor) if actor is not None else None,
             str(target) if target is not None else None, json.dumps(payload) if payload else None))
    except Exception:
        log.exception("Big Brother: could not log event %s", kind)


def store_message(message: discord.Message) -> None:
    ensure_tables()
    in_thread = isinstance(message.channel, discord.Thread)
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO bb_messages (message_id, user_id, content, at, attachments, reply_to, thread_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(message.id), str(message.author.id), message.content or "",
         int(message.created_at.timestamp()), len(message.attachments),
         str(message.reference.message_id) if message.reference and message.reference.message_id else None,
         str(message.channel.id) if in_thread else None))


def events() -> list[dict]:
    ensure_tables()
    rows = DatabaseManager.fetch_all("SELECT id, at, kind, actor_id, target_id, payload FROM bb_events ORDER BY id")
    out = []
    for r in rows:
        try:
            payload = json.loads(r[5]) if r[5] else {}
        except (TypeError, ValueError):
            payload = {"raw": r[5]}
        out.append({"id": r[0], "at": r[1], "kind": r[2], "actor_id": int(r[3]) if r[3] else None,
                    "target_id": int(r[4]) if r[4] else None, **payload})
    return out


def game_started_at() -> Optional[int]:
    v = get_state(STATE_GAME_STARTED_AT)
    return int(v) if v else None


def game_started() -> bool:
    return game_started_at() is not None


def house_silent() -> bool:
    return bool(get_state(STATE_HOUSE_SILENT, False))


def house_unlocked() -> bool:
    """The house panel works once the game has started, or while the test override is on."""
    return game_started() or bool(getattr(config, "BIG_BROTHER_HOUSE_PANEL_UNLOCKED", False))


# --- housemates ---

def housemates(status: Optional[str] = STATUS_IN) -> list[int]:
    ensure_tables()
    if status is None:
        rows = DatabaseManager.fetch_all("SELECT user_id FROM bb_housemates ORDER BY joined_at")
    else:
        rows = DatabaseManager.fetch_all(
            "SELECT user_id FROM bb_housemates WHERE status = ? ORDER BY joined_at", (status,))
    return [int(r[0]) for r in rows]


def is_housemate(user_id: int) -> bool:
    ensure_tables()
    row = DatabaseManager.fetch_one(
        "SELECT 1 FROM bb_housemates WHERE user_id = ? AND status = ?", (str(user_id), STATUS_IN))
    return row is not None


def immune_ids() -> set[int]:
    ensure_tables()
    rows = DatabaseManager.fetch_all(
        "SELECT user_id FROM bb_housemates WHERE status = ? AND immune = 1", (STATUS_IN,))
    return {int(r[0]) for r in rows}


def db_add_housemate(user_id: int) -> bool:
    """Returns True if newly added (or re-entered after eviction)."""
    ensure_tables()
    if is_housemate(user_id):
        return False
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO bb_housemates (user_id, joined_at, status, evicted_at, immune) "
        "VALUES (?, ?, ?, NULL, 0)", (str(user_id), _now(), STATUS_IN))
    return True


def db_set_status(user_id: int, status: str) -> None:
    ensure_tables()
    DatabaseManager.execute(
        "UPDATE bb_housemates SET status = ?, evicted_at = ?, immune = 0 WHERE user_id = ?",
        (status, _now() if status == STATUS_EVICTED else None, str(user_id)))


def db_toggle_immunity(user_id: int) -> bool:
    """Flip immunity, return the new value."""
    ensure_tables()
    row = DatabaseManager.fetch_one("SELECT immune FROM bb_housemates WHERE user_id = ?", (str(user_id),))
    new = 0 if (row and row[0]) else 1
    DatabaseManager.execute("UPDATE bb_housemates SET immune = ? WHERE user_id = ?", (new, str(user_id)))
    return bool(new)


def tokens_of(user_id: int) -> int:
    ensure_tables()
    row = DatabaseManager.fetch_one("SELECT tokens FROM bb_housemates WHERE user_id = ?", (str(user_id),))
    return int(row[0]) if row and row[0] else 0


def grant_token(user_id: int, n: int = 1) -> int:
    ensure_tables()
    DatabaseManager.execute("UPDATE bb_housemates SET tokens = tokens + ? WHERE user_id = ?", (int(n), str(user_id)))
    return tokens_of(user_id)


def spend_token(user_id: int) -> bool:
    """Atomically take one token; False if they had none."""
    ensure_tables()
    changed = DatabaseManager.execute(
        "UPDATE bb_housemates SET tokens = tokens - 1 WHERE user_id = ? AND tokens > 0", (str(user_id),))
    return bool(changed)


def set_immune(user_id: int, immune: bool) -> None:
    DatabaseManager.execute("UPDATE bb_housemates SET immune = ? WHERE user_id = ?",
                            (1 if immune else 0, str(user_id)))


def clear_all_immunity() -> list[int]:
    """Immunity covers one nominations round; called when that round closes."""
    was = sorted(immune_ids())
    DatabaseManager.execute("UPDATE bb_housemates SET immune = 0")
    return was


def create_ack(user_id: int, label: str) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_acks (user_id, label, sent_at) VALUES (?, ?, ?)", (str(user_id), label[:200], _now()))


def mark_ack(ack_id: int, user_id: int) -> Optional[str]:
    """Record the press; returns the label the first time, None if already acked or not theirs."""
    ensure_tables()
    row = DatabaseManager.fetch_one("SELECT user_id, label, acked_at FROM bb_acks WHERE id = ?", (int(ack_id),))
    if not row or str(row[0]) != str(user_id) or row[2]:
        return None
    DatabaseManager.execute("UPDATE bb_acks SET acked_at = ? WHERE id = ?", (_now(), int(ack_id)))
    return row[1]


def pending_acks() -> list[dict]:
    ensure_tables()
    rows = DatabaseManager.fetch_all(
        "SELECT id, user_id, label, sent_at FROM bb_acks WHERE acked_at IS NULL ORDER BY sent_at")
    return [{"id": r[0], "user_id": int(r[1]), "label": r[2], "sent_at": r[3]} for r in rows]


def add_snug(thread_id: int, opened_by: Optional[int], members: Iterable[int]) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_snugs (thread_id, opened_by, members, created_at) VALUES (?, ?, ?, ?)",
        (str(thread_id), str(opened_by) if opened_by else None, json.dumps([int(m) for m in members]), _now()))


def snugs() -> list[dict]:
    ensure_tables()
    rows = DatabaseManager.fetch_all("SELECT id, thread_id, opened_by, members, created_at FROM bb_snugs ORDER BY id")
    return [{"id": r[0], "thread_id": int(r[1]), "opened_by": int(r[2]) if r[2] else None,
             "members": json.loads(r[3]), "created_at": r[4]} for r in rows]


def recent_snug_by(user_id: int, within_seconds: int) -> bool:
    ensure_tables()
    row = DatabaseManager.fetch_one(
        "SELECT 1 FROM bb_snugs WHERE opened_by = ? AND created_at > ? LIMIT 1",
        (str(user_id), _now() - int(within_seconds)))
    return row is not None


def replace_nominee(round_id: int, old: int, new: int) -> None:
    """Save-and-replace: swap a nominee on an open vote and void the votes cast for them."""
    rnd = get_round(round_id)
    if not rnd:
        return
    nominees = [new if n == old else n for n in rnd["nominees"]]
    with DatabaseManager.transaction() as c:
        c.execute("UPDATE bb_rounds SET nominees = ? WHERE id = ?", (json.dumps(nominees), int(round_id)))
        c.execute("DELETE FROM bb_votes WHERE round_id = ? AND nominee_id = ?", (int(round_id), str(old)))


# --- rounds ---

def open_round(kind: str) -> Optional[dict]:
    ensure_tables()
    row = DatabaseManager.fetch_one(
        "SELECT id, kind, status, opened_at, channel_id, message_id, nominees FROM bb_rounds "
        "WHERE kind = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (kind,))
    return _round_row(row)


def get_round(round_id: int) -> Optional[dict]:
    ensure_tables()
    row = DatabaseManager.fetch_one(
        "SELECT id, kind, status, opened_at, channel_id, message_id, nominees FROM bb_rounds WHERE id = ?",
        (int(round_id),))
    return _round_row(row)


def _round_row(row) -> Optional[dict]:
    if not row:
        return None
    nominees = []
    if row[6]:
        try:
            nominees = [int(x) for x in json.loads(row[6])]
        except (TypeError, ValueError):
            nominees = []
    return {"id": row[0], "kind": row[1], "status": row[2], "opened_at": row[3],
            "channel_id": int(row[4]) if row[4] else None,
            "message_id": int(row[5]) if row[5] else None, "nominees": nominees}


def create_round(kind: str, *, channel_id: Optional[int] = None, message_id: Optional[int] = None,
                 nominees: Optional[Iterable[int]] = None) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_rounds (kind, status, opened_at, channel_id, message_id, nominees) "
        "VALUES (?, 'open', ?, ?, ?, ?)",
        (kind, _now(), str(channel_id) if channel_id else None,
         str(message_id) if message_id else None,
         json.dumps([int(x) for x in nominees]) if nominees else None))


def set_round_message(round_id: int, channel_id: int, message_id: int) -> None:
    DatabaseManager.execute("UPDATE bb_rounds SET channel_id = ?, message_id = ? WHERE id = ?",
                            (str(channel_id), str(message_id), int(round_id)))


def close_round(round_id: int) -> None:
    DatabaseManager.execute("UPDATE bb_rounds SET status = 'closed', closed_at = ? WHERE id = ?",
                            (_now(), int(round_id)))


# --- nominations ---

def record_nominations(round_id: int, nominator_id: int, nominee_ids: Iterable[int]) -> None:
    with DatabaseManager.transaction() as c:
        c.execute("DELETE FROM bb_nominations WHERE round_id = ? AND nominator_id = ?",
                  (int(round_id), str(nominator_id)))
        for nid in nominee_ids:
            c.execute("INSERT OR IGNORE INTO bb_nominations (round_id, nominator_id, nominee_id, created_at) "
                      "VALUES (?, ?, ?, ?)", (int(round_id), str(nominator_id), str(nid), _now()))


def nominations_for(round_id: int) -> list[tuple[int, int]]:
    rows = DatabaseManager.fetch_all(
        "SELECT nominator_id, nominee_id FROM bb_nominations WHERE round_id = ? ORDER BY created_at",
        (int(round_id),))
    return [(int(a), int(b)) for a, b in rows]


def nominators_done(round_id: int) -> set[int]:
    rows = DatabaseManager.fetch_all(
        "SELECT DISTINCT nominator_id FROM bb_nominations WHERE round_id = ?", (int(round_id),))
    return {int(r[0]) for r in rows}


# --- votes ---

def cast_vote(round_id: int, voter_id: int, nominee_id: int) -> None:
    DatabaseManager.execute(
        "INSERT OR REPLACE INTO bb_votes (round_id, voter_id, nominee_id, created_at) VALUES (?, ?, ?, ?)",
        (int(round_id), str(voter_id), str(nominee_id), _now()))


def vote_tally(round_id: int) -> dict[int, int]:
    rows = DatabaseManager.fetch_all(
        "SELECT nominee_id, COUNT(*) FROM bb_votes WHERE round_id = ? GROUP BY nominee_id", (int(round_id),))
    return {int(a): int(b) for a, b in rows}


def vote_count(round_id: int) -> int:
    row = DatabaseManager.fetch_one("SELECT COUNT(*) FROM bb_votes WHERE round_id = ?", (int(round_id),))
    return int(row[0]) if row else 0


# --- diary / missions / challenges / activity ---

def add_diary(user_id: int, text: str, anonymous: bool) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_diary (user_id, anonymous, text, created_at) VALUES (?, ?, ?, ?)",
        (str(user_id), 1 if anonymous else 0, text, _now()))


def add_mission(user_id: int, brief: str) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_missions (user_id, brief, status, created_at) VALUES (?, ?, 'active', ?)",
        (str(user_id), brief, _now()))


def active_missions() -> list[dict]:
    ensure_tables()
    rows = DatabaseManager.fetch_all(
        "SELECT id, user_id, brief, created_at FROM bb_missions WHERE status = 'active' ORDER BY id")
    return [{"id": r[0], "user_id": int(r[1]), "brief": r[2], "created_at": r[3]} for r in rows]


def active_mission_for(user_id: int) -> Optional[dict]:
    ensure_tables()
    row = DatabaseManager.fetch_one(
        "SELECT id, user_id, brief, created_at FROM bb_missions WHERE status = 'active' AND user_id = ? "
        "ORDER BY id DESC LIMIT 1", (str(user_id),))
    return {"id": row[0], "user_id": int(row[1]), "brief": row[2], "created_at": row[3]} if row else None


def resolve_mission(mission_id: int, status: str) -> Optional[dict]:
    row = DatabaseManager.fetch_one("SELECT id, user_id, brief FROM bb_missions WHERE id = ?", (int(mission_id),))
    if not row:
        return None
    DatabaseManager.execute("UPDATE bb_missions SET status = ?, resolved_at = ? WHERE id = ?",
                            (status, _now(), int(mission_id)))
    return {"id": row[0], "user_id": int(row[1]), "brief": row[2]}


def add_challenge(title: str, body: str, answer: Optional[str], message_id: Optional[int]) -> int:
    ensure_tables()
    return DatabaseManager.execute_insert(
        "INSERT INTO bb_challenges (title, body, answer, status, message_id, created_at) "
        "VALUES (?, ?, ?, 'open', ?, ?)",
        (title, body, answer or None, str(message_id) if message_id else None, _now()))


def open_challenge() -> Optional[dict]:
    ensure_tables()
    row = DatabaseManager.fetch_one(
        "SELECT id, title, body, answer, created_at FROM bb_challenges WHERE status = 'open' ORDER BY id DESC LIMIT 1")
    return {"id": row[0], "title": row[1], "body": row[2], "answer": row[3], "created_at": row[4]} if row else None


def close_challenge(challenge_id: int, winner_id: Optional[int]) -> None:
    DatabaseManager.execute(
        "UPDATE bb_challenges SET status = 'closed', winner_id = ?, resolved_at = ? WHERE id = ?",
        (str(winner_id) if winner_id else None, _now(), int(challenge_id)))


def touch_activity(user_id: int) -> None:
    ensure_tables()
    DatabaseManager.execute(
        "INSERT INTO bb_activity (user_id, last_message_at, messages) VALUES (?, ?, 1) "
        "ON CONFLICT(user_id) DO UPDATE SET last_message_at = excluded.last_message_at, messages = messages + 1",
        (str(user_id), _now()))


def quiet_housemates() -> list[int]:
    """Housemates with no house-channel message in the configured window. Never posted
    means quiet since they joined."""
    ensure_tables()
    cutoff = _now() - quiet_hours() * 3600
    rows = DatabaseManager.fetch_all(
        "SELECT h.user_id, h.joined_at, a.last_message_at FROM bb_housemates h "
        "LEFT JOIN bb_activity a ON a.user_id = h.user_id WHERE h.status = ?", (STATUS_IN,))
    out = []
    for uid, joined, last in rows:
        last_seen = last if last is not None else joined
        if last_seen < cutoff:
            out.append(int(uid))
    return out


# ---------------------------------------------------------------------------
# Discord helpers
# ---------------------------------------------------------------------------

def _guild(client: discord.Client) -> Optional[discord.Guild]:
    return client.get_guild(int(getattr(config, "GUILD_ID", 0)))


def _name(guild: Optional[discord.Guild], user_id: int) -> str:
    member = guild.get_member(int(user_id)) if guild else None
    return member.display_name if member else f"user {user_id}"


def _mention_and_name(guild: Optional[discord.Guild], user_id: int) -> str:
    return f"<@{user_id}> ({_name(guild, user_id)})"


async def _channel(client: discord.Client, channel_id: int):
    if not channel_id:
        return None
    ch = client.get_channel(channel_id)
    if ch is None:
        try:
            ch = await client.fetch_channel(channel_id)
        except discord.HTTPException:
            return None
    return ch


async def house_channel(client: discord.Client):
    return await _channel(client, house_channel_id())


def _role_mention() -> str:
    rid = housemate_role_id()
    return f"<@&{rid}> " if rid else ""


async def notify_host(client: discord.Client, content: Optional[str] = None,
                      embed: Optional[discord.Embed] = None) -> bool:
    """Every secret goes here and nowhere else."""
    uid = host_id()
    if not uid:
        return False
    try:
        user = client.get_user(uid) or await client.fetch_user(uid)
        await user.send(content=content, embed=embed)
        return True
    except discord.HTTPException as e:
        log.warning("Big Brother: could not DM host %s: %s", uid, e)
        return False


def _echo_body(content: Optional[str], embed: Optional[discord.Embed]) -> str:
    parts = []
    if content:
        parts.append(content)
    if embed:
        if embed.title:
            parts.append(f"**{embed.title}**")
        if embed.description:
            parts.append(embed.description)
    return "\n".join(parts)[:3800] or "*(no text)*"


async def _echo_to_host(client: discord.Client, where: str, content: Optional[str],
                        embed: Optional[discord.Embed], delivered: bool = True) -> None:
    """Copy of every message Big Brother sends, so the host can see what went out and that it
    arrived. Never raises."""
    try:
        e = discord.Embed(title=f"{'📨' if delivered else '⚠️'} {where}", description=_echo_body(content, embed),
                          colour=ACCENT if delivered else 0xE74C3C)
        e.set_footer(text="sent" if delivered else "NOT delivered (DMs closed?)")
        await notify_host(client, embed=e)
    except Exception:
        log.exception("Big Brother: echo to host failed")


async def dm_user(client: discord.Client, user_id: int, content: Optional[str] = None,
                  embed: Optional[discord.Embed] = None, *, echo: bool = True,
                  ack: Optional[str] = None) -> bool:
    """ack: a short label (e.g. "Mission #4") attaches an "I've seen this" button; the press
    is logged and DMed to the host, so Big Brother knows it was read."""
    ok = True
    view = None
    if ack and int(user_id) != host_id():
        view = discord.ui.View(timeout=None)
        view.add_item(AckButton(create_ack(user_id, ack), user_id))
    try:
        user = client.get_user(int(user_id)) or await client.fetch_user(int(user_id))
        if view is not None:
            await user.send(content=content, embed=embed, view=view)
        else:
            await user.send(content=content, embed=embed)
    except discord.HTTPException as e:
        log.info("Big Brother: could not DM %s: %s", user_id, e)
        ok = False
    if echo and int(user_id) != host_id():
        await _echo_to_host(client, f"DM to {_name(_guild(client), user_id)}", content, embed, ok)
    return ok


async def bb_send(channel, content: Optional[str] = None, embed: Optional[discord.Embed] = None,
                  view=None, *, reply_to: Optional[discord.Message] = None):
    """Send as Big Brother into a channel and echo a copy to the host."""
    if reply_to is not None:
        msg = await reply_to.reply(content=content, embed=embed, view=view) if view is not None \
            else await reply_to.reply(content=content, embed=embed)
    elif view is not None:
        msg = await channel.send(content=content, embed=embed, view=view)
    else:
        msg = await channel.send(content=content, embed=embed)
    if _client_ref is not None:
        where = f"Posted in #{getattr(channel, 'name', 'channel')}"
        await _echo_to_host(_client_ref, where, content, embed, True)
    return msg


def bb_embed(title: str, description: str = "") -> discord.Embed:
    e = discord.Embed(title=f"{EYE} {title}", description=description, colour=ACCENT)
    e.set_footer(text="Big Brother is watching.")
    return e


async def _sync_role(guild: Optional[discord.Guild], user_id: int, give: bool) -> None:
    rid = housemate_role_id()
    if not guild or not rid:
        return
    role = guild.get_role(rid)
    member = guild.get_member(int(user_id))
    if not role or not member:
        return
    try:
        if give and role not in member.roles:
            await member.add_roles(role, reason="Big Brother housemate")
        elif not give and role in member.roles:
            await member.remove_roles(role, reason="Big Brother eviction")
    except discord.HTTPException as e:
        log.warning("Big Brother: role sync failed for %s: %s", user_id, e)


# ---------------------------------------------------------------------------
# Game actions (shared by the panel and the slash commands)
# ---------------------------------------------------------------------------

async def add_housemates(client: discord.Client, user_ids: Iterable[int]) -> list[int]:
    guild = _guild(client)
    added = []
    for uid in user_ids:
        if db_add_housemate(uid):
            added.append(int(uid))
            log_event("housemate_added", target=uid)
        await _sync_role(guild, uid, True)
    return added


async def evict(client: discord.Client, user_id: int, *, announce: bool = True) -> None:
    guild = _guild(client)
    db_set_status(user_id, STATUS_EVICTED)
    last = get_state(STATE_LAST_VOTE_RESULT) or {}
    log_event("evicted", target=user_id, announced=announce, last_vote=last)
    await _sync_role(guild, user_id, False)
    if announce:
        ch = await house_channel(client)
        if ch:
            await bb_send(ch, 
                f"{_role_mention()}{EYE} **Big Brother has made a decision.**\n\n"
                f"<@{user_id}>, you have been evicted from the Big Brother house. "
                f"Please leave through the diary room door.")
    await dm_user(client, user_id, embed=bb_embed(
        "You have been evicted",
        "Thanks for playing. You can still watch and vote in the public evictions."))


async def set_house_silence(client: discord.Client, silent: bool) -> bool:
    """During nominations the house goes quiet: the Housemate role can't send in the house
    channel or its snug threads. Lifted when nominations close. Needs the role configured."""
    rid = housemate_role_id()
    ch = await house_channel(client)
    if not rid or not isinstance(ch, discord.TextChannel):
        return False
    role = ch.guild.get_role(rid)
    if not role:
        return False
    overwrite = ch.overwrites_for(role)
    if silent:
        overwrite.send_messages = False
        overwrite.send_messages_in_threads = False
    else:
        overwrite.send_messages = None
        overwrite.send_messages_in_threads = None
    try:
        if overwrite.is_empty():
            await ch.set_permissions(role, overwrite=None, reason="Big Brother: nominations closed")
        else:
            await ch.set_permissions(role, overwrite=overwrite,
                                     reason="Big Brother: house " + ("silenced" if silent else "unsilenced"))
        set_state(STATE_HOUSE_SILENT, bool(silent))
        return True
    except discord.HTTPException as e:
        log.warning("Big Brother: could not %s the house: %s", "silence" if silent else "unsilence", e)
        return False


async def open_nominations(client: discord.Client) -> Optional[int]:
    if open_round(KIND_NOMINATIONS):
        return None
    rid = create_round(KIND_NOMINATIONS)
    silenced = await set_house_silence(client, True)
    log_event("nominations_opened", round_id=rid, house_silenced=silenced)
    ch = await house_channel(client)
    if ch:
        n = nominations_each()
        await bb_send(ch,
            f"{_role_mention()}{EYE} **Nominations are open.**\n\n"
            f"Press **Nominate** on the panel below to pick the {n} housemate{'s' if n != 1 else ''} you want "
            f"to face the public vote. Only you and Big Brother will see who you chose. You can change your "
            f"mind until nominations close."
            + ("\n\nThe house is silent until then. No talking." if silenced else ""))
    return rid


async def close_nominations(client: discord.Client) -> Optional[dict]:
    rnd = open_round(KIND_NOMINATIONS)
    if not rnd:
        return None
    close_round(rnd["id"])
    await set_house_silence(client, False)
    guild = _guild(client)
    expired = clear_all_immunity()
    if expired:
        log_event("immunity_expired", housemates=expired, round_id=rnd["id"])
    pairs = nominations_for(rnd["id"])
    counts: dict[int, list[int]] = {}
    for nominator, nominee in pairs:
        counts.setdefault(nominee, []).append(nominator)
    ranked = sorted(counts.items(), key=lambda kv: (-len(kv[1]), _name(guild, kv[0]).lower()))
    done = nominators_done(rnd["id"])
    missing = [u for u in housemates() if u not in done]

    lines = [f"**{len(v)}** - {_mention_and_name(guild, nominee)}\n-# nominated by " +
             ", ".join(_name(guild, n) for n in v) for nominee, v in ranked] or ["Nobody nominated anyone."]
    desc = "\n".join(lines)
    if missing:
        desc += "\n\n**Did not nominate:** " + ", ".join(_name(guild, u) for u in missing)
    desc += "\n\nOpen the panel and press **Start eviction vote** to put the nominees to the public."
    await notify_host(client, embed=bb_embed(f"Nomination results (round {rnd['id']})", desc))

    top = [nominee for nominee, _ in ranked]
    log_event("nominations_closed", round_id=rnd["id"],
              tally=[[n, v] for n, v in ranked], did_not_nominate=missing)
    set_state(STATE_LAST_NOM_TALLY, {"round_id": rnd["id"], "ranked": [[n, len(counts[n])] for n in top]})
    ch = await house_channel(client)
    if ch:
        await bb_send(ch, f"{EYE} **Nominations are closed.** You may talk again. Big Brother is counting, and the "
                          f"nominees will be announced shortly.")
    return {"round_id": rnd["id"], "ranked": ranked, "missing": missing}


def _vote_view(round_id: int, nominee_ids: Iterable[int], guild: Optional[discord.Guild]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for n in nominee_ids:
        view.add_item(VoteButton(round_id, n, _name(guild, n)))
    return view


def _vote_embed(nominee_ids: Iterable[int], guild: Optional[discord.Guild]) -> discord.Embed:
    names = ", ".join(f"**{_name(guild, n)}**" for n in nominee_ids)
    return bb_embed(
        "Eviction vote",
        f"The housemates have nominated. Now the server decides.\n\n"
        f"Facing eviction: {names}\n\n"
        f"Press a button to vote for who should **leave** the house. One vote each, and you can "
        f"change it until the vote closes. Results stay secret until Big Brother reveals them.")


async def start_vote(client: discord.Client, nominee_ids: list[int]) -> Optional[dict]:
    if open_round(KIND_VOTE):
        return None
    guild = _guild(client)
    ch = await _channel(client, vote_channel_id())
    if not ch:
        return None
    rid = create_round(KIND_VOTE, nominees=nominee_ids)
    names = ", ".join(f"**{_name(guild, n)}**" for n in nominee_ids)
    embed = _vote_embed(nominee_ids, guild)
    msg = await bb_send(ch, content=f"{EYE} **Eviction vote is open.**", embed=embed,
                        view=_vote_view(rid, nominee_ids, guild))
    set_round_message(rid, ch.id, msg.id)
    log_event("vote_opened", round_id=rid, nominees=list(nominee_ids), channel_id=ch.id, message_id=msg.id)
    hc = await house_channel(client)
    if hc and hc.id != ch.id:
        await bb_send(hc, f"{_role_mention()}{EYE} The public eviction vote is open in <#{ch.id}>. "
                      f"Facing eviction: {names}.")
    return {"round_id": rid, "message_id": msg.id, "channel_id": ch.id}


async def close_vote(client: discord.Client) -> Optional[dict]:
    rnd = open_round(KIND_VOTE)
    if not rnd:
        return None
    close_round(rnd["id"])
    guild = _guild(client)
    tally = vote_tally(rnd["id"])
    total = sum(tally.values())
    ranked = sorted(rnd["nominees"], key=lambda n: (-tally.get(n, 0), _name(guild, n).lower()))
    lines = []
    for n in ranked:
        c = tally.get(n, 0)
        pct = (100 * c / total) if total else 0
        lines.append(f"**{c}** ({pct:.0f}%) - {_mention_and_name(guild, n)}")
    desc = "\n".join(lines) + f"\n\n{total} vote{'s' if total != 1 else ''} cast."
    desc += "\n\nNothing has been announced. Press **Evict housemate** on the panel when you're ready to reveal it."
    await notify_host(client, embed=bb_embed(f"Eviction vote result (round {rnd['id']})", desc))
    set_state(STATE_LAST_VOTE_RESULT, {"round_id": rnd["id"], "ranked": [[n, tally.get(n, 0)] for n in ranked],
                                       "total": total})
    log_event("vote_closed", round_id=rnd["id"], tally=[[n, tally.get(n, 0)] for n in ranked], total=total)
    if rnd["channel_id"] and rnd["message_id"]:
        ch = await _channel(client, rnd["channel_id"])
        if ch:
            try:
                msg = await ch.fetch_message(rnd["message_id"])
                embed = msg.embeds[0] if msg.embeds else bb_embed("Eviction vote")
                embed.description = (embed.description or "") + f"\n\n**Voting is closed.** {total} votes cast."
                await msg.edit(content=f"{EYE} **Eviction vote is closed.**", embed=embed, view=None)
            except discord.HTTPException:
                pass
    return {"round_id": rnd["id"], "ranked": ranked, "tally": tally, "total": total}


SNUG_ARCHIVE_MINUTES = 60
SNUG_COOLDOWN_SECONDS = 2 * 3600


async def open_snug(client: discord.Client, opened_by: Optional[int], member_ids: list[int],
                    *, reason: str = "") -> tuple[Optional[discord.Thread], str]:
    """Create a private thread under the house channel with the members and the host in it.
    Big Brother watches: the host is added, and every message is stored in the transcript."""
    ch = await house_channel(client)
    if not isinstance(ch, discord.TextChannel):
        return None, "House channel not found."
    guild = ch.guild
    members = list(dict.fromkeys(int(m) for m in member_ids))
    label = " & ".join(_name(guild, m) for m in members)[:80]
    try:
        thread = await ch.create_thread(
            name=f"🛋️ snug: {label}"[:100], type=discord.ChannelType.private_thread,
            invitable=False, auto_archive_duration=SNUG_ARCHIVE_MINUTES,
            reason="Big Brother snug")
    except discord.HTTPException as e:
        log.warning("Big Brother: snug thread failed: %s", e)
        return None, "Couldn't open a private thread here (check the bot can create private threads)."
    for uid in members + [host_id()]:
        member = guild.get_member(uid)
        if member:
            try:
                await thread.add_user(member)
            except discord.HTTPException:
                pass
    sid = add_snug(thread.id, opened_by, members)
    log_event("snug_opened", actor=opened_by, snug_id=sid, thread_id=thread.id, members=members, reason=reason)
    mentions = " ".join(f"<@{m}>" for m in members)
    intro = (f"{EYE} **Welcome to the snug.** {mentions}\n\n"
             + (f"{reason}\n\n" if reason else "")
             + f"Talk freely. Big Brother is in here too, and is watching. "
             f"This thread closes itself after {SNUG_ARCHIVE_MINUTES} minutes of quiet.")
    try:
        await thread.send(intro)
    except discord.HTTPException:
        pass
    return thread, ""


async def crown_winner(client: discord.Client, user_id: int) -> tuple[bool, str]:
    guild = _guild(client)
    db_set_status(user_id, STATUS_WINNER)
    prize = prize_ukp()
    paid = True
    if prize > 0:
        try:
            from lib.economy.economy_manager import add_bb
            paid = bool(add_bb(int(user_id), prize, reason="Big Brother winner",
                               taxable=False, discretionary=True))
        except Exception:
            log.exception("Big Brother: prize payout failed")
            paid = False
    ch = await house_channel(client)
    if ch:
        await bb_send(ch, 
            f"{_role_mention()}{EYE} **We have a winner.**\n\n"
            f"After two weeks in the house, the winner of UKPlace Big Brother is <@{user_id}>! "
            + (f"{prize:,} UKP is on its way." if prize and paid else ""))
    log_event("winner_crowned", target=user_id, prize=prize, paid=paid)
    note = f"{_name(guild, user_id)} crowned." + ("" if paid else f" Prize of {prize:,} UKP was NOT paid (bank refused) - pay manually.")
    return paid, note


# ---------------------------------------------------------------------------
# Public vote button (dynamic so it survives restarts with no per-message registration)
# ---------------------------------------------------------------------------

class VoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"bb:vote:(?P<rid>\d+):(?P<uid>\d+)"):
    def __init__(self, round_id: int, nominee_id: int, label: str = "Vote"):
        self.round_id, self.nominee_id = int(round_id), int(nominee_id)
        super().__init__(discord.ui.Button(
            label=label[:80], emoji=EYE, style=discord.ButtonStyle.secondary,
            custom_id=f"bb:vote:{self.round_id}:{self.nominee_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["rid"]), int(match["uid"]), item.label or "Vote")

    async def callback(self, interaction: discord.Interaction) -> None:
        if not enabled():
            await interaction.response.send_message("Big Brother has left the building.", ephemeral=True)
            return
        rnd = get_round(self.round_id)
        if not rnd or rnd["status"] != "open":
            await interaction.response.send_message("This vote has closed.", ephemeral=True)
            return
        if self.nominee_id not in rnd["nominees"]:
            await interaction.response.send_message("That housemate isn't up for eviction.", ephemeral=True)
            return
        if interaction.user.bot:
            return
        cast_vote(self.round_id, interaction.user.id, self.nominee_id)
        log_event("vote_cast", actor=interaction.user.id, target=self.nominee_id, round_id=self.round_id)
        await interaction.response.send_message(
            f"Vote recorded: you voted to evict **{_name(interaction.guild, self.nominee_id)}**. "
            f"Press another button to change it.", ephemeral=True)


class AckButton(discord.ui.DynamicItem[discord.ui.Button], template=r"bb:ack:(?P<aid>\d+):(?P<uid>\d+)"):
    """"I've seen this" on a DM. One press, then it greys out; the host is told."""

    def __init__(self, ack_id: int, user_id: int, done: bool = False):
        self.ack_id, self.user_id = int(ack_id), int(user_id)
        super().__init__(discord.ui.Button(
            label="Seen ✓" if done else "I've seen this", emoji=EYE if not done else None,
            style=discord.ButtonStyle.secondary if done else discord.ButtonStyle.primary,
            disabled=done, custom_id=f"bb:ack:{self.ack_id}:{self.user_id}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match, /):
        return cls(int(match["aid"]), int(match["uid"]), done=bool(item.disabled))

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("That's not yours to press.", ephemeral=True)
            return
        label = mark_ack(self.ack_id, self.user_id)
        view = discord.ui.View(timeout=None)
        view.add_item(AckButton(self.ack_id, self.user_id, done=True))
        try:
            await interaction.response.edit_message(view=view)
        except discord.HTTPException:
            pass
        if label is None:
            return
        log_event("acknowledged", actor=self.user_id, label=label)
        await notify_host(interaction.client,
                          f"👁️‍🗨️ **{_name(_guild(interaction.client), self.user_id)}** has seen: {label}")


# ---------------------------------------------------------------------------
# Ephemeral pickers used by the panel
# ---------------------------------------------------------------------------

class _HousematePicker(discord.ui.View):
    """Ephemeral dropdown of housemates. `on_done(interaction, [ids])` runs on submit."""

    def __init__(self, guild: Optional[discord.Guild], ids: list[int], on_done: Callable,
                 *, placeholder: str, min_values: int = 1, max_values: int = 1,
                 defaults: Iterable[int] = ()):
        super().__init__(timeout=300)
        defaults = set(defaults)
        options = [discord.SelectOption(label=_name(guild, i)[:100], value=str(i), default=i in defaults)
                   for i in ids[:25]]
        select = discord.ui.Select(placeholder=placeholder, options=options,
                                   min_values=min(min_values, len(options)),
                                   max_values=min(max_values, len(options)))

        async def _cb(interaction: discord.Interaction):
            await on_done(interaction, [int(v) for v in select.values])
        select.callback = _cb
        self.add_item(select)


GRID_MAX = 25  # Discord: 5 rows x 5 buttons on one message


def _chunks(ids: list[int]) -> list[list[int]]:
    """Split housemates into pages of at most GRID_MAX, one ephemeral message per page."""
    ids = list(ids)
    return [ids[i:i + GRID_MAX] for i in range(0, len(ids), GRID_MAX)] or [[]]


class _GridSet:
    """A button grid spread across as many ephemeral messages as it takes (25 buttons each).
    The first page is the interaction's response; later pages are follow-ups. One-shot
    pickers flip `done` on the first press so the other pages can't pick again, and then
    tidy the other pages away best-effort."""

    def __init__(self):
        self.done = False
        self.interaction: Optional[discord.Interaction] = None
        self.first_id: Optional[int] = None
        self.followup_ids: list[int] = []

    async def deliver(self, interaction: discord.Interaction, content: str, views: list, *, edit: bool = False):
        self.interaction = interaction
        pages = len(views)
        first = content + (f"\n-# page 1 of {pages}" if pages > 1 else "")
        if edit:
            await interaction.response.edit_message(content=first, view=views[0])
            self.first_id = interaction.message.id if interaction.message else None
        else:
            await interaction.response.send_message(first, view=views[0], ephemeral=True)
            try:
                self.first_id = (await interaction.original_response()).id
            except discord.HTTPException:
                self.first_id = None
        for i, v in enumerate(views[1:], start=2):
            try:
                m = await interaction.followup.send(f"-# page {i} of {pages}", view=v, ephemeral=True, wait=True)
                self.followup_ids.append(m.id)
            except discord.HTTPException as e:
                log.warning("Big Brother: could not send grid page %s: %s", i, e)

    async def dismiss_others(self, keep_id: Optional[int]) -> None:
        if not self.interaction:
            return
        for mid in [*self.followup_ids, self.first_id]:
            if mid is None or mid == keep_id:
                continue
            try:
                if mid == self.first_id and self.interaction.response.is_done() and not self.interaction.message:
                    await self.interaction.delete_original_response()
                else:
                    await self.interaction.followup.delete_message(mid)
            except discord.HTTPException:
                pass


class _PickGrid(discord.ui.View):
    """One page of a one-shot picker: one button per housemate, pressing one calls
    on_pick(interaction, user_id). Shares a _GridSet with its sibling pages."""

    def __init__(self, guild: Optional[discord.Guild], ids: list[int], on_pick: Callable, gridset: _GridSet,
                 *, style=discord.ButtonStyle.secondary, marked: Iterable[int] = ()):
        super().__init__(timeout=300)
        marked = set(marked)
        for uid in ids[:GRID_MAX]:
            btn = discord.ui.Button(label=_name(guild, uid)[:80],
                                    style=discord.ButtonStyle.primary if uid in marked else style)

            async def _cb(interaction: discord.Interaction, _uid=uid):
                if gridset.done:
                    await interaction.response.send_message("You've already picked from this list.", ephemeral=True)
                    return
                gridset.done = True
                await on_pick(interaction, _uid)
                keep = interaction.message.id if interaction.message else None
                asyncio.create_task(gridset.dismiss_others(keep))
            btn.callback = _cb
            self.add_item(btn)


async def _send_pick(interaction: discord.Interaction, content: str, guild, ids: list[int], on_pick: Callable,
                     *, marked: Iterable[int] = (), style=discord.ButtonStyle.secondary, edit: bool = False):
    gs = _GridSet()
    views = [_PickGrid(guild, page, on_pick, gs, style=style, marked=marked) for page in _chunks(ids)]
    await gs.deliver(interaction, content, views, edit=edit)


async def _send_toggle(interaction: discord.Interaction, content: str, guild, ids: list[int],
                       state: dict[int, bool], on_toggle: Callable):
    views = [_ToggleGrid(guild, page, state, on_toggle) for page in _chunks(ids)]
    await _GridSet().deliver(interaction, content, views)


async def _send_count(interaction: discord.Interaction, content: str, guild, ids: list[int],
                      counts: dict[int, int], on_press: Callable):
    views = [_CountGrid(guild, page, counts, on_press) for page in _chunks(ids)]
    await _GridSet().deliver(interaction, content, views)


class _ToggleGrid(discord.ui.View):
    """One button per housemate showing an on/off state (green/red). Pressing flips it via
    on_toggle(interaction, user_id) -> new state, then the grid redraws itself."""

    def __init__(self, guild: Optional[discord.Guild], ids: list[int], state: dict[int, bool],
                 on_toggle: Callable, *, label: Callable[[int], str] = None):
        super().__init__(timeout=300)
        self.guild, self.ids, self.state, self.on_toggle = guild, ids, dict(state), on_toggle
        self.label = label or (lambda uid: _name(guild, uid))
        self._build()

    def _build(self):
        self.clear_items()
        for uid in self.ids[:GRID_MAX]:
            on = self.state.get(uid, False)
            btn = discord.ui.Button(label=self.label(uid)[:80],
                                    style=discord.ButtonStyle.success if on else discord.ButtonStyle.danger)

            async def _cb(interaction: discord.Interaction, _uid=uid):
                await interaction.response.defer()
                self.state[_uid] = await self.on_toggle(interaction, _uid)
                self._build()
                await interaction.edit_original_response(view=self)
            btn.callback = _cb
            self.add_item(btn)


class _CountGrid(discord.ui.View):
    """One button per housemate with a running number; each press calls on_press and the
    button's count redraws. Used for handing out tokens."""

    def __init__(self, guild: Optional[discord.Guild], ids: list[int], counts: dict[int, int], on_press: Callable):
        super().__init__(timeout=300)
        self.guild, self.ids, self.counts, self.on_press = guild, ids, dict(counts), on_press
        self._build()

    def _build(self):
        self.clear_items()
        for uid in self.ids[:GRID_MAX]:
            n = self.counts.get(uid, 0)
            btn = discord.ui.Button(label=f"{_name(self.guild, uid)} · {n}"[:80],
                                    style=discord.ButtonStyle.primary if n else discord.ButtonStyle.secondary)

            async def _cb(interaction: discord.Interaction, _uid=uid):
                await interaction.response.defer()
                self.counts[_uid] = await self.on_press(interaction, _uid)
                self._build()
                await interaction.edit_original_response(view=self)
            btn.callback = _cb
            self.add_item(btn)


class _Confirm(discord.ui.View):
    def __init__(self, on_yes: Callable, label: str = "Confirm"):
        super().__init__(timeout=120)
        self.on_yes = on_yes
        yes = discord.ui.Button(label=label, style=discord.ButtonStyle.danger)
        no = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def _yes(interaction):
            await on_yes(interaction)
        async def _no(interaction):
            await interaction.response.edit_message(content="Cancelled.", view=None, embed=None)
        yes.callback, no.callback = _yes, _no
        self.add_item(yes)
        self.add_item(no)


class _TextModal(discord.ui.Modal):
    def __init__(self, title: str, fields: list[tuple[str, str, bool, int, bool]], on_submit: Callable):
        """fields: (key, label, required, max_length, long)"""
        super().__init__(title=title[:45])
        self._on_submit = on_submit
        self._inputs = {}
        for key, label, required, max_len, long in fields[:5]:
            ti = discord.ui.TextInput(label=label[:45], required=required, max_length=max_len,
                                      style=discord.TextStyle.long if long else discord.TextStyle.short)
            self._inputs[key] = ti
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        values = {k: (v.value or "").strip() for k, v in self._inputs.items()}
        await self._on_submit(interaction, values)


# ---------------------------------------------------------------------------
# Control panel
# ---------------------------------------------------------------------------

def _panel_text(guild: Optional[discord.Guild]) -> str:
    ins = housemates()
    evicted = housemates(STATUS_EVICTED)
    noms = open_round(KIND_NOMINATIONS)
    vote = open_round(KIND_VOTE)
    missions = active_missions()
    chal = open_challenge()
    quiet = quiet_housemates()

    started = game_started_at()
    lines = [f"## {EYE} Big Brother Control",
             "-# Only phase and counts are shown here. Everything secret is sent to the host's DMs.",
             "",
             (f"**Game:** 🟢 live since <t:{started}:f>" if started
              else "**Game:** ⚪ not started. Housemates can be added now; their panel unlocks when you press **Start the game**."),
             f"**Housemates:** {len(ins)} in the house · {len(evicted)} evicted · {len(immune_ids())} immune"]
    if noms:
        lines.append(f"**Nominations:** 🟢 open · {len(nominators_done(noms['id']))}/{len(ins)} have nominated")
    else:
        lines.append("**Nominations:** ⚪ closed")
    if vote:
        lines.append(f"**Eviction vote:** 🟢 open in <#{vote['channel_id']}> · {vote_count(vote['id'])} votes cast")
    else:
        lines.append("**Eviction vote:** ⚪ none running")
    lines.append("**House chat:** " + ("🔇 housemates silenced" if house_silent() else "🟢 open"))
    unread = pending_acks()
    if unread:
        lines.append(f"**Unread DMs:** {len(unread)} · " + ", ".join(
            f"{_name(guild, a['user_id'])} ({a['label']})" for a in unread[:6]) + (" …" if len(unread) > 6 else ""))
    lines.append(f"**Secret missions:** {len(missions)} active")
    lines.append(f"**Challenge:** {('🟢 ' + chal['title']) if chal else '⚪ none open'}")
    if quiet:
        lines.append(f"**Quiet for {quiet_hours()}h:** " + ", ".join(_name(guild, u) for u in quiet[:15]))
    lines.append("")
    lines.append(f"-# Updated <t:{_now()}:R>")
    return "\n".join(lines)


class _PanelButton(discord.ui.Button):
    """A button whose handler is looked up by name at press time, so the view stays a
    plain persistent layout and the registries below can be edited freely."""
    prefix = "bb:ctl"

    def __init__(self, action: str, label: str, style=discord.ButtonStyle.secondary, emoji=None):
        super().__init__(label=label, style=style, emoji=emoji, custom_id=f"{self.prefix}:{action}")
        self.action = action

    def registry(self) -> dict:
        return PANEL_ACTIONS

    async def callback(self, interaction: discord.Interaction):
        handler = self.registry().get(self.action)
        if not handler:
            await interaction.response.send_message("Unknown action.", ephemeral=True)
            return
        await handler(interaction)


class BigBrotherControlView(discord.ui.LayoutView):
    def __init__(self, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        card = discord.ui.Container(accent_colour=ACCENT)
        card.add_item(discord.ui.TextDisplay(_panel_text(guild)))

        started = game_started()
        sections = [
            ("### 🎬 Game", [
                [_PanelButton("start", "Start the game" if not started else "Game is live",
                              discord.ButtonStyle.success if not started else discord.ButtonStyle.secondary, "🎬"),
                 _PanelButton("refresh", "Refresh", emoji="🔄")],
            ]),
            ("### 🗳️ Eviction cycle", [
                [_PanelButton("open_noms", "Open nominations", discord.ButtonStyle.primary, "📝"),
                 _PanelButton("close_noms", "Close nominations", emoji="🔒")],
                [_PanelButton("start_vote", "Start eviction vote", discord.ButtonStyle.primary, "🗳️"),
                 _PanelButton("close_vote", "Close vote", emoji="🔒"),
                 _PanelButton("evict", "Evict housemate", discord.ButtonStyle.danger, "🚪")],
            ]),
            ("### 🏠 Housemates", [
                [_PanelButton("add", "Add housemates", discord.ButtonStyle.success, "➕"),
                 _PanelButton("immunity", "Toggle immunity", emoji="🛡️"),
                 _PanelButton("token", "Grant immunity token", emoji="🎟️")],
                [_PanelButton("snug", "Open a snug", emoji="🛋️"),
                 _PanelButton("silence", "Unsilence house" if house_silent() else "Silence house",
                              discord.ButtonStyle.secondary if house_silent() else discord.ButtonStyle.danger,
                              "🔊" if house_silent() else "🔇"),
                 _PanelButton("crown", "Crown winner", discord.ButtonStyle.success, "👑")],
            ]),
            ("### 🕵️ Secret missions", [
                [_PanelButton("mission", "Assign mission", emoji="🕵️"),
                 _PanelButton("resolve_mission", "Resolve mission", emoji="✅")],
            ]),
            ("### 🧠 Challenges & messages", [
                [_PanelButton("challenge", "Post challenge", emoji="🧠"),
                 _PanelButton("end_challenge", "End challenge", emoji="🏁")],
                [_PanelButton("dm", "DM as Big Brother", emoji="✉️"),
                 _PanelButton("broadcast", "Announce in house", emoji="📣")],
            ]),
        ]
        # Discord caps a layout at 40 components counting every nested item; keep an eye on
        # this if adding sections (each is separator + heading + rows + buttons).
        for heading, rows in sections:
            card.add_item(discord.ui.Separator())
            card.add_item(discord.ui.TextDisplay(heading))
            for row in rows:
                card.add_item(discord.ui.ActionRow(*row))
        self.add_item(card)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not enabled():
            await interaction.response.send_message("Big Brother is switched off.", ephemeral=True)
            return False
        if not is_operator(interaction.user.id):
            await interaction.response.send_message(f"{EYE} Only Big Brother can press that.", ephemeral=True)
            return False
        return True


def _house_panel_signature(guild: Optional[discord.Guild]) -> str:
    """Everything that affects what the house panel shows, minus the timestamp."""
    return json.dumps({"text": _house_panel_text(guild), "unlocked": house_unlocked(),
                       "noms_open": open_round(KIND_NOMINATIONS) is not None})


async def refresh_panel(client: discord.Client) -> None:
    """Re-render the control panel in place. The house panel is re-posted at the bottom of
    the channel when what it shows has changed (phase, counts, missions), and left alone
    otherwise so housemates' own presses don't make it jump around."""
    guild = _guild(client)
    mid = get_state(STATE_PANEL_MSG)
    ch = await _channel(client, control_channel_id())
    if ch and mid:
        try:
            msg = await ch.fetch_message(int(mid))
            await msg.edit(content=None, view=BigBrotherControlView(guild))
        except discord.HTTPException as e:
            log.info("Big Brother: control panel refresh failed: %s", e)
    try:
        sig = _house_panel_signature(guild)
        if sig != get_state(STATE_HOUSE_PANEL_SIG):
            await repost_house_panel(client)
    except Exception:
        log.exception("Big Brother: house panel repost failed")


async def ensure_control_panel(client: discord.Client) -> None:
    """Post the panel in the control channel once, then keep editing that message."""
    if not enabled():
        return
    ensure_tables()
    ch = await _channel(client, control_channel_id())
    if not ch:
        log.warning("Big Brother: control channel %s not found", control_channel_id())
        return
    view = BigBrotherControlView(_guild(client))
    mid = get_state(STATE_PANEL_MSG)
    if mid:
        try:
            msg = await ch.fetch_message(int(mid))
            await msg.edit(view=view)
            return
        except discord.HTTPException:
            pass
    msg = await ch.send(view=view)
    set_state(STATE_PANEL_MSG, msg.id)
    log.info("Big Brother: posted control panel %s in %s", msg.id, ch.id)


async def ensure_house_panel(client: discord.Client) -> None:
    """The housemates' panel in the house channel: diary, nominate, mission, expose."""
    if not enabled():
        return
    ensure_tables()
    ch = await _channel(client, house_channel_id())
    if not ch:
        log.warning("Big Brother: house channel %s not found", house_channel_id())
        return
    view = HousePanelView(_guild(client))
    mid = get_state(STATE_HOUSE_PANEL_MSG)
    if mid:
        try:
            msg = await ch.fetch_message(int(mid))
            await msg.edit(view=view)
            set_state(STATE_HOUSE_PANEL_SIG, _house_panel_signature(_guild(client)))
            return
        except discord.HTTPException:
            pass
    msg = await ch.send(view=view)
    set_state(STATE_HOUSE_PANEL_MSG, msg.id)
    set_state(STATE_HOUSE_MSGS_SINCE_PANEL, 0)
    set_state(STATE_HOUSE_PANEL_SIG, _house_panel_signature(_guild(client)))
    log.info("Big Brother: posted house panel %s in %s", msg.id, ch.id)


async def repost_house_panel(client: discord.Client) -> None:
    """Delete the current house panel and post a fresh one so it sits under the chat."""
    async with _repost_lock:
        ch = await _channel(client, house_channel_id())
        if not ch:
            return
        old = get_state(STATE_HOUSE_PANEL_MSG)
        guild = _guild(client)
        msg = await ch.send(view=HousePanelView(guild))
        set_state(STATE_HOUSE_PANEL_MSG, msg.id)
        set_state(STATE_HOUSE_MSGS_SINCE_PANEL, 0)
        set_state(STATE_HOUSE_PANEL_SIG, _house_panel_signature(guild))
        if old:
            try:
                old_msg = await ch.fetch_message(int(old))
                await old_msg.delete()
            except discord.HTTPException:
                pass


async def ensure_panels(client: discord.Client) -> None:
    global _client_ref
    _client_ref = client
    await ensure_control_panel(client)
    await ensure_house_panel(client)


# --- panel actions: each is `async (interaction) -> None` and responds itself ---

async def _reply(interaction: discord.Interaction, text: str, *, refresh: bool = True):
    if interaction.response.is_done():
        await interaction.edit_original_response(content=text, view=None, embed=None)
    else:
        await interaction.response.send_message(text, ephemeral=True)
    if refresh:
        asyncio.create_task(refresh_panel(interaction.client))


async def _act_start(interaction: discord.Interaction):
    if game_started():
        await _reply(interaction, f"The game has been live since <t:{game_started_at()}:f>.", refresh=False)
        return
    if not housemates():
        await _reply(interaction, "Add the housemates first, then start the game.", refresh=False)
        return

    async def yes(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        set_state(STATE_GAME_STARTED_AT, _now())
        log_event("game_started", housemates=housemates())
        ch = await house_channel(inter.client)
        if ch:
            await bb_send(ch, 
                f"{_role_mention()}{EYE} **The doors are open.**\n\n"
                f"Welcome to the Big Brother house. The panel at the bottom of this channel is how you talk to Big Brother: "
                f"the diary room, nominations, your secret mission and the snug are all there. "
                f"Big Brother is watching. Good luck.")
        await refresh_panel(inter.client)
        await _reply(inter, "The game is live. The house panel is unlocked and the doors announcement is posted.", refresh=False)

    await interaction.response.send_message(
        f"Start the game with {len(housemates())} housemates? This unlocks the house panel and posts the opening announcement.",
        view=_Confirm(yes, "Start the game"), ephemeral=True)


async def _act_silence(interaction: discord.Interaction):
    # thinking=True: on a panel button a plain defer would make the panel message itself the
    # "original response", and _reply would overwrite the panel with the confirmation text.
    await interaction.response.defer(ephemeral=True, thinking=True)
    target = not house_silent()
    ok = await set_house_silence(interaction.client, target)
    if not ok:
        await _reply(interaction, "Couldn't change the house permissions (is the Housemate role configured?).", refresh=False)
        return
    log_event("house_silenced" if target else "house_unsilenced", actor=interaction.user.id)
    ch = await house_channel(interaction.client)
    if ch:
        await bb_send(ch, f"{EYE} **The house is silent.** No talking until Big Brother says so." if target
                      else f"{EYE} **You may talk again.**")
    await _reply(interaction, "Housemates can no longer send messages in the house." if target
                 else "Housemates can talk in the house again.")


async def _act_open_noms(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    if not housemates():
        await _reply(interaction, "No housemates yet. Add some first.", refresh=False)
        return
    rid = await open_nominations(interaction.client)
    await _reply(interaction, "Nominations are already open." if rid is None else f"Nominations opened (round {rid}).")


async def _act_close_noms(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    res = await close_nominations(interaction.client)
    await _reply(interaction, "No nominations round is open." if res is None
                 else "Nominations closed. The tally is in your DMs.")


async def _act_start_vote(interaction: discord.Interaction):
    if open_round(KIND_VOTE):
        await _reply(interaction, "A vote is already running. Close it first.", refresh=False)
        return
    ins = housemates()
    if len(ins) < 2:
        await _reply(interaction, "Need at least two housemates for a vote.", refresh=False)
        return
    last = get_state(STATE_LAST_NOM_TALLY) or {}
    ranked = [int(n) for n, _ in last.get("ranked", []) if int(n) in ins]
    # Default to everyone who tied at the highest nomination count, minimum two.
    defaults: list[int] = []
    if last.get("ranked"):
        top = None
        for n, c in last["ranked"]:
            if int(n) not in ins:
                continue
            if top is None:
                top = c
            if c == top or len(defaults) < 2:
                defaults.append(int(n))
    immune = immune_ids()
    choices = [u for u in ranked if u not in immune] + [u for u in ins if u not in ranked and u not in immune]
    if len(choices) < 2:
        await _reply(interaction, "Fewer than two housemates are eligible (immunity excludes the rest).", refresh=False)
        return

    async def done(inter: discord.Interaction, ids: list[int]):
        await inter.response.defer(ephemeral=True)
        res = await start_vote(inter.client, ids)
        await _reply(inter, "Couldn't start the vote (already open, or the vote channel is missing)."
                     if not res else f"Eviction vote posted in <#{res['channel_id']}>.")

    view = _HousematePicker(interaction.guild, choices, done, placeholder="Who faces the public vote?",
                            min_values=2, max_values=min(25, len(choices)), defaults=defaults)
    await interaction.response.send_message(
        "Pick the nominees for the public vote. Pre-selected from the last nomination tally where there is one.",
        view=view, ephemeral=True)


async def _act_close_vote(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    res = await close_vote(interaction.client)
    await _reply(interaction, "No vote is open." if res is None
                 else "Vote closed. The result is in your DMs and nothing has been announced yet.")


async def _act_evict(interaction: discord.Interaction):
    ins = housemates()
    if not ins:
        await _reply(interaction, "Nobody to evict.", refresh=False)
        return
    last = get_state(STATE_LAST_VOTE_RESULT) or {}
    default = [int(last["ranked"][0][0])] if last.get("ranked") and int(last["ranked"][0][0]) in ins else []

    async def picked(inter: discord.Interaction, uid: int):
        async def yes(inter2: discord.Interaction):
            await inter2.response.defer(ephemeral=True)
            await evict(inter2.client, uid)
            await notify_host(inter2.client, f"{EYE} {_mention_and_name(inter2.guild, uid)} has been evicted and announced in the house.")
            await _reply(inter2, f"Evicted {_name(inter2.guild, uid)}. Announced in the house channel.")

        await inter.response.edit_message(
            content=f"Evict **{_name(inter.guild, uid)}**? This posts the announcement in the house channel and removes the role.",
            view=_Confirm(yes, "Evict"))

    await _send_pick(interaction,
                     "Pick the housemate to evict." + (" The last vote's top nominee is highlighted." if default else ""),
                     interaction.guild, ins, picked, marked=default, style=discord.ButtonStyle.danger)


async def _act_add(interaction: discord.Interaction):
    view = discord.ui.View(timeout=300)
    select = discord.ui.UserSelect(placeholder="Pick the new housemates", min_values=1, max_values=25)

    async def cb(inter: discord.Interaction):
        await inter.response.defer(ephemeral=True)
        ids = [u.id for u in select.values if not getattr(u, "bot", False)]
        added = await add_housemates(inter.client, ids)
        for uid in added:
            await dm_user(inter.client, uid, embed=bb_embed(
                "Welcome to the house",
                f"You're a housemate in UKPlace Big Brother.\n\n"
                f"The panel at the bottom of <#{house_channel_id()}> is how you talk to Big Brother: "
                f"the diary room (optionally anonymous), nominations when they're open, your "
                f"secret mission, and exposing a housemate you think is on one. Everything you "
                f"press there is only seen by you and Big Brother.\n\n"
                f"Big Brother will ping the house channel when something is happening."))
        await _reply(inter, f"Added {len(added)} housemate{'s' if len(added) != 1 else ''}. "
                     f"{len(ids) - len(added)} were already in.")
    select.callback = cb
    view.add_item(select)
    await interaction.response.send_message("Who's moving in?", view=view, ephemeral=True)


async def _act_immunity(interaction: discord.Interaction):
    ins = housemates()
    if not ins:
        await _reply(interaction, "No housemates.", refresh=False)
        return

    async def toggled(inter: discord.Interaction, uid: int) -> bool:
        now_immune = db_toggle_immunity(uid)
        log_event("immunity_toggled", target=uid, immune=now_immune)
        if now_immune:
            await dm_user(inter.client, uid, embed=bb_embed(
                "Immunity", "You are immune from the next nominations. Housemates won't be able to pick you."))
        asyncio.create_task(refresh_panel(inter.client))
        return now_immune

    immune = immune_ids()
    await _send_toggle(interaction,
                       "🟢 immune · 🔴 not immune. Press a name to flip it. Immunity lasts until nominations close.",
                       interaction.guild, ins, {u: u in immune for u in ins}, toggled)


async def _act_token(interaction: discord.Interaction):
    ins = housemates()
    if not ins:
        await _reply(interaction, "No housemates.", refresh=False)
        return

    async def pressed(inter: discord.Interaction, uid: int) -> int:
        total = grant_token(uid)
        log_event("token_granted", target=uid, tokens=total, source="host")
        asyncio.create_task(dm_user(inter.client, uid, ack="immunity token", embed=bb_embed("Immunity token", _token_blurb(total))))
        return total

    await _send_count(interaction,
                      "Each press gives that housemate one immunity token and DMs them. The number is how many they hold.",
                      interaction.guild, ins, {u: tokens_of(u) for u in ins}, pressed)


async def _act_snug(interaction: discord.Interaction):
    ins = housemates()
    if len(ins) < 2:
        await _reply(interaction, "Need at least two housemates.", refresh=False)
        return

    async def picked(inter: discord.Interaction, ids: list[int]):
        async def submitted(inter2: discord.Interaction, values: dict):
            await inter2.response.defer(ephemeral=True)
            thread, err = await open_snug(inter2.client, None, ids, reason=values.get("reason", ""))
            await _reply(inter2, err or f"Snug opened: <#{thread.id}>", refresh=False)
        await inter.response.send_modal(_TextModal("Open a snug", [
            ("reason", "What's it for? (optional, shown in the thread)", False, 500, True)], submitted))

    view = _HousematePicker(interaction.guild, ins, picked, placeholder="Who goes in the snug?",
                            min_values=2, max_values=min(25, len(ins)))
    await interaction.response.send_message("Pick the housemates for the snug.", view=view, ephemeral=True)


def _token_blurb(total: int) -> str:
    return (f"You now hold **{total}** immunity token{'s' if total != 1 else ''}.\n\n"
            f"Press **Use immunity** on the house panel to spend one:\n"
            f"• protect yourself from the next nominations\n"
            f"• give that protection to another housemate\n"
            f"• or bank it, and if you're ever facing the public vote, swap yourself out for "
            f"a housemate who wasn't nominated.")


async def _act_mission(interaction: discord.Interaction):
    ins = housemates()
    if not ins:
        await _reply(interaction, "No housemates.", refresh=False)
        return

    async def picked(inter: discord.Interaction, uid: int):
        async def submitted(inter2: discord.Interaction, values: dict):
            await inter2.response.defer(ephemeral=True)
            brief = values["brief"]
            mid = add_mission(uid, brief)
            log_event("mission_assigned", target=uid, mission_id=mid, brief=brief)
            ok = await dm_user(inter2.client, uid, ack=f"mission #{mid} brief", embed=bb_embed(
                "Secret mission",
                f"{brief}\n\nComplete it without the other housemates noticing. "
                f"Big Brother will let you know when it's done. Press **My mission** on the house panel to see this again."))
            await notify_host(inter2.client, embed=bb_embed(
                f"Mission #{mid} assigned", f"{_mention_and_name(inter2.guild, uid)}\n\n{brief}"
                + ("" if ok else "\n\n⚠️ Their DMs are closed - the brief did not reach them.")))
            await _reply(inter2, f"Mission #{mid} sent to {_name(inter2.guild, uid)}." + ("" if ok else " Their DMs are closed."))

        await inter.response.send_modal(_TextModal(
            f"Mission for {_name(inter.guild, uid)}",
            [("brief", "The secret mission", True, 1000, True)], submitted))

    busy = {m["user_id"] for m in active_missions()}
    await _send_pick(interaction,
                     "Pick the housemate." + (" Highlighted ones already have an active mission." if busy else ""),
                     interaction.guild, ins, picked, marked=busy)


async def _act_resolve_mission(interaction: discord.Interaction):
    missions = active_missions()
    if not missions:
        await _reply(interaction, "No active missions.", refresh=False)
        return
    view = discord.ui.View(timeout=300)
    options = [discord.SelectOption(label=f"#{m['id']} {_name(interaction.guild, m['user_id'])}"[:100],
                                    description=m["brief"][:100], value=str(m["id"])) for m in missions[:25]]
    select = discord.ui.Select(placeholder="Which mission?", options=options)

    async def cb(inter: discord.Interaction):
        mid = int(select.values[0])
        v = discord.ui.View(timeout=120)
        done = discord.ui.Button(label="Completed", style=discord.ButtonStyle.success)
        failed = discord.ui.Button(label="Failed", style=discord.ButtonStyle.danger)

        async def _finish(inter2: discord.Interaction, status: str):
            await inter2.response.defer(ephemeral=True)
            m = resolve_mission(mid, status)
            if not m:
                await _reply(inter2, "Mission not found.", refresh=False)
                return
            log_event("mission_resolved", target=m["user_id"], mission_id=mid, status=status, brief=m["brief"])
            if status == "done":
                total = grant_token(m["user_id"])
                log_event("token_granted", target=m["user_id"], tokens=total, source=f"mission:{mid}")
                await dm_user(inter2.client, m["user_id"], ack=f"mission #{mid} completed + token", embed=bb_embed(
                    "Mission complete",
                    f"Big Brother is pleased. *{m['brief']}*\n\n🎟️ You've earned an **immunity token**. "
                    + _token_blurb(total)))
            else:
                await dm_user(inter2.client, m["user_id"], embed=bb_embed(
                    "Mission failed", f"You were rumbled. {m['brief']}"))
            await _reply(inter2, f"Mission #{mid} marked {status}. The housemate has been told"
                         + (" and given an immunity token." if status == "done" else "."))

        async def _d(i): await _finish(i, "done")
        async def _f(i): await _finish(i, "failed")
        done.callback, failed.callback = _d, _f
        v.add_item(done)
        v.add_item(failed)
        await inter.response.edit_message(content=f"Mission #{mid}: how did it go?", view=v)
    select.callback = cb
    view.add_item(select)
    await interaction.response.send_message("Pick the mission to resolve.", view=view, ephemeral=True)


async def _act_challenge(interaction: discord.Interaction):
    if open_challenge():
        await _reply(interaction, "A challenge is already open. End it first.", refresh=False)
        return

    async def submitted(inter: discord.Interaction, values: dict):
        await inter.response.defer(ephemeral=True, thinking=True)
        ch = await house_channel(inter.client)
        if not ch:
            await _reply(inter, "House channel not found.", refresh=False)
            return
        answer = values.get("answer") or None
        embed = bb_embed(values["title"], values["body"])
        if answer:
            embed.add_field(name="How to win", value="First housemate to post the exact answer in this channel wins.")
        msg = await bb_send(ch, content=f"{_role_mention()}{EYE} **Challenge time.**", embed=embed)
        cid = add_challenge(values["title"], values["body"], answer, msg.id)
        log_event("challenge_posted", challenge_id=cid, title=values["title"], body=values["body"], answer=answer)
        await _reply(inter, f"Challenge #{cid} posted." + (" The bot will spot the first correct answer." if answer else ""))

    await interaction.response.send_modal(_TextModal("New challenge", [
        ("title", "Title", True, 100, False),
        ("body", "The challenge", True, 1500, True),
        ("answer", "Exact answer (optional, auto-judged)", False, 200, False),
    ], submitted))


async def _act_end_challenge(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    chal = open_challenge()
    if not chal:
        await _reply(interaction, "No challenge is open.", refresh=False)
        return
    close_challenge(chal["id"], None)
    log_event("challenge_ended", challenge_id=chal["id"], title=chal["title"])
    ch = await house_channel(interaction.client)
    if ch:
        await bb_send(ch, f"{EYE} **Challenge over:** {chal['title']}. Big Brother will announce the outcome.")
    await _reply(interaction, f"Challenge #{chal['id']} closed with no auto-winner.")


async def _act_dm(interaction: discord.Interaction):
    ins = housemates()
    if not ins:
        await _reply(interaction, "No housemates.", refresh=False)
        return

    async def picked(inter: discord.Interaction, ids: list[int]):
        async def submitted(inter2: discord.Interaction, values: dict):
            await inter2.response.defer(ephemeral=True)
            sent, closed = 0, []
            for uid in ids:
                ok = await dm_user(inter2.client, uid, ack=f"DM: {values['text'][:60]}",
                                   embed=bb_embed("Big Brother", values["text"]))
                log_event("bb_dm", target=uid, text=values["text"], delivered=ok)
                sent += ok
                if not ok:
                    closed.append(_name(inter2.guild, uid))
            await _reply(inter2, f"Sent to {sent}." + (f" DMs closed: {', '.join(closed)}" if closed else ""), refresh=False)
        await inter.response.send_modal(_TextModal("Message from Big Brother",
                                                   [("text", "Message", True, 1500, True)], submitted))

    view = _HousematePicker(interaction.guild, ins, picked, placeholder="Who should hear from Big Brother?",
                            max_values=min(25, len(ins)))
    await interaction.response.send_message("Pick one or more housemates.", view=view, ephemeral=True)


async def _act_broadcast(interaction: discord.Interaction):
    async def submitted(inter: discord.Interaction, values: dict):
        await inter.response.defer(ephemeral=True, thinking=True)
        ch = await house_channel(inter.client)
        if not ch:
            await _reply(inter, "House channel not found.", refresh=False)
            return
        ping = _role_mention() if values.get("ping", "").lower().startswith("y") else ""
        text = values["text"]
        if len(text) < 1800:
            await bb_send(ch, content=f"{ping}{EYE} {text}")
        else:
            await bb_send(ch, content=ping or None, embed=bb_embed("Big Brother", text))
        log_event("bb_announcement", text=text, pinged=bool(ping))
        await _reply(inter, "Posted in the house.", refresh=False)

    await interaction.response.send_modal(_TextModal("Announce in the house", [
        ("text", "Announcement", True, 1800, True),
        ("ping", "Ping the housemate role? (yes/no)", False, 3, False),
    ], submitted))


async def _act_crown(interaction: discord.Interaction):
    ins = housemates()
    if not ins:
        await _reply(interaction, "No housemates.", refresh=False)
        return

    async def picked(inter: discord.Interaction, uid: int):
        async def yes(inter2: discord.Interaction):
            await inter2.response.defer(ephemeral=True)
            paid, note = await crown_winner(inter2.client, uid)
            await notify_host(inter2.client, f"{EYE} {note}")
            await _reply(inter2, note)

        await inter.response.edit_message(
            content=f"Crown **{_name(inter.guild, uid)}** and pay {prize_ukp():,} UKP? This announces it in the house.",
            view=_Confirm(yes, "Crown"))

    await _send_pick(interaction, "Pick the winner.", interaction.guild, ins, picked,
                     style=discord.ButtonStyle.success)


async def _act_refresh(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    await refresh_panel(interaction.client)
    await interaction.followup.send("Panel refreshed.", ephemeral=True)


PANEL_ACTIONS = {
    "start": _act_start, "silence": _act_silence, "open_noms": _act_open_noms, "close_noms": _act_close_noms, "start_vote": _act_start_vote,
    "close_vote": _act_close_vote, "evict": _act_evict, "add": _act_add, "immunity": _act_immunity,
    "mission": _act_mission, "resolve_mission": _act_resolve_mission, "challenge": _act_challenge,
    "token": _act_token, "snug": _act_snug,
    "end_challenge": _act_end_challenge, "dm": _act_dm, "broadcast": _act_broadcast,
    "crown": _act_crown, "refresh": _act_refresh,
}


# ---------------------------------------------------------------------------
# House panel (in the house channel; everyone sees the buttons, every follow-up is
# ephemeral or a modal, so only the presser and Big Brother ever see what was said)
# ---------------------------------------------------------------------------

async def handle_diary(interaction: discord.Interaction, anonymous: bool = False):

    async def submitted(inter: discord.Interaction, values: dict):
        await inter.response.defer(ephemeral=True, thinking=True)
        text = values["text"]
        add_diary(inter.user.id, text, anonymous)
        log_event("diary", actor=inter.user.id, anonymous=anonymous, text=text)
        who = "An anonymous housemate" if anonymous else _mention_and_name(inter.guild, inter.user.id)
        await notify_host(inter.client, embed=bb_embed("Diary room", f"**{who}** says:\n\n{text}"))
        await inter.edit_original_response(
            content=f"{EYE} Big Brother has heard you." + (" Your name was not attached." if anonymous else ""))

    await interaction.response.send_modal(_TextModal(
        "Diary room" + (" (anonymous)" if anonymous else ""),
        [("text", "Tell Big Brother what you really think", True, 1500, True)], submitted))


async def handle_nominate(interaction: discord.Interaction):
    rnd = open_round(KIND_NOMINATIONS)
    if not rnd:
        await interaction.response.send_message("Nominations aren't open right now.", ephemeral=True)
        return
    immune = immune_ids()
    eligible = [u for u in housemates() if u != interaction.user.id and u not in immune]
    n = min(nominations_each(), len(eligible))
    if n == 0:
        await interaction.response.send_message("There's nobody you can nominate.", ephemeral=True)
        return
    already = {b for a, b in nominations_for(rnd["id"]) if a == interaction.user.id}

    async def done(inter: discord.Interaction, ids: list[int]):
        await inter.response.defer(ephemeral=True)
        record_nominations(rnd["id"], inter.user.id, ids)
        log_event("nominated", actor=inter.user.id, round_id=rnd["id"], nominees=ids, changed=bool(already))
        names = ", ".join(_name(inter.guild, i) for i in ids)
        await notify_host(inter.client, embed=bb_embed(
            f"Nomination (round {rnd['id']})",
            f"{_mention_and_name(inter.guild, inter.user.id)} nominated **{names}**"
            + ("\n-# (changed their earlier nomination)" if already else "")))
        await inter.edit_original_response(
            content=f"{EYE} Noted. You nominated **{names}**. Only Big Brother knows.", view=None)
        asyncio.create_task(refresh_panel(inter.client))

    view = _HousematePicker(interaction.guild, eligible, done,
                            placeholder=f"Pick {n} housemate{'s' if n != 1 else ''}",
                            min_values=n, max_values=n, defaults=already)
    await interaction.response.send_message(
        f"Choose the {n} housemate{'s' if n != 1 else ''} you're nominating for eviction."
        + (" You've already nominated; submitting again replaces it." if already else ""),
        view=view, ephemeral=True)


async def handle_mission(interaction: discord.Interaction):
    m = active_mission_for(interaction.user.id)
    if not m:
        await interaction.response.send_message(f"{EYE} No mission right now. Big Brother may be in touch.", ephemeral=True)
        return
    await interaction.response.send_message(embed=bb_embed(
        "Your secret mission", f"{m['brief']}\n\n-# Assigned <t:{m['created_at']}:R>"), ephemeral=True)


async def handle_expose(interaction: discord.Interaction):
    others = [u for u in housemates() if u != interaction.user.id]
    if not others:
        await interaction.response.send_message("There's nobody else in the house.", ephemeral=True)
        return

    async def picked(inter: discord.Interaction, suspect: int):
        async def submitted(inter2: discord.Interaction, values: dict):
            await inter2.response.defer(ephemeral=True)
            on_mission = active_mission_for(suspect) is not None
            log_event("exposed", actor=inter2.user.id, target=suspect, what=values["what"], was_on_mission=on_mission)
            await notify_host(inter2.client, embed=bb_embed(
                "Exposure attempt",
                f"{_mention_and_name(inter2.guild, inter2.user.id)} thinks "
                f"{_mention_and_name(inter2.guild, suspect)} is on a mission:\n\n{values['what']}\n\n"
                f"-# {_name(inter2.guild, suspect)} {'DOES' if on_mission else 'does NOT'} currently have an active mission."))
            await inter2.edit_original_response(
                content=f"{EYE} Big Brother has noted your suspicion about **{_name(inter2.guild, suspect)}**. "
                        f"You'll find out if you were right.", view=None)

        await inter.response.send_modal(_TextModal(
            f"Expose {_name(inter.guild, suspect)}",
            [("what", "What do you think they're up to?", True, 500, True)], submitted))

    await _send_pick(interaction, "Who do you suspect?", interaction.guild, others, picked)


async def handle_housemates(interaction: discord.Interaction):
    ins = housemates()
    out = housemates(STATUS_EVICTED)
    winner = housemates(STATUS_WINNER)
    g = interaction.guild
    desc = "**In the house:**\n" + ("\n".join(f"• {_name(g, u)}" for u in ins) or "nobody")
    if out:
        desc += "\n\n**Evicted:**\n" + "\n".join(f"• {_name(g, u)}" for u in out)
    if winner:
        desc += "\n\n**Winner:** 👑 " + ", ".join(_name(g, u) for u in winner)
    await interaction.response.send_message(embed=bb_embed("The house", desc), ephemeral=True)


async def _house_diary(interaction):
    await handle_diary(interaction, anonymous=False)


async def _house_diary_anon(interaction):
    await handle_diary(interaction, anonymous=True)


async def handle_use_immunity(interaction: discord.Interaction):
    me = interaction.user.id
    have = tokens_of(me)
    if have <= 0:
        await interaction.response.send_message(
            f"{EYE} You don't hold an immunity token. Complete a secret mission to earn one.", ephemeral=True)
        return
    vote = open_round(KIND_VOTE)
    facing = bool(vote and me in vote["nominees"])
    others = [u for u in housemates() if u != me]

    view = discord.ui.View(timeout=300)
    protect = discord.ui.Button(label="Protect myself", style=discord.ButtonStyle.primary, emoji="🛡️")
    gift = discord.ui.Button(label="Give to a housemate", emoji="🎁")
    swap = discord.ui.Button(label="Save & replace", style=discord.ButtonStyle.danger, emoji="🔁", disabled=not facing)

    async def _protect(inter: discord.Interaction):
        if me in immune_ids():
            await inter.response.edit_message(content="You're already immune from the next nominations.", view=None)
            return
        if not spend_token(me):
            await inter.response.edit_message(content="No token left.", view=None)
            return
        set_immune(me, True)
        log_event("immunity_used", actor=me, target=me, mode="self")
        asyncio.create_task(notify_host(inter.client, f"{EYE} {_mention_and_name(inter.guild, me)} used a token: immune from the next nominations."))
        await inter.response.edit_message(
            content=f"{EYE} Done. You're immune from the next nominations. Nobody will be able to pick you.", view=None)
        asyncio.create_task(refresh_panel(inter.client))

    async def _gift(inter: discord.Interaction):
        async def picked(inter2: discord.Interaction, target: int):
            if target in immune_ids():
                await inter2.response.edit_message(content=f"{_name(inter2.guild, target)} is already immune.", view=None)
                return
            if not spend_token(me):
                await inter2.response.edit_message(content="No token left.", view=None)
                return
            set_immune(target, True)
            log_event("immunity_used", actor=me, target=target, mode="gift")
            asyncio.create_task(dm_user(inter2.client, target, embed=bb_embed(
                "A gift", f"{_name(inter2.guild, me)} has given you immunity from the next nominations.")))
            asyncio.create_task(notify_host(inter2.client, f"{EYE} {_mention_and_name(inter2.guild, me)} gave immunity to {_mention_and_name(inter2.guild, target)}."))
            await inter2.response.edit_message(
                content=f"{EYE} Done. {_name(inter2.guild, target)} is immune from the next nominations, and knows it came from you.", view=None)
            asyncio.create_task(refresh_panel(inter2.client))
        await _send_pick(inter, "Who gets your immunity?", inter.guild, others, picked, edit=True)

    async def _swap(inter: discord.Interaction):
        vote_now = open_round(KIND_VOTE)
        if not vote_now or me not in vote_now["nominees"]:
            await inter.response.edit_message(content="You're not facing the public vote right now.", view=None)
            return
        immune = immune_ids()
        pool = [u for u in others if u not in vote_now["nominees"] and u not in immune]
        if not pool:
            await inter.response.edit_message(content="There's nobody you can swap with.", view=None)
            return

        async def picked(inter2: discord.Interaction, target: int):
            rnd = open_round(KIND_VOTE)
            if not rnd or me not in rnd["nominees"] or target in rnd["nominees"]:
                await inter2.response.edit_message(content="The vote changed under you. Try again.", view=None)
                return
            if not spend_token(me):
                await inter2.response.edit_message(content="No token left.", view=None)
                return
            await inter2.response.defer()
            replace_nominee(rnd["id"], me, target)
            log_event("immunity_used", actor=me, target=target, mode="swap", round_id=rnd["id"])
            new_nominees = get_round(rnd["id"])["nominees"]
            guild = inter2.guild
            # Re-post the buttons on the public vote and tell voters why.
            ch = await _channel(inter2.client, rnd["channel_id"]) if rnd["channel_id"] else None
            if ch and rnd["message_id"]:
                try:
                    msg = await ch.fetch_message(rnd["message_id"])
                    await msg.edit(embed=_vote_embed(new_nominees, guild), view=_vote_view(rnd["id"], new_nominees, guild))
                    await bb_send(ch, f"{EYE} **Save and replace.** {_name(guild, me)} has used immunity to leave the "
                                  f"eviction line-up, and **{_name(guild, target)}** takes their place. "
                                  f"Votes for {_name(guild, me)} have been cleared. If that was your vote, vote again.")
                except discord.HTTPException:
                    log.exception("Big Brother: could not update vote message after swap")
            hc = await house_channel(inter2.client)
            if hc and (not ch or hc.id != ch.id):
                await bb_send(hc, f"{_role_mention()}{EYE} **Save and replace.** {_name(guild, me)} has used immunity "
                              f"and **{_name(guild, target)}** now faces the public vote instead.")
            await dm_user(inter2.client, target, embed=bb_embed(
                "You're facing eviction", f"{_name(guild, me)} used immunity to swap out of the vote and put you in."))
            await notify_host(inter2.client, f"{EYE} {_mention_and_name(guild, me)} swapped out of the vote for "
                                             f"{_mention_and_name(guild, target)}. Votes for them were cleared.")
            await inter2.edit_original_response(
                content=f"{EYE} Done. You're out of the line-up and {_name(guild, target)} is in.", view=None)
            asyncio.create_task(refresh_panel(inter2.client))

        await _send_pick(inter, "Who takes your place in the vote?", inter.guild, pool, picked, edit=True)

    protect.callback, gift.callback, swap.callback = _protect, _gift, _swap
    view.add_item(protect)
    view.add_item(gift)
    view.add_item(swap)
    await interaction.response.send_message(
        f"🎟️ You hold **{have}** immunity token{'s' if have != 1 else ''}. Spend one how?\n"
        f"• **Protect myself**: nobody can nominate you in the next round.\n"
        f"• **Give to a housemate**: they get that protection instead, and they'll know it was you.\n"
        f"• **Save & replace**: only while you're facing the public vote. You leave the line-up and pick who replaces you."
        + ("" if facing else "\n-# You're not facing a vote right now, so that one's greyed out."),
        view=view, ephemeral=True)


async def handle_snug(interaction: discord.Interaction):
    me = interaction.user.id
    others = [u for u in housemates() if u != me]
    if not others:
        await interaction.response.send_message("There's nobody else in the house.", ephemeral=True)
        return
    if recent_snug_by(me, SNUG_COOLDOWN_SECONDS):
        await interaction.response.send_message(
            f"{EYE} You've opened a snug recently. Try again in a couple of hours.", ephemeral=True)
        return

    async def picked(inter: discord.Interaction, ids: list[int]):
        await inter.response.defer(ephemeral=True)
        thread, err = await open_snug(inter.client, me, [me] + ids)
        if err:
            await inter.edit_original_response(content=err, view=None)
            return
        await notify_host(inter.client, f"{EYE} {_mention_and_name(inter.guild, me)} opened a snug with "
                                        + ", ".join(_name(inter.guild, i) for i in ids) + f": <#{thread.id}>")
        await inter.edit_original_response(content=f"{EYE} The snug is open: <#{thread.id}>. Big Brother is listening.", view=None)

    view = _HousematePicker(interaction.guild, others, picked, placeholder="Who joins you in the snug?",
                            max_values=min(4, len(others)))
    await interaction.response.send_message(
        "Pick up to four housemates to take into the snug. It's a private thread, just you, them and Big Brother.",
        view=view, ephemeral=True)


HOUSE_ACTIONS = {
    "diary": _house_diary, "diary_anon": _house_diary_anon, "nominate": handle_nominate,
    "mission": handle_mission, "expose": handle_expose, "housemates": handle_housemates,
    "immunity": handle_use_immunity, "snug": handle_snug,
}
# Anyone in the server may press these; the rest need to be a housemate.
HOUSE_PUBLIC_ACTIONS = {"housemates"}


class _HouseButton(_PanelButton):
    prefix = "bb:house"

    def registry(self) -> dict:
        return HOUSE_ACTIONS


def _house_panel_text(guild: Optional[discord.Guild]) -> str:
    ins = housemates()
    noms = open_round(KIND_NOMINATIONS)
    vote = open_round(KIND_VOTE)
    n = nominations_each()
    lines = [f"## {EYE} The Big Brother House",
             f"**{len(ins)}** housemates remain · **{len(housemates(STATUS_EVICTED))}** evicted",
             ""]
    if not house_unlocked():
        lines.append("🚪 **The doors aren't open yet.** Big Brother will unlock this panel when the game starts.")
        lines.append("")
        lines.append("-# Everything you press here is between you and Big Brother. Nobody else sees it.")
        return "\n".join(lines)
    if noms:
        lines.append(f"📝 **Nominations are open.** Pick the {n} housemate{'s' if n != 1 else ''} you want to face the public vote.")
    else:
        lines.append("📝 Nominations are closed.")
    if vote:
        lines.append(f"🗳️ **Eviction vote is open** in <#{vote['channel_id']}>.")
    missions = len(active_missions())
    if missions:
        lines.append(f"🕵️ **{missions}** secret mission{'s' if missions != 1 else ''} in play. Trust no one.")
    lines.append("")
    lines.append("-# Everything you press here is between you and Big Brother. Nobody else sees it.")
    return "\n".join(lines)


class HousePanelView(discord.ui.LayoutView):
    def __init__(self, guild: Optional[discord.Guild] = None):
        super().__init__(timeout=None)
        card = discord.ui.Container(accent_colour=ACCENT)
        card.add_item(discord.ui.TextDisplay(_house_panel_text(guild)))

        noms_open = open_round(KIND_NOMINATIONS) is not None
        sections = [
            ("### 🎙️ Diary room\n-# A private word with Big Brother. Anonymous hides your name even from her.", [
                [_HouseButton("diary", "Diary room", discord.ButtonStyle.primary, "🎙️"),
                 _HouseButton("diary_anon", "Diary room (anonymous)", emoji="🎭")],
            ]),
            (("### 🗳️ Nominations are open\n-# Pick who you want to face the public vote. Only you and Big Brother will know. "
              "Spend an immunity token to protect yourself, gift it, or save-and-replace when you're up for eviction.")
             if noms_open else
             "### 🎟️ Immunity\n-# Spend an immunity token to protect yourself, gift it, or save-and-replace when you're up for eviction.", [
                ([_HouseButton("nominate", "Nominate", discord.ButtonStyle.danger, "📝")] if noms_open else [])
                + [_HouseButton("immunity", "Use immunity", emoji="🎟️")],
            ]),
            ("### 🕵️ Secret missions\n-# Complete yours unnoticed to earn an immunity token. Spot someone else on one? Expose them.", [
                [_HouseButton("mission", "My mission", emoji="🕵️"),
                 _HouseButton("expose", "Expose a housemate", emoji="🔦")],
            ]),
            ("### 🛋️ The house\n-# The snug is a private thread for you and up to four others to talk strategy. Big Brother listens.", [
                [_HouseButton("snug", "The snug", emoji="🛋️"),
                 _HouseButton("housemates", "Who's in the house", emoji="🏠")],
            ]),
        ]
        locked = not house_unlocked()
        for heading, rows in sections:
            card.add_item(discord.ui.Separator())
            card.add_item(discord.ui.TextDisplay(heading))
            for row in rows:
                for btn in row:
                    btn.disabled = locked
                card.add_item(discord.ui.ActionRow(*row))
        self.add_item(card)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not enabled():
            await interaction.response.send_message("Big Brother isn't running right now.", ephemeral=True)
            return False
        if not house_unlocked():
            await interaction.response.send_message(f"{EYE} The doors aren't open yet.", ephemeral=True)
            return False
        cid = (interaction.data or {}).get("custom_id", "")
        action = cid.rsplit(":", 1)[-1]
        if action in HOUSE_PUBLIC_ACTIONS:
            return True
        if not is_housemate(interaction.user.id):
            await interaction.response.send_message(f"{EYE} Only housemates can press that.", ephemeral=True)
            return False
        return True


async def handle_panel(interaction: discord.Interaction):
    if not enabled() or not is_operator(interaction.user.id):
        await interaction.response.send_message("Not for you.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    set_state(STATE_PANEL_MSG, None)
    set_state(STATE_HOUSE_PANEL_MSG, None)
    await ensure_panels(interaction.client)
    await interaction.followup.send("Both panels re-posted.", ephemeral=True)


# ---------------------------------------------------------------------------
# House channel message hook
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    return " ".join(s.lower().strip().strip(".!?\"'`").split())


async def on_house_message(client: discord.Client, message: discord.Message) -> None:
    """Called for every human message in the house channel: activity tracking and
    auto-judging an open challenge with an exact answer."""
    global _client_ref
    _client_ref = client
    if not enabled() or message.author.bot:
        return
    try:
        store_message(message)
    except Exception:
        log.exception("Big Brother: transcript store failed")
    if not is_housemate(message.author.id):
        return
    try:
        touch_activity(message.author.id)
    except Exception:
        log.exception("Big Brother: activity update failed")
    if isinstance(message.channel, discord.Thread):
        return  # snug chatter is recorded, but challenge answers only count in the house itself
    # Keep the panel within reach: after every few chat messages, move it back to the bottom.
    try:
        n = int(get_state(STATE_HOUSE_MSGS_SINCE_PANEL, 0) or 0) + 1
        if n >= HOUSE_PANEL_REPOST_EVERY:
            asyncio.create_task(repost_house_panel(client))
        else:
            set_state(STATE_HOUSE_MSGS_SINCE_PANEL, n)
    except Exception:
        log.exception("Big Brother: panel repost bookkeeping failed")
    chal = open_challenge()
    if not chal or not chal.get("answer"):
        return
    if _norm(message.content or "") != _norm(chal["answer"]):
        return
    # First correct answer wins; close before announcing so a second one can't also win.
    if not open_challenge() or open_challenge()["id"] != chal["id"]:
        return
    close_challenge(chal["id"], message.author.id)
    log_event("challenge_won", actor=message.author.id, challenge_id=chal["id"], title=chal["title"],
              message_id=message.id)
    try:
        await bb_send(message.channel, f"{EYE} **Correct.** {message.author.mention} wins **{chal['title']}**.",
                      reply_to=message)
    except discord.HTTPException:
        pass
    await notify_host(client, f"{EYE} Challenge **{chal['title']}** won by "
                              f"{_mention_and_name(message.guild, message.author.id)}.")
    asyncio.create_task(refresh_panel(client))


# ---------------------------------------------------------------------------
# Rundown export: everything, as JSON plus a readable timeline
# ---------------------------------------------------------------------------

def _fmt_ts(ts: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def build_rundown(guild: Optional[discord.Guild]) -> tuple[dict, str]:
    """Returns (json-able dump, markdown timeline). Names are resolved from the guild so
    the files still read well once the roles and channel are gone."""
    ensure_tables()
    def n(uid):
        return _name(guild, uid) if uid is not None else None

    rows = DatabaseManager.fetch_all(
        "SELECT user_id, joined_at, status, evicted_at, immune, tokens FROM bb_housemates ORDER BY joined_at")
    hm = [{"user_id": int(r[0]), "name": n(int(r[0])), "joined_at": r[1], "status": r[2],
           "evicted_at": r[3], "immune": bool(r[4]), "tokens": int(r[5] or 0)} for r in rows]
    rounds = [dict(_round_row(r), closed_at=r[7]) for r in DatabaseManager.fetch_all(
        "SELECT id, kind, status, opened_at, channel_id, message_id, nominees, closed_at FROM bb_rounds ORDER BY id")]
    noms = [{"round_id": r[0], "nominator": int(r[1]), "nominator_name": n(int(r[1])),
             "nominee": int(r[2]), "nominee_name": n(int(r[2])), "at": r[3]}
            for r in DatabaseManager.fetch_all(
                "SELECT round_id, nominator_id, nominee_id, created_at FROM bb_nominations ORDER BY created_at")]
    votes = [{"round_id": r[0], "voter": int(r[1]), "voter_name": n(int(r[1])),
              "nominee": int(r[2]), "nominee_name": n(int(r[2])), "at": r[3]}
             for r in DatabaseManager.fetch_all(
                 "SELECT round_id, voter_id, nominee_id, created_at FROM bb_votes ORDER BY created_at")]
    diary = [{"id": r[0], "user_id": int(r[1]), "name": n(int(r[1])), "anonymous": bool(r[2]),
              "text": r[3], "at": r[4]}
             for r in DatabaseManager.fetch_all(
                 "SELECT id, user_id, anonymous, text, created_at FROM bb_diary ORDER BY id")]
    missions = [{"id": r[0], "user_id": int(r[1]), "name": n(int(r[1])), "brief": r[2], "status": r[3],
                 "at": r[4], "resolved_at": r[5]}
                for r in DatabaseManager.fetch_all(
                    "SELECT id, user_id, brief, status, created_at, resolved_at FROM bb_missions ORDER BY id")]
    challenges = [{"id": r[0], "title": r[1], "body": r[2], "answer": r[3], "status": r[4],
                   "winner": int(r[5]) if r[5] else None, "winner_name": n(int(r[5])) if r[5] else None,
                   "at": r[6], "resolved_at": r[7]}
                  for r in DatabaseManager.fetch_all(
                      "SELECT id, title, body, answer, status, winner_id, created_at, resolved_at "
                      "FROM bb_challenges ORDER BY id")]
    msgs = [{"message_id": int(r[0]), "user_id": int(r[1]), "name": n(int(r[1])), "content": r[2],
             "at": r[3], "attachments": r[4], "reply_to": int(r[5]) if r[5] else None,
             "thread_id": int(r[6]) if r[6] else None}
            for r in DatabaseManager.fetch_all(
                "SELECT message_id, user_id, content, at, attachments, reply_to, thread_id FROM bb_messages ORDER BY at")]
    snug_rows = [dict(sn, member_names=[n(m) for m in sn["members"]]) for sn in snugs()]
    activity = [{"user_id": int(r[0]), "name": n(int(r[0])), "messages": r[2], "last_message_at": r[1]}
                for r in DatabaseManager.fetch_all(
                    "SELECT user_id, last_message_at, messages FROM bb_activity ORDER BY messages DESC")]
    evs = events()
    for e in evs:
        e["actor_name"] = n(e["actor_id"])
        e["target_name"] = n(e["target_id"])

    acks = [{"id": r[0], "user_id": int(r[1]), "name": n(int(r[1])), "label": r[2], "sent_at": r[3], "acked_at": r[4]}
            for r in DatabaseManager.fetch_all("SELECT id, user_id, label, sent_at, acked_at FROM bb_acks ORDER BY id")]
    dump = {"exported_at": _now(), "housemates": hm, "acks": acks, "rounds": rounds, "nominations": noms, "votes": votes,
            "diary": diary, "missions": missions, "challenges": challenges, "events": evs,
            "activity": activity, "snugs": snug_rows, "house_messages": msgs}

    lines = [f"# Big Brother rundown", f"Exported {_fmt_ts(_now())} UTC", "",
             "## Housemates"]
    for h in hm:
        lines.append(f"- {h['name']} - {h['status']}" + (f" (evicted {_fmt_ts(h['evicted_at'])})" if h["evicted_at"] else ""))
    lines += ["", "## Timeline"]
    for e in evs:
        kind = e["kind"]
        who = e.get("actor_name") or ""
        tgt = e.get("target_name") or ""
        if kind == "nominated":
            names = ", ".join(n(x) for x in e.get("nominees", []))
            detail = f"{who} nominated {names}" + (" (changed)" if e.get("changed") else "")
        elif kind == "vote_cast":
            detail = f"{who} voted to evict {tgt}"
        elif kind == "nominations_closed":
            detail = "nominations closed: " + ", ".join(f"{n(x)} {c}" for x, c in e.get("tally", []))
        elif kind == "vote_closed":
            detail = f"vote closed ({e.get('total')} votes): " + ", ".join(f"{n(x)} {c}" for x, c in e.get("tally", []))
        elif kind == "vote_opened":
            detail = "vote opened: " + ", ".join(n(x) for x in e.get("nominees", []))
        elif kind == "diary":
            detail = f"diary ({'anonymous' if e.get('anonymous') else who}): {e.get('text', '')}"
        elif kind == "exposed":
            detail = f"{who} accused {tgt}: {e.get('what', '')} [{'right' if e.get('was_on_mission') else 'wrong'}]"
        elif kind in ("mission_assigned", "mission_resolved"):
            detail = f"mission #{e.get('mission_id')} for {tgt} {e.get('status', 'assigned')}: {e.get('brief', '')}"
        elif kind.startswith("challenge"):
            detail = f"{kind.replace('_', ' ')}: {e.get('title', '')}" + (f" - won by {who}" if who else "")
        elif kind == "immunity_used":
            detail = {"self": f"{who} used immunity on themselves",
                      "gift": f"{who} gave immunity to {tgt}",
                      "swap": f"{who} used save-and-replace: {tgt} takes their place in the vote"}.get(e.get("mode"), kind)
        elif kind == "token_granted":
            detail = f"{tgt} earned an immunity token ({e.get('source', '')}), now holds {e.get('tokens')}"
        elif kind == "snug_opened":
            detail = f"snug opened by {who or 'Big Brother'}: " + ", ".join(n(m) for m in e.get("members", []))
        elif kind in ("bb_dm", "bb_announcement"):
            detail = f"{kind.replace('_', ' ')}" + (f" to {tgt}" if tgt else "") + f": {e.get('text', '')}"
        else:
            detail = f"{kind.replace('_', ' ')}" + (f": {tgt}" if tgt else "")
        lines.append(f"- {_fmt_ts(e['at'])}  {detail}")
    lines += ["", "## Activity (house channel messages)"]
    for a in activity:
        lines.append(f"- {a['name']}: {a['messages']}")
    return dump, "\n".join(lines)


async def handle_export(interaction: discord.Interaction):
    """Host-only: DM the full dump (JSON) and a readable timeline (markdown) to whoever asked."""
    if not enabled() or not is_operator(interaction.user.id):
        await interaction.response.send_message("Not for you.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    import io
    dump, timeline = build_rundown(interaction.guild)
    files = [discord.File(io.BytesIO(json.dumps(dump, indent=1, ensure_ascii=False).encode()), "big_brother_full.json"),
             discord.File(io.BytesIO(timeline.encode()), "big_brother_timeline.md")]
    try:
        await interaction.user.send(content=f"{EYE} Big Brother rundown export.", files=files)
        await interaction.followup.send("Sent to your DMs.", ephemeral=True)
    except discord.HTTPException as e:
        await interaction.followup.send(f"Couldn't DM you the files: {e}", ephemeral=True)
