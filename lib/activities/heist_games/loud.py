"""A loud job's things: no puzzles, a quick bit of action for each, scored on how well it's done. Three kinds,
dressed up for each role (the page draws each its own way):

    strike  a marker swings along a bar; tap while it's in the zone, a few times, quicker each time
            (the Hacker fries a panel, the Safecracker drills a lock, the Wheelman rams a gate)
    pop     things pop up round a 3x3 grid; tap the right ones before they duck away and leave the rest
            (the Lookout smashes cameras and leaves the pigeons, the Face floors whoever reaches for the alarm)
    mash    tap GRAB as fast as you can till the time's up (the Muscle's smash and grab)

Each is a schedule the page has to see in full to play it (where the marker is, what pops up when), so nothing in
them is secret; the page sends each tap's time and the bot judges it against the same schedule. A page doctored to
tap perfectly scores 1, and a loud job's edge (heist.loud_edge) never comes down as far as a perfect quiet job's.
Every loud thing's key is "loud" (heist_games.get() finds the role's one here); the loose cash a player picks up once
their own tasks are done is "loose-atm", "loose-till" or "loose-box" (LOOSE)."""

from lib.activities.heist_games import Game, Refuse
from lib.activities.heist_games.pace import paced


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == x and abs(x) < 1e9


def _tri(x: float) -> float:
    """A marker going 0 -> 1 -> 0 -> 1..., x in half-sweeps."""
    x = x % 2.0
    return x if x <= 1.0 else 2.0 - x


class Strike(Game):
    key, pace = "loud", (0.8, 2.0)
    ROUNDS = {"s": 3, "m": 3, "big": 4}
    LIMIT = 6                       # half-sweeps a round waits before it's a miss

    def __init__(self, role: str, title: str, key: str = "loud"):
        self.role, self.title, self.key = role, title, key

    def make(self, rng, size, story):
        n = self.ROUNDS.get(size, 3)
        rounds = []
        for k in range(n):
            half = round(1000 * (0.95 - 0.13 * k) * rng.uniform(0.92, 1.08))
            w = round(max(0.1, 0.2 - 0.025 * k), 3)
            rounds.append({"half": half, "c": round(rng.uniform(0.25 + w / 2, 0.95 - w / 2), 3), "w": w})
        return {"kind": "strike", "rounds": rounds, "hits": []}

    def public(self, p):
        return {"kind": "strike", "rounds": p["rounds"], "hits": p["hits"], "limit": self.LIMIT}

    @staticmethod
    def judge(r: dict, ms: float) -> float:
        d = abs(_tri(ms / r["half"]) - r["c"])
        if d <= r["w"] / 2:
            return round(1 - 0.4 * d / (r["w"] / 2), 3)
        return round(max(0.0, 0.6 - 3 * (d - r["w"] / 2)), 3)

    def move(self, p, body):
        k = len(p["hits"])
        if body.get("round", k) != k:
            raise Refuse("That go's been and gone.")
        r = p["rounds"][k]
        if body.get("slow") is True:
            p["hits"].append(0.0)
            return
        ms = body.get("tap")
        if not _num(ms) or ms < 0:
            raise Refuse("That tap needs its time.")
        p["hits"].append(0.0 if ms > self.LIMIT * r["half"] else self.judge(r, ms))

    def done(self, p):
        return len(p["hits"]) >= len(p["rounds"])

    def score(self, p):
        return sum(p["hits"]) / len(p["rounds"])

    def solve(self, p):
        return [{"tap": r["half"] * r["c"], "round": i} for i, r in enumerate(p["rounds"]) if i >= len(p["hits"])]

    def bot(self, p, rng, skill):
        k = len(p["hits"])
        r = p["rounds"][k]
        return {"tap": max(0.0, r["half"] * (r["c"] + rng.gauss(0, (1 - skill) * 0.25))), "round": k}


