"""UKP Kart: live kart races for up to eight in the activity, CPU karts filling the empty places.

A room is opened by its host, for a stake or free, and anyone can take a seat and pick a kart; the host
starts the race when they like (a staked race needs a second person). The grid's other places go to CPU
karts, which one racer's page drives for everyone (the "pilot"; another takes over if they go).

Every page drives its own kart and tells the bot where it is about fifteen times a second over a
WebSocket; the bot passes everyone's positions round and keeps its own count of how far each person has
got, capped at what their kart could do flat out, so a page that lies about its speed can't get round any
sooner than a perfect drive would. A finish counts once the bot's count says the laps are done, at the
time the page says (if that's within a second of when the bot heard about it).

Stakes go in when the host starts. The pot, less the PvP rake, goes to the people on the podium: with two
racers the winner takes it all, with three or four it's 70/30, with five or more 60/30/10. CPU karts are
ranked with everyone else on the screen but never take a share. A race nobody finishes gives every stake
back. Like Countdown, every read moves a room on to the present and a sweeper does the same for rooms
nobody's watching, so a race always ends; it's saved after every change that matters.
"""

import asyncio
import copy
import json
import logging
import os
import random
import secrets
import time

import config
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

GAME = "kart"
SEATS = 8
STAKES = [0, 50, 100, 250, 500, 1000]
LOBBY_SECONDS = 900          # a room nobody starts closes after this
KEEP_FINISHED = 1800
LOAD_SECONDS = 7.0           # from the host pressing start to the lights coming on: everyone loading in
COUNTDOWN = 3.6              # the lights, as src/kart/sim.ts COUNTDOWN
AFTER_FIRST = 30.0           # once the first person's home, the rest get this long
MAX_RACE = 420.0             # and nobody gets longer than this after the green
SPLITS = {2: [1.0], 3: [0.7, 0.3], 4: [0.7, 0.3]}
SPLIT_BIG = [0.6, 0.3, 0.1]

# The tracks and karts, as src/kart/track.ts and src/kart/sim.ts have them: the lap's length in metres
# (tests/test_kart.py checks it against tests/data/kart_tracks.json, made from the page's own code), and
# each kart's top speed in m/s, which is what caps how fast the bot believes someone's going.
TRACKS = {"village": {"length": 1247, "laps": 3}, "silverstone": {"length": 1453, "laps": 3},
          "wobbling": {"length": 1315, "laps": 3}, "clanking": {"length": 1377, "laps": 3},
          "wallop": {"length": 1376, "laps": 3}}
_last_track: str | None = None


def _pick_track() -> str:
    """A track for a new room, at random, never the one the last room was on."""
    global _last_track
    _last_track = random.choice([t for t in TRACKS if t != _last_track] or list(TRACKS))
    return _last_track
CARS = {"cab": 26.0, "bus": 27.4, "thomas": 27.2, "brum": 25.2, "mini": 25.6, "robin": 25.0, "wg": 26.0, "fryup": 25.6,
        "roadman": 24.6, "scooter": 24.0}
PACE = 1.12                  # how far over its top speed a kart may average (boosts, drafting)
BURST = 70.0                 # metres of slack for a burst of boosts or a late update
# The CPU drivers are British icons, seven picked at random for a race (the activity has the same list for practice,
# ukplace-activities src/kart/cpu.ts)
CPU_NAMES = [
    # off the telly
    "Gemma Collins", "Joey Essex", "Katie Price", "Kerry Katona", "Stacey Solomon", "Rylan Clark", "Danny Dyer",
    "Holly Willoughby", "Ant", "Dec", "Simon Cowell", "Piers Morgan", "Davina McCall", "Lorraine Kelly", "Alan Carr",
    "Paul Hollywood", "Mary Berry", "Prue Leith", "Gordon Ramsay", "Jamie Oliver", "Nigella Lawson", "Delia Smith",
    "Jeremy Clarkson", "Richard Hammond", "James May", "David Attenborough", "Bear Grylls", "Lord Sugar",
    "Bradley Walsh", "Richard Osman", "Stephen Fry", "Joanna Lumley", "Judi Dench", "Ricky Gervais", "Peter Kay",
    "Michael McIntyre", "Romesh Ranganathan", "Big Narstie", "Chris Packham", "Monty Don", "Kirstie Allsopp",
    "Phil Spencer", "Noel Edmonds", "Anne Robinson", "Gok Wan", "Sue Perkins",
    # pop stars
    "Rick Astley", "Elton John", "Tom Jones", "Adele", "Ed Sheeran", "Harry Styles", "Robbie Williams",
    "Liam Gallagher", "Noel Gallagher", "Stormzy", "Dizzee Rascal", "Craig David", "Shirley Bassey", "Mick Jagger",
    "Paul McCartney", "Posh Spice", "Scary Spice", "Sporty Spice", "Baby Spice", "Ginger Spice",
    # sport
    "David Beckham", "Wayne Rooney", "Peter Crouch", "Gary Lineker", "Andy Murray", "Tyson Fury", "Mo Farah",
    "Lewis Hamilton", "Nigel Mansell", "Jill Scott", "Luke Littler", "Phil Taylor", "Ronnie O'Sullivan", "Tom Daley",
    # and a few made up ones
    "Del Boy", "Mr Bean", "Hyacinth Bucket", "Alan Partridge", "Phil Mitchell", "Dot Cotton",
]

