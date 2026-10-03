"""What the activity's casino posts to #casino.

Play happens privately in the activity, so the channel gets a summary instead of a message
per hand: one live line per player per sitting, posted when they start playing and edited
as they go, then marked as left once they leave the table (the activity says so, or stops
checking in while the table is open), go idle, or move to another game. A round that pays at least BIG_WIN_MULTIPLE times the stake (or nets BIG_WIN_NET)
also gets a post of its own with a Play button, so the moments worth seeing still stand
out.

Each line carries a picture from cards.py: a small strip while they play, then, once they
leave, the final board if they played one round or a summary of the sitting if they played
more. If a picture can't be made the line falls back to text.

Posting is best-effort and never holds up a move: edits are batched a few seconds apart
and run as background tasks.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass, field

import discord

import config

log = logging.getLogger(__name__)

IDLE_AFTER = 300        # seconds without a round before a sitting is over
GONE_AFTER = 75         # seconds without a word from the open table before they've gone
EDIT_GAP = 6.0          # seconds between edits of one live line
IMAGE = "casino.png"
GOLD, WIN, LOSS = 0xE2BE78, 0x23A55A, 0xF87171


def _cfg(name, default):
    return getattr(config, name, default)


def _channel() -> int | None:
    ch = _cfg("ACTIVITIES_CASINO_CHANNEL", None)
    if ch:
        return int(ch)
    return getattr(getattr(config, "CHANNELS", None), "CASINO", None)


@dataclass
class Sitting:
    uid: int
    key: str
    label: str
    unit: str
    rounds: int = 0
    net: int = 0
    best_net: int | None = None
    best_text: str = ""
    last: float = field(default_factory=time.time)
    seen: float = field(default_factory=time.time)  # the table last checked in
    message_id: int | None = None
    done: bool = False
    dirty: bool = False
    task: asyncio.Task | None = None
    edited: float = 0.0
    started: float = field(default_factory=time.time)
    history: list = field(default_factory=list)     # one dict per round, for the pictures
    last_view: dict | None = None                   # the last round's table, for its board
    open_net: int = 0                               # a round still going: its net so far...
    open_note: str = ""                             # ...and what to say about it ("23 coins in the cup")
    watchers: list = field(default_factory=list)    # who's spectating, for the line


_sittings: dict[int, Sitting] = {}
_closing = False        # shutting down: text only, so the last edits land inside the drain
_no_files_until = 0.0   # the app can't attach files in the channel: text only until then
NO_FILES_RETRY = 600    # seconds before trying pictures again after a permissions refusal


def _signed(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"−{abs(n):,}"


def _play_row(key: str, watch: int | None = None) -> discord.ui.ActionRow:
    """Play, and while someone's at the table, Spectate (to watch them in the activity)."""
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(label="Play", style=discord.ButtonStyle.success,
                                   custom_id=f"ukplace:play:casino:{key}"))
    if watch is not None and _watchable(key):
        row.add_item(discord.ui.Button(label="Spectate", style=discord.ButtonStyle.secondary,
                                       custom_id=f"ukplace:watch:{int(watch)}:{key}"))
    return row


def _watchable(key: str) -> bool:
    from lib.activities import casino
    a = casino.adapter(key)
    return bool(a and a.watchable)


def _names(uids: list) -> str:
    """'<@a>', '<@a> and <@b>', '<@a>, <@b> and 3 others' (mentions; the posts ping nobody)."""
    tags = [f"<@{int(u)}>" for u in uids]
    if len(tags) <= 2:
        return " and ".join(tags)
    if len(tags) == 3:
        return f"{tags[0]}, {tags[1]} and {tags[2]}"
    return f"{tags[0]}, {tags[1]} and {len(tags) - 2} others"


def _gallery(image: str) -> discord.ui.MediaGallery:
    gallery = discord.ui.MediaGallery()
    gallery.add_item(media=f"attachment://{image}")
    return gallery


def live_view(s: Sitting, image: str | None = None) -> discord.ui.LayoutView:
    """The sitting's line. With a picture the numbers are in it, so the text is one line."""
    if s.done and not s.rounds and s.open_note:
        # left a long round unfinished (a cup half played): it waits for them
        head = f"<@{s.uid}> stepped away from **{s.label}** with {s.open_note}"
        accent = GOLD
    elif s.done:
        head = f"<@{s.uid}> played **{s.label}** · left the table"
        if s.open_note:
            head += f" with {s.open_note}"
        accent = WIN if s.net > 0 else (LOSS if s.net < 0 else GOLD)
    else:
        head = f"<@{s.uid}> is playing **{s.label}**"
        if s.watchers:
            head += f" · {_names(s.watchers)} watching"
        accent = GOLD
    view = discord.ui.LayoutView(timeout=None)
    if s.done and not s.rounds and not s.open_note:
        # sat down, never finished a round: nothing to show but that they've gone
        view.add_item(discord.ui.TextDisplay(f"<@{s.uid}> left the **{s.label}** table"))
    elif image:
        view.add_item(discord.ui.TextDisplay(head))
        view.add_item(_gallery(image))
    else:
        plural = s.unit if s.rounds != 1 else s.unit.rstrip("s")
        line = f"**{s.rounds:,}** {plural} · net **{_signed(s.net + s.open_net)}** UKP"
        if s.best_net is not None and s.best_net > 0:
            line += f" · best **{_signed(s.best_net)}**" + (f" ({s.best_text})" if s.best_text else "")
        box = discord.ui.Container(accent_colour=discord.Colour(accent))
        box.add_item(discord.ui.TextDisplay(f"{head}\n{line}"))
        view.add_item(box)
    view.add_item(_play_row(s.key, None if s.done else s.uid))
    return view


