"""Bank Job: a heist for up to six, in the activity's Together games.

A crew is opened by its host, who picks the target (the Nether Wallop Post Office, the Little
Puddleton Building Society or the vault of the Bank of HMS Victory itself) and a buy-in; anyone
can take a seat and a role. Once it starts, every buy-in goes into the bank and the job runs:

    plan    the crew votes quiet or loud, which decides whose work matters most and how fast
            the vault fills; a twist of the night (rain, a shift change, a chatty guard) leans
            on one role or another
    job     everyone plays their role's puzzle at once, against one clock: the Hacker cracks a
            keypad, the Safecracker listens for tumblers, the Lookout learns the patrol, the
            Wheelman plots the getaway, the Face blags past the guard on a cover story and the
            Muscle loads the van. A role nobody took is winged by Dodgy Dave.
    report  how well it went, as an alarm level
    vault   the take climbs from 1x towards the target's cap, and each of the crew legs it
            when they like, banking their buy-in times the take at that moment. The police
            turn up at a moment the bot rolled when the vault opened, and whoever's still
            inside is nicked and loses their buy-in; a crew that holds its nerve to the cap
            clears the vault.

The money. The police arrive at take M = (1 - edge) / U for U uniform on (0, 1], so the chance
the take reaches m before they do is (1 - edge) / m, and leaving at m returns m times that:
1 - edge of the buy-in, however long anyone waits. The edge runs from EDGE_WORST for a botched
job down to EDGE_BEST for a perfect one, so playing well is worth something, but even a perfect
crew (or a page that cheats at the puzzles) leaves the bank its EDGE_BEST. Payouts are floored to
whole UKPence, which only ever adds to it. A clean job (a crew of three or more, every one of
them out with at least CLEAN_TAKE) adds a small, once-a-day, reserve-scaled bonus, the one part of
the game that can run in the players' favour, and only by a little: see docs/heist.md for the sums.

Like Countdown, every read moves a crew on to the present and a sweeper does the same for crews
nobody's watching, so a job always finishes. It's all saved after every change, and the clocks
pause while the bot is down: on the next boot every live job's clocks move on by the time it was
away, so nobody is nicked while they couldn't press anything.
"""

import asyncio
import copy
import heapq
import logging
import math
import os
import random
import secrets
import time
from datetime import datetime

import config
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

GAME = "heist"
LABEL = "Bank Job"
SEATS = 6
STAKES = [0, 50, 100, 250, 500, 1000]
LOBBY_SECONDS = 900          # a crew nobody starts breaks up after this
PLAN_SECONDS = 15
JOB_SECONDS = 50
REPORT_SECONDS = 8
KEEP_FINISHED = 1800
VAULT_SECONDS = {"quiet": 30.0, "loud": 20.0}   # from 1x to the cap, before a twist
EDGE_WORST, EDGE_BEST = 0.12, 0.02              # the bank's share for a botched job and a perfect one
WINGING_IT = 0.3             # how Dodgy Dave does at a role nobody took
MIN_CAP = 1.5                # however hot it gets, the vault's worth this much
HEAT_HALF_LIFE = 3 * 3600    # the police calm down by half every three hours
HEAT_PER_JOB = 0.04          # a staked job warms them up a bit...
HEAT_SCALE = 20_000          # ...and a profit this big on top of the buy-ins sets them to boiling
HEAT_CAP_CUT = 0.4           # at full heat the vaults hold 40% less
TIP_BOOST = 1.25             # the fence's tip of the day holds this much more
CLEAN_TAKE = 1.5             # a clean job's crew all got out with at least this much

TARGETS = {
    "post": {"name": "Nether Wallop Post Office", "short": "Post Office", "cap": 3.0, "min_crew": 1, "max_stake": 1000,
             "blurb": "Pensions day. A float, a bell on the door and Maureen behind the counter."},
    "society": {"name": "Little Puddleton Building Society", "short": "Building Society", "cap": 6.0, "min_crew": 2,
                "max_stake": 1000, "blurb": "A proper safe and a security guard called Brian."},
    "vault": {"name": "The Bank of HMS Victory", "short": "The Vault", "cap": 12.0, "min_crew": 3, "max_stake": 500,
              "blurb": "The big one. The server's own vault, under the waterline."},
}

ROLES = ["hacker", "safecracker", "lookout", "wheelman", "face", "muscle"]
ROLE_INFO = {
    "hacker": ("The Hacker", "Crack the keypad"),
    "safecracker": ("The Safecracker", "Feel for the tumblers"),
    "lookout": ("The Lookout", "Learn the patrol"),
    "wheelman": ("The Wheelman", "Plot the getaway"),
    "face": ("The Face", "Blag past the guard"),
    "muscle": ("The Muscle", "Load the van"),
}
# whose work counts for how much, by the crew's approach
WEIGHTS = {
    "quiet": {"hacker": 0.22, "safecracker": 0.22, "lookout": 0.20, "face": 0.20, "wheelman": 0.08, "muscle": 0.08},
    "loud": {"hacker": 0.08, "safecracker": 0.17, "lookout": 0.10, "face": 0.05, "wheelman": 0.30, "muscle": 0.30},
}
TWISTS = [
    {"key": "shift", "name": "Shift change", "text": "The guards swap over at midnight. The Lookout counts double.", "role": "lookout"},
    {"key": "rain", "name": "Chucking it down", "text": "The roads are a mess. The Wheelman counts double.", "role": "wheelman"},
    {"key": "alarm", "name": "New alarm system", "text": "Fitted on Tuesday, by a man called Clive. The Hacker counts double.", "role": "hacker"},
    {"key": "guard", "name": "Brian's on the door", "text": "Brian does love a natter. The Face counts double.", "role": "face"},
    {"key": "gold", "name": "Gold, not notes", "text": "Heavy loot tonight. The Muscle counts double.", "role": "muscle"},
    {"key": "chubb", "name": "A 1962 Chubb", "text": "A proper old safe. The Safecracker counts double.", "role": "safecracker"},
    {"key": "mot", "name": "The panda car's in for its MOT", "text": "The police are slow tonight: the vault fills slower.", "pace": 1.3},
    {"key": "copper", "name": "Off-duty copper in the queue", "text": "Everything happens faster tonight.", "pace": 0.75},
]

