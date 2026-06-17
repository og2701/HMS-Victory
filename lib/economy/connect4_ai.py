"""A strong Connect 4 AI (bitboard negamax + alpha-beta).

Pure logic, no Discord/economy deps, so it can be unit-/simulation-tested standalone.

Strength comes from:
  - a 7x6 bitboard (one 49-bit int per side, with the standard sentinel row) so win
    detection and move generation are a handful of shifts;
  - negamax with alpha-beta, centre-first move ordering, and a per-search transposition
    table;
  - Pascal Pons's "non-losing moves" pruning - the engine never plays a move that hands
    the opponent an immediate win, and is forced onto the only defensive square when one
    exists. Combined with a deep search this is extremely hard to beat;
  - among genuinely equal-value moves it picks randomly, so it can't be beaten by replaying
    one memorised line (and can't be farmed for the bank's money).

Entry point: ``best_move(board, ai_disc, depth=?, time_budget=?) -> column``.
``board`` is the game's 6x7 grid (board[row][col], row 0 = TOP, row 5 = bottom; 0 empty,
else the disc id), matching commands/economy/connect4.py.
"""

import random
import time

WIDTH = 7
HEIGHT = 6
H1 = HEIGHT + 1          # 7 - one column's bit span (6 rows + 1 sentinel)
H2 = HEIGHT + 2          # 8
SIZE = WIDTH * HEIGHT    # 42
WIN = 100_000            # win/loss scores dominate the heuristic
COL_ORDER = (3, 2, 4, 1, 5, 0, 6)   # centre-first

_BOTTOM = 0
for _c in range(WIDTH):
    _BOTTOM |= 1 << (_c * H1)
_BOARD_MASK = _BOTTOM * ((1 << HEIGHT) - 1)     # every playable cell
_CENTER_MASK = ((1 << HEIGHT) - 1) << (3 * H1)  # the middle column


def _bit(r, c):
    """Bit for board cell (row r from TOP, col c) - matches _from_board's layout."""
    return 1 << (c * H1 + (HEIGHT - 1 - r))


def _build_windows():
    """Bitmask of every possible 4-in-a-row (the 'windows' a heuristic scores)."""
    out = []
    for r in range(HEIGHT):
        for c in range(WIDTH):
            if c + 3 < WIDTH:                                   # horizontal
                out.append(_bit(r, c) | _bit(r, c+1) | _bit(r, c+2) | _bit(r, c+3))
            if r + 3 < HEIGHT:                                  # vertical
                out.append(_bit(r, c) | _bit(r+1, c) | _bit(r+2, c) | _bit(r+3, c))
            if r + 3 < HEIGHT and c + 3 < WIDTH:                # diagonal \
                out.append(_bit(r, c) | _bit(r+1, c+1) | _bit(r+2, c+2) | _bit(r+3, c+3))
            if r - 3 >= 0 and c + 3 < WIDTH:                    # diagonal /
                out.append(_bit(r, c) | _bit(r-1, c+1) | _bit(r-2, c+2) | _bit(r-3, c+3))
    return out


_WINDOWS = _build_windows()
_W = (0, 1, 5, 30)   # value of holding 1/2/3 of a 4-window (index by count; 0 unused)


def _bottom_mask_col(c):
    return 1 << (c * H1)


def _column_mask(c):
    return ((1 << HEIGHT) - 1) << (c * H1)


def _can_play(mask, c):
    return (mask & (1 << (HEIGHT - 1 + c * H1))) == 0   # top playable cell free


def _possible(mask):
    return (mask + _BOTTOM) & _BOARD_MASK               # lowest empty cell per column


def _compute_winning(position, mask):
    """Bitmask of empty cells that would complete a 4-in-a-row for `position`."""
    # vertical
    r = (position << 1) & (position << 2) & (position << 3)
    # horizontal
    p = (position << H1) & (position << (2 * H1))
    r |= p & (position << (3 * H1))
    r |= p & (position >> H1)
    p = (position >> H1) & (position >> (2 * H1))
    r |= p & (position << H1)
    r |= p & (position >> (3 * H1))
    # diagonal /
    p = (position << HEIGHT) & (position << (2 * HEIGHT))
    r |= p & (position << (3 * HEIGHT))
    r |= p & (position >> HEIGHT)
    p = (position >> HEIGHT) & (position >> (2 * HEIGHT))
    r |= p & (position << HEIGHT)
    r |= p & (position >> (3 * HEIGHT))
    # diagonal \
    p = (position << H2) & (position << (2 * H2))
    r |= p & (position << (3 * H2))
    r |= p & (position >> H2)
    p = (position >> H2) & (position >> (2 * H2))
    r |= p & (position << H2)
    r |= p & (position >> (3 * H2))
    return r & (_BOARD_MASK ^ mask)


def _non_losing(position, mask):
    """Moves that don't hand the opponent an immediate win (Pons). 0 = every move loses."""
    poss = _possible(mask)
    opp_win = _compute_winning(position ^ mask, mask)
    forced = poss & opp_win
    if forced:
        if forced & (forced - 1):     # two separate immediate threats - can't block both
            return 0
        poss = forced                 # must take the single forced defence
    return poss & ~(opp_win >> 1)     # and never play directly beneath an opponent win


