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


def in_flight_actions() -> int:
    return _in_flight