_FILE = os.path.join(config.JSON_DATA_DIR, "activity_heist.json")
_rooms: dict[str, dict] = {}
_posts: dict[str, list] = {}       # room id -> [channel, message] of its post
_heat = {"v": 0.0, "at": 0.0}      # the police's mood, and when it was last set
_bonus: dict[str, str] = {}        # uid -> the UK day they last had a clean job bonus
_loaded = False
_board_cache: tuple[float, list] = (0.0, [])
CLIENT = None
listeners: list = []


class Refuse(Exception):
    """Something the rules don't allow; the message is for the player."""


def _cfg(name, default):
    return getattr(config, name, default)


def _rng() -> random.Random:
    return random.Random(secrets.randbits(64))


# ---- keeping it ------------------------------------------------------------------------------

def _load() -> None:
    """Read the crews back in. A job that was running while the bot was down has every clock
    moved on by the time it was away, so it carries on from where everyone left it."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        import json
        with open(_FILE) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        log.error("couldn't read %s; starting with no bank jobs", _FILE, exc_info=True)
        return
    _rooms.update(raw.get("rooms", {}))
    _posts.update(raw.get("posts", {}))
    _heat.update(raw.get("heat", {}))
    _bonus.update(raw.get("bonus", {}))
    saved = float(raw.get("saved", 0) or 0)
    away = time.time() - saved if saved else 0.0
    if away > 1:
        for r in _rooms.values():
            if not r.get("over") and r["state"] != "lobby":
                _shift(r, away)


def _shift(room: dict, by: float) -> None:
    """Move a running job's clocks on by ``by`` seconds (the bot was away that long)."""
    if room.get("deadline"):
        room["deadline"] += by
    v = room.get("vault")
    if v:
        v["start"] += by
    room["paused"] = round(room.get("paused", 0.0) + by, 1)


def _save() -> None:
    try:
        atomic_write_json(_FILE, {"rooms": _rooms, "posts": _posts, "heat": _heat, "bonus": _bonus,
                                  "saved": time.time()})
    except Exception:
        log.error("couldn't save the bank jobs to %s", _FILE, exc_info=True)


def _emit(event: str, room: dict) -> None:
    for fn in listeners:
        try:
            fn(event, copy.deepcopy(room))
        except Exception:
            log.warning("a bank job listener failed on %s", event, exc_info=True)


def post_for(rid: str) -> tuple[int | None, int] | None:
    _load()
    p = _posts.get(rid)
    if p is None:
        return None
    return (p[0], int(p[1]))


def remember_post(rid: str, message_id: int | None, channel: int | None = None) -> None:
    if message_id is None:
        return
    _posts[rid] = [int(channel) if channel else None, int(message_id)]
    _save()


def room_info(rid: str) -> dict | None:
    _load()
    r = _rooms.get(rid)
    return copy.deepcopy(r) if r else None


def active() -> int:
    """Jobs under way (for a restart to wait on)."""
    _load()
    return sum(1 for r in _rooms.values() if not r.get("over") and r["state"] != "lobby")


# ---- the police, the tip and the vault -------------------------------------------------------

def heat(now: float | None = None) -> float:
    """How warm the police are, 0 to 1: up with every job, halving every few hours."""
    now = time.time() if now is None else now
    v, at = float(_heat.get("v", 0.0)), float(_heat.get("at", 0.0) or now)
    return max(0.0, min(1.0, v * 0.5 ** (max(0.0, now - at) / HEAT_HALF_LIFE)))


def heat_label(h: float) -> str:
    return "Lockdown" if h >= 0.75 else "High alert" if h >= 0.5 else "Sniffing about" if h >= 0.25 else "Quiet"


def _warm(by: float, now: float) -> None:
    _heat.update(v=min(1.0, heat(now) + by), at=now)


def _uk_day(now: float | None = None) -> str:
    import pytz
    return datetime.fromtimestamp(time.time() if now is None else now, pytz.timezone("Europe/London")).strftime("%Y-%m-%d")


def tip(now: float | None = None) -> str:
    """The fence's tip of the day: the target holding more than usual, the same for everyone."""
    return random.Random(f"heist-tip-{_uk_day(now)}").choice(list(TARGETS))


def cap_for(target: str, now: float | None = None) -> float:
    """What the vault's take climbs to tonight: the target's own, more if it's the tip, less if the police are hot."""
    t = TARGETS[target]
    cap = t["cap"] * (TIP_BOOST if tip(now) == target else 1.0) * (1 - HEAT_CAP_CUT * heat(now))
    return round(max(MIN_CAP, cap), 2)


def max_stake(target: str) -> int:
    """The biggest buy-in a crew can open at, by target: no more than the target allows, and small
    enough that a full crew clearing a tipped vault takes at most HEIST_EXPOSURE_PCT of what the
    bank holds above its reserve floor. At the floor, jobs are free only."""
    t = TARGETS.get(target, TARGETS["post"])
    top = min(t["max_stake"], int(_cfg("HEIST_MAX_STAKE", 1000)))
    try:
        from lib.economy.reserve_policy import bank_reserves
        spare = max(0, bank_reserves() - int(_cfg("RESERVE_FLOOR", 80_000)))
        afford = spare * float(_cfg("HEIST_EXPOSURE_PCT", 0.15)) / (SEATS * t["cap"] * TIP_BOOST)
        top = min(top, int(afford))
    except Exception:
        log.warning("couldn't read the bank's reserves for the bank job's limit", exc_info=True)
    return max((s for s in STAKES if s <= top), default=0)


