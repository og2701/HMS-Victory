"""How Davy Jones' Locker (the activity's penny falls machine) is paying, from the cups in
pennyfalls_cups. Read-only.

    ./venv/bin/python scripts/pennyfalls_report.py [days]

Coins only leave a machine over the front edge (to the player) or down the side gaps (to the
house), and gold coins add value from outside, so in the long run a machine pays back
100% + what gold adds - what the sides take. "Return" below is coins back per coin dropped,
counting a gold coin as 10, and adjusted for coins left on (or taken from) the boards over the
period. The question this answers: does any way of aiming beat the house? If players who drop
mostly down the middle come out above 100%, gold coins need to be rarer
(PENNYFALLS_GOLD_EVERY in config.py).
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "report")

from database import DatabaseManager  # noqa: E402

GOLD = 10


def style(row) -> str:
    aimed = row["aim_left"] + row["aim_middle"] + row["aim_right"] + row["aim_tap"]
    if not aimed:
        return "unknown"
    if row["aim_middle"] / aimed >= 0.7:
        return "mostly middle"
    if row["aim_tap"] / aimed >= 0.7:
        return "mostly tapping"
    return "mixed lanes"


def summarise(rows) -> str:
    dropped = sum(r["dropped"] for r in rows)
    if not dropped:
        return "no coins dropped"
    back = sum(r["coins_won"] + r["golds_won"] * GOLD for r in rows)
    sides = sum(r["coins_lost"] + r["golds_lost"] * GOLD for r in rows)
    drift = sum(r["board_after"] - r["board_before"] for r in rows)
    staked, payout = sum(r["staked"] for r in rows), sum(r["payout"] for r in rows)
    return (f"{len(rows):>5} cups  {dropped:>7,} coins in  return {100 * (back + drift) / dropped:6.1f}%  "
            f"(raw {100 * back / dropped:5.1f}%)  sides {100 * sides / dropped:4.1f}%  "
            f"gold given {sum(r['golds_given'] for r in rows):>4}  house net {staked - payout:+,} UKP")


def main(days: float) -> None:
    since = int(time.time() - days * 86400)
    cols = ("user_id", "staked", "payout", "dropped", "coins_won", "golds_won", "coins_lost", "golds_lost",
            "golds_given", "board_before", "board_after", "trimmed", "note",
            "aim_left", "aim_middle", "aim_right", "aim_tap")
    rows = [dict(zip(cols, r)) for r in DatabaseManager.fetch_all(
        f"SELECT {', '.join(cols)} FROM pennyfalls_cups WHERE ended >= ?", (since,))]
    print(f"Davy Jones' Locker, last {days:g} days")
    if not rows:
        print("  no cups yet")
        return
    print(f"  all            {summarise(rows)}")
    for name in ("mostly middle", "mixed lanes", "mostly tapping", "unknown"):
        group = [r for r in rows if style(r) == name]
        if group:
            print(f"  {name:<14} {summarise(group)}")
    trimmed = [r for r in rows if r["trimmed"]]
    capped = [r for r in rows if r["note"]]
    print(f"  cups with reports trimmed: {len(trimmed)}  (players: {len({r['user_id'] for r in trimmed})})")
    print(f"  cups that hit a limit: {len(capped)}")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 30)
