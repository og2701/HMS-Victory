"""Shadow screening for join-watch with TypeSafe's Jev, to see whether it could take over.

Runs beside the OpenAI screener on every scan and never acts. Its verdict goes on the
usage-log card and into a JSONL file, so the two can be compared on real joiners rather
than on hand-written cases.

Jev answers yes/no questions with a probability; it does not write prose or look at images.
So the screen is one Noul per PERMANENT_FLOOR line plus one for the staff brief, and the
highest one is both the verdict and its reason. A flood across channels is decided in code
from the timestamps: Jev reads times as text and is unreliable at comparing them.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

JEV_MODEL = "jev-latest"
REQUEST_TIMEOUT_SECONDS = 8.0
FLOOD_CHANNELS = 3  # the same text in this many channels...
FLOOD_WINDOW_SECONDS = 120  # ...inside this long is a flood

SERVER = ("A casual, British, banter-heavy Discord server. Swearing, dark humour, edgy names and "
          "wind-ups are normal here.")


def _noul(question: str, yes: str, no: str) -> dict[str, Any]:
    return {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": no}}


# Mirrors PERMANENT_FLOOR in join_watch.py, one question per line. Threats and celebrating a
# tragedy are separate because one combined question put "you're dead tomorrow" at 0.77.
FLOOR_QUESTIONS = {
    "slur": _noul(
        "Does any message in `messages` use a slur (racial, ethnic, national, homophobic or sexual) "
        "or other hate speech?",
        "A slur or hate speech appears in a message.",
        "No slurs or hate speech; ordinary swearing does not count."),
    "threat": _noul(
        "Does any message in `messages` threaten a real person, or wish death or serious harm on them?",
        "A threat or death wish aimed at a real person.",
        "No threats; jokes and exaggeration about everyday things do not count."),
    "tragedy": _noul(
        "Does any message in `messages` celebrate or mock a real tragedy where people died or were hurt?",
        "It celebrates or laughs at a real tragedy.",
        "No tragedy is celebrated."),
    "sexual": _noul(
        "Does any message in `messages` direct sexual content at a member, or involve minors in sexual "
        "content?",
        "Sexual content aimed at a member, or involving minors.",
        "Nothing sexual aimed at anyone."),
    "doxx": _noul(
        "Does any message in `messages` post someone's personal information, such as an address, phone "
        "number or real name, to expose them?",
        "Personal information about someone is posted.",
        "No personal information is posted."),
    "spam": _noul(
        "Does the member post copypasta walls or repeated advertising in `messages`?",
        "Copypasta walls or repeated advertising.",
        "Ordinary messages; saying hello in a few places is not spam."),
    "raid": _noul(
        "Does any message in `messages` recruit or coordinate a raid on this server, such as calling "
        "people in to spam it?",
        "Recruiting or coordinating a raid.",
        "No raid recruiting or coordination."),
    "recruiting": _noul(
        "Is the member recruiting strangers into off-platform work in `messages`: unsolicited offers of "
        "tasks, jobs, easy money, giveaways, crypto or investments, or screening the room for who will "
        "take instructions in private?",
        "They are soliciting strangers into off-site work, money or tasks.",
        "No such offer; a plain 'dm me' between people already chatting does not count."),
    "scam": _noul(
        "Does any message in `messages` post scam bait: free Nitro, giveaway, gift card or login links, "
        "'I got hacked, click this', or selling accounts, boosts or codes?",
        "Scam bait is posted.",
        "No scam bait; links to ordinary sites, shops, news or clips do not count."),
    "impersonation": _noul(
        "Does the member claim in `messages` to be staff, a moderator, an admin, Discord itself or "
        "another member?",
        "They pass themselves off as staff, Discord or someone else.",
        "No such claim."),
}

RULE_LABELS = {
    "slur": "slur or hate speech", "threat": "threat", "tragedy": "celebrating a tragedy",
    "sexual": "sexual content", "doxx": "doxxing", "spam": "spam", "flood": "flood across channels",
    "raid": "raid recruiting", "recruiting": "off-platform recruiting", "scam": "scam bait",
    "impersonation": "impersonation", "brief": "the brief",
}


def shadow_enabled() -> bool:
    return bool(getattr(config, "JOIN_WATCH_JEV_SHADOW", False)) and bool(os.getenv("TYPESAFE_API_KEY"))


def _questions(brief: str) -> dict[str, Any]:
    questions = dict(FLOOR_QUESTIONS)
    if brief.strip():
        questions["brief"] = {
            "type": "noul",
            "instructions": {"brief": brief.strip(),
                             "question": "Has the member, in `messages`, done something that `brief` "
                                         "says to flag?"},
            "criteria": {"true": "At least one message does a thing `brief` says to flag.",
                         "false": "Nothing `brief` says to flag; talking about the topic without doing "
                                  "a flagged thing does not count."},
        }
    return questions


def is_flood(messages: list[dict[str, Any]]) -> bool:
    """The same text in FLOOD_CHANNELS or more channels inside FLOOD_WINDOW_SECONDS."""
    by_text: dict[str, list[tuple[int, str]]] = {}
    for m in messages:
        text = " ".join(str(m.get("content", "")).lower().split())
        if text and m.get("ts") is not None:
            by_text.setdefault(text, []).append((int(m["ts"]), str(m.get("channel", ""))))
    for hits in by_text.values():
        hits.sort()
        for i, (start, _) in enumerate(hits):
            channels = {ch for ts, ch in hits[i:] if ts - start <= FLOOD_WINDOW_SECONDS}
            if len(channels) >= FLOOD_CHANNELS:
                return True
    return False


def _state(member: Any, messages: list[dict[str, Any]]) -> dict[str, Any]:
    name = getattr(member, "display_name", None) or getattr(member, "name", None) or ""
    return {"server": SERVER,
            "member": {"display_name": str(name)},
            "messages": [{"channel": m.get("channel", ""), "text": m.get("content", "")}
                         for m in messages]}


def verdict_from(probs: dict[str, float], act_at: float) -> dict[str, Any]:
    rule = max(probs, key=probs.get) if probs else None
    p = probs.get(rule, 0.0) if rule else 0.0
    return {"call": "troll" if p >= act_at else "fine", "rule": rule, "p": p, "probs": probs}


async def screen(member: Any, messages: list[dict[str, Any]], brief: str,
                 act_at: float) -> dict[str, Any] | None:
    """Jev's verdict on the scan so far, or None if it could not be had. Never raises."""
    try:
        if not shadow_enabled() or not messages:
            return None
        from lib.features.mention_signals import _post

        started = time.monotonic()
        body = await _post({"model": JEV_MODEL, "state": _state(member, messages),
                            "questions": _questions(brief)},
                           os.environ["TYPESAFE_API_KEY"], session=None,
                           timeout=REQUEST_TIMEOUT_SECONDS)
        ms = int((time.monotonic() - started) * 1000)
        answers = (body or {}).get("answers") if isinstance(body, dict) else None
        if not answers:
            return None
        probs = {q: float(a.get("noul", 0.0)) for q, a in answers.items() if isinstance(a, dict)}
        if is_flood(messages):
            probs["flood"] = 1.0
        result = verdict_from(probs, act_at)
        result.update(ms=ms, model=body.get("model"),
                      tokens=(body.get("usage") or {}).get("input_tokens"))
        return result
    except Exception:
        logger.debug("join-watch jev shadow failed", exc_info=True)
        return None


