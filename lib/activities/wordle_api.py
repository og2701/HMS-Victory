"""HMS Wordle for the activity: the same daily game as /wordle, as JSON.

Progress is the same record /wordle keeps, so a game started in one carries on in the
other and is paid once. The answer is only ever sent once the game is over.
"""

import config
from lib.features import wordle as W


def state(uid: int, date) -> dict:
    p = W._player(date.isoformat(), uid)
    word = W._todays_word(date)
    rows = [{"word": g.upper(), "score": W._score(g, word)} for g in p["guesses"]]
    keys = {}
    for g in p["guesses"]:
        for s, ch in zip(W._score(g, word), g.upper()):
            if ch not in keys or W._RANK[s] > W._RANK[keys[ch]]:
                keys[ch] = s
    n = len(p["guesses"])
    return {
        "game": "wordle",
        "date": date.isoformat(),
        "number": (date - W._EPOCH).days + 1,
        "dateLabel": f"{date:%A %-d %B}",
        "rows": rows,
        "keys": keys,
        "rewards": list(config.WORDLE_REWARDS),
        "next": None if p["done"] else config.WORDLE_REWARDS[min(n, 5)],
        "solved": bool(p["solved"]),
        "done": bool(p["done"]),
        "paid": p.get("paid", config.WORDLE_REWARDS[n - 1] if p["solved"] and p.get("rewarded") else None),
        "answer": word.upper() if p["done"] else None,
    }


async def guess(client, uid: int, date, word: str) -> tuple[dict | None, str | None]:
    """(new state, None) after a guess, or (None, reason) when it wasn't accepted."""
    status, err, p = W._submit_guess(uid, date.isoformat(), W._todays_word(date), word)
    if status == "invalid":
        return None, (err or "That guess wasn't accepted.").replace("**", "")
    if status == "ok":
        await W.settle_solve(client, uid, date, p)
    return state(uid, date), None


def opened(client, uid: int, date) -> None:
    """The same bookkeeping /wordle does when the board is first shown."""
    W._note_opened(uid, date.isoformat())
    try:
        from lib.core import detection as D
        D.note_daily_command(uid, "wordle", client=client)
    except Exception:
        pass
