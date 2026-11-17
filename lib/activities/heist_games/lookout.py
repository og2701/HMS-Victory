"""The Lookout's games beyond the first (which is in heist.py). See __init__.py for the rules a game follows.

Three are rounds of a quick look (spot, count, moved): the page asks for a round with {"look": true} and only then
gets its scene, which it shows for a moment and hides before the answer comes, so a scene is never out before it's
asked for. Two race the clock (sweep, duck): make() draws up the schedule, the page plays it from c.since() and
sends each tap with its time in ms, and how it went is worked out here from the schedule. The last is a little
Minesweeper on the bank's floor, whose sensors never leave the server.
"""

import math
import random

from lib.activities.heist_games import Game, Refuse

# the figures the page draws (public/heist/g-lookout-<name>.webp)
GUARDS = ["guard1", "guard2", "guard3"]
STAFF = ["cleaner1", "cleaner2", "manager"]              # who else is about in a bank at night
# folk with two of a guard's three marks (the peaked cap, the hi-vis vest, the torch) but not all three
LOOKALIKES = ["worker", "warden", "jogger"]
WINDOWS = 9                                              # the bank's front: three floors of three windows


def _int(body: dict, name: str, below: int, msg: str) -> int:
    """A whole number 0 to below-1 from a move, or Refuse (True and False are ints in Python, so they're turned
    away by hand)."""
    v = body.get(name)
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < below:
        raise Refuse(msg)
    return v


def _ms(body: dict, name: str, longest: int, msg: str) -> int:
    """A time in ms since the start from a move, 0 to longest, or Refuse."""
    v = body.get(name)
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= longest:
        raise Refuse(msg)
    return int(round(v))


# ---- a quick look, then the answer --------------------------------------------------------------------------

class _Looks(Game):
    """Rounds of a quick look and an answer. p["rounds"] holds each round's scene and answer, p["said"] the answers
    so far, and p["looking"] whether this round's scene is out (asked for with {"look": true}). A subclass says what
    the page sees of a round, what answers it, and how an answer scores."""
    ANSWER = ""                                          # the move's name for an answer
    ASK = ""                                             # what to say to a move that isn't an answer

    def _scene(self, rd: dict) -> dict:
        raise NotImplementedError

    def _right(self, rd: dict) -> int:
        raise NotImplementedError

    def _mark(self, rd: dict, said: int) -> float:
        return 1.0 if said == self._right(rd) else 0.0

    def _below(self, rd: dict) -> int:
        """Answers run 0 to this less one."""
        raise NotImplementedError

    def _miss(self, rd: dict, rng: random.Random) -> int:
        """A wrong answer a so-so player might give."""
        raise NotImplementedError

    def public(self, p):
        at = len(p["said"])
        out = {"rounds": len(p["rounds"]), "at": at, "said": p["said"], "looking": p["looking"],
               "marks": [round(self._mark(rd, s), 2) for rd, s in zip(p["rounds"], p["said"])],
               "was": [self._right(rd) for rd in p["rounds"][:at]]}      # the answers to rounds already answered
        if p["looking"] and at < len(p["rounds"]):
            out["scene"] = self._scene(p["rounds"][at])
        return out

    def move(self, p, body):
        if self.done(p):
            raise Refuse("That's all of them.")
        if body.get("look") is True:
            p["looking"] = True                          # asking twice changes nothing
            return
        if not p["looking"]:
            raise Refuse("Have a look first.")
        said = _int(body, self.ANSWER, self._below(p["rounds"][len(p["said"])]), self.ASK)
        p["said"].append(said)
        p["looking"] = False

    def done(self, p):
        return len(p["said"]) >= len(p["rounds"])

    def score(self, p):
        return sum(self._mark(rd, s) for rd, s in zip(p["rounds"], p["said"])) / len(p["rounds"])

    def solve(self, p):
        out = []
        for i, rd in enumerate(p["rounds"][len(p["said"]):]):
            if i or not p["looking"]:
                out.append({"look": True})
            out.append({self.ANSWER: self._right(rd)})
        return out

    def bot(self, p, rng, skill):
        if not p["looking"]:
            return {"look": True}
        rd = p["rounds"][len(p["said"])]
        return {self.ANSWER: self._right(rd) if rng.random() < skill + 0.05 else self._miss(rd, rng)}


