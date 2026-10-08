"""Broadside: a live 1v1 duel played in the activity.

Seven naval moves sit round a wheel, and each beats the three after it going clockwise. Both
captains pick in secret, the picks are turned over together, and each move can be fired once a
match. First to three hits, or the most after five rounds; level after five goes to sudden death
with the cards still in hand, and level after all seven is a draw.

A challenge is to one person or open to anyone, for a stake or for free. Stakes are only taken
when the challenge is accepted; a win pays the pot less the PvP rake, a draw or a void refunds.

Nothing here waits on a player: every read moves the match on to the present (a lapsed clock
fires a random card from the hand), and a sweeper does the same for matches nobody's watching,
so a match always finishes and its stakes always settle. Two missed clocks in a row and that
captain has left the match. The whole lot is saved after every change so a restart can't lose
a staked match.
"""

import asyncio
import copy
import logging
import math
import os
import random
import secrets
import time

import config
from lib.core.file_operations import atomic_write_json

log = logging.getLogger(__name__)

# Clockwise round the wheel: each beats the next three.
MOVES = ["evade", "broadside", "fireship", "ram", "board", "grapeshot", "chainshot"]
LABELS = {"evade": "Evade", "broadside": "Broadside", "fireship": "Fireship", "ram": "Ram",
          "board": "Board", "grapeshot": "Grapeshot", "chainshot": "Chain Shot"}
# Why the first beats the second, said on the reveal.
REASONS = {
    ("evade", "broadside"): "Evade slips out of the line of fire.",
    ("evade", "fireship"): "Evade steers clear of the drifting fireship.",
    ("evade", "ram"): "Evade turns away and the ram hits only sea.",
    ("broadside", "fireship"): "Broadside sinks the fireship before it arrives.",
    ("broadside", "ram"): "Broadside holes the rammer below the waterline.",
    ("broadside", "board"): "Broadside cuts down the boarders as they cross.",
    ("fireship", "ram"): "The rammer drives straight into the flames.",
    ("fireship", "board"): "The fireship burns the boarding party.",
    ("fireship", "grapeshot"): "Grapeshot can't stop a ship with no crew.",
    ("ram", "board"): "The ram strikes before the boarders can cross.",
    ("ram", "grapeshot"): "Grapeshot rattles off an oak bow.",
    ("ram", "chainshot"): "Chain Shot cuts the rigging, but the ram is already charging.",
    ("board", "grapeshot"): "Boarders storm the deck while the grapeshot is loaded.",
    ("board", "chainshot"): "Boarders swarm aboard while the chain is reloaded.",
    ("board", "evade"): "Grappling hooks catch the ship before it slips away.",
    ("grapeshot", "chainshot"): "Grapeshot cuts down the crew loading the chain.",
    ("grapeshot", "evade"): "Grapeshot sweeps the helmsman off the wheel.",
    ("grapeshot", "broadside"): "Grapeshot clears the gun crews before they fire.",
    ("chainshot", "evade"): "Chain Shot shreds the sails, so there's no escape.",
    ("chainshot", "broadside"): "Chain Shot brings the masts down before the guns bear.",
    ("chainshot", "fireship"): "Chain Shot drops the fireship's masts and it drifts off.",
}

PICK_SECONDS = 15           # the clock on each round
REVEAL_SECONDS = 4          # the reveal shows this long before the next clock starts
CHALLENGE_SECONDS = 600     # a challenge nobody takes lapses after this
KEEP_FINISHED = 1800        # a finished match stays readable this long, for its result screen
MISSES_TO_LEAVE = 2         # missed clocks in a row that count as leaving the match
REGULATION = 5              # rounds before sudden death
STAKES = [0, 50, 100, 250, 500, 1000]
GAME = "broadside"
# HMS Victory's seat in a practice match: free, never posted, never counted. No member has id 1.
BOT, BOT_NAME = 1, "HMS Victory"

