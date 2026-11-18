"""The Muscle's games beyond the first (loading the van, which is in heist.py). See __init__.py for the rules a game
follows. The Muscle has a fourth size, "loot" (a loot job's score makes the vault hold more): it plays like "m", since
loot jobs come round often and an "s" one was over before it started; every size table here has s, m and big.

Three of these are played against the clock (count the cash, heave, stack the gold): make() draws up the schedule,
the page animates it from the moment the puzzle showed and sends each tap with its time in ms since then, and the
score is worked out here from the schedule and those times."""

import copy
import itertools
import math

from lib.activities.heist_games import Game, Refuse

# the loot art the page has (public/heist/loot-<name>.webp), for anything that wants a bag of something
LOOT = ["fivers", "gold", "krugerrands", "bonds", "medals", "pounds", "rolex", "premium", "diamonds", "coppers",
        "stamps", "crown"]
LATEST = 3_600_000          # no tap comes an hour in: anything later is rubbish


def _sized(table: dict, size: str):
    return table.get("m" if size == "loot" else size) or table["s"]


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_time(x) -> bool:
    """A tap's time: a real number of ms since the puzzle showed (bool is an int in Python, so it's turned away)."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and 0 <= x <= LATEST


class Weight(Game):
    """The fence's scales: he'll only take exactly the weight he's after, so put bags on the pan until the needle sits
    on it, before his patience (the fuse) runs out. The bags that make it are picked first, and the decoys are a kilo
    or two either side of those, so plenty of loads come close and only one is exact. The taps are few (two over the
    answer's), so it pays to add it up before putting anything on. Each tap comes with its time."""
    key, role, title = "weight", "muscle", "EXACT WEIGHT"
    pace = (1.2, 2.6)
    SIZES = {"s": (6, 3, 20000), "m": (7, 4, 22000), "big": (8, 5, 25000)}     # bags, how many make it, ms of fuse
    SPARE = 2                                                    # taps over the answer's: one bag put back
    SLACK = 400                                                  # ms late a tap can be and still count

    def make(self, rng, size, story):
        n, k, fuse = _sized(self.SIZES, size)
        for _ in range(3000):
            sol = [rng.randint(3, 11) for _ in range(k)]
            target = sum(sol)
            # the decoys are near the answer's own bags, so swapping one in nearly works
            decoys = [rng.choice(sol) + rng.choice((-2, -1, 1, 2)) for _ in range(n - k)]
            weights = sol + decoys
            if min(weights) < 2 or any(weights.count(w) > 2 for w in weights):
                continue
            exact = [c for m in range(1, n + 1) for c in itertools.combinations(weights, m) if sum(c) == target]
            if len(exact) == 1 or (len(exact) == 2 and min(map(len, exact)) >= k):
                break
        order = rng.sample(range(n), n)
        art = rng.sample(LOOT, n)
        bags = [{"w": weights[i], "art": art[j]} for j, i in enumerate(order)]
        return {"bags": bags, "target": target, "sol": sorted(order.index(i) for i in range(k)), "on": [], "taps": 0,
                "limit": k + self.SPARE, "fuse": fuse, "last": 0, "out": False}

    def _total(self, p):
        return sum(p["bags"][i]["w"] for i in p["on"])

    def public(self, p):
        return {"bags": p["bags"], "target": p["target"], "on": p["on"], "left": p["limit"] - p["taps"], "fuse": p["fuse"],
                "last": p["last"]}

    def move(self, p, body):
        t = body.get("t")
        if not _is_time(t) or t < p["last"]:
            raise Refuse("That tap's time is off.")
        if body.get("out") is True:                              # the fuse has gone: sold as it stands
            p["out"], p["last"] = True, t
            return
        i = body.get("bag")
        if not _is_int(i) or not 0 <= i < len(p["bags"]):
            raise Refuse("Tap a bag.")
        if t > p["fuse"] + self.SLACK:
            raise Refuse("He's not waiting any longer.")
        p["on"] = sorted(set(p["on"]) ^ {i})
        p["taps"] += 1
        p["last"] = t

    def done(self, p):
        return self._total(p) == p["target"] or p["taps"] >= p["limit"] or p["out"]

    def score(self, p):
        got, want = self._total(p), p["target"]
        if got == want:
            return 1.0
        near = max(0.0, 1 - 4 * abs(got - want) / want)          # a kilo or two out is worth something, ten nothing
        return (0.6 if got < want else 0.45) * near

    def _toggles(self, p):
        """The fewest taps from the pan as it is to exactly the weight: no fewer-tap pan on the way can hit it, so
        it isn't sold early."""
        on, n = set(p["on"]), len(p["bags"])
        best = None
        for mask in range(1 << n):
            pick = {i for i in range(n) if mask >> i & 1}
            if sum(p["bags"][i]["w"] for i in pick) == p["target"] and (best is None or len(pick ^ on) < len(best ^ on)):
                best = pick
        return sorted(on - best) + sorted(best - on)

    def solve(self, p):
        toggles = self._toggles(p)
        gap = max(1.0, (p["fuse"] - p["last"]) / (len(toggles) + 1))
        return [{"bag": i, "t": round(p["last"] + gap * (j + 1), 1)} for j, i in enumerate(toggles)]

    def bot(self, p, rng, skill):
        t = round(p["last"] + rng.uniform(1300, 2800), 1)
        if t > p["fuse"]:
            return {"out": True, "t": t}
        if rng.random() < skill:
            return {"bag": self._toggles(p)[0], "t": t}
        return {"bag": rng.randrange(len(p["bags"])), "t": t}


# crates: a shape is a list of [row, col] cells, normalised to its top left and sorted
def _norm(cells):
    r0, c0 = min(r for r, _ in cells), min(c for _, c in cells)
    return sorted([r - r0, c - c0] for r, c in cells)


def _turn(cells, k):
    """A crate turned a quarter clockwise k times, as the page turns it (row, col becomes col, -row)."""
    for _ in range(k % 4):
        cells = [[c, -r] for r, c in cells]
    return _norm(cells)


def _canon(cells):
    return min(tuple(map(tuple, _turn(cells, k))) for k in range(4))


class Pack(Game):
    """The back of the van, its floor a grid with a wheel arch or two poking into it, and crates of different shapes
    to fit in, Tetris style. It's made by cutting the floor round the arches into crates, so they always fill it
    exactly (where each came from is the secret), and then adding a spare that isn't one of them: there's more crate
    than van, so working out which to leave out is half of it. Pick a crate, turn it, then tap where its
    corner (its top row's first square) goes; a crate in the van can be lifted back out. Only a full van is full marks."""
    key, role, title = "pack", "muscle", "PACK THE VAN"
    pace = (1.5, 3.0)
    # rows, cols, crates that fill it (big: 8 or 9), spares, wheel arches (one back corner, or both)
    SIZES = {"s": (5, 5, (6,), 1, 1), "m": (6, 5, (7,), 1, 2), "big": (6, 6, (8, 9), 1, 2)}
    LO, HI = 3, 6                                  # how many squares a crate is
    SPARE_MOVES = 8                                # moves over one a crate: a few goes at it, then it's what's in

    def _cut(self, rng, rows, cols, blocked, k):
        n = rows * cols

        def nbrs(i):
            r, c = divmod(i, cols)
            for rr, cc in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)):
                if 0 <= rr < rows and 0 <= cc < cols:
                    yield rr * cols + cc
        free = [i for i in range(n) if i not in blocked]
        fallback = None
        for _ in range(3000):
            owner, sizes = [-1] * n, [1] * k
            for i in blocked:
                owner[i] = -2
            for j, s in enumerate(rng.sample(free, k)):
                owner[s] = j
            # grow every crate a square at a time, mostly the smallest, until the floor's covered
            while -1 in owner:
                grow: dict[int, set[int]] = {}
                for i in range(n):
                    if owner[i] == -1:
                        for m in nbrs(i):
                            if owner[m] >= 0 and sizes[owner[m]] < self.HI:
                                grow.setdefault(owner[m], set()).add(i)
                if not grow:
                    break
                small = min(sizes[j] for j in grow)
                pick = rng.choice(sorted(j for j in grow if sizes[j] == small) if rng.random() < 0.75 else sorted(grow))
                cell = rng.choice(sorted(grow[pick]))
                owner[cell] = pick
                sizes[pick] += 1
            if -1 in owner or min(sizes) < self.LO:
                continue
            homes = [[list(divmod(i, cols)) for i in range(n) if owner[i] == j] for j in range(k)]
            fallback = fallback or homes
            shapes = [_canon(h) for h in homes]
            # every crate a different shape; with eight or nine of four squares or so there aren't enough, so two alike
            if max(map(shapes.count, shapes)) <= (1 if k <= 7 else 2):
                return homes
        return fallback

    def _spare(self, rng, rows, cols, homes, spares):
        """A crate that looks like it belongs but doesn't: chunky, the size of the others, and a shape none of them
        is. With it there's more crate than van, so something has to be left out."""
        seen = {_canon(s) for s in [_norm(h) for h in homes] + spares}
        shape = None
        for _ in range(200):
            cells = {(0, 0)}
            size = rng.randint(4, self.HI - 1)
            while len(cells) < size:
                r, c = rng.choice(sorted(cells))
                dr, dc = rng.choice(((1, 0), (-1, 0), (0, 1), (0, -1)))
                cells.add((r + dr, c + dc))
            shape = _norm([list(x) for x in cells])
            if _canon(shape) not in seen and max(max(r, c) for r, c in shape) < min(rows, cols):
                break
        return shape

    def make(self, rng, size, story):
        rows, cols, ks, spares, arches = _sized(self.SIZES, size)
        corners = [(rows - 1) * cols, rows * cols - 1]           # the wheel arches stick up at the back corners
        blocked = sorted(rng.sample(corners, arches))
        homes = self._cut(rng, rows, cols, set(blocked), rng.choice(ks))
        extra: list = []
        for _ in range(spares):
            extra.append(self._spare(rng, rows, cols, homes, extra))
        crates = []
        for home in homes:
            turned = rng.randrange(4)                          # shown turned, so working out the turn is half of it
            crates.append({"shape": _turn(home, turned), "home": sorted(home), "turn": (4 - turned) % 4})
        crates += [{"shape": _turn(x, rng.randrange(4)), "home": None, "turn": 0} for x in extra]
        rng.shuffle(crates)
        return {"rows": rows, "cols": cols, "blocked": [list(divmod(i, cols)) for i in blocked], "crates": crates,
                "put": [None] * len(crates), "moves": 0, "limit": len(crates) + self.SPARE_MOVES}

    def _cells(self, p, i, rot, at):
        cells = _turn(p["crates"][i]["shape"], rot)
        (ar, ac), (r, c) = cells[0], at
        return [[cr - ar + r, cc - ac + c] for cr, cc in cells]

    def _floor(self, p):
        return p["rows"] * p["cols"] - len(p["blocked"])

    def public(self, p):
        return {"rows": p["rows"], "cols": p["cols"], "blocked": p["blocked"], "crates": [c["shape"] for c in p["crates"]],
                "put": p["put"], "left": p["limit"] - p["moves"]}

    def move(self, p, body):
        if "take" in body:
            i = body["take"]
            if not _is_int(i) or not 0 <= i < len(p["crates"]) or p["put"][i] is None:
                raise Refuse("That crate isn't in the van.")
            p["put"][i] = None
            p["moves"] += 1
            return
        i, rot, at = body.get("put"), body.get("rot", 0), body.get("at")
        if not _is_int(i) or not 0 <= i < len(p["crates"]):
            raise Refuse("Pick a crate.")
        if not _is_int(rot) or not 0 <= rot < 4:
            raise Refuse("Turn it a quarter at a time.")
        if not isinstance(at, list) or len(at) != 2 or not all(_is_int(x) for x in at):
            raise Refuse("Tap where it goes.")
        cells = self._cells(p, i, rot, at)
        taken = {tuple(x) for x in p["blocked"]} | {tuple(x) for j, put in enumerate(p["put"]) if put is not None and j != i
                                                    for x in put}
        if any(not (0 <= r < p["rows"] and 0 <= c < p["cols"]) or (r, c) in taken for r, c in cells):
            raise Refuse("It won't fit there.")
        p["put"][i] = sorted(cells)
        p["moves"] += 1

    def _filled(self, p):
        return sum(len(x) for x in p["put"] if x is not None)

    def done(self, p):
        return self._filled(p) == self._floor(p) or p["moves"] >= p["limit"]

    def score(self, p):
        return self._filled(p) / self._floor(p)

    def solve(self, p):
        crates = p["crates"]
        wrong = [i for i, x in enumerate(p["put"]) if x is not None and x != crates[i]["home"]]
        home = [i for i, c in enumerate(crates) if c["home"] and p["put"][i] != c["home"]]
        return [{"take": i} for i in wrong] + [{"put": i, "rot": crates[i]["turn"], "at": crates[i]["home"][0]} for i in home]

    def bot(self, p, rng, skill):
        """A so-so packer: it knows where a crate goes some of the time, and otherwise puts one in wherever it fits."""
        if rng.random() < skill * 0.75:
            return self.solve(p)[0]
        out = [i for i, x in enumerate(p["put"]) if x is None]
        if not out:
            return {"take": rng.randrange(len(p["crates"]))}
        i = rng.choice(out)
        for _ in range(40):
            mv = {"put": i, "rot": rng.randrange(4), "at": [rng.randrange(p["rows"]), rng.randrange(p["cols"])]}
            try:
                self.move(copy.deepcopy(p), mv)
                return mv
            except Refuse:
                pass
        return self.solve(p)[0]