def edge(score: float) -> float:
    """The bank's share, from the crew's job score (0 to 1)."""
    return EDGE_WORST - (EDGE_WORST - EDGE_BEST) * max(0.0, min(1.0, score))


def police_take(e: float, u: float) -> float:
    """The take at which the police arrive, for a uniform draw u in (0, 1]."""
    return (1.0 - e) / u


def take_at(t: float, cap: float, secs: float) -> float:
    """The take t seconds into the vault (1x at the door, the cap at the end)."""
    if t <= 0:
        return 1.0
    return min(cap, cap ** (t / secs))


def _floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


# ---- the puzzles -----------------------------------------------------------------------------
# Each role's puzzle is made here and judged here; the page only ever sees its public part.

HACK_LEN, HACK_SYMBOLS, HACK_TRIES = 4, 6, 6
HACK_SCORE = [1.0, 1.0, 1.0, 0.95, 0.85, 0.7]       # by the guess it was cracked on
TUMBLERS, DIALS = 3, 15
LOOKOUT_STEPS = 7
GRID = 6
BAGS = 8
FACE_QUESTIONS = 4

FIRMS = [("Thames Water", "a leak"), ("British Gas", "a gas smell"), ("Openreach", "the broadband"),
         ("Rentokil", "a mouse problem"), ("the council", "a fire door inspection"), ("Otis Lifts", "the lift"),
         ("Dyno-Rod", "the drains"), ("Securicor", "the CCTV")]
FIRST = ["Gary", "Brenda", "Keith", "Sharon", "Derek", "Linda", "Trevor", "Mandy", "Nigel", "Tracey", "Clive", "Denise",
         "Barry", "Julie", "Colin", "Pauline"]
SURNAMES = ["Pratt", "Higgins", "Bloggs", "Parker", "Dobbs", "Wright", "Shaw", "Fletcher", "Clegg", "Platt"]
LOOT = ["Used fivers", "Gold bar", "Krugerrands", "Bearer bonds", "Grandad's medals", "Pound coins", "A Rolex or two",
        "Premium Bonds", "Diamonds", "A bag of 2p coins", "First-class stamps", "The Crown Jewels (replica)"]


def _hacker(rng) -> dict:
    code = "".join(str(rng.randint(1, HACK_SYMBOLS)) for _ in range(HACK_LEN))
    return {"secret": code, "guesses": []}


def _feedback(code: str, guess: str) -> tuple[int, int]:
    exact = sum(a == b for a, b in zip(code, guess))
    common = sum(min(code.count(d), guess.count(d)) for d in set(guess))
    return exact, common - exact


def _safecracker(rng) -> dict:
    return {"secret": [rng.randint(0, 99) for _ in range(TUMBLERS)], "dials": [], "open": 0}


def _lookout(rng) -> dict:
    seq = [rng.randrange(9)]
    while len(seq) < LOOKOUT_STEPS:
        n = rng.randrange(9)
        if n != seq[-1]:
            seq.append(n)
    return {"seq": seq}


def _cheapest(costs: list[list[int | None]], start=(GRID - 1, 0), end=(0, GRID - 1)) -> int | None:
    """The cheapest way across the town, Dijkstra; a cell's cost is paid on entering it."""
    best = {start: 0}
    todo = [(0, start)]
    while todo:
        d, (r, c) = heapq.heappop(todo)
        if (r, c) == end:
            return d
        if d > best.get((r, c), 1 << 30):
            continue
        for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= nr < GRID and 0 <= nc < GRID and costs[nr][nc] is not None:
                nd = d + costs[nr][nc]
                if nd < best.get((nr, nc), 1 << 30):
                    best[(nr, nc)] = nd
                    heapq.heappush(todo, (nd, (nr, nc)))
    return None


def _wheelman(rng) -> dict:
    while True:
        costs = [[rng.choice((1, 1, 2, 2, 3, 4)) for _ in range(GRID)] for _ in range(GRID)]
        for _ in range(7):                      # roadblocks
            r, c = rng.randrange(GRID), rng.randrange(GRID)
            if (r, c) not in ((GRID - 1, 0), (0, GRID - 1)):
                costs[r][c] = None
        costs[GRID - 1][0] = costs[0][GRID - 1] = 1
        best = _cheapest(costs)
        if best is not None:
            return {"costs": costs, "best": best}


def _face(rng) -> dict:
    firm, why = rng.choice(FIRMS)
    story = {"name": f"{rng.choice(FIRST)} {rng.choice(SURNAMES)}", "firm": firm, "why": why,
             "floor": rng.choice(["the basement", "the ground floor", "the first floor", "the second floor", "the roof"]),
             "ref": str(rng.randint(1000, 9999)), "boss": f"{rng.choice(FIRST)} in head office"}
    asks = {"name": "Name?", "firm": "And who are you with?", "why": "What's it about, then?",
            "floor": "Which floor's it on?", "ref": "Got a job reference?", "boss": "Who signed it off?"}
    pools = {"name": [f"{f} {s}" for f in FIRST for s in SURNAMES], "firm": [f for f, _ in FIRMS],
             "why": [w for _, w in FIRMS], "floor": ["the basement", "the ground floor", "the first floor", "the second floor", "the roof"],
             "ref": [str(rng.randint(1000, 9999)) for _ in range(6)] + [story["ref"][::-1], story["ref"][1:] + story["ref"][0]],
             "boss": [f"{f} in head office" for f in FIRST]}
    questions = []
    for key in rng.sample(list(asks), FACE_QUESTIONS):
        wrong = [x for x in dict.fromkeys(pools[key]) if x != story[key]]
        options = rng.sample(wrong, 2) + [story[key]]
        rng.shuffle(options)
        questions.append({"key": key, "ask": asks[key], "options": options, "answer": options.index(story[key])})
    return {"story": story, "questions": questions, "ready": False, "answers": []}


def _best_load(bags: list[dict], cap: int) -> int:
    best = [0] * (cap + 1)
    for b in bags:
        for w in range(cap, b["w"] - 1, -1):
            best[w] = max(best[w], best[w - b["w"]] + b["v"])
    return best[cap]