_FILE = os.path.join(config.JSON_DATA_DIR, "activity_duels.json")
_challenges: dict[str, dict] = {}
_matches: dict[str, dict] = {}
_posts: dict[str, list] = {}    # challenge id -> [channel, message] of its post, which follows it into the match
_loaded = False
CLIENT = None               # the bot's client, for its id in the daily-allowance check
# Told about each step (challenge, withdrawn, declined, lapsed, start, round, over) with a copy
# of the challenge or match: how the #casino post keeps up (duel_posts).
listeners: list = []


def _emit(event: str, obj: dict) -> None:
    for fn in listeners:
        try:
            fn(event, copy.deepcopy(obj))
        except Exception:
            log.warning("a broadside listener failed on %s", event, exc_info=True)


def post_for(cid: str) -> tuple[int | None, int] | None:
    """The challenge's post as (channel, message); a channel of None is #casino (posts saved before
    they followed the channel the game was opened in)."""
    _load()
    p = _posts.get(cid)
    if p is None:
        return None
    return (None, int(p)) if isinstance(p, int) else (p[0], int(p[1]))


def remember_post(cid: str, message_id: int | None, channel: int | None = None) -> None:
    if message_id is None:
        return
    _posts[cid] = [int(channel) if channel else None, int(message_id)]
    _save()


def challenge_info(cid: str) -> dict | None:
    _load()
    c = _challenges.get(cid)
    return dict(c) if c else None


class Refuse(Exception):
    """A move the rules don't allow; the message is for the player."""


def beats(a: str, b: str) -> bool:
    return 1 <= (MOVES.index(b) - MOVES.index(a)) % len(MOVES) <= 3


def max_stake() -> int:
    return int(getattr(config, "DUEL_MAX_STAKE", 5000))


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
        _challenges.update(raw.get("challenges", {}))
        _matches.update(raw.get("matches", {}))
        _posts.update(raw.get("posts", {}))
    except FileNotFoundError:
        pass
    except Exception:
        log.error("couldn't read %s; starting with no duels", _FILE, exc_info=True)


def _save() -> None:
    try:
        atomic_write_json(_FILE, {"challenges": _challenges, "matches": _matches, "posts": _posts})
    except Exception:
        log.error("couldn't save the duels to %s", _FILE, exc_info=True)


# ---- the match -------------------------------------------------------------------------------

def _other(side: str) -> str:
    return "b" if side == "a" else "a"


def _side(m: dict, uid: int) -> str | None:
    return "a" if m["a"] == uid else "b" if m["b"] == uid else None


def _hand(m: dict, side: str) -> list[str]:
    used = {r[side] for r in m["rounds"]}
    return [mv for mv in MOVES if mv not in used]


def _wins(m: dict) -> tuple[int, int]:
    a = sum(1 for r in m["rounds"] if r["w"] == "a")
    b = sum(1 for r in m["rounds"] if r["w"] == "b")
    return a, b


def _decided(m: dict) -> str | None:
    """'a', 'b' or 'draw' once the match is settled by the score, else None."""
    n, (a, b) = len(m["rounds"]), _wins(m)
    if n < REGULATION:
        return ("a" if a > b else "b") if abs(a - b) > REGULATION - n else None
    if a != b:
        return "a" if a > b else "b"
    return "draw" if n >= len(MOVES) else None


def _active(uid: int) -> dict | None:
    return next((m for m in _matches.values() if not m["over"] and uid in (m["a"], m["b"])), None)


def _start_round(m: dict, now: float) -> None:
    """Clear the picks for the next round. With one card left there's nothing to choose, so
    it's played for both at once."""
    m["picks"] = {"a": None, "b": None}
    m["auto"] = []
    m["deadline"] = m["next_at"] + PICK_SECONDS
    for side in ("a", "b"):
        hand = _hand(m, side)
        if len(hand) == 1:
            m["picks"][side] = hand[0]
    if all(m["picks"].values()):
        m["deadline"] = m["next_at"]
    if m.get("bot"):
        forced = m["picks"]["b"] is not None
        if not forced:
            m["picks"]["b"] = _bot_pick(m)
        m["bot_at"] = m["next_at"] + (0 if forced else random.uniform(1.5, 4.5))


