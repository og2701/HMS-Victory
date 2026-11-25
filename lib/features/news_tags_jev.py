"""Forum tags for news posts from TypeSafe's Jev: one yes/no question per tag (Oggers, 16 Nov 2026).

The OpenAI judgement picks tags as one small part of a big answer (importance, why it matters, post matching), and
the result was uneven: a tag left off where it plainly applied, World given to some US stories and not others, one
tag on one post and three on a similar one. Here every tag gets its own question and its own probability, so a tag
is on when it applies and off when it doesn't, whatever else the story touches.

A post gets every tag at or above TAG_THRESHOLD, highest first, up to MAX_TAGS. None means "no verdict": the caller
keeps the OpenAI tags.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

TAG_THRESHOLD = 0.6
MAX_TAGS = 3
REQUEST_TIMEOUT_SECONDS = 8.0


def _noul(question: str, yes: str, no: str) -> Dict[str, Any]:
    return {"type": "noul", "instructions": question, "criteria": {"true": yes, "false": no}}


# Keyed by the forum's tag names. A tag in the forum with no question here is never set by Jev.
TAG_QUESTIONS: Dict[str, Dict[str, Any]] = {
    "UK Politics": _noul(
        "Is this story about UK politics: Westminster, the UK government or ministers, political parties, MPs, "
        "or a UK law or policy being made, changed or fought over?",
        "UK politics, government action or policy is a main part of the story.",
        "UK politics is absent or only mentioned in passing."),
    "UK & EU": _noul(
        "Is this story about the UK's relationship with the European Union: Brexit, a UK-EU deal or talks, trade "
        "with the EU, youth mobility or the UK following EU rules?",
        "UK-EU relations are a main part of the story.",
        "The EU is absent, or the story is about the EU or a European country without the UK's relationship to it."),
    "US": _noul(
        "Does this story mainly take place in the United States, or is it about the US government, US politics, "
        "US courts or the US president?",
        "The United States is a main part of the story.",
        "The US is absent or only mentioned in passing; a US company's product alone does not count."),
    "Europe": _noul(
        "Does this story mainly take place in a European country other than the UK (Russia and Ukraine included), "
        "or is it about the EU's own affairs?",
        "Events in Europe outside the UK are a main part of the story.",
        "It is mainly about the UK, about UK-EU relations, or about somewhere outside Europe."),
    "Middle East": _noul(
        "Does this story mainly take place in the Middle East, Iran and the Gulf states included?",
        "The Middle East is a main part of the story.",
        "The Middle East is absent or only mentioned in passing."),
    "World": _noul(
        "Does this story mainly take place outside the UK, the United States, Europe and the Middle East (for "
        "example Asia, Africa, Latin America or Oceania), or is it a truly global story?",
        "Most of what happens is in those other parts of the world, or worldwide.",
        "It is mainly about the UK, the US, Europe or the Middle East, or about a company rather than a country."),
    "Immigration": _noul(
        "Is this story about immigration: asylum, small boats, migrants, visas, borders or deportations?",
        "Immigration or asylum is a main part of the story.",
        "Immigration is absent or only incidental."),
    "Cost of Living": _noul(
        "Is this story about household money: energy and other bills, prices and inflation, rent and mortgages, "
        "pay, or benefits people live on?",
        "What people pay or receive is a main part of the story.",
        "Household money is absent or only incidental; company results and markets alone do not count."),
    "Nations & Regions": _noul(
        "Is this story specifically about Scotland, Wales or Northern Ireland, their governments, one region of "
        "England, or one UK town or city and the people living there?",
        "It is about one of the UK's nations, an English region, or one UK place and its community specifically.",
        "It is UK-wide, not about the UK, or national news that merely happens at a place named in passing (a "
        "military base, a court, a school)."),
    "Business & Economy": _noul(
        "Is this story about business or the economy: companies, markets, jobs, industry, strikes over pay, "
        "taxes, the public finances or economic figures?",
        "Money, companies, work or the economy is a main part of the story.",
        "Money and business are absent or only incidental."),
    "Elections": _noul(
        "Is this story about an actual election or referendum anywhere: voting, a named election's campaign, its "
        "polls or its result?",
        "A specific election or referendum is central.",
        "No specific election; a party's policy pledge or ordinary political row does not count."),
    "Crime & Courts": _noul(
        "Is this story about crime, abuse, police, a trial, inquiry or court case, sentencing, criminal records "
        "or prisons?",
        "Crime, policing or the justice system is a main part of the story.",
        "No crime or justice system involvement beyond a passing mention."),
    "Conflict & Defence": _noul(
        "Is this story about war, armed attacks by states or armed groups, terrorism, the military, spying for a "
        "foreign state, national security threats or defence policy?",
        "War, military, security threats or defence is a main part of the story.",
        "Nothing military, war-related or about national security."),
    "Health": _noul(
        "Is this story about health: the NHS, illness, medicine, public health, abortion and reproductive "
        "health, mental health or social care?",
        "Health, healthcare or medicine is a main part of the story.",
        "Health is absent or only incidental."),
    "Education": _noul(
        "Is this story about education: schools, exams, pupils, teachers, universities or students?",
        "Education is a main part of the story.",
        "Education is absent or only incidental."),
    "Environment": _noul(
        "Is this story about the environment: climate change, extreme weather, nature, pollution, oil and gas "
        "licensing, or the energy transition?",
        "The environment, climate or energy policy is a main part of the story.",
        "The environment is absent or only incidental."),
    "Tech & Science": _noul(
        "Is this story about technology or science: tech companies and products, AI, the internet, cyber attacks, "
        "crypto, space or scientific research?",
        "Technology or science is a main part of the story.",
        "Technology and science are absent or only incidental; transport disruption, weather or politics alone "
        "do not count."),
    # Tone tags (Oggers 25 Nov 2026: a way to see only positive news). Not topics: jev_tone() reads them, pick_tags
    # leaves them out. gpt-4o gave Good News to 1 post in 157; these questions gave it to 12.
    "Good News": _noul(
        "Is this news story positive or uplifting overall: a success, rescue, recovery, breakthrough, good outcome, "
        "celebration or kind act, that most readers would be glad to hear?",
        "Most readers would come away feeling good about what happened.",
        "The story is neutral, mixed, worrying or bad news."),
    "Heavy": _noul(
        "Is this a distressing story: death, killing, violence, abuse, war casualties, disasters or serious harm to "
        "people?",
        "Death, violence, abuse or serious harm is central to the story.",
        "No death, violence, abuse or serious harm is central."),
    "Sport": _noul(
        "Is this a sport story: a match, a competition, athletes, clubs or sporting bodies?",
        "It is a sport story.",
        "It is not about sport."),
}


def questions_for(tag_names: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """The questions for the forum's current tags, keyed by an id Jev accepts (the tag's index)."""
    return {f"t{i}": TAG_QUESTIONS[name] for i, name in enumerate(tag_names) if name in TAG_QUESTIONS}


# Place tags go after any topic tag that also applies: a story about Nvidia's boss is Tech first, a Ukraine story is
# Conflict & Defence first. World is for everywhere else, so it is dropped beside US, Europe or Middle East.
GENERAL_TAGS = ("World", "US", "Europe", "Middle East")
TONE_NAMES = {"Good News": "good", "Heavy": "heavy"}
REGION_TAGS = ("US", "Europe", "Middle East")


def pick_tags(probs: Dict[str, float], threshold: float = TAG_THRESHOLD, limit: int = MAX_TAGS) -> List[str]:
    """Every tag at or above the threshold, most likely first with the place tags last (World dropped beside US,
    Europe or Middle East), capped."""
    ranked = sorted(((p, name) for name, p in probs.items() if p >= threshold and name not in TONE_NAMES),
                    key=lambda x: (x[1] in GENERAL_TAGS, -x[0], x[1]))
    names = [name for _, name in ranked]
    if any(n in REGION_TAGS for n in names):
        names = [n for n in names if n != "World"]
    return names[:limit]


async def tag_probabilities(title: str, summary: str, outlet: str, tag_names: Iterable[str],
                            api_key: Optional[str] = None, session=None,
                            timeout: float = REQUEST_TIMEOUT_SECONDS) -> Optional[Dict[str, float]]:
    """{tag name: probability} for each forum tag Jev has a question for, or None if Jev gave no usable answer."""
    from lib.features.mention_signals import MENTION_SIGNALS_MODEL, _post
    key = api_key or os.getenv("TYPESAFE_API_KEY")
    names = [n for n in tag_names if n in TAG_QUESTIONS]
    if not key or not names or not (title or "").strip():
        return None
    ids = {f"t{i}": name for i, name in enumerate(names)}
    payload = {
        "model": MENTION_SIGNALS_MODEL,
        "state": {"headline": title.strip(), "summary": (summary or "").strip()[:600], "outlet": outlet or ""},
        "questions": {qid: TAG_QUESTIONS[name] for qid, name in ids.items()},
    }
    body = await _post(payload, key, session=session, timeout=timeout)
    if not isinstance(body, dict):
        return None
    answers = body.get("answers") or {}
    probs: Dict[str, float] = {}
    for qid, name in ids.items():
        try:
            probs[name] = float((answers.get(qid) or {})["noul"])
        except (KeyError, TypeError, ValueError):
            continue
    if not probs:
        logger.warning("[NEWSFEED] Jev tags gave no answers for %r", title[:80])
        return None
    return probs


def jev_tone(probs: Optional[Dict[str, float]], threshold: float = TAG_THRESHOLD) -> Optional[str]:
    """"good", "heavy" or "neutral" from the tone questions, or None when Jev didn't answer them (keep the AI's)."""
    if not probs or not any(n in probs for n in TONE_NAMES):
        return None
    good, heavy = probs.get("Good News", 0.0), probs.get("Heavy", 0.0)
    if heavy >= threshold and heavy >= good:
        return "heavy"
    if good >= threshold:
        return "good"
    return "neutral"


async def jev_tags(title: str, summary: str, outlet: str, tag_names: Iterable[str],
                   **kwargs) -> Tuple[Optional[List[str]], Optional[Dict[str, float]]]:
    """(tags, probabilities). tags is None when Jev failed or nothing reached the threshold: keep the OpenAI tags."""
    probs = await tag_probabilities(title, summary, outlet, tag_names, **kwargs)
    if probs is None:
        return None, None
    return (pick_tags(probs) or None), probs