class Spot(_Looks):
    """The bank's front at night with a few windows lit and someone in each, seen only as a shadow on the blind:
    one's a guard (the peaked cap and the torch). It shows for a moment and goes dark, then you tap where the guard
    was. Where he is is the secret, so a round's scene only goes out when it's asked for."""
    key, role, title = "spot", "lookout", "SPOT THE GUARD"
    pace = (1.0, 2.5)
    ANSWER, ASK = "at", "Tap a window."
    # each round: how many windows are lit, and how long it shows (ms)
    SIZES = {"s": [(3, 1400), (4, 1200), (4, 1000)],
             "m": [(3, 1300), (4, 1100), (5, 1000), (5, 900)],
             "big": [(4, 1200), (5, 1050), (5, 950), (6, 850), (6, 800)]}

    def make(self, rng, size, story):
        rounds = []
        for lit, flash in self.SIZES.get(size, self.SIZES["s"]):
            wins = rng.sample(range(WINDOWS), lit)
            figs: list[dict | None] = [None] * WINDOWS
            for k, w in enumerate(wins):
                figs[w] = {"k": rng.choice(GUARDS) if k == 0 else rng.choice(STAFF), "flip": rng.random() < 0.5,
                           "x": rng.choice([-1, 0, 1])}       # where it stands behind the blind
            rounds.append({"figs": figs, "guard": wins[0], "flash": flash})
        return {"rounds": rounds, "said": [], "looking": False}

    def _scene(self, rd):
        return {"figs": rd["figs"], "flash": rd["flash"]}

    def _right(self, rd):
        return rd["guard"]

    def _below(self, rd):
        return WINDOWS

    def _mark(self, rd, said):
        if said == rd["guard"]:
            return 1.0
        (r, c), (gr, gc) = divmod(said, 3), divmod(rd["guard"], 3)
        return 0.3 if abs(r - gr) + abs(c - gc) == 1 else 0.0   # the window next door: nearly had him

    def _miss(self, rd, rng):
        return rng.choice([i for i, f in enumerate(rd["figs"]) if f and i != rd["guard"]])


class Count(_Looks):
    """Folk hurrying past the bank at night, both ways along the pavement: guards among cleaners, the manager, a
    man walking his dog, a cat, and folk who could pass for one at a glance (a road worker in hi-vis, a traffic
    warden in a peaked cap, a jogger with a head torch). Count only the guards, then pick the number. How many is
    the secret, so a round's parade only goes out when it's asked for."""
    key, role, title = "count", "lookout", "COUNT THE GUARDS"
    pace = (2.0, 4.0)
    ANSWER, ASK = "n", "Pick how many."
    TOP = 9                                              # the buttons go up to 8
    # each round: how many go by, how many of them are guards (fewest, most), over how long (ms), and how long
    # one takes to cross (ms)
    SIZES = {"s": [(7, (2, 4), 3600, 1500), (9, (3, 5), 4000, 1250)],
             "m": [(8, (3, 5), 3800, 1400), (10, (3, 6), 4400, 1250), (11, (4, 6), 4800, 1100)],
             "big": [(9, (3, 6), 4200, 1300), (11, (4, 7), 4800, 1100), (13, (4, 7), 5200, 950)]}
    OTHERS = STAFF + ["dogman", "cat"]
    ALIKE = {"s": 0.3, "m": 0.5, "big": 0.65}            # how many of the rest could pass for a guard

    def make(self, rng, size, story):
        rounds = []
        for i, (n, (lo, hi), span, cross) in enumerate(self.SIZES.get(size, self.SIZES["s"])):
            guards = rng.randint(lo, hi)
            alike = self.ALIKE.get(size, self.ALIKE["s"])
            kinds = [rng.choice(GUARDS) for _ in range(guards)] + [
                rng.choice(LOOKALIKES if rng.random() < alike else self.OTHERS) for _ in range(n - guards)]
            rng.shuffle(kinds)
            step = span / n
            folk = []
            for j, k in enumerate(kinds):
                dur = cross * rng.uniform(0.85, 1.15) * (0.8 if k == "cat" else 1)
                folk.append({"k": k, "at": int(j * step + rng.uniform(0, step * 0.6)), "dur": int(dur),
                             "dir": 1 if i == 0 else rng.choice([1, -1]),     # the first round all one way
                             "lane": rng.randrange(2)})                       # 0 the far side of the pavement
            rounds.append({"folk": folk, "guards": guards})
        return {"rounds": rounds, "said": [], "looking": False}

    def _scene(self, rd):
        return {"folk": rd["folk"], "len": max(f["at"] + f["dur"] for f in rd["folk"])}

    def _right(self, rd):
        return rd["guards"]

    def _below(self, rd):
        return self.TOP

    def _mark(self, rd, said):
        return {0: 1.0, 1: 0.3}.get(abs(said - rd["guards"]), 0.0)    # one out gets a little

    def _miss(self, rd, rng):
        return max(0, min(self.TOP - 1, rd["guards"] + rng.choice([-1, 1, 1, -2])))