def _bot_pick(m: dict) -> str:
    """HMS Victory's order: it counts your cards like anyone would, favouring the ones that beat
    more of what you've got left, but it still rolls the dice so it can't be read."""
    mine, theirs = _hand(m, "b"), _hand(m, "a")
    def edge(x: str) -> float:
        return sum(1 if beats(x, y) else -1 if beats(y, x) else 0 for y in theirs) / max(1, len(theirs))
    return random.choices(mine, [math.exp(2.5 * edge(x)) for x in mine])[0]


def _resolve(m: dict, now: float) -> None:
    a, b = m["picks"]["a"], m["picks"]["b"]
    w = None if a == b else "a" if beats(a, b) else "b"
    m["rounds"].append({"a": a, "b": b, "w": w, "auto": list(m["auto"])})
    for side in ("a", "b"):
        m["misses"][side] = m["misses"][side] + 1 if side in m["auto"] else 0
    m["next_at"] = now + REVEAL_SECONDS
    gone = [s for s in ("a", "b") if m["misses"][s] >= MISSES_TO_LEAVE]
    if len(gone) == 2:
        _finish(m, None, "void", now)
    elif gone:
        _finish(m, _other(gone[0]), "left", now)
    elif (result := _decided(m)) is not None:
        _finish(m, None if result == "draw" else result, "score" if result != "draw" else "draw", now)
    else:
        _start_round(m, now)
        _emit("round", m)


def _advance(m: dict, now: float) -> bool:
    """Bring a match up to `now`: fire a random card for anyone whose clock ran out, and
    resolve the round once both cards are down. True if anything changed."""
    changed = False
    while not m["over"]:
        if all(m["picks"].values()) and now >= m["next_at"] and now >= m.get("bot_at", 0):
            # resolved the moment the second card went down, or when the clock ran out
            _resolve(m, min(now, m["deadline"]))
            changed = True
            continue
        if now >= m["deadline"] and now >= m["next_at"]:
            for side in ("a", "b"):
                if m["picks"][side] is None:
                    m["picks"][side] = random.choice(_hand(m, side))
                    m["auto"].append(side)
            changed = True
            continue
        break
    return changed


def _finish(m: dict, winner: str | None, how: str, now: float) -> None:
    """End the match and settle the stakes. Marked paid and saved before any money moves, so
    a crash between the two can't pay out twice."""
    m.update(over=True, winner=winner, how=how, ended=now)
    stake, a, b = m["stake"], m["a"], m["b"]
    if m.get("bot"):                # practice: nothing to pay, nothing to count, but it's kept
        _keep(m, "practice")
        _emit("over", m)
        return
    if m.get("paid"):
        return
    m["paid"] = True
    _save()
    try:
        from commands.economy.casino_base import credit_from_bank, settle_pvp_pot
        if winner is not None:
            wid, lid = (a, b) if winner == "a" else (b, a)
            if stake > 0:
                rake = settle_pvp_pot(wid, lid, stake * 2, "Broadside win", own_stake=stake)
                m["payout"] = stake * 2 - rake
        elif stake > 0:
            credit_from_bank(a, stake, "Broadside draw refund" if how == "draw" else "Broadside void refund")
            credit_from_bank(b, stake, "Broadside draw refund" if how == "draw" else "Broadside void refund")
            m["payout"] = stake
    except Exception:
        log.error("couldn't settle broadside match %s", m["id"], exc_info=True)
    try:
        from lib.economy import pvp_stats
        outcome = {"score": "win", "left": "forfeit", "forfeit": "forfeit"}.get(how, how)
        if winner is None:
            pvp_stats.record_result(GAME, None, None, stake, outcome)
        else:
            pvp_stats.record_result(GAME, a if winner == "a" else b, b if winner == "a" else a, stake, outcome)
    except Exception:
        log.warning("couldn't log broadside match %s", m["id"], exc_info=True)
    _keep(m, "game")
    _emit("over", m)


