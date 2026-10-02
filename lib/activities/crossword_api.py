"""HMS Crossword for the activity: the same daily grid as /crossword, as JSON.

Progress is the same record /crossword keeps, so a grid started in one carries on in the
other and pays once. Only letters the player has earned (solved entries, revealed letters)
are ever sent; the answers stay on the server.
"""

from lib.features import crossword as X


def state(uid: int, date) -> dict:
    puzzle = X._todays_puzzle(date)
    p = X._player(date.isoformat(), uid)
    size = X.grid_size(date)
    black = {tuple(b) for b in puzzle["black"]}
    seen = X._letters(puzzle, p)
    nums = X._numbers(puzzle)
    solved_cells = {tuple(c) for e in puzzle["entries"] if X._key(e) in p["solved"] for c in e["cells"]}
    cells = []
    for r in range(size):
        row = []
        for c in range(size):
            if (r, c) in black:
                row.append(None)
                continue
            status = "solved" if (r, c) in solved_cells else ("revealed" if (r, c) in seen else None)
            row.append({"num": nums.get((r, c)), "letter": seen.get((r, c)), "status": status})
        cells.append(row)
    rules = X.rules(date)
    hints_used, _ = X.penalties(p, date)
    wrong = int(p.get("wrong", 0))
    per = rules["wrong_per_tier"]
    return {
        "game": "crossword",
        "date": date.isoformat(),
        "dateLabel": f"{date:%A %-d %B}",
        "size": size,
        "cells": cells,
        "entries": [{"key": X._key(e), "num": e["num"], "dir": e["dir"], "clue": e["clue"],
                     "length": len(e["answer"]), "cells": [list(c) for c in e["cells"]],
                     "solved": X._key(e) in p["solved"]} for e in puzzle["entries"]],
        "rewards": list(rules["rewards"]),
        "reward": X.reward_for(p, date),
        "hints": {"used": hints_used, "max": rules["max_hints"],
                  "left": max(rules["max_hints"] - hints_used, 0) if rules["max_hints"] else None},
        "wrong": {"count": wrong, "perTier": per, "toDrop": (per - wrong % per) if per else None},
        "done": bool(p["done"]),
        "paid": p.get("paid"),
    }


async def answer(client, uid: int, date, entry_key: str, guess: str):
    """(new state, message, finished_now). message is set for a wrong or refused answer
    (state is None when the answer wasn't even checked) and for crossings filled in free."""
    puzzle = X._todays_puzzle(date)
    status, msg, p = X.submit(uid, date.isoformat(), puzzle, entry_key, guess)
    clean = (msg or "").replace("**", "").replace("✅ ", "") or None
    if status == "invalid":
        return None, clean or "That answer wasn't accepted.", False
    finished_now = status == "ok" and bool(p["done"])
    if status == "ok":
        await X.settle_finish(client, uid, date, p, puzzle)
    return state(uid, date), clean, finished_now


async def hint(client, uid: int, date):
    """(new state, message, finished_now) after revealing one letter."""
    puzzle = X._todays_puzzle(date)
    before = X._player(date.isoformat(), uid)
    if before["done"]:
        return state(uid, date), None, False
    msg, p = X.reveal_letter(uid, date.isoformat(), puzzle, date)
    if msg is None:
        return state(uid, date), "No hints left for today's grid.", False
    finished_now = bool(p["done"])
    await X.settle_finish(client, uid, date, p, puzzle)
    return state(uid, date), msg.replace("**", "").replace("💡 ", ""), finished_now


def finish_message(uid: int, board: dict) -> str:
    """The spoiler-free line posted to the channel when someone finishes the grid."""
    grid = []
    for row in board["cells"]:
        grid.append("".join("⬛" if c is None else ("🟨" if c["status"] == "revealed" else "🟩") for c in row))
    paid = f" · +{board['paid']:,} UKP" if board.get("paid") else ""
    return f"<@{uid}> finished today's **HMS Crossword**{paid}\n" + "\n".join(grid)


def opened(client, uid: int, date) -> None:
    """The same bookkeeping /crossword does when the grid is first shown."""
    X._note_opened(uid, date.isoformat())
    try:
        from lib.core import detection as D
        D.note_daily_command(uid, "crossword", client=client)
    except Exception:
        pass
