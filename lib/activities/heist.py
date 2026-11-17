"""Bank Job: a heist for up to six, in the activity's Together games.

A crew is opened by its host, who picks the target (one of the Bank of HMS Victory's branches: the
Nether Wallop village branch, the High Street branch or the Vault itself) and a buy-in; anyone
can take a seat and a role. Once it starts, every buy-in goes into the bank and the job runs:

    plan    the crew votes on the way in (the roof, the front door, the drains...), which sets the
            rooms they'll go through, and on going in quiet or loud; a twist of the night
            (rain, a shift change, a chatty guard) leans on one role or another
    job     quiet: a room at a time through the building (heist_rooms.py says what's where). Each
            room has a few things in the way, each a job for one role: a keypad, a lock, a patrol, a
            route, someone to blag past, loot to load. Whoever holds the role plays its puzzle, after
            a run-down of how it's played that its clock waits for (heist_briefs.py); a role nobody
            holds is winged by Two-Pint Terry unless someone free steps in. One alarm builds over the
            whole job, and loot jobs make the vault hold more; an alarm past ALARM_BLOWN sends them
            running for it.
            loud: the breach. One timer for the crew; each player does a few quick action tasks
            (heist_games/loud.py) at their own pace, and every one done takes time off the timer and
            adds safe time in the vault. Done early, they pick up loose cash (bonus tasks that pay
            them a little there and then) till the timer runs out and the whole crew's in the vault.
    report  how well it went
    vault   everyone grabs the cash: tapping GRAB keeps their bag up with the piles. For the safe
            time (longer for a cleaner quiet job, or as the loud crew earned it) the police can't
            come, and the piles fill to 1 - edge of the buy-in: the stake in play, once the bank's
            cut (the edge) is taken, which the page shows as 1.00x, "your stake back" (see shown());
            after that they climb on to the target's cap, all of it profit, and the police can turn
            up at any moment (the scanner shows the honest odds of it, second by second). Each of the
            crew legs it when they like with their bag; whoever's still inside when the police come
            is nicked and loses their buy-in.

The money. The piles reach 1 - edge as the safe time ends, then the police arrive at take
M = (1 - edge) / U for U uniform on (0, 1], so the chance the piles reach m before they do is
(1 - edge) / m, and a bag that's kept up with them returns m times that: 1 - edge of the buy-in,
however long anyone waits. A bag is never worth more than the piles (a slow grabber's is worth
less), and getting out in the safe time pays what the bag holds then, 1 - edge at most, so nothing
beats it. The scanner only shows the hazard, the same for every job with this edge, cap and pace, so
it says nothing about when these police will come. A loud job's edge (loud_edge) runs from
LOUD_EDGE[0] down to LOUD_EDGE[1], which is EDGE_BEST plus the most loose cash anyone can pick up
(LOOSE_MAX), so a loud job with all its loose cash never beats the best quiet one. A quiet job's edge runs from EDGE_WORST for a botched
job down to EDGE_BEST for a perfect one, so playing well is worth something, but even a perfect
crew (or a page that cheats at the puzzles) leaves the bank its EDGE_BEST. Payouts are floored to
whole UKPence, which only ever adds to it. A clean job (a crew of three or more, every one of
them out with at least CLEAN_TAKE) adds a small, once-a-day, reserve-scaled bonus, the one part of
the game that can run in the players' favour, and only by a little: see docs/heist.md for the sums.

Like Countdown, every read moves a crew on to the present and a sweeper does the same for crews
nobody's watching, so a job always finishes. It's all saved after every change, and the clocks
pause while the bot is down: on the next boot every live job's clocks move on by the time it was
away, so nobody is nicked while they couldn't press anything.

Practice (HEIST_TESTERS only) is a job for play money. Every role nobody took gets a bot crewmate who
backs the crew's plan, does its things in each room at a skill level rolled for it, and legs it from the
vault when it fancies. Nothing moves: no UKPence in or out, no clean job bonus, no heat, no badges, no
records, no post anywhere, and nobody else sees the crew in their list. The rooms, the puzzles, the
alarm and the police are the real thing.
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
from lib.activities import heist_briefs as briefs
from lib.activities import heist_games
from lib.activities import heist_rooms as rooms_of
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

GAME = "heist"
LABEL = "Bank Job"
SEATS = 6
STAKES = [0, 50, 100, 250, 500, 1000]
LOBBY_SECONDS = 900          # a crew nobody starts breaks up after this
PLAN_SECONDS = 25            # picking the way in
REPORT_SECONDS = 8
KEEP_FINISHED = 1800
EDGE_WORST, EDGE_BEST = 0.08, 0.01              # the bank's share for a botched job and a perfect one
WINGING_IT = 0.3             # how Two-Pint Terry does at a role nobody took
MIN_CAP = 1.5                # however hot it gets, the vault's worth this much
HEAT_HALF_LIFE = 3 * 3600    # the police calm down by half every three hours
HEAT_PER_JOB = 0.04          # a staked job warms them up a bit...
HEAT_SCALE = 20_000          # ...and a profit this big on top of the buy-ins sets them to boiling
HEAT_CAP_CUT = 0.4           # at full heat the vaults hold 40% less
TIP_BOOST = 1.25             # the fence's tip of the day holds this much more
VAULT_EASE = 2.0             # the take's climb: cap ** (t / secs) ** this, slow to start and fast to finish
CLEAN_TAKE = 1.5             # a clean job's crew all got out with at least this much
BRIEF_FULL, BRIEF_SHORT = 15.0, 5.0     # the run-down before a puzzle: the first time you meet its game, and after
BRIEF_FORGET = 30 * 86400    # a game someone hasn't met in a month gets the full run-down again

BOT_NAMES = ["Nervous Nigel", "Big Barry", "Two-Coats Trevor", "Mad Mandy", "Light-Fingered Linda", "Clive the Drill",
             "Posh Pauline", "Keith the Kettle", "Sharon Two-Phones", "Del Boy Derek", "Tracey Tealeaf", "Colin the Cat"]
BOT_SKILL = (0.35, 0.95)      # a bot crewmate's skill is rolled from this range, and each thing it does lands near it
BOT_PACE = (0.3, 0.85)        # how far into a room's time a bot crewmate finishes each of its things there

# every target is a branch of the Bank of HMS Victory, the server's own bank: whatever a crew gets away with
# comes out of its reserves, and whatever the police take back goes into them
TARGETS = {
    "post": {"name": "The Bank of HMS Victory, Nether Wallop branch", "short": "Village Branch", "cap": 3.0, "min_crew": 1,
             "max_stake": 1000, "blurb": "The bank's smallest branch, at the back of the post office. Pensions day, a bell "
                                         "on the door and Maureen behind the counter."},
    "society": {"name": "The Bank of HMS Victory, High Street branch", "short": "High Street Branch", "cap": 6.0, "min_crew": 2,
                "max_stake": 1000, "blurb": "A proper safe and a security guard called Brian."},
    "vault": {"name": "The Bank of HMS Victory, the Vault", "short": "The Vault", "cap": 12.0, "min_crew": 3, "max_stake": 500,
              "blurb": "The big one. The server's own vault, under the waterline, where every UKPence in the bank is "
                       "kept."},
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
_met: dict[str, dict[str, float]] = {}   # uid -> {"role:game": when they last had its run-down}
_loaded = False
_board_cache: tuple[float, list] = (0.0, [])
CLIENT = None
listeners: list = []


Refuse = heist_games.Refuse     # something the rules don't allow; the message is for the player


def _cfg(name, default):
    return getattr(config, name, default)


def _rng() -> random.Random:
    return random.Random(secrets.randbits(64))


def tester(uid) -> bool:
    return int(uid or 0) in _cfg("HEIST_TESTERS", [])


def allowed(uid) -> bool:
    """Everyone once Bank Job's live (HEIST_LIVE), until then only its testers."""
    return bool(_cfg("HEIST_LIVE", False)) or tester(uid)


def can_practise(uid) -> bool:
    """Practice jobs (bot crewmates, play money) are for the testers only, live or not."""
    return tester(uid)


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
    _met.update(raw.get("met", {}))
    for r in _rooms.values():
        # a job the old one-puzzle Bank Job was in the middle of (only ever a practice one: a real one holds up the
        # restart) has no rooms to carry on through, so it's called off
        if r["state"] == "job" and "rooms" not in r and not r.get("over"):
            r.update(over=True, how="closed", ended=time.time())
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
    if room.get("arrive"):
        room["arrive"] += by
    for rm in room.get("rooms", []):
        for th in rm["things"]:
            for k in ("at", "go"):
                if th.get(k):
                    th[k] += by
    v = room.get("vault")
    if v:
        v["start"] += by
    room["paused"] = round(room.get("paused", 0.0) + by, 1)


def _save() -> None:
    try:
        atomic_write_json(_FILE, {"rooms": _rooms, "posts": _posts, "heat": _heat, "bonus": _bonus, "met": _met,
                                  "saved": time.time()})
    except Exception:
        log.error("couldn't save the bank jobs to %s", _FILE, exc_info=True)


def _emit(event: str, room: dict) -> None:
    if room.get("practice"):        # a practice job is posted nowhere
        return
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
    return sum(1 for r in _rooms.values() if not r.get("over") and r["state"] != "lobby" and not r.get("practice"))


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
        spare = max(0, bank_reserves() - int(_cfg("RESERVE_FLOOR", 5_000)))
        afford = spare * float(_cfg("HEIST_EXPOSURE_PCT", 0.15)) / (SEATS * t["cap"] * TIP_BOOST * (1 + LOOT_MAX))
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


def take_at(t: float, cap: float, secs: float, base: float = 1.0) -> float:
    """The piles t seconds into the vault's climb (base at its start, the cap at the end). It creeps at first and races
    at the end (the time's squared), so the low takes, where the police most often come, get a few seconds each
    instead of flashing past. Only when the police come is drawn (police_take); this just paces it, so it moves
    nobody's odds."""
    if t <= 0:
        return base
    return min(cap, base * (cap / base) ** (min(1.0, t / secs) ** VAULT_EASE))


def time_at(m: float, cap: float, secs: float, base: float = 1.0) -> float:
    """Seconds into the climb until the piles reach m: take_at turned round."""
    if m <= base:
        return 0.0
    return secs * min(1.0, math.log(m / base) / math.log(cap / base)) ** (1 / VAULT_EASE)


# The vault as it's played: SAFE_SECONDS of grabbing with no police, the piles going from nothing to base = 1 - edge,
# then the climb from base to the cap. The police come at take M >= base (police_take), so never in the safe time.

def pile_at(v: dict, cap: float, t: float) -> float:
    """What the piles are worth (a perfect grabber's bag) t seconds after the vault opened."""
    base, safe = v["base"], v["safe"]
    if t <= 0:
        return 0.0
    if t < safe:
        return base * t / safe
    return take_at(t - safe, cap, v["secs"], base)