def _keep(m: dict, mode: str) -> None:
    """The match kept for good (lib/activities/archive.py): every round's moves, who won, what it paid."""
    from lib.activities import archive
    winner, payout = m.get("winner"), m.get("payout", 0)
    def side(s):
        uid = m[s] if not (mode == "practice" and s == "b") else None
        if winner is None:
            return {"user_id": uid, "place": None, "result": m.get("how"), "payout": payout}
        won = winner == s
        return {"user_id": uid, "place": 1 if won else 2, "result": "win" if won else m.get("how") if m.get("how") in ("left", "forfeit") else "lose",
                "payout": payout if won else 0}
    archive.keep(GAME, m["id"], mode, m.get("stake", 0), m.get("how") or "", [side("a"), side("b")], m, m.get("ended"))


def _tidy(now: float) -> bool:
    """Let lapsed challenges go and forget long-finished matches."""
    gone = [cid for cid, c in _challenges.items() if c["expires"] <= now]
    old = [mid for mid, m in _matches.items() if m["over"] and now - m.get("ended", now) > KEEP_FINISHED]
    for cid in gone:
        _emit("lapsed", _challenges.pop(cid))
        _posts.pop(cid, None)
    for mid in old:
        _posts.pop(_matches.pop(mid).get("cid"), None)
    return bool(gone or old)


def sweep(now: float | None = None) -> None:
    """Move every match on to now and tidy up, for the ones nobody is looking at."""
    _load()
    now = time.time() if now is None else now
    changed = _tidy(now)
    for m in list(_matches.values()):
        changed = _advance(m, now) or changed
    if changed:
        _save()


async def run_sweeper(every: float = 2.0) -> None:
    while True:
        try:
            sweep()
        except Exception:
            log.error("broadside sweep failed", exc_info=True)
        await asyncio.sleep(every)


# ---- what the page sees ----------------------------------------------------------------------

def _person(uid: int | None, name_of) -> dict | None:
    if uid is None:
        return None
    if uid == BOT:
        return {"uid": str(uid), "name": BOT_NAME, "bot": True}
    return {"uid": str(uid), "name": name_of(uid) or "Someone"}


def _challenge_view(c: dict, now: float, name_of) -> dict:
    return {"id": c["id"], "from": _person(c["from"], name_of), "to": _person(c.get("to"), name_of),
            "stake": c["stake"], "expiresIn": max(0, round(c["expires"] - now))}


def view(m: dict, uid: int, name_of, now: float | None = None) -> dict:
    """The match from one captain's side: their hand, what the other still holds (public, since
    every card played is shown), the score, the clock, and every round so far. The other
    captain's card for the round in play is never sent until both are down."""
    now = time.time() if now is None else now
    me = _side(m, uid)
    if me is None:
        raise Refuse("That isn't your duel.")
    them = _other(me)
    you_w, them_w = (_wins(m) if me == "a" else _wins(m)[::-1])
    rounds = []
    for r in m["rounds"]:
        result = "draw" if r["w"] is None else "win" if r["w"] == me else "loss"
        pair = (r[me], r[them]) if result == "win" else (r[them], r[me])
        rounds.append({"you": r[me], "them": r[them], "result": result,
                       "reason": REASONS.get(pair, "Both fire the same order, and nothing comes of it."),
                       "auto": {"you": me in r["auto"], "them": them in r["auto"]}})
    phase = "over" if m["over"] else "reveal" if now < m["next_at"] else "pick"
    out = {
        "id": m["id"], "stake": m["stake"], "pot": m["stake"] * 2,
        "you": _person(m[me], name_of), "them": _person(m[them], name_of),
        "score": {"you": you_w, "them": them_w},
        "round": len(m["rounds"]) + (0 if phase == "reveal" or m["over"] else 1),
        "phase": phase,
        "deadlineIn": round(max(0.0, m["deadline"] - now), 2),
        "revealIn": round(max(0.0, m["next_at"] - now), 2),
        "hand": _hand(m, me), "theirHand": _hand(m, them),
        "picked": m["picks"][me] if not m["over"] else None,
        "theyPicked": m["picks"][them] is not None and not m["over"] and (m[them] != BOT or now >= m.get("bot_at", 0)),
        "rounds": rounds,
    }
    if m["over"]:
        result = "draw" if m["winner"] is None else "win" if m["winner"] == me else "loss"
        out["over"] = {"result": result, "how": m["how"],
                       "payout": m.get("payout", 0) if result != "loss" else 0}
    return out