_FILE = os.path.join(config.JSON_DATA_DIR, "activity_kart.json")
_rooms: dict[str, dict] = {}
_posts: dict[str, list] = {}
_loaded = False
CLIENT = None
listeners: list = []


class Refuse(Exception):
    """Something the rules don't allow; the message is for the player."""


def max_stake() -> int:
    return int(getattr(config, "KART_MAX_STAKE", 5000))


# ---- keeping it ------------------------------------------------------------------------------

def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(_FILE) as f:
            raw = json.load(f)
        _rooms.update(raw.get("rooms", {}))
        _posts.update(raw.get("posts", {}))
    except FileNotFoundError:
        pass
    except Exception:
        log.error("couldn't read %s; starting with no kart rooms", _FILE, exc_info=True)


def _save() -> None:
    try:
        atomic_write_json(_FILE, {"rooms": _rooms, "posts": _posts})
    except Exception:
        log.error("couldn't save the kart rooms to %s", _FILE, exc_info=True)


def _emit(event: str, room: dict) -> None:
    for fn in listeners:
        try:
            fn(event, copy.deepcopy(room))
        except Exception:
            log.warning("a kart listener failed on %s", event, exc_info=True)


def post_for(rid: str) -> tuple[int | None, int] | None:
    _load()
    p = _posts.get(rid)
    if p is None:
        return None
    return p[0], int(p[1])


def remember_post(rid: str, message_id: int | None, channel: int | None = None) -> None:
    if message_id is None:
        return
    _posts[rid] = [int(channel) if channel else None, int(message_id)]
    _save()


def room_info(rid: str) -> dict | None:
    _load()
    r = _rooms.get(rid)
    return copy.deepcopy(r) if r else None


# ---- the room --------------------------------------------------------------------------------

def _present(room: dict) -> list[int]:
    return [u for u in room["players"] if u not in room["left"]]


def _seated(uid: int) -> dict | None:
    return next((r for r in _rooms.values() if not r.get("over") and uid in _present(r)), None)


def _car(car) -> str:
    return car if car in CARS else "cab"


def _check_stake(uid: int, stake: int, who: str = "You") -> None:
    from lib.economy.economy_manager import get_bb, wager_blocked_reason
    if stake <= 0:
        return
    if get_bb(uid) < stake:
        raise Refuse(f"You don't have {stake:,} UKPence." if who == "You" else f"{who} can't cover {stake:,} UKPence.")
    bot_id = getattr(getattr(CLIENT, "user", None), "id", None)
    if why := wager_blocked_reason(uid, stake, bot_id, name=who):
        raise Refuse(why.replace("**", ""))


def _busy(uid: int) -> None:
    if _seated(uid) is not None:
        raise Refuse("You're already in a race.")


def open_room(uid: int, stake, car=None, channel: int | None = None, track=None) -> dict:
    """Open a room; ``channel`` is where the game was opened, where its post goes. ``track`` is the host's pick,
    or (not one of the tracks) a random one."""
    _load()
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        raise Refuse("Pick a stake.")
    if stake < 0 or stake > max_stake():
        raise Refuse(f"Stakes go up to {max_stake():,} UKPence.")
    _busy(uid)
    _check_stake(uid, stake)
    now = time.time()
    r = {"id": secrets.token_hex(4), "host": uid, "players": [uid], "left": [], "cars": {str(uid): _car(car)},
         "stake": stake, "state": "lobby", "track": track if track in TRACKS else _pick_track(), "created": now,
         "expires": now + LOBBY_SECONDS,
         "over": False, "ch": int(channel) if channel else None}
    _rooms[r["id"]] = r
    _save()
    _emit("open", r)
    return r