def police_time(m: float, cap: float, secs: float, base: float, safe: float) -> float | None:
    """Seconds after the vault opened when the piles reach the police's take m (None: they never get there)."""
    return None if m >= cap else safe + time_at(m, cap, secs, base)


def hazard(v: dict, cap: float, t: float, dt: float = 1.0) -> float:
    """The honest chance the police turn up in the next dt seconds, given they haven't yet: 1 - pile(t) / pile(t + dt)
    (they come at M with P(M > m) = base / m). The same for any job with this edge, cap and pace, so it shows the risk
    and nothing about this job's draw. Nothing in the safe time; nothing once the piles are at the cap."""
    a, b = max(t, v["safe"]), max(t + dt, v["safe"])
    pa, pb = pile_at(v, cap, a), pile_at(v, cap, b)
    return 0.0 if pb <= 0 or pa >= cap else max(0.0, 1.0 - pa / pb)


def _floor2(x: float) -> float:
    return math.floor(x * 100 + 1e-9) / 100


# What the player sees. The money is all worked in multiples of the buy-in (m above), but the page shows the bank's
# cut taken up front and the vault's figures as multiples of the stake left in play, m' = m / base: the safe time
# fills the piles to 1.00x (your stake back), and everything past it is profit. Only the labels change: leaving at
# m' pays m' * stake * base = m * stake, the same UKPence as ever, and the police still come at m' = 1 / U.

def shown(m: float, base: float) -> float:
    """A take (a multiple of the buy-in) as the vault shows it: a multiple of the stake in play, floored to 0.01."""
    return _floor2(m / base) if base > 0 else 0.0


def in_play(stake: int, base: float) -> int:
    """The stake in play, in UKPence: what getting out at exactly 1.00x pays (the buy-in less the bank's cut)."""
    return int(stake * _floor2(base))


def verdict(stake: int, base: float, r: dict) -> dict:
    """One player's result in words the end screen and the #casino card both use: the take as the vault showed it,
    what it came to against the buy-in (the bank's cut included), and a line saying so."""
    cut = stake - in_play(stake, base)
    x = r.get("shown", shown(r.get("take", 0.0), base))
    if r["how"] == "nicked":
        return {"x": 0.0, "net": -stake, "kind": "nicked", "text": f"Nicked: -{stake:,}" if stake else "Nicked"}
    net = r["payout"] - stake
    if not stake:
        kind = "profit" if x > 1.0 else "even" if x >= 1.0 else "short"
        return {"x": x, "net": 0, "kind": kind, "text": f"Out at {x:.2f}x"}
    if x >= 1.0 and net <= 0:
        up = r["payout"] - in_play(stake, base)
        return {"x": x, "net": net, "kind": "even",
                "text": f"Out at {x:.2f}x: broke even" if not net
                else f"Out at {x:.2f}x: stake back, broke even less the bank's cut ({net:,})" if up <= 0
                else f"Out at {x:.2f}x: +{up:,} on the stake, less the bank's cut ({net:,})"}
    if net > 0:
        return {"x": x, "net": net, "kind": "profit", "text": f"Out at {x:.2f}x: +{net:,}"}
    return {"x": x, "net": net, "kind": "short",
            "text": f"Out at {x:.2f}x, before the stake was back: {net:,}" + (f" (the bank's cut was {cut:,})" if cut else "")}


# ---- the puzzles -----------------------------------------------------------------------------
# Each puzzle is made here and judged here; the page only ever sees its public part. A puzzle carries its own
# size (a room's quick keypad has three digits, the vault's time lock four), so the same rules judge both.

HACK_LEN, HACK_SYMBOLS, HACK_TRIES = 4, 6, 6
HACK_SCORE = [1.0, 1.0, 0.9, 0.8, 0.7, 0.6]         # by the guess it was cracked on
TUMBLERS, DIALS = 3, 15
LOOKOUT_STEPS = 7
GRID = 7
ROADBLOCKS = 11
TRAFFIC = (1, 1, 1, 2, 3, 5, 6)          # mostly clear roads, and jams worth going round
BAGS = 8
FACE_QUESTIONS = 4
FACE_WAIT = 9.0              # how long the guard waits on each answer about the cover story (the page times it)
# a thing in a room, by its size: what each role's puzzle looks like
SIZES = {
    "hacker": {"s": {"n": 3, "symbols": 5, "tries": 5}, "m": {"n": 4, "symbols": 6, "tries": 6},
               "big": {"n": 4, "symbols": 7, "tries": 6}},
    "safecracker": {"s": {"tumblers": 1, "listens": 7}, "m": {"tumblers": 2, "listens": 12},
                    "big": {"tumblers": 3, "listens": 16}},
    "lookout": {"s": {"steps": 5}, "m": {"steps": 7}, "big": {"steps": 9}},
    "wheelman": {"s": {"grid": 6, "blocks": 8}, "m": {"grid": 7, "blocks": 11}, "big": {"grid": 7, "blocks": 14}},
    "face": {"s": {"n": 2}, "m": {"n": 3}, "big": {"n": 4}},
    "muscle": {"loot": {"bags": 7}, "s": {"bags": 6}, "m": {"bags": 7}, "big": {"bags": 8}},
}

FIRMS = [("Thames Water", "a leak"), ("British Gas", "a gas smell"), ("Openreach", "the broadband"),
         ("Rentokil", "a mouse problem"), ("the council", "a fire door inspection"), ("Otis Lifts", "the lift"),
         ("Dyno-Rod", "the drains"), ("Securicor", "the CCTV")]
FIRST = ["Gary", "Brenda", "Keith", "Sharon", "Derek", "Linda", "Trevor", "Mandy", "Nigel", "Tracey", "Clive", "Denise",
         "Barry", "Julie", "Colin", "Pauline"]
SURNAMES = ["Pratt", "Higgins", "Bloggs", "Parker", "Dobbs", "Wright", "Shaw", "Fletcher", "Clegg", "Platt"]
# the Face is drawn as a woman, so her cover's a woman's name (and so are the guard's wrong guesses at it)
HER = ["Brenda", "Sharon", "Linda", "Mandy", "Tracey", "Denise", "Julie", "Pauline", "Karen", "Sandra", "Debbie", "Lorraine"]
FLOORS = ["the basement", "the ground floor", "the first floor", "the second floor", "the roof"]
LOOT = ["Used fivers", "Gold bar", "Krugerrands", "Bearer bonds", "Grandad's medals", "Pound coins", "A Rolex or two",
        "Premium Bonds", "Diamonds", "A bag of 2p coins", "First-class stamps", "The Crown Jewels (replica)"]


def _hacker(rng, n: int = HACK_LEN, symbols: int = HACK_SYMBOLS, tries: int = HACK_TRIES) -> dict:
    code = "".join(str(rng.randint(1, symbols)) for _ in range(n))
    return {"secret": code, "guesses": [], "len": n, "symbols": symbols, "tries": tries}


def _feedback(code: str, guess: str) -> tuple[int, int]:
    exact = sum(a == b for a, b in zip(code, guess))
    common = sum(min(code.count(d), guess.count(d)) for d in set(guess))
    return exact, common - exact


def _safecracker(rng, tumblers: int = TUMBLERS, listens: int = DIALS) -> dict:
    return {"secret": [rng.randint(0, 99) for _ in range(tumblers)], "dials": [], "open": 0, "listens": listens}


def _lookout(rng, steps: int = LOOKOUT_STEPS) -> dict:
    seq = [rng.randrange(9)]
    while len(seq) < steps:
        n = rng.randrange(9)
        if n != seq[-1]:
            seq.append(n)
    return {"seq": seq}


def _cheapest(costs: list[list[int | None]], start=None, end=None) -> int | None:
    """The cheapest way across the town, Dijkstra; a cell's cost is paid on entering it."""
    g = len(costs)
    start, end = start or (g - 1, 0), end or (0, g - 1)
    best = {start: 0}
    todo = [(0, start)]
    while todo:
        d, (r, c) = heapq.heappop(todo)
        if (r, c) == end:
            return d
        if d > best.get((r, c), 1 << 30):
            continue
        for nr, nc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
            if 0 <= nr < g and 0 <= nc < g and costs[nr][nc] is not None:
                nd = d + costs[nr][nc]
                if nd < best.get((nr, nc), 1 << 30):
                    best[(nr, nc)] = nd
                    heapq.heappush(todo, (nd, (nr, nc)))
    return None


def _straight_on(costs: list[list[int | None]]) -> int | None:
    """The cheapest route that only ever heads up or right: the obvious way, which a good town makes a trap."""
    g = len(costs)
    best = [[None] * g for _ in range(g)]
    for r in range(g - 1, -1, -1):
        for c in range(g):
            if costs[r][c] is None:
                continue
            if (r, c) == (g - 1, 0):
                best[r][c] = 0
                continue
            came = [x for x in ((best[r + 1][c] if r + 1 < g else None), (best[r][c - 1] if c else None)) if x is not None]
            best[r][c] = min(came) + costs[r][c] if came else None
    return best[0][g - 1]


def _wheelman(rng, grid: int = GRID, blocks: int = ROADBLOCKS) -> dict:
    """A town where the cheapest route has to double back somewhere: heading straight up and across costs at
    least two minutes more (or is blocked). After a few hundred tries, any town with a way through does."""
    fallback = None
    for _ in range(400):
        costs = [[rng.choice(TRAFFIC) for _ in range(grid)] for _ in range(grid)]
        for _ in range(blocks):
            r, c = rng.randrange(grid), rng.randrange(grid)
            if (r, c) not in ((grid - 1, 0), (0, grid - 1)):
                costs[r][c] = None
        costs[grid - 1][0] = costs[0][grid - 1] = 1
        best = _cheapest(costs)
        if best is None:
            continue
        fallback = fallback or {"costs": costs, "best": best}
        straight = _straight_on(costs)
        if straight is None or straight >= best + 2:
            return {"costs": costs, "best": best}
    return fallback


def _story(rng) -> dict:
    """A cover story for the crew's Face: who they are and why they're in the building at this hour."""
    firm, why = rng.choice(FIRMS)
    return {"name": f"{rng.choice(HER)} {rng.choice(SURNAMES)}", "firm": firm, "why": why,
            "floor": rng.choice(FLOORS), "ref": str(rng.randint(1000, 9999)), "boss": f"{rng.choice(FIRST)} in head office"}


ASKS = {"name": "Name?", "firm": "And who are you with?", "why": "What's it about, then?",
        "floor": "Which floor's it on?", "ref": "Got a job reference?", "boss": "Who signed it off?"}