def _muscle(rng) -> dict:
    names = rng.sample(LOOT, BAGS)
    bags = []
    for n in names:
        w = rng.randint(2, 9)
        bags.append({"name": n, "w": w, "v": max(5, int(w * rng.uniform(3, 9)) + rng.randint(-6, 12))})
    cap = max(10, int(sum(b["w"] for b in bags) * 0.45))
    return {"bags": bags, "cap": cap, "best": _best_load(bags, cap)}


MAKERS = {"hacker": _hacker, "safecracker": _safecracker, "lookout": _lookout, "wheelman": _wheelman, "face": _face,
          "muscle": _muscle}


def _public(role: str, p: dict) -> dict:
    """What the player sees of their puzzle."""
    if role == "hacker":
        return {"len": HACK_LEN, "symbols": HACK_SYMBOLS, "tries": HACK_TRIES,
                "guesses": [{"code": g["code"], "exact": g["exact"], "near": g["near"]} for g in p["guesses"]]}
    if role == "safecracker":
        return {"tumblers": TUMBLERS, "open": p["open"], "dials": DIALS,
                "log": [{"n": d["n"], "t": d["t"], "said": d["said"], "way": d["way"]} for d in p["dials"]]}
    if role == "lookout":
        return {"seq": p["seq"], "steps": LOOKOUT_STEPS, **({"answer": p["answer"]} if "answer" in p else {})}
    if role == "wheelman":
        return {"grid": GRID, "costs": p["costs"], **({"path": p["path"], "cost": p.get("cost")} if "path" in p else {})}
    if role == "face":
        out = {"ready": p["ready"], "count": len(p["questions"]), "answers": p["answers"]}
        if not p["ready"]:
            out["story"] = p["story"]
        else:
            n = len(p["answers"])
            out["questions"] = [{"ask": q["ask"], "options": q["options"]} for q in p["questions"][:n + 1]]
            out["right"] = [p["questions"][i]["answer"] == a for i, a in enumerate(p["answers"])]
        return out
    if role == "muscle":
        return {"bags": [{"name": b["name"], "w": b["w"], "v": b["v"]} for b in p["bags"]], "cap": p["cap"],
                **({"load": p["load"]} if "load" in p else {})}
    return {}


def _score(role: str, p: dict) -> float:
    """How the role went, 0 to 1."""
    if role == "hacker":
        for i, g in enumerate(p["guesses"]):
            if g["exact"] == HACK_LEN:
                return HACK_SCORE[i]
        return 0.1 * max((g["exact"] for g in p["guesses"]), default=0)
    if role == "safecracker":
        n = len(p["dials"])
        return (p["open"] / TUMBLERS) * max(0.6, min(1.0, 1 - 0.04 * max(0, n - 10)))
    if role == "lookout":
        got = 0
        for a, b in zip(p.get("answer", []), p["seq"]):
            if a != b:
                break
            got += 1
        return got / LOOKOUT_STEPS
    if role == "wheelman":
        cost = p.get("cost")
        return p["best"] / cost if cost else 0.0
    if role == "face":
        return sum(p["questions"][i]["answer"] == a for i, a in enumerate(p["answers"])) / FACE_QUESTIONS
    if role == "muscle":
        if "load" not in p:
            return 0.0
        w = sum(p["bags"][i]["w"] for i in p["load"])
        v = sum(p["bags"][i]["v"] for i in p["load"])
        return 0.0 if w > p["cap"] else v / p["best"] if p["best"] else 0.0
    return 0.0


def _done(role: str, p: dict) -> bool:
    if role == "hacker":
        return len(p["guesses"]) >= HACK_TRIES or any(g["exact"] == HACK_LEN for g in p["guesses"])
    if role == "safecracker":
        return p["open"] >= TUMBLERS or len(p["dials"]) >= DIALS
    if role == "lookout":
        return "answer" in p
    if role == "wheelman":
        return "path" in p
    if role == "face":
        return len(p["answers"]) >= len(p["questions"])
    if role == "muscle":
        return "load" in p
    return True


def _move(role: str, p: dict, body: dict) -> None:
    """A move at the puzzle; raises Refuse if it isn't one."""
    if _done(role, p):
        raise Refuse("You've done your bit. Sit tight.")
    if role == "hacker":
        code = str(body.get("code") or "")
        if len(code) != HACK_LEN or any(ch not in "123456" for ch in code):
            raise Refuse(f"Four digits, each 1 to {HACK_SYMBOLS}.")
        exact, near = _feedback(p["secret"], code)
        p["guesses"].append({"code": code, "exact": exact, "near": near})
    elif role == "safecracker":
        try:
            n = int(body.get("n"))
        except (TypeError, ValueError):
            raise Refuse("Turn the dial to a number.")
        if not 0 <= n <= 99:
            raise Refuse("The dial goes 0 to 99.")
        s = p["secret"][p["open"]]
        gap = abs(n - s)
        said = "click" if gap <= 2 else "hot" if gap <= 8 else "warm" if gap <= 20 else "cold"
        p["dials"].append({"n": n, "t": p["open"], "said": said, "way": 0 if said == "click" else (1 if s > n else -1)})
        if said == "click":
            p["open"] += 1
    elif role == "lookout":
        seq = body.get("seq")
        if not isinstance(seq, list) or len(seq) > LOOKOUT_STEPS or not all(isinstance(x, int) and 0 <= x < 9 for x in seq):
            raise Refuse("Tap the windows in the order the torch went.")
        p["answer"] = seq
    elif role == "wheelman":
        path = body.get("path")
        cost = _route_cost(p["costs"], path)
        if cost is None:
            raise Refuse("That route doesn't get from the bank to the lock-up.")
        p["path"], p["cost"] = path, cost
    elif role == "face":
        if not p["ready"]:
            if body.get("ready"):
                p["ready"] = True
                return
            raise Refuse("Learn your story first.")
        try:
            a = int(body.get("answer"))
        except (TypeError, ValueError):
            raise Refuse("Pick an answer.")
        if not 0 <= a < 3:
            raise Refuse("Pick an answer.")
        p["answers"].append(a)
    elif role == "muscle":
        load = body.get("load")
        if not isinstance(load, list) or not all(isinstance(i, int) and 0 <= i < len(p["bags"]) for i in load) or len(set(load)) != len(load):
            raise Refuse("Pick the bags to load.")
        if sum(p["bags"][i]["w"] for i in load) > p["cap"]:
            raise Refuse("The van won't take that much.")
        p["load"] = load


