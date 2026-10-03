"""What the activity's casino posts to #casino.

Play happens privately in the activity, so the channel gets a summary instead of a message
per hand: one live line per player per sitting, posted after their first round and edited
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


_sittings: dict[int, Sitting] = {}
_closing = False        # shutting down: text only, so the last edits land inside the drain
_no_files_until = 0.0   # the app can't attach files in the channel: text only until then
NO_FILES_RETRY = 600    # seconds before trying pictures again after a permissions refusal


def _signed(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"−{abs(n):,}"


def _play_row(key: str) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(label="Play", style=discord.ButtonStyle.success,
                                   custom_id=f"ukplace:play:casino:{key}"))
    return row


def _gallery(image: str) -> discord.ui.MediaGallery:
    gallery = discord.ui.MediaGallery()
    gallery.add_item(media=f"attachment://{image}")
    return gallery


def live_view(s: Sitting, image: str | None = None) -> discord.ui.LayoutView:
    """The sitting's line. With a picture the numbers are in it, so the text is one line."""
    if s.done:
        head = f"<@{s.uid}> played **{s.label}** · left the table"
        accent = WIN if s.net > 0 else (LOSS if s.net < 0 else GOLD)
    else:
        head = f"<@{s.uid}> is playing **{s.label}**"
        accent = GOLD
    view = discord.ui.LayoutView(timeout=None)
    if image:
        view.add_item(discord.ui.TextDisplay(head))
        view.add_item(_gallery(image))
    else:
        plural = s.unit if s.rounds != 1 else s.unit.rstrip("s")
        line = f"**{s.rounds:,}** {plural} · net **{_signed(s.net)}** UKP"
        if s.best_net is not None and s.best_net > 0:
            line += f" · best **{_signed(s.best_net)}**" + (f" ({s.best_text})" if s.best_text else "")
        box = discord.ui.Container(accent_colour=discord.Colour(accent))
        box.add_item(discord.ui.TextDisplay(f"{head}\n{line}"))
        view.add_item(box)
    view.add_item(_play_row(s.key))
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


def summary(uid: int, key: str) -> dict:
    """The sitting's numbers for the table's ledger (zero if they're not sitting at it)."""
    s = _sittings.get(int(uid))
    if s is None or s.key != key or s.done or time.time() - s.last > IDLE_AFTER:
        return {"rounds": 0, "net": 0}
    return {"rounds": s.rounds, "net": s.net}


def record(uid: int, key: str, label: str, unit: str, rnd, view: dict | None = None) -> None:
    """Count a finished round towards the player's sitting and update #casino. ``view`` is
    the finished table (the adapter's view), drawn as the board if this is the only round."""
    uid = int(uid)
    now = time.time()
    s = _sittings.get(uid)
    if s is not None and (s.key != key or now - s.last > IDLE_AFTER):
        _finish(s)
        s = None
    if s is None:
        s = _sittings[uid] = Sitting(uid, key, label, unit)
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
        return await cards.final_png(s.key, s.label, s.unit, s.history, s.last_view, s.started, s.last)
    return await cards.live_png(s.key, s.label, s.unit, s.history)


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


def sweep() -> None:
    """Close sittings nobody has played at for IDLE_AFTER seconds, or whose table stopped
    checking in GONE_AFTER seconds ago (the activity was closed without saying so)."""
    now = time.time()
    for s in list(_sittings.values()):
        if now - s.last > IDLE_AFTER or now - s.seen > GONE_AFTER:
            _finish(s)


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