def _questions(rng, story: dict, n: int, keys: list[str] | None = None) -> list[dict]:
    """Questions on the cover story, each with the right answer and two that sound right."""
    pools = {"name": [f"{f} {s}" for f in HER for s in SURNAMES], "firm": [f for f, _ in FIRMS],
             "why": [w for _, w in FIRMS], "floor": FLOORS,
             "ref": [str(rng.randint(1000, 9999)) for _ in range(6)] + [story["ref"][::-1], story["ref"][1:] + story["ref"][0]],
             "boss": [f"{f} in head office" for f in FIRST]}
    out = []
    for key in (keys or rng.sample(list(ASKS), n))[:n]:
        wrong = [x for x in dict.fromkeys(pools[key]) if x != story[key]]
        options = rng.sample(wrong, 2) + [story[key]]
        rng.shuffle(options)
        out.append({"key": key, "ask": ASKS[key], "options": options, "answer": options.index(story[key])})
    return out


def _face(rng, story: dict | None = None, n: int = FACE_QUESTIONS) -> dict:
    """The Face's puzzle: learn the cover story (the crew's, in a room), then the guard asks about it with it hidden.
    Every one starts with the story, so whoever steps in for Terry on a Face job gets to learn it too."""
    story = story or _story(rng)
    return {"questions": _questions(rng, story, n), "ready": False, "answers": [], "story": story}


def _best_load(bags: list[dict], cap: int) -> int:
    best = [0] * (cap + 1)
    for b in bags:
        for w in range(cap, b["w"] - 1, -1):
            best[w] = max(best[w], best[w - b["w"]] + b["v"])
    return best[cap]


def _greedy_load(bags: list[dict], cap: int) -> int:
    """What the obvious way loads: the best value for weight first, while it fits."""
    v = w = 0
    for b in sorted(bags, key=lambda b: -b["v"] / b["w"]):
        if w + b["w"] <= cap:
            v, w = v + b["v"], w + b["w"]
    return v


def _muscle(rng, bags: int = BAGS) -> dict:
    """Bags of loot and a van that can't take them all. Dealt until the obvious load (the best value for weight
    first) falls short of the best, so it takes working out rather than a glance."""
    deal = None
    for _ in range(60):
        out = []
        for n in rng.sample(LOOT, bags):
            w = rng.randint(2, 9)
            out.append({"name": n, "w": w, "v": max(5, int(w * rng.uniform(3, 9)) + rng.randint(-6, 12))})
        cap = max(10, int(sum(b["w"] for b in out) * 0.45))
        best = _best_load(out, cap)
        deal = {"bags": out, "cap": cap, "best": best}
        if _greedy_load(out, cap) <= best * 0.93:
            break
    return deal


MAKERS = {"hacker": _hacker, "safecracker": _safecracker, "lookout": _lookout, "wheelman": _wheelman, "face": _face,
          "muscle": _muscle}


# what a thing's size plays as: a small thing (most of the rooms' and every spare) gets a game's middle size, since
# its smallest was over before it had started; the vault's big things stay the hardest
PLAYS = {"s": "m"}


def _make(role: str, size: str, rng, story: dict, game: str | None = None) -> dict:
    """A thing in a room's puzzle, at its size: the role's first game (below), or another from heist_games."""
    if game == "loud" or (game or "").startswith("loose-"):
        size = "m" if size == "big" else "s"           # a loud task's quick: the vault door's a bit more, that's all
    else:
        size = PLAYS.get(size, size)
    g = heist_games.get(role, game)
    if g:
        return {**g.make(rng, size, story), "game": g.key}
    k = SIZES[role].get(size) or SIZES[role]["s"]
    if role == "hacker":
        return _hacker(rng, k["n"], k["symbols"], k["tries"])
    if role == "safecracker":
        return _safecracker(rng, k["tumblers"], k["listens"])
    if role == "lookout":
        return _lookout(rng, k["steps"])
    if role == "wheelman":
        return _wheelman(rng, k["grid"], k["blocks"])
    if role == "face":
        return _face(rng, story, k["n"])
    return _muscle(rng, k["bags"])


def _public(role: str, p: dict) -> dict:
    """What the player sees of their puzzle."""
    g = heist_games.get(role, p.get("game"))
    if g:
        return {"game": g.key, "title": g.title, "g": g.public(p)}
    return {"game": heist_games.FIRST[role], "title": heist_games.FIRST_TITLES[heist_games.FIRST[role]], **_first_public(role, p)}


def _first_public(role: str, p: dict) -> dict:
    if role == "hacker":
        return {"len": p.get("len", HACK_LEN), "symbols": p.get("symbols", HACK_SYMBOLS), "tries": p.get("tries", HACK_TRIES),
                "guesses": [{"code": g["code"], "exact": g["exact"], "near": g["near"]} for g in p["guesses"]]}
    if role == "safecracker":
        return {"tumblers": len(p["secret"]), "open": p["open"], "dials": p.get("listens", DIALS),
                "log": [{"n": d["n"], "t": d["t"], "said": d["said"], "way": d["way"]} for d in p["dials"]]}
    if role == "lookout":
        return {"seq": p["seq"], "steps": len(p["seq"]), **({"answer": p["answer"]} if "answer" in p else {})}
    if role == "wheelman":
        return {"grid": len(p["costs"]), "costs": p["costs"], **({"path": p["path"], "cost": p.get("cost")} if "path" in p else {})}
    if role == "face":
        # the page shows the story a line at a time and the guard won't wait long, so a screenshot's no crib sheet
        out = {"ready": p["ready"], "count": len(p["questions"]), "answers": p["answers"], "patience": FACE_WAIT}
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
    """How a puzzle went, 0 to 1."""
    g = heist_games.get(role, p.get("game"))
    if g:
        return max(0.0, min(1.0, float(g.score(p))))
    if role == "hacker":
        n = p.get("len", HACK_LEN)
        for i, g in enumerate(p["guesses"]):
            if g["exact"] == n:
                return HACK_SCORE[min(i, len(HACK_SCORE) - 1)]
        return 0.1 * max((g["exact"] for g in p["guesses"]), default=0)
    if role == "safecracker":
        n, listens = len(p["dials"]), p.get("listens", DIALS)
        spare = max(1, listens - 5 * len(p["secret"]))          # listens over a good pace cost a little
        return (p["open"] / len(p["secret"])) * max(0.6, min(1.0, 1 - 0.2 * max(0, n - (listens - spare)) / spare))
    if role == "lookout":
        got = 0
        for a, b in zip(p.get("answer", []), p["seq"]):
            if a != b:
                break
            got += 1
        return got / len(p["seq"])
    if role == "wheelman":
        cost = p.get("cost")
        return (p["best"] / cost) ** 2 if cost else 0.0          # a fifth over the best route scores about 0.7
    if role == "face":
        return sum(p["questions"][i]["answer"] == a for i, a in enumerate(p["answers"])) / len(p["questions"])
    if role == "muscle":
        if "load" not in p:
            return 0.0
        w = sum(p["bags"][i]["w"] for i in p["load"])
        v = sum(p["bags"][i]["v"] for i in p["load"])
        return 0.0 if w > p["cap"] else v / p["best"] if p["best"] else 0.0
    return 0.0


def _done(role: str, p: dict) -> bool:
    g = heist_games.get(role, p.get("game"))
    if g:
        return bool(g.done(p))
    if role == "hacker":
        return len(p["guesses"]) >= p.get("tries", HACK_TRIES) or any(g["exact"] == p.get("len", HACK_LEN) for g in p["guesses"])
    if role == "safecracker":
        return p["open"] >= len(p["secret"]) or len(p["dials"]) >= p.get("listens", DIALS)
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
    g = heist_games.get(role, p.get("game"))
    if g:
        g.move(p, body if isinstance(body, dict) else {})
    elif role == "hacker":
        code, n, symbols = str(body.get("code") or ""), p.get("len", HACK_LEN), p.get("symbols", HACK_SYMBOLS)
        if len(code) != n or any(ch not in "123456789"[:symbols] for ch in code):
            raise Refuse(f"{n} digits, each 1 to {symbols}.")
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
        said = "click" if gap <= 1 else "hot" if gap <= 6 else "warm" if gap <= 15 else "cold"
        p["dials"].append({"n": n, "t": p["open"], "said": said, "way": 0 if said == "click" else (1 if s > n else -1)})
        if said == "click":
            p["open"] += 1
    elif role == "lookout":
        seq = body.get("seq")
        if not isinstance(seq, list) or len(seq) > len(p["seq"]) or not all(isinstance(x, int) and 0 <= x < 9 for x in seq):
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
        if body.get("slow") is True:
            p["answers"].append(-1)             # he got tired of waiting: wrong
            return
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
    g = len(costs)
    if not isinstance(path, list) or not path or len(path) > g * g:
        return None
    cells = []
    for c in path:
        if not isinstance(c, list) or len(c) != 2 or not all(isinstance(x, int) for x in c):
            return None
        cells.append((c[0], c[1]))
    if cells[0] != (g - 1, 0) or cells[-1] != (0, g - 1) or len(set(cells)) != len(cells):
        return None
    total = 0
    for (r0, c0), (r1, c1) in zip(cells, cells[1:]):
        if abs(r0 - r1) + abs(c0 - c1) != 1 or not (0 <= r1 < g and 0 <= c1 < g) or costs[r1][c1] is None:
            return None
        total += costs[r1][c1]
    return total


# ---- the job ---------------------------------------------------------------------------------
# The crew picks a way in and goes through the building a room at a time. Every room has a few things in the way,
# each a job for one role: whoever holds the role does it, quietly (the puzzle) or loudly (straight through, but
# the alarm jumps); a role nobody holds is left to Two-Pint Terry, unless someone free steps in to do it themselves
# (a little worse, not being the expert). Everything that goes less than perfectly adds to one alarm for the whole
# job, by its weight; loot jobs swell the vault. The alarm is the job score's other side, J = 1 - alarm, so the
# vault's maths (and the bank's edge) are exactly as they were.

