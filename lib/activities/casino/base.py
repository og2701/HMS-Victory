"""The plumbing every casino game in the activity shares.

Each game is an Adapter: a thin layer over the bot's own game class that deals, applies a
move, and describes the table as JSON. The adapter calls the game module's own stake,
settle and payout functions, with their reason strings, so the bank's per-game P/L,
casino_results, badges and the economy log can't tell a hand played here from one played
with the slash command.

What lives here:
    Refuse      a move the player can't make, with the message to show them
    Round       how a finished round came out (staked, paid, a short description)
    Adapter     the interface each game fills in, plus bet limits
    store       the in-play game per player per game, saved to disk so a reopen resumes
    play()      one locked, drain-aware move: deal or act, then save or settle
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass

import config
from lib.core.file_operations import atomic_write_json
from lib.economy.casino_drain import action_in_flight

log = logging.getLogger(__name__)


class Refuse(Exception):
    """A move that isn't allowed right now. str(e) is shown to the player as is."""


class Busy(Refuse):
    """The player's previous move hasn't finished yet."""


@dataclass
class Round:
    staked: int
    payout: int
    outcome: str = ""

    @property
    def net(self) -> int:
        return self.payout - self.staked

    @property
    def multiple(self) -> float:
        return self.payout / self.staked if self.staked else 0.0


class Adapter:
    key = ""                 # the casino_results key, also the game's name in URLs
    command = ""             # what lib.core.restrictions calls it (the slash command name)
    label = ""
    actions: frozenset = frozenset()
    enabled_cfg = ""         # config flag that closes the table, if the game has one
    min_cfg = ""
    max_cfg = ""
    default_min = 5
    default_max = 10_000
    max_multiplier = None    # set only where the slash command scales its max to the bank's
                             # reserves (max_casino_bet with this payout multiple); else static
    watchable = True         # others can spectate a hand in play (its view hides nothing secret)

    # --- limits ---------------------------------------------------------------------
    def enabled(self) -> bool:
        return not self.enabled_cfg or bool(getattr(config, self.enabled_cfg, True))

    def limits(self) -> tuple[int, int]:
        low = int(getattr(config, self.min_cfg, self.default_min)) if self.min_cfg else self.default_min
        high = int(getattr(config, self.max_cfg, self.default_max)) if self.max_cfg else self.default_max
        if self.max_multiplier:
            from lib.economy.reserve_policy import max_casino_bet
            high = max_casino_bet(game_max_multiplier=self.max_multiplier, static_max=high)
        return low, high

    def check_bet(self, uid: int, bet: int) -> None:
        from lib.economy.economy_manager import get_bb
        if not self.enabled():
            raise Refuse(f"The {self.label} table is closed right now.")
        low, high = self.limits()
        if bet < low:
            raise Refuse(f"The minimum bet is {low:,} UKPence.")
        if bet > high:
            raise Refuse(f"The maximum bet is {high:,} UKPence right now.")
        if get_bb(uid) < bet:
            raise Refuse("You don't have enough UKPence for that bet.")

    # --- the game -------------------------------------------------------------------
    def deal(self, uid: int, name: str, bet: int, body: dict):
        """Take the stake and start a round. Must refund the stake itself if it fails
        after taking it."""
        raise NotImplementedError

    def act(self, game, action: str, body: dict) -> None:
        raise NotImplementedError

    def over(self, game) -> bool:
        raise NotImplementedError

    def result(self, game) -> Round:
        """How a finished round came out. Only called once over() is true."""
        raise NotImplementedError

    def view(self, game) -> dict:
        """The table as the player may see it. Nothing they haven't earned yet: no deck
        order, no hole card, no mine layout."""
        raise NotImplementedError

    def dump(self, game) -> dict:
        return game.to_dict()

    def load(self, data: dict):
        raise NotImplementedError

    def rules(self) -> list[tuple[str, str]]:
        return []

    def extras(self, uid: int) -> dict:
        """Anything else the idle table needs (a paytable, the odds)."""
        return {}

    def opening(self, uid: int) -> dict:
        """Anything the table needs only when it's opened, too big to send with every move."""
        return {}


# The bot's client, for the badge awards that need it. Set when the API starts.
CLIENT = None

# Payouts queued during a move, run once the finished game is off the books. Same order as
# the slash commands (delete the saved game, then credit): a crash between the two leaves
# an unpaid hand at worst, never one that's both paid and still resumable. A move runs
# start to finish without awaiting, so one list serves every player.
_after: list = []


def after_save(fn, *args, **kwargs) -> None:
    """Run fn (a credit, a settle) after this move's game state is saved."""
    _after.append((fn, args, kwargs))


