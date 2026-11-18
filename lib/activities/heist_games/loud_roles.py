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
        if not isinstance(ev, list) or len(ev) > MOST or not _int(at):
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


# ---- the Muscle: CARRY THE SWAG ----------------------------------------------------------------------------------
# A tottering stack of cash bags, carried at a jog across a hall full of chaos. The stack sways like a broom balanced
# on a hand: tilt is a (past 1 or -1 it's gone too far), and it tips further the taller it is. Hold LEFT to push it
# back left, RIGHT to push it right (both, or neither: no push). Barges (short, hard) and wet floor (longer, gentler)
# come from the schedule, each flagged WARN steps ahead with the side it'll shove from. Past the tipping point the top
# bag falls off and the stack comes back to half its tilt. The walking's done for you: speed's no part of it.

class Carry(Timed):
    kind = "carry"
    SECS = {"s": 12, "m": 16}
    BAGS = {"s": 6, "m": 8}
    G0, G1 = 0.9, 0.15         # how hard the stack falls further as it tilts: more for every bag on it
    LEAN = 2.6                 # a lean's push back
    DAMP = 0.8                 # how much the sway drags
    WARN = 12                  # steps a shove is flagged before it lands (0.4s)
    EVERY = 3                  # steps between a perfect player's changes of mind (10 a second at most)

    def make_table(self, rng, size, secs):
        n = secs * 30
        for tries in range(40):
            soft = 0.92 ** tries
            push, k, i = [], 36 + rng.randint(0, 12), 0
            while True:
                barge = rng.random() < 0.6
                dur = rng.randint(7, 11) if barge else rng.randint(30, 45)
                f = (rng.uniform(3.0, 4.6) if barge else rng.uniform(1.3, 2.1)) * (0.75 + 0.25 * min(1.0, i / 3)) * soft
                if k + dur >= n - 15:
                    break
                push.append([k, k + dur, round(f * rng.choice([-1, 1]), 2)])
                k += dur + rng.randint(26, 46)
                i += 1
            p = {"bags": self.BAGS[size], "a0": round(rng.uniform(0.04, 0.1) * rng.choice([-1, 1]), 3), "push": push,
                 "warn": self.WARN, "secs": secs}
            if self.run(p, self._ideal(p), n)["bags"] == p["bags"]:
                break
        del p["secs"]
        return p

    def ok(self, p, e):
        return 0 <= e[1] <= 3

    def fresh(self, p) -> dict:
        return {"a": p["a0"], "w": 0.0, "bags": p["bags"], "hold": 0, "drops": []}

    def step(self, p, st, k) -> None:
        """One step of the stack (the page's carryStep is this, sum for sum)."""
        f = 0.0
        for s0, s1, pf in p["push"]:
            if s0 <= k < s1:
                f = f + pf
        h = st["hold"]
        lean = -self.LEAN if h == 1 else self.LEAN if h == 2 else 0.0
        acc = (self.G0 + self.G1 * st["bags"]) * st["a"] + f + lean - self.DAMP * st["w"]
        st["w"] = st["w"] + acc * DT
        st["a"] = st["a"] + st["w"] * DT
        if st["a"] >= 1.0 or st["a"] <= -1.0:
            if st["bags"] > 0:
                st["bags"] -= 1
                st["drops"].append(k)
            st["a"] = st["a"] * 0.5
            st["w"] = 0.0

    def run(self, p, ev, n) -> dict:
        st = self.fresh(p)

        def step(st, k, fired):
            for e in fired:
                st["hold"] = e[1]
            self.step(p, st, k)
        _replay(ev, n, st, step)
        return st

    def trace(self, p, n):
        """For the golden timelines: the tilt every half second and where it all ends up."""
        st, out = self.fresh(p), []
        ev, i = p["ev"], 0
        for k in range(n):
            while i < len(ev) and step_of(ev[i][0]) <= k:
                st["hold"] = ev[i][1]
                i += 1
            self.step(p, st, k)
            if k % 15 == 14:
                out.append(st["a"])
        return {"a": out, "w": st["w"], "bags": st["bags"], "drops": st["drops"]}

    def judge(self, p, n):
        kept = self.run(p, p["ev"], n)["bags"] / p["bags"]
        return kept if p["over"] else kept * n / self.steps(p)

    def _plan(self, p, lag: int, every: int, look: int, rng=None, slip: float = 0.0) -> list:
        """A timeline from a player who, every few steps, picks LEFT, RIGHT or neither by trying each a little way
        ahead (seeing the shoves coming if look > 0), from what the stack was doing lag steps ago."""
        n = p.get("secs", 0) * 30 or self.steps(p)
        st, past, ev, hold = self.fresh(p), [], [], 0

        def ahead(s, k, first):
            s = {**s, "drops": []}
            worst = 0.0
            for j in range(look or 12):
                if j < every:
                    s["hold"] = first
                else:
                    u = s["a"] + 0.35 * s["w"]
                    s["hold"] = 1 if u > 0.05 else 2 if u < -0.05 else 0
                q = p if look else {**p, "push": [x for x in p["push"] if x[0] <= k]}
                self.step(q, s, k + j)
                worst = max(worst, abs(s["a"]) + (10.0 if s["drops"] else 0.0))
            return worst

        for k in range(n):
            past.append({**st, "drops": []})
            if k % every == 0:
                seen = past[max(0, k - lag)]
                best = min((0, 1, 2), key=lambda o: (ahead(seen, k, o), o != hold))
                if rng is not None and rng.random() < slip:
                    best = rng.choice((0, 1, 2))
                if best != hold:
                    hold = best
                    ev.append([ms_of(k), hold])
            st["hold"] = hold
            self.step(p, st, k)
        return ev

    def _ideal(self, p):
        return self._plan(p, 0, self.EVERY, 24)

    def ideal(self, p):
        return self._ideal(p)

    def lazy(self, p, rng, skill):
        return self._plan(p, round((1 - skill) * 10) + 2, 4, 24 if skill > 0.7 else 0, rng, (1 - skill) * 0.25)