MOVE_SECONDS = 6.0           # walking from one room to the next (the page shows how the last one went)
STEP_IN = 0.8                # someone doing a job that isn't theirs does it this well
TERRY_AFTER = (8.0, 16.0)    # how long Terry fiddles before he's done (or not)
ALARM_BLOWN = 0.85           # an alarm this loud and the police are on their way: straight to the vault
LOOT_STEP = 0.12             # a perfect loot job makes the vault hold this much more (of its cap)
LOOT_MAX = 0.36              # and three of them at most, which the stake limits allow for
STYLES = ("quiet", "loud")   # the crew's call at the plan, with the way in
SAFE_QUIET = (4.0, 12.0)     # a quiet job's safe time in the vault: a botched job's, up to a clean one's
# a loud job: one breach timer for the crew, each player on their own few tasks at their own pace
LOUD_TASKS = 3               # each player's tasks (their own things on the way in first, then spares)
LOUD_BREACH = (60.0, 15.0)   # the breach timer: this, and this for each room on the way in (and a run-down's worth)
LOUD_CUT = 5.0               # every task done, by anyone, takes this off the breach timer (times how well it went)...
LOUD_SAFE = (3.0, 1.0, 15.0)  # ...and adds this to the vault's safe time: from a base, up to a cap
LOUD_LOOT = 0.18             # a loud loot task grabs more than a quiet one (LOOT_STEP), up to the same LOOT_MAX
LOUD_EDGE = (0.08, 0.04)     # a loud job's edge, botched to perfect: its floor's higher than a quiet one's...
LOOSE_MAX = 0.03             # ...by loose cash: a player picks up 3% of the buy-in a job at most,
LOOSE_EACH = (3, 15, 4)      # each bonus task a few UKPence: 3 to 15, nearer the top the bigger the buy-in, the luck of
LOOSE_TOP_STAKE = 1000       # the draw over the last 4 of it, times how well it's done (and never past LOOSE_MAX)
LOOSE_TASKS = 4              # bonus tasks a player can do in a breach
LOOSE_KINDS = ["atm", "till", "box"]
BOT_LOUD = (6.0, 14.0)       # how long a bot crewmate takes over each loud task
# quick chat: a few set things to shout to the crew (no free text), one every CHAT_EVERY seconds each, shown for a bit
CHAT = {"hurry": "Hurry up!", "nice": "Nice one!", "well": "Well done!", "help": "Help!", "leg": "Leg it!",
        "oops": "Oops", "terry": "Where's Terry?", "kettle": "Put the kettle on", "brian": "It's Brian!",
        "shh": "Shh!", "greed": "Hold your nerve!", "grab": "Grab it all!"}
CHAT_EVERY, CHAT_SHOW, CHAT_KEEP = 2.5, 4.0, 12
# the vault's grab
GRAB_WINDOW = 0.5            # a grab keeps your bag up with the piles for this long after it (two a second will do)
GRAB_LAG = 2.0               # grabs sent in more than this late (a slow connection's) are dropped
# gold bars: now and then, for anyone mashing GRAB faster than it can use, it locks and a bar turns up somewhere in
# the vault to tap instead
BAR_EVERY = (5.0, 9.0)       # seconds between them
BAR_FAST = 4                 # grabs in the second before one, to be mashing faster than GRAB can use (it's 2 a second)
BAR_DELAY = 0.4              # a bar shows this long after its moment, so grabs still on their way in count
BAR_WINDOW = 1.5             # how long it's there to be caught
BAR_GRACE = 0.3              # a grab this soon after it shows is forgiven; any later fumbles it
BAR_TOPUP = 0.5              # catching one tops the bag up by this much of what it's short of the piles...
BAR_HOLD = 4.0               # ...and keeps it up, hands free, for this long
WALKOFF_GRAB = 0.5           # someone who walked off: the crew bag a bit for them, badly
BOT_GRAB = (0.75, 1.0)       # how well a bot crewmate keeps its bag up, from its skill


def loud_edge(score: float) -> float:
    """A loud job's edge, from how well its tasks went (0 to 1). Its best (LOUD_EDGE[1]) is the best quiet edge plus
    LOOSE_MAX, so a perfect loud job with all its loose cash returns no more than a perfect quiet one."""
    return LOUD_EDGE[0] - (LOUD_EDGE[0] - LOUD_EDGE[1]) * max(0.0, min(1.0, score))


def _present(room: dict) -> list[int]:
    return [u for u in room["players"] if u not in room["left"]]


def _seated(uid: int) -> dict | None:
    return next((r for r in _rooms.values() if not r.get("over") and uid in _present(r)), None)


def _bot(room: dict, uid) -> dict | None:
    return (room.get("bots") or {}).get(str(uid))


def _loud(room: dict) -> bool:
    return room.get("style") == "loud"


def _fill_with_bots(room: dict, rng: random.Random) -> None:
    """A practice job: every role nobody took gets a bot crewmate, with a skill level, how well it grabs, and the
    moment in the vault (as a share of its time) it'll leg it at. They back whatever plan the crew picks."""
    names = rng.sample(BOT_NAMES, SEATS)
    bots = {}
    for i, role in enumerate([r for r in ROLES if r not in room["roles"].values()]):
        uid = -(i + 1)
        # some bottle it early, some wait for the cap
        leave = rng.choice([rng.uniform(0.05, 0.3), rng.uniform(0.25, 0.6), rng.uniform(0.5, 0.95), 1.0])
        skill = round(rng.uniform(*BOT_SKILL), 2)
        grab = BOT_GRAB[0] + (BOT_GRAB[1] - BOT_GRAB[0]) * (skill - BOT_SKILL[0]) / (BOT_SKILL[1] - BOT_SKILL[0])
        bots[str(uid)] = {"name": names[i], "skill": skill, "leave": round(leave, 3), "grab": round(grab, 3)}
        room["players"].append(uid)
        room["roles"][str(uid)] = role
    room["bots"] = bots


def _bots_leg(room: dict, now: float) -> None:
    """Bot crewmates in the vault get out at their moment (a share of the climb, after the safe time), if the police
    haven't come first."""
    v = room["vault"]
    gone = now - v["start"]
    for uid, b in (room.get("bots") or {}).items():
        t = v["safe"] + b["leave"] * v["secs"]
        if uid in v["out"] or b["leave"] >= 1.0 or t > gone or (v["police"] is not None and t >= v["police"]):
            continue
        held = bag(room, int(uid), t)
        v["out"][uid] = [round(t, 3), _floor2(held), shown(held, v["base"])]
        _bot_says(room, int(uid), "leg", v["start"] + t)


def _chat(room: dict, uid: int, key: str, now: float) -> bool:
    """Put a quick chat line up for the crew, if it's one of CHAT and they haven't said something in CHAT_EVERY."""
    if key not in CHAT:
        return False
    said = room.setdefault("chat", [])
    if any(int(u) == uid and now - at < CHAT_EVERY for u, _, at in said):
        return False
    said.append([uid, key, round(now, 2)])
    del said[:-CHAT_KEEP]
    return True


def _bot_says(room: dict, uid: int, key: str, now: float, chance: float = 0.2) -> None:
    """A bot crewmate pipes up now and then."""
    if key and _bot(room, uid) is not None and _rng().random() < chance:
        _chat(room, uid, key, now)


def _free_role(room: dict) -> str | None:
    taken = set(room["roles"].values())
    return next((r for r in ROLES if r not in taken), None)


def _route_of(room: dict) -> dict:
    return rooms_of.routes(room["target"])[room.get("approach") or next(iter(rooms_of.routes(room["target"])))]


def _things(room: dict):
    for i, rm in enumerate(room.get("rooms", [])):
        for th in rm["things"]:
            yield i, th


def _all_things(room: dict):
    """Every thing in the job, a loud job's loose cash too."""
    yield from (th for _, th in _things(room))
    yield from room.get("extra", [])


def _weights(room: dict) -> dict:
    """How much each role's work counts in the job, from the route's rooms (and the twist)."""
    w: dict[str, float] = {r: 0.0 for r in ROLES}
    if room.get("rooms"):
        for _, th in _things(room):
            w[th["role"]] += th["w"]
    else:
        twist = (room.get("twist") or {}).get("role")
        for key in _route_of(room)["rooms"]:
            for role, _, size in rooms_of.ROOMS[key][1]:
                w[role] += rooms_of.WEIGHT[size] * (2 if role == twist else 1)
    total = sum(w.values()) or 1.0
    return {k: v / total for k, v in w.items()}


def job_score(room: dict) -> float:
    """The crew's job, 0 to 1: the alarm's other side."""
    return round(max(0.0, min(1.0, 1.0 - room.get("alarm", 1.0))), 4)


def _holder(room: dict, role: str) -> int | None:
    return next((int(u) for u, r in room["roles"].items() if r == role and int(u) not in room["left"]), None)


def _on_something(room: dict, uid: int) -> bool:
    """On something in this room already (it's theirs and not done)."""
    at = room.get("at", 0)
    return any(th["who"] == uid and th["state"] in ("ready", "active")
               for i, th in _things(room) if i == at)


def _vote_for(room: dict, key: str, options) -> str:
    """The most votes in room[key] among options; a tie goes to the host's pick, and nobody voting to the first."""
    votes = [v for v in room.get(key, {}).values() if v in options]       # the bot crewmates back whatever's picked
    if not votes:
        return next(iter(options))
    top = max(votes.count(k) for k in options)
    best = [k for k in options if votes.count(k) == top]
    mine = room.get(key, {}).get(str(room["host"]))
    return mine if mine in best else best[0]


def _start_job(room: dict, now: float) -> None:
    """The plan's agreed: take the way in most of the crew voted for, quiet or loud likewise, lay out its rooms,
    and walk in."""
    room["approach"] = _vote_for(room, "votes", rooms_of.routes(room["target"]))
    room["style"] = _vote_for(room, "styles", STYLES)
    loud = _loud(room)
    rng = _rng()
    room["story"] = room.get("story") or _story(rng)
    twist = (room.get("twist") or {}).get("role")
    held = {r for u, r in room["roles"].items() if int(u) not in room["left"]}
    plan = [(i, key, rooms_of.things(key, i, held)) for i, key in enumerate(_route_of(room)["rooms"])]
    if loud:
        plan = _loud_plan(room, plan)
    # a different game for each of a role's things, so nobody plays the same one twice in a heist (a loud job's
    # are all its role's quick bit of action)
    games = iter(lambda: "loud", None) if loud else iter(heist_games.deal(rng, [role for _, _, here in plan for role, _, _, _ in here]))
    layout = []
    for i, key, here in plan:
        things = []
        for j, (role, name, size, spare) in enumerate(here):
            things.append({"id": f"{i}-{j}", "role": role, "name": name, "size": size, "spare": spare, "mode": room["style"],
                           "w": rooms_of.WEIGHT[size] * (2 if role == twist else 1), "state": "waiting", "who": None,
                           "p": _make(role, size, rng, room["story"], next(games)), "terry": round(rng.uniform(0.0, 0.6), 3)})
        layout.append({"key": key, "things": things})
    # the alarm's weighed over the building's own things, so the roles nobody took (Terry's) cost the crew as much
    # however many spares the players have; a spare done badly only adds to it
    total = sum(th["w"] for rm in layout for th in rm["things"] if not th["spare"]) or 1.0
    for rm in layout:
        for th in rm["things"]:
            th["w"] = round(th["w"] / total, 5)
    room.update(state="job", rooms=layout, alarm=0.0, loot=0.0)
    if loud:
        _start_breach(room, now)
    else:
        _enter(room, 0, now)


def _loud_plan(room: dict, plan: list) -> list:
    """A loud job's tasks: for each of the crew, LOUD_TASKS of their own things on the way in (the building's first,
    the most important of them, then spares), in the order they come. Nothing for Terry: nobody needs him."""
    keep: dict[tuple[int, int], int] = {}
    for u in _present(room):
        role = room["roles"].get(str(u))
        mine = [(i, j, spare, rooms_of.WEIGHT[size]) for i, _, here in plan
                for j, (r, _, size, spare) in enumerate(here) if r == role]
        for i, j, _, _ in sorted(mine, key=lambda x: (x[2], -x[3], x[0]))[:LOUD_TASKS]:
            keep[(i, j)] = u
    return [(i, key, [t for j, t in enumerate(here) if (i, j) in keep]) for i, key, here in plan]


