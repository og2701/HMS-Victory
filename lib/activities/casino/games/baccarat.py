"""Baccarat (Punto Banco), only in the activity: bet on the Player's hand, the Banker's, or a tie, and the
bot deals both hands to the drawing rules every casino uses, so there's nothing to decide once the bet's down.

Cards count their pip value (aces 1, tens and faces nought) and a hand is worth the last digit of its total.
Each hand gets two cards; an 8 or 9 on either is a natural and both stand. Otherwise the Player draws a third
on 0 to 5, and the Banker draws by its own total and the Player's third card (the table below). The nearer 9
wins: Player pays 1:1, Banker 1:1 less 5% commission, a tie 8:1, and a tie hands Player and Banker bets back.

The shoe is six fresh decks every hand. That leaves the house about 1.06% on Banker, 1.24% on Player and 14.4%
on a tie, the same as any casino's."""

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


def shoe() -> list[str]:
    cards = [r + s for _ in range(DECKS) for s in SUITS for r in RANKS]
    _rng.shuffle(cards)
    return cards


class Hand:
    """One coup: both hands, in the order they were dealt, and how the bet came out."""

    def __init__(self, uid: int, bet: int, side: str, cards: list[str]):
        self.id = uuid.uuid4().hex[:12]
        self.uid = int(uid)
        self.bet = int(bet)
        self.side = side
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
        if self.winner == side:
            win = self.bet * TIE_PAYS if side == "tie" else \
                self.bet * (100 - COMMISSION) // 100 if side == "banker" else self.bet
            self.payout = self.bet + win
            self.outcome = "win"
        elif self.winner == "tie":
            self.payout = self.bet          # a tie hands Player and Banker bets back
            self.outcome = "push"
        else:
            self.payout = 0
            self.outcome = "lose"

    @property
    def net(self) -> int:
        return self.payout - self.bet

    @property
    def score(self) -> str:
        return f"{total(self.player)}-{total(self.banker)}"


def _pay(hand: Hand) -> None:
    if hand.payout > 0:
        reason = f"{BANK} push (bet returned)" if hand.outcome == "push" else f"{BANK} win"
        credit_from_bank(hand.uid, hand.payout, reason)
    record_result(hand.uid, KEY, hand.bet, hand.bet, hand.payout, f"{hand.side} {hand.winner} {hand.score}")


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
        if not remove_bb(uid, bet, reason=f"{BANK} bet"):
            raise Refuse("You don't have enough UKPence.")
        try:
            hand = Hand(uid, bet, side, shoe())
        except Exception:
            credit_from_bank(uid, bet, f"{BANK} stake refund (deal failed)")
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
            return Round(game.bet, game.payout, f"tie {game.score}, {side} bet back")
        if game.outcome == "win":
            return Round(game.bet, game.payout, f"{side} wins {game.score}" if game.side != "tie" else f"tie {game.score} at 8:1")
        return Round(game.bet, 0, f"{game.winner.title()} wins {game.score}" if game.winner != "tie" else f"tie {game.score}")

    def view(self, game):
        return {"id": game.id, "bet": game.bet, "side": game.side, "player": list(game.player), "banker": list(game.banker),
                "order": list(game.order), "playerTotal": total(game.player), "bankerTotal": total(game.banker),
                "natural": game.natural, "winner": game.winner, "outcome": game.outcome, "payout": game.payout,
                "net": game.net, "over": True}

    def dump(self, game):
        return {}

    def load(self, data):
        raise ValueError("a hand is never left in play")

    def extras(self, uid):
        return {"pays": {"player": "1:1", "banker": f"1:1 less {COMMISSION}%", "tie": f"{TIE_PAYS}:1"}}

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
            ("Edge", "Banker is the best bet, with the house keeping about 1%. Tie pays most but keeps about 14%."),
            ("Bets", f"{low:,} to {high:,} UKPence."),
        ]
