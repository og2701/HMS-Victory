"""Baccarat (Punto Banco), only in the activity: bet on the Player's hand, the Banker's, or a tie, and the
bot deals both hands to the drawing rules every casino uses, so there's nothing to decide once the bet's down.

Cards count their pip value (aces 1, tens and faces nought) and a hand is worth the last digit of its total.
Each hand gets two cards; an 8 or 9 on either is a natural and both stand. Otherwise the Player draws a third
on 0 to 5, and the Banker draws by its own total and the Player's third card (the table below). The nearer 9
wins: Player pays 1:1, Banker 1:1 less 5% commission, a tie 8:1, and a tie hands Player and Banker bets back.

Two side bets ride alongside the main one: Player Pair and Banker Pair, each won when that hand's first two cards
are the same rank (two sevens, two queens; suits don't matter) and paid 11:1 whoever wins the coup. A side bet is
at least the table minimum and at most the main bet, and it's settled with the main bet as one stake and one
payout, so the bank's books and casino_results see one hand.

The shoe is six fresh decks every hand. That leaves the house about 1.06% on Banker, 1.24% on Player, 14.4% on a
tie and 11.25% on either pair, the same as any casino's. The squeeze (peeling your cards up slowly) is the page's
alone: the coup is settled when it's dealt, so nothing waits on it."""

from __future__ import annotations

import random
import uuid

from commands.economy.casino_base import RANKS, SUITS, credit_from_bank
from lib.activities.casino.base import Adapter, Refuse, Round, after_save, badge
from lib.economy.casino_stats import record_result
from lib.economy.economy_manager import remove_bb
from lib.features.income_badges import record_income_source

KEY = "baccarat"
BANK = "Baccarat"        # the reason keyword the bank's books file it under
SIDES = ("player", "banker", "tie")
DECKS = 6
TIE_PAYS = 8             # N:1
PAIR_PAYS = 11           # N:1, on either pair
PAIRS = ("player", "banker")
COMMISSION = 5           # per cent off a Banker win

_rng = random.SystemRandom()


def points(code: str) -> int:
    """What a card counts: aces 1, two to nine their face, tens and faces nought."""
    r = code[0]
    return 1 if r == "A" else 0 if r in "TJQK" else int(r)


def total(cards: list[str]) -> int:
    return sum(points(c) for c in cards) % 10


def banker_draws(banker: int, third: int | None) -> bool:
    """Does the Banker take a third card, on `banker` with the Player's third card worth `third` (None: the
    Player stood)?"""
    if third is None:
        return banker <= 5
    if banker <= 2:
        return True
    if banker == 3:
        return third != 8
    if banker == 4:
        return 2 <= third <= 7
    if banker == 5:
        return 4 <= third <= 7
    if banker == 6:
        return third in (6, 7)
    return False


def is_pair(cards: list[str]) -> bool:
    """Are a hand's first two cards the same rank?"""
    return len(cards) >= 2 and cards[0][0] == cards[1][0]


def shoe() -> list[str]:
    cards = [r + s for _ in range(DECKS) for s in SUITS for r in RANKS]
    _rng.shuffle(cards)
    return cards