def _heuristic(position, mask):
    """Static eval from the side-to-move's view. Scores every possible 4-in-a-row window by
    how many of each side's pieces it holds (the classic discriminating Connect 4 eval), plus
    centre control. Discriminating enough that deeper search has clear preferences (a coarse
    eval makes deep search collapse into tied/near-random moves)."""
    me = position
    opp = position ^ mask
    score = (((me & _CENTER_MASK).bit_count() - (opp & _CENTER_MASK).bit_count()) * 4
             + (_compute_winning(me, mask)).bit_count() * 12      # immediately-playable threats
             - (_compute_winning(opp, mask)).bit_count() * 12)
    for wm in _WINDOWS:
        cm = (me & wm).bit_count()
        co = (opp & wm).bit_count()
        if 0 < cm < 4 and not co:
            score += _W[cm]
        elif 0 < co < 4 and not cm:
            score -= _W[co]
    return score


def _negamax(position, mask, moves, depth, alpha, beta, tt):
    if moves >= SIZE:
        return 0                                        # board full -> draw
    win = _compute_winning(position, mask) & _possible(mask)
    if win:
        return (SIZE + 1 - moves) // 2 + WIN            # we win on the move
    if depth <= 0:
        return _heuristic(position, mask)
    nonlosing = _non_losing(position, mask)
    if nonlosing == 0:
        return -(WIN + (SIZE - moves) // 2)             # every reply loses

    # Transposition table. Entries store a value tagged EXACT / LOWER / UPPER bound; a
    # cutoff value is only a bound, so it must NOT be reused as exact (that corrupts the
    # search - the bug that made deeper play weaker). Use it to tighten the window instead.
    key = (position, mask)
    cached = tt.get(key)
    if cached is not None and cached[0] >= depth:
        cdepth, cval, cflag = cached
        if cflag == 0:                       # EXACT
            return cval
        elif cflag == 1 and cval > alpha:    # LOWER bound
            alpha = cval
        elif cflag == 2 and cval < beta:     # UPPER bound
            beta = cval
        if alpha >= beta:
            return cval

    orig_alpha, orig_beta = alpha, beta
    best = -(1 << 30)
    for c in COL_ORDER:
        move = nonlosing & _column_mask(c)
        if move:
            score = -_negamax(position ^ mask, mask | move, moves + 1,
                              depth - 1, -beta, -alpha, tt)
            if score > best:
                best = score
                if best > alpha:
                    alpha = best
                    if alpha >= beta:
                        break
    flag = 1 if best >= orig_beta else 0 if best > orig_alpha else 2  # LOWER / EXACT / UPPER
    tt[key] = (depth, best, flag)
    return best


def _from_board(board, ai_disc):
    position = mask = moves = 0
    for r in range(HEIGHT):
        for c in range(WIDTH):
            v = board[r][c]
            if v:
                bit = 1 << (c * H1 + (HEIGHT - 1 - r))   # row 0 = top -> high bit
                mask |= bit
                moves += 1
                if v == ai_disc:
                    position |= bit
    return position, mask, moves


def best_move(board, ai_disc, depth=12, time_budget=None):
    """Best column (0..WIDTH-1) for ``ai_disc`` to play, or None if the board is full.

    Iterative deepening up to ``depth``; if ``time_budget`` (seconds) is set it stops early
    and returns the best move from the last completed iteration. Ties are broken randomly."""
    position, mask, moves = _from_board(board, ai_disc)
    legal = [c for c in COL_ORDER if _can_play(mask, c)]
    if not legal:
        return None

    # 1) take an immediate win if there is one.
    winning_cells = _compute_winning(position, mask)
    for c in legal:
        if winning_cells & ((mask + _bottom_mask_col(c)) & _column_mask(c)):
            return c

    # 2) restrict to non-losing moves (don't walk into a forced loss). If all moves lose,
    #    play on regardless (centre-most) and hope the opponent slips.
    nonlosing = _non_losing(position, mask)
    candidates = [c for c in legal if nonlosing & _column_mask(c)] or legal
    if len(candidates) == 1:
        return candidates[0]

    # Evaluate each root candidate with a FULL window so every move gets its exact value.
    # (Narrowing alpha across root moves lets a worse move's fail-high tie the true best, and
    # the random tie-break would then sometimes play the inferior move.) The per-candidate
    # subtree still prunes via _negamax's own alpha-beta.
    start = time.monotonic()
    best_cols = [candidates[0]]
    for d in range(2, depth + 1):
        tt = {}
        best, ties = -(1 << 31), []
        for c in candidates:
            move = (mask + _bottom_mask_col(c)) & _column_mask(c)
            score = -_negamax(position ^ mask, mask | move, moves + 1,
                              d - 1, -(1 << 30), (1 << 30), tt)
            if score > best:
                best, ties = score, [c]
            elif score == best:
                ties.append(c)
            if time_budget and time.monotonic() - start > time_budget:
                break
        best_cols = ties
        if time_budget and time.monotonic() - start > time_budget:
            break
    return random.choice(best_cols)