# where folk can stand in the banking hall: four spots at the back, five in the middle, four at the front, each
# as (row, how far across in fifths); the page draws them at the same places
SPOTS = [(0, 1), (0, 2), (0, 3), (0, 4), (1, 0.5), (1, 1.5), (1, 2.5), (1, 3.5), (1, 4.5), (2, 1), (2, 2), (2, 3), (2, 4)]


class Moved(_Looks):
    """The banking hall with guards and cleaners about. It shows for a couple of seconds, goes dark for a blink,
    then shows again with one of them stepped somewhere else, and (past the smallest) one or two others only turned
    round where they stand: tap the one who moved. Who moved is the secret, so a round's two pictures only go out
    when it's asked for."""
    key, role, title = "moved", "lookout", "WHO MOVED"
    pace = (2.0, 4.0)
    ANSWER, ASK = "pick", "Tap someone."
    # each round: how many are in the hall, how long the first look lasts (ms), and how many others turn round
    SIZES = {"s": [(5, 2200, 0), (6, 2000, 0)],
             "m": [(7, 1800, 1), (8, 1700, 1), (9, 1600, 1)],
             "big": [(8, 1700, 1), (9, 1600, 2), (10, 1500, 2), (10, 1400, 2)]}
    FOLK = GUARDS + ["cleaner1", "cleaner2", "manager"]

    def make(self, rng, size, story):
        rounds = []
        for n, show, turns in self.SIZES.get(size, self.SIZES["s"]):
            spots = rng.sample(range(len(SPOTS)), n)
            mover = rng.randrange(n)
            (r, x) = SPOTS[spots[mover]]
            # somewhere empty, a step or two away: far enough to see, near enough that you have to have looked
            empty = [i for i in range(len(SPOTS)) if i not in spots]
            near = [i for i in empty if 1 <= abs(SPOTS[i][0] - r) + abs(SPOTS[i][1] - x) <= (2 if n > 6 else 3)]
            to = rng.choice(near or empty)
            folk = [{"k": rng.choice(self.FOLK), "flip": rng.random() < 0.5} for _ in range(n)]
            turn = rng.sample([i for i in range(n) if i != mover], turns)   # turned round, but haven't moved
            rounds.append({"folk": folk, "a": spots, "b": [to if i == mover else s for i, s in enumerate(spots)],
                           "turn": sorted(turn), "mover": mover, "show": show})
        return {"rounds": rounds, "said": [], "looking": False}

    def _scene(self, rd):
        return {"folk": rd["folk"], "a": rd["a"], "b": rd["b"], "turn": rd.get("turn", []), "show": rd["show"]}

    def _right(self, rd):
        return rd["mover"]

    def _below(self, rd):
        return len(rd["folk"])

    def _miss(self, rd, rng):
        return rng.choice([i for i in range(len(rd["folk"])) if i != rd["mover"]])


# ---- against the clock --------------------------------------------------------------------------------------

