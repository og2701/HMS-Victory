"""Liveness signal for single-player casino games so the graceful-shutdown drain
doesn't tear down the gateway/HTTP session while a player's click is mid-flight.

A button handler that settles a hand removes the game from persistent_views before
it finishes redrawing the result. Without this counter the drain would see zero
active games at that instant and close the session, so the redraw fails and the
player sees "This interaction failed" even though their move counted. The drain
adds in_flight_actions() to its active-game tally, so it waits the extra tick for
the redraw to land.

Single event loop, so a plain int is safe (no await between read and write).
"""

import contextlib
import functools

_in_flight = 0


@contextlib.contextmanager
def action_in_flight():
    """Wrap a casino button handler so the shutdown drain waits for it."""
    global _in_flight
    _in_flight += 1
    try:
        yield
    finally:
        _in_flight = max(0, _in_flight - 1)


def deal_in_flight(fn):
    """Decorator for async deal/replay entry points (slash commands, replays).

    A fresh deal debits the stake, sends the table message, and only THEN writes the
    game to persistent_views - so for a couple of seconds the hand is invisible to the
    shutdown drain's active-game count. A SIGTERM in that window tears the session down
    mid-send: the player sees a live table whose buttons are dead, and the error path
    refunds a hand that actually reached Discord. Counting the whole handler closes
    the window. Nesting inside action_in_flight() is harmless (the counter just goes
    to 2 and back).
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with action_in_flight():
            return await fn(*args, **kwargs)
    return wrapper


def in_flight_actions() -> int:
    return _in_flight
