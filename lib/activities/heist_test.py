"""TEMP: /heisttest, so a Bank Job tester can play any one of its games on its own and tick it off.

Rip it all out once the games are tested: this file, tests/test_heist_test.py, the two hooks in heist.py (search
"heist_test"), the command in launcher.py (search "heisttest") and its line in docs/heist.md under Practice.

Picking a game opens a one-thing practice job (heist.open_room's rules: play money, nothing moves, never posted,
nobody else sees it) that drops the tester straight into that game, through the real rooms, run-downs, scoring and
screens: a quiet game in one room on its own, a loud task or loose cash as a one-task breach, or the vault's GRAB
straight after the report. Its score is on the report screen as usual, and kept here (best and last) for the
tester's checklist, /heisttest with no game (or "all")."""

import logging
import os
import secrets
import time

import config
from lib.activities import heist as H
from lib.activities import heist_games
from lib.activities import heist_rooms as rooms_of
from lib.activities.heist_games.loud import LOOSE, LOOSE_NAMES, LOUD
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

COMMAND = "heisttest"
DESCRIPTION = "TEMP Bank Job testers: play any one game on its own, or see your checklist"
OPTIONS = [{"type": 3, "name": "game", "description": "The game to play, or all for your checklist",
            "required": False, "autocomplete": True}]
ALL = "all"

_FILE = os.path.join(config.JSON_DATA_DIR, "activity_heist_test.json")
_played: dict[str, dict[str, dict]] = {}      # uid -> {entry key: {"best", "last", "n"}}
_loaded = False

SIZE_NAMES = {"s": "room", "big": "vault door", "loot": "loot"}


def _name(role: str) -> str:
    return H.ROLE_INFO[role][0].removeprefix("The ")


def _where(role: str, size: str) -> tuple[str, str, str, str]:
    """A real room with a thing of this role at this size as it plays (any of the role's, if none is that size):
    (target, way in, room, the thing's name)."""
    plays = H.PLAYS.get(size, size)
    for exact in (True, False):
        for key, (_, things) in rooms_of.ROOMS.items():
            for r, name, sz in things:
                if r == role and (H.PLAYS.get(sz, sz) == plays or not exact):
                    for target, b in rooms_of.BUILDINGS.items():
                        for way, w in b["routes"].items():
                            if key in w["rooms"]:
                                return target, way, key, name
    raise KeyError((role, size))


def _sizes(role: str) -> list[str]:
    """The sizes a role's things really come in, as they play (the rooms' and the spares'): "s" (a room's, which plays
    as the middle size, as a spare does), "big" (the vault door's) and "loot" (the Muscle's)."""
    seen = {H.PLAYS.get(sz, sz) for _, things in rooms_of.ROOMS.values() for r, _, sz in things if r == role}
    seen |= {H.PLAYS.get(sz, sz) for _, sz in rooms_of.SPARE[role]}
    return [s for s, plays in (("s", "m"), ("big", "big"), ("loot", "loot")) if plays in seen]


def _catalogue() -> dict[str, dict]:
    """Every game a tester can pick, by key: each role's quiet games at each size it comes in, each role's loud task
    in a room (and at the vault door, for the roles with a thing there), the three loose cash and the vault's GRAB."""
    out: dict[str, dict] = {}
    for role in H.ROLES:
        for game in heist_games.keys(role):
            g = heist_games.get(role, game)
            title = g.title if g else heist_games.FIRST_TITLES[game]
            for size in _sizes(role):
                out[f"{role}:{game}:{size}"] = {"kind": "quiet", "role": role, "game": game, "size": size, "group": _name(role),
                                                "label": f"{_name(role)}: {title} ({SIZE_NAMES[size]})"}
    for role in H.ROLES:
        for size in ("s", "big") if "big" in _sizes(role) else ("s",):       # only the vault door's roles get a big one
            out[f"loud:{role}:{size}"] = {"kind": "loud", "role": role, "game": "loud", "size": size, "group": "Loud",
                                          "label": f"{_name(role)}: {LOUD[role].title} (loud"
                                                   + (", vault door)" if size == "big" else ")")}
    for kind, g in LOOSE.items():
        out[f"loose:{kind}"] = {"kind": "loose", "role": "muscle", "game": f"loose-{kind}", "size": "s", "group": "Loose cash",
                                "label": f"Loose cash: {g.title}"}
    out["grab"] = {"kind": "grab", "role": "muscle", "game": None, "size": None, "group": "The vault",
                   "label": "The vault: GRAB"}
    return out


GAMES = _catalogue()


# ---- the ticks ----------------------------------------------------------------------------------

def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        import json
        with open(_FILE) as f:
            _played.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception:
        log.error("couldn't read %s", _FILE, exc_info=True)


def played(uid) -> dict[str, dict]:
    _load()
    return dict(_played.get(str(uid), {}))


def _tick(uid, key: str, score: float) -> None:
    _load()
    mine = _played.setdefault(str(uid), {})
    was = mine.get(key, {})
    score = round(max(0.0, float(score)), 3)
    mine[key] = {"best": max(score, was.get("best", 0.0)), "last": score, "n": was.get("n", 0) + 1}
    try:
        atomic_write_json(_FILE, _played)
    except Exception:
        log.error("couldn't save %s", _FILE, exc_info=True)