class Cash(Game):
    """Bundles of notes slapped down on the table one after another, then the total to pick from a few. Soon after
    the last one lands a cloth goes over the pile, so it's a running total as they land, not a count afterwards. The
    bundles are sent to the page (it has to show them), so the right total isn't much of a secret, but which button
    it's on stays here. Each round's answer has to come in time: it counts from the last bundle landing."""
    key, role, title = "cash", "muscle", "COUNT THE CASH"
    pace = (2.0, 4.0)
    NOTES = [5, 10, 10, 20, 20, 20, 50]          # how often each turns up
    # bundles in each round, totals to pick from, ms between bundles, ms to answer once the last has landed, and ms
    # the pile stays in sight after that before the cloth goes over
    SIZES = {"s": ([4, 5], 3, 480, 6500, 3000), "m": ([5, 6, 7], 4, 380, 4500, 1200), "big": ([6, 7, 8], 4, 330, 4000, 700)}
    LEAD, GAP, SLACK = 900, 1300, 400     # ms before the first round, after an answer before the next, late allowed

    def make(self, rng, size, story):
        counts, choices, every, window, peek = _sized(self.SIZES, size)
        rounds = []
        for n in counts:
            notes = [rng.choice(self.NOTES) for _ in range(n)]
            total = sum(notes)
            # the totals a quick count gets wrong: a fiver, a tenner or a twenty out (a bundle missed or counted
            # twice), never so far off it can be ruled out at a glance
            near = {total + d for d in (-20, -10, -5, 5, 10, 20)}
            near = sorted(t for t in near if t > 0 and t != total)
            options = rng.sample(near, choices - 1) + [total]
            rng.shuffle(options)
            rounds.append({"notes": notes, "options": options, "answer": options.index(total), "at": self._pile(rng, n)})
        return {"rounds": rounds, "every": every, "window": window, "peek": peek, "picks": []}

    @staticmethod
    def _pile(rng, n):
        """Where each bundle lands on the table (its middle, as a share of the table's width and height) and how it's
        turned: a pile, overlapping, but never so close that one hides another."""
        at = []
        for _ in range(n):
            for _ in range(60):
                x, y = rng.uniform(0.2, 0.8), rng.uniform(0.2, 0.8)
                if all((x - a) ** 2 + ((y - b) * 0.8) ** 2 > 0.03 for a, b, _ in at):
                    break
            at.append([round(x, 3), round(y, 3), rng.randint(-28, 28)])
        return at

    def _start(self, p):
        """When the round now showing started dealing, in ms since the puzzle showed."""
        return self.LEAD if not p["picks"] else p["picks"][-1][1] + self.GAP

    def _right(self, p, k):
        pick, t = p["picks"][k]
        r = p["rounds"][k]
        start = self.LEAD if k == 0 else p["picks"][k - 1][1] + self.GAP
        landed = start + (len(r["notes"]) - 1) * p["every"]
        return pick == r["answer"] and t <= landed + p["window"] + self.SLACK

    def public(self, p):
        n = len(p["picks"])
        shown = [{"notes": r["notes"], "options": r["options"], "at": r["at"]} for r in p["rounds"][:n + 1]]
        return {"rounds": len(p["rounds"]), "shown": shown, "right": [self._right(p, k) for k in range(n)],
                "start": self._start(p), "every": p["every"], "window": p["window"], "peek": p["peek"],
                "last": p["picks"][-1][1] if p["picks"] else 0}

    def move(self, p, body):
        if self.done(p):
            raise Refuse("That's the lot.")
        pick, t = body.get("pick"), body.get("t")
        r = p["rounds"][len(p["picks"])]
        if not _is_int(pick) or not -1 <= pick < len(r["options"]):       # -1: the time ran out
            raise Refuse("Pick a total.")
        if not _is_time(t) or (p["picks"] and t < p["picks"][-1][1]):
            raise Refuse("That tap's time is off.")
        p["picks"].append([pick, t])

    def done(self, p):
        return len(p["picks"]) >= len(p["rounds"])

    def score(self, p):
        return sum(self._right(p, k) for k in range(len(p["picks"]))) / len(p["rounds"])

    def solve(self, p):
        out, start = [], self._start(p)
        for r in p["rounds"][len(p["picks"]):]:
            t = start + (len(r["notes"]) - 1) * p["every"] + 600
            out.append({"pick": r["answer"], "t": t})
            start = t + self.GAP
        return out

    def bot(self, p, rng, skill):
        r = p["rounds"][len(p["picks"])]
        t = self._start(p) + (len(r["notes"]) - 1) * p["every"] + rng.uniform(1200, 4500)
        pick = r["answer"] if rng.random() < skill else rng.randrange(len(r["options"]))
        return {"pick": pick, "t": round(t, 1)}


