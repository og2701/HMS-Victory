"""Paperboy's day beyond the score: the day's theme, its three jobs, and the wardrobe.

Every day has a theme by the day of the week (Bin Day, Roadworks Wednesday, the Sunday Papers...),
which changes what turns up in the road (paperboy_sim.RULES) and picks the day's lead job. Three jobs
a day, the same for everyone: each one done raises the player's multiplier for the rest of the day
(x1 up to x4: their scores count that many times over), and all three done on the run of days builds
a streak. Progress comes from the bot's own replay of each run, never from the page.

The papers a player collects (every run's, as the bot counted them) are spent in the wardrobe on
bikes, hoodies, caps, bags, trails and bells, all for show: nothing bought changes a score.
"""

from __future__ import annotations

import json
from datetime import date as _date, timedelta

from database import DatabaseManager
from lib.activities import paperboy_sim
from lib.activities.score_limits import rng

# ---- the day's theme ----------------------------------------------------------------------------

THEMES = {
    "drizzle": ("Drizzly Monday", "Wet roads all the way. Keep your head down and keep riding."),
    "bins": ("Bin Day", "Every wheelie bin on the street is out. Hop to it."),
    "works": ("Roadworks Wednesday", "Cones, barriers and scaffolding everywhere you look."),
    "walkies": ("Walkies Thursday", "Every dog in town is off the lead."),
    "payday": ("Payday Friday", "Golden papers turn up three times as often."),
    "stunts": ("Stunt Saturday", "Ramps all over the shop. Get some air."),
    "sunday": ("The Sunday Papers", "The big edition: every paper's worth double."),
}


def theme(iso: str) -> dict:
    key = paperboy_sim.theme_of(iso)
    name, line = THEMES[key]
    return {"key": key, "name": name, "line": line}


# ---- the jobs -----------------------------------------------------------------------------------
# key: (name, what it counts, over one round or the whole day, targets easy/medium/hard, the ask)
JOBS = {
    "air-mail": ("Air Mail", "ramps", "day", (2, 4, 6), "Take {n} ramps"),
    "hop-it": ("Hop It", "bins", "day", (6, 12, 20), "Hop {n} wheelie bins"),
    "limbo": ("Limbo Champion", "ducks", "run", (5, 9, 14), "Duck under {n} things in one round"),
    "golden-boy": ("Golden Boy", "golden", "day", (2, 4, 6), "Grab {n} golden papers"),
    "full-bag": ("Full Bag", "got", "run", (50, 100, 160), "Collect {n} papers in one round"),
    "long-round": ("The Long Round", "metres", "run", (400, 800, 1300), "Ride {n} m in one round"),
    "walkies": ("Walkies", "dogs", "day", (2, 4, 7), "Hop {n} dogs"),
    "magnetic": ("Magnetic Personality", "magnets", "day", (1, 2, 4), "Pick up {n} magnets"),
    "close-shave": ("Close Shave", "saves", "day", (1, 2, 3), "Let a helmet save you {n} times"),
    "front-page": ("Front Page", "score", "run", (150, 300, 600), "Score {n} in one round"),
    "double-up": ("Double Up", "doubled", "day", (10, 25, 45), "Collect {n} papers on double papers"),
    "hoppity": ("Hoppity", "hops", "day", (12, 25, 45), "Hop over {n} things"),
    "keen-bean": ("Keen Bean", "rounds", "day", (3, 5, 8), "Do {n} rounds"),
    "early-bird": ("Early Bird", "secs", "run", (36, 54, 72), "Still be riding at {clock} in one round"),
}
# the day's theme brings its own job, at medium
THEME_JOB = {"drizzle": "long-round", "bins": "hop-it", "works": "limbo", "walkies": "walkies",
             "payday": "golden-boy", "stunts": "air-mail", "sunday": "full-bag"}
MAX_MULT = 4
JOB_PAPERS = 25          # papers (to spend in the wardrobe) for each job done
STREAK_PAPERS = 50       # and for finishing all three, times the streak (up to a week)


def _clock(secs: int) -> str:
    mins = 5 * 60 + 30 + secs * 150 // 180
    return f"{mins // 60}:{mins % 60:02d} AM"


def jobs_for(iso: str) -> list[dict]:
    """The day's three jobs, the same for everyone: the theme's own at medium, and two drawn from
    the rest, one easy and one hard."""
    lead = THEME_JOB[paperboy_sim.theme_of(iso)]
    r = rng(f"paperboy:jobs:{iso}")
    rest = [k for k in JOBS if k != lead]
    picks = []
    while len(picks) < 2:
        k = rest[int(r() * len(rest))]
        if k not in picks:
            picks.append(k)
    out = []
    for key, level in ((picks[0], 0), (lead, 1), (picks[1], 2)):
        name, stat, scope, targets, ask = JOBS[key]
        n = targets[level]
        out.append({"key": key, "name": name, "stat": stat, "scope": scope, "target": n,
                    "text": ask.format(n=f"{n:,}", clock=_clock(n))})
    return out


