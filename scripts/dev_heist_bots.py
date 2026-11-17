"""A stand-in crew for testing Bank Job on this machine, run only by scripts/dev_activity_api.py (the live bot
never imports it). A few pretend players join any crew a tester opens, take a free job, vote on the way in,
and quiet or loud (mostly quiet), then go through the job doing what's theirs (sometimes stepping in for Terry in
a quiet one), read (or skip) each run-down, play each puzzle or loud task about as well as a middling person
would, grab at the vault's piles a few times a second and leg it at a take they picked going in.

They go through heist's own functions, the same ones the page's moves reach, so everything they do is a real
move in a real job. DEV_HEIST_BOTS sets how big they fill a crew to (4 by default, 0 for none)."""

import asyncio
import heapq
import itertools
import os
import random
import time

BOTS = {4545: "Daz", 4646: "Kev", 4747: "Shaz", 4848: "Ronnie", 4949: "Tina"}
FILL = int(os.environ.get("DEV_HEIST_BOTS", "4"))
_codes: dict[tuple[int, int], list[str]] = {}


def codes(n: int, symbols: int) -> list[str]:
    if (n, symbols) not in _codes:
        _codes[(n, symbols)] = ["".join(c) for c in itertools.product("123456789"[:symbols], repeat=n)]
    return _codes[(n, symbols)]
# the safe's hints, as how far off the number is: (nearest, furthest)
GAPS = {"cold": (16, 99), "warm": (7, 15), "hot": (2, 6)}      # how far off each word means (heist._move)


class Mind:
    """One bot in one job: how good it is, how greedy, and when it next does something."""

    def __init__(self, rng: random.Random, cap: float):
        self.rng = rng
        self.skill = rng.uniform(0.45, 0.9)
        style = rng.random()
        goal = rng.uniform(1.2, 1.8) if style < 0.4 else rng.uniform(1.8, 3.0) if style < 0.8 else rng.uniform(3.0, cap)
        self.goal = min(goal, cap * 0.95)
        self.next = 0.0

    def wait(self, now: float, lo: float, hi: float) -> None:
        self.next = now + self.rng.uniform(lo, hi)


def _hacker(heist, p, m: Mind) -> dict:
    every = codes(p.get("len", 4), p.get("symbols", 6))
    left = [c for c in every if all(heist._feedback(c, g["code"]) == (g["exact"], g["near"]) for g in p["guesses"])]
    return {"code": m.rng.choice(left) if left and m.rng.random() < 0.9 else m.rng.choice(every)}


def _safecracker(p, m: Mind) -> dict:
    lo, hi = 0, 99
    for d in p["dials"]:
        if d["t"] != p["open"] or d["said"] == "click":
            continue
        near, far = GAPS[d["said"]]
        if d["way"] == 1:
            lo, hi = max(lo, d["n"] + near), min(hi, d["n"] + far)
        else:
            lo, hi = max(lo, d["n"] - far), min(hi, d["n"] - near)
    if lo > hi:
        lo, hi = 0, 99
    n = (lo + hi) // 2 + round(m.rng.uniform(-1, 1) * (1 - m.skill) * 8)
    return {"n": max(0, min(99, n))}


def _lookout(p, m: Mind) -> dict:
    return {"seq": [x if m.rng.random() < min(0.97, m.skill + 0.3) else m.rng.randrange(9) for x in p["seq"]]}


def _wheelman(heist, p, m: Mind) -> dict:
    """The cheapest route, worked out on a slightly wrong idea of the traffic."""
    costs = p["costs"]
    g = len(costs)
    seen = {(r, c): (costs[r][c] or 0) + m.rng.uniform(0, (1 - m.skill) * 3) for r in range(g) for c in range(g)}
    start, end = (g - 1, 0), (0, g - 1)
    best, came, todo = {start: 0.0}, {}, [(0.0, start)]
    while todo:
        d, (r, c) = heapq.heappop(todo)
        if (r, c) == end:
            break
        for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= nr < g and 0 <= nc < g and costs[nr][nc] is not None:
                nd = d + seen[(nr, nc)]
                if nd < best.get((nr, nc), 1e9):
                    best[(nr, nc)], came[(nr, nc)] = nd, (r, c)
                    heapq.heappush(todo, (nd, (nr, nc)))
    path, at = [list(end)], end
    while at != start:
        at = came[at]
        path.append(list(at))
    return {"path": path[::-1]}


def _face(p, m: Mind) -> dict:
    if not p.get("ready", True):
        return {"ready": True}
    q = p["questions"][len(p["answers"])]
    if m.rng.random() < m.skill + 0.1:
        return {"answer": q["answer"]}
    return {"answer": m.rng.choice([i for i in range(3) if i != q["answer"]])}


