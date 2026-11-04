"""Named actions the watcher skill can run inside the live bot process.

Scripts that the watcher skill pipes onto the VM run as their own python process: they have
the bot token and the databases, but not the running bot's scheduler, its live
predictions or its button views. A prediction scheduled with a bare DB insert, for
instance, wouldn't post until the next restart. So those scripts drop a request here
instead and the bot runs it:

    data/bot_actions/inbox/<id>.json    {"id": ..., "action": ..., "args": {...}, "ts": <unix>}
    data/bot_actions/outbox/<id>.json   {"id": ..., "action": ..., "ok": true, "result": ...}
                                        {"id": ..., "action": ..., "ok": false, "error": "..."}

The bot checks the inbox every POLL_SECONDS (4 times a minute). Safety:

- only the actions in ACTIONS run, each with typed args; unknown actions or args are refused,
  and nothing is ever eval'd or imported from a request;
- the folder is 0700 and a request file must be a regular file owned by the bot's own user,
  so only processes on the VM running as that user can drop one;
- every action runs as Oggers and is logged to #bot-usage-log;
- a request older than STALE_SECONDS is refused unrun, each file is claimed (moved out of
  the inbox) before it runs so it can only run once, and at most RATE_LIMIT actions run a
  minute (the rest wait for a later poll);
- any failure becomes an error result; the loop itself never dies on a bad request.

The helper that drops requests and waits for results is
.claude/skills/watcher/scripts/bot_action.py.
"""

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from datetime import datetime

import discord
import pytz

from config import CHANNELS, DATA_DIR, USERS

logger = logging.getLogger(__name__)

ACTIONS_DIR = os.path.join(DATA_DIR, "bot_actions")
POLL_SECONDS = 15
STALE_SECONDS = 120
RATE_LIMIT = 5            # actions per RATE_WINDOW
RATE_WINDOW = 60.0
MAX_REQUEST_BYTES = 16_000
ACTOR_ID = USERS.OGGERS   # every action runs as, and is credited to, Oggers

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ActionError(Exception):
    """A request that can't run; the message goes back in the result."""


def _dirs(base: str = None) -> tuple:
    base = base or ACTIONS_DIR
    return (base, os.path.join(base, "inbox"), os.path.join(base, "outbox"),
            os.path.join(base, "processing"))


def ensure_dirs(base: str = None) -> None:
    for d in _dirs(base):
        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


# ---------------------------------------------------------------- arg checking

def _as_int(name, value):
    if isinstance(value, bool):
        raise ActionError(f"`{name}` must be a whole number.")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ActionError(f"`{name}` must be a whole number.")


def _as_str(name, value):
    if not isinstance(value, str):
        raise ActionError(f"`{name}` must be text.")
    if len(value) > 500:
        raise ActionError(f"`{name}` is too long.")
    return value


def _as_str_or_int(name, value):
    if isinstance(value, bool):
        raise ActionError(f"`{name}` must be text or a number.")
    if isinstance(value, int):
        return str(value)
    return _as_str(name, value)


def _as_str_list(name, value):
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ActionError(f"`{name}` must be a list of text.")
    if len(value) > 10:
        raise ActionError(f"`{name}` has too many entries.")
    return [_as_str(name, v) for v in value]


_TYPES = {"int": _as_int, "str": _as_str, "str_or_int": _as_str_or_int, "str_list": _as_str_list}