def card_line(jev: dict[str, Any] | None, openai_acted: bool) -> str | None:
    """One line for the usage-log card. Flags a disagreement about acting, not about wording."""
    if not jev:
        return None
    label = RULE_LABELS.get(jev["rule"], jev["rule"])
    agree = (jev["call"] == "troll") == openai_acted
    return (f"-# 🧪 Jev (shadow): **{jev['call']}** · top: {label} {jev['p']:.0%} · {jev['ms']}ms"
            + ("" if agree else " · ⚠️ disagrees"))


def record(member: Any, messages: list[dict[str, Any]], openai_call: str, openai_conf: float,
           openai_reason: str, openai_acted: bool, jev: dict[str, Any] | None) -> None:
    """Append the pair of verdicts to the shadow log. Best effort."""
    if not jev:
        return
    path = getattr(config, "JOIN_WATCH_JEV_SHADOW_FILE", None)
    if not path:
        return
    row = {
        "ts": int(time.time()), "member_id": getattr(member, "id", None),
        "scan": len(messages), "messages": [m.get("content", "") for m in messages],
        "openai": {"call": openai_call, "confidence": round(openai_conf, 3),
                   "reason": openai_reason, "acted": openai_acted},
        "jev": {"call": jev["call"], "rule": jev["rule"], "p": round(jev["p"], 3),
                "probs": {k: round(v, 3) for k, v in jev["probs"].items()},
                "ms": jev.get("ms"), "model": jev.get("model")},
        "agree": (jev["call"] == "troll") == openai_acted,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("could not write the jev shadow log", exc_info=True)
