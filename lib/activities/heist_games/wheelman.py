"""The Wheelman's games beyond the first (which is in heist.py). See __init__.py for the rules a game follows."""

import math
import time
from collections import deque

from lib.activities.heist_games import Game, Refuse

LATEST = 600_000      # no timed game runs anywhere near ten minutes: a tap later than this is nonsense


def _num(x) -> bool:
    """A real number from the page (bool is an int in Python, so it's turned away)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


class Gears(Game):
    """The getaway van pulls away: the rev needle climbs and a shift in its green band is a clean one; too early
    bogs the van down, too late bangs it off the limiter (and at the limiter the van shifts itself). The schedule's
    public, as it's what you time against. Each gear climbs from the moment the last shift went in (after the
    needle's drop), and picks up speed as it goes (each gear its own way), so it's watched rather than counted.
    The page sends every shift so far, timed from the start, and the server works out each shift's place on the
    needle from the same numbers."""
    key, role, title = "gears", "wheelman", "GEAR CHANGE"
    pace = (2.0, 3.5)
    SIZES = {"s": (4, 0.065), "m": (5, 0.05), "big": (6, 0.044)}   # gears, and the first gear's half-band
    START, GAP = 2400, 700          # ms of start lights before first gear, and of the needle dropping after a shift

    def make(self, rng, size, story):
        n, half = self.SIZES.get(size, self.SIZES["s"])
        gears = []
        for i in range(n):
            # each climb a bit quicker and each band a bit narrower; the sweet spot moves so it's never a rhythm
            # (curve: the needle's at (time / climb) ** curve, so it speeds up into the band)
            gears.append({"climb": round(3300 * 0.93 ** i * rng.uniform(0.95, 1.05)), "curve": round(rng.uniform(1.25, 1.75), 2),
                          "sweet": round(rng.uniform(0.62, 0.86), 3), "half": round(half * 0.88 ** i, 4)})
        return {"gears": gears, "start": self.START, "gap": self.GAP, "taps": []}

    def _begins(self, p, taps, i):
        """When gear i starts to climb, given the shifts before it."""
        return p["start"] if i == 0 else taps[i - 1] + p["gap"]

    @staticmethod
    def _limiter(g):
        """How long the needle sits past the redline before the van shifts itself (in whole ms, as the page has it)."""
        return g["climb"] + g["climb"] // 4

    @staticmethod
    def _needle(g, ms):
        """Where the needle is, ms into a gear: 0 at its start, 1 at the redline."""
        return (max(0, ms) / g["climb"]) ** g.get("curve", 1.0)

    @staticmethod
    def _quality(g, x):
        """A shift at x (0 at the gear's start, 1 at the redline): 1 in the middle of the band, half at its edge,
        and nothing a little outside it or on the limiter."""
        if x >= 1:
            return 0.0
        e, half = abs(x - g["sweet"]), g["half"]
        core = half / 6
        if e <= core:
            return 1.0
        if e <= half:
            return 1 - 0.5 * (e - core) / (half - core)
        return max(0.0, 0.5 * (1 - (e - half) / 0.1))

    def _qualities(self, p):
        out = []
        for i, t in enumerate(p["taps"]):
            g = p["gears"][i]
            out.append(round(self._quality(g, self._needle(g, t - self._begins(p, p["taps"], i))), 3))
        return out

    def public(self, p):
        return {"gears": p["gears"], "start": p["start"], "gap": p["gap"], "taps": p["taps"], "got": self._qualities(p)}

    def move(self, p, body):
        taps = body.get("taps")
        if not isinstance(taps, list) or not taps or len(taps) > len(p["gears"]) or not all(_num(t) for t in taps):
            raise Refuse("Shift with the button.")
        taps = [round(t) for t in taps]
        if taps[:len(p["taps"])] != p["taps"]:
            raise Refuse("Those aren't the shifts you made.")
        for i in range(len(p["taps"]), len(taps)):
            begin = self._begins(p, taps, i)
            if not begin <= taps[i] <= LATEST:
                raise Refuse("Wait for the revs to climb.")
            taps[i] = min(taps[i], begin + self._limiter(p["gears"][i]))    # past the limiter it shifted itself
        p["taps"] = taps

    def done(self, p):
        return len(p["taps"]) >= len(p["gears"])

    def score(self, p):
        return sum(self._qualities(p)) / len(p["gears"])

    def solve(self, p):
        taps, out = list(p["taps"]), []
        for i in range(len(taps), len(p["gears"])):
            g = p["gears"][i]
            taps.append(self._begins(p, taps, i) + round(g["sweet"] ** (1 / g.get("curve", 1.0)) * g["climb"]))
            out.append({"taps": list(taps)})
        return out

    def bot(self, p, rng, skill):
        taps = list(p["taps"])
        i = len(taps)
        g = p["gears"][i]
        begin = self._begins(p, taps, i)
        t = begin + g["sweet"] ** (1 / g.get("curve", 1.0)) * g["climb"] + rng.gauss(0, 30 + 120 * (1 - skill))
        taps.append(round(min(begin + self._limiter(g), max(begin, t))))
        return {"taps": taps}


class Dodge(Game):
    """Three lanes of road scrolling at you, with cones, police cars and roadblocks in some of them; steer left
    and right round them. The road (when each thing reaches the van, and in which lanes) is the public schedule;
    the page sends every lane change with its time and how far it's driven, and the server drives the same road to
    count what the van hit. A thing hits if the van's in its lane at any moment it's alongside. Two-lane blocks
    come thick and fast later on, often leaving the gap on the far side from the last one, so you're across two
    lanes in a hurry."""
    key, role, title = "dodge", "wheelman", "LANE DODGE"
    pace = (4.0, 6.0)
    # ms of driving, what a hit costs, the road's speed at the start and the end (in screen-heights a ms: a thing
    # shows that many ms before it's on you), and the gap between things at the end
    SIZES = {"s": (15000, 0.15, 1 / 1500, 1 / 900, 820), "m": (22000, 0.18, 1 / 1300, 1 / 750, 560),
             "big": (30000, 0.14, 1 / 1200, 1 / 650, 500)}
    START = 2000                    # ms of lights before the road moves
    REACH = 0.085                   # how close (in screen-heights) a thing and the van have to be to touch
    LANES = 3

    def make(self, rng, size, story):
        drive, hit, v0, v1, last = self.SIZES.get(size, self.SIZES["s"])
        end = self.START + drive
        waves, t, kinds, gap = [], self.START + 1300.0, ("cone", "cone", "cop"), [1]
        while t < end - 500:
            k = (t - self.START) / drive
            if rng.random() < 0.3 + 0.35 * k:
                # two lanes shut: often the gap's on the far side from the last one
                far = max(range(self.LANES), key=lambda x: min(abs(x - g) for g in gap))
                open_ = far if rng.random() < 0.3 + 0.4 * k else rng.randrange(self.LANES)
                lanes = [x for x in range(self.LANES) if x != open_]
            else:
                lanes = [rng.randrange(self.LANES)]
            gap = [x for x in range(self.LANES) if x not in lanes]
            if len(lanes) == 2 and lanes[1] - lanes[0] == 1 and rng.random() < 0.6:
                things = [{"lane": lanes[0], "kind": "block", "span": 2}]       # a roadblock across two lanes
            else:
                things = [{"lane": x, "kind": rng.choice(kinds), "span": 1} for x in lanes]
            v = v0 + (v1 - v0) * k
            waves.append({"t": round(t), "w": round(self.REACH / v), "things": things})
            t += (1100 - (1100 - last) * k) * rng.uniform(0.9, 1.1)
        return {"start": self.START, "end": end, "v0": v0, "v1": v1, "waves": waves, "steer": [], "t": 0, "hit": hit}

    @staticmethod
    def _blocked(wave):
        return {x["lane"] + k for x in wave["things"] for k in range(x["span"])}

    def _hits(self, p, steer=None, upto=None):
        """Which waves the van hit, driving these lane changes up to this far."""
        steer = p["steer"] if steer is None else steer
        upto = p["t"] if upto is None else upto
        out = []
        for i, wv in enumerate(p["waves"]):
            if wv["t"] > upto:
                break
            a, b = wv["t"] - wv["w"], wv["t"] + wv["w"]
            lane = 1 + sum(d for t, d in steer if t <= a)
            lanes = {lane}
            for t, d in steer:
                if a < t <= b:
                    lane += d
                    lanes.add(lane)
            if lanes & self._blocked(wv):
                out.append(i)
        return out

    def public(self, p):
        return {"start": p["start"], "end": p["end"], "v0": p["v0"], "v1": p["v1"], "waves": p["waves"],
                "steer": p["steer"], "t": p["t"], "hits": self._hits(p)}

    def move(self, p, body):
        steer, t = body.get("steer"), body.get("t")
        if not isinstance(steer, list) or not _num(t):
            raise Refuse("Steer with the buttons.")
        if not all(isinstance(s, list) and len(s) == 2 and _num(s[0]) and s[1] in (-1, 1) and _int(s[1]) for s in steer):
            raise Refuse("Steer left or right.")
        steer = [[round(s[0]), s[1]] for s in steer]
        t = round(t)
        if steer[:len(p["steer"])] != p["steer"]:
            raise Refuse("That's not the way you drove.")
        if not p["t"] <= t <= p["end"]:
            raise Refuse("That's not how far you've driven.")
        lane, last = 1, 0
        for i, (at, d) in enumerate(steer):
            lane += d
            if at < last or not 0 <= lane < self.LANES or at > t or (i >= len(p["steer"]) and at < p["t"]):
                raise Refuse("You can't steer there.")
            last = at
        p["steer"], p["t"] = steer, t

    def done(self, p):
        return p["t"] >= p["end"]

    def score(self, p):
        driven = max(0.0, min(1.0, (p["t"] - p["start"]) / (p["end"] - p["start"])))
        return driven * max(0.0, 1 - p["hit"] * len(self._hits(p)))

    def _steer_round(self, p, steer, now, until, rng=None, skill=1.0):
        """Lane changes that miss everything from now until then (a so-so driver misses a few)."""
        steer = [list(s) for s in steer]
        lane = 1 + sum(d for _, d in steer)
        clear = now                                      # the last moment something was alongside
        for wv in p["waves"]:
            a, b = wv["t"] - wv["w"], wv["t"] + wv["w"]
            if wv["t"] > until:
                break
            if a <= now:                                 # already alongside: too late to do anything about it
                clear = max(clear, b)
                continue
            blocked = self._blocked(wv)
            if lane in blocked and (rng is None or rng.random() < skill):
                free = sorted((x for x in range(self.LANES) if x not in blocked), key=lambda x: abs(x - lane))
                at = round((max(clear, now) + a) / 2)
                step = 1 if free[0] > lane else -1
                while lane != free[0]:
                    lane += step
                    steer.append([at, step])
            clear = b
        return steer

    def solve(self, p):
        now = max([p["t"], *(t for t, _ in p["steer"])])
        return [{"steer": self._steer_round(p, p["steer"], now, p["end"]), "t": p["end"]}]

    def bot(self, p, rng, skill):
        now = max([p["t"], *(t for t, _ in p["steer"])])
        until = min(p["end"], now + 5000)
        return {"steer": self._steer_round(p, p["steer"], now, until, rng, min(1.0, skill + 0.1)), "t": until}


class Jammed(Game):
    """Rush Hour in the multi-storey: the parked cars only slide along their length, and the getaway van (car 0,
    across the exit's row) has to get out of the gap on the right. Made by scattering cars round a van that's at
    the exit, exploring every board those cars can slide to, and starting from one the right number of moves out
    (the fewest moves that solve it is known, as the par). A move is one car slid any distance."""
    key, role, title = "jammed", "wheelman", "JAMMED IN"
    pace = (1.5, 3.0)
    # the side, the fewest moves to get out (as low and as high as it may be), and how many cars park round the van
    SIZES = {"s": (5, 6, 7, 7), "m": (5, 8, 9, 8), "big": (6, 10, 12, 12)}
    MOST = 8_000                   # boards to explore from one layout before trying another
    TRIES = 250                    # layouts to try for one far enough out (big finds one in about a quarter second)
    BUDGET = 0.4                   # seconds to look for the full depth before settling for the deepest so far:
                                   # a job makes all its puzzles at once, and the bot mustn't stall while it does

    def make(self, rng, size, story):
        n, lo, hi, count = self.SIZES.get(size, self.SIZES["s"])
        row = (n - 1) // 2
        best = None
        stop = time.perf_counter() + self.BUDGET
        for _ in range(self.TRIES):
            if best is not None and best[1] >= lo - 2 and time.perf_counter() > stop:
                break                                    # near enough to the depth, and it's taking a while
            cars = [{"r": row, "c": n - 2, "len": 2, "h": True}]
            for _ in range(count * 8):
                if len(cars) > count:
                    break
                h, ln = rng.random() < 0.5, 3 if rng.random() < 0.25 else 2
                r, c = (rng.randrange(n), rng.randrange(n - ln + 1)) if h else (rng.randrange(n - ln + 1), rng.randrange(n))
                car = {"r": r, "c": c, "len": ln, "h": h}
                if h and r == row:
                    continue                             # a car across the van's row could never get out of its way
                if self._cells(car, n) & set().union(*(self._cells(x, n) for x in cars)):
                    continue
                cars.append(car)
            found = self._deepest(cars, n, hi)
            if found is None:
                continue
            state, far = found
            if lo <= far <= hi:
                best = (state, far, cars)
                break
            if best is None or far > best[1]:
                best = (state, far, cars)
        state, par, cars = best
        cars = [dict(x, **({"c": s} if x["h"] else {"r": s})) for x, s in zip(cars, state)]
        return {"n": n, "row": row, "cars": cars, "par": par, "left": par, "moves": 0, "limit": 2 * par + 8}

    @staticmethod
    def _cells(car, n):
        return {(car["r"], car["c"] + k) if car["h"] else (car["r"] + k, car["c"]) for k in range(car["len"])}

    @staticmethod
    def _masks(cars, n):
        """For each car, the board cells it covers at each place along its line, as bits."""
        out = []
        for x in cars:
            row = []
            for s in range(n - x["len"] + 1):
                m = 0
                for k in range(x["len"]):
                    r, c = (x["r"], s + k) if x["h"] else (s + k, x["c"])
                    m |= 1 << (r * n + c)
                row.append(m)
            out.append(row)
        return out

    @staticmethod
    def _next(state, masks):
        """Every board one move from this one, with the move: (car, where it slid to, the board)."""
        occ = 0
        for i, s in enumerate(state):
            occ |= masks[i][s]
        out = []
        for i, s in enumerate(state):
            line = masks[i]
            others = occ & ~line[s]
            q = s - 1
            while q >= 0 and not line[q] & others:
                out.append((i, q, state[:i] + (q,) + state[i + 1:]))
                q -= 1
            q = s + 1
            while q < len(line) and not line[q] & others:
                out.append((i, q, state[:i] + (q,) + state[i + 1:]))
                q += 1
        return out

    @staticmethod
    def _slides(st, masks):
        """Every board one move from this one, with the board packed into an int (three bits a car), for speed."""
        pos = [(st >> (3 * i)) & 7 for i in range(len(masks))]
        occ = 0
        for line, at in zip(masks, pos):
            occ |= line[at]
        out = []
        for i, (line, at) in enumerate(zip(masks, pos)):
            others, sh = occ & ~line[at], 3 * i
            q = at - 1
            while q >= 0 and not line[q] & others:
                out.append(st - ((at - q) << sh))
                q -= 1
            q = at + 1
            while q < len(line) and not line[q] & others:
                out.append(st + ((q - at) << sh))
                q += 1
        return out

    def _deepest(self, cars, n, hi):
        """A board as many moves from out as there are (up to hi), among every board these cars can slide to,
        with how many: or None if there are too many boards to look through."""
        masks = self._masks(cars, n)
        start = sum((x["c"] if x["h"] else x["r"]) << (3 * i) for i, x in enumerate(cars))
        seen, todo = {start}, [start]
        while todo:
            nxt = []
            for s in todo:
                for t in self._slides(s, masks):
                    if t not in seen:
                        seen.add(t)
                        nxt.append(t)
            if len(seen) > self.MOST:
                return None
            todo = nxt
        # then out from every board with the van at the exit, a move at a time
        todo = [s for s in seen if s & 7 == n - 2]
        dist, far = set(todo), 0
        while todo and far < hi:
            nxt = []
            for s in todo:
                for t in self._slides(s, masks):
                    if t not in dist:
                        dist.add(t)
                        nxt.append(t)
            if not nxt:
                break
            far, todo = far + 1, nxt
        if not far:
            return None
        st = min(todo)
        return tuple((st >> (3 * i)) & 7 for i in range(len(cars))), far

    def _state(self, p):
        return tuple(x["c"] if x["h"] else x["r"] for x in p["cars"])

    def _way_out(self, p):
        """The fewest moves out from here, as (car, to) pairs."""
        masks = self._masks(p["cars"], p["n"])
        start, out = self._state(p), p["n"] - 2
        came = {start: None}
        todo = deque([start])
        while todo:
            s = todo.popleft()
            if s[0] == out:
                path = []
                while came[s] is not None:
                    s, i, q = came[s]
                    path.append((i, q))
                return path[::-1]
            for i, q, t in self._next(s, masks):
                if t not in came:
                    came[t] = (s, i, q)
                    todo.append(t)
        return []

    def public(self, p):
        return {"n": p["n"], "row": p["row"], "cars": p["cars"], "par": p["par"], "moves": p["moves"], "limit": p["limit"]}

    def move(self, p, body):
        i, to = body.get("car"), body.get("to")
        if not _int(i) or not 0 <= i < len(p["cars"]) or not _int(to):
            raise Refuse("Slide a car.")
        car = p["cars"][i]
        at = car["c"] if car["h"] else car["r"]
        if not 0 <= to <= p["n"] - car["len"] or to == at:
            raise Refuse("It can't go there.")
        masks = self._masks(p["cars"], p["n"])
        occ = 0
        for k, s in enumerate(self._state(p)):
            if k != i:
                occ |= masks[k][s]
        step = 1 if to > at else -1
        if any(masks[i][q] & occ for q in range(at + step, to + step, step)):
            raise Refuse("Something's in the way.")
        car["c" if car["h"] else "r"] = to
        p["moves"] += 1
        p["left"] = len(self._way_out(p))

    def _out(self, p):
        return p["cars"][0]["c"] == p["n"] - 2

    def done(self, p):
        return self._out(p) or p["moves"] >= p["limit"]

    def score(self, p):
        if self._out(p):
            return max(0.5, 1 - 0.06 * max(0, p["moves"] - p["par"]))
        return 0.45 * max(0.0, 1 - p["left"] / p["par"])       # partway: how near the van is to getting out

    def solve(self, p):
        return [{"car": i, "to": q} for i, q in self._way_out(p)]

    def bot(self, p, rng, skill):
        if rng.random() < skill:
            way = self._way_out(p)
            if way:
                return {"car": way[0][0], "to": way[0][1]}
        moves = list(self._next(self._state(p), self._masks(p["cars"], p["n"])))
        i, q, _ = rng.choice(moves)
        return {"car": i, "to": q}


TURNS = "LSR"                       # left, straight on, right
STEP = ((0, -1), (1, 0), (0, 1), (-1, 0))   # north, east, south, west, on the map (y grows down it)


class Memory(Game):
    """The way out flashes up on a little street map, a turn at each junction, and then you drive it from memory.
    The route goes to the page only while it's being shown (it has to be, to be seen); once the player says go,
    the page gets nothing but the turns they've made, and the server judges them."""
    key, role, title = "memory", "wheelman", "ROUTE MEMORY"
    pace = (1.0, 2.0)
    SIZES = {"s": (5, 600), "m": (7, 520), "big": (9, 470)}   # turns, and ms the van takes over each block shown
    W, H = 5, 6                     # junctions across and down the map

    def make(self, rng, size, story):
        n, block = self.SIZES.get(size, self.SIZES["s"])
        while True:
            x, d = rng.randrange(self.W), 0
            y = self.H - 1
            seen = {(x, y)}
            start = [x, y]
            x, y = x, y - 1                              # the van drives up to the first junction
            seen.add((x, y))
            route = []
            for _ in range(n):
                options = []
                for turn in TURNS:
                    nd = (d + TURNS.index(turn) - 1) % 4
                    nx, ny = x + STEP[nd][0], y + STEP[nd][1]
                    if 0 <= nx < self.W and 0 <= ny < self.H and (nx, ny) not in seen:
                        # not three the same in a row, and straight on less often: it's a run of turns to learn
                        if len(route) >= 2 and route[-1] == route[-2] == turn:
                            continue
                        options += [(turn, nd, nx, ny)] * (1 if turn == "S" else 2)
                if not options:
                    break
                turn, d, x, y = rng.choice(options)
                seen.add((x, y))
                route.append(turn)
            if len(route) == n and not self._samey(route):
                return {"w": self.W, "h": self.H, "start": start, "route": route, "block": block, "go": False, "turns": []}

    @staticmethod
    def _samey(route):
        """A run that repeats itself (left left right right, over and over) is learnt as one thing, not nine."""
        return any(sum(route[i] == route[i - k] for i in range(k, len(route))) > 0.6 * (len(route) - k)
                   for k in (2, 3, 4))

    def public(self, p):
        out = {"w": p["w"], "h": p["h"], "start": p["start"], "n": len(p["route"]), "block": p.get("block", 620), "go": p["go"],
               "turns": p["turns"]}
        if not p["go"]:
            out["route"] = p["route"]                    # to be shown, before the drive
        elif self.done(p):
            out["route"] = p["route"]
            out["right"] = [a == b for a, b in zip(p["turns"], p["route"])]
        return out

    def move(self, p, body):
        if body.get("go") is True:
            p["go"] = True                               # (again, if a page didn't hear the first: no harm)
            return
        turns = body.get("turns")
        if not isinstance(turns, list) or not turns or len(turns) > len(p["route"]) or not all(t in ("L", "S", "R") for t in turns):
            raise Refuse("Pick a way at the junction.")
        if not p["go"]:
            raise Refuse("Watch the route first.")
        if turns[:len(p["turns"])] != p["turns"]:
            raise Refuse("That's not the way you went.")
        p["turns"] = list(turns)

    def done(self, p):
        return len(p["turns"]) >= len(p["route"])

    def score(self, p):
        route, turns = p["route"], p["turns"]
        good = 0
        while good < len(turns) and turns[good] == route[good]:
            good += 1
        after = sum(a == b for a, b in zip(turns[good + 1:], route[good + 1:]))   # back on track after a wrong turn
        return (good + 0.25 * after) / len(route)

    def solve(self, p):
        out = [] if p["go"] else [{"go": True}]
        turns = list(p["turns"])
        for t in p["route"][len(turns):]:
            turns.append(t)
            out.append({"turns": list(turns)})
        return out

    def bot(self, p, rng, skill):
        if not p["go"]:
            return {"go": True}
        right = p["route"][len(p["turns"])]
        turn = right if rng.random() < skill + 0.1 else rng.choice([t for t in TURNS if t != right])
        return {"turns": [*p["turns"], turn]}


class Hotwire(Game):
    """Under the steering column: pairs of coloured wire ends on a grid, to join up with wires that never cross and,
    between them, fill every square (a Flow puzzle). Made from one path through every square, shuffled, then cut
    into a wire for each pair, so there's always a full fill; that cover is the secret, and any wires that join
    every pair and fill the board will do. A move lays one whole wire; one laid over another cuts the other out."""
    key, role, title = "hotwire", "wheelman", "HOT-WIRE"
    pace = (3.0, 5.0)
    SIZES = {"s": (5, 4), "m": (6, 5), "big": (7, 6)}  # the grid's side, and how many pairs

    def make(self, rng, size, story):
        n, k = self.SIZES.get(size, self.SIZES["s"])
        path = self._snake(rng, n)
        while True:
            # cut it into k wires, none very short (three squares on the small board, four on the others) or much
            # more than twice its share
            least = 3 if n <= 5 else 4
            cuts = sorted(rng.sample(range(least, n * n - least + 1), k - 1))
            wires = [path[a:b] for a, b in zip([0, *cuts], [*cuts, n * n])]
            if all(least <= len(w) <= 2.2 * n * n / k and not self._near(w[0], w[-1], n) for w in wires):
                break
        return {"n": n, "ends": [[w[0], w[-1]] for w in wires], "sol": wires, "wires": [None] * k}

    @staticmethod
    def _near(a, b, n):
        return abs(a // n - b // n) + abs(a % n - b % n) == 1

    @staticmethod
    def _snake(rng, n):
        """A path through every square: back and forth across the rows to start with, shaken up by backbites (join
        an end to a square next to it and turn round the loop that makes), each of which leaves a path through
        every square."""
        path = [r * n + (c if r % 2 == 0 else n - 1 - c) for r in range(n) for c in range(n)]
        for _ in range(40 * n * n):
            if rng.random() < 0.5:
                path.reverse()
            r, c = divmod(path[-1], n)
            q = rng.choice([rr * n + cc for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)) if 0 <= rr < n and 0 <= cc < n])
            i = path.index(q)
            path[i + 1:] = path[:i:-1]
        return path

    def _filled(self, p):
        return {i for w in p["wires"] if w for i in w}

    def public(self, p):
        return {"n": p["n"], "ends": p["ends"], "wires": p["wires"]}

    def move(self, p, body):
        k, path, n = body.get("wire"), body.get("path"), p["n"]
        if not _int(k) or not 0 <= k < len(p["ends"]) or not isinstance(path, list) or len(path) < 2:
            raise Refuse("Draw a wire from one end to its pair.")
        if not all(_int(i) and 0 <= i < n * n for i in path) or len(set(path)) != len(path):
            raise Refuse("That's not a wire.")
        if sorted([path[0], path[-1]]) != sorted(p["ends"][k]):
            raise Refuse("Join two ends of the same colour.")
        if not all(self._near(a, b, n) for a, b in zip(path, path[1:])):
            raise Refuse("A wire goes square to square.")
        others = {e for j, ends in enumerate(p["ends"]) if j != k for e in ends}
        if others & set(path):
            raise Refuse("You can't run it over another wire's end.")
        cells = set(path)
        for j, w in enumerate(p["wires"]):
            if j != k and w and cells & set(w):
                p["wires"][j] = None                     # laid over: that one's cut out
        p["wires"][k] = list(path)

    def done(self, p):
        return all(p["wires"]) and len(self._filled(p)) == p["n"] ** 2

    def score(self, p):
        """Half for the pairs joined and half for filling the board, the fill counting for little until it's nearly
        full: every pair joined the short way round leaves about half the board empty, and scores about 0.6."""
        joined = sum(1 for w in p["wires"] if w) / len(p["wires"])
        return 0.5 * joined + 0.5 * (len(self._filled(p)) / p["n"] ** 2) ** 4

    def solve(self, p):
        # the cover, what's joined first and the rest last (stopping if the board's full before then)
        q, out = {**p, "wires": list(p["wires"])}, []
        for j in sorted(range(len(p["ends"])), key=lambda j: not p["wires"][j]):
            if self.done(q):
                break
            self.move(q, {"wire": j, "path": p["sol"][j]})
            out.append({"wire": j, "path": p["sol"][j]})
        return out

    def bot(self, p, rng, skill):
        """A so-so player: joins pairs the short way round (now and then the right way), then once they're all
        joined, reroutes one at a time, getting it right now and then, until the board's full."""
        loose = [j for j, w in enumerate(p["wires"]) if not w]
        j = rng.choice(loose or [j for j, w in enumerate(p["wires"]) if w != p["sol"][j]])
        if rng.random() < 0.5 * skill:
            return {"wire": j, "path": p["sol"][j]}
        n, (a, b) = p["n"], p["ends"][j]
        others = {e for i, ends in enumerate(p["ends"]) if i != j for e in ends}
        busy = {c for i, w in enumerate(p["wires"]) if w and i != j for c in w} if loose else set()
        for avoid in (busy, set()):                      # round what's laid if it can, or through it if it must
            came, todo = {a: None}, deque([a])
            while todo:
                x = todo.popleft()
                if x == b:
                    path = [b]
                    while came[path[-1]] is not None:
                        path.append(came[path[-1]])
                    return {"wire": j, "path": path[::-1]}
                r, c = divmod(x, n)
                nxt = [rr * n + cc for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)) if 0 <= rr < n and 0 <= cc < n]
                rng.shuffle(nxt)
                for y in nxt:
                    if y not in came and y not in others and y not in avoid:
                        came[y] = x
                        todo.append(y)
        return {"wire": j, "path": p["sol"][j]}