def scored(room: dict) -> None:
    """heist._end_job's hook: a test job's game is done, so tick it with its score (not the vault's GRAB: that's
    ticked when the vault's over)."""
    key = room.get("test")
    if key in GAMES and GAMES[key]["kind"] != "grab":
        _tick(room["host"], key, room.get("score", 0.0))


def finished(room: dict) -> None:
    """heist._finish's hook: a GRAB test's vault is over, ticked with the take as the vault showed it."""
    if room.get("test") == "grab":
        r = (room.get("results") or {}).get(str(room["host"]), {})
        _tick(room["host"], "grab", r.get("shown", 0.0))


# ---- the command ---------------------------------------------------------------------------------

def find(text: str) -> str | None:
    """The game a tester means: its key, its label, or the only label with that in it."""
    t = (text or "").strip().lower()
    if t in GAMES:
        return t
    hits = [k for k, e in GAMES.items() if t and t in e["label"].lower()]
    exact = [k for k in hits if GAMES[k]["label"].lower() == t]
    return (exact or hits)[0] if len(exact) == 1 or len(hits) == 1 else None


def _score(key: str, best: float) -> str:
    """A best score for the list: a game's out of 100%, the GRAB's as the take the vault showed."""
    return f"{best:.2f}x" if key == "grab" else f"{round(best * 100)}%"


def choices(uid, typed: str = "") -> list[tuple[str, str]]:
    """The autocomplete list (25 at most): "all" for the checklist, then the games matching what's typed, the ones
    not played yet first, each ticked if it has been."""
    if not H.can_practise(uid):
        return []
    mine = played(uid)
    t = (typed or "").strip().lower()
    rows = [(k, e) for k, e in GAMES.items() if not t or t in e["label"].lower() or t in k]
    rows.sort(key=lambda x: x[0] in mine)
    out = [(f"All: your checklist ({len(mine)}/{len(GAMES)} played)", ALL)] if not t or t in ALL else []
    for k, e in rows:
        done = mine.get(k)
        out.append(((f"✓ {e['label']} ({_score(k, done['best'])})" if done else e["label"])[:100], k))
    return out[:25]


def checklist(uid) -> tuple[str, str]:
    """(title, body) of a tester's checklist: every game, ticked with its best score once played."""
    mine = played(uid)
    lines, last = [], None
    for k, e in GAMES.items():
        group = e["group"]
        if group != last:
            lines.append(f"\n**{group}**")
            last = group
        done = mine.get(k)
        what = e["label"] if e["kind"] == "loud" else e["label"].split(": ", 1)[1]
        lines.append(f"✓ {what}: best {_score(k, done['best'])}" if done else f"☐ {what}")
    return f"Bank Job test: {len(mine)} of {len(GAMES)} played", "\n".join(lines).strip()


def open_test(uid: int, key: str, now: float | None = None) -> dict:
    """A one-thing practice job for a tester, straight into the game: play money, nothing moves, never posted. A
    test or practice job they're still in is called off first; a real one has to be left first."""
    H._load()
    if not H.can_practise(uid):
        raise H.Refuse("This is for the Bank Job testers.")
    found = find(key)
    if found is None:
        raise H.Refuse("Pick a game from the list.")
    e = GAMES[found]
    now = time.time() if now is None else now
    seated = H._seated(uid)
    if seated is not None:
        if not seated.get("practice"):
            raise H.Refuse("You're in a real crew. Finish or leave it first.")
        seated.update(over=True, how="closed", ended=now)       # play money: nothing to give back
    rng = H._rng()
    role = e["role"]
    # the thing's size: a quiet game's own; a loud task's in a room (the Muscle's is loot, as in a real job) or at the
    # vault door; loose cash's quick
    size = e["size"] if e["kind"] != "loud" or e["size"] == "big" else "loot" if role == "muscle" else "s"
    target, way, rkey, name = _where(role, size or "loot")
    if e["kind"] == "loose":
        name = LOOSE_NAMES[found[6:]]
    loud = e["kind"] in ("loud", "loose")
    r = {"id": secrets.token_hex(4), "host": uid, "players": [uid], "left": [], "stake": 0, "target": target,
         "state": "job", "created": now, "expires": now, "started": now, "roles": {str(uid): role}, "votes": {},
         "styles": {}, "over": False, "ch": None, "practice": True, "test": found, "bots": {}, "twist": None,
         "approach": way, "style": "loud" if loud else "quiet", "cap": H.cap_for(target, now), "story": H._story(rng)}
    things = []
    if e["kind"] != "grab":
        things.append({"id": "0-0", "role": role, "name": name, "size": size, "spare": False,
                       "mode": "loose" if e["kind"] == "loose" else r["style"], "w": 1.0, "state": "waiting",
                       "who": None, "p": H._make(role, size, rng, r["story"], e["game"]), "terry": 0.0})
    r.update(rooms=[{"key": rkey, "things": things}], alarm=0.0, loot=0.0)
    H._rooms[r["id"]] = r
    if e["kind"] == "grab":
        H._end_job(r, now)                      # straight to the report, then the vault
    elif loud:
        H._start_breach(r, now)
    else:
        H._enter(r, 0, now)
    H._save()
    return r
