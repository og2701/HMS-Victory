"""Penny Falls: a coin pusher, only in the activity.

The machine runs on the player's screen (three.js and Rapier): they drop coins, the pusher
shoves the pile, and whatever goes over the front edge comes back into their cup. Every few
seconds the page reports what went in and what came off. The bot can't see the physics, so
it keeps the books and won't accept what can't be true:

- Each player has their own machine and the bot counts the coins on it. Nobody can take
  more off the shelves than are on them, plus what they've dropped since.
- Coins go in no faster than a hand can feed the slot, and only from the cup.
- Gold coins are the bot's to give: one drops in after every PENNYFALLS_GOLD_EVERY coins a
  player feeds their machine. The bot counts the coins, so it alone says when one is due.
- A cup (one round: the coins you buy, play and cash out) nets at most PENNYFALLS_CUP_MAX_NET
  and a day at most PENNYFALLS_DAY_MAX_NET.

So a doctored page can at worst empty its own machine, inside the caps, and honest play
keeps the house edge the machine's side gaps give it. Anything that doesn't add up is
trimmed to what could have happened, and logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass

import config
from commands.economy.casino_base import credit_from_bank
from lib.activities.casino.base import Adapter, Refuse, Round, after_save
from lib.core.file_operations import atomic_write_json
from lib.economy.casino_stats import record_result
from lib.economy.economy_manager import get_bb, remove_bb

log = logging.getLogger(__name__)

LABEL = "Penny Falls"
REASON = "Penny Falls"              # how the economy log names its money


def _cfg(name, default):
    return getattr(config, f"PENNYFALLS_{name}", default)


def value() -> int:
    """UKPence a coin."""
    return int(_cfg("COIN", 10))


def gold_every() -> int:
    """Coins dropped for each gold one. A gold coin is worth GOLD coins, so this hands back
    GOLD / it of what goes in, against what the side gaps take."""
    return max(1, int(_cfg("GOLD_EVERY", 150)))


GOLD = 10                           # a gold coin is worth this many coins
DROPS_PER_SECOND = 6                # the page allows one every 200ms; a little slack on top
TOP_UP_MAX = 100


# --- the machines --------------------------------------------------------------------------
# One per player: how many coins and gold coins sit on its shelves, how many coins have gone
# in towards the next gold one, and how much the player has netted from it today. Positions
# live on the player's screen; only the counts matter here.
_FILE = os.path.join(config.JSON_DATA_DIR, "activity_pennyfalls.json")
_machines: dict[str, dict] = {}
_loaded = False


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def machine(uid: int) -> dict:
    global _loaded
    if not _loaded:
        _loaded = True
        try:
            with open(_FILE) as f:
                _machines.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception:
            log.error("couldn't read %s; machines start fresh", _FILE, exc_info=True)
    m = _machines.get(str(int(uid)))
    if m is None:
        m = _machines[str(int(uid))] = {"coins": int(_cfg("SEED_COINS", 110)), "golds": int(_cfg("SEED_GOLDS", 0)),
                                        "fed": 0, "day": _today(), "day_net": 0}
    m.setdefault("fed", 0)
    if m.get("day") != _today():
        m["day"], m["day_net"] = _today(), 0
    return m


def _save_machines() -> None:
    try:
        atomic_write_json(_FILE, _machines)
    except Exception:
        log.error("couldn't save the penny falls machines", exc_info=True)


# --- a cup ---------------------------------------------------------------------------------
@dataclass
class Cup:
    id: str
    uid: int
    coins: int                      # in the cup now
    bought: int
    staked: int
    dropped: int = 0
    won: int = 0                    # coins back, gold counted at GOLD each
    synced: float = 0.0
    over: bool = False
    payout: int = 0
    note: str = ""
    release: int = 0                # gold coins to drop now (this move only)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("release")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Cup":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _count(body: dict, key: str) -> int:
    try:
        return max(0, min(500, int(body.get(key) or 0)))
    except (TypeError, ValueError):
        return 0


def _settle(cup: Cup) -> None:
    if cup.payout > 0:
        credit_from_bank(cup.uid, cup.payout, reason=f"{REASON} cashout")
    record_result(cup.uid, "pennyfalls", cup.staked, cup.staked, cup.payout,
                  "win" if cup.payout > cup.staked else "push" if cup.payout == cup.staked else "lose")


class PennyFalls(Adapter):
    key = "pennyfalls"
    command = "pennyfalls"
    label = LABEL
    unit = "cups"
    actions = frozenset({"buy", "sync", "cashout"})
    enabled_cfg = "PENNYFALLS_ENABLED"
    min_cfg = "PENNYFALLS_MIN_BET"
    max_cfg = "PENNYFALLS_MAX_BET"
    default_min = 100
    default_max = 1_000

    def deal(self, uid, name, bet, body):
        coins, rest = divmod(bet, value())
        if rest or coins < 1:
            raise Refuse(f"Coins are {value()} UKPence each: buy a whole number of them.")
        if not remove_bb(uid, bet, reason=f"{REASON} coins"):
            raise Refuse("You don't have enough UKPence.")
        machine(uid)
        return Cup(uuid.uuid4().hex[:12], int(uid), coins, coins, bet, synced=time.time())

    def act(self, cup, action, body):
        cup.release = 0
        if cup.over:
            raise Refuse("That cup's been cashed out.")
        if action == "buy":
            n = _count(body, "coins")
            if not 1 <= n <= TOP_UP_MAX:
                raise Refuse(f"Buy between 1 and {TOP_UP_MAX} coins.")
            if get_bb(cup.uid) < n * value() or not remove_bb(cup.uid, n * value(), reason=f"{REASON} coins"):
                raise Refuse("You don't have enough UKPence for more coins.")
            cup.coins += n
            cup.bought += n
            cup.staked += n * value()
            return
        self._sync(cup, body)
        if action == "cashout":
            self._cash_out(cup)

    def _sync(self, cup: Cup, body: dict) -> None:
        """Book what the page says went in and came off since last time, trimmed to what
        could have happened."""
        m = machine(cup.uid)
        dropped, won, lost = _count(body, "dropped"), _count(body, "won"), _count(body, "lost")
        won_gold, lost_gold = _count(body, "wonGold"), _count(body, "lostGold")
        claimed = (dropped, won, lost, won_gold, lost_gold)
        now = time.time()

        # in no faster than a hand can feed the slot
        dropped = min(dropped, int((now - cup.synced) * DROPS_PER_SECOND) + DROPS_PER_SECOND)
        # off the shelves: no more than was on them, plus what's gone in since
        gold_off = won_gold + lost_gold
        if gold_off > m["golds"]:
            won_gold = max(0, won_gold - (gold_off - m["golds"]))
            lost_gold = min(lost_gold, m["golds"] - won_gold)
        # coins can't go in unless they're in the cup, counting what came back this batch
        for _ in range(2):
            off_cap = m["coins"] + dropped
            if won + lost > off_cap:
                won = max(0, won - (won + lost - off_cap))
                lost = min(lost, off_cap - won)
            dropped = min(dropped, cup.coins + won + won_gold * GOLD)
        if (dropped, won, lost, won_gold, lost_gold) != claimed:
            log.warning("penny falls: trimmed %s's report %s to %s", cup.uid, claimed,
                        (dropped, won, lost, won_gold, lost_gold))

        m["coins"] += dropped - won - lost
        m["golds"] -= won_gold + lost_gold
        back = won + won_gold * GOLD
        cup.coins += back - dropped
        cup.dropped += dropped
        cup.won += back
        cup.synced = now
        # a gold coin for every GOLD_EVERY that go in, counted across cups
        every = gold_every()
        m["fed"] += dropped
        cup.release, m["fed"] = divmod(m["fed"], every)
        m["golds"] += cup.release
        _save_machines()

    def _cash_out(self, cup: Cup) -> None:
        m = machine(cup.uid)
        payout = cup.coins * value()
        cup_cap = int(_cfg("CUP_MAX_NET", 1_000))
        day_left = max(0, int(_cfg("DAY_MAX_NET", 5_000)) - m["day_net"])
        allowed = min(cup_cap, day_left)
        if payout - cup.staked > allowed:
            cup.note = "daily limit" if day_left < cup_cap else "cup limit"
            payout = cup.staked + allowed
        cup.payout = payout
        cup.over = True
        m["day_net"] += payout - cup.staked
        _save_machines()
        after_save(_settle, cup)

    def over(self, cup):
        return cup.over

    def result(self, cup):
        text = f"{cup.won} coin{'s' if cup.won != 1 else ''} back from {cup.dropped}"
        if cup.note:
            text += f", {cup.note} reached"
        return Round(cup.staked, cup.payout, text)

    def view(self, cup):
        return {
            "id": cup.id, "cup": cup.coins, "bought": cup.bought, "staked": cup.staked,
            "dropped": cup.dropped, "won": cup.won, "value": value(), "release": cup.release,
            "board": self._board(cup.uid), "over": cup.over, "payout": cup.payout,
            "net": cup.payout - cup.staked if cup.over else 0, "note": cup.note,
        }

    @staticmethod
    def _board(uid: int) -> dict:
        m = machine(uid)
        return {"coins": m["coins"], "golds": m["golds"], "fed": m["fed"], "every": gold_every()}

    def dump(self, cup):
        return cup.to_dict()

    def load(self, data):
        return Cup.from_dict(data)

    def extras(self, uid):
        return {"coinValue": value(), "board": self._board(uid)}

    def rules(self):
        low, high = self.limits()
        return [
            ("Aim", "Drop coins onto the moving shelf. Whatever the pusher shoves over the front "
                    "edge drops back into your cup."),
            ("Coins", f"Every coin is {value()} UKPence. Buy a cup of them, play them, and cash out "
                      "what's left in your cup whenever you like."),
            ("Gold", f"Every {gold_every()} coins you drop, a gold coin drops in after them. Push it "
                     f"over and it pays {GOLD} coins. The count carries on between cups."),
            ("The sides", "Coins that fall down the gaps in the front corners go to the house."),
            ("Your machine", "The coins on the shelves stay as you left them for next time."),
            ("Limits", f"A cup can win at most {int(_cfg('CUP_MAX_NET', 1_000)):,} UKPence and a day "
                       f"{int(_cfg('DAY_MAX_NET', 5_000)):,}."),
            ("Cups", f"{low:,} to {high:,} UKPence."),
        ]