def _room_secs(rm: dict) -> float:
    """The room's clock: what its own things take to play, and the longest run-down in it on top (rm["brief"]), so
    reading how a puzzle's played never eats into playing it."""
    return rooms_of.room_seconds([(t["role"], t["name"], t["size"]) for t in rm["things"] if not t.get("spare")]) \
        + rm.get("brief", 0.0)


def _game_key(th: dict) -> str:
    """Which run-down a thing gets: its game's ("role:game"), a loud task's role's ("loud:role"), or the loose
    cash's kind ("loose:atm")."""
    g = th["p"].get("game")
    if g == "loud":
        return f"loud:{th['role']}"
    if (g or "").startswith("loose-"):
        return f"loose:{g[6:]}"
    return f"{th['role']}:{g or heist_games.FIRST[th['role']]}"


def _brief_secs(uid: int, th: dict, now: float) -> float:
    """How long the run-down before a thing would be for this player: the full one the first time they meet its game
    (or the first in a month), a quick one with a one-line reminder after."""
    seen = _met.get(str(uid), {}).get(_game_key(th))
    return BRIEF_FULL if seen is None or now - float(seen) > BRIEF_FORGET else BRIEF_SHORT


def _start(th: dict, uid: int, now: float) -> None:
    """Someone starts on a thing: the run-down first, then its clock (it takes no moves till then, or till GOT IT)."""
    secs = _brief_secs(uid, th, now)
    _met.setdefault(str(uid), {})[_game_key(th)] = round(now)
    th.update(state="active", who=uid, go=round(now + secs, 2), brief="full" if secs == BRIEF_FULL else "short")


def _enter(room: dict, i: int, now: float) -> None:
    """Walk into room i (a quiet job): everything in it goes to whoever holds its role, who gets straight on with it
    (its run-down first), or to Terry. The room's clock grows by the longest run-down any player gets in it."""
    rm = room["rooms"][i]
    there = now + MOVE_SECONDS
    rng = _rng()
    secs = _room_secs(rm)
    longest = 0.0
    for th in rm["things"]:
        who = _holder(room, th["role"])
        b = _bot(room, who) if who is not None else None
        if b is not None:
            # a bot crewmate gets straight on with it, done at a moment of its own with a score near its skill
            th.update(state="active", who=who, at=round(there + rng.uniform(*BOT_PACE) * secs, 2),
                      bot=round(max(0.0, min(1.0, rng.gauss(b["skill"], 0.12))), 4))
        elif who is not None:
            longest = max(longest, _brief_secs(who, th, there))
            _start(th, who, there)
        else:
            th.update(state="terry", who=None, at=round(there + rng.uniform(*TERRY_AFTER), 2))
    rm["brief"] = longest
    room.update(at=i, arrive=there, deadline=there + _room_secs(rm))


# ---- a loud job: the breach ----------------------------------------------------------------------
# One timer for the whole crew. Everyone has a few tasks (their own things on the way in), done one after another at
# their own pace; every task done, by anyone, takes time off the timer and adds safe time in the vault. Someone whose
# tasks are done picks up loose cash (bonus tasks that pay them a little, there and then) till the timer runs out,
# and then the whole crew's in the vault, done or not.

def _start_breach(room: dict, now: float) -> None:
    there = now + MOVE_SECONDS
    t0 = LOUD_BREACH[0] + LOUD_BREACH[1] * len(room["rooms"]) + BRIEF_FULL
    for i, th in _things(room):
        th.update(i=i, who=_holder(room, th["role"]))
    room.update(at=0, arrive=there, deadline=there + t0, extra=[], loose={},
                breach={"t0": t0, "cut": 0.0, "safe": LOUD_SAFE[0], "done": 0})
    for u in _present(room):
        _next_task(room, u, there)


def _tasks(room: dict, uid: int) -> list[dict]:
    """A player's own tasks in a loud job, in order."""
    return [th for _, th in _things(room) if th["who"] == uid]


def _current(room: dict, uid: int) -> dict | None:
    """What a player's on now: in a loud job, their task or loose cash; in a quiet one, their thing in this room."""
    if _loud(room):
        return next((th for th in _all_things(room) if th["who"] == uid and th["state"] == "active"), None)
    return next((th for i, th in _things(room) if i == room.get("at") and th["who"] == uid and th["state"] == "active"), None)


def _loose_each(room: dict) -> float:
    """The most a bonus task can pay: 3 UKPence on the smallest buy-in up to 15 on a 1,000 one (none on a free job).
    What one does pay is drawn from the 4 below that (never under 3), times how well it went."""
    if room["stake"] <= 0:
        return 0.0
    lo, hi, _ = LOOSE_EACH
    return lo + (hi - lo) * min(1.0, room["stake"] / LOOSE_TOP_STAKE)


def _loose_left(room: dict, uid: int) -> int:
    """How much loose cash a player can still pick up this job: none on a free job, LOOSE_MAX of the buy-in at most."""
    return max(0, int(room["stake"] * LOOSE_MAX) - room.get("loose", {}).get(str(uid), 0))


def _next_task(room: dict, uid: int, now: float) -> None:
    """Start a player's next task: their own, then loose cash while there's any to be had."""
    if uid in room["left"] or _current(room, uid) is not None:
        return
    nxt = next((th for th in _tasks(room, uid) if th["state"] == "waiting"), None)
    b = _bot(room, uid)
    if nxt is not None:
        if b is not None:
            nxt.update(state="active", at=round(now + _rng().uniform(*BOT_LOUD), 2),
                       bot=round(max(0.0, min(1.0, _rng().gauss(b["skill"], 0.12))), 4))
        else:
            _start(nxt, uid, now)
        return
    extra = room.setdefault("extra", [])
    mine = [th for th in extra if th["who"] == uid]
    if b is not None or len(mine) >= LOOSE_TASKS or _loose_left(room, uid) <= 0:
        return
    from lib.activities.heist_games.loud import LOOSE_NAMES
    kind = LOOSE_KINDS[len(mine) % len(LOOSE_KINDS)]
    th = {"id": f"x{len(extra)}", "role": room["roles"][str(uid)], "name": LOOSE_NAMES[kind], "size": "s", "spare": True,
          "mode": "loose", "w": 0.0, "state": "waiting", "who": uid, "i": len(room["rooms"]) - 1,
          "p": _make("loose", "s", _rng(), room["story"], f"loose-{kind}")}
    extra.append(th)
    _start(th, uid, now)


def _breach(room: dict, now: float) -> bool:
    """Move a loud job's breach on: bot crewmates finish their tasks at their moments, and anyone free gets their next.
    When nobody's anything left to do, the timer's done."""
    changed = False
    for th in [t for t in _all_things(room) if t["state"] == "active" and t.get("bot") is not None and now >= t["at"]]:
        _settle(room, th, th["bot"])
        _bot_says(room, th["who"], "nice" if th["bot"] >= 0.85 else "oops" if th["bot"] < 0.4 else "", th["at"])
        _next_task(room, th["who"], th["at"])
        changed = True
    for u in _present(room):
        if _current(room, u) is None:
            before = len(room.get("extra", []))
            _next_task(room, u, now)
            changed = changed or len(room.get("extra", [])) != before or _current(room, u) is not None
    if now >= room["arrive"] and not any(t["state"] == "active" and t["who"] not in room["left"] for t in _all_things(room)):
        room["deadline"] = min(room["deadline"], now)
    return changed


def _pay_loose(room: dict, uid: int, amount: int) -> None:
    """Loose cash, straight to the player who picked it up: saved first, so it can't be paid twice. Play money in
    practice (nothing moves)."""
    if amount <= 0:
        return
    loose = room.setdefault("loose", {})
    loose[str(uid)] = loose.get(str(uid), 0) + amount
    if room.get("practice") or uid < 0:
        return
    _save()
    try:
        from commands.economy.casino_base import credit_from_bank
        credit_from_bank(int(uid), amount, "Bank Job loose cash")
    except Exception:
        log.error("couldn't pay loose cash on bank job %s", room["id"], exc_info=True)


def _settle(room: dict, th: dict, score: float) -> None:
    """A thing done, however it went. Quiet: into the alarm by its weight. Loud: off the breach timer and onto the
    vault's safe time. Loose cash: paid out. Loot makes the vault hold more."""
    raw = max(0.0, min(1.0, score))
    th.update(state="done", score=round(raw, 4))
    mode = th.get("mode")
    if mode == "loose":
        pay = _rng().uniform(max(LOOSE_EACH[0], _loose_each(room) - LOOSE_EACH[2]), _loose_each(room)) if raw > 0 else 0.0
        _pay_loose(room, th["who"], min(_loose_left(room, th["who"]), int(round(pay * raw))))
        return
    if mode == "loud":
        b = room["breach"]
        cut = LOUD_CUT * raw
        b.update(cut=round(b["cut"] + cut, 2), done=b["done"] + 1,
                 safe=round(min(LOUD_SAFE[2], b["safe"] + LOUD_SAFE[1] * raw), 2))
        room["deadline"] = round(room["deadline"] - cut, 2)
    else:
        room["alarm"] = round(min(1.0, room["alarm"] + th["w"] * (1 - raw)), 5)
    if th["size"] == "loot":
        room["loot"] = round(min(LOOT_MAX, room["loot"] + (LOUD_LOOT if mode == "loud" else LOOT_STEP) * raw), 4)


def _played(th: dict) -> float:
    """How a puzzle someone was doing went, as it stands (a step-in does it a bit worse; a bot crewmate's is rolled)."""
    if th.get("bot") is not None:
        return th["bot"]
    return _score(th["role"], th["p"]) * (STEP_IN if th.get("stepped") else 1.0)


def _end_room(room: dict, now: float) -> None:
    """The room's done or out of time: anything unfinished is scored as it stands."""
    for i, th in _things(room):
        if i != room["at"] or th["state"] == "done":
            continue
        if th["state"] == "active":
            _settle(room, th, _played(th))
        elif th["state"] == "terry":
            _settle(room, th, th["terry"])
        else:
            _settle(room, th, 0.0)
    last = room["at"] >= len(room["rooms"]) - 1
    if last or room["alarm"] >= ALARM_BLOWN:
        if not last:
            room["blown"] = room["at"]            # the alarm went: they ran for the vault from here
            for i, th in _things(room):
                if th["state"] != "done":
                    _settle(room, th, 0.0)
        _end_job(room, now)
    else:
        _enter(room, room["at"] + 1, now)


