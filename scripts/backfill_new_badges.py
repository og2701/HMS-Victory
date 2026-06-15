"""Retroactively award the new economy badges from existing data.

Run on the server AFTER deploying the new code (so the badge rows exist):

    python -m scripts.backfill_new_badges

Backfills only what's reconstructable from stored data:
  - on_the_dole   : anyone in the benefits claims file
  - green_fingers : anyone in the tree-watering file (+ sir_branchalot if total >= 100)
  - saver/long_game/paper_hands/bond_villain : from the bonds table
  - red_letter_day/zero_hero : from casino_results roulette rows
Badges needing per-event detail we never stored (drip, lucky_number, career streak,
rock_bottom, benefits_cheat) can't be backfilled and just accrue going forward.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import DatabaseManager, award_badge, init_db
import config


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def main():
    init_db()  # make sure the new badge rows exist before we reference them
    counts = {}

    def give(uid, badge):
        if uid and not str(uid).startswith("_") and award_badge(str(uid), badge):
            counts[badge] = counts.get(badge, 0) + 1

    # Benefits claims -> On the Dole
    for uid, rec in (_load(config.BENEFITS_FILE) or {}).items():
        claimed = (isinstance(rec, dict) and rec.get("last")) or isinstance(rec, str)
        if claimed:
            give(uid, "on_the_dole")

    # Tree watering -> Green Fingers (+ Sir Branchalot at 100 lifetime waters)
    for uid, rec in (_load(config.TREE_WATER_FILE) or {}).items():
        give(uid, "green_fingers")
        if isinstance(rec, dict) and rec.get("total", 0) >= 100:
            give(uid, "sir_branchalot")

    # Bonds table
    try:
        rows = DatabaseManager.fetch_all(
            "SELECT user_id, term_days, status, principal, rate_pct FROM bonds") or []
    except Exception:
        rows = []
    interest = {}
    for uid, term, status, principal, rate in rows:
        give(uid, "saver")
        if term == 30:
            give(uid, "long_game")
        if status == "withdrawn":
            give(uid, "paper_hands")
        if status == "matured":
            interest[uid] = interest.get(uid, 0) + (int(principal) * int(rate) // 100)
    for uid, total in interest.items():
        if total >= 10000:
            give(uid, "bond_villain")

    # Roulette history -> Red Letter Day / Zero Hero
    try:
        rrows = DatabaseManager.fetch_all(
            "SELECT user_id, net, outcome FROM casino_results WHERE game = 'roulette'") or []
    except Exception:
        rrows = []
    for uid, net, outcome in rrows:
        if net is not None and net >= 1000:
            give(uid, "red_letter_day")
        if str(outcome) == "0":
            give(uid, "zero_hero")

    print("Backfilled:", counts or "nothing to award")


if __name__ == "__main__":
    main()