def _route_cost(costs, path) -> int | None:
    """What a route costs, or None if it isn't one from the bank (bottom left) to the lock-up (top right)."""
    if not isinstance(path, list) or not path or len(path) > GRID * GRID:
        return None
    cells = []
    for c in path:
        if not isinstance(c, list) or len(c) != 2 or not all(isinstance(x, int) for x in c):
            return None
        cells.append((c[0], c[1]))
    if cells[0] != (GRID - 1, 0) or cells[-1] != (0, GRID - 1) or len(set(cells)) != len(cells):
        return None
    total = 0
    for (r0, c0), (r1, c1) in zip(cells, cells[1:]):
        if abs(r0 - r1) + abs(c0 - c1) != 1 or not (0 <= r1 < GRID and 0 <= c1 < GRID) or costs[r1][c1] is None:
            return None
        total += costs[r1][c1]
    return total


# ---- the job ---------------------------------------------------------------------------------

def _present(room: dict) -> list[int]:
    return [u for u in room["players"] if u not in room["left"]]


def _seated(uid: int) -> dict | None:
    return next((r for r in _rooms.values() if not r.get("over") and uid in _present(r)), None)


def _free_role(room: dict) -> str | None:
    taken = set(room["roles"].values())
    return next((r for r in ROLES if r not in taken), None)


def _weights(room: dict) -> dict:
    w = dict(WEIGHTS[room.get("approach") or "quiet"])
    role = (room.get("twist") or {}).get("role")
    if role:
        w[role] *= 2
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def job_score(room: dict) -> float:
    """The crew's job, 0 to 1: every role's result by its weight, Dodgy Dave's for one nobody took."""
    w = _weights(room)
    played = {role: uid for uid, role in room["roles"].items()}
    total = 0.0
    for role in ROLES:
        if role in played:
            total += w[role] * room["job"][role].get("score", 0.0)
        else:
            total += w[role] * WINGING_IT
    return round(total, 4)


def _start_job(room: dict, now: float) -> None:
    """The plan's agreed: count the votes, set up the puzzles, start the job's clock."""
    votes = list(room["votes"].values())
    quiet, loud = votes.count("quiet"), votes.count("loud")
    if quiet == loud:
        room["approach"] = room["votes"].get(str(room["host"]), "quiet")
    else:
        room["approach"] = "quiet" if quiet > loud else "loud"
    rng = _rng()
    room["job"] = {role: MAKERS[role](rng) for role in set(room["roles"].values())}
    room.update(state="job", deadline=now + JOB_SECONDS)


def _end_job(room: dict, now: float) -> None:
    """Time's up on the job: score it, and roll when the police come."""
    for role, p in room["job"].items():
        p["score"] = round(_score(role, p), 4)
    score = job_score(room)
    e = edge(score)
    u = (secrets.randbelow(2 ** 52) + 1) / 2 ** 52
    m = police_take(e, u)
    cap = room["cap"]
    secs = VAULT_SECONDS[room["approach"]] * (room.get("twist") or {}).get("pace", 1.0)
    police = None if m >= cap else 0.0 if m <= 1.0 else secs * math.log(m) / math.log(cap)
    room.update(state="report", deadline=now + REPORT_SECONDS, score=score, edge=round(e, 4),
                vault={"start": now + REPORT_SECONDS, "secs": round(secs, 2), "police": police, "out": {}})


def _advance(room: dict, now: float) -> bool:
    if room.get("over"):
        return False
    if room["state"] == "lobby":
        if now >= room["expires"]:
            _close(room, "lapsed", now)
            return True
        return False
    changed = False
    while not room.get("over"):
        s = room["state"]
        if s == "plan":
            everyone = all(str(u) in room["votes"] for u in _present(room))
            if now < room["deadline"] and not everyone:
                break
            _start_job(room, min(now, room["deadline"]))
            _emit("job", room)
        elif s == "job":
            done = all(_done(room["roles"][str(u)], room["job"][room["roles"][str(u)]]) for u in _present(room))
            if now < room["deadline"] and not done:
                break
            _end_job(room, min(now, room["deadline"]))
            _emit("report", room)
        elif s == "report":
            if now < room["deadline"]:
                break
            room["state"] = "vault"
            _emit("vault", room)
        else:
            v = room["vault"]
            end = v["start"] + (v["secs"] if v["police"] is None else min(v["police"], v["secs"]))
            everyone_out = all(str(u) in v["out"] for u in room["players"])
            if now < end and not everyone_out:
                break
            _finish(room, min(now, end))
        changed = True
    return changed


def _close(room: dict, how: str, now: float) -> None:
    """A crew that never started: nobody paid in, so nothing to give back."""
    room.update(over=True, how=how, ended=now)
    _emit(how, room)


def outcome(room: dict) -> dict:
    """How it went for each of the crew: {uid: {"how": escaped|cleared|nicked, "take", "payout"}}."""
    v, stake = room["vault"], room["stake"]
    police_came = v["police"] is not None and v["police"] < v["secs"]
    out = {}
    for u in room["players"]:
        left = v["out"].get(str(u))
        if left:
            take = left[1]
            out[str(u)] = {"how": "escaped", "take": take, "payout": int(stake * take)}
        elif not police_came:
            out[str(u)] = {"how": "cleared", "take": room["cap"], "payout": int(stake * room["cap"])}
        else:
            out[str(u)] = {"how": "nicked", "take": 0.0, "payout": 0}
    return out