def lobby(uid: int, name_of, balance: int) -> dict:
    _load()
    now = time.time()
    if _tidy(now):
        _save()
    m = _active(uid)
    if m is not None and _advance(m, now):
        _save()
    mine = next((c for c in _challenges.values() if c["from"] == uid), None)
    return {
        "you": _person(uid, name_of), "balance": balance, "stakes": STAKES, "maxStake": max_stake(),
        "match": view(m, uid, name_of, now) if m is not None else None,
        "mine": _challenge_view(mine, now, name_of) if mine else None,
        "forYou": [_challenge_view(c, now, name_of) for c in _challenges.values() if c.get("to") == uid],
        "open": [_challenge_view(c, now, name_of) for c in _challenges.values()
                 if c.get("to") is None and c["from"] != uid],
    }


def match(uid: int, mid: str, name_of) -> dict:
    _load()
    m = _matches.get(mid)
    if m is None:
        raise Refuse("That duel has finished.")
    if _advance(m, time.time()):
        _save()
    return view(m, uid, name_of)


# ---- what the captains do --------------------------------------------------------------------

def _check_stake(uid: int, stake: int, name: str = "You") -> None:
    from lib.economy.economy_manager import get_bb, wager_blocked_reason
    if stake <= 0:
        return
    if get_bb(uid) < stake:
        raise Refuse(f"{name} can't cover {stake:,} UKPence." if name != "You" else f"You don't have {stake:,} UKPence.")
    bot_id = getattr(getattr(CLIENT, "user", None), "id", None)
    if why := wager_blocked_reason(uid, stake, bot_id, name=name):
        raise Refuse(why.replace("**", ""))


def challenge(uid: int, stake, to: int | None = None, channel: int | None = None) -> dict:
    """Post a challenge, to one person or to anyone. A new one replaces your old one. ``channel``
    is where the game was opened, where its post goes."""
    _load()
    try:
        stake = int(stake)
    except (TypeError, ValueError):
        raise Refuse("Pick a stake.")
    if stake < 0 or stake > max_stake():
        raise Refuse(f"Stakes go up to {max_stake():,} UKPence.")
    if to is not None and int(to) == uid:
        raise Refuse("You can't duel yourself.")
    if _active(uid) is not None:
        raise Refuse("Finish your duel first.")
    _check_stake(uid, stake)
    now = time.time()
    for old in [old for old, c in _challenges.items() if c["from"] == uid]:
        _emit("withdrawn", _challenges.pop(old))
        _posts.pop(old, None)
    c = {"id": secrets.token_hex(4), "from": uid, "to": int(to) if to is not None else None,
         "stake": stake, "created": now, "expires": now + CHALLENGE_SECONDS, "ch": int(channel) if channel else None}
    _challenges[c["id"]] = c
    _save()
    _emit("challenge", c)
    return c


def cancel(uid: int, cid: str) -> None:
    _load()
    c = _challenges.get(cid)
    if c is None or uid not in (c["from"], c.get("to")):
        raise Refuse("That challenge has gone.")
    del _challenges[cid]
    _save()
    _emit("withdrawn" if uid == c["from"] else "declined", c)
    _posts.pop(cid, None)