def join(uid: int, rid: str, car=None) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None or r.get("over") or r["state"] != "lobby":
        raise Refuse("That race has already started or closed.")
    if uid in r["players"]:
        return r
    if len(r["players"]) >= SEATS:
        raise Refuse("That race is full.")
    _busy(uid)
    _check_stake(uid, r["stake"])
    r["players"].append(uid)
    r["cars"][str(uid)] = _car(car)
    _save()
    _emit("seats", r)
    return r


def pick_car(uid: int, rid: str, car) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"] or r["state"] != "lobby" or r.get("over"):
        raise Refuse("You can only change kart before the race.")
    r["cars"][str(uid)] = _car(car)
    _save()
    return r


def leave(uid: int, rid: str) -> dict:
    """Leave a room. Before it starts you just get up; the host leaving closes it. Once it's started
    your stake stays in the pot and you're out of the race."""
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"] or r.get("over"):
        raise Refuse("You aren't in that race.")
    now = time.time()
    if r["state"] == "lobby":
        if uid == r["host"]:
            _close(r, "closed", now)
        else:
            r["players"].remove(uid)
            r["cars"].pop(str(uid), None)
            _emit("seats", r)
    elif uid not in r["left"]:
        r["left"].append(uid)
        live = _live.get(rid)
        if live:
            live.gone(uid)
        _advance(r, now)
    _save()
    return r


def start(uid: int, rid: str) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None or r.get("over") or r["state"] != "lobby":
        raise Refuse("That race has already started or closed.")
    if uid != r["host"]:
        raise Refuse("Only the host can start the race.")
    stake = r["stake"]
    if stake > 0 and len(r["players"]) < 2:
        raise Refuse("A race for a stake needs someone to race.")
    if stake > 0:
        for u in r["players"]:
            _check_stake(u, stake, "You" if u == uid else "Someone in the race")
        from commands.economy.casino_base import credit_from_bank
        from lib.economy.economy_manager import remove_bb
        taken = []
        for u in r["players"]:
            if not remove_bb(u, stake, reason="UKP Kart stake"):
                for t in taken:
                    credit_from_bank(t, stake, "UKP Kart stake refund")
                raise Refuse("Someone in the race can't cover the stake any more.")
            taken.append(u)
    now = time.time()
    # CPU karts at the front of the grid, the people behind them in a random order
    people = list(r["players"])
    random.shuffle(people)
    cpus = SEATS - len(people)
    names = random.sample(CPU_NAMES, cpus)
    grid = [{"id": f"cpu{i}", "name": names[i], "car": random.choice(list(CARS)), "cpu": True, "slot": i} for i in range(cpus)]
    grid += [{"id": str(u), "car": r["cars"].get(str(u), "cab"), "cpu": False, "slot": cpus + i} for i, u in enumerate(people)]
    r.update(state="race", started=now, green=now + LOAD_SECONDS + COUNTDOWN, grid=grid, finish={}, prog={},
             pilot=uid, total=len(r["players"]))
    _save()
    _emit("start", r)
    return r


# ---- the race --------------------------------------------------------------------------------

def _track(r: dict) -> dict:
    return TRACKS.get(r.get("track"), TRACKS["village"])


def race_length(r: dict) -> float:
    t = _track(r)
    return t["length"] * t["laps"]


def _close(room: dict, how: str, now: float) -> None:
    """A room that never started: nobody staked, so nothing to give back."""
    room.update(over=True, how=how, ended=now)
    _emit(how, room)


def _advance(room: dict, now: float) -> bool:
    """Move a room on to now: lapse an unstarted one, end a race that's run its course."""
    if room.get("over"):
        return False
    if room["state"] == "lobby":
        if now >= room["expires"]:
            _close(room, "lapsed", now)
            return True
        return False
    green = room["green"]
    people = [str(u) for u in _present(room)]
    done = [u for u in people if u in room["finish"]]
    firsts = [t for u, t in room["finish"].items() if not u.startswith("cpu")]
    if (people and len(done) == len(people)) or not people \
            or (firsts and now - green >= min(firsts) + AFTER_FIRST) or now - green >= MAX_RACE:
        _settle(room, now)
        return True
    return False


def _lap(r: dict, lap) -> float | None:
    """A best lap a page reports, if it's a believable one: no quicker than the fastest kart flat out round a lap."""
    try:
        lap = float(lap)
    except (TypeError, ValueError):
        return None
    quickest = _track(r)["length"] / (max(CARS.values()) * 1.5)
    return round(lap, 3) if quickest <= lap < 600 else None