class Sweep(Game):
    """A corridor watched by a camera on the far wall, its light over the whole floor. Every so often it turns
    away to the wall for a moment, and for less as it goes on (later on it sometimes only twitches): tap DASH to
    run to the next pillar while it's looking away. If it's looking while you're in the open you're spotted (you
    get there, but it costs). The camera's schedule is what you time against, so the page sees it all; it sends
    each dash's time and whether you were seen is worked out here, the same way the page draws it."""
    key, role, title = "sweep", "lookout", "CAMERA SWEEP"
    pace = (0.8, 2.0)
    # dashes, how long it looks away at first and at its quickest (ms), and how often it only twitches
    SIZES = {"s": (4, 800, 480, 0.0), "m": (5, 680, 420, 0.35), "big": (6, 620, 360, 0.45)}
    LOOKS = 40                                           # turns in the schedule, far more than anyone needs
    TURN = 300                                           # ms to turn away, and back
    TWITCH = 200                                         # ms a twitch takes each way (it only gets halfway)
    SEES = 0.6                                           # it sees the floor until it's this far round
    DASH = 550                                           # ms from one pillar to the next
    LONGEST = 180000

    def make(self, rng, size, story):
        n, first, last, twitchy = self.SIZES.get(size, self.SIZES["s"])
        looks, t = [], rng.randint(1300, 1900)
        for i in range(self.LOOKS):
            f = min(1.0, i / n)                          # at its quickest by about halfway along
            twitch = i > 1 and not looks[-1]["twitch"] and rng.random() < twitchy
            away = 0 if twitch else int((first + (last - first) * f) * rng.uniform(0.9, 1.1))
            looks.append({"at": t, "away": away, "twitch": twitch})
            t += (2 * self.TWITCH if twitch else 2 * self.TURN + away) + int(rng.uniform(1500, 2600) * (1 - 0.3 * f))
        return {"looks": looks, "turn": self.TURN, "twitch": self.TWITCH, "sees": self.SEES, "dash": self.DASH,
                "dashes": n, "taps": [], "seen": []}

    @staticmethod
    def _turned(p, t: float) -> float:
        """How far round the camera is at t ms: 0 looking down the corridor, 1 at the wall."""
        turn, tw = p["turn"], p["twitch"]
        for lk in p["looks"]:
            s = t - lk["at"]
            if s < 0:
                return 0.0
            if lk["twitch"]:
                if s < 2 * tw:
                    return 0.45 * (1 - abs(s - tw) / tw)
            elif s < 2 * turn + lk["away"]:
                return min(1.0, s / turn, (2 * turn + lk["away"] - s) / turn)
        return 0.0

    def _caught(self, p, t: int) -> bool:
        """Whether a dash at t ms is seen: the camera looking while you're in the open (the first and last tenth
        of a dash you're still tucked in by a pillar)."""
        d = p["dash"]
        return any(self._turned(p, t + s) < p["sees"] for s in range(int(d * 0.1), int(d * 0.9) + 1, 25))

    def _clear(self, p, t: int) -> int:
        """A time from t to dash unseen: the middle of the next look away that's long enough."""
        d, turn = p["dash"], p["turn"]
        for lk in p["looks"]:
            if lk["twitch"]:
                continue
            lo = lk["at"] + p["sees"] * turn - 0.1 * d          # the earliest and latest a dash fits in it
            hi = lk["at"] + turn + lk["away"] + (1 - p["sees"]) * turn - 0.9 * d
            mid = int((max(lo, t) + hi) / 2)
            if mid >= t and not self._caught(p, mid):
                return mid
        return t

    def public(self, p):
        return {k: p[k] for k in ("looks", "turn", "twitch", "sees", "dash", "dashes", "taps", "seen")}

    def move(self, p, body):
        t = _ms(body, "tap", self.LONGEST, "Tap DASH.")
        if p["taps"] and t == p["taps"][-1]:
            return                                       # the same dash sent twice
        if p["taps"] and t < p["taps"][-1] + p["dash"]:
            raise Refuse("You're still running.")
        p["seen"].append(self._caught(p, t))
        p["taps"].append(t)

    def done(self, p):
        return len(p["taps"]) >= p["dashes"]

    def score(self, p):
        return sum(not s for s in p["seen"]) / p["dashes"]

    def _next(self, p) -> int:
        return p["taps"][-1] + p["dash"] if p["taps"] else 0

    def solve(self, p):
        out, t = [], self._next(p)
        for _ in range(len(p["taps"]), p["dashes"]):
            t = self._clear(p, t)
            out.append({"tap": t})
            t += p["dash"]
        return out

    def bot(self, p, rng, skill):
        t = self._next(p)
        if rng.random() < skill:
            return {"tap": max(t, self._clear(p, t) + int(rng.gauss(0, 30 + 160 * (1 - skill))))}
        return {"tap": t + rng.randrange(1500)}           # goes for it