PLATE_LETTERS = "ABCDEFGHJKLMNOPRSTUVWXY"   # no I, Q or Z, as on a real plate
LOOKALIKE = {"B": "PRDE", "C": "GO", "D": "OB", "E": "FB", "F": "EP", "G": "CO", "H": "NM", "J": "LU", "K": "XR",
             "L": "JE", "M": "NW", "N": "MH", "O": "DC", "P": "RB", "R": "PK", "S": "BG", "T": "YJ", "U": "VJ",
             "V": "YU", "W": "MV", "X": "KY", "Y": "VX", "A": "RH",
             "0": "86", "1": "74", "2": "73", "3": "85", "4": "19", "5": "63", "6": "58", "7": "12", "8": "306",
             "9": "64"}


class Plate(Game):
    """Your getaway car's plate flashes up, then a car park full of near misses (a digit off, two letters swapped,
    a V for a Y), and you tap yours before the fuse burns down. A few rounds, each with closer misses than the
    last; in the closer ones every character of yours is off in one car or another, so no one character gives it
    away. Each round's plate goes to the page only while it's showing; in the car park, the page gets the plates
    but not which one's yours."""
    key, role, title = "plate", "wheelman", "NUMBER PLATE"
    pace = (1.5, 3.0)
    # rounds, plates in the car park, ms your plate shows, ms to pick yours, and how close the first round's misses
    # are (0 a bit off, 1 a single lookalike or swap; the last round's are 1)
    SIZES = {"s": (2, 6, 2400, 10000, 0.0), "m": (3, 8, 1800, 8000, 0.6), "big": (4, 10, 1500, 7000, 0.8)}

    def make(self, rng, size, story):
        rounds, count, show, fuse, first = self.SIZES.get(size, self.SIZES["s"])
        out = []
        for i in range(rounds):
            plate = self._plate(rng)
            close = first + (1 - first) * i / max(1, rounds - 1)
            slots = rng.sample([0, 1, 2, 3, 5, 6, 7], 7)
            decoys = set()
            while len(decoys) < count - 1:
                # close ones go after every character in turn
                d = self._near_miss(rng, plate, close, slots[len(decoys) % 7] if close >= 0.6 else None)
                if d != plate:
                    decoys.add(d)
            options = [plate, *sorted(decoys)]
            rng.shuffle(options)
            out.append({"plate": plate, "options": options, "answer": options.index(plate)})
        return {"rounds": out, "show": show, "fuse": fuse, "at": 0, "seen": False, "picks": []}

    @staticmethod
    def _plate(rng):
        age = rng.choice([f"{y:02d}" for y in (*range(2, 26), *range(51, 76))])
        return (rng.choice(PLATE_LETTERS) + rng.choice(PLATE_LETTERS) + age + " "
                + "".join(rng.choice(PLATE_LETTERS) for _ in range(3)))

    @staticmethod
    def _near_miss(rng, plate, close, slot=None):
        """A plate that's nearly this one: further off in early rounds, a lookalike or a swap by the last (at this
        character, if it's given)."""
        s = list(plate)
        slots = [i for i in range(len(s)) if i != 4]
        changes = 1 if rng.random() < 0.35 + 0.65 * close else 2
        for _ in range(changes):
            how = rng.random()
            i = rng.choice(slots) if slot is None else slot
            if slot is not None and how < 0.3:           # a swap, with the one next to it in its group
                b = i + 1 if i in (0, 2, 5, 6) and (i != 6 or rng.random() < 0.5) else i - 1
                if b in slots and s[i] != s[b]:
                    s[i], s[b] = s[b], s[i]
                    continue
            if how < 0.3:                                # two next to each other swapped (in the same group)
                pairs = [(a, a + 1) for a in (0, 2, 5, 6) if s[a] != s[a + 1]]
                if pairs:
                    a, b = rng.choice(pairs)
                    s[a], s[b] = s[b], s[a]
                    continue
            if how < 0.3 + 0.5 * close or how < 0.55:    # a lookalike
                s[i] = rng.choice(LOOKALIKE.get(s[i], PLATE_LETTERS))
            elif s[i].isdigit():
                s[i] = rng.choice("0123456789")
            else:
                s[i] = rng.choice(PLATE_LETTERS)
        return "".join(s)

    def public(self, p):
        out = {"count": len(p["rounds"]), "at": p["at"], "seen": p["seen"], "size": len(p["rounds"][0]["options"]),
               "show": p.get("show", 2400), "fuse": p.get("fuse", 10000),
               "got": [pick == r["answer"] for pick, r in zip(p["picks"], p["rounds"])]}
        if p["picks"]:                                   # the round just gone, to show how it went
            r = p["rounds"][len(p["picks"]) - 1]
            out["last"] = {"options": r["options"], "answer": r["answer"], "pick": p["picks"][-1], "plate": r["plate"]}
        if not self.done(p):
            r = p["rounds"][p["at"]]
            if p["seen"]:
                out["options"] = r["options"]
            else:
                out["plate"] = r["plate"]
        return out

    def move(self, p, body):
        if body.get("seen") is True:
            p["seen"] = True                             # (again, if a page didn't hear the first: no harm)
            return
        pick = -1 if body.get("miss") is True else body.get("pick")    # (miss: the fuse burnt down)
        if not _int(pick):
            raise Refuse("Tap your car.")
        if not p["seen"]:
            raise Refuse("Look at your plate first.")
        if not -1 <= pick < len(p["rounds"][p["at"]]["options"]) or (pick == -1 and body.get("miss") is not True):
            raise Refuse("Tap one of the cars.")
        p["picks"].append(pick)
        p["at"] += 1
        p["seen"] = False

    def done(self, p):
        return p["at"] >= len(p["rounds"])

    def score(self, p):
        return sum(pick == r["answer"] for pick, r in zip(p["picks"], p["rounds"])) / len(p["rounds"])

    def solve(self, p):
        out = []
        for i in range(p["at"], len(p["rounds"])):
            if i > p["at"] or not p["seen"]:
                out.append({"seen": True})
            out.append({"pick": p["rounds"][i]["answer"]})
        return out

    def bot(self, p, rng, skill):
        if not p["seen"]:
            return {"seen": True}
        r = p["rounds"][p["at"]]
        if rng.random() < skill + 0.1:
            return {"pick": r["answer"]}
        return {"pick": rng.choice([i for i in range(len(r["options"])) if i != r["answer"]])}


GAMES: dict[str, Game] = {g.key: g for g in (Gears(), Dodge(), Jammed(), Memory(), Hotwire(), Plate())}
