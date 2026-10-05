"""Plinko, only in the activity: drop a ball through twelve rows of pegs and it lands in one of
thirteen slots along the bottom, each paying a multiple of the bet. Low, medium or high risk
changes what the slots pay: low keeps most of the bet coming back, high pays up to 150x at the
far edges and a fifth of the bet in the middle.

The bot drops the ball. At each row it goes left or right at even odds, so the slot it lands in
is just how many times it went right, and the middle slots are by far the likeliest. The page is
sent that path and plays it back peg by peg; nothing it does changes where the ball lands.

The tables keep about 3% for the house (97.5% back at low risk, 97.4% medium, 96.7% high), the
same sort of edge as roulette's green zero."""

from __future__ import annotations

import random
import uuid
from math import comb

from commands.economy.casino_base import credit_from_bank
from lib.activities.casino.base import Adapter, Refuse, Round, badge
from lib.economy.casino_stats import record_result
from lib.economy.economy_manager import remove_bb
from lib.features.income_badges import record_income_source

ROWS = 12
RISKS = ("low", "medium", "high")
# Each risk's slots in tenths of the bet, from the left edge to the middle; the right half
# mirrors it. Tenths keep the payout sums exact.
_HALF = {
    "low": (80, 30, 15, 13, 11, 10, 5),
    "medium": (240, 90, 40, 20, 11, 6, 3),
    "high": (1500, 220, 80, 20, 7, 2, 2),
}
TENTHS = {risk: list(h) + list(h[-2::-1]) for risk, h in _HALF.items()}
TOP = max(max(t) for t in TENTHS.values()) / 10

_rng = random.SystemRandom()


def multiple(risk: str, slot: int) -> float:
    return TENTHS[risk][slot] / 10


def odds() -> list[float]:
    """How likely each slot is: the chance of that many rights in ROWS even-odds bounces."""
    return [comb(ROWS, k) / 2 ** ROWS for k in range(ROWS + 1)]


def returns(risk: str) -> float:
    """What a risk level hands back on average, as a fraction of the bet."""
    return sum(p * t / 10 for p, t in zip(odds(), TENTHS[risk]))


class Drop:
    """One ball: which way it bounced at each row, where it landed and what that paid."""

    def __init__(self, uid: int, bet: int, risk: str, path: list[int]):
        self.id = uuid.uuid4().hex[:12]
        self.uid = int(uid)
        self.bet = int(bet)
        self.risk = risk
        self.path = path                       # 0 left, 1 right, one per row
        self.slot = sum(path)
        self.payout = self.bet * TENTHS[risk][self.slot] // 10

    @property
    def mult(self) -> float:
        return multiple(self.risk, self.slot)


class Plinko(Adapter):
    key = "plinko"
    command = "plinko"
    label = "Plinko"
    unit = "balls"
    actions = frozenset()
    enabled_cfg = "PLINKO_ENABLED"
    min_cfg = "PLINKO_MIN_BET"
    max_cfg = "PLINKO_MAX_BET"
    default_min = 10
    max_multiplier = TOP
    big_multiple = 20.0        # a ball a second: only the far edges are worth a post of their own

    def deal(self, uid, name, bet, body):
        risk = str(body.get("risk") or "medium").lower()
        if risk not in TENTHS:
            raise Refuse("Pick low, medium or high risk.")
        if not remove_bb(uid, bet, reason="Plinko bet"):
            raise Refuse("You don't have enough UKPence.")
        drop = Drop(uid, bet, risk, [_rng.randint(0, 1) for _ in range(ROWS)])
        if drop.payout > 0:
            credit_from_bank(uid, drop.payout, "Plinko win")
            badge(record_income_source, uid, "casino")
        record_result(uid, "plinko", bet, bet, drop.payout, f"{drop.mult:g}x {risk}")
        return drop

    def over(self, game):
        return True

    def result(self, game):
        return Round(game.bet, game.payout, f"{game.mult:g}x on {game.risk} risk")

    def view(self, game):
        return {"id": game.id, "bet": game.bet, "risk": game.risk, "path": list(game.path),
                "slot": game.slot, "mult": game.mult, "payout": game.payout,
                "net": game.payout - game.bet, "over": True}

    def dump(self, game):
        return {}

    def load(self, data):
        raise ValueError("a ball is never left in play")

    def extras(self, uid):
        return {"rows": ROWS, "risks": list(RISKS),
                "tables": {risk: [t / 10 for t in TENTHS[risk]] for risk in RISKS}}

    def rules(self):
        low, high = self.limits()
        top = {risk: max(TENTHS[risk]) / 10 for risk in RISKS}
        return [
            ("Aim", "Drop a ball through the pegs. It bounces left or right at every row and pays "
                    "whatever the slot it lands in says."),
            ("Risk", f"Low pays up to {top['low']:g}x and never less than half your bet back. Medium "
                     f"pays up to {top['medium']:g}x. High pays up to {top['high']:g}x at the very "
                     "edges, but the middle slots only give a fifth back."),
            ("Odds", "Every bounce is a coin toss, so the ball usually ends near the middle. The "
                     "edges are rare: about 1 ball in 2,000 lands in either corner."),
            ("Edge", "The slots are set so the house keeps about 3% over time."),
            ("Bets", f"{low:,} to {high:,} UKPence a ball. Drop as many as you like."),
        ]