def accept(uid: int, cid: str) -> dict:
    """Take a challenge: both stakes go in and the first clock starts."""
    _load()
    now = time.time()
    _tidy(now)
    c = _challenges.get(cid)
    if c is None:
        raise Refuse("That challenge has gone.")
    if c["from"] == uid:
        raise Refuse("You can't accept your own challenge.")
    if c.get("to") is not None and c["to"] != uid:
        raise Refuse("That challenge is for someone else.")
    if _active(uid) is not None:
        raise Refuse("Finish your duel first.")
    if _active(c["from"]) is not None:
        raise Refuse("They're already in a duel.")
    stake = c["stake"]
    _check_stake(c["from"], stake, name="They")
    _check_stake(uid, stake)
    if stake > 0:
        from commands.economy.casino_base import credit_from_bank
        from lib.economy.economy_manager import remove_bb
        if not remove_bb(c["from"], stake, reason="Broadside stake"):
            raise Refuse("They can't cover the stake any more.")
        if not remove_bb(uid, stake, reason="Broadside stake"):
            credit_from_bank(c["from"], stake, "Broadside stake refund")
            raise Refuse("You can't cover the stake.")
    del _challenges[c["id"]]
    for other in [k for k, v in _challenges.items() if v["from"] in (uid, c["from"])]:
        _emit("withdrawn", _challenges.pop(other))
        _posts.pop(other, None)
    m = {"id": secrets.token_hex(4), "cid": c["id"], "ch": c.get("ch"), "a": c["from"], "b": uid, "stake": stake, "started": now,
         "rounds": [], "misses": {"a": 0, "b": 0}, "next_at": now, "over": False, "winner": None}
    _start_round(m, now)
    _matches[m["id"]] = m
    _save()
    _emit("start", m)
    return m


def vs_bot(uid: int) -> dict:
    """A practice match against HMS Victory: free, and it starts at once."""
    _load()
    if _active(uid) is not None:
        raise Refuse("Finish your duel first.")
    now = time.time()
    m = {"id": secrets.token_hex(4), "a": uid, "b": BOT, "bot": True, "stake": 0, "started": now,
         "rounds": [], "misses": {"a": 0, "b": 0}, "next_at": now, "over": False, "winner": None}
    _start_round(m, now)
    _matches[m["id"]] = m
    _save()
    return m


def pick(uid: int, mid: str, move: str) -> dict:
    _load()
    m = _matches.get(mid)
    if m is None or _side(m, uid) is None:
        raise Refuse("That isn't your duel.")
    now = time.time()
    _advance(m, now)
    if m["over"]:
        raise Refuse("The duel is over.")
    if now < m["next_at"]:
        raise Refuse("Wait for the next round.")
    side = _side(m, uid)
    if m["picks"][side] is not None:
        raise Refuse("Your order's already given.")
    if move not in _hand(m, side):
        raise Refuse("That card's already been fired." if move in MOVES else "That isn't an order.")
    m["picks"][side] = move
    _advance(m, now)
    _save()
    return m


def forfeit(uid: int, mid: str) -> dict:
    _load()
    m = _matches.get(mid)
    if m is None or _side(m, uid) is None:
        raise Refuse("That isn't your duel.")
    if not m["over"]:
        _finish(m, _other(_side(m, uid)), "forfeit", time.time())
        _save()
    return m


def people(uid: int, query: str, members) -> list[dict]:
    """Members to challenge whose names start with (or contain) what's typed."""
    q = (query or "").strip().lower()
    out = []
    for mem in members:
        if getattr(mem, "bot", False) or int(mem.id) == uid:
            continue
        name = getattr(mem, "display_name", "") or ""
        if q and q not in name.lower():
            continue
        out.append((not name.lower().startswith(q), name.lower(), {"uid": str(mem.id), "name": name}))
    out.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in out[:8]]
