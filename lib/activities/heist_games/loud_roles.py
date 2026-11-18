"""A loud job's tasks, one proper game for each role, built round what that role does when it's noisy:

    call   the Lookout: police cars come in on different roads; tap the one that'll reach the bank first
    carry  the Muscle: jog a tottering stack of cash bags across the hall; lean LEFT and RIGHT to keep it up
    room   the Face: keep the customers down; shout at whoever's panicking most (a breath between shouts)
    jam    the Hacker: hold to ride the jammer up, let go to drop it, and keep it on the alarm's signal
    brake  the Wheelman: back the van in to the loading door; feather the brake to stop as close as you can
    lance  the Safecracker: drag the thermal lance round the cut line; the tip follows at its own pace

Each is a schedule made here and shown in full (where the cars are, when someone's barged into, where the signal
goes), so nothing in them is secret. The page plays it from the moment its board shows and sends what you did as a
timeline ({"ev": [[ms, ...], ...], "at": ms}, every half a second or so, and {"end": true} when the time's up); the
bot plays the same timeline through the same steps (_replay, 30 a second) and judges that. The steps use nothing but
+ - * / and sqrt on numbers from the schedule, so the page's copy of them (ukplace-activities
src/heist/games/loudsim.ts) lands on exactly the same numbers: what you see is what's judged. A doctored page can
only play perfectly, and a loud job's edge (heist.loud_edge) never comes down as far as a perfect quiet job's.

A timeline can't be from the future: heist.play() passes the ms since the task's clock started ("_now") and anything
later than that (and a little slack) is refused, as is anything out of order, past the end, or faster than RATE a
second. A task cut off (by the breach, or a tab closed) is scored as it stands, up to its last "at".

Every one of these is a Game with key "loud" in LOUD (loud.py), and returns a score 0 to 1 like the rest; the money
round it (heist._settle, loud_edge, LOUD_CUT, LOUD_SAFE, LOUD_LOOT) doesn't change."""

import math

from lib.activities.heist_games import Game, Refuse

DT = 1 / 30                # the replay's step, seconds
GRACE = 2000               # ms an event can be ahead of the bot's clock (a slow reply, a clock a little out)
RATE = 25                  # events a second at most (the page spaces them 40ms apart)
MOST = 600                 # events in a task at most
TAIL = 500                 # ms past the end an event's dropped, not refused (the last press as the time runs out)
SIZES = {"s": "s", "m": "m", "big": "m"}


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and abs(x) < 10 ** 9


def step_of(ms: int) -> int:
    """The replay step an event at this ms lands in (exact in integers, the same on the page)."""
    return (ms * 3) // 100


def ms_of(k: int) -> int:
    """The first whole ms in step k."""
    return (k * 100 + 2) // 3


def _replay(ev: list, n: int, state, step) -> None:
    """Play a timeline through n fixed steps: step(state, k, fired) for each, fired the events that land in step k
    (in order). Inputs that hold (a key down) are the step function's to remember."""
    i, m = 0, len(ev)
    for k in range(n):
        fired = []
        while i < m and step_of(ev[i][0]) <= k:
            fired.append(ev[i])
            i += 1
        step(state, k, fired)