class Duck(Game):
    """The windows across the street. Now and then a guard's torch shines out of one, your way: tap HIDE, quick.
    But not for the fake-outs, a car's headlights going by, a cat's eyes, a lamp coming on. The schedule's the
    game, so the page sees it all; it sends each hide's time and the end, and it's scored here: quick hides from a
    torch score most, a torch missed scores nothing, and a hide for nothing costs half a torch. The nastiest
    fake-out is a guard's torch shone along the street rather than at you: the window lights up just the same, so
    you have to see which way the beam goes."""
    key, role, title = "duck", "lookout", "DUCK!"
    pace = (0.8, 2.0)
    # how long (ms), how many real torches and fake-outs, and how long you've got to hide from the first torch
    # and from the last (ms)
    SIZES = {"s": (16000, 5, 4, 1200, 950), "m": (22000, 6, 7, 1000, 700), "big": (30000, 8, 10, 900, 600)}
    FAKES = ("car", "cat", "lamp", "aside", "aside")     # a torch shone aside is the likeliest
    APART = 1250                                         # ms between one thing and the next at least
    QUICK = 250                                          # hidden this fast after a torch is perfect
    GAP = 350                                            # ms between hides at least (you're still down)
    WRONG = 0.5                                          # a hide for nothing costs this much of a torch
    SHOWS = {"car": 1300, "cat": 1100, "lamp": 1400, "aside": 1000}   # how long a fake-out lasts (ms)
    WINDOWS = 6                                          # the top two floors (the street hides the bottom one)

    def make(self, rng, size, story):
        length, torches, fakes, first, last = self.SIZES.get(size, self.SIZES["s"])
        kinds = ["torch"] * torches + [rng.choice(self.FAKES) for _ in range(fakes)]
        rng.shuffle(kinds)
        start, step = 1500, (length - 3000) / len(kinds)
        events, k, win = [], 0, -1
        for i, kind in enumerate(kinds):
            e = {"at": int(start + i * step + rng.uniform(0, step - self.APART)), "k": kind}
            if kind != "car":
                win = rng.choice([w for w in range(self.WINDOWS) if w != win])
                e["win"] = win
            if kind in ("car", "aside"):
                e["dir"] = rng.choice([1, -1])           # which way it goes, or the beam points
            if kind == "torch":
                e["hide"] = int(first + (last - first) * k / max(1, torches - 1))
                k += 1
            e["dur"] = e.get("hide") or self.SHOWS[kind]
            events.append(e)
        return {"len": length, "events": events, "taps": []}

    def _torches(self, p):
        return [e for e in p["events"] if e["k"] == "torch"]

    def _marks(self, p):
        """Each torch's points as it stands, and how many hides were for nothing."""
        taps, used, pts = p["taps"], set(), []
        for e in self._torches(p):
            hit = next((i for i, t in enumerate(taps) if i not in used and e["at"] <= t <= e["at"] + e["hide"]), None)
            if hit is None:
                pts.append(0.0)
                continue
            used.add(hit)
            late = taps[hit] - e["at"] - self.QUICK
            pts.append(1.0 if late <= 0 else 1 - 0.7 * late / (e["hide"] - self.QUICK))
        wrong = sum(i not in used and not any(e["at"] <= t <= e["at"] + e["hide"] for e in self._torches(p))
                    for i, t in enumerate(taps))
        return pts, wrong

    def public(self, p):
        pts, wrong = self._marks(p)
        return {"len": p["len"], "events": p["events"], "taps": p["taps"], "pts": [round(x, 2) for x in pts],
                "wrong": wrong, "over": "end" in p}

    def move(self, p, body):
        if "end" in body:
            t = _ms(body, "end", p["len"] + 600000, "That's not the end.")
            if t < p["len"] - 200:
                raise Refuse("It's not over yet.")
            p["end"] = t
            return
        t = _ms(body, "tap", p["len"], "Tap HIDE.")
        if p["taps"] and t == p["taps"][-1]:
            return                                       # the same hide sent twice
        if p["taps"] and t < p["taps"][-1] + self.GAP:
            raise Refuse("You're still down.")
        p["taps"].append(t)

    def done(self, p):
        return "end" in p

    def score(self, p):
        pts, wrong = self._marks(p)
        return max(0.0, min(1.0, (sum(pts) - self.WRONG * wrong) / len(pts)))

    def solve(self, p):
        out, last = [], p["taps"][-1] if p["taps"] else -self.GAP
        hit = {e["at"] for e, x in zip(self._torches(p), self._marks(p)[0]) if x}
        for e in self._torches(p):
            t = max(e["at"] + 150, last + self.GAP)
            if e["at"] not in hit and t <= e["at"] + e["hide"]:
                out.append({"tap": t})
                last = t
        return out + [{"end": p["len"]}]

    def bot(self, p, rng, skill):
        last = p["taps"][-1] if p["taps"] else -self.GAP
        for e in p["events"]:
            if e["at"] + 100 < last + self.GAP:
                continue                                 # been and gone
            if e["k"] == "torch":
                t = e["at"] + int(max(180, rng.gauss(330 + 600 * (1 - skill), 150)))
            elif rng.random() < 0.35 * (1 - skill):
                t = e["at"] + rng.randrange(250, 600)    # fell for it
            else:
                continue
            t = max(t, last + self.GAP)
            if t <= p["len"]:
                return {"tap": t}
        return {"end": p["len"]}