def _finish(room: dict, now: float) -> None:
    """Settle the job. Saved as paid before any money moves, so it can't pay twice."""
    if room.get("over"):
        return
    results = outcome(room)
    stake = room["stake"]
    v = room["vault"]
    nicked = [u for u, r in results.items() if r["how"] == "nicked"]
    # clean: a crew of three or more, all out, none of them on the first shaky bit of the take
    clean = (stake > 0 and len(room["players"]) >= 3 and not nicked
             and all(r["take"] >= CLEAN_TAKE for r in results.values()))
    if v["police"] is not None and v["police"] < v["secs"]:
        v["police_take"] = _floor2(take_at(v["police"], room["cap"], v["secs"]))
    bonus = {}
    if clean:
        day = _uk_day(now)
        for u in results:
            if _bonus.get(u) != day and tenure_ok(int(u)):
                bonus[u] = min(int(stake * _cfg("HEIST_CLEAN_BONUS_PCT", 0.10)), int(_cfg("HEIST_CLEAN_BONUS_MAX", 75)))
                _bonus[u] = day
    room.update(over=True, ended=now, how="clean" if clean else "nicked" if nicked else "done", results=results,
                bonus=bonus, paid=True)
    if stake > 0:
        take = sum(r["payout"] for r in results.values())
        _warm(HEAT_PER_JOB + max(0, take - stake * len(room["players"])) / HEAT_SCALE, now)
    room["heat"] = round(heat(now), 3)
    _save()
    if stake > 0:
        try:
            from commands.economy.casino_base import credit_from_bank
            for u, r in results.items():
                credit_from_bank(int(u), r["payout"], "Bank Job take")
        except Exception:
            log.error("couldn't pay out bank job %s", room["id"], exc_info=True)
        try:
            from lib.economy.economy_manager import add_bb
            for u, amount in bonus.items():
                if amount > 0:
                    add_bb(int(u), amount, "Bank Job clean job bonus", discretionary=True)
        except Exception:
            log.error("couldn't pay the clean job bonus on bank job %s", room["id"], exc_info=True)
        try:
            from lib.economy import casino_stats
            for u, r in results.items():
                casino_stats.record_result(int(u), GAME, stake, stake, r["payout"], r["how"])
        except Exception:
            log.warning("couldn't log bank job %s to the casino stats", room["id"], exc_info=True)
    try:
        from lib.activities import archive
        ranked = sorted(results, key=lambda u: -results[u]["take"])
        archive.keep(GAME, room["id"], room["target"], stake, room["how"], [
            {"user_id": int(u), "place": ranked.index(u) + 1, "payout": results[u]["payout"] if stake else 0,
             "result": results[u]["how"]} for u in results], room, now)
    except Exception:
        log.warning("couldn't keep bank job %s", room["id"], exc_info=True)
    try:
        from lib.economy import activity_badges
        activity_badges.heist_over(room)
    except Exception:
        log.warning("couldn't check the bank job badges for %s", room["id"], exc_info=True)
    _emit("over", room)


def tenure_ok(uid: int) -> bool:
    """Whether someone's been in the server long enough for a clean job bonus (alts can't farm it)."""
    days = int(_cfg("HEIST_BONUS_MIN_DAYS", 14))
    try:
        guild = CLIENT.get_guild(config.GUILD_ID) if CLIENT is not None else None
        member = guild.get_member(uid) if guild else None
        joined = getattr(member, "joined_at", None)
        return joined is not None and (time.time() - joined.timestamp()) >= days * 86400
    except Exception:
        return False


def _tidy(now: float) -> bool:
    old = [rid for rid, r in _rooms.items() if r.get("over") and now - r.get("ended", now) > KEEP_FINISHED]
    for rid in old:
        _rooms.pop(rid)
        _posts.pop(rid, None)
    return bool(old)


def sweep(now: float | None = None) -> None:
    _load()
    now = time.time() if now is None else now
    changed = _tidy(now)
    for r in list(_rooms.values()):
        changed = _advance(r, now) or changed
    # a job under way is saved every sweep, so a restart knows to the second when the clocks stopped
    if changed or any(not r.get("over") and r["state"] != "lobby" for r in _rooms.values()):
        _save()


async def run_sweeper(every: float = 1.0) -> None:
    while True:
        try:
            sweep()
        except Exception:
            log.error("bank job sweep failed", exc_info=True)
        await asyncio.sleep(every)


# ---- what the page sees ----------------------------------------------------------------------

def _person(uid: int, name_of) -> dict:
    return {"uid": str(uid), "name": name_of(uid) or "Someone"}


def _target_view(key: str, now: float) -> dict:
    t = TARGETS[key]
    return {"key": key, "name": t["name"], "short": t["short"], "blurb": t["blurb"], "minCrew": t["min_crew"],
            "cap": cap_for(key, now), "baseCap": t["cap"], "tip": tip(now) == key, "maxStake": max_stake(key)}