def _end_job(room: dict, now: float) -> None:
    """Out of the building and at the vault: score the job, swell the vault by the loot, and roll when the police come.
    Quiet: the score's the alarm's other side and the safe time grows with it. Loud: the score's how well the tasks
    went (one not done scores 0) and the safe time's what the crew earned in the breach."""
    for th in _all_things(room):
        if th["state"] == "active" and th.get("bot") is None:
            _settle(room, th, _played(th))
        elif th["state"] != "done":
            _settle(room, th, 0.0)
    if _loud(room):
        tasks = [th for _, th in _things(room)]
        score = round(sum(th["score"] for th in tasks) / len(tasks), 4) if tasks else 0.0
        room["alarm"] = round(1 - score, 5)
        e, safe = loud_edge(score), room["breach"]["safe"]
    else:
        score = job_score(room)
        e, safe = edge(score), SAFE_QUIET[0] + (SAFE_QUIET[1] - SAFE_QUIET[0]) * score
    u = (secrets.randbelow(2 ** 52) + 1) / 2 ** 52
    m = police_take(e, u)
    cap = round(room["cap"] * (1 + room.get("loot", 0.0)), 2)
    secs = round(_route_of(room)["secs"] * (room.get("twist") or {}).get("pace", 1.0), 2)
    base, safe = 1.0 - e, round(safe, 2)
    room.update(state="report", deadline=now + REPORT_SECONDS, score=score, edge=round(e, 4), cap=cap,
                vault={"start": now + REPORT_SECONDS, "secs": secs, "safe": safe, "base": base,
                       "police": police_time(m, cap, secs, base, safe), "out": {}, "grab": {}, "bar": {}})
    rng = _rng()
    room["vault"]["bars"] = _bar_plan(room["vault"], rng)
    for b in (room.get("bots") or {}).values():           # a bot crewmate catches some, by its skill
        b["bars"] = [rng.random() < b["skill"] for _ in room["vault"]["bars"]]


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
            everyone = all(str(u) in room["votes"] and str(u) in room.get("styles", {})
                           for u in _present(room) if not _bot(room, u))
            if now < room["deadline"] and not everyone:
                break
            _start_job(room, min(now, room["deadline"]))
            _emit("job", room)
        elif s == "job" and _loud(room):
            changed = _breach(room, min(now, room["deadline"])) or changed
            if now < room["deadline"]:
                break
            _end_job(room, room["deadline"])
            _emit("report", room)
        elif s == "job":
            here = [th for i, th in _things(room) if i == room["at"]]
            for th in here:
                if th["state"] == "terry" and now >= th["at"]:
                    _settle(room, th, th["terry"])
                    changed = True
                elif th["state"] == "active" and th.get("bot") is not None and now >= th["at"]:
                    _settle(room, th, th["bot"])
                    _bot_says(room, th["who"], "nice" if th["bot"] >= 0.85 else "oops" if th["bot"] < 0.4 else "", th["at"])
                    changed = True
                elif th["state"] in ("ready", "active") and th["who"] in room["left"]:
                    th.update(state="terry", who=None, at=now)           # walked off: Terry picks it up
                    changed = True
            if now < room["deadline"] and not all(th["state"] == "done" for th in here):
                break
            _end_room(room, min(now, room["deadline"]) if now >= room["deadline"] else now)
            _emit("room" if room["state"] == "job" else "report", room)
        elif s == "report":
            if now < room["deadline"]:
                break
            room["state"] = "vault"
            _emit("vault", room)
        else:
            v = room["vault"]
            end = v["start"] + _vault_end(v)
            _bots_leg(room, min(now, end))
            everyone_out = all(str(u) in v["out"] for u in room["players"])
            if now < end and not everyone_out:
                break
            _finish(room, min(now, end))
        changed = True
    return changed


# ---- the vault -----------------------------------------------------------------------------------

def _vault_end(v: dict) -> float:
    """Seconds after the vault opens that it's over: the police, or the piles run out at the cap."""
    whole = v["safe"] + v["secs"]
    return whole if v["police"] is None else min(v["police"], whole)


def _bar_plan(v: dict, rng: random.Random) -> list[dict]:
    """When the vault's gold bars turn up, and where (as percentages of the vault's floor): one every BAR_EVERY seconds
    or so. Kept here; the page is only ever told about the one showing now."""
    out, t, end = [], rng.uniform(2.5, 5.0), v["safe"] + v["secs"] - BAR_WINDOW - 1.0
    while t < end:
        out.append({"t": round(t, 2), "x": rng.randint(14, 86), "y": rng.randint(8, 92)})
        t += rng.uniform(*BAR_EVERY)
    return out


def _bot_bars(room: dict, uid: int, t: float) -> int:
    """How many gold bars a bot crewmate has caught by t (each was a toss of a coin weighted by its skill)."""
    b, v = _bot(room, uid), room["vault"]
    return sum(1 for k, bar in enumerate(v.get("bars", [])) if bar["t"] + BAR_WINDOW <= t and k < len(b.get("bars", [])) and b["bars"][k])


def bag(room: dict, uid: int, t: float) -> float:
    """What uid's bag holds t seconds into the vault, as a multiple of the buy-in. A grab keeps it up with the piles for
    GRAB_WINDOW, so it's what the piles gained over the time it was kept up (plus what a gold bar topped it back up
    by, which is never past the piles): never more than the piles themselves. A bot crewmate keeps up so well (its
    "grab", and each bar it catches closes half the gap); someone who walked off has the crew bag a bit for them."""
    v, cap = room["vault"], room["cap"]
    t = max(0.0, min(t, v["safe"] + v["secs"]))
    b = _bot(room, uid)
    if b is not None:
        short = (1.0 - b.get("grab", 1.0)) * (1.0 - BAR_TOPUP) ** _bot_bars(room, uid, t)
        return (1.0 - short) * pile_at(v, cap, t)
    if uid in room["left"]:
        return WALKOFF_GRAB * pile_at(v, cap, t)
    g = v.get("grab", {}).get(str(uid))
    if not g:
        return 0.0
    a, z = min(g["a"], t), min(g["b"], t)
    return g["done"] + pile_at(v, cap, z) - pile_at(v, cap, a)


def _cover(room: dict, g: dict, x: float, until: float) -> None:
    """Keep a bag up with the piles from x until ``until`` (a grab's GRAB_WINDOW, or a gold bar's hold)."""
    v = room["vault"]
    if x <= g["b"]:
        g["b"] = max(g["b"], until)
    else:
        g["done"] = round(g["done"] + pile_at(v, room["cap"], g["b"]) - pile_at(v, room["cap"], g["a"]), 6)
        g.update(a=x, b=until)


def _bars(room: dict, uid: int, t_now: float) -> None:
    """Gold bars for uid, up to now. A bar is theirs to catch if they were mashing GRAB faster than it can use when it
    turned up (BAR_FAST grabs or more in the second before it shows, which is BAR_DELAY after its moment so grabs
    still on their way in count): it's something for the taps the cap was throwing away. While it's showing, GRAB is
    locked and the bag's kept up for them, so going for the bar costs nothing. Someone grabbing at the cap's pace or
    under never sees one, and loses nothing by it: a bar only ever helps a bag that's fallen behind the piles."""
    v = room["vault"]
    g = v.get("grab", {}).get(str(uid))
    mine = v.setdefault("bar", {}).setdefault(str(uid), {})
    for k, bar in enumerate(v.get("bars", [])):
        show = bar["t"] + BAR_DELAY
        if str(k) in mine or t_now < show or show >= _vault_end(v):
            continue
        if g and sum(1 for x in g.get("recent", []) if show - 1.0 <= x <= show) >= BAR_FAST:
            mine[str(k)] = "live"
            _cover(room, g, show, show + BAR_WINDOW)
        else:
            mine[str(k)] = "none"                    # not grabbing: no bar for them


def _bar_now(room: dict, uid: int, t: float) -> tuple[int, dict, str] | None:
    """The gold bar that's on for uid at t, if any: (which, the bar, live | caught | fumbled)."""
    v = room["vault"]
    mine = v.get("bar", {}).get(str(uid), {})
    for k, bar in enumerate(v.get("bars", [])):
        show = bar["t"] + BAR_DELAY
        if show <= t < show + BAR_WINDOW and mine.get(str(k), "none") != "none":
            return k, bar, mine[str(k)]
    return None


def _catch(room: dict, uid: int, k, x, t_now: float) -> None:
    """A tap on gold bar k at x seconds: caught, if it's theirs, still showing and not fumbled. Catching it tops the
    bag up by BAR_TOPUP of what it's short of the piles (so never past them) and keeps it up, hands free, for
    BAR_HOLD. The bot decides all of it: a page can't grant itself a bar, or one that isn't showing."""
    v = room["vault"]
    g = v.get("grab", {}).get(str(uid))
    if not g or isinstance(k, bool) or not isinstance(k, int) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return
    x = min(float(x), t_now)
    now = _bar_now(room, uid, x)
    if now is None or now[0] != k or now[2] != "live" or x > _vault_end(v):
        return
    v["bar"][str(uid)][str(k)] = "caught"
    g["done"] = round(g["done"] + BAR_TOPUP * max(0.0, pile_at(v, room["cap"], x) - bag(room, uid, x)), 6)
    _cover(room, g, x, x + BAR_HOLD)
    g["bars"] = g.get("bars", 0) + 1


def _grab(room: dict, uid: int, taps, t_now: float) -> None:
    """Grabs from the page, each its time in seconds since the vault opened: in order, none from the future or more
    than GRAB_LAG late, none after the police came. Each keeps the bag up for GRAB_WINDOW after it. A grab while a
    gold bar's showing (GRAB is locked then) doesn't count, and past the first BAR_GRACE of it, fumbles the bar."""
    if not isinstance(taps, list):
        return
    v = room["vault"]
    g = v.setdefault("grab", {}).get(str(uid))
    lo = max(0.0, t_now - GRAB_LAG)
    stop = min(t_now, _vault_end(v))
    for x in taps[:400]:
        if not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x):
            continue
        x = float(x)
        if x < lo or x > stop or (g and x < g["last"]):
            continue
        if g is None:
            g = v["grab"][str(uid)] = {"done": 0.0, "a": x, "b": x + GRAB_WINDOW, "last": x, "recent": [x]}
            continue
        _bars(room, uid, x)
        on = _bar_now(room, uid, x)
        if on is not None:
            g["last"] = x
            _cover(room, g, x, x + GRAB_WINDOW)
            if on[2] == "live" and x >= on[1]["t"] + BAR_DELAY + BAR_GRACE:
                v["bar"][str(uid)][str(on[0])] = "fumbled"
            continue
        _cover(room, g, x, x + GRAB_WINDOW)
        g["last"] = x
        g["recent"] = [*g.get("recent", [])[-7:], round(x, 3)]
    _bars(room, uid, t_now)


def _close(room: dict, how: str, now: float) -> None:
    """A crew that never started: nobody paid in, so nothing to give back."""
    room.update(over=True, how=how, ended=now)
    _emit(how, room)


