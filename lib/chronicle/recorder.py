"""Turns Discord events into chronicle rows, off the event loop.

Every ``record_*`` helper does only cheap attribute reads on the discord.py object it
is handed, packs the result into plain tuples and drops them on a queue; a single
background thread drains that queue and writes them to ``chronicle.db`` in batched
transactions. At ~5k messages a day the writes are nowhere near heavy enough to need
this, but it keeps SQLite entirely out of the gateway's way and makes the backfill
(which fires tens of thousands of rows in a burst) use the exact same path.

Everything here swallows its own exceptions. A missing row of history must never take
down a message handler.
"""

import json
import logging
import queue
import re
import threading
import time

import pytz

from lib.chronicle.db import ChronicleDB

log = logging.getLogger(__name__)

# Bounded so a wedged disk costs memory that is measured rather than unbounded; at
# 50k queued rows we are already ten days of chat behind and dropping is the right call.
_MAX_QUEUE = 50_000
_MAX_BATCH = 2_000
_BATCH_WINDOW = 1.0      # seconds to keep collecting before committing a batch

_queue: "queue.Queue" = queue.Queue(maxsize=_MAX_QUEUE)
_worker = None
_worker_lock = threading.Lock()
_SENTINEL = object()
_dropped = 0

MSG_SQL = (
    "INSERT OR IGNORE INTO messages (message_id, guild_id, channel_id, parent_id, user_id, "
    "is_bot, content, ts, reply_to, reply_to_user, attachments, n_attachments, stickers, "
    "n_embeds, char_count, word_count, edited_ts, source, local_day, local_hour, flags) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
MENTION_SQL = ("INSERT INTO mentions (message_id, ts, channel_id, user_id, target_id, kind) "
               "VALUES (?,?,?,?,?,?)")
EMOJI_SQL = ("INSERT INTO emoji_uses (message_id, user_id, channel_id, ts, emoji, emoji_id, n) "
             "VALUES (?,?,?,?,?,?,?)")
EDIT_SQL = ("INSERT INTO message_edits (message_id, user_id, ts, old_content, new_content) "
            "VALUES (?,?,?,?,?)")
REACTION_SQL = ("INSERT INTO reactions (message_id, channel_id, guild_id, user_id, author_id, "
                "emoji, emoji_id, animated, action, ts) VALUES (?,?,?,?,?,?,?,?,?,?)")
VOICE_SQL = ("INSERT INTO voice_events (user_id, guild_id, channel_id, from_channel, action, "
             "self_state, ts) VALUES (?,?,?,?,?,?,?)")
MEMBER_SQL = "INSERT INTO member_events (user_id, guild_id, action, detail, ts) VALUES (?,?,?,?,?)"
INTERACTION_SQL = ("INSERT INTO interactions (user_id, guild_id, channel_id, kind, name, detail, ts) "
                   "VALUES (?,?,?,?,?,?,?)")
AUDIT_SQL = ("INSERT OR IGNORE INTO audit_log (entry_id, guild_id, action, user_id, target_id, "
             "target_type, reason, changes, extra, ts) VALUES (?,?,?,?,?,?,?,?,?,?)")
POLL_SQL = ("INSERT OR IGNORE INTO polls (message_id, channel_id, user_id, question, answers, "
            "multiple, expires_ts, ts) VALUES (?,?,?,?,?,?,?,?)")
POLL_VOTE_SQL = ("INSERT INTO poll_votes (message_id, channel_id, user_id, answer_id, action, ts) "
                 "VALUES (?,?,?,?,?,?)")


# --------------------------------------------------------------------------- writer

def _ensure_worker():
    global _worker
    if _worker is not None and _worker.is_alive():
        return
    with _worker_lock:
        if _worker is not None and _worker.is_alive():
            return
        _worker = threading.Thread(target=_run, name="chronicle-writer", daemon=True)
        _worker.start()


def _run():
    while True:
        item = _queue.get()
        if item is _SENTINEL:
            _queue.task_done()
            return
        batch = [item]
        stop = False
        deadline = time.monotonic() + _BATCH_WINDOW
        while len(batch) < _MAX_BATCH:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                nxt = _queue.get(timeout=remaining)
            except queue.Empty:
                break
            if nxt is _SENTINEL:
                stop = True
                break
            batch.append(nxt)
        try:
            _write(batch)
        except Exception:
            log.error("chronicle batch write failed (%d rows dropped)", len(batch), exc_info=True)
        finally:
            for _ in batch:
                _queue.task_done()
            if stop:
                _queue.task_done()
                return


def _write(batch):
    """One transaction, with consecutive identical statements collapsed into executemany.

    Order is preserved, which matters: an edit or delete UPDATE must land after the
    INSERT of the message it refers to.
    """
    with ChronicleDB.transaction() as cur:
        run_sql, run_params = None, []
        for sql, params in batch:
            if sql != run_sql:
                if run_sql is not None:
                    cur.executemany(run_sql, run_params)
                run_sql, run_params = sql, [params]
            else:
                run_params.append(params)
        if run_sql is not None:
            cur.executemany(run_sql, run_params)


def enqueue(sql, params):
    global _dropped
    _ensure_worker()
    try:
        _queue.put_nowait((sql, params))
    except queue.Full:
        _dropped += 1
        if _dropped % 1000 == 1:
            log.error("chronicle queue full - %d rows dropped so far", _dropped)


def enqueue_many(rows):
    for sql, params in rows:
        enqueue(sql, params)


def flush(timeout=10.0):
    """Block until the queue is drained. Called on graceful shutdown and after backfills."""
    if _worker is None or not _worker.is_alive():
        return True
    done = threading.Event()
    threading.Thread(target=lambda: (_queue.join(), done.set()), daemon=True).start()
    return done.wait(timeout)


def stats():
    return {"queued": _queue.qsize(), "dropped": _dropped,
            "worker_alive": bool(_worker and _worker.is_alive())}


# --------------------------------------------------------------------- field helpers

_CUSTOM_EMOJI_RE = re.compile(r"<(a?):([A-Za-z0-9_~]{2,32}):(\d+)>")
# Deliberately broad, ZWJ-aware and not exhaustive: enough for "your most used emoji"
# without shipping an emoji database. Custom emoji (the interesting ones for a Discord
# wrapped) are matched exactly by the regex above.
_EMOJI_CHAR = "[\U0001F000-\U0001FAFF☀-➿⬀-⯿←-⇿⤀-⥿]"
_UNICODE_EMOJI_RE = re.compile(rf"{_EMOJI_CHAR}(?:[️‍\U0001F3FB-\U0001F3FF]{_EMOJI_CHAR}?)*")


_UK = pytz.timezone("Europe/London")


def _ts(dt):
    return int(dt.timestamp()) if dt else None


def _local(dt):
    """(YYYY-MM-DD, hour) in UK local time - the buckets the stats group by."""
    if dt is None:
        return None, None
    local = dt.astimezone(_UK)
    return local.strftime("%Y-%m-%d"), local.hour


def _json_or_none(value):
    return json.dumps(value, separators=(",", ":")) if value else None


def _emoji_rows(message_id, user_id, channel_id, ts, content):
    if not content:
        return []
    counts = {}
    for animated, name, eid in _CUSTOM_EMOJI_RE.findall(content):
        key = (name, int(eid))
        counts[key] = counts.get(key, 0) + 1
    stripped = _CUSTOM_EMOJI_RE.sub(" ", content)
    for match in _UNICODE_EMOJI_RE.findall(stripped):
        seq = match.strip("️")
        if not seq:
            continue
        key = (seq, None)
        counts[key] = counts.get(key, 0) + 1
    return [(EMOJI_SQL, (message_id, user_id, channel_id, ts, name, eid, n))
            for (name, eid), n in counts.items()]


# ------------------------------------------------------------------------- messages

def message_rows(message, source="live"):
    """Every row one message produces. Separate from ``record_message`` so the
    backfill can batch-insert the same tuples without going through the queue."""
    guild = message.guild
    if guild is None:               # DMs stay out of the server chronicle
        return []
    channel = message.channel
    ts = _ts(message.created_at)
    mid, uid = message.id, message.author.id
    cid = channel.id
    content = message.content or ""

    reply_to = reply_to_user = None
    ref = message.reference
    if ref is not None and ref.message_id:
        reply_to = ref.message_id
        resolved = getattr(ref, "resolved", None)
        author = getattr(resolved, "author", None)
        if author is not None:
            reply_to_user = author.id

    attachments = [{"url": a.url, "filename": a.filename, "size": a.size,
                    "content_type": a.content_type} for a in message.attachments]
    stickers = [{"id": s.id, "name": s.name} for s in getattr(message, "stickers", [])]

    local_day, local_hour = _local(message.created_at)
    # MessageFlags is how a voice note, a forward and a silent message are told apart
    # from ordinary text - all three look like an attachment or empty content otherwise.
    flags = getattr(getattr(message, "flags", None), "value", 0) or 0
    rows = [(MSG_SQL, (
        mid, guild.id, cid, getattr(channel, "parent_id", None), uid,
        int(bool(message.author.bot)), content, ts, reply_to, reply_to_user,
        _json_or_none(attachments), len(attachments), _json_or_none(stickers),
        len(message.embeds or []), len(content), len(content.split()),
        _ts(message.edited_at), source, local_day, local_hour, flags))]

    poll = getattr(message, "poll", None)
    if poll is not None:
        answers = [{"id": a.id, "text": a.text,
                    "emoji": str(a.emoji) if a.emoji else None} for a in poll.answers]
        rows.append((POLL_SQL, (mid, cid, uid, poll.question, _json_or_none(answers),
                                int(bool(poll.multiple)), _ts(poll.expires_at), ts)))

    for target in message.raw_mentions:
        rows.append((MENTION_SQL, (mid, ts, cid, uid, target, "user")))
    for target in message.raw_role_mentions:
        rows.append((MENTION_SQL, (mid, ts, cid, uid, target, "role")))
    for target in getattr(message, "raw_channel_mentions", []):
        rows.append((MENTION_SQL, (mid, ts, cid, uid, target, "channel")))
    if message.mention_everyone:
        rows.append((MENTION_SQL, (mid, ts, cid, uid, None, "everyone")))
    if reply_to_user is not None:
        rows.append((MENTION_SQL, (mid, ts, cid, uid, reply_to_user, "reply")))

    rows += _emoji_rows(mid, uid, cid, ts, content)
    return rows


def record_message(message):
    try:
        enqueue_many(message_rows(message))
    except Exception:
        log.debug("chronicle record_message failed", exc_info=True)


def record_edit(before, after):
    try:
        if after.guild is None:
            return
        now = _ts(after.edited_at) or int(time.time())
        old = before.content if before is not None else None
        new = after.content or ""
        if old == new:              # embed resolution and pins fire edits too
            return
        enqueue_many([
            (EDIT_SQL, (after.id, after.author.id, now, old, new)),
            ("UPDATE messages SET content = ?, edited_ts = ?, char_count = ?, word_count = ? "
             "WHERE message_id = ?", (new, now, len(new), len(new.split()), after.id)),
        ])
    except Exception:
        log.debug("chronicle record_edit failed", exc_info=True)


def record_raw_edit(payload):
    """Edits of messages the bot never cached: content comes from the raw payload."""
    try:
        data = payload.data or {}
        if "content" not in data:
            return
        new = data.get("content") or ""
        now = int(time.time())
        author = (data.get("author") or {}).get("id")
        enqueue_many([
            (EDIT_SQL, (payload.message_id, int(author) if author else None, now, None, new)),
            ("UPDATE messages SET content = ?, edited_ts = ?, char_count = ?, word_count = ? "
             "WHERE message_id = ?", (new, now, len(new), len(new.split()), payload.message_id)),
        ])
    except Exception:
        log.debug("chronicle record_raw_edit failed", exc_info=True)


def record_delete(message_ids):
    """Mark deleted in place - the point of the chronicle is that nothing disappears."""
    try:
        now = int(time.time())
        for mid in message_ids:
            enqueue("UPDATE messages SET deleted_ts = ? WHERE message_id = ? AND deleted_ts IS NULL",
                    (now, int(mid)))
    except Exception:
        log.debug("chronicle record_delete failed", exc_info=True)


# ------------------------------------------------------------------------ reactions

def record_raw_reaction(payload, action):
    """Fed from the RAW events so uncached (i.e. most) messages are covered too."""
    try:
        emoji = payload.emoji
        author_id = None
        cached = getattr(payload, "cached_message", None)
        if cached is not None and cached.author is not None:
            author_id = cached.author.id
        enqueue(REACTION_SQL, (
            payload.message_id, payload.channel_id, payload.guild_id, payload.user_id,
            author_id, emoji.name or str(emoji), emoji.id, int(bool(emoji.animated)),
            action, int(time.time())))
    except Exception:
        log.debug("chronicle record_raw_reaction failed", exc_info=True)


# ---------------------------------------------------------------------------- voice

def _voice_state_json(state):
    return _json_or_none({
        "self_mute": bool(state.self_mute), "self_deaf": bool(state.self_deaf),
        "mute": bool(state.mute), "deaf": bool(state.deaf),
        "stream": bool(state.self_stream), "video": bool(state.self_video),
    })


def record_voice(member, before, after):
    """One row per distinct thing that changed, so sessions can be reconstructed and
    'hours in VC' is a sum over join/leave pairs."""
    try:
        now = int(time.time())
        guild_id = member.guild.id if member.guild else None
        b_ch = before.channel.id if before.channel else None
        a_ch = after.channel.id if after.channel else None
        state = _voice_state_json(after)
        rows = []

        def add(action, channel_id, from_channel=None):
            rows.append((VOICE_SQL, (member.id, guild_id, channel_id, from_channel,
                                     action, state, now)))

        if b_ch != a_ch:
            if b_ch is None:
                add("join", a_ch)
            elif a_ch is None:
                add("leave", b_ch)
            else:
                add("move", a_ch, b_ch)
        if before.self_mute != after.self_mute:
            add("mute" if after.self_mute else "unmute", a_ch or b_ch)
        if before.self_deaf != after.self_deaf:
            add("deafen" if after.self_deaf else "undeafen", a_ch or b_ch)
        if before.mute != after.mute:
            add("server_mute" if after.mute else "server_unmute", a_ch or b_ch)
        if before.deaf != after.deaf:
            add("server_deafen" if after.deaf else "server_undeafen", a_ch or b_ch)
        if before.self_stream != after.self_stream:
            add("stream_start" if after.self_stream else "stream_stop", a_ch or b_ch)
        if before.self_video != after.self_video:
            add("video_start" if after.self_video else "video_stop", a_ch or b_ch)
        enqueue_many(rows)
    except Exception:
        log.debug("chronicle record_voice failed", exc_info=True)


# --------------------------------------------------------------------------- member

def record_member_event(user_id, action, guild_id=None, detail=None):
    try:
        enqueue(MEMBER_SQL, (int(user_id), guild_id, action,
                             _json_or_none(detail), int(time.time())))
    except Exception:
        log.debug("chronicle record_member_event failed", exc_info=True)


def record_member_update(before, after):
    """Nickname, username and role churn - the raw material for 'you changed your name
    14 times this year' and for reconstructing who held what role when."""
    try:
        guild_id = after.guild.id if after.guild else None
        if before.nick != after.nick:
            record_member_event(after.id, "nick", guild_id,
                                {"old": before.nick, "new": after.nick})
        if getattr(before, "global_name", None) != getattr(after, "global_name", None):
            record_member_event(after.id, "username", guild_id,
                                {"old": getattr(before, "global_name", None),
                                 "new": getattr(after, "global_name", None)})
        before_roles = {r.id: r.name for r in before.roles}
        after_roles = {r.id: r.name for r in after.roles}
        for rid in after_roles.keys() - before_roles.keys():
            record_member_event(after.id, "role_add", guild_id,
                                {"role_id": rid, "role": after_roles[rid]})
        for rid in before_roles.keys() - after_roles.keys():
            record_member_event(after.id, "role_remove", guild_id,
                                {"role_id": rid, "role": before_roles[rid]})
        b_to = getattr(before, "timed_out_until", None)
        a_to = getattr(after, "timed_out_until", None)
        if b_to != a_to:
            record_member_event(after.id, "timeout" if a_to else "untimeout", guild_id,
                                {"until": a_to.isoformat() if a_to else None})
        b_boost = getattr(before, "premium_since", None)
        a_boost = getattr(after, "premium_since", None)
        if b_boost != a_boost:
            record_member_event(after.id, "boost" if a_boost else "unboost", guild_id,
                                {"since": a_boost.isoformat() if a_boost else None})
    except Exception:
        log.debug("chronicle record_member_update failed", exc_info=True)


# --------------------------------------------------------------------- interactions

def record_interaction(interaction):
    try:
        data = interaction.data or {}
        itype = getattr(interaction.type, "name", str(interaction.type))
        if itype == "application_command":
            kind, name = "command", data.get("name")
            options = data.get("options") or []
            detail = {o.get("name"): o.get("value") for o in options if isinstance(o, dict)}
        elif itype == "modal_submit":
            kind, name, detail = "modal", data.get("custom_id"), None
        elif itype == "component":
            kind, name, detail = "component", data.get("custom_id"), None
        else:
            return                       # autocomplete/ping: noise, not history
        enqueue(INTERACTION_SQL, (
            interaction.user.id if interaction.user else 0,
            interaction.guild_id, getattr(interaction, "channel_id", None),
            kind, name, _json_or_none(detail), int(time.time())))
    except Exception:
        log.debug("chronicle record_interaction failed", exc_info=True)


# ------------------------------------------------------------------------ audit log

def audit_row(entry):
    """The one row an audit entry produces, shared by the live hook and the backfill."""
    target = getattr(entry, "target", None)
    changes = [{"attr": change.attribute,
                "before": _readable(getattr(change, "before", None)),
                "after": _readable(getattr(change, "after", None))}
               for change in (getattr(entry, "changes", []) or [])]
    extra = _readable(getattr(entry, "extra", None))
    return (AUDIT_SQL, (
        entry.id, getattr(getattr(entry, "guild", None), "id", None),
        getattr(entry.action, "name", str(entry.action)),
        getattr(entry.user, "id", None),
        getattr(target, "id", None) if target is not None else None,
        type(target).__name__ if target is not None else None,
        entry.reason, _json_or_none(changes),
        _json_or_none(extra) if extra else None,
        _ts(entry.created_at)))


def record_audit_entry(entry):
    """Channel and role edits, kicks, bans, timeouts, pins, webhooks, thread creation -
    Discord logs all of it and one event carries the lot, including who did it and why.

    Discord keeps only 45 days of audit log, so anything before the first backfill is
    gone for good; from here on the chronicle holds it permanently.
    """
    try:
        sql, params = audit_row(entry)
        enqueue(sql, params)
    except Exception:
        log.debug("chronicle record_audit_entry failed", exc_info=True)


def _readable(value):
    """Audit log before/after values are arbitrary discord objects; keep them as
    something JSON can hold without losing which object it was."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_readable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _readable(v) for k, v in value.items()}
    if hasattr(value, "id"):
        return {"id": value.id, "name": getattr(value, "name", None)}
    return str(value)


# ---------------------------------------------------------------------------- polls

def record_poll_vote(payload, action):
    try:
        enqueue(POLL_VOTE_SQL, (payload.message_id, payload.channel_id, payload.user_id,
                                payload.answer_id, action, int(time.time())))
    except Exception:
        log.debug("chronicle record_poll_vote failed", exc_info=True)