class Hand:
    """One coup: both hands, in the order they were dealt, and how the bet came out."""

    def __init__(self, uid: int, bet: int, side: str, cards: list[str], pairs: dict | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.uid = int(uid)
        self.bet = int(bet)
        self.side = side
        self.pair_bets = {h: int((pairs or {}).get(h) or 0) for h in PAIRS}
        deck = list(cards)
        player, banker = [deck.pop()], [deck.pop()]
        player.append(deck.pop())
        banker.append(deck.pop())
        # the order the cards went out, for the page to deal them the same way: P, B, P, B, then any thirds
        self.order = ["player", "banker", "player", "banker"]
        self.natural = total(player) >= 8 or total(banker) >= 8
        if not self.natural:
            third = None
            if total(player) <= 5:
                player.append(deck.pop())
                self.order.append("player")
                third = points(player[2])
            if banker_draws(total(banker), third):
                banker.append(deck.pop())
                self.order.append("banker")
        self.player, self.banker = player, banker
        p, b = total(player), total(banker)
        self.winner = "player" if p > b else "banker" if b > p else "tie"
        # the main bet on its own: outcome and main_payout are what it did, whatever the pairs did
        if self.winner == side:
            win = self.bet * TIE_PAYS if side == "tie" else \
                self.bet * (100 - COMMISSION) // 100 if side == "banker" else self.bet
            self.main_payout = self.bet + win
            self.outcome = "win"
        elif self.winner == "tie":
            self.main_payout = self.bet     # a tie hands Player and Banker bets back
            self.outcome = "push"
        else:
            self.main_payout = 0
            self.outcome = "lose"
        self.pair_hits = {"player": is_pair(player), "banker": is_pair(banker)}
        self.pair_payouts = {h: self.pair_bets[h] * (1 + PAIR_PAYS) if self.pair_hits[h] else 0 for h in PAIRS}
        self.payout = self.main_payout + sum(self.pair_payouts.values())

    @property
    def staked(self) -> int:
        return self.bet + sum(self.pair_bets.values())

    @property
    def net(self) -> int:
        return self.payout - self.staked

    @property
    def score(self) -> str:
        return f"{total(self.player)}-{total(self.banker)}"


def _pay(hand: Hand) -> None:
    if hand.payout > 0:
        pushed = hand.outcome == "push" and not any(hand.pair_payouts.values())
        reason = f"{BANK} push (bet returned)" if pushed else f"{BANK} win"
        credit_from_bank(hand.uid, hand.payout, reason)
    outcome = f"{hand.side} {hand.winner} {hand.score}"
    for h in PAIRS:
        if hand.pair_bets[h]:
            outcome += f" {h[0].upper()}P:{'hit' if hand.pair_hits[h] else 'miss'}"
    record_result(hand.uid, KEY, hand.bet, hand.staked, hand.payout, outcome)


def _pair_bets(body: dict, bet: int, low: int) -> dict:
    """The side bets on the slip: {"player": n, "banker": n}, each nothing or the table minimum up to the main bet."""
    raw = body.get("pairs") or {}
    if not isinstance(raw, dict) or set(raw) - set(PAIRS):
        raise Refuse("One of those side bets isn't on this table.")
    out = {}
    for h in PAIRS:
        amount = raw.get(h) or 0
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise Refuse("Enter a whole number of UKPence for the pair bets.")
        if amount and amount < low:
            raise Refuse(f"A pair bet is at least {low:,} UKPence.")
        if amount > bet:
            raise Refuse("A pair bet can't be more than your main bet.")
        out[h] = amount
    return out


class Baccarat(Adapter):
    key = KEY
    command = KEY
    label = "Baccarat"
    unit = "hands"
    actions = frozenset()
    enabled_cfg = "BACCARAT_ENABLED"
    min_cfg = "BACCARAT_MIN_BET"
    max_cfg = "BACCARAT_MAX_BET"
    default_min = 10
    max_multiplier = 1 + TIE_PAYS

    def deal(self, uid, name, bet, body):
        side = str(body.get("side") or "").lower()
        if side not in SIDES:
            raise Refuse("Pick Player, Banker or Tie.")
        pairs = _pair_bets(body, bet, self.limits()[0])
        stake = bet + sum(pairs.values())
        if any(pairs.values()):
            import config
            from lib.economy.reserve_policy import max_casino_net_payout
            cap = max_casino_net_payout(static_max_net=int(getattr(config, self.max_cfg, self.default_max)) * 30)
            best = bet * TIE_PAYS + sum(pairs.values()) * PAIR_PAYS       # a tie on two pairs
            if best > cap:
                raise Refuse(f"That could win more than the bank can cover ({cap:,} UKPence). Take some off the pairs.")
        if not remove_bb(uid, stake, reason=f"{BANK} bet"):
            raise Refuse("You don't have enough UKPence.")
        try:
            hand = Hand(uid, bet, side, shoe(), pairs)
        except Exception:
            credit_from_bank(uid, stake, f"{BANK} stake refund (deal failed)")
            raise
        after_save(_pay, hand)
        if hand.net > 0:
            badge(record_income_source, uid, "casino")
        return hand

    def over(self, game):
        return True

    def result(self, game):
        side = game.side.title()
        if game.outcome == "push":
            text = f"tie {game.score}, {side} bet back"
        elif game.outcome == "win":
            text = f"{side} wins {game.score}" if game.side != "tie" else f"tie {game.score} at 8:1"
        else:
            text = f"{game.winner.title()} wins {game.score}" if game.winner != "tie" else f"tie {game.score}"
        hits = [f"{h.title()} Pair" for h in PAIRS if game.pair_payouts[h]]
        if hits:
            text += f", {' and '.join(hits)} at {PAIR_PAYS}:1"
        return Round(game.staked, game.payout, text)

    def view(self, game):
        return {"id": game.id, "bet": game.bet, "side": game.side, "player": list(game.player), "banker": list(game.banker),
                "order": list(game.order), "playerTotal": total(game.player), "bankerTotal": total(game.banker),
                "natural": game.natural, "winner": game.winner, "outcome": game.outcome, "payout": game.payout,
                "net": game.net, "staked": game.staked, "mainPayout": game.main_payout,
                "pairs": {h: {"bet": game.pair_bets[h], "hit": game.pair_hits[h], "payout": game.pair_payouts[h]}
                          for h in PAIRS},
                "over": True}

    def dump(self, game):
        return {}

    def load(self, data):
        raise ValueError("a hand is never left in play")

    def extras(self, uid):
        return {"pays": {"player": "1:1", "banker": f"1:1 less {COMMISSION}%", "tie": f"{TIE_PAYS}:1",
                         "pair": f"{PAIR_PAYS}:1"}}

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "Bet on the Player's hand, the Banker's hand, or a tie. Both hands are dealt for you and the "
                    "one nearer 9 wins."),
            ("Counting", "Aces count 1, two to nine their face value, tens and picture cards nothing. A hand is "
                         "worth the last digit of its total, so 7 and 8 make 5."),
            ("Drawing", "Each hand gets two cards. An 8 or 9 is a natural and both hands stand. Otherwise the "
                        "Player draws a third card on 0 to 5, and the Banker draws by the usual table: always on "
                        "0 to 2, and on 3 to 6 depending on the Player's third card."),
            ("Pays", f"Player 1:1. Banker 1:1 less {COMMISSION}% commission. Tie {TIE_PAYS}:1. On a tie, Player "
                     "and Banker bets come back."),
            ("Pairs", f"Side bets on Player Pair and Banker Pair win when that hand's first two cards are the same "
                      f"rank, whoever wins the hand, and pay {PAIR_PAYS}:1. Each is at least {low:,} UKPence and no "
                      "more than your main bet."),
            ("Squeeze", "Turn on Squeeze and the cards on your side come face down: drag each one up to peel it, "
                        "or tap to turn it. The hand is already settled, so take your time."),
            ("Edge", "Banker is the best bet, with the house keeping about 1%. Tie pays most but keeps about 14%, "
                     "and each pair about 11%."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]