def big_win_view(uid: int, key: str, label: str, rnd, image: str | None = None) -> discord.ui.LayoutView:
    what = f" ({rnd.outcome})" if rnd.outcome else ""
    head = f"<@{uid}> won **{_signed(rnd.net)} UKP** at **{label}**{what}"
    view = discord.ui.LayoutView(timeout=None)
    if image:
        view.add_item(discord.ui.TextDisplay(head))
        view.add_item(_gallery(image))
    else:
        box = discord.ui.Container(accent_colour=discord.Colour(GOLD))
        box.add_item(discord.ui.TextDisplay(
            f"{head}\n-# {rnd.payout:,} back from a {rnd.staked:,} stake · {rnd.multiple:.2f}x"))
        view.add_item(box)
    view.add_item(_play_row(key))
    return view


def _entry(rnd, big: bool) -> dict:
    return {"net": rnd.net, "staked": rnd.staked, "payout": rnd.payout,
            "multiple": rnd.multiple, "outcome": rnd.outcome, "big": big}


def is_big(rnd) -> bool:
    if rnd.net <= 0:
        return False
    return (rnd.multiple >= _cfg("ACTIVITIES_CASINO_BIG_WIN_MULTIPLE", 5.0)
            and rnd.net >= _cfg("ACTIVITIES_CASINO_BIG_WIN_MIN", 250)) \
        or rnd.net >= _cfg("ACTIVITIES_CASINO_BIG_WIN_NET", 5_000)


def playing(uid: int, key: str) -> bool:
    """Is this player at this table right now (for spectators)?"""
    s = _sittings.get(int(uid))
    return s is not None and s.key == key and not s.done and time.time() - s.last <= IDLE_AFTER


def summary(uid: int, key: str) -> dict:
    """The sitting's numbers for the table's ledger (zero if they're not sitting at it)."""
    s = _sittings.get(int(uid))
    if s is None or s.key != key or s.done or time.time() - s.last > IDLE_AFTER:
        return {"rounds": 0, "net": 0}
    return {"rounds": s.rounds, "net": s.net}


def _sitting(uid: int, key: str, label: str, unit: str, now: float) -> tuple[Sitting, bool]:
    """The player's sitting at this table, starting one (and closing any other) if need be.
    Returns it and whether it's new."""
    s = _sittings.get(uid)
    if s is not None and (s.key != key or now - s.last > IDLE_AFTER):
        _finish(s)
        s = None
    if s is not None:
        return s, False
    s = _sittings[uid] = Sitting(uid, key, label, unit)
    return s, True


def begin(uid: int, key: str, label: str, unit: str, progress: dict | None = None) -> None:
    """A player has started a round: their line goes up now, not when the round ends (which
    for a cup of coins can be many minutes later)."""
    s, new = _sitting(int(uid), key, label, unit, time.time())
    s.last = s.seen = time.time()
    if _progress(s, progress) or new:
        _schedule(s)


def touch(uid: int, key: str, progress: dict | None = None) -> None:
    """Any move at the table counts as still playing, round finished or not. ``progress`` is
    how a long round is going (the adapter's progress()), shown on the line as it changes."""
    s = _sittings.get(int(uid))
    if s is not None and s.key == key and not s.done:
        s.last = s.seen = time.time()
        if _progress(s, progress):
            _schedule(s)


def _progress(s: Sitting, progress: dict | None) -> bool:
    """Take in a round-in-progress report; True if it changed what the line shows."""
    if not progress:
        return False
    net, note = int(progress.get("net", 0)), str(progress.get("note", ""))
    if (net, note) == (s.open_net, s.open_note):
        return False
    s.open_net, s.open_note = net, note
    return True


def record(uid: int, key: str, label: str, unit: str, rnd, view: dict | None = None) -> None:
    """Count a finished round towards the player's sitting and update #casino. ``view`` is
    the finished table (the adapter's view), drawn as the board if this is the only round."""
    uid = int(uid)
    now = time.time()
    s, _ = _sitting(uid, key, label, unit, now)
    s.open_net, s.open_note = 0, ""
    s.rounds += 1
    s.net += rnd.net
    s.last = s.seen = now
    if rnd.net > 0 and (s.best_net is None or rnd.net > s.best_net):
        s.best_net, s.best_text = rnd.net, rnd.outcome
    big = is_big(rnd)
    s.history.append(_entry(rnd, big))
    s.last_view = view
    _schedule(s)
    if big:
        _spawn(_post_big(uid, key, label, rnd, view))