def _metres(steps: int) -> int:
    """How far a run of `steps` rode, m (the bike's speed only depends on the step)."""
    return sum(paperboy_sim.speed_at(s) for s in range(1, steps + 1)) // 1000


def run_values(played: dict, times: int = 1) -> dict:
    """Everything a job can count, from one replayed run (its score as it counted, multiplier and all)."""
    return {**played.get("stats", {}), "metres": _metres(played["steps"]), "score": played["score"] * times,
            "secs": played["steps"] // paperboy_sim.HZ, "rounds": 1}


def _row(uid: int, iso: str) -> tuple[dict, int]:
    row = DatabaseManager.fetch_one("SELECT progress, done FROM paperboy_jobs WHERE user_id = ? AND date = ?", (str(uid), iso))
    if not row:
        return {}, 0
    try:
        return json.loads(row[0]), int(row[1])
    except ValueError:
        return {}, int(row[1])


def progress(uid: int, iso: str) -> list[dict]:
    """The day's jobs with how far this player's got on each."""
    got, _ = _row(uid, iso)
    return [{**j, "progress": min(j["target"], int(got.get(j["key"], 0))), "done": int(got.get(j["key"], 0)) >= j["target"]}
            for j in jobs_for(iso)]


def mult(uid: int, iso: str) -> int:
    """The player's multiplier today: one more for each job done."""
    return min(MAX_MULT, 1 + _row(uid, iso)[1])


def streak(uid: int, iso: str) -> int:
    """Days in a row with all three jobs done, up to today (or yesterday, while today's still open)."""
    rows = DatabaseManager.fetch_all("SELECT date FROM paperboy_jobs WHERE user_id = ? AND done >= 3 ORDER BY date DESC LIMIT 400",
                                     (str(uid),))
    days = {r[0] for r in rows}
    day = _date.fromisoformat(iso)
    if iso not in days:
        day -= timedelta(days=1)
    n = 0
    while day.isoformat() in days:
        n += 1
        day -= timedelta(days=1)
    return n


def record(uid: int, iso: str, played: dict) -> dict:
    """Count a replayed run towards the day's jobs. Returns the jobs it finished, the multiplier
    now, and the papers won for them."""
    got, done_before = _row(uid, iso)
    values = run_values(played, min(MAX_MULT, 1 + done_before))
    finished = []
    for j in jobs_for(iso):
        was = int(got.get(j["key"], 0))
        v = int(values.get(j["stat"], 0))
        now = max(was, v) if j["scope"] == "run" else was + v
        got[j["key"]] = now
        if was < j["target"] <= now:
            finished.append(j["name"])
    done = sum(1 for j in jobs_for(iso) if got.get(j["key"], 0) >= j["target"])
    DatabaseManager.execute(
        "INSERT INTO paperboy_jobs (user_id, date, progress, done) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, date) DO UPDATE SET progress = excluded.progress, done = excluded.done",
        (str(uid), iso, json.dumps(got), done))
    papers = JOB_PAPERS * len(finished)
    if done >= 3 > done_before:
        papers += STREAK_PAPERS * min(7, streak(uid, iso))
    if papers:
        _kit_row(uid)
        DatabaseManager.execute("UPDATE paperboy_kit SET bonus = bonus + ? WHERE user_id = ?", (papers, str(uid)))
    return {"jobsDone": finished, "mult": min(MAX_MULT, 1 + done), "jobPapers": papers}


# ---- the wardrobe -------------------------------------------------------------------------------
# id: (slot, name, papers, colour, or what it is)
WARDROBE = {
    "bike-red": ("bike", "Postbox Red", 0, "#D8262E"),
    "bike-green": ("bike", "Racing Green", 300, "#1F8A50"),
    "bike-blue": ("bike", "Royal Blue", 500, "#2456C8"),
    "bike-yellow": ("bike", "Sunshine", 500, "#F5C400"),
    "bike-pink": ("bike", "Bubblegum", 800, "#FF6FB5"),
    "bike-black": ("bike", "Midnight", 800, "#26262E"),
    "bike-gold": ("bike", "Solid Gold", 3000, "#E8B530"),
    "top-navy": ("top", "Navy Hoodie", 0, ""),
    "top-orange": ("top", "Tangerine", 300, "#FF8A2A"),
    "top-mint": ("top", "Mint", 300, "#6FD8A8"),
    "top-lilac": ("top", "Lilac", 500, "#A98CF0"),
    "top-hivis": ("top", "Hi-Vis", 800, "#C8F020"),
    "top-red": ("top", "Christmas Jumper", 1200, "#C8202A"),
    "cap-red": ("cap", "Red Cap", 0, ""),
    "cap-sky": ("cap", "Sky Blue", 200, "#5AB4FF"),
    "cap-lime": ("cap", "Lime", 200, "#8CE04A"),
    "cap-black": ("cap", "Black", 400, "#22222A"),
    "cap-white": ("cap", "White", 400, "#F4F4F0"),
    "cap-gold": ("cap", "Gold", 2000, "#E8B530"),
    "bag-yellow": ("bag", "Paper Bag", 0, ""),
    "bag-brown": ("bag", "Leather Satchel", 300, "#8A5A30"),
    "bag-red": ("bag", "Pillar Box Red", 500, "#D8262E"),
    "bag-purple": ("bag", "Purple", 500, "#7A4AD0"),
    "trail-none": ("trail", "No Trail", 0, "none"),
    "trail-paper": ("trail", "Paper Trail", 1000, "paper"),
    "trail-confetti": ("trail", "Confetti", 2000, "confetti"),
    "trail-sparkle": ("trail", "Sparkles", 3000, "sparkle"),
    "bell-ding": ("bell", "Ding Ding", 0, "ding"),
    "bell-horn": ("bell", "Bike Horn", 400, "horn"),
    "bell-duck": ("bell", "Rubber Duck", 600, "duck"),
    "bell-fanfare": ("bell", "Fanfare", 1000, "fanfare"),
}
SLOTS = ("bike", "top", "cap", "bag", "trail", "bell")
FREE = [k for k, v in WARDROBE.items() if v[2] == 0]
WEARING = {WARDROBE[k][0]: k for k in FREE}


class WardrobeRefuse(Exception):
    """A purchase or a change the wardrobe won't make, with why."""


def _kit_row(uid: int) -> tuple[list, dict, int, int]:
    row = DatabaseManager.fetch_one("SELECT owned, wearing, spent, bonus FROM paperboy_kit WHERE user_id = ?", (str(uid),))
    if not row:
        DatabaseManager.execute("INSERT OR IGNORE INTO paperboy_kit (user_id, owned, wearing, spent, bonus) VALUES (?, ?, ?, 0, 0)",
                                (str(uid), json.dumps([]), json.dumps({})))
        return [], {}, 0, 0
    return json.loads(row[0] or "[]"), json.loads(row[1] or "{}"), int(row[2]), int(row[3])


def balance(uid: int) -> int:
    """Papers to spend: every paper collected in a counted run, and those won from jobs, less what's spent."""
    _, _, spent, bonus = _kit_row(uid)
    row = DatabaseManager.fetch_one("SELECT COALESCE(SUM(papers), 0) FROM paperboy_runs WHERE user_id = ?", (str(uid),))
    return int(row[0] if row else 0) + bonus - spent


def kit(uid: int) -> dict:
    owned, wearing, _, _ = _kit_row(uid)
    return {"balance": balance(uid), "owned": sorted(set(FREE) | set(owned)),
            "wearing": {**WEARING, **{s: k for s, k in wearing.items() if k in WARDROBE}},
            "catalogue": [{"id": k, "slot": v[0], "name": v[1], "cost": v[2], "look": v[3]} for k, v in WARDROBE.items()]}


def buy(uid: int, item: str) -> dict:
    """Buy something and put it on."""
    if item not in WARDROBE:
        raise WardrobeRefuse("That's not in the wardrobe.")
    owned, wearing, spent, bonus = _kit_row(uid)
    if item in owned or item in FREE:
        return wear(uid, item)
    cost = WARDROBE[item][2]
    if balance(uid) < cost:
        raise WardrobeRefuse(f"You need {cost - balance(uid):,} more papers for that.")
    owned.append(item)
    wearing[WARDROBE[item][0]] = item
    DatabaseManager.execute("UPDATE paperboy_kit SET owned = ?, wearing = ?, spent = spent + ? WHERE user_id = ?",
                            (json.dumps(owned), json.dumps(wearing), cost, str(uid)))
    return kit(uid)


def wear(uid: int, item: str) -> dict:
    """Put on something already owned."""
    if item not in WARDROBE:
        raise WardrobeRefuse("That's not in the wardrobe.")
    owned, wearing, _, _ = _kit_row(uid)
    if item not in owned and item not in FREE:
        raise WardrobeRefuse("You haven't got that yet.")
    wearing[WARDROBE[item][0]] = item
    DatabaseManager.execute("UPDATE paperboy_kit SET wearing = ? WHERE user_id = ?", (json.dumps(wearing), str(uid)))
    return kit(uid)


def day(uid: int, iso: str) -> dict:
    """Everything the page shows beyond the score: the theme, the jobs, the multiplier, the streak, the wardrobe."""
    return {"theme": theme(iso), "jobs": progress(uid, iso), "mult": mult(uid, iso), "streak": streak(uid, iso), "kit": kit(uid)}
