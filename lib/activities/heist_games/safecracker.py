"""The Safecracker's games beyond the first (which is in heist.py). See __init__.py for the rules a game follows."""

import copy
import itertools
import math
import random

from lib.activities.heist_games import Game, Refuse


def _whole(v, lo: int, hi: int, msg: str) -> int:
    """A whole number from lo to hi, or Refuse (a bool counts as a number in Python, so it's turned away first)."""
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise Refuse(msg)
    return v


def _settings(v, k: int, lo: int, hi: int, msg: str) -> list[int]:
    """A setting for each of k things (pins, levers), each a whole number from lo to hi, or Refuse."""
    if not isinstance(v, list) or len(v) != k:
        raise Refuse(msg)
    return [_whole(x, lo, hi, msg) for x in v]


def _over(used: int, par: int, per: float, floor: float) -> float:
    """A clean job's score: 1 up to par, then less for each one over, never below floor."""
    return max(floor, 1 - per * max(0, used - par))


class SweetSpot(Game):
    """The door's sweet spot: tap anywhere on it to listen, and each tap says how far off it is: hot is the ring of
    squares one off, warm two, cool anywhere three or four off, and cold further, so a few rings cross on it. The
    spot is the secret."""
    key, role, title = "sweet", "safecracker", "SWEET SPOT"
    pace = (1.5, 3.0)
    SIZES = {"s": (6, 7, 3), "m": (7, 8, 3), "big": (8, 7, 3)}    # the door's side in squares, taps, and par
    RINGS = (0, 1, 2, 3, 5)     # how far off each thing heard starts (click, hot, warm, cool, cold); it runs to the next
    COLD = len(RINGS) - 1

    def make(self, rng, size, story):
        n, limit, par = self.SIZES.get(size, self.SIZES["s"])
        return {"n": n, "spot": rng.randrange(n * n), "taps": [], "limit": limit, "par": par}

    def _far(self, n, a, b):
        """What a tap at a hears of the spot at b: how many rings off it is (a square's eight neighbours are one
        off), as click, hot, warm, cool or cold."""
        (ar, ac), (br, bc) = divmod(a, n), divmod(b, n)
        d = max(abs(ar - br), abs(ac - bc))
        return max(i for i, r in enumerate(self.RINGS) if d >= r)

    def public(self, p):
        return {"n": p["n"], "limit": p["limit"], "rings": list(self.RINGS),
                "taps": [{"at": t, "far": self._far(p["n"], t, p["spot"])} for t in p["taps"]]}

    def move(self, p, body):
        p["taps"].append(_whole(body.get("at"), 0, p["n"] ** 2 - 1, "Tap the door."))

    def _found(self, p):
        return p["spot"] in p["taps"]

    def done(self, p):
        return self._found(p) or len(p["taps"]) >= p["limit"]

    def score(self, p):
        if self._found(p):
            return _over(p["taps"].index(p["spot"]) + 1, p["par"], 0.12, 0.5)
        best = min((self._far(p["n"], t, p["spot"]) for t in p["taps"]), default=self.COLD)
        return 0.4 * (self.COLD - best) / (self.COLD - 1)           # partway: how near the best tap got

    def solve(self, p):
        return [{"at": p["spot"]}]

    def bot(self, p, rng, skill):
        n, said = p["n"], [(t, self._far(p["n"], t, p["spot"])) for t in p["taps"]]
        fits = [i for i in range(n * n) if all(self._far(n, t, i) == f for t, f in said)]
        return {"at": rng.choice(fits) if fits and rng.random() < skill else rng.randrange(n * n)}