def _spawn(coro) -> asyncio.Task | None:
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:            # no loop (tests, scripts): nothing to post to
        coro.close()
        return None


def _schedule(s: Sitting) -> None:
    s.dirty = True
    if s.task is None or s.task.done():
        s.task = _spawn(_flush(s))


async def _picture(s: Sitting) -> bytes | None:
    from lib.activities.casino import cards
    if _closing or time.time() < _no_files_until:
        return None
    if s.done:
        if not s.history and s.open_note:
            return await cards.live_png(s.key, s.label, s.unit, s.history, s.open_net, s.open_note, "STEPPED AWAY")
        return await cards.final_png(s.key, s.label, s.unit, s.history, s.last_view, s.started, s.last)
    return await cards.live_png(s.key, s.label, s.unit, s.history, s.open_net, s.open_note)


def _files(png: bytes | None) -> list[discord.File] | None:
    return [discord.File(io.BytesIO(png), filename=IMAGE)] if png else None


async def _flush(s: Sitting) -> None:
    """Post the live line, or edit it, no more often than EDIT_GAP."""
    ch = _channel()
    if not ch:
        return
    while s.dirty:
        wait = s.edited + EDIT_GAP - time.time()
        if wait > 0 and s.message_id is not None:
            await asyncio.sleep(wait)
        s.dirty = False
        s.edited = time.time()
        try:
            png = await _picture(s)
        except Exception:
            log.warning("couldn't draw the casino line for %s", s.uid, exc_info=True)
            png = None
        try:
            await _send(ch, s.message_id, lambda image: live_view(s, image), png,
                        lambda mid: setattr(s, "message_id", mid))
        except Exception:
            log.warning("couldn't update the casino line for %s", s.uid, exc_info=True)


async def _send(ch: int, message_id: int | None, make_view, png: bytes | None, posted=None) -> None:
    """Post or edit with the picture. If the channel won't take files (the app is missing
    Attach Files there), send the text version instead and stop drawing pictures for a while,
    so the lines keep appearing."""
    global _no_files_until
    from lib.activities import launcher

    async def go(with_picture: bool):
        view = make_view(IMAGE if with_picture else None)
        files = _files(png) if with_picture else None
        if message_id is None:
            mid = await launcher.post_view(ch, view, files=files)
            if posted is not None:
                posted(mid)
        else:
            await launcher.edit_view(ch, message_id, view, files=files)

    try:
        await go(png is not None)
    except discord.Forbidden:
        if png is None:
            raise
        _no_files_until = time.time() + NO_FILES_RETRY
        log.warning("the activities app can't attach files in #casino; posting text instead")
        await go(False)


async def _post_big(uid, key, label, rnd, table: dict | None = None) -> None:
    from lib.activities.casino import cards
    ch = _channel()
    if not ch:
        return
    png = None
    if time.time() >= _no_files_until:
        try:
            png = await cards.big_win_png(key, label, _entry(rnd, True), table)
        except Exception:
            log.warning("couldn't draw a casino big win for %s", uid, exc_info=True)
    try:
        await _send(ch, None, lambda image: big_win_view(uid, key, label, rnd, image), png)
    except Exception:
        log.warning("couldn't post a casino big win for %s", uid, exc_info=True)


def _finish(s: Sitting) -> None:
    if s.done:
        return
    s.done = True
    if _sittings.get(s.uid) is s:
        del _sittings[s.uid]
    _schedule(s)


def here(uid: int, key: str) -> None:
    """The activity still has this table open (it checks in every 25 seconds or so)."""
    s = _sittings.get(int(uid))
    if s is not None and s.key == key:
        s.seen = time.time()


def leave(uid: int, key: str) -> None:
    """The player closed the table or the activity: the line stops saying playing now."""
    s = _sittings.get(int(uid))
    if s is not None and s.key == key:
        _finish(s)


def spectated(uid: int, key: str, watchers: list) -> None:
    """Who's watching this player's table changed: say so on their line."""
    s = _sittings.get(int(uid))
    watchers = [int(w) for w in watchers if w]
    if s is not None and s.key == key and not s.done and s.watchers != watchers:
        s.watchers = watchers
        _schedule(s)


def sweep() -> None:
    """Close sittings nobody has played at for IDLE_AFTER seconds, or whose table stopped
    checking in GONE_AFTER seconds ago (the activity was closed without saying so), and drop
    spectators who've stopped looking from the lines."""
    from lib.activities import casino
    now = time.time()
    for s in list(_sittings.values()):
        if now - s.last > IDLE_AFTER or now - s.seen > GONE_AFTER:
            _finish(s)
        else:
            spectated(s.uid, s.key, casino.spectators(s.uid, s.key))


async def run_sweeper() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            sweep()
        except Exception:
            log.warning("casino sitting sweep failed", exc_info=True)


async def close_all(timeout: float = 4.0) -> None:
    """On shutdown: mark every open sitting as left, so no line says playing forever."""
    global _closing
    _closing = True
    tasks = []
    for s in list(_sittings.values()):
        _finish(s)
        if s.task is not None:
            tasks.append(s.task)
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