def view(room: dict, uid: int, name_of, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    s = room["state"]
    phase = "over" if room.get("over") else s
    me = str(uid)
    out = {
        "id": room["id"], "host": str(room["host"]), "stake": room["stake"], "seats": SEATS, "phase": phase,
        "how": room.get("how"), "target": _target_view(room["target"], now) if s == "lobby" else
        {**_target_view(room["target"], now), "cap": room["cap"]},
        "crew": [{**_person(u, name_of), "role": room["roles"].get(str(u)), "left": u in room["left"],
                  "voted": str(u) in room.get("votes", {}),
                  "done": bool(s == "job" and _done(room["roles"][str(u)], room["job"][room["roles"][str(u)]]))}
                 for u in room["players"]],
        "expiresIn": max(0, round(room["expires"] - now)) if s == "lobby" else None,
        "deadlineIn": round(max(0.0, room.get("deadline", now) - now), 2) if s in ("plan", "job", "report") else None,
        "twist": room.get("twist"), "approach": room.get("approach"),
        "weights": _weights(room) if s != "lobby" else WEIGHTS["quiet"],
    }
    if s == "plan":
        out["votes"] = {"quiet": list(room["votes"].values()).count("quiet"),
                        "loud": list(room["votes"].values()).count("loud")}
        out["myVote"] = room["votes"].get(me)
    if s == "job" and not room.get("over"):
        role = room["roles"].get(me)
        if role and role in room["job"]:
            p = room["job"][role]
            out["puzzle"] = {"role": role, "done": _done(role, p), **_public(role, p)}
    if room.get("score") is not None:
        played = {r: u for u, r in room["roles"].items()}
        out["report"] = {"score": room["score"], "alarm": round((room["edge"] - EDGE_BEST) / (EDGE_WORST - EDGE_BEST), 3),
                         "roles": [{"role": r, "uid": played.get(r), "score": room["job"][r]["score"] if r in played else WINGING_IT,
                                    "dave": r not in played} for r in ROLES]}
    if room.get("vault"):
        v = room["vault"]
        out["vault"] = {"at": round(now - v["start"], 2), "secs": v["secs"], "cap": room["cap"],
                        "out": {u: x[1] for u, x in v["out"].items()}}
        if room.get("over"):
            out["vault"]["police"] = v.get("police_take")
    if room.get("over") and room.get("results"):
        out["results"] = room["results"]
        out["bonus"] = room.get("bonus", {})
        out["heat"] = room.get("heat")
    return out


def most_wanted(name_of, now: float | None = None) -> list[dict]:
    """The week's biggest earners from bank jobs, for the lobby's board. Cached for a minute."""
    global _board_cache
    now = time.time() if now is None else now
    if now - _board_cache[0] < 60:
        rows = _board_cache[1]
    else:
        rows = []
        try:
            from database import DatabaseManager
            rows = DatabaseManager.fetch_all(
                "SELECT p.user_id, SUM(p.payout - r.stake), COUNT(*) FROM game_record_players p "
                "JOIN game_records r ON r.game = p.game AND r.id = p.id "
                "WHERE p.game = ? AND r.stake > 0 AND r.ended >= ? GROUP BY p.user_id "
                "HAVING SUM(p.payout - r.stake) > 0 ORDER BY SUM(p.payout - r.stake) DESC LIMIT 5",
                (GAME, int(now - 7 * 86400))) or []
        except Exception:
            log.warning("couldn't read the bank job board", exc_info=True)
        _board_cache = (now, rows)
    return [{**_person(int(u), name_of), "net": int(n), "jobs": int(c)} for u, n, c in rows if u]


def lobby(uid: int, name_of, balance: int) -> dict:
    _load()
    now = time.time()
    changed = _tidy(now)
    for r in list(_rooms.values()):
        changed = _advance(r, now) or changed
    if changed:
        _save()
    mine = _seated(uid)
    h = heat(now)
    return {
        "you": _person(uid, name_of), "balance": balance, "stakes": STAKES,
        "targets": [_target_view(k, now) for k in TARGETS], "roles": [{"key": r, "name": ROLE_INFO[r][0],
                                                                       "job": ROLE_INFO[r][1]} for r in ROLES],
        "heat": {"level": round(h, 3), "label": heat_label(h)}, "tip": tip(now),
        "room": view(mine, uid, name_of, now) if mine else None,
        "open": [{"id": r["id"], "host": _person(r["host"], name_of), "players": len(_present(r)), "seats": SEATS,
                  "stake": r["stake"], "target": TARGETS[r["target"]]["short"], "expiresIn": max(0, round(r["expires"] - now)),
                  "roles": sorted(r["roles"].values(), key=ROLES.index)}
                 for r in _rooms.values() if r["state"] == "lobby" and not r.get("over") and uid not in r["players"]],
        "board": most_wanted(name_of, now),
    }


def room(uid: int, rid: str, name_of) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None:
        raise Refuse("That crew has broken up.")
    if _advance(r, time.time()):
        _save()
    if uid not in r["players"]:
        raise Refuse("You aren't in that crew.")
    return view(r, uid, name_of)


# ---- what players do -------------------------------------------------------------------------

def _check_stake(uid: int, stake: int, who: str = "You") -> None:
    from lib.economy.economy_manager import get_bb
    if stake > 0 and get_bb(uid) < stake:
        raise Refuse(f"You don't have {stake:,} UKPence." if who == "You" else f"{who} can't cover {stake:,} UKPence.")


def _busy(uid: int) -> None:
    if _seated(uid) is not None:
        raise Refuse("You're already in a crew.")


def _lobby_room(rid: str) -> dict:
    r = _rooms.get(rid)
    if r is None or r.get("over") or r["state"] != "lobby":
        raise Refuse("That crew has already gone in, or broken up.")
    return r


def _stake_for(target: str, stake) -> int:
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        raise Refuse("Pick a buy-in.")
    if target not in TARGETS:
        raise Refuse("Pick a target.")
    top = max_stake(target)
    if stake < 0 or stake > top or stake not in STAKES:
        raise Refuse(f"The {TARGETS[target]['short']} takes buy-ins up to {top:,} UKPence." if top
                     else "The bank's too short to stake a job right now. Free jobs only.")
    return stake


def open_room(uid: int, stake, target="post", channel: int | None = None, role: str | None = None) -> dict:
    """Get a crew together; ``channel`` is where the game was opened, where its post goes."""
    _load()
    target = target if target in TARGETS else "post"
    stake = _stake_for(target, stake)
    _busy(uid)
    _check_stake(uid, stake)
    now = time.time()
    role = role if role in ROLES else "hacker"
    r = {"id": secrets.token_hex(4), "host": uid, "players": [uid], "left": [], "stake": stake, "target": target,
         "state": "lobby", "created": now, "expires": now + LOBBY_SECONDS, "roles": {str(uid): role}, "votes": {},
         "over": False, "ch": int(channel) if channel else None}
    _rooms[r["id"]] = r
    _save()
    _emit("open", r)
    return r


def join(uid: int, rid: str, role: str | None = None) -> dict:
    _load()
    r = _lobby_room(rid)
    if uid in r["players"]:
        return r
    if len(r["players"]) >= SEATS:
        raise Refuse("That crew's full.")
    _busy(uid)
    _check_stake(uid, r["stake"])
    taken = set(r["roles"].values())
    role = role if role in ROLES and role not in taken else _free_role(r)
    r["players"].append(uid)
    r["roles"][str(uid)] = role
    _save()
    _emit("seats", r)
    return r


def pick_role(uid: int, rid: str, role: str) -> dict:
    """Swap to a role nobody else has (in the lobby)."""
    _load()
    r = _lobby_room(rid)
    if uid not in r["players"]:
        raise Refuse("You aren't in that crew.")
    if role not in ROLES:
        raise Refuse("That isn't a job.")
    holder = next((u for u, x in r["roles"].items() if x == role), None)
    if holder is not None and holder != str(uid):
        raise Refuse("Someone's already got that job.")
    r["roles"][str(uid)] = role
    _save()
    _emit("seats", r)
    return r


def pick_target(uid: int, rid: str, target: str, stake=None) -> dict:
    """The host changes the target (and the buy-in, if it's over the new one's limit or they say)."""
    _load()
    r = _lobby_room(rid)
    if uid != r["host"]:
        raise Refuse("Only the host picks the target.")
    if target not in TARGETS:
        raise Refuse("Pick a target.")
    new = _stake_for(target, r["stake"] if stake is None else stake)
    if new > 0:
        for u in r["players"]:
            _check_stake(u, new, "You" if u == uid else "Someone in the crew")
    r.update(target=target, stake=new)
    _save()
    _emit("seats", r)
    return r


def leave(uid: int, rid: str) -> dict:
    """Leave a crew. Before it goes in you just walk off; the host leaving breaks it up. Once it's
    started your buy-in is in the job: you're still in the vault until you leg it or the police come."""
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"] or r.get("over"):
        raise Refuse("You aren't in that crew.")
    now = time.time()
    if r["state"] == "lobby":
        if uid == r["host"]:
            _close(r, "closed", now)
        else:
            r["players"].remove(uid)
            r["roles"].pop(str(uid), None)
            _emit("seats", r)
    elif uid not in r["left"]:
        r["left"].append(uid)
        _advance(r, now)
    _save()
    return r


def start(uid: int, rid: str) -> dict:
    _load()
    r = _lobby_room(rid)
    if uid != r["host"]:
        raise Refuse("Only the host can send the crew in.")
    t = TARGETS[r["target"]]
    need = max(t["min_crew"], 2 if r["stake"] > 0 else 1)
    if len(r["players"]) < need:
        raise Refuse(f"The {t['short']} needs a crew of {need}." if t["min_crew"] >= need
                     else "A staked job needs at least two of you.")
    stake = r["stake"]
    if stake > max_stake(r["target"]):
        raise Refuse(f"The bank's short tonight: the {t['short']} takes buy-ins up to {max_stake(r['target']):,} now.")
    if stake > 0:
        for u in r["players"]:
            _check_stake(u, stake, "You" if u == uid else "Someone in the crew")
        from commands.economy.casino_base import credit_from_bank
        from lib.economy.economy_manager import remove_bb
        taken = []
        for u in r["players"]:
            if not remove_bb(u, stake, reason="Bank Job buy-in"):
                for x in taken:
                    credit_from_bank(x, stake, "Bank Job refund")
                raise Refuse("Someone in the crew can't cover the buy-in any more.")
            taken.append(u)
    now = time.time()
    r.update(state="plan", started=now, deadline=now + PLAN_SECONDS, cap=cap_for(r["target"], now),
             twist=_rng().choice(TWISTS))
    _save()
    _emit("start", r)
    return r


def _live(uid: int, rid: str) -> tuple[dict, float]:
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"]:
        raise Refuse("You aren't in that crew.")
    now = time.time()
    _advance(r, now)
    return r, now


def vote(uid: int, rid: str, approach: str) -> dict:
    r, now = _live(uid, rid)
    if r.get("over") or r["state"] != "plan":
        raise Refuse("The plan's already made.")
    if approach not in WEIGHTS:
        raise Refuse("Quiet or loud?")
    r["votes"][str(uid)] = approach
    _advance(r, now)
    _save()
    return r


def play(uid: int, rid: str, body: dict) -> dict:
    """A move at your role's puzzle."""
    r, now = _live(uid, rid)
    if r.get("over") or r["state"] != "job":
        raise Refuse("The job isn't on.")
    if uid in r["left"]:
        raise Refuse("You've walked off the job.")
    role = r["roles"][str(uid)]
    _move(role, r["job"][role], body if isinstance(body, dict) else {})
    _advance(r, now)
    _save()
    return r


def leg_it(uid: int, rid: str) -> dict:
    """Get out of the vault with the take as it stands."""
    r, now = _live(uid, rid)
    v = r.get("vault")
    if v and str(uid) in v["out"]:
        raise Refuse("You're already out.")
    if r.get("over"):
        raise Refuse("Too late. The police are here." if r.get("how") == "nicked" else "It's all over.")
    if not v or r["state"] != "vault":
        raise Refuse("The vault's not open.")
    if str(uid) in v["out"]:
        raise Refuse("You're already out.")
    if uid in r["left"]:
        raise Refuse("You've walked off the job.")
    t = now - v["start"]
    if t < 0:
        raise Refuse("The vault's not open yet.")
    if v["police"] is not None and t >= v["police"]:
        raise Refuse("Too late. The police are here.")
    v["out"][str(uid)] = [round(t, 3), _floor2(take_at(t, r["cap"], v["secs"]))]
    _advance(r, now)
    _save()
    _emit("out", r)
    return r
