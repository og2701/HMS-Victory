"""Countdown: the letters game, for up to six players in the activity.

A room is opened by its host, for a stake or free, and anyone can take one of its six seats; the
host starts it once there are two of them. Each of the three rounds, one player (taking turns)
calls vowel or consonant nine times, the letters coming off shuffled piles weighted like the
show's; then everyone has thirty seconds to make the longest word they can. Only the longest
valid word scores, its length (a full nine scores eighteen), and anyone who matches it scores
too. After the clock HMS Victory's Dictionary Corner shows the best that could be made.

Stakes go in when the host starts; the top scorer takes the pot less the PvP rake (a tie splits
it), and a game where nobody scores, or everybody ties, gives every stake back. Like Broadside,
every read moves a room on to the present and a sweeper does the same for rooms nobody's
watching, so a game always finishes; it's all saved after every change.
"""

import asyncio
import copy
import logging
import os
import random
import secrets
import time
from pathlib import Path

import config
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

GAME = "countdown"
ROUNDS = 3
SEATS = 6
LETTERS = 9
MIN_VOWELS, MIN_CONSONANTS = 3, 4
LETTER_SECONDS = 7          # the picker's clock on each call
CLOCK_SECONDS = 30
REVEAL_SECONDS = 9
LOBBY_SECONDS = 900         # a room nobody starts closes after this
KEEP_FINISHED = 1800
STAKES = [0, 50, 100, 250, 500, 1000]
# The show's piles, roughly: how many of each letter go in before they're shuffled.
VOWELS = {"a": 15, "e": 21, "i": 13, "o": 13, "u": 5}
CONSONANTS = {"b": 2, "c": 3, "d": 6, "f": 2, "g": 3, "h": 2, "j": 1, "k": 1, "l": 5, "m": 4, "n": 8, "p": 4,
              "q": 1, "r": 9, "s": 9, "t": 9, "v": 1, "w": 1, "x": 1, "y": 1, "z": 1}
WORDS_FILE = Path(__file__).resolve().parents[2] / "data" / "words" / "countdown.txt"
COMMON_FILE = WORDS_FILE.with_name("countdown-common.txt")     # everyday words, shown first in the corner

_FILE = os.path.join(config.JSON_DATA_DIR, "activity_countdown.json")
_rooms: dict[str, dict] = {}
_posts: dict[str, int] = {}
_loaded = False
_words: set[str] | None = None
_by_length: dict[int, list[str]] = {}
_common: set[str] = set()
CLIENT = None
listeners: list = []


class Refuse(Exception):
    """Something the rules don't allow; the message is for the player."""


def max_stake() -> int:
    return int(getattr(config, "COUNTDOWN_MAX_STAKE", 5000))


# ---- the dictionary --------------------------------------------------------------------------

def words() -> set[str]:
    global _words
    if _words is None:
        with open(WORDS_FILE) as f:
            _words = {w.strip() for w in f if w.strip()}
        try:
            with open(COMMON_FILE) as f:
                _common.update(w.strip() for w in f if w.strip())
        except FileNotFoundError:
            pass
        _by_length.clear()
        for w in _words:
            _by_length.setdefault(len(w), []).append(w)
        for ws in _by_length.values():
            ws.sort(key=lambda w: (w not in _common, w))
    return _words


def fits(word: str, letters: list[str]) -> bool:
    """Whether a word can be made from these letters, each used once."""
    pool = list(letters)
    for ch in word:
        if ch not in pool:
            return False
        pool.remove(ch)
    return True


def best_words(letters: list[str], limit: int = 3) -> list[str]:
    """The longest words on the board, for Dictionary Corner: everyday ones first."""
    words()
    for n in range(len(letters), 0, -1):
        found = [w for w in _by_length.get(n, []) if fits(w, letters)]
        if found:
            return found[:limit]
    return []


def points(word: str) -> int:
    return 18 if len(word) == LETTERS else len(word)


# ---- keeping it ------------------------------------------------------------------------------

def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        import json
        with open(_FILE) as f:
            raw = json.load(f)
        _rooms.update(raw.get("rooms", {}))
        _posts.update(raw.get("posts", {}))
    except FileNotFoundError:
        pass
    except Exception:
        log.error("couldn't read %s; starting with no countdown rooms", _FILE, exc_info=True)


def _save() -> None:
    try:
        atomic_write_json(_FILE, {"rooms": _rooms, "posts": _posts})
    except Exception:
        log.error("couldn't save the countdown rooms to %s", _FILE, exc_info=True)