def _muscle(p, m: Mind) -> dict:
    noise = 1 - m.skill
    order = sorted(range(len(p["bags"])), key=lambda i: -p["bags"][i]["v"] / p["bags"][i]["w"] * m.rng.uniform(1 - noise, 1 + noise))
    load, w = [], 0
    for i in order:
        if w + p["bags"][i]["w"] <= p["cap"]:
            load.append(i)
            w += p["bags"][i]["w"]
    return {"load": load}


def _move(heist, role: str, p: dict, m: Mind) -> dict:
    g = heist.heist_games.get(role, p.get("game"))
    if g:
        return g.bot(p, m.rng, m.skill)
    if role == "hacker":
        return _hacker(heist, p, m)
    if role == "safecracker":
        return _safecracker(p, m)
    if role == "lookout":
        return _lookout(p, m)
    if role == "wheelman":
        return _wheelman(heist, p, m)
    if role == "face":
        return _face(p, m)
    return _muscle(p, m)


# how long a bot takes over each move, by job (seconds): like someone thinking, not instant
PACE = {"hacker": (2.5, 5), "safecracker": (1.5, 3), "lookout": (8, 12), "wheelman": (8, 16), "face": (2.5, 4.5), "muscle": (7, 14)}


def tick(heist, humans: set[int], minds: dict, joins: dict, rng: random.Random, now: float) -> None:
    heist._load()
    rooms = [r for r in heist._rooms.values() if not r.get("over")]
    # join any crew a tester's getting together, one bot at a time, until it's FILL strong
    for r in rooms:
        if (r["state"] != "lobby" or r.get("practice") or r["host"] not in humans
                or len(r["players"]) >= min(FILL, heist.SEATS)):
            continue
        if now < joins.setdefault(r["id"], now + rng.uniform(1.0, 2.0)):
            continue
        free = [b for b in BOTS if heist._seated(b) is None]
        if free:
            heist.join(rng.choice(free), r["id"])
            joins[r["id"]] = now + rng.uniform(0.8, 2.0)
    for r in rooms:
        if r["state"] == "lobby":
            continue
        for uid in [u for u in r["players"] if u in BOTS and u not in r["left"]]:
            key = (r["id"], uid)
            if key not in minds:
                minds[key] = Mind(rng, r.get("cap") or 3.0)
                minds[key].wait(now, 1.5, 4)
            m = minds[key]
            try:
                if r["state"] == "plan" and str(uid) not in r["votes"] and now >= m.next:
                    ways = list(heist.rooms_of.routes(r["target"]))
                    heist.vote(uid, r["id"], ways[0] if rng.random() < 0.5 else rng.choice(ways),
                               "loud" if rng.random() < 0.25 else "quiet")
                    m.wait(now, 2, 4)
                elif r["state"] == "job" and now >= r["arrive"] and now >= m.next:
                    mine = heist._current(r, uid)
                    if mine is None:
                        # nothing of theirs on: now and then, help Terry out (a quiet job)
                        here = [th for i, th in heist._things(r) if i == r["at"]]
                        lonely = [th for th in here if th["state"] == "terry"]
                        if lonely and rng.random() < 0.25:
                            heist.play(uid, r["id"], {"ob": rng.choice(lonely)["id"], "step": True})
                        m.wait(now, 2, 4)
                    elif now < mine.get("go", 0):
                        if rng.random() < 0.5:                   # reads the run-down, or knows the drill already
                            heist.play(uid, r["id"], {"ob": mine["id"], "go": True})
                        m.wait(now, 1, 3)
                    else:
                        heist.play(uid, r["id"], {"ob": mine["id"], **_move(heist, mine["role"], mine["p"], m)})
                        g = heist.heist_games.get(mine["role"], mine["p"].get("game"))
                        m.wait(now, *(g.pace if g else PACE[mine["role"]]))
                elif r["state"] == "vault":
                    v = r["vault"]
                    t = now - v["start"]
                    if str(uid) not in v["out"] and t >= 0:
                        # a grab or two since the last tick (every 0.4s), so the bag keeps up with the piles
                        taps = [t - 0.25, t] if rng.random() < 0.9 else []
                        if t >= v["safe"] and heist.pile_at(v, r["cap"], t) >= m.goal:
                            heist.leg_it(uid, r["id"], taps)
                        elif taps:
                            heist.grab(uid, r["id"], taps)
            except heist.Refuse:
                m.wait(now, 1, 2)
    for key in [k for k in minds if k[0] not in heist._rooms or heist._rooms[k[0]].get("over")]:
        del minds[key]
    for rid in [k for k in joins if k not in heist._rooms or heist._rooms[k]["state"] != "lobby"]:
        del joins[rid]


async def run(heist, humans: set[int], every: float = 0.4) -> None:
    if FILL <= 1:
        return
    minds: dict = {}
    joins: dict = {}
    rng = random.Random()
    while True:
        await asyncio.sleep(every)
        try:
            tick(heist, humans, minds, joins, rng, time.time())
        except Exception as e:                                   # a dev helper: say so and carry on
            print(f"heist bots: {e!r}", flush=True)