def badge(fn, *args) -> None:
    """Run a game's badge award (async, needs the client) without holding up the move."""
    if CLIENT is None:
        return
    try:
        result = fn(CLIENT, *args)
        if asyncio.iscoroutine(result):
            asyncio.get_running_loop().create_task(result)
    except Exception:
        log.warning("casino badge award failed", exc_info=True)


# ---------------------------------------------------------------------------------------
# In-play games, one per player per game, saved so closing the activity mid-hand is safe
# ---------------------------------------------------------------------------------------
_FILE = os.path.join(config.JSON_DATA_DIR, "activity_casino.json")
_games: dict[str, object] = {}
_finished: dict[str, object] = {}      # the last finished round, so a refresh still shows it
_loaded = False


def _slot(uid: int, key: str) -> str:
    return f"{int(uid)}:{key}"


def _load(registry: dict) -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        import json
        with open(_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        log.error("couldn't read %s; starting with no in-play activity hands", _FILE, exc_info=True)
        return
    for slot, entry in raw.items():
        adapter = registry.get(slot.split(":", 1)[-1])
        if adapter is None:
            continue
        try:
            _games[slot] = adapter.load(entry)
        except Exception:
            log.error("dropping unreadable activity hand %s", slot, exc_info=True)


def _save(registry: dict) -> None:
    out = {}
    for slot, game in _games.items():
        adapter = registry.get(slot.split(":", 1)[-1])
        if adapter is not None:
            out[slot] = adapter.dump(game)
    atomic_write_json(_FILE, out)


def in_play(uid: int, key: str, registry: dict):
    _load(registry)
    return _games.get(_slot(uid, key))


def last_finished(uid: int, key: str):
    return _finished.get(_slot(uid, key))


def in_play_keys(uid: int, registry: dict) -> set[str]:
    _load(registry)
    prefix = f"{int(uid)}:"
    return {slot[len(prefix):] for slot in _games if slot.startswith(prefix)}


# ---------------------------------------------------------------------------------------
# A move
# ---------------------------------------------------------------------------------------
_locks: dict[int, asyncio.Lock] = {}
_recent: dict[int, list[float]] = {}
MOVES_PER_SECOND = 6


def _lock(uid: int) -> asyncio.Lock:
    lock = _locks.get(uid)
    if lock is None:
        lock = _locks[uid] = asyncio.Lock()
    return lock


def _throttle(uid: int) -> None:
    now = time.monotonic()
    hits = [t for t in _recent.get(uid, []) if now - t < 1.0]
    if len(hits) >= MOVES_PER_SECOND:
        raise Busy("Slow down a little.")
    _recent[uid] = hits + [now]


def _bet(body: dict) -> int:
    try:
        return int(str(body.get("bet", "")).replace(",", "").strip())
    except ValueError:
        raise Refuse("Enter a whole number of UKPence.")


async def play(registry: dict, uid: int, name: str, key: str, action: str, body: dict,
               on_round=None):
    """Apply one move and return (adapter, game, finished_round_or_None).

    Moves for one player run one at a time: a second tap that arrives while the first is
    still going through is turned away rather than queued, the same as the slash
    commands' busy flag. The whole move counts towards the shutdown drain."""
    adapter = registry.get(key)
    if adapter is None:
        raise Refuse("That game isn't here.")
    _load(registry)
    lock = _lock(uid)
    if lock.locked():
        raise Busy("Hold on, your last move is still going through.")
    _throttle(uid)
    async with lock:
        with action_in_flight():
            _after.clear()
            slot = _slot(uid, key)
            game = _games.get(slot)
            if action == "deal":
                if game is not None and not adapter.over(game):
                    raise Refuse("Finish this hand first.")
                bet = _bet(body)
                adapter.check_bet(uid, bet)
                game = adapter.deal(uid, name, bet, body)
            else:
                if game is None:
                    raise Refuse("That hand is over. Deal a new one.")
                if action not in adapter.actions:
                    raise Refuse("That move isn't available.")
                adapter.act(game, action, body)

            finished = None
            if adapter.over(game):
                _games.pop(slot, None)
                _finished[slot] = game
                finished = adapter.result(game)
            else:
                _games[slot] = game
                _finished.pop(slot, None)
            try:
                _save(registry)
            except Exception:
                log.error("couldn't save in-play activity hands", exc_info=True)
            queued, _after[:] = list(_after), []
            for fn, args, kwargs in queued:
                fn(*args, **kwargs)
            if finished is not None and on_round is not None:
                try:
                    on_round(uid, key, finished, game)
                except Exception:
                    log.error("activity casino round hook failed", exc_info=True)
            return adapter, game, finished
