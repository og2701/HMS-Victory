"""Texas Hold'em hand engine - Discord-agnostic and fully testable.

A `Hand` runs one hand of no-limit Hold'em for a fixed set of seats: post blinds, deal,
drive the betting rounds (preflop/flop/turn/river), then resolve with correct side pots.
The Discord layer feeds it actions and reads its state; it knows nothing about Discord.

Cards are (rank, suit) with rank 2..14 (14 = Ace) and suit 0..3. Chips are integers.
"""

from collections import Counter
from itertools import combinations

RANKS = list(range(2, 15))
SUITS = (0, 1, 2, 3)
RANK_NAMES = {11: "J", 12: "Q", 13: "K", 14: "A"}
SUIT_NAMES = {0: "♠", 1: "♥", 2: "♦", 3: "♣"}  # spade heart diamond club
CATEGORY_NAMES = {
    8: "Straight Flush", 7: "Four of a Kind", 6: "Full House", 5: "Flush",
    4: "Straight", 3: "Three of a Kind", 2: "Two Pair", 1: "Pair", 0: "High Card",
}


def full_deck():
    return [(r, s) for s in SUITS for r in RANKS]


def card_str(card):
    r, s = card
    return f"{RANK_NAMES.get(r, str(r))}{SUIT_NAMES[s]}"


# --- hand evaluation -----------------------------------------------------------
def _eval5(cards):
    """Return a comparable key for a 5-card hand (higher = better)."""
    ranks = sorted((c[0] for c in cards), reverse=True)
    is_flush = len({c[1] for c in cards}) == 1
    distinct = sorted(set(ranks))
    straight_high = None
    if len(distinct) == 5:
        if distinct[-1] - distinct[0] == 4:
            straight_high = distinct[-1]
        elif distinct == [2, 3, 4, 5, 14]:  # wheel: A-2-3-4-5
            straight_high = 5
    cnt = Counter(ranks)
    by = sorted(cnt.items(), key=lambda x: (x[1], x[0]), reverse=True)
    counts = [c for _, c in by]
    ordered = [r for r, _ in by]

    if straight_high and is_flush:
        return (8, straight_high)
    if counts[0] == 4:
        return (7, ordered[0], ordered[1])
    if counts[0] == 3 and counts[1] == 2:
        return (6, ordered[0], ordered[1])
    if is_flush:
        return (5, *ranks)
    if straight_high:
        return (4, straight_high)
    if counts[0] == 3:
        return (3, ordered[0], *sorted(ordered[1:], reverse=True))
    if counts[0] == 2 and counts[1] == 2:
        pair_hi, pair_lo = sorted((ordered[0], ordered[1]), reverse=True)
        return (2, pair_hi, pair_lo, ordered[2])
    if counts[0] == 2:
        return (1, ordered[0], *sorted(ordered[1:], reverse=True))
    return (0, *ranks)


def evaluate(cards):
    """Best 5-card key from 5..7 cards."""
    if len(cards) == 5:
        return _eval5(cards)
    return max(_eval5(combo) for combo in combinations(cards, 5))


def hand_category(cards):
    return CATEGORY_NAMES[evaluate(cards)[0]]


# --- side-pot distribution -----------------------------------------------------
def build_pots(contrib, folded):
    """Split total contributions into main + side pots.

    contrib: {seat: total chips committed this hand}
    folded:  set of seats that folded (they fund pots but can't win them)
    Returns a list of (amount, [eligible seats], [contributors], level) from main pot out.
    """
    remaining = {s: c for s, c in contrib.items() if c > 0}
    pots = []
    while remaining:
        level = min(remaining.values())
        contributors = list(remaining.keys())
        amount = level * len(contributors)
        for s in contributors:
            remaining[s] -= level
            if remaining[s] == 0:
                del remaining[s]
        eligible = [s for s in contributors if s not in folded]
        pots.append((amount, eligible, contributors, level))
    return pots


def settle(contrib, folded, hole, board):
    """Return {seat: winnings} (gross chips returned, before netting out their contribution).

    Ties split a pot evenly; any odd remainder goes to the earliest eligible seat (by the
    order seats appear in `contrib`, which the caller sets to seat order from the button).
    A pot layer that no live player is eligible for (all its contributors folded - i.e. an
    uncalled overbet relative to the all-in players) is refunded to those contributors.
    """
    winnings = {s: 0 for s in contrib}
    live = [s for s in contrib if s not in folded]
    strengths = {s: evaluate(list(hole[s]) + list(board)) for s in live}
    for amount, eligible, contributors, level in build_pots(contrib, folded):
        if eligible:
            best = max(strengths[s] for s in eligible)
            winners = [s for s in eligible if strengths[s] == best]
            share, rem = divmod(amount, len(winners))
            for s in winners:
                winnings[s] += share
            for s in winners[:rem]:  # the odd chip(s) go to the earliest winners
                winnings[s] += 1
        else:
            for s in contributors:  # uncalled layer: hand it back to who put it in
                winnings[s] += level
    return winnings
