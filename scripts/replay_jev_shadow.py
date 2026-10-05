"""Re-score the join-watch Jev shadow log with the current questions.

Every logged scan is sent to Jev again with today's wording, and the result is set beside
what Jev said at the time and what OpenAI did. Use it before changing the questions, to
check a fix catches what it should without new false alarms on everything else.

    TYPESAFE_API_KEY=... python scripts/replay_jev_shadow.py path/to/join_watch_jev_shadow.jsonl

Rows logged before version 2 have message text only, so the replay sends them with no
channel, and keeps the flood verdict they were logged with.
"""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from commands.moderation import join_watch_jev as jj  # noqa: E402
from commands.moderation.join_watch import ACT_CONFIDENCE  # noqa: E402
from lib.features.mention_signals import _post  # noqa: E402

CONCURRENCY = 8


def _messages(row):
    out = []
    for m in row["messages"]:
        if isinstance(m, str):
            out.append({"channel": "", "content": m, "ts": None})
        else:
            out.append({"channel": m.get("channel", ""), "content": m.get("text", ""), "ts": m.get("ts")})
    return out


class _Member:
    def __init__(self, row):
        self.id = row.get("member_id")
        self.display_name = row.get("display_name", "")


async def _rescore(row, key, gate):
    msgs = _messages(row)
    async with gate:
        body = await _post({"model": jj.JEV_MODEL, "state": jj._state(_Member(row), msgs),
                            "questions": jj._questions("")}, key, session=None, timeout=20)
    if not isinstance(body, dict) or not body.get("answers"):
        return None
    probs = {q: float(a.get("noul", 0.0)) for q, a in body["answers"].items()}
    if "flood" in row["jev"]["probs"] or jj.is_flood([m for m in msgs if m["ts"] is not None]):
        probs["flood"] = 1.0
    return jj.verdict_from(probs, ACT_CONFIDENCE)


def _last(row, n=2):
    return " | ".join(m if isinstance(m, str) else m.get("text", "") for m in row["messages"][-n:])[:160]


async def main(path):
    key = os.environ["TYPESAFE_API_KEY"]
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    gate = asyncio.Semaphore(CONCURRENCY)
    new = await asyncio.gather(*(_rescore(r, key, gate) for r in rows))
    pairs = [(r, n) for r, n in zip(rows, new) if n is not None]
    print(f"re-scored {len(pairs)}/{len(rows)} scans with questions v{jj.QUESTIONS_VERSION}\n")

    print("CHANGED CALL, OR TOP SCORE MOVED BY 0.2+")
    for r, n in pairs:
        old = r["jev"]
        if old["call"] != n["call"] or abs(old["p"] - n["p"]) >= 0.2:
            others = ", ".join(f"{q} {p:.2f}" for q, p in sorted(n["probs"].items(), key=lambda kv: -kv[1])
                               if p >= 0.2 and q != n["rule"])
            print(f"  openai {r['openai']['call']:<6} acted={str(r['openai']['acted']):<5} "
                  f"old {old['call']:<5} {old['rule']} {old['p']:.2f}  ->  new {n['call']:<5} "
                  f"{n['rule']} {n['p']:.2f}" + (f" (also {others})" if others else "")
                  + f"   {_last(r)}")

    def agree(call, row):
        return (call == "troll") == row["openai"]["acted"]

    acted = sum(r["openai"]["acted"] for r, _ in pairs)
    print(f"\nOpenAI acted on {acted} scans")
    print(f"agreement with OpenAI: old {sum(agree(r['jev']['call'], r) for r, _ in pairs)}"
          f"  new {sum(agree(n['call'], r) for r, n in pairs)}  of {len(pairs)}")
    print(f"Jev would act: old {sum(r['jev']['call'] == 'troll' for r, _ in pairs)}"
          f"  new {sum(n['call'] == 'troll' for _, n in pairs)}")
    print("Jev would act where OpenAI did not (new):")
    for r, n in pairs:
        if n["call"] == "troll" and not r["openai"]["acted"]:
            print(f"  {n['rule']} {n['p']:.2f}  openai {r['openai']['call']} "
                  f"{r['openai']['confidence']}   {_last(r)}")
    for t in (0.1, 0.2, 0.3):
        skipped = [(r, n) for r, n in pairs if n["p"] < t]
        missed = sum(r["openai"]["acted"] for r, _ in skipped)
        print(f"first-pass gate at {t}: skips {len(skipped)}/{len(pairs)} OpenAI calls, "
              f"misses {missed} of {acted} timeouts")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "data/json/join_watch_jev_shadow.jsonl"))