class Dye(Game):
    """A dye pack slips into one of the cash bags, then the bags are shuffled round like a shell game: tap the one
    with the dye pack in, to leave it behind. The page is sent the shuffle (which the player can follow) but not
    where it ends up, which is judged here. Each move swaps a pair of bags, or in a bigger row two pairs at once."""
    key, role, title = "dye", "muscle", "DYE PACKS"
    pace = (4.0, 7.0)
    # each round: bags, moves, ms a move takes, and the chance a move swaps two pairs at once
    SIZES = {"s": [(3, 6, 520, 0), (3, 7, 480, 0)],
             "m": [(3, 9, 380, 0), (4, 10, 350, 0.25), (4, 11, 330, 0.3)],
             "big": [(4, 11, 320, 0.3), (4, 12, 300, 0.4), (5, 13, 290, 0.45)]}

    def make(self, rng, size, story):
        rounds = []
        for n, count, ms, double in _sized(self.SIZES, size):
            start = rng.randrange(n)
            at, moves = start, []
            while len(moves) < count:
                if rng.random() < 0.6:                             # mostly the bag that matters
                    a, b = at, rng.choice([x for x in range(n) if x != at])
                else:
                    a, b = rng.sample(range(n), 2)
                move = [sorted([a, b])]
                rest = [x for x in range(n) if x not in (a, b)]
                # another pair at the same time, to pull the eye; never one meeting in the same middle (0-3 with
                # 1-2), where all four would pile up and nobody could follow it
                others = [sorted(q) for q in itertools.combinations(rest, 2) if sum(q) != a + b]
                if others and rng.random() < double:
                    move.append(rng.choice(others))
                if moves and moves[-1] == move:                    # straight back again looks like nothing happened
                    continue
                moves.append(move)
                for a, b in move:
                    at = b if at == a else a if at == b else at
            rounds.append({"n": n, "start": start, "swaps": moves, "ms": ms, "end": at})
        return {"rounds": rounds, "picks": []}

    def public(self, p):
        k = len(p["picks"])
        shown = [{"n": r["n"], "start": r["start"], "swaps": r["swaps"], "ms": r["ms"],
                  **({"end": r["end"], "pick": p["picks"][i]} if i < k else {})} for i, r in enumerate(p["rounds"][:k + 1])]
        return {"rounds": len(p["rounds"]), "shown": shown}

    def move(self, p, body):
        if self.done(p):
            raise Refuse("That's the lot.")
        pick = body.get("pick")
        if not _is_int(pick) or not 0 <= pick < p["rounds"][len(p["picks"])]["n"]:
            raise Refuse("Tap a bag.")
        p["picks"].append(pick)

    def done(self, p):
        return len(p["picks"]) >= len(p["rounds"])

    def score(self, p):
        return sum(pick == r["end"] for pick, r in zip(p["picks"], p["rounds"])) / len(p["rounds"])

    def solve(self, p):
        return [{"pick": r["end"]} for r in p["rounds"][len(p["picks"]):]]

    def bot(self, p, rng, skill):
        r = p["rounds"][len(p["picks"])]
        return {"pick": r["end"] if rng.random() < skill else rng.randrange(r["n"])}