def _emit(event: str, room: dict) -> None:
    for fn in listeners:
        try:
            fn(event, copy.deepcopy(room))
        except Exception:
            log.warning("a countdown listener failed on %s", event, exc_info=True)


def post_for(rid: str) -> int | None:
    _load()
    return _posts.get(rid)


def remember_post(rid: str, message_id: int | None) -> None:
    if message_id is not None:
        _posts[rid] = int(message_id)
        _save()


def room_info(rid: str) -> dict | None:
    _load()
    r = _rooms.get(rid)
    return copy.deepcopy(r) if r else None


# ---- the game --------------------------------------------------------------------------------

def _present(room: dict) -> list[int]:
    return [u for u in room["players"] if u not in room["left"]]


def _seated(uid: int) -> dict | None:
    """The room this person is in (and hasn't left), if any."""
    return next((r for r in _rooms.values() if not r.get("over") and uid in _present(r)), None)


def _piles() -> dict:
    v = [ch for ch, n in VOWELS.items() for _ in range(n)]
    c = [ch for ch, n in CONSONANTS.items() for _ in range(n)]
    random.shuffle(v)
    random.shuffle(c)
    return {"v": v, "c": c}


def _counts(letters: list[str]) -> tuple[int, int]:
    v = sum(1 for ch in letters if ch in VOWELS)
    return v, len(letters) - v


def _allowed(letters: list[str]) -> dict:
    """Which calls are still allowed: room must be left for the minimum of each."""
    v, c = _counts(letters)
    left = LETTERS - len(letters)
    return {"vowel": left > 0 and (MIN_CONSONANTS - c) < left and v < LETTERS - MIN_CONSONANTS,
            "consonant": left > 0 and (MIN_VOWELS - v) < left and c < LETTERS - MIN_VOWELS}


def _start_round(room: dict, now: float) -> None:
    present = _present(room)
    n = len(room["rounds"])
    room["rounds"].append({"picker": present[n % len(present)], "letters": [], "piles": _piles(),
                           "phase": "pick", "deadline": now + LETTER_SECONDS, "words": {}, "until": 0.0})


def _call(rd: dict, kind: str, now: float) -> None:
    pile = rd["piles"]["v" if kind == "vowel" else "c"]
    rd["letters"].append(pile.pop())
    if len(rd["letters"]) == LETTERS:
        rd["phase"] = "clock"
        rd["deadline"] = now + CLOCK_SECONDS
    else:
        rd["deadline"] = now + LETTER_SECONDS


def _resolve(room: dict, rd: dict, now: float) -> None:
    """Time's up: check every word, score the longest, ask Dictionary Corner."""
    dictionary = words()
    results = {}
    for uid in room["players"]:
        w = rd["words"].get(str(uid), "")
        ok = bool(w) and fits(w, rd["letters"]) and w in dictionary
        results[str(uid)] = {"word": w, "valid": ok, "points": 0}
    best = max((len(r["word"]) for r in results.values() if r["valid"]), default=0)
    for uid, r in results.items():
        if r["valid"] and len(r["word"]) == best:
            r["points"] = points(r["word"])
            room["scores"][uid] = room["scores"].get(uid, 0) + r["points"]
    rd.update(phase="reveal", results=results, best=best, corner=best_words(rd["letters"]), until=now + REVEAL_SECONDS)
    rd.pop("piles", None)


def _advance(room: dict, now: float) -> bool:
    changed = False
    if room.get("over"):
        return False
    if room["state"] == "lobby":
        if now >= room["expires"]:
            _close(room, "lapsed", now)
            return True
        return False
    while not room.get("over"):
        if len(_present(room)) < 2:
            _finish(room, now)
            return True
        rd = room["rounds"][-1]
        if rd["phase"] == "pick":
            if rd["picker"] in room["left"]:
                rd["deadline"] = min(rd["deadline"], now)
            if now < rd["deadline"]:
                break
            can = [k for k, ok in _allowed(rd["letters"]).items() if ok]
            _call(rd, random.choice(can), rd["deadline"])
            changed = True
        elif rd["phase"] == "clock":
            everyone = all(str(u) in rd["words"] for u in _present(room))
            if now < rd["deadline"] and not everyone:
                break
            _resolve(room, rd, min(now, rd["deadline"]))
            _emit("round", room)
            changed = True
        else:
            if now < rd["until"]:
                break
            if len(room["rounds"]) >= ROUNDS:
                _finish(room, rd["until"])
            else:
                _start_round(room, rd["until"])
            changed = True
    return changed


