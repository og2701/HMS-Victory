"""The Hacker's games beyond the first (which is in heist.py). See __init__.py for the rules a game follows."""

import heapq
import itertools
import math
import string

from lib.activities.heist_games import Game, Refuse


def _num(body: dict, name: str, below: int, msg: str) -> int:
    """A whole number 0 to below-1 from a move, or Refuse (True and False are ints in Python, so they're turned
    away by hand)."""
    v = body.get(name)
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < below:
        raise Refuse(msg)
    return v


class Lights(Game):
    """The alarm panel's lights: tapping one flips it and the ones next to it, and the alarm's off when they all are.
    It's made by pressing a few buttons on a dark panel, so it can always be put out; what's pressed is the secret.
    Some panels can be put out more than one way (pressing a "quiet" pattern changes nothing), so the presses it's
    made from are only kept if no shorter way puts it out: that's par, and a few taps over it lock the panel. The
    small panel is strict; the bigger ones allow a few more taps, charge less for each, and give more for a panel
    left part dark."""
    key, role, title = "lights", "hacker", "LIGHTS OUT"
    pace = (1.2, 2.5)
    # the panel's side, how many presses made it, taps over par before it locks, what each tap over par costs once
    # it's out, and the share of credit for how dark it is if it isn't
    SIZES = {"s": (4, 4, 3, 0.1, 0.5), "m": (5, 6, 5, 0.05, 0.6), "big": (5, 8, 6, 0.05, 0.6)}
    _QUIET: dict[int, list[int]] = {}

    def make(self, rng, size, story):
        n, k, spare, cost, part = self.SIZES.get(size, self.SIZES["s"])
        while True:
            sol = rng.sample(range(n * n), k)
            if bin(self._fewest(n, sum(1 << i for i in sol))).count("1") == k:
                break
        on = [0] * (n * n)
        for i in sol:
            self._press(on, n, i)
        return {"n": n, "on": on, "sol": sorted(sol), "made": [], "par": k, "limit": k + spare, "cost": cost, "part": part}

    @staticmethod
    def _press(on, n, i):
        r, c = divmod(i, n)
        for rr, cc in ((r, c), (r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
            if 0 <= rr < n and 0 <= cc < n:
                on[rr * n + cc] ^= 1

    @classmethod
    def _quiet(cls, n):
        """Every press pattern that leaves an n x n panel as it was (as bit masks), found by solving for them over
        GF(2): 16 of them on a 4x4 panel, 4 on a 5x5, just pressing nothing on a 3x3."""
        if n not in cls._QUIET:
            size = n * n
            rows = []
            for i in range(size):                         # row i: the presses that flip light i
                r, c = divmod(i, n)
                rows.append(sum(1 << (rr * n + cc) for rr, cc in ((r, c), (r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1))
                                if 0 <= rr < n and 0 <= cc < n))
            pivots, at = [], 0
            for col in range(size):
                pick = next((i for i in range(at, size) if rows[i] >> col & 1), None)
                if pick is None:
                    continue
                rows[at], rows[pick] = rows[pick], rows[at]
                for i in range(size):
                    if i != at and rows[i] >> col & 1:
                        rows[i] ^= rows[at]
                pivots.append(col)
                at += 1
            basis = []
            for free in (c for c in range(size) if c not in pivots):
                basis.append((1 << free) | sum(1 << pc for i, pc in enumerate(pivots) if rows[i] >> free & 1))
            quiet = [0]
            for b in basis:
                quiet += [q ^ b for q in quiet]
            cls._QUIET[n] = quiet
        return cls._QUIET[n]

    def _fewest(self, n, presses):
        """The fewest presses doing the same as these ones (as a bit mask)."""
        return min((presses ^ q for q in self._quiet(n)), key=lambda m: bin(m).count("1"))

    def public(self, p):
        return {"n": p["n"], "on": p["on"], "moves": len(p["made"]), "limit": p["limit"]}

    def move(self, p, body):
        at = body.get("at")
        if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at < p["n"] ** 2:
            raise Refuse("Tap a light.")
        self._press(p["on"], p["n"], at)
        p["made"].append(at)

    def done(self, p):
        return not any(p["on"]) or len(p["made"]) >= p["limit"]

    def score(self, p):
        if not any(p["on"]):
            return max(0.6, 1 - p.get("cost", 0.1) * max(0, len(p["made"]) - p["par"]))
        return p.get("part", 0.5) * (1 - sum(p["on"]) / len(p["on"]))       # partway: for how dark it is

    def _left(self, p):
        """The fewest presses that still put it out: the ones that made it, less any pressed an odd number of times
        since, then the shortest way of doing the same."""
        need = sum(1 << i for i in p["sol"])
        for i in p["made"]:
            need ^= 1 << i
        need = self._fewest(p["n"], need)
        return [i for i in range(p["n"] ** 2) if need >> i & 1]

    def solve(self, p):
        return [{"at": i} for i in self._left(p)]

    def bot(self, p, rng, skill):
        left = self._left(p)
        if left and rng.random() < skill:
            return {"at": rng.choice(left)}
        return {"at": rng.randrange(p["n"] ** 2)}


class Wire(Game):
    """A junction box: terminals down the left, coloured lamps down the right, and a tangle of wires between. Cut
    the one that runs to the lamp asked for, a few times over. The wires come in only two colours, so the colour
    narrows it down and following the wire through its crossings does the rest. Each wire weaves through a few
    columns of slots on its way across, which is what tangles them. The page gets the drawing only (each wire's
    colour and the points its curve passes through): which terminal reaches which lamp is the secret.

    Each round burns a fuse: a cut after it's gone out (or none, when the page says it went out) loses the round.
    The page times it, sending each cut with its time in ms since the puzzle showed; a round's fuse is lit when
    the cut before it was made (if the page's clock went back to 0, say on a reload, from 0)."""
    key, role, title = "wire", "hacker", "TRACE THE WIRE"
    pace = (2.5, 5.0)
    # rounds, how many wires of each of the two colours (the wire to cut is in the first lot), the columns each wire
    # weaves through on the way across, and each round's fuse in ms (the first gets FIRST more, to take it in)
    SIZES = {"s": (2, (2, 2), 2, 9000), "m": (4, (3, 2), 3, 7000), "big": (4, (3, 3), 4, 7000)}
    FIRST = 2000
    LATE = 400                                                    # leeway for the answer's trip back
    LAMPS = ["red", "blue", "green", "yellow", "pink", "white"]
    WIRES = ["red", "blue", "yellow", "green"]

    def make(self, rng, size, story):
        rounds, lots, cols, fuse = self.SIZES.get(size, self.SIZES["s"])
        return {"n": sum(lots), "rounds": [self._round(rng, lots, cols) for _ in range(rounds)], "got": [], "fuse": fuse,
                "t0": 0}

    def _fuse(self, p):
        return p["fuse"] + (self.FIRST if not p["got"] else 0)

    def _round(self, rng, lots, cols):
        n = sum(lots)
        while True:
            # lanes[k][i]: the slot wire i is in at column k, from its terminal (k = 0, wire i starts at slot i)
            # to its lamp (the last column); no wire jumps more than two slots between columns, or it runs so
            # steep that it can't be followed
            lanes = [list(range(n))]
            while len(lanes) < cols + 2:
                q = rng.sample(range(n), n)
                if all(abs(a - b) <= 2 for a, b in zip(q, lanes[-1])):
                    lanes.append(q)
            colour = [0] * lots[0] + [1] * lots[1]
            rng.shuffle(colour)
            cut = rng.choice([i for i in range(n) if colour[i] == 0])
            crosses = lambda i, j: sum((lanes[k][i] - lanes[k][j]) * (lanes[k + 1][i] - lanes[k + 1][j]) < 0  # noqa: E731
                                       for k in range(cols + 1))
            bends = sum(lanes[k][cut] != lanes[k + 1][cut] for k in range(cols + 1))
            twins = sum(crosses(cut, j) for j in range(n) if j != cut and colour[j] == 0)
            # the wire to cut wanders, and crosses one of its own colour, so the colour alone won't do
            if bends >= 2 and twins >= 1:
                break
        pair = rng.sample(self.WIRES, 2)
        gap = 100 / n
        wires = []
        for i in range(n):
            pts = []
            for k in range(cols + 2):
                y = (lanes[k][i] + 0.5) * gap
                if 0 < k < cols + 1:
                    y += rng.uniform(-0.16, 0.16) * gap
                pts.append([round(100 * k / (cols + 1), 1), round(y, 1)])
            wires.append({"c": pair[colour[i]], "pts": pts})
        rng.shuffle(wires)                                        # drawn in any order, so the list says nothing
        lamps = rng.sample(self.LAMPS, n)
        return {"wires": wires, "lamps": lamps, "lamp": lanes[-1][cut], "answer": cut}

    def public(self, p):
        at = len(p["got"])
        rd = p["rounds"][min(at, len(p["rounds"]) - 1)]
        return {"n": p["n"], "at": at, "rounds": len(p["rounds"]), "got": p["got"], "wires": rd["wires"],
                "lamps": rd["lamps"], "lamp": rd["lamp"], "t0": p["t0"], "fuse": self._fuse(p)}

    def move(self, p, body):
        pick, t = body.get("pick"), body.get("t")
        if not isinstance(pick, int) or isinstance(pick, bool) or not -1 <= pick < p["n"]:
            raise Refuse("Tap a terminal.")                       # -1: the fuse went out first
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not 0 <= t <= 3_600_000:
            raise Refuse("That cut needs its time.")
        took = t - p["t0"] if t >= p["t0"] else t
        right = pick == p["rounds"][len(p["got"])]["answer"] and took <= self._fuse(p) + self.LATE
        p["got"].append(right)
        p["t0"] = t

    def done(self, p):
        return len(p["got"]) >= len(p["rounds"])

    def score(self, p):
        return sum(p["got"]) / len(p["rounds"])

    def solve(self, p):
        return [{"pick": rd["answer"], "t": p["t0"] + 1500 * (k + 1)} for k, rd in enumerate(p["rounds"][len(p["got"]):])]

    def bot(self, p, rng, skill):
        t = p["t0"] + rng.uniform(1500, 0.95 * self._fuse(p))
        if rng.random() < skill:
            return {"pick": p["rounds"][len(p["got"])]["answer"], "t": t}
        return {"pick": rng.randrange(p["n"]), "t": t}


class Password(Game):
    """Hangman at the bank's login: guess the password a letter at a time, and every letter that isn't in it is a
    try gone. The words are naval, bank and British, so a good guess helps; the longer ones lean on the less common
    letters (K, P, B, W, X, Q...) so running through the vowels doesn't crack them. The word is the secret."""
    key, role, title = "password", "hacker", "PASSWORD"
    pace = (1.5, 3.5)
    TRIES = 5                                                     # wrong letters before it locks you out
    WORDS = {
        "s": "VAULT MONEY CROWN BOSUN PENNY FLEET TIARA BOOTY DRAKE HEIST CORGI ANCHOR NELSON CANNON PIRATE GUINEA "
             "TELLER POUNDS KIPPER TEAPOT GALLEY CHEQUE SAILOR PARROT BUTLER SCONES BRANDY CASTLE BOWLER MUSKET "
             "JEWELS RANSOM ROBBER CUTTER TOFFEE",
        "m": "VICTORY CUTLASS BULLION SKIPPER PICKAXE JACKPOT BRIBERY KIPPERS PUFFINS FLOTSAM QUARTER BOWLINE "
             "FATHOMS SHERBET CROWBAR FLUMMOX SQUALLS BOXROOM CRUMPET",
        "big": "SOVEREIGN SHIPWRECK BUCCANEER JEWELLERY BOWSPRIT CUFFLINKS QUAYSIDE KNAPSACK PAWNSHOP SHIPSHAPE "
               "BLUDGEON WAXWORKS STRONGBOX LOCKSMITH SPYGLASS KEELHAUL FLAGSHIP DOUBLOON LIGHTHOUSE",
    }
    COMMON = "EAORINTSLUDCHMPGBYFWKVXZJQ"                         # roughly how often each letter turns up

    def make(self, rng, size, story):
        return {"word": rng.choice(self.WORDS.get(size, self.WORDS["s"]).split()), "tried": []}

    def _misses(self, p):
        return sum(ch not in p["word"] for ch in p["tried"])

    def _found(self, p):
        return sum(ch in p["tried"] for ch in p["word"])

    def public(self, p):
        return {"shown": [ch if ch in p["tried"] else "" for ch in p["word"]], "tried": p["tried"],
                "misses": self._misses(p), "tries": self.TRIES}

    def move(self, p, body):
        ch = body.get("letter")
        ch = ch.upper() if isinstance(ch, str) else ""
        if len(ch) != 1 or ch not in string.ascii_uppercase:
            raise Refuse("Pick a letter.")
        if ch in p["tried"]:
            raise Refuse("You've tried that one.")
        p["tried"].append(ch)

    def done(self, p):
        return self._found(p) == len(p["word"]) or self._misses(p) >= self.TRIES

    def score(self, p):
        if self._found(p) == len(p["word"]):
            return 1 - 0.15 * max(0, self._misses(p) - 1)        # one wrong letter is free, then 0.15 each
        return 0.5 * self._found(p) / len(p["word"])

    def solve(self, p):
        return [{"letter": ch} for ch in dict.fromkeys(p["word"]) if ch not in p["tried"]]

    def bot(self, p, rng, skill):
        left = [ch for ch in self.COMMON if ch not in p["tried"]]
        if rng.random() < skill:
            hits = [ch for ch in left if ch in p["word"]]
            if hits:
                return {"letter": rng.choice(hits)}
        return {"letter": left[min(len(left) - 1, int(rng.random() ** 2 * len(left)))]}


# a tile's ends, as bits: north, east, south, west
N, E, S, W = 1, 2, 4, 8
STEPS = ((N, -1, 0), (E, 0, 1), (S, 1, 0), (W, 0, -1))
FACING = {N: S, E: W, S: N, W: E}


def _turn(m: int) -> int:
    """A tile turned a quarter clockwise: north goes to east, east to south..."""
    return ((m << 1) | (m >> 3)) & 15


class Grid(Game):
    """The alarm's power: a board of circuit tiles, each turned a quarter at a tap, to carry the power from the
    source on the left to the alarm box on the right. It's made by laying a wandering path across the board and
    filling the rest with spare pieces, then turning every tile at random; the path is the secret. The power's
    worked out here (what's lit is shown, since the player can see it anyway)."""
    key, role, title = "grid", "hacker", "POWER GRID"
    pace = (0.6, 1.6)
    SIZES = {"s": (5, 9, 12), "m": (5, 10, 13), "big": (5, 13, 17)}   # the board's side, and how long the path runs
    SPARES = (1, 1, 5, 5, 3, 3, 3, 7)                                  # dead ends, straights, elbows and tees for the rest

    def make(self, rng, size, story):
        n, lo, hi = self.SIZES.get(size, self.SIZES["s"])
        path = self._path(rng, n, lo, hi)
        sol = {}
        for k, (r, c) in enumerate(path):
            ends = [W if k == 0 else self._way((r, c), path[k - 1]), E if k == len(path) - 1 else self._way((r, c), path[k + 1])]
            sol[r * n + c] = ends[0] | ends[1]
        cells = [r * n + c for r, c in path]
        best, tries = None, 0
        while best is None or tries < 60:
            tries += 1
            tiles = [sol.get(i, rng.choice(self.SPARES)) for i in range(n * n)]
            tiles = [self._turned(m, rng.randrange(4)) for m in tiles]
            p = {"n": n, "tiles": tiles, "src": path[0][0], "dst": path[-1][0], "path": cells,
                 "sol": [sol[i] for i in cells], "turns": 0}
            # mostly turned wrong and not already on
            if sum(tiles[i] == m for i, m in zip(cells, p["sol"])) > len(cells) // 4 or self._live(p)[1]:
                continue
            # and, as near as can be, no short cut through the spare pieces
            cut = self._shortest(p)
            if best is None or cut > best[0]:
                best = (cut, p)
            if cut >= 0.75 * len(cells):
                break
        p = best[1]
        p["par"] = sum(self._need(p["tiles"][i], m) for i, m in zip(cells, p["sol"]))
        p["limit"] = 2 * p["par"] + 2 * n
        return p

    @staticmethod
    def _way(a, b):
        return {(-1, 0): N, (1, 0): S, (0, 1): E, (0, -1): W}[(b[0] - a[0], b[1] - a[1])]

    @staticmethod
    def _turned(m, k):
        for _ in range(k):
            m = _turn(m)
        return m

    def _need(self, m, want):
        """Quarter turns to bring a tile round to how the path wants it (a straight's right two ways round)."""
        return next(k for k in range(4) if self._turned(m, k) == want)

    def _path(self, rng, n, lo, hi):
        """A path of lo to hi cells from somewhere on the left edge to somewhere on the right, never crossing itself."""
        while True:
            path = [(rng.randrange(n), 0)]
            budget = [4000]

            def walk():
                budget[0] -= 1
                r, c = path[-1]
                if c == n - 1 and len(path) >= lo:
                    return True
                if len(path) >= hi or budget[0] < 0:
                    return False
                steps = [(r + dr, c + dc) for _, dr, dc in STEPS]
                rng.shuffle(steps)
                for rr, cc in steps:
                    if 0 <= rr < n and 0 <= cc < n and (rr, cc) not in path:
                        path.append((rr, cc))
                        if walk():
                            return True
                        path.pop()
                return False

            if walk():
                return path

    def _shortest(self, p):
        """The fewest tiles any route from the source to the alarm box could run through, however they're turned (a
        straight carries on, an elbow turns, a tee does either)."""
        n, tiles = p["n"], p["tiles"]
        todo, seen = [(p["src"], 0, W, 1)], set()
        for r, c, came, k in todo:                                # breadth first: todo grows as it goes
            if (r, c, came) in seen:
                continue
            seen.add((r, c, came))
            ends = bin(tiles[r * n + c]).count("1")
            straight = tiles[r * n + c] in (5, 10)
            for d, dr, dc in STEPS:
                if d == came or (straight and d != FACING[came]) or (ends == 2 and not straight and d == FACING[came]):
                    continue
                if r == p["dst"] and c == n - 1 and d == E:
                    return k
                rr, cc = r + dr, c + dc
                if 0 <= rr < n and 0 <= cc < n:
                    todo.append((rr, cc, FACING[d], k + 1))
        return n * n

    def _live(self, p):
        """Which tiles the power reaches from the source, and whether it gets out the far side to the alarm box."""
        n, tiles = p["n"], p["tiles"]
        lit = [0] * (n * n)
        start = p["src"] * n
        if tiles[start] & W:
            lit[start] = 1
            todo = [start]
            while todo:
                i = todo.pop()
                r, c = divmod(i, n)
                for d, dr, dc in STEPS:
                    rr, cc = r + dr, c + dc
                    j = rr * n + cc
                    if tiles[i] & d and 0 <= rr < n and 0 <= cc < n and not lit[j] and tiles[j] & FACING[d]:
                        lit[j] = 1
                        todo.append(j)
        end = p["dst"] * n + n - 1
        return lit, bool(lit[end] and tiles[end] & E)

    def public(self, p):
        lit, on = self._live(p)
        return {"n": p["n"], "tiles": p["tiles"], "lit": lit, "on": on, "src": p["src"], "dst": p["dst"],
                "turns": p["turns"], "limit": p["limit"]}

    def move(self, p, body):
        at = _num(body, "at", p["n"] ** 2, "Tap a tile.")
        p["tiles"][at] = _turn(p["tiles"][at])
        p["turns"] += 1

    def done(self, p):
        return self._live(p)[1] or p["turns"] >= p["limit"]

    def score(self, p):
        lit, on = self._live(p)
        if on:
            spare = p["par"] + 2                                         # a couple of turns spent looking are fine
            return max(0.6, 1 - 0.04 * max(0, p["turns"] - spare))
        return 0.6 * sum(lit[i] for i in p["path"]) / len(p["path"])   # partway: for how far along the power gets

    def _wrong(self, p):
        """The path's tiles that aren't round the right way yet, in order from the source."""
        return [i for i, m in zip(p["path"], p["sol"]) if p["tiles"][i] != m]

    def solve(self, p):
        q = {**p, "tiles": list(p["tiles"])}
        out = []
        for i, m in zip(p["path"], p["sol"]):
            for _ in range(self._need(q["tiles"][i], m)):
                if self._live(q)[1]:                                   # a spare piece got there first
                    return out
                q["tiles"][i] = _turn(q["tiles"][i])
                out.append({"at": i})
        return out

    def bot(self, p, rng, skill):
        wrong = self._wrong(p)
        if wrong and rng.random() < skill:
            return {"at": wrong[0]}
        return {"at": rng.randrange(p["n"] ** 2)}


def _clue(code: str, guess: str) -> tuple[int, int]:
    """How a guess at a code of different digits does: digits in the right place, and right but in the wrong place."""
    exact = sum(a == b for a, b in zip(code, guess))
    return exact, len(set(code) & set(guess)) - exact


class Logic(Game):
    """The classic clue lock: a few codes with how many digits each gets right, and in the right place or not, and
    only one code fits them all. Work it out and key it in; a wrong try is a clue too. The code is the secret (and
    the clues are made to fit exactly one, checked by trying every code)."""
    key, role, title = "logic", "hacker", "LOGIC LOCK"
    pace = (6.0, 12.0)
    TRIES = 2
    SCORES = (1.0, 0.6)                                           # cracked on the first try, or the second
    # how many digits, which ones the lock takes, and how many clues (the first always NOTHING RIGHT, a way in)
    SIZES = {"s": (3, "1234567", (4, 4)), "m": (3, "0123456789", (4, 5)), "big": (4, "12345678", (5, 5))}

    def make(self, rng, size, story):
        n, digits, ks = self.SIZES.get(size, self.SIZES["s"])
        codes = ["".join(c) for c in itertools.permutations(digits, n)]
        while True:
            code = rng.choice(codes)
            clues = self._clues(rng, code, codes, rng.randint(*ks))
            if clues:
                rng.shuffle(clues)
                return {"len": n, "digits": digits, "code": code, "clues": clues, "tries": []}

    def _clues(self, rng, code, codes, k):
        """k clues that leave only the code, each one needed, the first sharing nothing with it; or None if this go
        didn't find any."""
        n = len(code)
        g = rng.choice([c for c in codes if not set(c) & set(code)])
        clues = [g]
        left = [c for c in codes if not set(c) & set(g)]
        for step in range(1, k):
            aim = len(left) ** ((k - step - 1) / (k - step))     # narrow it down about evenly, clue by clue
            opts = []
            for g in rng.sample(codes, min(len(codes), 60)):
                said = _clue(code, g)
                if said[0] == n or g in clues:
                    continue
                keep = [c for c in left if _clue(c, g) == said]
                if len(keep) < len(left):
                    opts.append((abs(math.log(len(keep)) - math.log(aim)), g, keep))
            if not opts:
                return None
            opts.sort()
            _, g, left = opts[rng.randrange(min(3, len(opts)))]
            clues.append(g)
        if left != [code]:
            return None
        said = {g: _clue(code, g) for g in clues}
        for g in clues:                                       # every clue must matter
            rest = [h for h in clues if h != g]
            if sum(all(_clue(c, h) == said[h] for h in rest) for c in codes) == 1:
                return None
        return [{"code": g, "right": said[g][0], "near": said[g][1]} for g in clues]

    def public(self, p):
        return {"len": p["len"], "digits": p["digits"], "clues": p["clues"], "max": self.TRIES,
                "tries": [{"code": t, "right": _clue(p["code"], t)[0], "near": _clue(p["code"], t)[1]} for t in p["tries"]]}

    def move(self, p, body):
        code = body.get("code")
        if (not isinstance(code, str) or len(code) != p["len"] or any(ch not in p["digits"] for ch in code)
                or len(set(code)) != len(code)):
            raise Refuse(f"{p['len']} different digits.")
        p["tries"].append(code)

    def done(self, p):
        return p["code"] in p["tries"] or len(p["tries"]) >= self.TRIES

    def score(self, p):
        if p["code"] in p["tries"]:
            return self.SCORES[p["tries"].index(p["code"])]
        return 0.1 * max((_clue(p["code"], t)[0] for t in p["tries"]), default=0)

    def solve(self, p):
        return [{"code": p["code"]}]

    def bot(self, p, rng, skill):
        if rng.random() < skill:
            return {"code": p["code"]}
        # a slip: a code that fits every clue but one
        codes = ["".join(c) for c in itertools.permutations(p["digits"], p["len"])]
        slip = rng.randrange(len(p["clues"]))
        near = [c for c in codes if c != p["code"] and c not in p["tries"] and all(
            _clue(c, h["code"]) == (h["right"], h["near"]) for i, h in enumerate(p["clues"]) if i != slip)]
        return {"code": rng.choice(near or [c for c in codes if c not in p["tries"]])}


class Tiles(Game):
    """A CCTV still cut into a 3x3 sliding puzzle, eight tiles and a gap: slide them back into the picture. It's
    scrambled by sliding at random from solved, so it can always be put back, and kept only if the fewest slides
    that put it back (par) are at least so many; it's scored against par, with a couple of slides' leeway."""
    key, role, title = "tiles", "hacker", "SLIDING TILES"
    pace = (0.6, 1.5)
    SIZES = {"s": (8, 6), "m": (16, 12), "big": (24, 18)}         # slides that scramble it, and the least par kept
    SPARE = 2
    GAP = 8                                                       # the gap's tile (it belongs bottom right)
    NEXT = {i: [j for j in range(9) if abs(i // 3 - j // 3) + abs(i % 3 - j % 3) == 1] for i in range(9)}

    def make(self, rng, size, story):
        k, least = self.SIZES.get(size, self.SIZES["s"])
        while True:
            at, was = list(range(9)), None
            for _ in range(k):
                gap = at.index(self.GAP)
                j = rng.choice([j for j in self.NEXT[gap] if j != was])   # never straight back where it came from
                at[gap], at[j] = at[j], at[gap]
                was = gap
            par = len(self._slides(at))
            if par >= least:
                return {"at": at, "par": par, "moves": 0, "limit": 4 * par + 12}

    def public(self, p):
        return {"at": p["at"], "moves": p["moves"], "limit": p["limit"]}

    def move(self, p, body):
        i = _num(body, "at", 9, "Tap a tile.")
        gap = p["at"].index(self.GAP)
        if i not in self.NEXT[gap]:
            raise Refuse("Slide one next to the gap.")
        p["at"][gap], p["at"][i] = p["at"][i], p["at"][gap]
        p["moves"] += 1

    def _home(self, p):
        return sum(t == i for i, t in enumerate(p["at"]) if t != self.GAP)

    def done(self, p):
        return self._home(p) == 8 or p["moves"] >= p["limit"]

    def score(self, p):
        if self._home(p) == 8:
            return max(0.6, 1 - 0.3 * max(0, p["moves"] - p["par"] - self.SPARE) / p["par"])
        return 0.5 * self._home(p) / 8

    def _slides(self, at):
        """The fewest slides that put it back (A*, by how far each tile is from home), as the cells to tap."""
        far = lambda s: sum(abs(i // 3 - t // 3) + abs(i % 3 - t % 3) for i, t in enumerate(s) if t != self.GAP)  # noqa: E731
        start, goal = tuple(at), tuple(range(9))
        best, came = {start: 0}, {start: None}
        todo = [(far(start), 0, start)]
        while todo:
            _, g, s = heapq.heappop(todo)
            if s == goal:
                break
            if g > best[s]:
                continue
            gap = s.index(self.GAP)
            for j in self.NEXT[gap]:
                t = list(s)
                t[gap], t[j] = t[j], t[gap]
                t = tuple(t)
                if g + 1 < best.get(t, 99):
                    best[t], came[t] = g + 1, (s, j)
                    heapq.heappush(todo, (g + 1 + far(t), g + 1, t))
        out, s = [], goal
        while came[s]:
            s, j = came[s]
            out.append(j)
        return out[::-1]

    def solve(self, p):
        return [{"at": j} for j in self._slides(p["at"])]

    def bot(self, p, rng, skill):
        if rng.random() < skill:
            return {"at": self._slides(p["at"])[0]}
        return {"at": rng.choice(self.NEXT[p["at"].index(self.GAP)])}


GAMES: dict[str, Game] = {g.key: g for g in (Lights(), Wire(), Password(), Grid(), Logic(), Tiles())}