GAMES["muscle"] = Carry("muscle", "CARRY THE SWAG")


# ---- the Face: HOLD THE ROOM -------------------------------------------------------------------------------------
# "Everybody stay calm!" A few customers on the floor (4, 6 at the vault door), each with a panic meter rising at its
# own rate, and jolts from the schedule (a phone rings, a baby cries) that shove one up at once. Tap someone to shout
# them down (their meter back to nothing); then the Face needs BREATH steps to draw breath, and a shout in that time
# isn't heard. A meter that fills is someone bolting for the door (or the alarm): it counts, and they start again.
# Mashing does nothing: who to shout at next is the game.

class Room(Timed):
    kind = "room"
    SECS = {"s": 12, "m": 16}
    PEOPLE = {"s": 4, "m": 6}
    BREATH = 21                # steps after a shout before the next is heard (0.7s)
    RED = 0.75                 # a meter this high is in the red
    WARN = 10                  # steps a jolt shows before it lands

    def make_table(self, rng, size, secs):
        n, people = secs * 30, self.PEOPLE[size]
        for tries in range(40):
            soft = 0.93 ** tries
            m0 = [round(rng.uniform(0.0, 0.3), 3) for _ in range(people)]
            rate = [round(rng.uniform(0.1, 0.16) * soft, 3) for _ in range(people)]
            jolts, k = [], 30 + rng.randint(0, 20)
            while k < n - 20:
                jolts.append([k, rng.randrange(people), round(rng.uniform(0.2, 0.32) * soft, 3)])
                k += rng.randint(24, 44)
            p = {"m0": m0, "rate": rate, "jolts": jolts, "breath": self.BREATH, "red": self.RED, "warn": self.WARN, "secs": secs}
            st = self.run(p, self._ideal(p), n)
            if st["bolts"] == 0 and st["reds"] == 0:
                break
        del p["secs"]
        return p

    def ok(self, p, e):
        return 0 <= e[1] < len(p["m0"])

    def fresh(self, p) -> dict:
        return {"m": list(p["m0"]), "ready": 0, "bolts": 0, "reds": 0, "shouts": 0}

    def step(self, p, st, k, fired) -> None:
        """One step of the room (the page's roomStep is this, sum for sum)."""
        m = st["m"]
        for e in fired:
            if k >= st["ready"]:
                m[e[1]] = 0.0
                st["ready"] = k + p["breath"]
                st["shouts"] += 1
        for i in range(len(m)):
            m[i] = m[i] + p["rate"][i] * DT
        for jk, who, amt in p["jolts"]:
            if jk == k:
                m[who] = m[who] + amt
        hot = False
        for i in range(len(m)):
            if m[i] >= 1.0:
                st["bolts"] += 1
                m[i] = 0.0
            if m[i] >= p["red"]:
                hot = True
        if hot:
            st["reds"] += 1

    def run(self, p, ev, n) -> dict:
        st = self.fresh(p)
        _replay(ev, n, st, lambda st, k, fired: self.step(p, st, k, fired))
        return st

    def trace(self, p, n):
        st, out = self.fresh(p), []
        ev, i = p["ev"], 0
        for k in range(n):
            fired = []
            while i < len(ev) and step_of(ev[i][0]) <= k:
                fired.append(ev[i])
                i += 1
            self.step(p, st, k, fired)
            if k % 15 == 14:
                out.append(list(st["m"]))
        return {"m": out, "bolts": st["bolts"], "reds": st["reds"], "shouts": st["shouts"], "ready": st["ready"]}

    def judge(self, p, n):
        st = self.run(p, p["ev"], n)
        N = self.steps(p)
        s = max(0.0, 1 - 0.25 * st["bolts"] - 0.3 * st["reds"] / N)
        return s if p["over"] else s * n / N

    def _due(self, p, m: list, k: int, n: int) -> list:
        """For each person, the step their meter would hit the red if nobody shouted at them (n if never)."""
        out = []
        for i in range(len(m)):
            v, j = m[i], k
            while j < n:
                v = v + p["rate"][i] * DT
                for jk, who, amt in p["jolts"]:
                    if jk == j and who == i:
                        v = v + amt
                if v >= p["red"]:
                    break
                j += 1
            out.append(j)
        return out

    def _plan(self, p, lag: int, rng=None, miss: float = 0.0) -> list:
        """Shout, as soon as there's breath (and lag steps more), at whoever's going to be in the red soonest (or for
        a so-so player, whoever looks worst, now and then the wrong one)."""
        n = p.get("secs", 0) * 30 or self.steps(p)
        st, ev, want = self.fresh(p), [], None
        for k in range(n):
            fired = []
            if k >= st["ready"] + lag:
                if rng is None:
                    due = self._due(p, st["m"], k, n)
                    who = min(range(len(due)), key=lambda i: (due[i], -st["m"][i]))
                    want = who if due[who] < n else None
                else:
                    who = max(range(len(st["m"])), key=lambda i: st["m"][i])
                    if rng.random() < miss:
                        who = rng.randrange(len(st["m"]))
                    want = who if st["m"][who] > 0.35 else None
                if want is not None:
                    fired = [[ms_of(k), want]]
                    ev.append(fired[0])
            self.step(p, st, k, fired)
        return ev

    def _ideal(self, p):
        return self._plan(p, 0)

    def ideal(self, p):
        return self._ideal(p)

    def lazy(self, p, rng, skill):
        return self._plan(p, round((1 - skill) * 18) + 4, rng, (1 - skill) * 0.5)