def _record(room: dict) -> None:
    """The race, kept for good in kart_races and kart_results: the track, the stake and the pot, then every kart in
    finishing order with its time, best lap and what it won. Best-effort: a failure here never touches the payout."""
    try:
        from database import DatabaseManager
        fin, laps, shares, left = room.get("finish", {}), room.get("laps", {}), room.get("shares", {}), set(room.get("left", []))
        place = {kid: i + 1 for i, kid in enumerate(order(room))}
        DatabaseManager.execute(
            "INSERT OR REPLACE INTO kart_races (id, mode, track, laps, stake, pot, outcome, host_id, channel_id, started, ended) "
            "VALUES (?, 'race', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (room["id"], room.get("track", "village"), _track(room)["laps"], room["stake"], room.get("pot", 0), room.get("how", "race"),
             str(room["host"]), str(room["ch"]) if room.get("ch") else None, int(room.get("green") or room.get("started") or 0),
             int(room.get("ended") or time.time())))
        for g in room.get("grid", []):
            kid = g["id"]
            DatabaseManager.execute(
                "INSERT OR REPLACE INTO kart_results (race_id, slot, user_id, name, car, place, finish_time, best_lap, payout, left_early) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (room["id"], g["slot"], None if g["cpu"] else kid, g.get("name") if g["cpu"] else None, g["car"], place.get(kid, len(place)),
                 fin.get(kid), laps.get(kid), int(shares.get(kid, 0)), 0 if g["cpu"] else int(int(kid) in left)))
    except Exception:
        log.error("couldn't keep kart race %s", room.get("id"), exc_info=True)


def record_practice(uid: int, body: dict) -> str:
    """A practice race the page ran on its own (CPU karts only), kept like a race against people. The page says how it
    went, so it's checked for being a race that could have happened, but no money ever rides on one."""
    track = str(body.get("track") or "")
    if track not in TRACKS:
        raise Refuse("That isn't a UKP Kart track.")
    field = body.get("field")
    if not isinstance(field, list) or not 1 <= len(field) <= SEATS:
        raise Refuse("That isn't a race.")
    length = TRACKS[track]["length"] * TRACKS[track]["laps"]
    rows, mine = [], 0
    for slot, k in enumerate(field):
        if not isinstance(k, dict):
            raise Refuse("That isn't a race.")
        cpu = bool(k.get("cpu"))
        mine += 0 if cpu else 1
        car = _car(k.get("car"))
        try:
            place = int(k.get("place"))
            t = float(k["time"]) if k.get("time") is not None else None
        except (TypeError, ValueError):
            raise Refuse("That isn't a race.")
        if not 1 <= place <= len(field) or (t is not None and not length / (CARS[car] * 1.5) <= t < 3600):
            raise Refuse("That isn't a race.")
        rows.append((slot, None if cpu else str(uid), str(k.get("name") or "")[:40] if cpu else None, car, place, t,
                     _lap({"track": track}, k.get("lap"))))
    if mine != 1:
        raise Refuse("That isn't a race.")
    from database import DatabaseManager
    rid = "p" + secrets.token_hex(6)
    now = int(time.time())
    DatabaseManager.execute(
        "INSERT INTO kart_races (id, mode, track, laps, stake, pot, outcome, host_id, channel_id, started, ended) "
        "VALUES (?, 'practice', ?, ?, 0, 0, 'race', ?, NULL, NULL, ?)", (rid, track, TRACKS[track]["laps"], str(uid), now))
    for slot, user, name, car, place, t, lap in rows:
        DatabaseManager.execute(
            "INSERT INTO kart_results (race_id, slot, user_id, name, car, place, finish_time, best_lap, payout, left_early) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0)", (rid, slot, user, name, car, place, t, lap))
    return rid


def order(room: dict) -> list[str]:
    """Everyone on the grid in finishing order: those home by their times, then the rest by how far they got."""
    fin = room.get("finish", {})
    prog = room.get("prog", {})
    ids = [g["id"] for g in room.get("grid", [])]
    home = sorted((i for i in ids if i in fin), key=lambda i: fin[i])
    rest = sorted((i for i in ids if i not in fin), key=lambda i: -prog.get(i, -1e9))
    return home + rest


def _split(n: int) -> list[float]:
    return SPLITS.get(n, SPLIT_BIG if n >= 5 else [1.0])