class Timed(Game):
    """A loud task played as a timeline against a public schedule. A game sets kind, SECS (by size) and WIDTH (the
    numbers in an event, its ms first), fills in make_table, ok and judge, and the rest is here."""
    key, pace = "loud", (1.0, 2.0)
    kind = ""
    SECS = {"s": 12, "m": 16}
    WIDTH = 2

    def __init__(self, role: str, title: str):
        self.role, self.title = role, title

    # ---- to fill in ----
    def make_table(self, rng, size: str, secs: int) -> dict:
        raise NotImplementedError

    def ok(self, p: dict, e: list) -> bool:
        """An event's numbers after its ms are ones this game takes."""
        return True

    def judge(self, p: dict, n: int) -> float:
        """The score from the replay of the first n steps (all of them once it's over)."""
        raise NotImplementedError

    def ideal(self, p: dict) -> list:
        """A perfect timeline, from the start."""
        raise NotImplementedError

    def lazy(self, p: dict, rng, skill: float) -> list:
        """A so-so player's timeline, from the start."""
        raise NotImplementedError

    def settled(self, p: dict) -> bool:
        """Done before the time's up (every round called, say)."""
        return False

    # ---- the Game ----
    def secs(self, p: dict) -> float:
        return p["secs"]

    def steps(self, p: dict) -> int:
        return p["secs"] * 30

    def make(self, rng, size, story):
        size = SIZES.get(size, "s")
        secs = self.SECS[size]
        return {"kind": self.kind, "size": size, "secs": secs, **self.make_table(rng, size, secs), "ev": [], "at": 0,
                "over": False}

    def public(self, p):
        return dict(p)

    def move(self, p, body):
        ev = body.get("ev", [])
        end = body.get("end") is True
        at = body.get("at", 0)
        now = body.get("_now")
        if not isinstance(ev, list) or len(ev) > 4 * RATE or not _int(at):
            raise Refuse("That's not a go.")
        T = p["secs"] * 1000
        last = p["ev"][-1][0] if p["ev"] else 0
        rows = []
        for e in ev:
            if not (isinstance(e, list) and len(e) == self.WIDTH and all(_int(x) for x in e)) or not self.ok(p, e):
                raise Refuse("That's not a go.")
            if e[0] < last or e[0] > T + TAIL:
                raise Refuse("That's out of order.")
            last = e[0]
            if e[0] <= T:
                rows.append(e)
        if len(p["ev"]) + len(rows) > MOST:
            raise Refuse("That's a lot of goes.")
        per: dict[int, int] = {}
        for e in p["ev"][-RATE:] + rows:
            per[e[0] // 1000] = per.get(e[0] // 1000, 0) + 1
        if any(v > RATE for v in per.values()):
            raise Refuse("Not so fast.")
        at = T if end else max(p["at"], min(T, at), min(T, last))
        if _int(now) and max(at, last) > now + GRACE:
            raise Refuse("Not so fast.")
        p["ev"].extend(rows)
        p["at"] = at
        if end or self.settled(p):
            p["over"] = True

    def done(self, p):
        return p["over"]

    def score(self, p):
        n = self.steps(p) if p["over"] else min(self.steps(p), step_of(p["at"]))
        return max(0.0, min(1.0, self.judge(p, n)))

    def solve(self, p):
        rest = [e for e in self.ideal(p) if not p["ev"] or e[0] >= p["ev"][-1][0]]
        return [{"ev": rest, "end": True}]

    def bot(self, p, rng, skill):
        return {"ev": self.lazy(p, rng, skill), "end": True}


# ---- the Lookout: CALL IT ----------------------------------------------------------------------------------------
# A top-down street plan, the bank in the middle and a road in from each side. Each round 2-4 police cars come in on
# different roads at different speeds (later on, one stops at a red light for a moment, shown), and you tap the one
# that'll reach the bank first, before it does. Nothing for being quick: it's whether you called it right.

class Call(Timed):
    kind = "call"
    ROUNDS = {"s": 4, "m": 6}
    SECS = {"s": 13, "m": 18}         # (the rounds' own schedule decides; this is the most they take)
    GAP = 700                         # ms between one round's first car arriving and the next round
    LEAD = 300                        # ms into a round before a call counts (the cars have to be seen)

    def make_table(self, rng, size, secs):
        n = self.ROUNDS[size]
        rounds, t = [], 500
        for k in range(n):
            cars = 2 if k == 0 else 3 if k < 3 else rng.choice([3, 4])
            close = 500 - 300 * k / max(1, n - 1)                     # ms the first beats the next by: closer later
            first = rng.randint(1500, 2100)
            etas = [first, first + round(close)] + [first + round(close) + rng.randint(150, 500) for _ in range(cars - 2)]
            stop = k >= 2                                              # one car stops at a red light
            roads = rng.sample(range(4), cars)
            out = []
            for i, eta in enumerate(etas):
                wait = rng.randint(400, 700) if stop and i == (1 if k % 2 else 0) else 0
                move = (eta - wait) / 1000
                v = round(rng.uniform(max(0.28, 0.45 / move), max(0.28, min(0.62, 1.0 / move))), 3)
                d = round(v * move, 3)
                car = {"road": roads[i], "d": d, "v": v}
                if wait:
                    car.update(light=round(d * rng.uniform(0.35, 0.6), 3), wait=wait)
                out.append(car)
            order = list(range(cars))
            rng.shuffle(order)
            out = [out[i] for i in order]
            rounds.append({"t": t, "cars": out, "first": order.index(0), "end": t + first})
            t += first + self.GAP
        return {"rounds": rounds, "calls": []}

    def make(self, rng, size, story):
        p = super().make(rng, size, story)
        p["secs"] = math.ceil((p["rounds"][-1]["end"] + self.GAP) / 1000)
        return p

    @staticmethod
    def eta(car: dict) -> float:
        """When a car reaches the bank, in ms from its round's start."""
        return 1000 * car["d"] / car["v"] + car.get("wait", 0)

    def ok(self, p, e):
        return 0 <= e[1] < 4

    def move(self, p, body):
        super().move(p, body)
        self._calls(p)
        if len(p["calls"]) >= len(p["rounds"]):
            p["over"] = True

    @staticmethod
    def _round_at(p: dict, ms: int) -> int:
        for i, r in enumerate(p["rounds"]):
            if r["t"] + Call.LEAD <= ms < r["end"]:
                return i
        return -1

    def _calls(self, p: dict) -> None:
        """Each round's call: the first tap in it on one of its cars (later ones, and any between rounds, don't count)."""
        calls, seen = [], set()
        for ms, car in p["ev"]:
            k = self._round_at(p, ms)
            if k >= 0 and k not in seen and car < len(p["rounds"][k]["cars"]):
                seen.add(k)
                calls.append([k, car])
        p["calls"] = calls

    def judge(self, p, n):
        return sum(1 for k, car in p["calls"] if p["rounds"][k]["first"] == car) / len(p["rounds"])

    def score(self, p):
        return self.judge(p, 0)

    def ideal(self, p):
        return [[r["t"] + self.LEAD + 200, r["first"]] for r in p["rounds"]]

    def lazy(self, p, rng, skill):
        out = []
        for r in p["rounds"]:
            n = len(r["cars"])
            pick = r["first"] if rng.random() < skill else rng.choice([i for i in range(n) if i != r["first"]])
            out.append([round(r["t"] + self.LEAD + (r["end"] - r["t"] - self.LEAD) * rng.uniform(0.3, 0.8)), pick])
        return out


GAMES = {"lookout": Call("lookout", "CALL IT")}
