"""Paperboy's rules, the bot's copy: the day's street and a run replayed from the inputs the page
recorded, so the score paid is the one the rules give, not one the page claims.

This mirrors src/paperboy/sim.ts in ukplace-activities line for line. Everything is whole numbers
(millimetres up the street, centimetres across it) apart from the seeded random, which both copies
share (score_limits.rng), so the two land on exactly the same result. Change one, change the other,
and keep tests/test_paperboy_sim.py (vectors recorded from the page's copy) passing.
"""

from __future__ import annotations

from lib.activities.score_limits import rng

HZ = 120
LANES = (-160, 0, 160)
HOUSE = 4200
START_PAPERS, MAX_PAPERS, BUNDLE = 10, 10, 5
FLIGHT = 108
HOP = 72
LETTERBOX, MAT = 900, 2600
LEFT, RIGHT, HOP_UP, THROW_LEFT, THROW_RIGHT = 1, 2, 3, 4, 5
MAX_INPUTS = 20_000


def speed_at(step: int) -> int:
    return min(134, 70 + step // 300)


class Street:
    def __init__(self, seed: str):
        self.houses: list[dict] = []
        self.obs: list[dict] = []
        self.rh = rng(f"paperboy:houses:{seed}")
        self.ro = rng(f"paperboy:road:{seed}")
        self.built_to = [0, 0]
        self.since_gap = [0, 0]
        self.ob_to = 40000
        self.next_id = 1

    def ensure(self, d: int) -> None:
        while self.built_to[0] < d + 10 * HOUSE or self.built_to[1] < d + 10 * HOUSE:
            s = 0 if self.built_to[0] <= self.built_to[1] else 1
            self._house(-1 if s == 0 else 1)
        while self.ob_to < d:
            self._beat()

    def _house(self, side: int) -> None:
        s, r = (0 if side < 0 else 1), self.rh
        d = self.built_to[s]
        index = d // HOUSE
        roll = r()
        if self.since_gap[s] >= 9 and roll < 0.12:
            for k in range(2):
                self.houses.append({"side": side, "index": index + k, "d": d + k * HOUSE, "kind": "gap", "wants": False,
                                    "door": 0, "delivered": False, "tint": 0})
            self.built_to[s] += 2 * HOUSE
            self.since_gap[s] = 0
            for h in self.houses:
                if h["side"] == side and h["d"] == d - HOUSE:
                    h["kind"], h["wants"] = "shop", False
            return
        kind = ("brick", "render", "painted")[int(r() * 3)]
        wants = index >= 3 and r() < 0.32
        tint = int(r() * 6)
        door = d + HOUSE // 2 + (1220 if side < 0 else -1220)
        self.houses.append({"side": side, "index": index, "d": d, "kind": kind, "wants": wants, "door": door,
                            "delivered": False, "tint": tint})
        self.built_to[s] += HOUSE
        self.since_gap[s] += 1

    def _gap_near(self, d0: int, d1: int) -> dict | None:
        for h in self.houses:
            if h["kind"] != "gap" or h["d"] < d0 or h["d"] >= d1:
                continue
            if any(p["side"] == h["side"] and p["kind"] == "gap" and p["d"] == h["d"] - HOUSE for p in self.houses):
                continue
            if any(o["kind"] == "car" and abs(o["d"] - (h["d"] + HOUSE)) < HOUSE * 2 for o in self.obs):
                continue
            return h
        return None

    def _add(self, **o) -> None:
        o.setdefault("trig", -1)
        o.setdefault("from_", 0)
        o.setdefault("dir", 0)
        o["id"] = self.next_id
        self.next_id += 1
        self.obs.append(o)

    def _beat(self) -> None:
        r, d = self.ro, self.ob_to
        while self.built_to[0] < d + 40000 or self.built_to[1] < d + 40000:
            s = 0 if self.built_to[0] <= self.built_to[1] else 1
            self._house(-1 if s == 0 else 1)
        hard = min(1000, d // 600)
        pick = r()
        lane = int(r() * 3)
        gap = self._gap_near(d, d + 16000)
        if gap is not None and pick < 0.55:
            side = gap["side"]
            self._add(kind="car", d=gap["d"] + HOUSE, x=side * 400, hw=88, hl=2000, low=False, from_=side * 400, dir=-side)
        elif pick < 0.3:
            self._add(kind="bin", d=d, x=LANES[lane], hw=34, hl=380, low=True)
        elif pick < 0.45 and hard > 150:
            free = int(r() * 3)
            for k in range(3):
                if k != free:
                    self._add(kind="bins", d=d + (k % 2) * 500, x=LANES[k], hw=34, hl=380, low=True)
        elif pick < 0.6:
            direction = 1 if r() < 0.5 else -1
            self._add(kind="dog", d=d, x=-direction * 330, hw=22, hl=380, low=True, from_=-direction * 330, dir=direction)
        elif pick < 0.75 and hard > 80:
            self._add(kind="cones", d=d, x=LANES[lane], hw=42, hl=300, low=True)
            if r() < 0.5:
                self._add(kind="barrier", d=d + 900, x=LANES[(lane + 1) % 3], hw=75, hl=300, low=False)
        elif pick < 0.88 and hard > 250:
            self._add(kind="float", d=d + 30000, x=LANES[lane], hw=82, hl=1800, low=False)
        else:
            self._add(kind="bin", d=d, x=LANES[lane], hw=34, hl=380, low=True)
        if r() < 0.3:
            bl = int(r() * 3)
            blocked = any(d - 2000 <= o["d"] <= d + 2000 and o["x"] == LANES[bl] and o["kind"] != "bundle" for o in self.obs)
            if not blocked:
                self._add(kind="bundle", d=d + 6000, x=LANES[bl], hw=60, hl=500, low=True)
        self.ob_to = d + 13000 + int(r() * 9000) + min(12000, d // 40) - hard * 4


class Run:
    def __init__(self, seed: str):
        self.street = Street(seed)
        self.street.ensure(200000)
        self.step = 0
        self.dist = 0
        self.x = 0
        self.lane = 1
        self.hop_at = -1000
        self.papers = START_PAPERS
        self.score = 0
        self.throws = 0
        self.over = False
        self.flying: list[dict] = []
        self.pending: list[int] = []
        # (not in the page's copy, and no change to the result: only what's within reach is looked at,
        # refreshed every second, so a long run doesn't slow down as the road behind it piles up)
        self._near: list[dict] = []
        self._near_at = -HZ

    def input(self, code: int) -> None:
        if not self.over:
            self.pending.append(code)

    def airborne(self) -> bool:
        k = self.step - self.hop_at
        return 6 <= k <= HOP - 6

    def ob_x(self, o: dict) -> int:
        if o["kind"] == "dog":
            return o["from_"] if o["trig"] < 0 else o["from_"] + o["dir"] * 3 * (self.step - o["trig"])
        if o["kind"] == "car":
            if o["trig"] < 0:
                return o["from_"]
            moved = o["from_"] + o["dir"] * 2 * (self.step - o["trig"])
            return max(LANES[2], moved) if o["dir"] < 0 else min(LANES[0], moved)
        return o["x"]

    def ob_d(self, o: dict) -> int:
        return o["d"] - 33 * (self.step - o["trig"]) if o["kind"] == "float" and o["trig"] >= 0 else o["d"]

    def tick(self) -> None:
        if self.over:
            return
        for code in self.pending:
            self._apply(code)
        self.pending = []
        self.step += 1
        self.dist += speed_at(self.step)
        self.street.ensure(self.dist + 200000)
        self.x += max(-10, min(10, LANES[self.lane] - self.x))
        if self.step - self._near_at >= HZ:
            self._near = [o for o in self.street.obs if o["d"] >= self.dist - 40000]
            self._near_at = self.step
        for o in self._near:
            if o["trig"] >= 0:
                continue
            reach = {"dog": 16000, "car": 22000, "float": 45000}.get(o["kind"], -1)
            if reach > 0 and self.dist >= o["d"] - reach:
                o["trig"] = self.step
        for p in self.flying:
            if p.get("result") is None and self.step >= p["lands"]:
                self._land(p)
        self.flying = [p for p in self.flying if p.get("result") is None or self.step - p["lands"] < 30]
        for o in self._near:
            od = self.ob_d(o)
            if od < self.dist - 30000 or od > self.dist + 30000 or o.get("taken"):
                continue
            if abs(self.x - self.ob_x(o)) >= 45 + o["hw"] or abs(self.dist - od) >= 900 + o["hl"]:
                continue
            if o["kind"] == "bundle":
                o["taken"] = True
                self.papers = min(MAX_PAPERS, self.papers + BUNDLE)
                continue
            if o["low"] and self.airborne():
                continue
            self.over = True
            return

    def _apply(self, code: int) -> None:
        if code == LEFT:
            self.lane = max(0, self.lane - 1)
        elif code == RIGHT:
            self.lane = min(2, self.lane + 1)
        elif code == HOP_UP:
            if self.step - self.hop_at >= HOP:
                self.hop_at = self.step
        elif code in (THROW_LEFT, THROW_RIGHT):
            if self.papers <= 0:
                return
            self.papers -= 1
            self.throws += 1
            self.flying.append({"side": -1 if code == THROW_LEFT else 1, "thrown": self.step, "lands": self.step + FLIGHT})

    def _land(self, p: dict) -> None:
        best, gap = None, MAT + 1
        for h in self.street.houses:
            if h["side"] != p["side"] or not h["wants"] or h["delivered"]:
                continue
            g = abs(h["door"] - self.dist)
            if g < gap:
                gap, best = g, h
        if best is not None and gap <= MAT:
            best["delivered"] = True
            points = 3 if gap <= LETTERBOX else 1
            p["result"] = "letterbox" if gap <= LETTERBOX else "mat"
            self.score += points
        else:
            p["result"] = "miss"


def clean_inputs(raw) -> list[list[int]]:
    """The page's inputs as [step, code] pairs, dropping anything that isn't one."""
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw[:MAX_INPUTS]:
        if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in item):
            step, code = item
            if step >= 0 and code in (LEFT, RIGHT, HOP_UP, THROW_LEFT, THROW_RIGHT):
                out.append([step, code])
    return out


def replay(seed: str, inputs: list, max_steps: int) -> dict:
    """Play a recorded run up to a crash or `max_steps`: its score, how long it lasted, its throws."""
    run = Run(seed)
    todo = sorted(clean_inputs(inputs), key=lambda p: p[0])
    i = 0
    while not run.over and run.step < max_steps:
        while i < len(todo) and todo[i][0] <= run.step:
            run.input(todo[i][1])
            i += 1
        run.tick()
    return {"score": run.score, "steps": run.step, "throws": run.throws, "crashed": run.over}
