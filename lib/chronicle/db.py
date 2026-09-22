"""Connection and schema for ``chronicle.db``, the permanent server event log.

Deliberately a *separate* SQLite file from ``database.db``:

* ``backup_database`` re-zips and re-uploads the whole live DB to #data-backup every
  five minutes. The chronicle grows by roughly a gigabyte a year, so folding it into
  that file would make a cheap job permanently expensive; it gets its own, far less
  frequent backup instead.
* The chronicle is append-only and high-frequency while the economy tables are
  read-modify-write and latency-sensitive. Separate files mean separate WALs, so an
  insert burst here can never hold up a ``/pay``.
* It is disposable in a way balances are not - reset it by deleting one file.

Nothing in here ever raises into a Discord event handler: the bot losing a row of
history is acceptable, the bot falling over because of it is not.
"""

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager

from config import CHRONICLE_DB_FILE

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Timestamps are epoch SECONDS (UTC) everywhere. Discord snowflake ids are stored as
# INTEGER - they fit in 64 bits and sort chronologically, which makes range scans on
# message_id as good as a timestamp index.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    message_id   INTEGER PRIMARY KEY,
    guild_id     INTEGER,
    channel_id   INTEGER NOT NULL,
    parent_id    INTEGER,           -- parent channel when the message is in a thread
    user_id      INTEGER NOT NULL,
    is_bot       INTEGER NOT NULL DEFAULT 0,
    content      TEXT,
    ts           INTEGER NOT NULL,  -- message.created_at, not receipt time
    reply_to     INTEGER,
    reply_to_user INTEGER,
    attachments  TEXT,              -- JSON [{url, filename, size, content_type}]
    n_attachments INTEGER NOT NULL DEFAULT 0,
    stickers     TEXT,              -- JSON [{id, name}]
    n_embeds     INTEGER NOT NULL DEFAULT 0,
    char_count   INTEGER NOT NULL DEFAULT 0,
    word_count   INTEGER NOT NULL DEFAULT 0,
    edited_ts    INTEGER,           -- last edit seen
    deleted_ts   INTEGER,           -- set in place; rows are never removed
    source       TEXT NOT NULL DEFAULT 'live',  -- 'live' or 'backfill'
    -- Local-time buckets written at insert, because SQLite has no timezone database:
    -- "what hour do you post at" has to mean the clock people were looking at, and
    -- strftime('localtime') would silently answer in whatever TZ the box happens to run.
    local_day    TEXT,              -- YYYY-MM-DD in Europe/London
    local_hour   INTEGER            -- 0-23 in Europe/London
);
CREATE INDEX IF NOT EXISTS idx_messages_local_day ON messages(local_day);
CREATE INDEX IF NOT EXISTS idx_messages_user_ts    ON messages(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_channel_ts ON messages(channel_id, ts);
CREATE INDEX IF NOT EXISTS idx_messages_ts         ON messages(ts);
CREATE INDEX IF NOT EXISTS idx_messages_reply_to   ON messages(reply_to);

CREATE TABLE IF NOT EXISTS message_edits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    user_id     INTEGER,
    ts          INTEGER NOT NULL,
    old_content TEXT,
    new_content TEXT
);
CREATE INDEX IF NOT EXISTS idx_edits_message ON message_edits(message_id);
CREATE INDEX IF NOT EXISTS idx_edits_ts      ON message_edits(ts);

CREATE TABLE IF NOT EXISTS mentions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    ts          INTEGER NOT NULL,
    channel_id  INTEGER,
    user_id     INTEGER NOT NULL,   -- who wrote the mention
    target_id   INTEGER,            -- mentioned user/role/channel; NULL for @everyone
    kind        TEXT NOT NULL       -- user | role | channel | everyone | reply
);
CREATE INDEX IF NOT EXISTS idx_mentions_target ON mentions(target_id, ts);
CREATE INDEX IF NOT EXISTS idx_mentions_user   ON mentions(user_id, ts);