def _settle(room: dict, now: float) -> None:
    """End the race and pay the podium. Saved as paid before any money moves, so it can't pay twice."""
    if room.get("over"):
        return
    fin = room.get("finish", {})
    people = [g["id"] for g in room.get("grid", []) if not g["cpu"]]
    ranked = [i for i in order(room) if i in people]
    home = [i for i in ranked if i in fin]
    stake, n = room["stake"], len(room["players"])
    pot = stake * n
    split = _split(n)
    winners = [int(i) for i in home[:len(split)]]
    shares: dict[str, int] = {}
    if stake > 0 and winners:
        won = prize(pot)
        weights = split[:len(winners)]
        # a share nobody finished to claim goes to the winner
        weights[0] += 1 - sum(weights)
        for w, part in zip(winners, weights):
            shares[str(w)] = int(won * part)
        shares[str(winners[0])] += won - sum(shares.values())
    how = "race" if home else "refund"
    room.update(over=True, ended=now, how=how, winners=[str(w) for w in winners], shares=shares, ranked=ranked, pot=pot, paid=True)
    _save()
    try:
        from commands.economy.casino_base import credit_from_bank
        if stake > 0:
            if how == "refund":
                for u in room["players"]:
                    credit_from_bank(u, stake, "UKP Kart refund")
            else:
                from lib.economy.economy_manager import record_game_transfer
                for w in winners:
                    credit_from_bank(w, shares[str(w)], "UKP Kart winnings")
                losers = [u for u in room["players"] if u not in winners]
                won = sum(shares.values()) or 1
                for lo in losers:
                    for w in winners:
                        record_game_transfer(lo, w, stake * shares[str(w)] // won)
    except Exception:
        log.error("couldn't settle kart room %s", room["id"], exc_info=True)
    try:
        from lib.economy import pvp_stats
        if winners:
            for lo in [u for u in room["players"] if u != winners[0]]:
                pvp_stats.record_result(GAME, winners[0], lo, stake, "win")
    except Exception:
        log.warning("couldn't log kart room %s", room["id"], exc_info=True)
    _record(room)
    _emit("over", room)
    live = _live.get(room["id"])
    if live:
        live.over()


class Pace:
    """How far the bot believes someone has got: what their page says, but never faster than their kart
    could manage, with a little slack banked for bursts."""

    def __init__(self, top: float, start: float, at: float):
        self.rate = top * PACE
        self.prog = start
        self.at = at
        self.bank = 0.0
        self.trimmed = 0.0

    def report(self, prog: float, now: float) -> float:
        now = max(now, self.at)
        # flat out since the last word, plus whatever slack was saved up (only so much of it keeps)
        avail = self.bank + self.rate * (now - self.at)
        self.at = now
        step = prog - self.prog
        if step > avail:
            self.trimmed += step - avail
            step = avail
        self.bank = min(BURST, avail - max(0.0, step))
        self.prog += step
        return self.prog


def report(room: dict, uid: str, prog: float, now: float, pace: Pace) -> float:
    """A page's word on how far its kart has got: kept, capped, and checked for the line."""
    if room.get("over") or room["state"] != "race":
        return pace.prog
    if now < room["green"]:
        return pace.prog
    got = pace.report(float(prog), now)
    room["prog"][uid] = round(got, 1)
    return got


def claim_finish(room: dict, kid: str, at: float, now: float, pace: Pace | None) -> int | None:
    """A page says a kart's crossed the line for the last time. People need the bot's own count to agree;
    CPU karts (only the pilot's page sends theirs) are taken at their word: they never win anything.
    Returns the place, or None if it doesn't stand."""
    if room.get("over") or room["state"] != "race" or kid in room["finish"]:
        return None
    heard = now - room["green"]
    if not kid.startswith("cpu"):
        if pace is None or pace.prog < race_length(room) - 4:
            return None
        at = min(max(float(at), heard - 1.0), heard)
    else:
        at = min(float(at), heard)
    room["finish"][kid] = round(max(0.0, at), 3)
    _save()
    return sorted(room["finish"].values()).index(room["finish"][kid]) + 1


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
    if changed:
        _save()


async def run_sweeper(every: float = 1.0) -> None:
    while True:
        try:
            sweep()
        except Exception:
            log.error("kart sweep failed", exc_info=True)
        await asyncio.sleep(every)


# ---- what the page sees ----------------------------------------------------------------------

def prize(pot: int) -> int:
    from lib.economy.economy_manager import pvp_rake
    return pot - pvp_rake(pot) if pot else 0


def _person(uid: int, name_of) -> dict:
    return {"uid": str(uid), "name": name_of(uid) or "Someone"}


def view(room: dict, uid: int, name_of, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    pot = room["stake"] * len(room["players"])
    state = "over" if room.get("over") else room["state"]
    out = {
        "id": room["id"], "host": str(room["host"]), "stake": room["stake"], "pot": pot, "prize": prize(pot),
        "seats": SEATS, "state": state, "how": room.get("how"), "track": room.get("track", "village"),
        "laps": _track(room)["laps"], "now": now,
        "players": [{**_person(u, name_of), "car": room["cars"].get(str(u), "cab"), "left": u in room["left"]}
                    for u in room["players"]],
        "expiresIn": max(0, round(room["expires"] - now)) if room["state"] == "lobby" else None,
    }
    if room["state"] != "lobby":
        names = {str(u): name_of(u) or "Someone" for u in room["players"]}
        out.update(green=room["green"], pilot=str(room.get("pilot") or ""),
                   grid=[{**g, "name": g.get("name") or names.get(g["id"], "Someone"),
                          "left": (not g["cpu"]) and int(g["id"]) in room["left"]} for g in room["grid"]],
                   finish=room.get("finish", {}))
    if room.get("over") and room.get("how") in ("race", "refund"):
        out.update(order=order(room), winners=room.get("winners", []), shares=room.get("shares", {}))
    return out


def lobby(uid: int, name_of, balance: int) -> dict:
    _load()
    now = time.time()
    changed = _tidy(now)
    for r in list(_rooms.values()):
        changed = _advance(r, now) or changed
    if changed:
        _save()
    mine = _seated(uid)
    return {
        "game": "kart", "you": {"id": str(uid), "name": name_of(uid) or "You"}, "balance": balance,
        "stakes": STAKES, "maxStake": max_stake(), "room": view(mine, uid, name_of, now) if mine else None,
        "open": [{"id": r["id"], "host": _person(r["host"], name_of), "players": len(_present(r)), "seats": SEATS,
                  "stake": r["stake"], "expiresIn": max(0, round(r["expires"] - now))}
                 for r in _rooms.values() if r["state"] == "lobby" and not r.get("over") and uid not in r["players"]],
        "live": [{"id": r["id"], "host": _person(r["host"], name_of), "players": len(_present(r)), "stake": r["stake"],
                  "track": r.get("track", "village")}
                 for r in _rooms.values() if r["state"] == "race" and not r.get("over") and uid not in r["players"]],
    }


def watch(uid: int, rid: str, name_of) -> dict:
    """A race under way, for someone who wants to watch it (in it or not)."""
    _load()
    r = _rooms.get(rid)
    if r is not None and _advance(r, time.time()):
        _save()
    if r is None or r.get("over") or r["state"] != "race":
        raise Refuse("That race has finished.")
    return view(r, uid, name_of)


def room(uid: int, rid: str, name_of) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None:
        raise Refuse("That race has closed.")
    if _advance(r, time.time()):
        _save()
    if uid not in r["players"]:
        raise Refuse("You aren't in that race.")
    return view(r, uid, name_of)


# ---- the live race: one WebSocket per racer ----------------------------------------------------

HZ = 15                       # how often everyone's positions go round
MAX_MESSAGE = 4096            # bytes: a page's update for itself and up to seven CPU karts fits easily
EVENTS_PER_SECOND = 30        # item throws and hits a page may pass on
MAX_WATCHERS = 50             # people watching one race who aren't in it
MAX_THINGS = 64               # cones and the like on the road, kept for a watcher who arrives mid-race

_live: dict[str, "Live"] = {}


def _later(coro) -> None:
    """Send something without waiting for it; outside the bot's loop (tests, scripts) there's nobody to send to."""
    try:
        asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()


class Live:
    """A race in progress: who's connected, where every kart last said it was, and each person's pace."""

    def __init__(self, room: dict):
        self.rid = room["id"]
        self.socks: dict[int, object] = {}
        self.states: dict[str, list] = {}
        self.paces: dict[int, Pace] = {}
        self.task: asyncio.Task | None = None
        self.events: dict[int, list[float]] = {}
        self.away: set[int] = set()       # pages in the background (a phone flicked to another app): they can't drive CPUs
        # people watching who aren't in the race: they hear everything and say nothing, and never drive the CPUs
        self.watchers: dict[int, object] = {}
        # what's on the road (thrown cones, hubcaps in flight), so someone who starts watching mid-race sees it too
        self.things: dict[int, dict] = {}
        grid_len = race_length(room) / _track(room)["laps"]
        for g in room["grid"]:
            if g["cpu"]:
                continue
            # where the grid slot puts the kart: behind the line, so a little under nothing
            row, odd = divmod(g["slot"], 2)
            start = -(7 + row * 6 + odd * 3)
            self.paces[int(g["id"])] = Pace(CARS.get(g["car"], 26.0), start, room["green"])
        self.length = grid_len

    def room(self) -> dict | None:
        r = _rooms.get(self.rid)
        return r if r and r["state"] == "race" else None

    async def send(self, ws, msg: dict) -> None:
        try:
            await ws.send_str(json.dumps(msg, separators=(",", ":")))
        except Exception:
            pass

    async def everyone(self, msg: dict, but: int | None = None) -> None:
        text = json.dumps(msg, separators=(",", ":"))
        for socks in (self.socks, self.watchers):
            for uid, ws in list(socks.items()):
                if uid == but:
                    continue
                try:
                    await ws.send_str(text)
                except Exception:
                    socks.pop(uid, None)

    def pilot(self) -> int | None:
        r = _rooms.get(self.rid)
        return int(r["pilot"]) if r and r.get("pilot") else None

    def repilot(self) -> None:
        """The CPU karts need a page to drive them: the host's, or whoever's still connected."""
        r = _rooms.get(self.rid)
        if not r:
            return
        now = self.pilot()
        if now in self.socks and now not in r["left"] and now not in self.away:
            return
        new = next((u for u in _present(r) if u in self.socks and u not in self.away), None)
        if new is None and (now not in self.socks or now in r["left"]):
            new = next((u for u in _present(r) if u in self.socks), None)
        if new is not None and new != now:
            r["pilot"] = new
            _later(self.everyone({"t": "pilot", "id": str(new)}))

    def gone(self, uid: int) -> None:
        self.states.pop(str(uid), None)
        _later(self.everyone({"t": "gone", "id": str(uid)}))
        self.repilot()

    def over(self) -> None:
        r = _rooms.get(self.rid)
        if r is None:
            return
        out = {"t": "over", "order": order(r), "winners": r.get("winners", []), "shares": r.get("shares", {}),
               "how": r.get("how"), "laps": r.get("laps", {})}
        _later(self.everyone(out))

    async def tick(self) -> None:
        """Everyone's latest positions to everyone, HZ times a second, until the race is over and they've gone."""
        try:
            while True:
                await asyncio.sleep(1 / HZ)
                r = _rooms.get(self.rid)
                if r is None or (r.get("over") and not self.socks):
                    break
                if r.get("over"):
                    if time.time() - r.get("ended", 0) > 60:
                        break
                    continue
                if self.states and self.socks:
                    await self.everyone({"t": "w", "s": round(time.time(), 3), "k": self.states})
        finally:
            _live.pop(self.rid, None)

    def allowed(self, uid: int) -> bool:
        now = time.time()
        recent = [t for t in self.events.get(uid, []) if now - t < 1]
        if len(recent) >= EVENTS_PER_SECOND:
            return False
        self.events[uid] = recent + [now]
        return True

    async def handle(self, uid: int, msg: dict) -> None:
        r = _rooms.get(self.rid)
        if r is None:
            return
        kind = msg.get("t")
        now = time.time()
        if kind == "ping":
            ws = self.socks.get(uid)
            if ws is not None:
                await self.send(ws, {"t": "pong", "c": msg.get("c"), "s": now})
        elif kind == "s":
            karts = msg.get("k")
            if not isinstance(karts, dict):
                return
            mine, pilot = str(uid), self.pilot() == uid
            for kid, state in karts.items():
                if not isinstance(state, list) or len(state) > 16:
                    continue
                if kid == mine:
                    if uid in r["left"] or r.get("over"):
                        continue
                    pace = self.paces.get(uid)
                    if pace is not None and len(state) > 6 and isinstance(state[6], (int, float)):
                        report(r, mine, state[6], now, pace)
                    self.states[kid] = state
                elif pilot and kid.startswith("cpu"):
                    self.states[kid] = state
                    if len(state) > 6 and isinstance(state[6], (int, float)):
                        r["prog"][kid] = round(float(state[6]), 1)
        elif kind == "away":
            if msg.get("away"):
                self.away.add(uid)
            else:
                self.away.discard(uid)
            self.repilot()
        elif kind == "e":
            if self.allowed(uid):
                e = msg.get("e")
                if isinstance(e, dict):
                    th = e.get("th")
                    if e.get("k") == "add" and isinstance(th, dict) and isinstance(th.get("id"), int):
                        if len(self.things) >= MAX_THINGS:
                            self.things.pop(next(iter(self.things)))
                        self.things[th["id"]] = th
                    elif e.get("k") == "del" and isinstance(e.get("id"), int):
                        self.things.pop(e["id"], None)
                await self.everyone({"t": "e", "from": str(uid), "e": e}, but=uid)
        elif kind == "fin":
            kid = str(msg.get("id") or uid)
            if kid != str(uid) and not (kid.startswith("cpu") and self.pilot() == uid):
                return
            place = claim_finish(r, kid, msg.get("at") or 0, now, self.paces.get(uid) if kid == str(uid) else None)
            if place is not None:
                lap = _lap(r, msg.get("lap"))
                if lap:
                    r.setdefault("laps", {})[kid] = lap
            if place is not None:
                await self.everyone({"t": "fin", "id": kid, "at": r["finish"][kid], "place": place})
                if _advance(r, time.time()):
                    _save()


async def _watch(sock, live: Live, r: dict, uid: int, name_of):
    """Someone watching a race they aren't in: everything that goes round, from where it's got to, and nothing
    they send counts but a ping (to set their clock by)."""
    from aiohttp import WSMsgType
    if len(live.watchers) >= MAX_WATCHERS and uid not in live.watchers:
        await sock.send_str(json.dumps({"t": "no", "why": "Too many people are watching that race."}))
        await sock.close()
        return sock
    old = live.watchers.get(uid)
    live.watchers[uid] = sock
    if old is not None and old is not sock:
        await old.close()
    try:
        await sock.send_str(json.dumps({"t": "hi", "now": time.time(), "room": view(r, uid, name_of), "k": live.states,
                                        "things": list(live.things.values()), "watch": True}))
        async for m in sock:
            if m.type != WSMsgType.TEXT:
                continue
            try:
                msg = json.loads(m.data)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("t") == "ping":
                await live.send(sock, {"t": "pong", "c": msg.get("c"), "s": time.time()})
    finally:
        if live.watchers.get(uid) is sock:
            live.watchers.pop(uid, None)
    return sock


async def ws(request, read_session, name_of):
    """The race's WebSocket. The page's first message names itself with its session token (a browser
    can't put it in a header) and the room; everything after is the race."""
    from aiohttp import WSMsgType, web
    sock = web.WebSocketResponse(heartbeat=20, max_msg_size=MAX_MESSAGE)
    await sock.prepare(request)
    uid, live = None, None
    try:
        first = await asyncio.wait_for(sock.receive(), timeout=10)
        if first.type != WSMsgType.TEXT:
            await sock.close()
            return sock
        hello = json.loads(first.data)
        who = read_session(str(hello.get("s") or ""))
        _load()
        r = _rooms.get(str(hello.get("room") or ""))
        racing = who is not None and r is not None and who["uid"] in r["players"]
        watching = bool(hello.get("watch")) and who is not None and r is not None and not racing and not r.get("over")
        if r is None or r["state"] != "race" or not (racing or watching):
            await sock.send_str(json.dumps({"t": "no", "why": "That race isn't running."}))
            await sock.close()
            return sock
        uid = who["uid"]
        live = _live.get(r["id"])
        if live is None:
            live = _live[r["id"]] = Live(r)
            live.task = asyncio.ensure_future(live.tick())
        if watching:
            return await _watch(sock, live, r, uid, name_of)
        old = live.socks.get(uid)
        live.socks[uid] = sock
        if old is not None and old is not sock:
            await old.close()
        live.repilot()
        # where everyone last was, so a page that's reloaded mid-race picks its kart up where it left it
        await sock.send_str(json.dumps({"t": "hi", "now": time.time(), "room": view(r, uid, name_of), "k": live.states}))
        async for m in sock:
            if m.type != WSMsgType.TEXT:
                continue
            try:
                msg = json.loads(m.data)
            except ValueError:
                continue
            if isinstance(msg, dict):
                await live.handle(uid, msg)
    except asyncio.TimeoutError:
        pass
    except Exception:
        log.warning("kart socket failed", exc_info=True)
    finally:
        if live is not None and uid is not None and live.socks.get(uid) is sock:
            live.socks.pop(uid, None)
            live.repilot()
    return sock