def check_args(spec: dict, args) -> dict:
    """Validate ``args`` against ``spec`` ({name: (type, required)}). Unknown keys,
    missing required keys and wrong types are all refused."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ActionError("`args` must be an object.")
    unknown = sorted(set(args) - set(spec))
    if unknown:
        raise ActionError(f"Unknown args: {', '.join(unknown)}. Allowed: {', '.join(spec) or 'none'}.")
    out = {}
    for name, (kind, required) in spec.items():
        if name not in args or args[name] is None:
            if required:
                raise ActionError(f"Missing `{name}`.")
            continue
        out[name] = _TYPES[kind](name, args[name])
    return out


# ---------------------------------------------------------------- the actions

def _actor_mention() -> str:
    return f"<@{ACTOR_ID}>"


async def _resolve_channel(client, channel_id: int):
    channel = client.get_channel(channel_id)
    if channel is None:
        try:
            channel = await client.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            channel = None
    if channel is None or not hasattr(channel, "send"):
        raise ActionError(f"Channel {channel_id} not found, or not one I can post in.")
    return channel


def _live_pred(client, message_id: int):
    pred = getattr(client, "predictions", {}).get(message_id)
    if pred is None:
        raise ActionError(f"No live prediction with message id {message_id}.")
    return pred


def _pred_summary(pred) -> dict:
    return {
        "message_id": pred.msg_id,
        "channel_id": pred.channel_id,
        "title": pred.title,
        "options": list(pred.options),
        "totals": pred.totals(),
        "bettors": sum(len(pool) for pool in pred.bets.values()),
        "locked": bool(pred.locked),
        "end_ts": int(pred.end_ts) if pred.end_ts else None,
    }


async def _pred_list(client, args):
    return [_pred_summary(p) for p in getattr(client, "predictions", {}).values()]


async def _pred_list_scheduled(client, args):
    from database import DatabaseManager
    from lib.economy.prediction_system import _options_from_row
    rows = DatabaseManager.fetch_all(
        "SELECT id, channel_id, title, opt1, opt2, options_json, scheduled_ts, duration_minutes, creator_id "
        "FROM scheduled_predictions WHERE status = 'pending' ORDER BY scheduled_ts ASC"
    )
    return [{
        "id": r[0], "channel_id": int(r[1]), "title": r[2],
        "options": _options_from_row(r[3], r[4], r[5]),
        "scheduled_ts": r[6], "duration_minutes": r[7], "creator_id": r[8],
    } for r in rows]


async def _pred_schedule(client, args):
    from lib.economy import prediction_system as ps
    channel = await _resolve_channel(client, args["channel_id"])
    try:
        title, options, scheduled_ts, duration_minutes = ps.validate_scheduled_prediction(
            args["title"], args["options"], args["when"], args["duration"])
    except ValueError as e:
        raise ActionError(str(e))
    sched_id = ps.store_scheduled_prediction(
        client, channel.id, ACTOR_ID, title, options, scheduled_ts, duration_minutes)
    if sched_id is None:
        raise ActionError("The database didn't return an id for the new schedule.")
    await ps.announce_scheduled_prediction(
        client, sched_id, channel.id, title, options, scheduled_ts, duration_minutes, _actor_mention())
    return {"sched_id": sched_id, "channel_id": channel.id, "title": title, "options": options,
            "scheduled_ts": scheduled_ts, "duration_minutes": duration_minutes}


async def _pred_create(client, args):
    from lib.economy import prediction_system as ps
    channel = await _resolve_channel(client, args["channel_id"])
    try:
        title, options, end_ts = ps.validate_new_prediction(
            args["title"], args["options"], args["duration"], discord.utils.utcnow().timestamp())
    except ValueError as e:
        raise ActionError(str(e))
    pred = await ps.post_prediction(client, channel, title, options, end_ts)
    return _pred_summary(pred)


async def _pred_cancel_scheduled(client, args):
    from lib.economy.prediction_system import cancel_scheduled_prediction
    ok, msg = await cancel_scheduled_prediction(client, args["sched_id"], _actor_mention())
    if not ok:
        raise ActionError(msg)
    return {"sched_id": args["sched_id"], "message": msg}


async def _pred_lock(client, args):
    from lib.economy.prediction_system import award_indecisive_badges, set_prediction_locked
    pred = _live_pred(client, args["message_id"])
    if not await set_prediction_locked(client, pred, True):
        raise ActionError("Already locked.")
    await award_indecisive_badges(client, pred)
    return _pred_summary(pred)


async def _pred_unlock(client, args):
    from lib.economy.prediction_system import set_prediction_locked
    pred = _live_pred(client, args["message_id"])
    if not await set_prediction_locked(client, pred, False):
        raise ActionError("Already unlocked.")
    return _pred_summary(pred)


# name -> (arg spec {arg: (type, required)}, handler(client, args))
ACTIONS = {
    "pred_list": ({}, _pred_list),
    "pred_list_scheduled": ({}, _pred_list_scheduled),
    "pred_schedule": ({
        "channel_id": ("int", True), "title": ("str", True), "options": ("str_list", True),
        "when": ("str_or_int", True), "duration": ("str_or_int", True),
    }, _pred_schedule),
    "pred_create": ({
        "channel_id": ("int", True), "title": ("str", True), "options": ("str_list", True),
        "duration": ("str_or_int", True),
    }, _pred_create),
    "pred_cancel_scheduled": ({"sched_id": ("int", True)}, _pred_cancel_scheduled),
    "pred_lock": ({"message_id": ("int", True)}, _pred_lock),
    "pred_unlock": ({"message_id": ("int", True)}, _pred_unlock),
}


# ---------------------------------------------------------------- the queue

def _write_json_atomic(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, default=str)
    os.replace(tmp, path)


def _short(value, limit=900) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


class BotActionQueue:
    def __init__(self, client, base: str = None):
        self.client = client
        self.base, self.inbox, self.outbox, self.processing = _dirs(base)
        self._recent = deque()   # monotonic times of actions run, for the rate limit

    def _pending_files(self) -> list:
        try:
            names = [n for n in os.listdir(self.inbox) if n.endswith(".json")]
        except FileNotFoundError:
            return []
        paths = [os.path.join(self.inbox, n) for n in names]

        def _mtime(p):
            try:
                return os.lstat(p).st_mtime
            except OSError:
                return 0.0
        return sorted(paths, key=_mtime)

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        while self._recent and now - self._recent[0] >= RATE_WINDOW:
            self._recent.popleft()
        return len(self._recent) < RATE_LIMIT

    def _claim(self, path: str):
        """Move a request out of the inbox so it can only ever run once. Returns the
        claimed path, or None if it isn't a regular file owned by us (or vanished)."""
        try:
            st = os.lstat(path)
        except OSError:
            return None
        claimed = os.path.join(self.processing, os.path.basename(path))
        try:
            os.replace(path, claimed)
        except OSError:
            return None
        import stat
        if not stat.S_ISREG(st.st_mode) or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
            logger.warning("bot action: refused %s (not a regular file owned by the bot user)", path)
            try:
                os.remove(claimed)
            except OSError:
                pass
            return None
        return claimed

    async def poll_once(self) -> int:
        """Handle what's waiting in the inbox. Returns how many requests were handled."""
        handled = 0
        for path in self._pending_files():
            if not self._rate_ok():
                break   # the rest wait for a later poll
            claimed = self._claim(path)
            if claimed is None:
                continue
            fallback_id = os.path.basename(path)[:-len(".json")]
            try:
                await self._handle(claimed, fallback_id)
            except Exception:
                logger.exception("bot action: unexpected failure handling %s", path)
            finally:
                try:
                    os.remove(claimed)
                except OSError:
                    pass
            handled += 1
        return handled

    async def _handle(self, claimed: str, fallback_id: str) -> None:
        req_id = fallback_id if _ID_RE.match(fallback_id) else None
        action = None
        try:
            if os.path.getsize(claimed) > MAX_REQUEST_BYTES:
                raise ActionError("Request is too big.")
            with open(claimed) as f:
                try:
                    req = json.load(f)
                except ValueError:
                    raise ActionError("Request isn't valid JSON.")
            if not isinstance(req, dict):
                raise ActionError("Request must be a JSON object.")
            if str(req.get("id")) != fallback_id or req_id is None:
                raise ActionError("Request id must match its file name and be letters, digits, - or _.")
            action = req.get("action")
            ts = req.get("ts")
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                raise ActionError("Request needs a numeric `ts`.")
            age = time.time() - ts
            if age > STALE_SECONDS:
                raise ActionError(f"Request is stale ({int(age)}s old, limit {STALE_SECONDS}s); not run.")
            if action not in ACTIONS:
                raise ActionError(f"Unknown action `{action}`. Allowed: {', '.join(sorted(ACTIONS))}.")
            spec, handler = ACTIONS[action]
            args = check_args(spec, req.get("args"))
            self._recent.append(time.monotonic())
            result = await handler(self.client, args)
            outcome = {"id": req_id, "action": action, "ok": True, "result": result}
            await self._log(action, args, f"ok: {_short(result, 600)}")
        except ActionError as e:
            outcome = {"id": req_id, "action": action, "ok": False, "error": str(e)}
            if action in ACTIONS:
                await self._log(action, None, f"refused: {e}")
        except Exception as e:
            logger.exception("bot action %s failed", action)
            outcome = {"id": req_id, "action": action, "ok": False, "error": f"{type(e).__name__}: {e}"}
            await self._log(action, None, f"failed: {type(e).__name__}: {e}")
        outcome["finished_ts"] = time.time()
        if req_id is not None:
            _write_json_atomic(os.path.join(self.outbox, f"{req_id}.json"), outcome)
        else:
            logger.warning("bot action: request %r has a bad id; no result written: %s", fallback_id, outcome)

    async def _log(self, action, args, outcome: str) -> None:
        logger.info("bot action %s args=%s -> %s", action, args, outcome)
        try:
            channel = self.client.get_channel(CHANNELS.BOT_USAGE_LOG)
            if channel is None:
                return
            now = datetime.now(pytz.timezone("Europe/London")).strftime("%Y-%m-%d %H:%M:%S")
            args_text = f" with args: {_short(args, 500)}" if args is not None else ""
            await channel.send(
                f"{now} - bot action `{action}` (watcher, run as <@{ACTOR_ID}>){args_text} -> {outcome}"[:1900],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception:
            logger.warning("bot action: could not log to bot-usage-log", exc_info=True)

    def cleanup_outbox(self, max_age: float = 3600) -> None:
        """Drop results nobody collected within an hour."""
        try:
            names = os.listdir(self.outbox)
        except FileNotFoundError:
            return
        cutoff = time.time() - max_age
        for name in names:
            path = os.path.join(self.outbox, name)
            try:
                if os.lstat(path).st_mtime < cutoff:
                    os.remove(path)
            except OSError:
                pass

    async def run(self) -> None:
        ensure_dirs(self.base)
        logger.info("Bot action queue watching %s every %ss.", self.inbox, POLL_SECONDS)
        polls = 0
        while True:
            try:
                await self.poll_once()
                polls += 1
                if polls % 240 == 0:   # about hourly
                    self.cleanup_outbox()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("bot action queue poll failed")
            await asyncio.sleep(POLL_SECONDS)


def start_bot_action_watcher(client):
    """Start the queue once per process; later calls (e.g. a reconnect) are no-ops."""
    task = getattr(client, "_bot_action_task", None)
    if task is not None and not task.done():
        return task
    client._bot_action_task = asyncio.get_running_loop().create_task(BotActionQueue(client).run())
    return client._bot_action_task
