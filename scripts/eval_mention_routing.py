"""Check how direct mentions get routed: records table, stats opinion, picture, or a plain reply.

Each case is sent to Jev with today's questions, exactly as the mention handler would, and the route the
code takes from the answers is checked against the expected one. Run it before and after changing the
questions or the thresholds in lib/features/mention_signals.py.

    TYPESAFE_API_KEY=... python scripts/eval_mention_routing.py [-v]

A records case passes when the code would answer from the database with one of the expected metrics or
lists; a chat case passes when it would not touch the database. -v prints every case's raw picks.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.features.mention_signals import judge_mention  # noqa: E402

CONCURRENCY = 6
BOT_LAST = "The biggest single casino win was over $39 million, bagged by a chap in Las Vegas. Quite a tidy sum, that."

# (message, expected, replied-to bot text or None). expected: a set of metric/list names (records), "chat",
# "opinion" (stats-grounded verdict) or "picture".
CASES = [
    # the two that went wrong on 2026-10-08
    ("whats the single biggest casino win", {"casino_biggest_win"}, None),
    ("the biggest casino win, in the server", {"casino_biggest_win"}, BOT_LAST),
    # casino records, phrased loosely
    ("biggest casino win", {"casino_biggest_win"}, None),
    ("who's won the most in one go at the casino", {"casino_biggest_win"}, None),
    ("what's the most anyone's won on roulette", {"casino_biggest_win", "casino_net"}, None),
    ("biggest single loss at the casino", {"casino_biggest_loss"}, None),
    ("who's lost the most gambling", {"casino_net"}, None),
    ("who's had the best day at the casino", {"casino_best_day"}, None),
    ("who's played the most slots", {"casino_games"}, None),
    ("how much has kim won at blackjack", {"casino_net"}, None),
    ("most anyone's bet in a single day", {"casino_biggest_day_stake"}, None),
    ("casino leaderboard", {"casino_net"}, None),
    # the rest of the records
    ("who's the richest", {"ukpence"}, None),
    ("what's my balance", {"ukpence"}, None),
    ("top 10 shutcoin users", {"shutcoins_used"}, None),
    ("how much xp has steven got", {"xp"}, None),
    ("biggest yapper this week", {"messages"}, None),
    ("what's in the house bank", {"bank"}, None),
    ("who owns yorkshire", {"county_owners"}, None),
    # not the server's records
    ("biggest casino win ever in las vegas", "chat", None),
    ("what's the biggest lottery win in uk history", "chat", None),
    ("tell me about the biggest casino in macau", "chat", None),
    ("the casino is rigged", "chat", None),
    ("i'm skint after the casino lol", "chat", None),
    ("how does blackjack work", "chat", None),
    ("write a poem about losing at roulette", "chat", None),
    ("who won the euromillions last night", "chat", None),
    ("lol vic you're useless", "chat", None),
    ("who's your favourite member based on stats", "opinion", None),
    ("draw the richest member as a pig", "picture", None),
]


def route(sig):
    if sig is None:
        return "no verdict", None
    if sig.action != "reply":
        return "picture", None
    if sig.data_query_requested():
        shape = sig.effective_shape
        return "records", (sig.data_list if shape == "list" else sig.data_metric)
    if sig.says("stats_opinion"):
        return "opinion", None
    return "chat", None


async def run_case(sem, text, expected, replied):
    async with sem:
        sig = await judge_mention(
            text, caller_name="ogme", has_reply_ref=replied is not None,
            replied_to_text=replied, replied_to_author="HMS Victory" if replied else None,
        )
    kind, pick = route(sig)
    if isinstance(expected, set):
        ok = kind == "records" and pick in expected
    else:
        ok = kind == expected
    return text, expected, kind, pick, ok, sig


async def main():
    if not os.getenv("TYPESAFE_API_KEY"):
        sys.exit("Set TYPESAFE_API_KEY first.")
    verbose = "-v" in sys.argv
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(run_case(sem, t, e, r) for t, e, r in CASES))
    passed = 0
    for text, expected, kind, pick, ok, sig in results:
        passed += ok
        want = "/".join(sorted(expected)) if isinstance(expected, set) else expected
        got = f"{kind}:{pick}" if pick else kind
        print(f"{'PASS' if ok else 'FAIL'}  {text[:52]:<52}  want {want:<34} got {got}")
        if verbose or not ok:
            print(f"      {sig.summary() if sig else 'no verdict'}")
    print(f"\n{passed}/{len(results)} routed as expected")


if __name__ == "__main__":
    asyncio.run(main())