CREATE TABLE IF NOT EXISTS reactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    channel_id  INTEGER,
    guild_id    INTEGER,
    user_id     INTEGER NOT NULL,   -- who reacted
    author_id   INTEGER,            -- who wrote the message, when known
    emoji       TEXT NOT NULL,      -- unicode char, or custom emoji name
    emoji_id    INTEGER,            -- NULL for unicode
    animated    INTEGER NOT NULL DEFAULT 0,
    action      TEXT NOT NULL,      -- add | remove
    ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reactions_user    ON reactions(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_reactions_author  ON reactions(author_id, ts);
CREATE INDEX IF NOT EXISTS idx_reactions_message ON reactions(message_id);
CREATE INDEX IF NOT EXISTS idx_reactions_emoji   ON reactions(emoji, ts);

CREATE TABLE IF NOT EXISTS emoji_uses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    channel_id INTEGER,
    ts         INTEGER NOT NULL,
    emoji      TEXT NOT NULL,       -- unicode sequence, or custom emoji name
    emoji_id   INTEGER,
    n          INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_emoji_user  ON emoji_uses(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_emoji_emoji ON emoji_uses(emoji, ts);

CREATE TABLE IF NOT EXISTS voice_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    guild_id   INTEGER,
    channel_id INTEGER,
    from_channel INTEGER,
    action     TEXT NOT NULL,       -- join | leave | move | mute | unmute | deafen |
                                    -- undeafen | stream_start | stream_stop |
                                    -- video_start | video_stop | server_mute | server_deafen
    self_state TEXT,                -- JSON of the self_mute/self_deaf/stream/video flags
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_voice_user ON voice_events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_voice_ts   ON voice_events(ts);

CREATE TABLE IF NOT EXISTS member_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    guild_id INTEGER,
    action  TEXT NOT NULL,          -- join | leave | ban | unban | nick | username |
                                    -- role_add | role_remove | timeout | untimeout | boost
    detail  TEXT,                   -- JSON: old/new nick, role id+name, etc.
    ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_member_user ON member_events(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_member_act  ON member_events(action, ts);

CREATE TABLE IF NOT EXISTS interactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    guild_id   INTEGER,
    channel_id INTEGER,
    kind       TEXT NOT NULL,       -- command | component | modal | autocomplete
    name       TEXT,                -- command name, or custom_id for components
    detail     TEXT,                -- JSON of the supplied options, when it's a command
    ts         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interactions_user ON interactions(user_id, ts);
CREATE INDEX IF NOT EXISTS idx_interactions_name ON interactions(name, ts);

-- Where the history backfill got to per channel, so it can resume instead of
-- re-walking millions of messages after an interruption.
CREATE TABLE IF NOT EXISTS backfill_progress (
    channel_id    INTEGER PRIMARY KEY,
    channel_name  TEXT,
    oldest_seen   INTEGER,   -- walking backwards: the oldest message id stored so far
    newest_seen   INTEGER,
    n_messages    INTEGER NOT NULL DEFAULT 0,
    complete      INTEGER NOT NULL DEFAULT 0,
    updated_ts    INTEGER
);
"""


class ChronicleDB:
    """Thread-safe single-connection wrapper, same shape as ``DatabaseManager``."""

    _connection = None
    _lock = threading.RLock()
    _path = CHRONICLE_DB_FILE

    @classmethod
    def set_path(cls, path):
        """Point at a different file (tests, one-off analysis copies)."""
        with cls._lock:
            cls.close()
            cls._path = path

    @classmethod
    def get_connection(cls):
        if cls._connection is None:
            cls._connection = sqlite3.connect(cls._path, check_same_thread=False)
            cls._connection.execute("PRAGMA journal_mode=WAL")
            # NORMAL, not FULL: a crash can cost the last fraction of a second of
            # history, which is a fine trade for not fsyncing on every chat message.
            cls._connection.execute("PRAGMA synchronous=NORMAL")
            cls._connection.execute("PRAGMA busy_timeout=5000")
            cls._init_schema(cls._connection)
        return cls._connection

    @classmethod
    def _init_schema(cls, conn):
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()

    @classmethod
    def execute(cls, query, params=()):
        with cls._lock:
            conn = cls.get_connection()
            cur = conn.cursor()
            cur.execute(query, params)
            conn.commit()
            return cur.rowcount

    @classmethod
    def executemany(cls, query, seq):
        with cls._lock:
            conn = cls.get_connection()
            cur = conn.cursor()
            cur.executemany(query, seq)
            conn.commit()
            return cur.rowcount

    @classmethod
    def fetch_one(cls, query, params=()):
        with cls._lock:
            cur = cls.get_connection().cursor()
            cur.execute(query, params)
            return cur.fetchone()

    @classmethod
    def fetch_all(cls, query, params=()):
        with cls._lock:
            cur = cls.get_connection().cursor()
            cur.execute(query, params)
            return cur.fetchall()

    @classmethod
    @contextmanager
    def transaction(cls):
        """Several statements as one locked, atomic transaction. Yields a cursor."""
        with cls._lock:
            conn = cls.get_connection()
            try:
                yield conn.cursor()
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    @classmethod
    def snapshot_to_file(cls, dest_path):
        """Consistent copy via SQLite's online backup API - safe while writes continue."""
        with cls._lock:
            src = cls.get_connection()
            dest = sqlite3.connect(dest_path)
            try:
                src.backup(dest)
            finally:
                dest.close()

    @classmethod
    def checkpoint(cls):
        """Fold the WAL back into the .db so the file on disk is self-contained."""
        with cls._lock:
            if cls._connection is not None:
                try:
                    cls._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    log.debug("chronicle checkpoint failed", exc_info=True)

    @classmethod
    def close(cls):
        with cls._lock:
            if cls._connection is not None:
                try:
                    cls._connection.close()
                finally:
                    cls._connection = None

    @classmethod
    def size_bytes(cls):
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(cls._path + suffix)
            except OSError:
                pass
        return total