# ---- the floor ------------------------------------------------------------------------------------------------

class Sensors(Game):
    """The banking hall's floor in tiles, with motion sensors under a few: get from the bottom row to the top,
    stepping one tile at a time next to one you've stood on (corners count). Each tile you stand on says how many
    sensors are round it. The first step's always safe, as the sensors go down after it, clear of it, and laid so
    that working it out from the numbers always gets you to the top without a guess. Where they are is the
    secret (shown once it's over); the page marks the ones you think you've found on its own."""
    key, role, title = "sensors", "lookout", "SENSOR SWEEP"
    pace = (1.2, 3.0)
    SIZES = {"s": (5, 6), "m": (6, 9), "big": (7, 13)}  # the floor's side, and how many sensors
    TRIP = 0.34                                          # what each one set off costs (three and it's nothing)
    CAUGHT = 3                                           # set off this many and that's it
    SENSOR = 9                                           # a tile you stood on that had one

    def make(self, rng, size, story):
        n, k = self.SIZES.get(size, self.SIZES["s"])
        return {"n": n, "k": k, "seed": rng.randrange(1 << 30), "sensors": None, "seen": [-1] * (n * n), "trips": 0}

    @staticmethod
    def _near(n: int, i: int) -> list[int]:
        r, c = divmod(i, n)
        return [rr * n + cc for rr in (r - 1, r, r + 1) for cc in (c - 1, c, c + 1)
                if (rr, cc) != (r, c) and 0 <= rr < n and 0 <= cc < n]

    def _fair(self, n: int, sensors: set, first: int, pin: bool = True) -> bool:
        """Whether you can get from the first step to the top row without a guess, working from the numbers one
        tile at a time: a tile whose sensors are all found makes the rest round it safe, and (if pin) one with only
        as many unknowns round it as sensors left makes them all sensors."""
        stood, found = {first}, set()
        while not any(i < n for i in stood):
            more = False
            for i in list(stood):
                near = self._near(n, i)
                left = sum(j in sensors for j in near) - sum(j in found for j in near)
                unknown = [j for j in near if j not in stood and j not in found]
                if unknown and left == 0:
                    stood.update(unknown)
                    more = True
                elif pin and unknown and left == len(unknown):
                    found.update(unknown)
                    more = True
            if not more:
                return False
        return True

    def _hard(self, n: int, sensors: set, first: int) -> bool:
        """Fair, and (past the smallest floor) not just a walk round the zeros: somewhere the numbers have to pin
        a sensor down before the way on is safe."""
        return self._fair(n, sensors, first) and (n < 6 or not self._fair(n, sensors, first, pin=False))

    def _place(self, p: dict, first: int) -> list[int]:
        """The sensors, laid after the first step (the same way every time for a puzzle and a first step): clear
        of it and round it, and fair if a few hundred goes find a fair floor."""
        n, k = p["n"], p["k"]
        rng = random.Random(p["seed"] * 7919 + first)
        clear = {first, *self._near(n, first)}
        free = [i for i in range(n * n) if i not in clear]
        fallback = None
        for _ in range(400):
            s = set(rng.sample(free, k))
            if self._hard(n, s, first):
                return sorted(s)
            if fallback is None and self._route(n, s, {first}):
                fallback = sorted(s)
        return fallback or sorted(rng.sample(free, k))

    def _route(self, n: int, sensors: set, stood: set) -> list[int]:
        """The fewest steps from where you've stood to the top row, missing every sensor ([] if you're there)."""
        if any(i < n for i in stood):
            return []
        came = {i: None for i in stood}
        todo = list(stood)
        for i in todo:
            for j in self._near(n, i):
                if j not in came and j not in sensors:
                    came[j] = i
                    if j < n:
                        path = [j]
                        while came[path[-1]] not in stood:
                            path.append(came[path[-1]])
                        return path[::-1]
                    todo.append(j)
        return []

    def _stood(self, p) -> set:
        return {i for i, v in enumerate(p["seen"]) if v != -1}

    def _top(self, p) -> bool:
        return any(v not in (-1, self.SENSOR) for v in p["seen"][:p["n"]])

    def public(self, p):
        out = {"n": p["n"], "k": p["k"], "seen": p["seen"], "trips": p["trips"], "caught": self.CAUGHT}
        if self.done(p) and p["sensors"] is not None:
            out["sensors"] = p["sensors"]                # it's over: show where they were
        return out

    def move(self, p, body):
        n, seen = p["n"], p["seen"]
        i = _int(body, "at", n * n, "Tap a tile.")
        if seen[i] != -1:
            raise Refuse("You've stood there.")
        if not self._stood(p):
            if i // n != n - 1:
                raise Refuse("Start on the bottom row.")
        elif not any(seen[j] != -1 for j in self._near(n, i)):
            raise Refuse("Step next to where you've been.")
        if p["sensors"] is None:
            p["sensors"] = self._place(p, i)
        if i in p["sensors"]:
            seen[i] = self.SENSOR
            p["trips"] += 1
        else:
            seen[i] = sum(j in p["sensors"] for j in self._near(n, i))

    def done(self, p):
        return self._top(p) or p["trips"] >= self.CAUGHT

    def score(self, p):
        n = p["n"]
        if self._top(p):
            s = 1.0
        else:                                            # partway: for how far up you got
            s = 0.6 * max((n - 1 - i // n for i in self._stood(p)), default=0) / (n - 1)
        return max(0.0, min(1.0, s - self.TRIP * p["trips"]))

    def solve(self, p):
        n, stood, out = p["n"], self._stood(p), []
        if stood:
            sensors = set(p["sensors"])
        else:
            first = (n - 1) * n + n // 2
            sensors = set(self._place(p, first))
            out.append({"at": first})
            stood = {first}
        return out + [{"at": i} for i in self._route(n, sensors, stood)]

    def _read(self, p) -> tuple[set, set]:
        """What the numbers so far say, as a player would read them: tiles next to where you've stood that must be
        safe, and tiles that must be sensors (the ones set off, and any the numbers pin down)."""
        n, seen = p["n"], p["seen"]
        stood = self._stood(p)
        safe, found = set(), {i for i, v in enumerate(seen) if v == self.SENSOR}
        more = True
        while more:
            more = False
            for i in stood - found:
                near = self._near(n, i)
                left = seen[i] - sum(j in found for j in near)
                unknown = [j for j in near if j not in stood and j not in found and j not in safe]
                if unknown and left == 0:
                    safe.update(unknown)
                    more = True
                elif unknown and left == len(unknown):
                    found.update(unknown)
                    more = True
        return safe, found

    def bot(self, p, rng, skill):
        """Reads the numbers and steps on a tile they say is safe, as high up as it can, but now and then misreads
        them and steps somewhere they don't."""
        n, stood = p["n"], self._stood(p)
        if not stood:
            return {"at": (n - 1) * n + rng.randrange(n)}
        safe, found = self._read(p)
        if safe and rng.random() < 0.45 + 0.45 * skill:
            return {"at": min(safe, key=lambda i: (i // n, rng.random()))}
        edge = [i for i in range(n * n) if i not in stood and i not in found and any(j in stood for j in self._near(n, i))]
        edge = sorted([i for i in edge if i not in safe] or edge, key=lambda i: i // n)   # a guess, leaning upwards
        return {"at": edge[min(len(edge) - 1, int(rng.random() ** 2 * len(edge)))]}


GAMES: dict[str, Game] = {g.key: g for g in (Spot(), Sweep(), Count(), Moved(), Duck(), Sensors())}
