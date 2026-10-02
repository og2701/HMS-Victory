"""Fruit Machine and Roulette: one move, one result.

Slots reuses commands/economy/slots.py's reels, weights and paytable. Roulette in the bot is
a shared table on a timer; here it's a solo table that spins the moment you press Spin, on
roulette.py's own bet types, payout maths and bank reasons, with the same per-slip exposure
limit and the same badges."""

import random
import uuid

from commands.economy import casino_base as cb
from commands.economy import roulette as R
from commands.economy import slots as S
from lib.activities.casino.base import Adapter, Refuse, Round, badge
from lib.economy.casino_stats import record_result
from lib.economy.economy_manager import remove_bb
from lib.features.income_badges import award_badge_safe, record_income_source


# --- Fruit Machine -------------------------------------------------------------------------
class Slots(Adapter):
    key = "slots"
    command = "slots"
    label = "Fruit Machine"
    unit = "spins"
    actions = frozenset()
    enabled_cfg = "SLOTS_ENABLED"
    min_cfg = "SLOTS_MIN_BET"
    max_cfg = "SLOTS_MAX_BET"
    default_min = 1
    max_multiplier = 100.0

    def deal(self, uid, name, bet, body):
        if not remove_bb(uid, bet, reason="Slots bet"):
            raise Refuse("You don't have enough UKPence.")
        try:
            machine = S.SlotMachine(uid, name, None, bet)
            machine.do_spin()
        except Exception:
            S._credit(uid, bet, "Slots stake refund (spin failed)")
            raise
        S._credit(uid, machine.win, "Slots win")
        if machine.reels == ["crown", "crown", "crown"]:
            badge(award_badge_safe, uid, "slots_jackpot")
        record_result(uid, "slots", bet, bet, machine.win,
                      f"{machine.mult}x" if machine.win else "no win")
        machine.spin_count = 1
        return machine

    def over(self, game):
        return True

    def result(self, game):
        a, b, c = game.reels
        if game.mult <= 0:
            text = ""
        elif a == b == c:
            text = "jackpot, three crowns" if a == "crown" else f"three {S.NAME[a].lower()}"
        else:
            text = "two cherries"
        return Round(game.bet, game.win, text)

    def view(self, game):
        return {"id": game.spin_id, "bet": game.bet, "reels": list(game.reels),
                "mult": game.mult, "payout": game.win, "net": game.net, "over": True,
                "jackpot": game.reels == ["crown", "crown", "crown"]}

    def dump(self, game):
        return {}

    def load(self, data):
        raise ValueError("a spin is never left in play")

    def extras(self, uid):
        return {
            "symbols": [{"key": k, "emoji": e, "name": S.NAME[k], "three": S.THREE_OF_A_KIND[k]}
                        for k, e, _w in S.REEL],
            "twoCherries": S.TWO_CHERRY,
        }

    def rules(self):
        low, high = self.limits()
        pay = " · ".join(f"three {S.NAME[k].lower()} {S.THREE_OF_A_KIND[k]}x" for k, _e, _w in S.REEL)
        return [
            ("Aim", "Stake your bet and spin three reels. Match symbols on the line to win."),
            ("Paytable", f"{pay} · any two cherries {S.TWO_CHERRY}x"),
            ("Prizes", "Prizes are multiples of your bet. The reels are weighted so the house "
                       "keeps a small edge over time."),
            ("Bets", f"{low:,} to {high:,} UKPence a spin."),
        ]


# --- Roulette ------------------------------------------------------------------------------
_KEYS = set(R._OUTSIDE_PAYOUT) | {f"straight:{n}" for n in range(37)}
_history: dict[int, list[int]] = {}


class Spin:
    """A solo roulette spin: the chips, the number and what came back."""

    def __init__(self, uid, bets):
        self.id = uuid.uuid4().hex[:12]
        self.uid = int(uid)
        self.bets = dict(bets)
        self.staked = sum(self.bets.values())
        self.number = None
        self.returned = 0


class Roulette(Adapter):
    key = "roulette"
    command = "roulette"
    label = "Roulette"
    unit = "spins"
    actions = frozenset()
    enabled_cfg = "ROULETTE_ENABLED"
    max_cfg = "ROULETTE_MAX_BET"
    default_min = 10

    def _slip(self, body) -> dict:
        raw = body.get("bets")
        if not isinstance(raw, dict) or not raw:
            raise Refuse("Put some chips on the table first.")
        bets = {}
        for key, amount in raw.items():
            if key not in _KEYS or not isinstance(amount, int) or amount <= 0:
                raise Refuse("One of those bets isn't on this table.")
            bets[key] = amount
        return bets

    def deal(self, uid, name, bet, body):
        bets = self._slip(body)
        if sum(bets.values()) != bet:
            raise Refuse("Your chips changed. Spin again.")
        slip = R.BetSlip(uid, name)
        slip.bets = dict(bets)
        from lib.economy.reserve_policy import max_casino_net_payout
        import config
        cap = max_casino_net_payout(static_max_net=getattr(config, "ROULETTE_MAX_BET", 10_000) * 35)
        if slip.max_potential_win > cap:
            raise Refuse(f"That could win more than the bank can cover ({cap:,} UKPence). "
                         "Take some chips off.")
        if not remove_bb(uid, bet, reason="Roulette bet"):
            raise Refuse("You don't have enough UKPence.")
        spin = Spin(uid, bets)
        spin.number = random.randint(0, 36)
        spin.returned = R._resolve(bets, spin.number)
        if spin.returned > 0:
            cb.credit_from_bank(uid, spin.returned, "Roulette win")
        record_result(uid, "roulette", spin.staked, spin.staked, spin.returned, str(spin.number))
        n = spin.number
        if n == 0:
            badge(award_badge_safe, uid, "zero_hero")
        straights = [k for k in bets if k.startswith("straight:")]
        if len(straights) <= 3 and any(R.bet_wins(k, n) for k in straights) and spin.returned > spin.staked:
            badge(award_badge_safe, uid, "lucky_number")
        if spin.returned - spin.staked >= 1000:
            badge(award_badge_safe, uid, "red_letter_day")
        if spin.returned > 0:
            badge(record_income_source, uid, "casino")
        _history.setdefault(int(uid), []).insert(0, n)
        del _history[int(uid)][12:]
        return spin

    def over(self, game):
        return True

    def result(self, game):
        return Round(game.staked, game.returned, f"{game.number} {R.color(game.number)}")

    def view(self, game):
        n = game.number
        return {
            "id": game.id, "bets": game.bets, "bet": game.staked, "number": n,
            "color": R.color(n), "payout": game.returned, "net": game.returned - game.staked,
            "won": [k for k in game.bets if R.bet_wins(k, n)], "over": True,
        }

    def dump(self, game):
        return {}

    def load(self, data):
        raise ValueError("a spin is never left in play")

    def extras(self, uid):
        return {"recent": _history.get(int(uid), []), "wheel": R.WHEEL_ORDER,
                "red": sorted(R.RED), "chips": R.CHIP_SIZES}

    def rules(self):
        low, high = self.limits()
        return [
            ("Even money", "Red or black, even or odd, 1-18 or 19-36 pay 1:1."),
            ("Dozens, columns", "Pay 2:1."),
            ("Straight up", "A single number pays 35:1."),
            ("Zero", "One green zero, a 2.7% house edge. Zero loses every outside, dozen and "
                     "column bet."),
            ("Your table", "This is your own table: the wheel spins as soon as you press Spin."),
            ("Bets", f"Up to {high:,} UKPence a spin."),
        ]