class Pop(Game):
    key, pace = "loud", (1.0, 2.0)
    EVENTS = {"s": (7, 5), "m": (8, 6), "big": (10, 7)}       # things that pop up, how many of them to tap

    def __init__(self, role: str, title: str, key: str = "loud"):
        self.role, self.title, self.key = role, title, key

    def make(self, rng, size, story):
        n, good = self.EVENTS.get(size, self.EVENTS["s"])
        kinds = [True] * good + [False] * (n - good)
        rng.shuffle(kinds)
        events, t, busy = [], 700, {}
        for i, ok in enumerate(kinds):
            dur = round(1150 - 35 * i)
            cell = rng.choice([c for c in range(9) if busy.get(c, 0) < t])
            busy[cell] = t + dur + 200
            events.append({"t": t, "dur": dur, "cell": cell, "ok": ok})
            t += rng.randint(520, 820)
        return {"kind": "pop", "events": events, "end": events[-1]["t"] + events[-1]["dur"] + 300, "hit": [], "bad": [],
                "last": 0, "over": False}

    def public(self, p):
        return {"kind": "pop", "events": p["events"], "end": p["end"], "hit": p["hit"], "bad": p["bad"], "over": p["over"]}

    def move(self, p, body):
        taps = body.get("taps", [])
        if not isinstance(taps, list) or len(taps) > 40:
            raise Refuse("Tap them.")
        for tap in taps:
            if not (isinstance(tap, list) and len(tap) == 2 and isinstance(tap[0], int) and _num(tap[1])):
                raise Refuse("Tap them.")
            cell, ms = tap
            if ms < p["last"] or ms > p["end"]:
                continue
            p["last"] = ms
            for i, e in enumerate(p["events"]):
                if e["cell"] == cell and e["t"] <= ms <= e["t"] + e["dur"] and i not in p["hit"] and i not in p["bad"]:
                    (p["hit"] if e["ok"] else p["bad"]).append(i)
                    break
        if body.get("end") is True:
            p["over"] = True

    def done(self, p):
        return p["over"]

    def score(self, p):
        good = sum(1 for e in p["events"] if e["ok"])
        return max(0.0, min(1.0, (len(p["hit"]) - 0.5 * len(p["bad"])) / good))

    def solve(self, p):
        return [{"taps": [[e["cell"], e["t"] + e["dur"] / 2] for e in p["events"] if e["ok"]], "end": True}]

    def bot(self, p, rng, skill):
        taps = [[e["cell"], e["t"] + e["dur"] / 2] for e in p["events"] if (e["ok"] and rng.random() < skill)
                or (not e["ok"] and rng.random() > skill + 0.3)]
        return {"taps": taps, "end": True}


class Mash(Game):
    key, pace = "loud", (1.0, 2.0)
    SECS = {"s": 6, "m": 7, "big": 8}
    RATE = 7                         # taps a second for full marks (pace.py holds them to one thumb's 12 a second at
                                     # most, two hands at it or the space bar and the mouse together no faster)

    def __init__(self, role: str, title: str, key: str = "loud"):
        self.role, self.title, self.key = role, title, key

    def make(self, rng, size, story):
        return {"kind": "mash", "secs": self.SECS.get(size, 6), "n": 0, "last": 0, "over": False}

    def public(self, p):
        return {"kind": "mash", "secs": p["secs"], "n": p["n"], "need": p["secs"] * self.RATE, "over": p["over"]}

    def move(self, p, body):
        taps = body.get("taps", [])
        if not isinstance(taps, list) or len(taps) > 200 or not all(_num(t) for t in taps):
            raise Refuse("Mash it.")
        for ms in taps:
            if ms < p["last"] or ms > p["secs"] * 1000 or not paced(p, ms):
                continue
            p["last"] = ms
            p["n"] += 1
        if body.get("end") is True:
            p["over"] = True

    def done(self, p):
        return p["over"]

    def score(self, p):
        return min(1.0, p["n"] / (p["secs"] * self.RATE)) ** 1.3

    def solve(self, p):
        step = 1000 / self.RATE
        return [{"taps": [i * step + 1 for i in range(p["secs"] * self.RATE)], "end": True}]

    def bot(self, p, rng, skill):
        rate = self.RATE * min(1.0, skill + 0.15)
        return {"taps": [i * 1000 / rate + 1 for i in range(int(p["secs"] * rate))], "end": True}


LOUD = {
    "hacker": Strike("hacker", "FRY THE PANEL"),
    "safecracker": Strike("safecracker", "DRILL THE LOCK"),
    "wheelman": Strike("wheelman", "RAM IT"),
    "lookout": Pop("lookout", "SMASH THE CAMERAS"),
    "face": Pop("face", "EVERYONE DOWN!"),
    "muscle": Mash("muscle", "SMASH AND GRAB"),
}


# loose cash: a loud job's bonus tasks for anyone whose own are done, until the crew goes into the vault
LOOSE = {
    "atm": Strike("loose", "CRACK THE ATM", "loose-atm"),
    "till": Mash("loose", "EMPTY THE TILL", "loose-till"),
    "box": Pop("loose", "SNATCH THE CASH", "loose-box"),
}
LOOSE_NAMES = {"atm": "AN ATM", "till": "A TILL", "box": "A CASH BOX"}