def outcome(room: dict) -> dict:
    """How it went for each of the crew: {uid: {"how": escaped|cleared|nicked, "take", "payout", "loose"}}. The take is
    the bag: what they'd grabbed by when they got out (or by the end, for those still in when the police never came).
    Loose cash (a loud job's) was paid as they picked it up, so it's theirs whatever happens in the vault."""
    v, stake = room["vault"], room["stake"]
    police_came = v["police"] is not None and v["police"] < v["safe"] + v["secs"]
    loose = room.get("loose", {})
    out = {}
    for u in room["players"]:
        left = v["out"].get(str(u))
        if left:
            take = left[1]
            out[str(u)] = {"how": "escaped", "take": take, "payout": int(stake * take),
                           "shown": left[2] if len(left) > 2 else shown(take, v["base"])}
        elif not police_came:
            held = bag(room, u, v["safe"] + v["secs"])
            take = _floor2(held)
            out[str(u)] = {"how": "cleared", "take": take, "payout": int(stake * take), "shown": shown(held, v["base"])}
        else:
            out[str(u)] = {"how": "nicked", "take": 0.0, "payout": 0, "shown": 0.0}
        out[str(u)]["loose"] = int(loose.get(str(u), 0))
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
    if v["police"] is not None and v["police"] < v["safe"] + v["secs"]:
        v["police_take"] = _floor2(pile_at(v, room["cap"], v["police"]))
        v["police_shown"] = shown(pile_at(v, room["cap"], v["police"]), v["base"])
    if room.get("practice"):
        # play money: nothing paid, no bonus, no heat, no records, no badges, no post
        room.update(over=True, ended=now, how="clean" if clean else "nicked" if nicked else "done", results=results,
                    bonus={}, paid=True)
        room["heat"] = round(heat(now), 3)
        _save()
        return
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
                casino_stats.record_result(int(u), GAME, stake, stake, r["payout"] + r["loose"], r["how"])
        except Exception:
            log.warning("couldn't log bank job %s to the casino stats", room["id"], exc_info=True)
    try:
        from lib.activities import archive
        ranked = sorted(results, key=lambda u: -results[u]["take"])
        archive.keep(GAME, room["id"], room["target"], stake, room["how"], [
            {"user_id": int(u), "place": ranked.index(u) + 1, "payout": results[u]["payout"] + results[u]["loose"] if stake else 0,
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


def _crewmate(room: dict, uid: int, name_of) -> dict:
    b = _bot(room, uid)
    if b is not None:
        return {"uid": str(uid), "name": b["name"], "bot": True, "skill": b["skill"]}
    return _person(uid, name_of)


def _practice_stake(target: str) -> int:
    """The biggest play-money buy-in a practice job can have (the bank's reserves don't come into it)."""
    top = min(TARGETS[target]["max_stake"], int(_cfg("HEIST_MAX_STAKE", 1000)))
    return max((s for s in STAKES if s <= top), default=0)


def _target_view(key: str, now: float) -> dict:
    t = TARGETS[key]
    return {"key": key, "name": t["name"], "short": t["short"], "blurb": t["blurb"], "minCrew": t["min_crew"],
            "cap": cap_for(key, now), "baseCap": t["cap"], "tip": tip(now) == key, "maxStake": max_stake(key),
            "practiceStake": _practice_stake(key)}


def _thing_view(th: dict, now: float) -> dict:
    out = {"id": th["id"], "role": th["role"], "name": th["name"], "size": th["size"], "state": th["state"],
           "who": str(th["who"]) if th["who"] is not None else None, "mode": th.get("mode"), "stepped": bool(th.get("stepped")),
           "w": round(th["w"] * 100, 1)}               # its weight in the alarm, in points: it adds that times what it misses by
    if th["state"] == "done":
        out["score"] = th["score"]
    if th["state"] == "terry":
        out["terryIn"] = round(max(0.0, th["at"] - now), 1)
    return out


def _ways(room: dict) -> list[dict]:
    """The ways into the target, for the plan: each one's rooms, where they are, and who it leans on."""
    b = rooms_of.BUILDINGS[room["target"]]
    return [{"key": k, "name": w["name"], "leans": rooms_of.leans(room["target"], k),
             "rooms": [{"key": x, "name": rooms_of.ROOMS[x][0], "pin": b["pins"].get(x)} for x in w["rooms"]]}
            for k, w in b["routes"].items()]


def _role_scores(room: dict) -> list[dict]:
    """How each role did over the job (Terry's when nobody held it), weighted like the alarm."""
    out = []
    for role in ROLES:
        mine = [th for _, th in _things(room) if th["role"] == role]
        if not mine:
            continue
        w = sum(th["w"] for th in mine) or 1.0
        holder = _holder(room, role)
        out.append({"role": role, "uid": str(holder) if holder is not None else None,
                    "score": round(sum(th["w"] * th.get("score", 0.0) for th in mine) / w, 3), "dave": holder is None})
    return out


def view(room: dict, uid: int, name_of, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    s = room["state"]
    phase = "over" if room.get("over") else s
    me = str(uid)
    loud = _loud(room)
    out = {
        "id": room["id"], "host": str(room["host"]), "stake": room["stake"], "seats": SEATS, "phase": phase,
        "how": room.get("how"), "target": _target_view(room["target"], now) if s == "lobby" else
        {**_target_view(room["target"], now), "cap": room["cap"]},
        "practice": bool(room.get("practice")), "style": room.get("style"),
        "crew": [{**_crewmate(room, u, name_of), "role": room["roles"].get(str(u)), "left": u in room["left"],
                  "voted": (str(u) in room.get("votes", {}) and str(u) in room.get("styles", {})) or _bot(room, u) is not None,
                  # in the job: they've nothing left on (in a quiet job, this room's things; in a loud one, their tasks)
                  "done": bool(s == "job" and _current(room, u) is None
                               and not any(th["who"] == u and th["state"] == "ready" for _, th in _things(room)))}
                 for u in room["players"]],
        "expiresIn": max(0, round(room["expires"] - now)) if s == "lobby" else None,
        "deadlineIn": round(max(0.0, room.get("deadline", now) - now), 2) if s in ("plan", "job", "report") else None,
        "twist": room.get("twist"), "approach": room.get("approach"), "weights": _weights(room),
        "chat": [{"uid": str(u), "key": k, "text": CHAT[k], "at": at} for u, k, at in room.get("chat", [])
                 if now - at < CHAT_SHOW and k in CHAT],
        "chatKeys": [{"key": k, "text": t} for k, t in CHAT.items()],
    }
    if s == "plan":
        ways = rooms_of.routes(room["target"])
        out["votes"] = {k: list(room["votes"].values()).count(k) for k in ways}
        out["myVote"] = room["votes"].get(me)
        out["ways"] = _ways(room)
        styles = room.get("styles", {})
        out["styles"] = {k: list(styles.values()).count(k) for k in STYLES}
        out["myStyle"] = styles.get(me)
    if s == "job" and not room.get("over"):
        pins = rooms_of.BUILDINGS[room["target"]]["pins"]
        cur = _current(room, uid)
        if loud:
            # in a loud job everyone's somewhere different: you're in the room of the task you're on (or your last)
            mine_tasks = _tasks(room, uid)
            at = cur["i"] if cur else next((t["i"] for t in mine_tasks if t["state"] != "done"), len(room["rooms"]) - 1)
            listed = mine_tasks + [th for th in room.get("extra", []) if th["who"] == uid]
        else:
            at = room["at"]
            listed = room["rooms"][at]["things"]
        rm = room["rooms"][at]
        out["job"] = {"at": at, "count": len(room["rooms"]), "key": rm["key"], "name": rooms_of.ROOMS[rm["key"]][0],
                      "walk": round(max(0.0, room["arrive"] - now), 2), "alarm": 0.0 if loud else room["alarm"],
                      "loot": room["loot"], "loud": loud,
                      "secs": room["breach"]["t0"] if loud else _room_secs(rm), "brief": rm.get("brief", 0.0),
                      "cap": round(room["cap"] * (1 + room["loot"]), 2), "route": room.get("approach"),
                      "rooms": [{"key": x["key"], "name": rooms_of.ROOMS[x["key"]][0], "pin": pins.get(x["key"]),
                                 "done": j < at} for j, x in enumerate(room["rooms"])],
                      "things": [_thing_view(th, now) for th in listed]}
        if loud:
            b = room["breach"]
            out["job"]["breach"] = {"t0": b["t0"], "cut": b["cut"], "safe": b["safe"], "safeMax": LOUD_SAFE[2],
                                    "done": b["done"], "tasks": sum(1 for _ in _things(room))}
            out["job"]["progress"] = {str(u): [sum(1 for t in _tasks(room, u) if t["state"] == "done"), len(_tasks(room, u))]
                                      for u in room["players"]}
            out["job"]["loose"] = {"got": room.get("loose", {}).get(me, 0), "cap": int(room["stake"] * LOOSE_MAX),
                                   "each": min(int(round(_loose_each(room))), int(room["stake"] * LOOSE_MAX))}
        elif at > 0:
            # how the room just left went, for the walk to the next: every thing in it, and what it put on the alarm
            prev = room["rooms"][at - 1]
            out["job"]["last"] = {"name": rooms_of.ROOMS[prev["key"]][0], "things": [_thing_view(th, now) for th in prev["things"]],
                                  "alarm": round(100 * sum(th["w"] * (1 - th.get("score", 0.0)) for th in prev["things"]
                                                           if th["state"] == "done"))}
        if cur:
            pub = _public(cur["role"], cur["p"])
            out["puzzle"] = {"role": cur["role"], "ob": cur["id"], "name": cur["name"], "stepped": bool(cur.get("stepped")),
                             "done": False, "mode": cur.get("mode")}
            if now < cur.get("go", 0):
                # the run-down: what it is and how to play it, and nothing of the puzzle itself till its clock starts
                how, line = briefs.brief(_game_key(cur))
                full = cur.get("brief") == "full"
                out["puzzle"].update(game=pub["game"], title=pub["title"], briefing={
                    "full": full, "how": how, "line": line, "left": round(cur["go"] - now, 2),
                    "secs": BRIEF_FULL if full else BRIEF_SHORT})
            else:
                out["puzzle"].update(pub)
    if room.get("score") is not None:
        # the edge itself, so the page can show the police's odds (they come at take m with chance 1 - (1 - edge) / m)
        out["report"] = {"score": room["score"], "alarm": round((room["edge"] - EDGE_BEST) / (EDGE_WORST - EDGE_BEST), 3),
                         "edge": room["edge"], "waiting": False, "loud": loud, "safe": room.get("vault", {}).get("safe"),
                         "loose": room.get("loose", {}).get(me, 0), "how": briefs.brief("vault")[0],
                         "loot": room.get("loot", 0.0), "blown": room.get("blown"), "roles": _role_scores(room)}
        if room.get("vault"):
            # the bank's cut, taken up front: the vault's figures are multiples of what's left in play
            ip = in_play(room["stake"], room["vault"]["base"])
            out["report"].update(inPlay=ip, cut=room["stake"] - ip)
    if room.get("vault"):
        v = room["vault"]
        t = now - v["start"]
        g = v.get("grab", {}).get(me)
        # everything the page needs to draw the piles, your bag and the scanner itself (pile_at, bag, hazard); the
        # scanner's figure is the honest hazard, the same for any job like this one, so it says nothing of the draw
        out["vault"] = {"at": round(t, 2), "secs": v["secs"], "safe": v["safe"], "base": round(v["base"], 4),
                        "cap": room["cap"], "window": GRAB_WINDOW, "out": {u: x[1] for u, x in v["out"].items()},
                        "outX": {u: x[2] if len(x) > 2 else shown(x[1], v["base"]) for u, x in v["out"].items()},
                        "inPlay": in_play(room["stake"], v["base"]),
                        "bag": round(bag(room, uid, t), 4),
                        "grab": {"done": g["done"], "a": g["a"], "b": g["b"], "bars": g.get("bars", 0)} if g else None,
                        "risk": round(hazard(v, room["cap"], t), 4)}
        on = _bar_now(room, uid, t) if not room.get("over") and me not in v["out"] else None
        if on is not None:
            # the gold bar showing for you now (and only now: when the next one's due is the bot's to know)
            k, bar, state = on
            out["vault"]["bar"] = {"k": k, "x": bar["x"], "y": bar["y"], "state": state,
                                   "left": round(bar["t"] + BAR_DELAY + BAR_WINDOW - t, 2), "hold": BAR_HOLD}
        if room.get("over"):
            out["vault"]["police"] = v.get("police_take")
            out["vault"]["policeX"] = v.get("police_shown", shown(v["police_take"], v["base"]) if v.get("police_take") else None)
    if room.get("over") and room.get("results"):
        out["results"] = room["results"]
        out["verdicts"] = {u: verdict(room["stake"], room["vault"]["base"], x) for u, x in room["results"].items()}
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
        "you": _person(uid, name_of), "balance": balance, "stakes": STAKES, "practice": can_practise(uid),
        "targets": [_target_view(k, now) for k in TARGETS], "roles": [{"key": r, "name": ROLE_INFO[r][0],
                                                                       "job": ROLE_INFO[r][1]} for r in ROLES],
        "heat": {"level": round(h, 3), "label": heat_label(h)}, "tip": tip(now),
        "room": view(mine, uid, name_of, now) if mine else None,
        "open": [{"id": r["id"], "host": _person(r["host"], name_of), "players": len(_present(r)), "seats": SEATS,
                  "stake": r["stake"], "target": TARGETS[r["target"]]["short"], "expiresIn": max(0, round(r["expires"] - now)),
                  "roles": sorted(r["roles"].values(), key=ROLES.index)}
                 for r in _rooms.values() if r["state"] == "lobby" and not r.get("over") and uid not in r["players"]
                 and not r.get("practice")],
        "board": most_wanted(name_of, now), "bank": _reserves(),
    }


def _reserves() -> int | None:
    """What the Bank of HMS Victory holds, for the lobby to show what's there to steal (None if unreadable)."""
    try:
        from lib.economy.reserve_policy import bank_reserves
        return int(bank_reserves())
    except Exception:
        return None


def room(uid: int, rid: str, name_of) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None:
        raise Refuse("That crew has broken up.")
    if _advance(r, time.time()):
        _save()
    if uid not in r["players"]:
        raise Refuse("You aren't in that crew.")
    if r["state"] == "vault" and not r.get("over") and uid not in r["left"] and str(uid) not in r["vault"]["out"]:
        _bars(r, uid, time.time() - r["vault"]["start"])        # a gold bar that's just turned up for them
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


def _stake_for(target: str, stake, practice: bool = False) -> int:
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        raise Refuse("Pick a buy-in.")
    if target not in TARGETS:
        raise Refuse("Pick a target.")
    if practice:
        top = _practice_stake(target)
        if stake < 0 or stake > top or stake not in STAKES:
            raise Refuse(f"The {TARGETS[target]['short']} takes buy-ins up to {top:,}.")
        return stake
    top = max_stake(target)
    if stake < 0 or stake > top or stake not in STAKES:
        raise Refuse(f"The {TARGETS[target]['short']} takes buy-ins up to {top:,} UKPence." if top
                     else "The bank's too short to stake a job right now. Free jobs only.")
    return stake


def open_room(uid: int, stake, target="post", channel: int | None = None, role: str | None = None,
              practice: bool = False) -> dict:
    """Get a crew together; ``channel`` is where the game was opened, where its post goes. A practice
    crew (testers only) is play money, filled with bot crewmates when it goes in, and seen by nobody else."""
    _load()
    practice = bool(practice)
    if practice and not can_practise(uid):
        raise Refuse("Practice jobs are for the testers.")
    target = target if target in TARGETS else "post"
    stake = _stake_for(target, stake, practice)
    _busy(uid)
    if not practice:
        _check_stake(uid, stake)
    now = time.time()
    role = role if role in ROLES else "hacker"
    r = {"id": secrets.token_hex(4), "host": uid, "players": [uid], "left": [], "stake": stake, "target": target,
         "state": "lobby", "created": now, "expires": now + LOBBY_SECONDS, "roles": {str(uid): role}, "votes": {},
         "over": False, "ch": None if practice else int(channel) if channel else None, "practice": practice}
    _rooms[r["id"]] = r
    _save()
    _emit("open", r)
    return r


def join(uid: int, rid: str, role: str | None = None) -> dict:
    _load()
    r = _lobby_room(rid)
    if uid in r["players"]:
        return r
    if r.get("practice"):
        raise Refuse("That's someone's practice job.")
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
    new = _stake_for(target, r["stake"] if stake is None else stake, r.get("practice", False))
    if new > 0 and not r.get("practice"):
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
    if r.get("practice"):
        # play money, nothing to take in; bot crewmates make up the crew
        now = time.time()
        rng = _rng()
        _fill_with_bots(r, rng)
        r.update(state="plan", started=now, deadline=now + PLAN_SECONDS, cap=cap_for(r["target"], now),
                 twist=rng.choice(TWISTS), story=_story(rng))
        _save()
        return r
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
    rng = _rng()
    r.update(state="plan", started=now, deadline=now + PLAN_SECONDS, cap=cap_for(r["target"], now),
             twist=rng.choice(TWISTS), story=_story(rng))
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


def vote(uid: int, rid: str, approach: str | None = None, style: str | None = None) -> dict:
    """A vote at the plan: the way in, quiet or loud, or both. The plan's made when everyone's voted on both (or the
    clock runs out); the most votes win each, and the host breaks a tie."""
    r, now = _live(uid, rid)
    if r.get("over") or r["state"] != "plan":
        raise Refuse("The plan's already made.")
    if approach is None and style is None:
        raise Refuse("Pick a way in.")
    if approach is not None and approach not in rooms_of.routes(r["target"]):
        raise Refuse("Pick a way in.")
    if style is not None and style not in STYLES:
        raise Refuse("Quiet or loud?")
    if approach is not None:
        r["votes"][str(uid)] = approach
    if style is not None:
        r.setdefault("styles", {})[str(uid)] = style
    _advance(r, now)
    _save()
    return r


def play(uid: int, rid: str, body: dict) -> dict:
    """A move at one thing in the job (body["ob"]): your own start as you get to them, so it's cut its run-down short
    ({"go": true}) or a move at the puzzle you're on (refused till its run-down's over); in a quiet job, step in for
    Terry on one nobody's got ({"step": true}). In a loud job, finishing one starts your next (or loose cash)."""
    r, now = _live(uid, rid)
    if r.get("over") or r["state"] != "job":
        raise Refuse("The job isn't on.")
    if uid in r["left"]:
        raise Refuse("You've walked off the job.")
    body = body if isinstance(body, dict) else {}
    loud = _loud(r)
    if loud:
        th = next((t for t in _all_things(r) if t["id"] == str(body.get("ob"))), None)
    else:
        th = next((t for i, t in _things(r) if i == r["at"] and t["id"] == str(body.get("ob"))), None)
    if th is None:
        raise Refuse("That's not in this room.")
    if now < r["arrive"]:
        raise Refuse("Hang on, you're not in the room yet.")
    if body.get("step"):
        if loud or th["state"] != "terry":
            raise Refuse("Someone's already on that.")
        if _on_something(r, uid):
            raise Refuse("Finish what you're on first.")
        _start(th, uid, now)
        th["stepped"] = True
    elif body.get("go") is True:
        # GOT IT: cut the run-down short and start the clock now
        if th["who"] != uid or th["state"] != "active":
            raise Refuse("You're not on that one.")
        th["go"] = min(th.get("go", now), now)
    else:
        if th["who"] != uid or th["state"] != "active":
            raise Refuse("You're not on that one.")
        if now < th.get("go", 0):
            raise Refuse("Read the run-down first, or tap GOT IT.")
        _move(th["role"], th["p"], body)
        if _done(th["role"], th["p"]):
            _settle(r, th, _played(th))
            if loud:
                _next_task(r, uid, now)
    _advance(r, now)
    _save()
    return r


def say(uid: int, rid: str, key: str) -> dict:
    """Quick chat: one of CHAT to the whole crew. Too soon after the last one, or not one of them, and it's refused."""
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"] or r.get("over"):
        raise Refuse("You aren't in that crew.")
    if key not in CHAT:
        raise Refuse("You can't say that here.")
    if not _chat(r, uid, key, time.time()):
        raise Refuse("Give it a second.")
    _save()
    return r


def grab(uid: int, rid: str, taps, bar=None, at=None) -> dict:
    """Grabs at the vault's piles ({"taps": [seconds since the vault opened, ...]}), sent a few at a time by the page,
    and a tap on the gold bar that's showing ({"bar": which, "at": seconds})."""
    r, now = _live(uid, rid)
    v = r.get("vault")
    if r.get("over") or not v or r["state"] != "vault":
        raise Refuse("The vault's not open.")
    if str(uid) in v["out"]:
        raise Refuse("You're already out.")
    if uid in r["left"]:
        raise Refuse("You've walked off the job.")
    _grab(r, uid, taps, now - v["start"])
    if bar is not None:
        _catch(r, uid, bar, at, now - v["start"])
    _save()
    return r


def leg_it(uid: int, rid: str, taps=None) -> dict:
    """Get out of the vault with your bag as it stands (and any last grabs the page had still to send)."""
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
    if taps:
        _grab(r, uid, taps, t)
    held = bag(r, uid, t)
    # [when, the take (what's paid: stake x this), the take as the vault showed it (of the stake in play)]
    v["out"][str(uid)] = [round(t, 3), _floor2(held), shown(held, v["base"])]
    _advance(r, now)
    _save()
    _emit("out", r)
    return r
