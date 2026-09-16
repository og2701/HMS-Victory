"""Fast typed judgments about a direct mention, from TypeSafe's Jev model.

Working out what a mention wants used to be a cascade: a gpt-4o planner on every message, a
gpt-4o-mini classifier if that failed, and a pile of regexes under both. The regexes were
brittle to phrasing ("do me next" worked, "go on then, me" did not) and the planner ran on
pure banter that never needed one. Jev is not a language model: it takes the message plus
its surroundings as state and answers a batch of typed questions in one parallel pass, in a
few hundred milliseconds, for a fraction of a cent. It cannot paraphrase or resolve names,
so the planner still does that for pictures; this decides whether the planner is needed at
all and replaces the regex layer everywhere else.

Every answer is a probability. `MentionSignals.says()` applies one threshold for the yes/no
signals; the action Choice carries its own confidence and gates the planner separately, so a
flat distribution between edit and reply never skips the model that can tell them apart.

Nothing here ever blocks a reply: no key, a timeout, a bad status or a malformed body all
come back as None and the caller falls through to what it did before.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
MENTION_SIGNALS_MODEL = "jev-latest"
REQUEST_TIMEOUT_SECONDS = 4.0

# A Noul is the probability of "yes"; 0.5 is a coin toss, not "somewhat". Every yes/no signal
# here used to be a regex boolean, so the neutral cut-over is the honest replacement.
NOUL_THRESHOLD = 0.5
# The planner (gpt-4o) is skipped only when Jev is this sure the message wants text. Choice
# confidence measures how peaked the distribution is; a wrong skip costs a re-ask, a wrong
# planner call costs a cent, so the bar is high but not absurd.
REPLY_GATE_CONFIDENCE = 0.85
# A head-count is only trusted when the distribution is clearly peaked; a flat spread over
# "5", "6" and "not_given" means the number was probably counting something else.
GROUP_COUNT_CONFIDENCE = 0.6

MAX_GROUP_COUNT = 12

BOT_DESCRIPTION = (
    "HMS Victory, also called Vic: a Discord bot with a dry British persona. It replies in text and can "
    "generate or edit images (portraits, caricatures, sprite sheets, comics, anything visual) with an AI "
    "image generator."
)

_GROUP_COUNT_OPTIONS: Dict[str, Any] = {"not_given": "No number of people was asked for, or the number counts something other than people"}
for _n in range(2, MAX_GROUP_COUNT + 1):
    _GROUP_COUNT_OPTIONS[str(_n)] = f"{_n} people in the picture"
_GROUP_COUNT_OPTIONS["more_than_12"] = "More than twelve people"

QUESTIONS: Dict[str, Dict[str, Any]] = {
    "action": {
        "type": "choice",
        "instructions": {
            "question": "What does `message.text` want from the bot?",
            "rules": [
                "If the message is a bare hand-off ('pls do this', '^', 'what he said'), judge the request it points at in `replied_to` and `reply_chain` instead, with any tweaks along the chain folded in.",
                "Written things are always reply, whatever the verb: poems, soliloquies, songs, raps, speeches, roasts in words, letters, lists, opinions, facts, questions, banter, thanks.",
                "A terse statement of fact or a bare descriptor sent shortly after the bot posted an image ('the cat is black', 'no, blonde', 'he's bald', 'the green one') is a correction to that image: edit.",
                "If `message.attached_image` is true and they ask to change, add to, remove from or restyle 'this picture' / 'this gentleman' / 'him', that is an edit of the attachment, not a new image.",
                "Only choose edit when there is something to edit: a recent bot image, an image in the reply chain, or an attachment. If they want a picture and nothing exists, choose generate.",
                "When torn between edit and reply, choose reply: a wasted image costs money, a text reply does not.",
            ],
        },
        "criteria": {
            "generate": {
                "what": "Make a new picture of someone or something",
                "examples": ["draw steven as a goblin", "what do I look like", "do me next", "same for @X", "picture of the lads at the pub", "sprite sheet of hadidas", "generate what you think johnny looks like from his messages"],
            },
            "edit": {
                "what": "Change, correct or redo an existing picture: the bot's most recent image, an image in the reply chain, or one the user attached",
                "examples": ["bit generous with the hair", "make him balder", "remove the flag", "try again without the hat", "add a pink mullet to this gentleman", "no, blonde"],
            },
            "reply": {
                "what": "Answer in text: banter, questions, facts, opinions, written pieces, reactions to an image with no change asked for",
                "examples": ["write a poem about johnny", "why is his office in a pub", "lol the degrees", "what's the score tonight", "thanks vic", "notice how it featured the red lion twice"],
            },
        },
    },
    "text_creation": {
        "type": "noul",
        "instructions": "Does `message.text` ask for a WRITTEN piece rather than a picture, whatever the verb ('write', 'make', 'create', 'generate')?",
        "criteria": {
            "true": {"what": "A poem, soliloquy, song, rap, story, speech, eulogy, roast in words, letter, list, review, joke, horoscope or similar written form", "examples": ["make a hate soliloquy for X", "generate a limerick about the lads"]},
            "false": {"what": "A picture is wanted, or nothing is being created at all", "examples": ["make a caricature of X", "what do you think of the weather"]},
        },
    },
    "delegation": {
        "type": "noul",
        "instructions": "Is `message.text` a bare hand-off that only makes sense as 'do what the replied-to message asked'?",
        "criteria": {
            "true": {"what": "A short deferral with no request of its own", "examples": ["pls do this", "do that", "this one", "^", "what he said", "same", "go on then, sort it"]},
            "false": {"what": "The message states its own request, even if it also refers to another message", "examples": ["do that but make him bald", "draw me next"]},
        },
    },
    "attachment_modification": {
        "type": "noul",
        "instructions": "If `message.attached_image` is true: does `message.text` ask to change, add to, remove from or restyle THAT attached image rather than draw someone afresh? If `message.attached_image` is false, answer no.",
        "criteria": {
            "true": {"what": "The attached picture is the thing to be modified", "examples": ["add a pink mullet to this fine gentleman", "give this photo a hat", "make him bald", "turn this into a sprite sheet"]},
            "false": {"what": "The attachment is a reference for a new picture, or something to react to, or there is no attachment", "examples": ["sprite sheet of hadidas (attached)", "what do you make of this", "rate this"]},
        },
    },
    "bot_self": {
        "type": "noul",
        "instructions": "Is the picture or piece asked for in `message.text` of the bot ITSELF, HMS Victory / Vic? In a message addressed to the bot, 'you', 'yourself', 'what you look like' and 'your self-portrait' mean the bot, never the caller.",
        "criteria": {
            "true": {"examples": ["show us an image of what you look like", "draw yourself", "a picture of you", "what do you think you look like"]},
            "false": {"what": "The subject is the caller, another member, a group, or not a person", "examples": ["draw me", "what does steven look like"]},
        },
    },
    "random_pick": {
        "type": "noul",
        "instructions": "Does `message.text` ask for ONE person chosen at random to be the subject?",
        "criteria": {
            "true": {"examples": ["pick someone at random and draw them", "a random member", "surprise me with someone", "choose one of the users in this channel"]},
            "false": {"what": "A specific person, several people, or nobody in particular"},
        },
    },
    "group": {
        "type": "noul",
        "instructions": "Is the picture asked for in `message.text` of SEVERAL people or the community as a whole, rather than one named person?",
        "criteria": {
            "true": {"examples": ["the members of ukplace", "everyone here", "all of us", "the lads", "the regulars", "the top 5 chatters", "a family photo of the channel"]},
            "false": {"what": "One person, the bot, or not a person"},
        },
    },
    "follow_up_image": {
        "type": "noul",
        "instructions": "Is `message.text` a follow-up asking for ANOTHER picture in the same vein as one the bot recently made (see `bot_recent_image` and `recent_chat`)?",
        "criteria": {
            "true": {"examples": ["do me next", "now do steven", "same for @X", "what about me", "my turn", "another one"]},
            "false": {"what": "A fresh, self-contained request, a tweak to the existing picture, or no picture at all"},
        },
    },
    "live_query": {
        "type": "noul",
        "instructions": "Would answering `message.text` well need UP-TO-DATE information from the web: news, weather, sports scores or fixtures, prices, or anything about today, tonight, this week or 'right now'?",
        "criteria": {
            "true": {"examples": ["what league one games are on today", "what's the weather like", "did pompey win", "latest on the strike"]},
            "false": {"what": "Banter, opinions, things about server members, timeless facts, or a picture request"},
        },
    },
    "group_count": {
        "type": "choice",
        "instructions": "How many PEOPLE does `message.text` ask to put in the picture? Count only a number that counts people ('the 12 most prominent people', 'six of the lads', 'top 5 NPCs'). A number counting anything else ('in 5 different styles', 'the 3 best memes') means not_given, as does no number at all.",
        "criteria": _GROUP_COUNT_OPTIONS,
    },
}

_NOUL_KEYS = ("text_creation", "delegation", "attachment_modification", "bot_self", "random_pick", "group", "follow_up_image", "live_query")
ACTIONS = ("generate", "edit", "reply")


@dataclass(frozen=True)
class MentionSignals:
    """One batch of answers about a mention. Nouls are probabilities of yes; see `says()`."""

    action: str
    action_confidence: float
    action_probabilities: Dict[str, float]
    text_creation: float
    delegation: float
    attachment_modification: float
    bot_self: float
    random_pick: float
    group: float
    follow_up_image: float
    live_query: float
    group_count: Optional[int]
    input_tokens: int = 0
    output_tokens: int = 0

    def says(self, name: str) -> bool:
        """Whether a yes/no signal clears the threshold. Only valid for the Noul fields."""
        if name not in _NOUL_KEYS:
            raise KeyError(name)
        return float(getattr(self, name)) >= NOUL_THRESHOLD

    def confident_reply(self) -> bool:
        """True when the message so clearly wants text that the gpt-4o planner can be skipped."""
        return self.action == "reply" and self.action_confidence >= REPLY_GATE_CONFIDENCE

    def as_intent(self) -> Dict[str, Any]:
        """The shape `classify_mention_intent` returned, so the fallback path consumes Jev unchanged.

        The classifier named the subject; Jev only knows the kinds it was asked about, so anything
        that is not the bot, a random pick or a group is left to mention/name resolution downstream.
        """
        if self.says("bot_self"):
            subject = "bot"
        elif self.says("random_pick"):
            subject = "random"
        elif self.says("group"):
            subject = "group"
        else:
            subject = None
        return {
            "intent": self.action,
            "subject": subject,
            "subject_name": None,
            "reason": f"jev {self.summary()}",
            "input_tokens": 0,
            "output_tokens": 0,
        }

    def summary(self) -> str:
        """Compact one-line rendering for the logs."""
        flags = " ".join(f"{k}={getattr(self, k):.2f}" for k in _NOUL_KEYS)
        count = f" count={self.group_count}" if self.group_count else ""
        return f"action={self.action}({self.action_confidence:.2f}) {flags}{count}"


def build_state(
    prompt: str,
    *,
    caller_name: str,
    mentioned_names: Optional[List[str]] = None,
    has_reply_ref: bool = False,
    replied_to_text: Optional[str] = None,
    replied_to_author: Optional[str] = None,
    reply_chain: Optional[List[Tuple[str, str]]] = None,
    has_attached_image: bool = False,
    has_recent_bot_image: bool = False,
    recent_bot_image_prompt: Optional[str] = None,
    recent_history: str = "",
) -> Dict[str, Any]:
    """The state Jev judges: the message with everything the old classifier was told, as named fields."""
    replied_to = None
    if replied_to_text or replied_to_author:
        replied_to = {"author": replied_to_author or "someone", "text": (replied_to_text or "").strip()[:500]}
    chain = [{"author": who, "text": (txt or "").strip()[:220]} for who, txt in (reply_chain or [])]
    return {
        "bot": BOT_DESCRIPTION,
        "message": {
            "author": caller_name,
            "text": (prompt or "").strip(),
            "mentioned_users": list(mentioned_names or []),
            "is_discord_reply": bool(has_reply_ref),
            "attached_image": bool(has_attached_image),
        },
        "replied_to": replied_to,
        "reply_chain": chain,
        "bot_recent_image": {
            "posted": bool(has_recent_bot_image),
            "description": (recent_bot_image_prompt or "")[:300] if has_recent_bot_image else None,
        },
        "recent_chat": (recent_history or "").strip()[:1500],
    }


def parse_signals(answers: Dict[str, Any], usage: Optional[Dict[str, Any]] = None) -> Optional[MentionSignals]:
    """Turn the API's answers map into signals, or None if anything the code relies on is missing."""
    try:
        action_ans = answers["action"]
        action = action_ans["choice"]
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        probs = {k: float(v) for k, v in (action_ans.get("probabilities") or {}).items()}
        nouls = {k: float(answers[k]["noul"]) for k in _NOUL_KEYS}
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("Jev answers missing or malformed: %s", exc)
        return None

    group_count: Optional[int] = None
    count_ans = answers.get("group_count") or {}
    try:
        if float(count_ans.get("confidence", 0.0)) >= GROUP_COUNT_CONFIDENCE:
            picked = str(count_ans.get("choice", "not_given"))
            if picked == "more_than_12":
                group_count = MAX_GROUP_COUNT
            elif picked != "not_given":
                group_count = max(2, min(int(picked), MAX_GROUP_COUNT))
    except (TypeError, ValueError):
        group_count = None

    usage = usage or {}
    return MentionSignals(
        action=action,
        action_confidence=float(action_ans.get("confidence", 0.0)),
        action_probabilities=probs,
        group_count=group_count,
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        **nouls,
    )


