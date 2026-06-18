"""Perfect Connect 4 via the bitbully solver (strongly-solved, C-speed, with an opening book).

Connect 4 is a solved game and bitbully plays it perfectly in milliseconds. We hand it the
game's move history (the columns played, in order) and it returns the optimal column for the
side to move - it never blunders and punishes every mistake.

bitbully is an OPTIONAL dependency. If it isn't installed, ``best_move`` returns ``None`` and
the caller falls back to the in-house negamax engine (``lib/economy/connect4_ai``). So the bot
runs fine either way; installing bitbully just upgrades the AI from "very strong" to "perfect".
"""
import logging

logger = logging.getLogger(__name__)

_agent = None
_state = "init"   # "init" | "ready" | "unavailable"


def _get_agent():
    global _agent, _state
    if _state == "ready":
        return _agent
    if _state == "unavailable":
        return None
    try:
        import bitbully as bb
        _agent = bb.BitBully()           # the default opening book loads automatically
        _state = "ready"
        logger.info("Connect 4: bitbully perfect solver loaded (book loaded: %s).",
                    _agent.is_book_loaded())
    except Exception:
        _agent = None
        _state = "unavailable"
        logger.info("Connect 4: bitbully not installed - using the in-house engine.")
    return _agent


def available() -> bool:
    return _get_agent() is not None


def best_move(move_history):
    """Perfect column (0..6) for the side to move after ``move_history`` (the columns played so
    far, in order), or ``None`` if bitbully is unavailable / the game is already over / on any
    error - in which case the caller falls back to the in-house engine."""
    agent = _get_agent()
    if agent is None:
        return None
    try:
        import bitbully as bb
        board = bb.Board.from_moves([int(c) for c in move_history])
        if board.is_game_over():
            return None
        return int(agent.best_move(board))
    except Exception:
        logger.error("Connect 4: bitbully best_move failed - falling back.", exc_info=True)
        return None
