"""Paperboy's rules, the bot's copy: the day's street and a run replayed from the moves the page
recorded, so the score paid is the one the rules give, not one the page claims. It plays like
Subway Surfers: three lanes, hop the low things, duck the high ones, go round the big ones (or take
the ramp over them), ride through the papers, and pick up the odd power-up on the way.

This mirrors src/paperboy/sim.ts in ukplace-activities line for line, drawing the same random
numbers in the same order. Everything is whole numbers (millimetres up the street, centimetres
across and up) apart from the seeded random, which both copies share (score_limits.rng), so the
two land on exactly the same result. Change one, change the other, and re-record the parity runs
in tests/data/paperboy_vectors.json (ukplace-activities scripts/paperboy-vectors.ts).
"""

from __future__ import annotations

from lib.activities.score_limits import rng

HZ = 120
LANES = (-160, 0, 160)
HOUSE = 4200
HOP = 72
DUCK = 72
HOP_TOP = 150
STEER = 12
PAPER, GOLDEN = 1, 10
PER_POINT = 10000
JUMP = 22000
JUMP_TOP = 420
MAGNET = 1200
DOUBLE = 1800
SAFE = 90
LEFT, RIGHT, UP, DOWN = 1, 2, 3, 4
MAX_INPUTS = 20_000

OB = {
    "bin": (34, 380, "low"), "barrier": (72, 300, "low"), "cones": (60, 380, "low"), "dog": (22, 380, "low"),
    "scaffold": (75, 200, "high"), "washing": (75, 200, "high"), "ladder": (75, 300, "high"),
    "van": (80, 2500, "block"), "bus": (78, 5000, "block"), "float": (82, 1800, "block"), "cab": (88, 1000, "block"),
}