GAMES["face"] = Room("face", "HOLD THE ROOM")


# ---- the Hacker: JAM THE SIGNAL ----------------------------------------------------------------------------------
# The alarm's dialling out to the police: ride its frequency to jam the call. The signal wanders up and down a scope
# (straight lines between the schedule's keypoints; at the vault door it also hops, flickered WARN steps ahead). Your
# jammer rises while you hold and falls when you let go, with some weight to it. Keep it on the signal: the band you
# have to stay in narrows as it goes. Scored on the share of steps in the band, a little squared (90% is about 0.85).

class Jam(Timed):
    kind = "jam"
    SECS = {"s": 12, "m": 16}
    UP, DOWN, DRAG = 3.5, 3.5, 5.0     # the jammer's push while held, its fall when not, and its drag
    BAND = {"s": (0.13, 0.08), "m": (0.12, 0.07)}   # the band's half-width, from the start to the end
    WARN = 9                           # steps a hop flickers before it comes (0.3s)
    EVERY = 2                          # steps between a perfect player's changes of mind (15 a second at most)

    def make_table(self, rng, size, secs):
        n = secs * 30
        for tries in range(40):
            soft = 0.92 ** tries
            y = round(rng.uniform(0.35, 0.65), 3)
            keys, hops, k = [[0, y]], [], 0
            hop_at = sorted(rng.sample(range(90, n - 60, 30), 3 if size == "m" else 0))
            while k < n:
                if hop_at and k >= hop_at[0]:
                    hop_at.pop(0)
                    jump = rng.uniform(0.22, 0.32) * soft
                    y = round(y + jump if y < 0.5 else y - jump, 3)
                    k += 9
                    hops.append(k - 9)
                else:
                    dk = rng.randint(24, 42)
                    most = 0.42 * soft * dk / 30
                    y = round(min(0.85, max(0.15, y + rng.uniform(-most, most))), 3)
                    k += dk
                keys.append([k, y])
            hw = self.BAND[size]
            p = {"keys": keys, "hops": hops, "band": [round(hw[0] + 0.03 * (1 - soft), 3), round(hw[1] + 0.03 * (1 - soft), 3)],
                 "warn": self.WARN, "secs": secs}
            if self.run(p, self._ideal(p), n)["inside"] == n:
                break
        del p["secs"]
        return p

    def ok(self, p, e):
        return e[1] in (0, 1)

    @staticmethod
    def sig(p, k) -> float:
        """The signal at step k: a straight line between the keypoints either side (the page's jamSig)."""
        keys = p["keys"]
        i = 0
        while i < len(keys) - 2 and keys[i + 1][0] <= k:
            i += 1
        k0, y0 = keys[i]
        k1, y1 = keys[i + 1]
        return y0 + (y1 - y0) * (k - k0) / (k1 - k0)

    def fresh(self, p) -> dict:
        return {"y": p["keys"][0][1], "v": 0.0, "hold": 0, "inside": 0}

    def step(self, p, st, k, n) -> None:
        """One step of the jammer (the page's jamStep is this, sum for sum); n is the whole task's steps."""
        acc = self.UP if st["hold"] else -self.DOWN
        st["v"] = st["v"] + acc * DT
        st["v"] = st["v"] - self.DRAG * st["v"] * DT
        st["y"] = st["y"] + st["v"] * DT
        if st["y"] < 0.0:
            st["y"] = 0.0
            st["v"] = 0.0
        if st["y"] > 1.0:
            st["y"] = 1.0
            st["v"] = 0.0
        b0, b1 = p["band"]
        if abs(st["y"] - self.sig(p, k)) <= b0 + (b1 - b0) * k / n:
            st["inside"] += 1

    def run(self, p, ev, n) -> dict:
        st, N = self.fresh(p), p.get("secs", 0) * 30 or self.steps(p)

        def step(st, k, fired):
            for e in fired:
                st["hold"] = e[1]
            self.step(p, st, k, N)
        _replay(ev, n, st, step)
        return st

    def trace(self, p, n):
        st, out = self.fresh(p), []
        ev, i = p["ev"], 0
        for k in range(n):
            while i < len(ev) and step_of(ev[i][0]) <= k:
                st["hold"] = ev[i][1]
                i += 1
            self.step(p, st, k, n)
            if k % 15 == 14:
                out.append(st["y"])
        return {"y": out, "v": st["v"], "inside": st["inside"]}

    def judge(self, p, n):
        share = self.run(p, p["ev"], n)["inside"] / self.steps(p)
        return share * math.sqrt(share)

    def _plan(self, p, lag: int, every: int, rng=None, err: float = 0.0) -> list:
        """Every few steps, hold or let go: a perfect player tries both a little way ahead against the signal coming
        (hops and all); a so-so one chases where the signal is now, from what the jammer was doing lag steps ago."""
        n = p.get("secs", 0) * 30 or self.steps(p)
        st, past, ev, hold = self.fresh(p), [], [], 0
        off = 0.0

        def cost(s, k, first):
            s = dict(s)
            c = 0.0
            for j in range(18):
                if j < every:
                    s["hold"] = first
                else:
                    s["hold"] = 1 if s["y"] + 0.12 * s["v"] < self.sig(p, min(k + j + 3, n - 1)) else 0
                before = s["inside"]
                self.step(p, s, min(k + j, n - 1), n)
                d = abs(s["y"] - self.sig(p, min(k + j, n - 1)))
                c += (0.0 if s["inside"] > before else 1.0 + d) + 0.05 * d
            return c

        for k in range(n):
            past.append(dict(st))
            if k % every == 0:
                if rng is None:
                    want = min((0, 1), key=lambda o: (cost(st, k, o), o != hold))
                else:
                    seen = past[max(0, k - lag)]
                    if k % 30 == 0:
                        off = rng.gauss(0, err)
                    want = 1 if seen["y"] + 0.2 * seen["v"] < self.sig(p, k) + off else 0
                if want != hold:
                    hold = want
                    ev.append([ms_of(k), hold])
            st["hold"] = hold
            self.step(p, st, k, n)
        return ev

    def _ideal(self, p):
        return self._plan(p, 0, self.EVERY)

    def ideal(self, p):
        return self._ideal(p)

    def lazy(self, p, rng, skill):
        return self._plan(p, round((1 - skill) * 12) + 3, 3, rng, (1 - skill) * 0.12)