async def judge_mention(
    prompt: str,
    *,
    caller_name: str,
    mentioned_names: Optional[List[str]] = None,
    has_reply_ref: bool = False,
    replied_to_text: Optional[str] = None,
    replied_to_author: Optional[str] = None,
    reply_chain: Optional[List[Tuple[str, str]]] = None,
    has_attached_image: bool = False,
    has_recent_bot_image: bool = False,
    recent_bot_image_prompt: Optional[str] = None,
    recent_history: str = "",
    api_key: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Optional[MentionSignals]:
    """Ask Jev the whole question batch about one mention. None means "no verdict, do what you did before"."""
    key = api_key or os.getenv("TYPESAFE_API_KEY")
    if not key or not (prompt or "").strip():
        return None

    payload = {
        "model": MENTION_SIGNALS_MODEL,
        "state": build_state(
            prompt,
            caller_name=caller_name,
            mentioned_names=mentioned_names,
            has_reply_ref=has_reply_ref,
            replied_to_text=replied_to_text,
            replied_to_author=replied_to_author,
            reply_chain=reply_chain,
            has_attached_image=has_attached_image,
            has_recent_bot_image=has_recent_bot_image,
            recent_bot_image_prompt=recent_bot_image_prompt,
            recent_history=recent_history,
        ),
        "questions": QUESTIONS,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()
    body: Any = None
    try:
        for attempt in (1, 2):
            try:
                async with session.post(
                    TYPESAFE_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        break
                    text = (await resp.text())[:300]
                    # 429 and 529 are the service asking for a moment; anything else 5xx is a blip worth one
                    # more go. A 4xx is our payload's fault and will not improve on retry.
                    if resp.status in (429, 529) or resp.status >= 500:
                        logger.warning("Jev HTTP %s (attempt %d): %s", resp.status, attempt, text)
                        if attempt == 1:
                            await asyncio.sleep(0.3)
                            continue
                    else:
                        logger.warning("Jev HTTP %s: %s", resp.status, text)
                    return None
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                logger.warning("Jev request failed (attempt %d): %s", attempt, exc)
                if attempt == 1:
                    await asyncio.sleep(0.3)
                    continue
                return None
    finally:
        if own_session:
            await session.close()

    if not isinstance(body, dict):
        return None
    signals = parse_signals(body.get("answers") or {}, body.get("usage") or {})
    if signals is not None:
        logger.info("Jev on %r: %s", (prompt or "")[:80], signals.summary())
    return signals