class Mash(Game):
    """Carry the sacks up the stairs: mash HEAVE as fast as you can for a few seconds, a step a tap. The page sends
    its taps in batches with their times and says when the time's up; taps outside the time are refused, and no thumb
    manages more than CAP in a second or one within MIN_GAP of the last, so any like that don't count. Nine a second (ten at the vault), a strong steady
    thumb, gets the sack to the top, and the score falls away faster than the taps do short of it."""
    key, role, title = "mash", "muscle", "HEAVE"
    pace = (0.4, 0.6)
    SIZES = {"s": (6000, 9), "m": (7000, 9), "big": (8000, 10)}     # ms of heaving, and taps a second to the top
    GO = 2400                                      # ms of 3, 2, 1 before it starts
    CAP = 14
    MIN_GAP = 70                                   # ms: a tap this soon after the last one that counted doesn't (two
                                                   # hands, the space bar and the mouse; the page holds to 80ms)
    CURVE = 1.3                                    # score = share of the target to this power: 80% of it is 0.75
    EARLY, LATE = 150, 500                         # ms a tap can be off the window and still be the page's

    def make(self, rng, size, story):
        secs, rate = _sized(self.SIZES, size)
        return {"go": self.GO, "secs": secs, "target": round(rate * secs / 1000), "taps": [], "counted": 0,
                "last": 0, "over": False}

    def public(self, p):
        return {"go": p["go"], "secs": p["secs"], "target": p["target"], "steps": p["counted"], "last": p["last"]}

    def move(self, p, body):
        taps, end = body.get("taps", []), body.get("end", False)
        if not isinstance(taps, list) or len(taps) > 200 or not isinstance(end, bool) or not (taps or end):
            raise Refuse("Heave!")
        lo, hi = p["go"] - self.EARLY, p["go"] + p["secs"] + self.LATE
        last = p["last"]
        for t in taps:
            if not _is_time(t) or t < last or not lo <= t <= hi:
                raise Refuse("Those taps don't add up.")
            last = t
        for t in taps:
            if p["taps"] and t - p["taps"][-1] < self.MIN_GAP:
                continue
            recent = sum(1 for x in p["taps"][-self.CAP:] if t - x < 1000)
            if recent < self.CAP:
                p["taps"].append(t)
                p["counted"] += 1
        if taps:
            p["last"] = taps[-1]
        p["over"] = p["over"] or end

    def done(self, p):
        return p["over"]

    def score(self, p):
        return min(1.0, p["counted"] / p["target"]) ** self.CURVE

    def solve(self, p):
        need = max(0, p["target"] - p["counted"])
        start = max(p["last"], p["go"]) + 1
        gap = (p["go"] + p["secs"] - start) / (need + 1)
        return [{"taps": [round(start + gap * (i + 1), 1) for i in range(need)], "end": True}]

    def bot(self, p, rng, skill):
        """Half a second's taps at a so-so rate, and the last batch says the time's up."""
        rate = 4 + 4 * skill
        start = max(p["last"], p["go"])
        stop = min(start + 500, p["go"] + p["secs"])
        taps, t = [], start + rng.uniform(0.6, 1.4) * 1000 / rate
        while t < stop:
            taps.append(round(t, 1))
            t += rng.uniform(0.6, 1.4) * 1000 / rate
        return {"taps": taps, "end": not taps or stop >= p["go"] + p["secs"] - 1}