def _close(room: dict, how: str, now: float) -> None:
    """A room that never started: nobody staked, so nothing to give back."""
    room.update(over=True, how=how, ended=now)
    _emit(how, room)


def _finish(room: dict, now: float) -> None:
    """Settle the game. Saved as paid before any money moves, so it can't pay twice."""
    if room.get("over"):
        return
    present = [u for u in room["players"] if u not in room["left"]]
    scores = {u: room["scores"].get(str(u), 0) for u in present}
    top = max(scores.values(), default=0)
    winners = [u for u, s in scores.items() if s == top] if top > 0 else []
    everyone_tied = len(winners) == len(room["players"])
    room.update(over=True, ended=now, winners=winners, how="score" if winners and not everyone_tied else "refund")
    stake = room["stake"]
    pot = stake * len(room["players"])
    room["pot"] = pot
    room["paid"] = True
    _save()
    try:
        from commands.economy.casino_base import credit_from_bank
        if stake > 0:
            if room["how"] == "refund":
                for u in room["players"]:
                    credit_from_bank(u, stake, "Countdown refund")
                room["share"] = stake
            else:
                from lib.economy.economy_manager import record_game_transfer
                won = prize(pot)
                share = won // len(winners)
                for i, w in enumerate(winners):
                    credit_from_bank(w, share + (won - share * len(winners) if i == 0 else 0), "Countdown win")
                room["share"] = share
                losers = [u for u in room["players"] if u not in winners]
                for l in losers:
                    for w in winners:
                        record_game_transfer(l, w, stake // len(winners))
    except Exception:
        log.error("couldn't settle countdown room %s", room["id"], exc_info=True)
    try:
        from lib.economy import pvp_stats
        for w in winners:
            for l in [u for u in room["players"] if u not in winners]:
                pvp_stats.record_result(GAME, w, l, stake, "win")
    except Exception:
        log.warning("couldn't log countdown room %s", room["id"], exc_info=True)
    _emit("over", room)


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
            log.error("countdown sweep failed", exc_info=True)
        await asyncio.sleep(every)


# ---- what the page sees ----------------------------------------------------------------------

def prize(pot: int) -> int:
    """What the winner takes from a pot (shared if it's a tie)."""
    from lib.economy.economy_manager import pvp_rake
    return pot - pvp_rake(pot) if pot else 0


def _person(uid: int, name_of) -> dict:
    return {"uid": str(uid), "name": name_of(uid) or "Someone"}


def view(room: dict, uid: int, name_of, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    rd = room["rounds"][-1] if room["rounds"] else None
    phase = "over" if room.get("over") else room["state"] if room["state"] == "lobby" else rd["phase"]
    pot = room["stake"] * len(room["players"])
    out = {
        "id": room["id"], "host": str(room["host"]), "stake": room["stake"], "pot": pot, "prize": prize(pot),
        "seats": SEATS, "rounds": ROUNDS, "phase": phase, "round": len(room["rounds"]),
        "how": room.get("how"),
        "players": [{**_person(u, name_of), "score": room["scores"].get(str(u), 0), "left": u in room["left"],
                     "declared": bool(rd and rd["phase"] == "clock" and str(u) in rd["words"])} for u in room["players"]],
        "expiresIn": max(0, round(room["expires"] - now)) if room["state"] == "lobby" else None,
    }
    if rd:
        v, c = _counts(rd["letters"])
        out.update(picker=str(rd["picker"]), letters=rd["letters"], vowels=v, consonants=c,
                   can=_allowed(rd["letters"]) if rd["phase"] == "pick" else {"vowel": False, "consonant": False},
                   deadlineIn=round(max(0.0, rd["deadline"] - now), 2), revealIn=round(max(0.0, rd["until"] - now), 2),
                   word=rd["words"].get(str(uid), ""))
        if rd["phase"] == "reveal" or (room.get("over") and "results" in rd):
            out["results"] = [{**_person(int(u), name_of), **r} for u, r in rd["results"].items()]
            out["results"].sort(key=lambda r: (-r["points"], -len(r["word"]) if r["valid"] else 1, r["name"]))
            out["corner"] = rd.get("corner", [])
    if room.get("over"):
        out["winners"] = [str(u) for u in room.get("winners", [])]
        out["share"] = room.get("share", 0)
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
        "you": _person(uid, name_of), "balance": balance, "stakes": STAKES, "maxStake": max_stake(),
        "room": view(mine, uid, name_of, now) if mine else None,
        "open": [{"id": r["id"], "host": _person(r["host"], name_of), "players": len(_present(r)), "seats": SEATS,
                  "stake": r["stake"], "expiresIn": max(0, round(r["expires"] - now))}
                 for r in _rooms.values() if r["state"] == "lobby" and not r.get("over") and uid not in r["players"]],
    }


def room(uid: int, rid: str, name_of) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None:
        raise Refuse("That room has closed.")
    if _advance(r, time.time()):
        _save()
    if uid not in r["players"]:
        raise Refuse("You aren't in that room.")
    return view(r, uid, name_of)


# ---- what players do -------------------------------------------------------------------------

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
        raise Refuse("You're already in a Countdown room.")


def open_room(uid: int, stake) -> dict:
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
    r = {"id": secrets.token_hex(4), "host": uid, "players": [uid], "left": [], "stake": stake, "state": "lobby",
         "created": now, "expires": now + LOBBY_SECONDS, "rounds": [], "scores": {}, "over": False}
    _rooms[r["id"]] = r
    _save()
    _emit("open", r)
    return r


def join(uid: int, rid: str) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None or r.get("over") or r["state"] != "lobby":
        raise Refuse("That room has already started or closed.")
    if uid in r["players"]:
        return r
    if len(r["players"]) >= SEATS:
        raise Refuse("That room is full.")
    _busy(uid)
    _check_stake(uid, r["stake"])
    r["players"].append(uid)
    _save()
    _emit("seats", r)
    return r


def leave(uid: int, rid: str) -> dict:
    """Leave a room. Before it starts you just get up; the host leaving closes it. Once it's
    started your stake stays in the pot."""
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"] or r.get("over"):
        raise Refuse("You aren't in that room.")
    now = time.time()
    if r["state"] == "lobby":
        if uid == r["host"]:
            _close(r, "closed", now)
        else:
            r["players"].remove(uid)
            _emit("seats", r)
    elif uid not in r["left"]:
        r["left"].append(uid)
        _advance(r, now)
    _save()
    return r


def start(uid: int, rid: str) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None or r.get("over") or r["state"] != "lobby":
        raise Refuse("That room has already started or closed.")
    if uid != r["host"]:
        raise Refuse("Only the host can start the game.")
    if len(r["players"]) < 2:
        raise Refuse("Wait for at least one more player.")
    stake = r["stake"]
    if stake > 0:
        for u in r["players"]:
            _check_stake(u, stake, "You" if u == uid else "Someone in the room")
        from commands.economy.casino_base import credit_from_bank
        from lib.economy.economy_manager import remove_bb
        taken = []
        for u in r["players"]:
            if not remove_bb(u, stake, reason="Countdown stake"):
                for t in taken:
                    credit_from_bank(t, stake, "Countdown stake refund")
                raise Refuse("Someone in the room can't cover the stake any more.")
            taken.append(u)
    now = time.time()
    r.update(state="playing", started=now)
    _start_round(r, now)
    _save()
    _emit("start", r)
    return r


def call(uid: int, rid: str, kind: str) -> dict:
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"]:
        raise Refuse("You aren't in that room.")
    now = time.time()
    _advance(r, now)
    rd = r["rounds"][-1] if r["rounds"] else None
    if r.get("over") or not rd or rd["phase"] != "pick":
        raise Refuse("It isn't time to pick letters.")
    if rd["picker"] != uid:
        raise Refuse("It's someone else's turn to pick.")
    if kind not in ("vowel", "consonant") or not _allowed(rd["letters"])[kind]:
        raise Refuse("You need more of the other kind." if kind in ("vowel", "consonant") else "Vowel or consonant?")
    _call(rd, kind, now)
    _save()
    return r


def declare(uid: int, rid: str, word: str) -> dict:
    """Set (or change, or with "" clear) your word while the clock runs. Only the letters are
    checked now; whether it's a word is Dictionary Corner's job at the end."""
    _load()
    r = _rooms.get(rid)
    if r is None or uid not in r["players"] or uid in r["left"]:
        raise Refuse("You aren't in that room.")
    now = time.time()
    _advance(r, now)
    rd = r["rounds"][-1] if r["rounds"] else None
    if r.get("over") or not rd or rd["phase"] != "clock":
        raise Refuse("The clock isn't running.")
    word = (word or "").strip().lower()
    if not word:
        rd["words"].pop(str(uid), None)
    else:
        if not word.isalpha() or not word.isascii() or not fits(word, rd["letters"]):
            raise Refuse("Use only the letters on the board, each once.")
        rd["words"][str(uid)] = word
    _advance(r, now)
    _save()
    return r
