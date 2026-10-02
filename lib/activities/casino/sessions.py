"""What the activity's casino posts to #casino.

Play happens privately in the activity, so the channel gets a summary instead of a message
per hand: one live line per player per sitting, posted after their first round and edited
as they go, then marked as left once they've been idle for a while or moved to another
game. A round that pays at least BIG_WIN_MULTIPLE times the stake (or nets BIG_WIN_NET)
also gets a post of its own with a Play button, so the moments worth seeing still stand
out.

Posting is best-effort and never holds up a move: edits are batched a few seconds apart
and run as background tasks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import discord

import config

log = logging.getLogger(__name__)

IDLE_AFTER = 300        # seconds without a round before a sitting is over
EDIT_GAP = 4.0          # seconds between edits of one live line
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
    message_id: int | None = None
    done: bool = False
    dirty: bool = False
    task: asyncio.Task | None = None
    edited: float = 0.0


_sittings: dict[int, Sitting] = {}


def _signed(n: int) -> str:
    return f"+{n:,}" if n >= 0 else f"−{abs(n):,}"


def _play_row(key: str) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(label="Play", style=discord.ButtonStyle.success,
                                   custom_id=f"ukplace:play:casino:{key}"))
    return row


def live_view(s: Sitting) -> discord.ui.LayoutView:
    if s.done:
        head = f"<@{s.uid}> played **{s.label}** · left the table"
        accent = WIN if s.net > 0 else (LOSS if s.net < 0 else GOLD)
    else:
        head = f"<@{s.uid}> is at the **{s.label}** table · \U0001F7E2 playing"
        accent = GOLD
    plural = s.unit if s.rounds != 1 else s.unit.rstrip("s")
    line = f"**{s.rounds:,}** {plural} · net **{_signed(s.net)}** UKP"
    if s.best_net is not None and s.best_net > 0:
        line += f" · best **{_signed(s.best_net)}**" + (f" ({s.best_text})" if s.best_text else "")
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_colour=discord.Colour(accent))
    box.add_item(discord.ui.TextDisplay(f"{head}\n{line}"))
    view.add_item(box)
    view.add_item(_play_row(s.key))
    return view


def big_win_view(uid: int, key: str, label: str, rnd) -> discord.ui.LayoutView:
    what = f" ({rnd.outcome})" if rnd.outcome else ""
    view = discord.ui.LayoutView(timeout=None)
    box = discord.ui.Container(accent_colour=discord.Colour(GOLD))
    box.add_item(discord.ui.TextDisplay(
        f"<@{uid}> won **{_signed(rnd.net)} UKP** at **{label}**{what}\n"
        f"-# {rnd.payout:,} back from a {rnd.staked:,} stake · {rnd.multiple:.2f}x"))
    view.add_item(box)
    view.add_item(_play_row(key))
    return view


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


def record(uid: int, key: str, label: str, unit: str, rnd) -> None:
    """Count a finished round towards the player's sitting and update #casino."""
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
    s.last = now
    if rnd.net > 0 and (s.best_net is None or rnd.net > s.best_net):
        s.best_net, s.best_text = rnd.net, rnd.outcome
    _schedule(s)
    if is_big(rnd):
        _spawn(_post_big(uid, key, label, rnd))


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


async def _flush(s: Sitting) -> None:
    """Post the live line, or edit it, no more often than EDIT_GAP."""
    from lib.activities import launcher
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
            if s.message_id is None:
                s.message_id = await launcher.post_view(ch, live_view(s))
            else:
                await launcher.edit_view(ch, s.message_id, live_view(s))
        except Exception:
            log.warning("couldn't update the casino line for %s", s.uid, exc_info=True)


async def _post_big(uid, key, label, rnd) -> None:
    from lib.activities import launcher
    ch = _channel()
    if ch:
        try:
            await launcher.post_view(ch, big_win_view(uid, key, label, rnd))
        except Exception:
            log.warning("couldn't post a casino big win for %s", uid, exc_info=True)


def _finish(s: Sitting) -> None:
    if s.done:
        return
    s.done = True
    if _sittings.get(s.uid) is s:
        del _sittings[s.uid]
    _schedule(s)


def sweep() -> None:
    """Close sittings nobody has played at for IDLE_AFTER seconds."""
    now = time.time()
    for s in list(_sittings.values()):
        if now - s.last > IDLE_AFTER:
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
    tasks = []
    for s in list(_sittings.values()):
        _finish(s)
        if s.task is not None:
            tasks.append(s.task)
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)