GAMES["hacker"] = Jam("hacker", "JAM THE SIGNAL")


# ---- the Wheelman: BACK IT IN ------------------------------------------------------------------------------------
# Reversing the van to the loading door at speed, so the Muscle can throw the bags in. Each round (3, 4 at the vault
# door, W steps each) the van rolls back towards the dock from x0 metres at v0 m/s, on its own slope (downhill adds
# speed) and grip (wet: the brake does less). Hold BRAKE: it builds up over 0.4s while held and lets off over 0.25s
# when you let go, so you feather it. Stopped is parked; touching the dock is a bump. Per round, by the gap: within
# 0.2m scores 1, sliding to 0.2 at 2m (and to nothing at 4m); a bump scores 0.3; still rolling at the end, half.

class Brake(Timed):
    kind = "brake"
    ROUNDS = {"s": 3, "m": 4}
    SECS = {"s": 12, "m": 16}
    W = 120                    # steps a round (4s)
    BMAX = 8.0                 # m/s/s at full brake, dry

    def make_table(self, rng, size, secs):
        rounds = []
        for r in range(self.ROUNDS[size]):
            for tries in range(60):
                rd = {"x0": round(rng.uniform(7.5, 11.0), 2), "v0": round(rng.uniform(3.4, 4.2) + 0.5 * r, 2),
                      "slope": 0.0 if r == 0 else rng.choice([0.0, 0.6, 1.2]), "grip": 1.0 if r == 0 else rng.choice([1.0, 1.0, 0.65])}
                if self._best(rd)[1] >= 0.9999:
                    break
            rounds.append(rd)
        return {"rounds": rounds, "w": self.W}

    def ok(self, p, e):
        return e[1] in (0, 1)

    def fresh(self, p) -> dict:
        return {"x": 0.0, "v": 0.0, "b": 0.0, "parked": False, "touched": False, "hold": 0, "res": []}

    def step(self, p, st, k) -> None:
        """One step of the van (the page's brakeStep is this, sum for sum)."""
        W = p["w"]
        rd = p["rounds"][k // W]
        if k % W == 0:
            st.update(x=rd["x0"], v=rd["v0"], b=0.0, parked=False, touched=False)
        if st["hold"]:
            st["b"] = min(1.0, st["b"] + DT / 0.4)
        else:
            st["b"] = max(0.0, st["b"] - DT / 0.25)
        if not st["parked"] and not st["touched"]:
            st["v"] = st["v"] + (rd["slope"] - self.BMAX * rd["grip"] * st["b"]) * DT
            if st["v"] <= 0.0:
                st["v"] = 0.0
                st["parked"] = True
            st["x"] = st["x"] - st["v"] * DT
            if st["x"] <= 0.0:
                st["x"] = 0.0
                st["touched"] = True
        if k % W == W - 1:
            st["res"].append([st["x"], st["parked"], st["touched"]])

    @staticmethod
    def rscore(x: float, parked: bool, touched: bool) -> float:
        if touched:
            return 0.3
        s = 1.0 if x <= 0.2 else 1 - 0.8 * (x - 0.2) / 1.8 if x <= 2.0 else max(0.0, 0.2 - 0.1 * (x - 2.0))
        return s if parked else s * 0.5

    def run(self, p, ev, n) -> dict:
        st = self.fresh(p)

        def step(st, k, fired):
            for e in fired:
                st["hold"] = e[1]
            self.step(p, st, k)
        _replay(ev, n, st, step)
        return st

    def trace(self, p, n):
        st = self.run(p, p["ev"], n)
        return {"res": [[x, 1 if a else 0, 1 if b else 0] for x, a, b in st["res"]], "x": st["x"], "v": st["v"], "b": st["b"]}

    def judge(self, p, n):
        st = self.run(p, p["ev"], n)
        got = [self.rscore(*r) for r in st["res"]]
        if len(st["res"]) < len(p["rounds"]) and n % p["w"] and (st["parked"] or st["touched"]):
            got.append(self.rscore(st["x"], st["parked"], st["touched"]))      # this round's, if it's stopped
        return sum(got) / len(p["rounds"])

    def _round(self, rd: dict, on: int, off: int = -1, again: int = -1) -> tuple:
        """One round on its own: brake from step `on` (let off at `off`, back on at `again`)."""
        p = {"rounds": [rd], "w": self.W}
        st = self.fresh(p)
        for j in range(self.W):
            st["hold"] = 1 if j >= on and not (off >= 0 and off <= j < again) else 0
            self.step(p, st, j)
        return st["res"][0]

    def _best(self, rd: dict) -> tuple:
        """The best braking for a round: when to start, and if it helps, a quick let-off to land it."""
        best = (0, -1, -1), -1.0
        for on in range(self.W):
            s = self.rscore(*self._round(rd, on))
            if s > best[1]:
                best = (on, -1, -1), s
        if best[1] < 0.9999:
            on = best[0][0]
            for start in range(max(0, on - 6), on + 2):
                for off in range(start + 2, min(self.W, start + 40)):
                    for again in range(off + 1, min(self.W, off + 6)):
                        s = self.rscore(*self._round(rd, start, off, again))
                        if s > best[1]:
                            best = (start, off, again), s
                if best[1] >= 0.9999:
                    break
        return best

    def _timeline(self, p, plan: list) -> list:
        ev = []
        for r, (on, off, again) in enumerate(plan):
            base = r * p["w"]
            ev.append([ms_of(base + on), 1])
            if off >= 0:
                ev += [[ms_of(base + off), 0], [ms_of(base + again), 1]]
            if r < len(p["rounds"]) - 1:
                ev.append([ms_of(base + p["w"]), 0])
        return ev

    def ideal(self, p):
        return self._timeline(p, [self._best(rd)[0] for rd in p["rounds"]])

    def lazy(self, p, rng, skill):
        plan = []
        for rd in p["rounds"]:
            on = self._best(rd)[0][0]
            plan.append((max(0, min(self.W - 1, round(on + rng.gauss(0, (1 - skill) * 14)))), -1, -1))
        return self._timeline(p, plan)


GAMES["wheelman"] = Brake("wheelman", "BACK IT IN")