class Stack(Game):
    """The classic stacker: a gold bar slides back and forth over the stack and a tap drops it; whatever hangs over
    the bar under it is chopped off, so the next one's that much narrower, and they speed up. Dropped within SNAP of
    dead on, it counts as dead on. The speeds and where each bar sets off from are drawn up here; the page sends each
    tap's time and the stack is replayed from them."""
    key, role, title = "stack", "muscle", "STACK THE GOLD"
    pace = (0.8, 1.6)
    # bars, the first and last bar's speed, and how wide the first is (the track's 100)
    SIZES = {"s": (6, 68, 96, 40), "m": (8, 66, 104, 40), "big": (10, 64, 116, 40)}
    TRACK, SNAP = 100.0, 2.5                     # the track's width, and how near counts as dead on
    GO, GAP = 1200, 450                          # ms before the first bar moves, and after each drop before the next

    def make(self, rng, size, story):
        n, v0, v1, width = _sized(self.SIZES, size)
        speeds = [round(v0 + (v1 - v0) * i / (n - 1), 1) for i in range(n)]
        sides = [rng.randrange(2) for _ in range(n)]          # 0 sets off from the left, 1 from the right
        return {"speeds": speeds, "sides": sides, "bars": [[(self.TRACK - width) / 2, width]], "taps": [], "missed": False}

    def _start(self, p):
        return self.GO if not p["taps"] else p["taps"][-1] + self.GAP

    def _at(self, p, w, t):
        """Where the bar now sliding is (its left edge) at t ms, w wide: it bounces between the track's ends."""
        i = len(p["taps"])
        room = self.TRACK - w
        d = p["speeds"][i] * max(0.0, t - self._start(p)) / 1000 % (2 * room)
        x = d if d <= room else 2 * room - d
        return room - x if p["sides"][i] else x

    def public(self, p):
        return {"n": len(p["speeds"]), "speeds": p["speeds"], "sides": p["sides"], "track": self.TRACK,
                "width": p["bars"][0][1], "bars": p["bars"], "start": self._start(p), "missed": p["missed"],
                "gap": self.GAP}

    def move(self, p, body):
        if self.done(p):
            raise Refuse("That's the stack.")
        t = body.get("tap")
        if not _is_time(t) or t < self._start(p) - 60:
            raise Refuse("Wait for the bar.")
        px, pw = p["bars"][-1]
        x = self._at(p, pw, t)
        if abs(x - px) <= self.SNAP:
            x = px
        left, right = max(x, px), min(x + pw, px + pw)
        p["taps"].append(t)
        if right - left <= 0:
            p["missed"] = True
        else:
            p["bars"].append([round(left, 4), round(right - left, 4)])

    def done(self, p):
        return p["missed"] or len(p["taps"]) >= len(p["speeds"])

    def score(self, p):
        stacked = len(p["bars"]) - 1
        if not stacked:
            return 0.0
        return stacked / len(p["speeds"]) * (0.35 + 0.65 * p["bars"][-1][1] / p["bars"][0][1])

    def _dead_on(self, p, start):
        """When the bar now sliding is right over the one under it, for a bar that set off at start."""
        i = len(p["taps"])
        px, pw = p["bars"][-1]
        room = self.TRACK - pw
        return start + (room - px if p["sides"][i] else px) / p["speeds"][i] * 1000

    def solve(self, p):
        q = {**p, "taps": list(p["taps"])}
        out = []
        while len(q["taps"]) < len(q["speeds"]):
            t = round(self._dead_on(q, self._start(q)), 1)
            out.append({"tap": t})
            q["taps"].append(t)
        return out

    def bot(self, p, rng, skill):
        t = self._dead_on(p, self._start(p)) + rng.gauss(0, 25 + 110 * (1 - skill))
        return {"tap": round(max(t, self._start(p)), 1)}


GAMES: dict[str, Game] = {g.key: g for g in (Weight(), Pack(), Cash(), Dye(), Mash(), Stack())}