class Pins(Game):
    """A pin lock: push each pin to a height from 1 to 9 and TEST, and the lock says only how many pins are set and
    how many are pushed too high, never which, so it's worked out from the counts across tests. The heights are
    the secret."""
    key, role, title = "pins", "safecracker", "PIN HEIGHTS"
    pace = (3.0, 6.0)
    SIZES = {"s": (3, 6, 5), "m": (4, 7, 6), "big": (5, 8, 7)}    # pins, tests, and par (a careful deducer's)
    TOP = 9                     # heights run 1 to TOP

    def make(self, rng, size, story):
        k, limit, par = self.SIZES.get(size, self.SIZES["s"])
        return {"h": [rng.randint(1, self.TOP) for _ in range(k)], "tests": [], "limit": limit, "par": par}

    @staticmethod
    def _said(h, s):
        """What the lock says of heights s against the right ones h: how many are set, and how many too high."""
        return sum(x == y for x, y in zip(s, h)), sum(x > y for x, y in zip(s, h))

    def public(self, p):
        return {"k": len(p["h"]), "top": self.TOP, "limit": p["limit"],
                "tests": [dict(zip(("pins", "set", "high"), (s, *self._said(p["h"], s)))) for s in p["tests"]]}

    def move(self, p, body):
        p["tests"].append(_settings(body.get("set"), len(p["h"]), 1, self.TOP, f"Set every pin, 1 to {self.TOP}."))

    def _open(self, p):
        return p["h"] in p["tests"]

    def done(self, p):
        return self._open(p) or len(p["tests"]) >= p["limit"]

    def score(self, p):
        if self._open(p):
            return _over(p["tests"].index(p["h"]) + 1, p["par"], 0.08, 0.65)
        best = max((self._said(p["h"], s)[0] for s in p["tests"]), default=0)
        return 0.6 * best / len(p["h"])                             # partway: the most pins set in one test

    def solve(self, p):
        return [{"set": list(p["h"])}]

    def bot(self, p, rng, skill):
        """A so-so player: often heights that fit everything the lock's said so far (found by nudging one pin at a
        time towards fitting, so not always), otherwise the last test with a pin or two moved."""
        k, top = len(p["h"]), self.TOP
        said = [(s, self._said(p["h"], s)) for s in p["tests"]]
        last = p["tests"][-1] if p["tests"] else [(top + 1) // 2] * k
        if said and rng.random() < skill:
            def off(c):
                return sum(abs(a - x) + abs(b - y) for s, (x, y) in said for a, b in [self._said(c, s)])
            for _ in range(4):
                c = [rng.randint(1, top) for _ in range(k)]
                for _ in range(12 * k):
                    if not off(c):
                        return {"set": c}
                    i = rng.randrange(k)
                    c[i] = min(range(1, top + 1), key=lambda v: (off(c[:i] + [v] + c[i + 1:]), rng.random()))
        c = list(last)
        for i in rng.sample(range(k), rng.randint(1, min(2, k))):
            c[i] = rng.randint(1, top)
        return {"set": c}


class Levers(Game):
    """A bank of levers, each UP, MIDDLE or DOWN: PULL and the lock says only how many are right, not which, so it's
    worked out by moving one at a time and watching the count. The right settings are the secret."""
    key, role, title = "levers", "safecracker", "LEVERS"
    pace = (2.0, 4.0)
    SIZES = {"s": (4, 6), "m": (5, 6), "big": (6, 7)}            # levers, and pulls allowed

    def make(self, rng, size, story):
        k, limit = self.SIZES.get(size, self.SIZES["s"])
        return {"want": [rng.randrange(3) for _ in range(k)], "pulls": [], "limit": limit}

    def _right(self, p, s):
        return sum(a == b for a, b in zip(s, p["want"]))

    def public(self, p):
        return {"k": len(p["want"]), "limit": p["limit"],
                "pulls": [{"set": s, "right": self._right(p, s)} for s in p["pulls"]]}

    def move(self, p, body):
        p["pulls"].append(_settings(body.get("set"), len(p["want"]), 0, 2, "Set every lever."))

    def _open(self, p):
        return p["want"] in p["pulls"]

    def done(self, p):
        return self._open(p) or len(p["pulls"]) >= p["limit"]

    def score(self, p):
        k = len(p["want"])
        if self._open(p):
            return _over(p["pulls"].index(p["want"]) + 1, k, 0.1, 0.6)   # one pull a lever is a clean job
        return 0.5 * max((self._right(p, s) for s in p["pulls"]), default=0) / k

    def solve(self, p):
        return [{"set": list(p["want"])}]

    def bot(self, p, rng, skill):
        """A so-so player moves a lever or so from the last pull, to one that fits what the lamps have said if it
        can see one; otherwise any lever anywhere."""
        k, said = len(p["want"]), [(s, self._right(p, s)) for s in p["pulls"]]
        last = p["pulls"][-1] if p["pulls"] else [1] * k
        if rng.random() < skill:
            fits = [list(s) for s in itertools.product(range(3), repeat=k)
                    if all(sum(a == b for a, b in zip(s, t)) == n for t, n in said)]
            near = [s for s in fits if sum(a != b for a, b in zip(s, last)) <= 1]
            if near or fits:
                return {"set": rng.choice(near or fits)}
        c = list(last)
        c[rng.randrange(k)] = rng.randrange(3)
        return {"set": c}


class Order(Game):
    """Buttons on the safe that go in a hidden order: a right press stays lit, a wrong one clunks, they all go out
    and the keypad scrambles, so the buttons have to be remembered by their look, along with which ones have
    clunked where. It's scored on presses a player who remembered everything wouldn't have made, so luck doesn't
    come into it. The order is the secret; where the buttons sit isn't."""
    key, role, title = "order", "safecracker", "BUTTON ORDER"
    pace = (0.7, 1.5)
    SIZES = {"s": 4, "m": 5, "big": 6}
    WASTE = 0.15        # what each wasted press costs

    def make(self, rng, size, story):
        n = self.SIZES.get(size, self.SIZES["s"])
        # the most a player who never forgets can need: a wrong guess with j lit costs it and the j to light again
        worst = sum(1 + (n - j - 1) * (j + 1) for j in range(n))
        return {"order": rng.sample(range(n), n), "lit": 0, "best": 0, "log": [], "layout": list(range(n)),
                "seed": rng.randrange(1 << 30), "limit": worst + 4}

    def public(self, p):
        last = p["log"][-1] if p["log"] else None
        return {"n": len(p["order"]), "lit": p["order"][:p["lit"]], "presses": len(p["log"]), "limit": p["limit"],
                "layout": p["layout"], "wrong": last[0] if last and not last[1] else None,
                "clunks": sum(not ok for _, ok in p["log"])}

    def move(self, p, body):
        at = _whole(body.get("at"), 0, len(p["order"]) - 1, "Press a button.")
        if at in p["order"][:p["lit"]]:
            raise Refuse("That one's already lit.")
        ok = at == p["order"][p["lit"]]
        p["lit"] = p["lit"] + 1 if ok else 0
        p["best"] = max(p["best"], p["lit"])
        p["log"].append([at, ok])
        if not ok:                                                  # the keypad scrambles, never into the same places
            was, shuffle = list(p["layout"]), random.Random(f"{p['seed']}:{len(p['log'])}")
            while p["layout"] == was:
                shuffle.shuffle(p["layout"])

    def done(self, p):
        return p["lit"] >= len(p["order"]) or len(p["log"]) >= p["limit"]

    def _wasted(self, p):
        """Presses a player who remembered everything wouldn't have made: a wrong one where the right one was
        already known (it had got further before), or one that had already clunked at that point."""
        lit, best, tried, waste = 0, 0, {}, 0
        for at, ok in p["log"]:
            if ok:
                lit += 1
                best = max(best, lit)
                continue
            seen = tried.setdefault(lit, set())
            waste += lit < best or at in seen
            seen.add(at)
            lit = 0
        return waste

    def score(self, p):
        n = len(p["order"])
        if p["lit"] >= n:
            return max(0.5, 1 - self.WASTE * self._wasted(p))
        return 0.5 * p["best"] / n                                  # partway: the furthest it got

    def solve(self, p):
        return [{"at": b} for b in p["order"][p["lit"]:]]

    def bot(self, p, rng, skill):
        lit = p["lit"]
        if lit < p["best"] and rng.random() < skill ** 0.7:         # mostly remembers the ones it's found
            return {"at": p["order"][lit]}
        wrong, j = set(), 0                                         # what's clunked at this point before
        for at, ok in p["log"]:
            if not ok and j == lit:
                wrong.add(at)
            j = j + 1 if ok else 0
        free = [b for b in range(len(p["order"])) if b not in p["order"][:lit]]
        fresh = [b for b in free if b not in wrong and b not in p["order"][lit + 1:p["best"]]]
        return {"at": rng.choice(fresh if fresh and rng.random() < skill ** 0.7 else free)}


class Needle(Game):
    """A needle sweeps round the dial, and a tap while it's in the green notch sets a tumbler; then the notch moves,
    the needle turns back the other way and speeds up. The schedule's all on the page (it has to be, to time
    against); the page sends each tap's time and the needle's worked out here. A miss costs a little, and a few
    on one tumbler let the last one set slip back."""
    key, role, title = "needle", "safecracker", "NEEDLE"
    pace = (1.0, 2.5)
    # tumblers, the first sweep's speed and how much faster each after (degrees a second), the notch's half-width
    # and how much narrower each after (degrees), and how long the needle runs (ms)
    SIZES = {"s": (3, 165, 45, 14, 1.5, 45000), "m": (4, 170, 42, 13, 1.2, 55000), "big": (5, 175, 38, 13, 1.0, 65000)}
    SLIP = 3            # misses on one tumbler before the last one set slips back
    SPARE = 8           # taps allowed beyond one a tumbler
    DEAD = 0.15         # a tap this near the notch's middle (of its half-width) is spot on
    EDGE = 0.6          # what a tap right on the notch's edge is worth
    GRACE = 4.0         # degrees past the notch's edge that still count (as an edge tap): the needle's blade is
                        # that wide where it crosses the notch, so any red over the green is in
    MISS = 0.06         # what each miss takes off

    def make(self, rng, size, story):
        n, v0, dv, w0, dw, length = self.SIZES.get(size, self.SIZES["s"])
        way, at, notches = rng.choice((1, -1)), rng.uniform(0, 360), []
        for _ in range(n):
            notches.append(round(at % 360, 1))
            at += rng.uniform(110, 250)                             # each notch well round from the last
        return {"speeds": [round((v0 + dv * i) * rng.uniform(0.96, 1.04), 1) for i in range(n)],
                "ways": [way * (-1) ** i for i in range(n)], "notches": notches,
                "widths": [round(w0 - dw * i, 1) for i in range(n)], "len": length,
                # the needle starts across the dial from the first notch, so the first pass gives a moment to see it
                "seg": {"t": 0, "a": round((notches[0] + 180 + rng.uniform(-50, 50)) % 360, 1)},
                "set": [], "miss": 0, "misses": 0, "taps": [], "limit": n + self.SPARE, "stopped": False}

    def _angle(self, p, t):
        """Where the needle is at t ms: it sweeps from the current run's start at the current tumbler's speed."""
        k, seg = len(p["set"]), p["seg"]
        return (seg["a"] + p["ways"][k] * p["speeds"][k] * (t - seg["t"]) / 1000) % 360

    def _off(self, p, t):
        """How far the needle is from the notch's middle at t, in degrees."""
        d = abs(self._angle(p, t) - p["notches"][len(p["set"])]) % 360
        return min(d, 360 - d)

    def public(self, p):
        return {"n": len(p["notches"]), "speeds": p["speeds"], "ways": p["ways"], "notches": p["notches"],
                "widths": p["widths"], "grace": self.GRACE, "len": p["len"], "seg": p["seg"], "set": len(p["set"]), "miss": p["miss"],
                "slip": self.SLIP, "misses": p["misses"], "taps": len(p["taps"]), "limit": p["limit"],
                "last": p["taps"][-1] if p["taps"] else None}

    def move(self, p, body):
        if "stop" in body:                                          # the needle's run out (only ever the player's loss)
            if not self._time(body.get("stop")) or body["stop"] < p["len"]:
                raise Refuse("It's still going.")
            p["stopped"] = True
            return
        t = body.get("tap")
        if not self._time(t) or t > p["len"] or t < max(p["taps"][-1] if p["taps"] else 0, p["seg"]["t"]):
            raise Refuse("That tap's out of time.")
        off, w, a = self._off(p, t), p["widths"][len(p["set"])], round(self._angle(p, t), 3)
        p["taps"].append(t)
        if off <= w + self.GRACE:
            worth = 1 - (1 - self.EDGE) * (min(off, w) / w - self.DEAD) / (1 - self.DEAD)
            p["set"].append(1.0 if off <= self.DEAD * w else round(worth, 4))
            p["miss"], p["seg"] = 0, {"t": t, "a": a}
            return
        p["miss"] += 1
        p["misses"] += 1
        if p["miss"] >= self.SLIP:                                  # the last tumbler set slips back
            p["miss"] = 0
            if p["set"]:
                p["set"].pop()
            p["seg"] = {"t": t, "a": a}

    @staticmethod
    def _time(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0

    def done(self, p):
        return len(p["set"]) >= len(p["notches"]) or len(p["taps"]) >= p["limit"] or p["stopped"]

    def score(self, p):
        return sum(p["set"]) / len(p["notches"]) * max(0.5, 1 - self.MISS * p["misses"])

    def _next(self, p, after):
        """The first time after `after` that the needle's dead on the notch."""
        k, seg = len(p["set"]), p["seg"]
        v, way = p["speeds"][k], p["ways"][k]
        to_go = ((p["notches"][k] - seg["a"]) * way) % 360              # degrees still to sweep to the notch
        t = seg["t"] + to_go * 1000 / v
        lap = 360 * 1000 / v
        if t < after:
            t += math.ceil((after - t) / lap) * lap
        return t

    def solve(self, p):
        p, out = copy.deepcopy(p), []
        while not self.done(p):
            t = self._next(p, max(p["taps"][-1] if p["taps"] else 0, p["seg"]["t"]) + 300)
            out.append({"tap": t})
            self.move(p, {"tap": t})
        return out

    def bot(self, p, rng, skill):
        last = max(p["taps"][-1] if p["taps"] else 0, p["seg"]["t"])
        t = self._next(p, last + rng.uniform(300, 1200)) + rng.gauss(0, 20 + 70 * (1 - skill))
        return {"tap": min(p["len"], max(last, t))}


class Rings(Game):
    """The bolt's held by rings, one inside the next, each with a gap: tap a ring to turn it a notch, and a ring with
    a bar to its neighbour turns that one a notch too. Line every gap up at the top and the bolt slides out. It's
    made by turning a solved lock backwards, so the fewest turns are known; that count is the secret."""
    key, role, title = "rings", "safecracker", "RING ALIGN"
    pace = (1.0, 2.2)
    NOTCHES = 8
    SIZES = {"s": (3, (1, 2), (4, 6)), "m": (4, (3, 3), (7, 10)), "big": (5, (4, 4), (9, 13))}   # rings, bars, turns

    def make(self, rng, size, story):
        r, bars, turns = self.SIZES.get(size, self.SIZES["s"])
        n = self.NOTCHES
        for _ in range(500):
            link = [None] * r
            for i in rng.sample(range(r - 1), rng.randint(*bars)):   # a bar between rings i and i + 1, one way
                a, b = (i, i + 1) if rng.random() < 0.5 else (i + 1, i)
                if link[a] is None:
                    link[a] = b
            if sum(x is not None for x in link) < bars[0]:
                continue
            want = rng.randint(*turns)
            x = [0] * r
            for _ in range(want):                                   # the turns that solve it, at most 3 a ring
                i = rng.choice([j for j in range(r) if x[j] < 3])
                x[i] += 1
            pos = [0] * r
            for i, c in enumerate(x):                               # turn a solved lock backwards
                pos[i] -= c
                if link[i] is not None:
                    pos[link[i]] -= c
            pos = [q % n for q in pos]
            if all(pos):                                            # every ring starts visibly out
                break
        par = sum(x)
        return {"pos": pos, "start": list(pos), "turned": [0] * r, "link": link, "turns": 0, "par": par,
                "limit": par + n + 2}                               # room to waste a lap, once

    def public(self, p):
        return {"n": self.NOTCHES, "pos": p["pos"], "start": p["start"], "turned": p["turned"], "link": p["link"],
                "turns": p["turns"], "limit": p["limit"]}

    def move(self, p, body):
        i = _whole(body.get("ring"), 0, len(p["pos"]) - 1, "Tap a ring.")
        for j in (i, p["link"][i]):
            if j is not None:
                p["pos"][j] = (p["pos"][j] + 1) % self.NOTCHES
                p["turned"][j] += 1
        p["turns"] += 1

    def done(self, p):
        return not any(p["pos"]) or p["turns"] >= p["limit"]

    def score(self, p):
        if not any(p["pos"]):
            return _over(p["turns"], p["par"], 0.3 / self.NOTCHES, 0.6)   # a lap wasted costs about 0.3
        return 0.5 * sum(q == 0 for q in p["pos"]) / len(p["pos"])

    def _need(self, p):
        """The turns each ring still needs: a ring's own, less what the rings with bars to it give it, worked out
        from the ones nothing turns."""
        r, n = len(p["pos"]), self.NOTCHES
        need: list[int | None] = [None] * r
        while None in need:
            for i in range(r):
                drivers = [j for j in range(r) if p["link"][j] == i]
                if need[i] is None and all(need[j] is not None for j in drivers):
                    need[i] = (-p["pos"][i] - sum(need[j] for j in drivers)) % n
        return need

    def solve(self, p):
        return [{"ring": i} for i, c in enumerate(self._need(p)) for _ in range(c)]

    def bot(self, p, rng, skill):
        need = self._need(p)
        if rng.random() < skill ** 0.4:                             # a slip here costs a lap, so it's rarer
            return {"ring": next(i for i, c in enumerate(need) if c)}
        return {"ring": rng.randrange(len(p["pos"]))}


GAMES: dict[str, Game] = {g.key: g for g in (SweetSpot(), Pins(), Levers(), Order(), Needle(), Rings())}