def speed_at(step: int) -> int:
    return min(150, 80 + step // 240)


def hop_height(k: int) -> int:
    return 0 if k < 0 or k > HOP else (4 * HOP_TOP * k * (HOP - k)) // (HOP * HOP)


def jump_height(s: int) -> int:
    return 0 if s < 0 or s > JUMP else (4 * JUMP_TOP * s * (JUMP - s)) // (JUMP * JUMP)


class Street:
    def __init__(self, seed: str):
        self.houses: list[dict] = []
        self.obs: list[dict] = []
        self.papers: list[dict] = []
        self.powers: list[dict] = []
        self.ramps: list[dict] = []
        self.rh = rng(f"paperboy:houses:{seed}")
        self.ro = rng(f"paperboy:road:{seed}")
        self.built_to = [0, 0]
        self.since_gap = [0, 0]
        self.ob_to = 36000
        self.next_id = 1

    def ensure(self, d: int) -> None:
        self._houses_to(d + 10 * HOUSE)
        while self.ob_to < d:
            self._beat()

    def _houses_to(self, d: int) -> None:
        while self.built_to[0] < d or self.built_to[1] < d:
            s = 0 if self.built_to[0] <= self.built_to[1] else 1
            self._house(-1 if s == 0 else 1)

    def _house(self, side: int) -> None:
        s, r = (0 if side < 0 else 1), self.rh
        d = self.built_to[s]
        index = d // HOUSE
        roll = r()
        if self.since_gap[s] >= 9 and roll < 0.12:
            for k in range(2):
                self.houses.append({"side": side, "index": index + k, "d": d + k * HOUSE, "kind": "gap", "tint": 0, "lamp": False})
            self.built_to[s] += 2 * HOUSE
            self.since_gap[s] = 0
            corner = "shop" if r() < 0.5 else "pub"
            for h in self.houses:
                if h["side"] == side and h["d"] == d - HOUSE:
                    h["kind"] = corner
            return
        kind = ("a", "b", "c", "d")[int(r() * 4)]
        tint = int(r() * 6)
        lamp = r() < 0.35
        self.houses.append({"side": side, "index": index, "d": d, "kind": kind, "tint": tint, "lamp": lamp})
        self.built_to[s] += HOUSE
        self.since_gap[s] += 1

    def _gap_near(self, d0: int, d1: int) -> dict | None:
        for h in self.houses:
            if h["kind"] != "gap" or h["d"] < d0 or h["d"] >= d1:
                continue
            if any(p["side"] == h["side"] and p["kind"] == "gap" and p["d"] == h["d"] - HOUSE for p in self.houses):
                continue
            if any(o["kind"] == "cab" and abs(o["d"] - (h["d"] + HOUSE)) < HOUSE * 2 for o in self.obs):
                continue
            return h
        return None

    def _add(self, kind: str, d: int, x: int, from_: int = 0, direction: int = 0) -> None:
        hw, hl, height = OB[kind]
        self.obs.append({"id": self.next_id, "kind": kind, "d": d, "x": x, "hw": hw, "hl": hl, "height": height,
                         "trig": -1, "from_": from_, "dir": direction, "hit": False})
        self.next_id += 1

    def _ramp(self, d: int, x: int, golden: bool) -> None:
        self.ramps.append({"id": self.next_id, "d": d, "x": x})
        self.next_id += 1
        for k in range(9):
            s = 2000 + k * 2250
            self.papers.append({"id": self.next_id, "d": d + s, "x": x, "h": jump_height(s), "golden": golden and k == 4, "taken": False})
            self.next_id += 1

    def _line(self, d: int, x: int, n: int, arc: bool) -> None:
        for k in range(n):
            off = (k * 2 - (n - 1)) * 600
            h = max(0, HOP_TOP - (off * off) // 30000) if arc else 0
            self.papers.append({"id": self.next_id, "d": d + off, "x": x, "h": h, "golden": False, "taken": False})
            self.next_id += 1

    def _beat(self) -> None:
        r, d = self.ro, self.ob_to
        self._houses_to(d + 40000)
        hard = min(1000, d // 800)
        pick = r()
        lane = int(r() * 3)
        gap = self._gap_near(d, d + 16000)
        extra = 0
        if gap is not None and pick < 0.4:
            side = gap["side"]
            self._add("cab", gap["d"] + HOUSE, side * 400, side * 400, -side)
            self._line(d + 6000, LANES[1], 6, False)
        elif pick < 0.3 or hard < 120:
            which = r()
            if which < 0.35:
                kind = "bin" if r() < 0.5 else "cones"
            elif which < 0.6:
                kind = "scaffold" if r() < 0.5 else "washing"
            elif which < 0.8:
                kind = "barrier"
            else:
                kind = "van"
            self._add(kind, d, LANES[lane])
            if OB[kind][2] == "low" and r() < 0.6:
                self._line(d, LANES[lane], 5, True)
            else:
                to = LANES[(lane + 1 + int(r() * 2)) % 3]
                self._line(d + 8000, to, 5 + int(r() * 4), False)
        elif pick < 0.55:
            wall = hard >= 250 and r() < 0.3
            open_ = -1 if wall else int(r() * 3)
            for k in range(3):
                if k != open_:
                    self._add("van" if r() < 0.5 else "bus", d + (1500 if k == 2 else 0), LANES[k])
            if not wall:
                self._line(d - 3000, LANES[open_], 7, False)
            if wall or r() < 0.4:
                rl = lane if wall else (open_ + 1 + int(r() * 2)) % 3
                self._ramp(d + (1500 if rl == 2 else 0) - 11000, LANES[rl], wall)
                extra = 12000
        elif pick < 0.68:
            low = r() < 0.5
            for k in range(3):
                self._add(("barrier" if k == 1 else "bin") if low else ("ladder" if k == 1 else "scaffold"), d, LANES[k])
            if low:
                self._line(d, LANES[lane], 5, True)
        elif pick < 0.78:
            kinds = ("bin", "washing", "van")
            for k in range(3):
                self._add(kinds[(k + lane) % 3], d, LANES[k])
        elif pick < 0.88:
            direction = 1 if r() < 0.5 else -1
            self._add("dog", d, -direction * 330, -direction * 330, direction)
            self._line(d + 5000, LANES[lane], 6, False)
        elif hard > 200:
            self._add("float", d + 30000, LANES[lane])
            self._line(d, LANES[(lane + 1) % 3], 8, False)
        else:
            self._add("bin", d, LANES[lane])
            self._line(d + 6000, LANES[(lane + 2) % 3], 6, False)
        if r() < 0.08:
            x = LANES[int(r() * 3)]
            self.papers.append({"id": self.next_id, "d": d + 12000, "x": x, "h": 0, "golden": True, "taken": False})
            self.next_id += 1
        if extra == 0 and d > 120000 and r() < 0.16:
            roll, x, pd = r(), LANES[int(r() * 3)], d + 9500
            kind = "magnet" if roll < 0.4 else "double" if roll < 0.7 else "helmet"
            if not any(o["from_"] == 0 and o["x"] == x and abs(o["d"] - pd) < o["hl"] + 1500 for o in self.obs):
                self.powers.append({"id": self.next_id, "d": pd, "x": x, "kind": kind, "taken": False})
                self.next_id += 1
        self.ob_to = d + 14000 + int(r() * 8000) + min(14000, d // 40) - hard * 5 + extra


class Run:
    def __init__(self, seed: str):
        self.street = Street(seed)
        self.street.ensure(200000)
        self.step = 0
        self.dist = 0
        self.x = 0
        self.lane = 1
        self.hop_at = -1000
        self.duck_at = -1000
        self.jump_from = -1
        self.papers = 0
        self.helmet = False
        self.magnet_to = -1
        self.double_to = -1
        self.safe_to = -1
        self.over = False
        self.pending: list[int] = []
        # (not in the page's copy, and no change to the result: only what's within reach is looked at,
        # refreshed every second, so a long run doesn't slow down as the road behind it piles up)
        self._near: list[dict] = []
        self._near_papers: list[dict] = []
        self._near_ramps: list[dict] = []
        self._near_powers: list[dict] = []
        self._near_at = -HZ

    @property
    def score(self) -> int:
        return self.dist // PER_POINT + self.papers

    def input(self, code: int) -> None:
        if not self.over:
            self.pending.append(code)

    def height(self) -> int:
        return jump_height(self.dist - self.jump_from) if self.jumping() else hop_height(self.step - self.hop_at)

    def jumping(self) -> bool:
        return self.jump_from >= 0 and self.dist - self.jump_from <= JUMP

    def airborne(self) -> bool:
        return self.height() >= 40

    def ducking(self) -> bool:
        k = self.step - self.duck_at
        return 4 <= k <= DUCK

    def ob_x(self, o: dict) -> int:
        if o["kind"] == "dog":
            return o["from_"] if o["trig"] < 0 else o["from_"] + o["dir"] * 3 * (self.step - o["trig"])
        if o["kind"] == "cab":
            if o["trig"] < 0:
                return o["from_"]
            moved = o["from_"] + o["dir"] * 2 * (self.step - o["trig"])
            return max(LANES[2], moved) if o["dir"] < 0 else min(LANES[0], moved)
        return o["x"]

    def ob_d(self, o: dict) -> int:
        return o["d"] - 40 * (self.step - o["trig"]) if o["kind"] == "float" and o["trig"] >= 0 else o["d"]

    def tick(self) -> None:
        if self.over:
            return
        for code in self.pending:
            self._apply(code)
        self.pending = []
        self.step += 1
        was = self.dist
        self.dist += speed_at(self.step)
        self.street.ensure(self.dist + 200000)
        self.x += max(-STEER, min(STEER, LANES[self.lane] - self.x))
        if self.step - self._near_at >= HZ:
            self._near = [o for o in self.street.obs if o["d"] >= self.dist - 40000]
            self._near_papers = [p for p in self.street.papers if p["d"] >= self.dist - 40000 and not p["taken"]]
            self._near_ramps = [rp for rp in self.street.ramps if rp["d"] >= self.dist - 40000]
            self._near_powers = [pw for pw in self.street.powers if pw["d"] >= self.dist - 40000 and not pw["taken"]]
            self._near_at = self.step
        for o in self._near:
            if o["trig"] >= 0:
                continue
            reach = {"dog": 16000, "cab": 24000, "float": 48000}.get(o["kind"], -1)
            if reach > 0 and self.dist >= o["d"] - reach:
                o["trig"] = self.step
        for rp in self._near_ramps:
            if self.jumping() or rp["d"] <= was or rp["d"] > self.dist or abs(self.x - rp["x"]) >= 60:
                continue
            self.jump_from = rp["d"]
            self.hop_at = -1000
            self.duck_at = -1000
        h = self.height()
        for pw in self._near_powers:
            if pw["taken"] or abs(self.dist - pw["d"]) >= 700 or abs(self.x - pw["x"]) >= 70 or h >= 120:
                continue
            pw["taken"] = True
            if pw["kind"] == "magnet":
                self.magnet_to = self.step + MAGNET
            elif pw["kind"] == "double":
                self.double_to = self.step + DOUBLE
            else:
                self.helmet = True
        pull, times = self.step <= self.magnet_to, 2 if self.step <= self.double_to else 1
        for p in self._near_papers:
            if p["taken"] or p["d"] < self.dist - 1000 or p["d"] > self.dist + 1000:
                continue
            if abs(self.dist - p["d"]) < 700 and (pull or (abs(self.x - p["x"]) < 60 and abs(h - p["h"]) < 60)):
                p["taken"] = True
                self.papers += (GOLDEN if p["golden"] else PAPER) * times
        if self.step <= self.safe_to or h >= 200:
            return
        for o in self._near:
            if o["hit"]:
                continue
            od = self.ob_d(o)
            if od < self.dist - 30000 or od > self.dist + 30000:
                continue
            if abs(self.x - self.ob_x(o)) >= 45 + o["hw"] or abs(self.dist - od) >= 900 + o["hl"]:
                continue
            if o["height"] == "low" and self.airborne():
                continue
            if o["height"] == "high" and self.ducking():
                continue
            if self.helmet:
                self.helmet = False
                o["hit"] = True
                self.safe_to = self.step + SAFE
                return
            self.over = True
            return

    def _apply(self, code: int) -> None:
        if code == LEFT:
            self.lane = max(0, self.lane - 1)
        elif code == RIGHT:
            self.lane = min(2, self.lane + 1)
        elif code == UP:
            if self.height() == 0:
                self.hop_at = self.step
                self.duck_at = -1000
        elif code == DOWN:
            if self.jumping():
                return
            self.hop_at = -1000
            self.duck_at = self.step


def clean_inputs(raw) -> list[list[int]]:
    """The page's moves as [step, code] pairs, dropping anything that isn't one."""
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw[:MAX_INPUTS]:
        if isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in item):
            step, code = item
            if step >= 0 and code in (LEFT, RIGHT, UP, DOWN):
                out.append([step, code])
    return out


def replay(seed: str, inputs: list, max_steps: int) -> dict:
    """Play a recorded run up to a crash or `max_steps`: its score, how long it lasted, its papers."""
    run = Run(seed)
    todo = sorted(clean_inputs(inputs), key=lambda p: p[0])
    i = 0
    while not run.over and run.step < max_steps:
        while i < len(todo) and todo[i][0] <= run.step:
            run.input(todo[i][1])
            i += 1
        run.tick()
    return {"score": run.score, "steps": run.step, "papers": run.papers, "crashed": run.over}
