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

The same batch also asks whether the message wants a figure from the server's records (a
leaderboard, one member's balance, a comparison, a total) and which metric, window, game
and head-count it named. The catalogue of metrics comes from `data_queries`, so Jev is only
ever offered numbers the code can actually produce.

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

from lib.features.data_queries import (CASINO_GAMES, GAME_LABELS, PVP_GAMES, SHAPES, WINDOWS, catalogue_for_jev,
                                       lists_for_jev, sources_for_jev)

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
# A records question is answered from the database only when both the metric and the shape are
# clearly picked; anything flatter goes to the normal reply, which can still talk about it. The
# metric Choice has thirty options, so 0.5 is already a clear peak rather than a coin toss.
DATA_QUERY_CONFIDENCE = 0.5
# The follow-up call that picks a named person out of the people directory.
NAMED_SUBJECT_CONFIDENCE = 0.6

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
                "'Show me' / 'let's see' a member's badges, counties, balance, stats, record or Skyrim character means post the records as text: reply, not a picture.",
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
                "examples": ["write a poem about johnny", "why is his office in a pub", "lol the degrees", "what's the score tonight", "thanks vic", "notice how it featured the red lion twice", "show me my skyrim character", "show me kim's badges", "top 10 richest"],
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

_DATA_LIMIT_OPTIONS = {
    "not_given": "A list is wanted and no number of entries was stated ('leaderboard', 'the rankings', 'top players')",
    "single": "One member is asked for: who has the most or least, who's the richest, the biggest yapper, the most shut member",
}
for _n in (3, 5, 10, 15, 20):
    _DATA_LIMIT_OPTIONS[str(_n)] = f"{_n} entries"
SINGLE_ANSWER_LIMIT = 3   # "who has the most": the winner plus two runners-up for context
_DATA_GAME_OPTIONS = {"any": "No particular game named, or a game not listed here"}
for _g in list(CASINO_GAMES) + list(PVP_GAMES):
    _DATA_GAME_OPTIONS[_g] = GAME_LABELS[_g]

QUESTIONS.update({
    "data_metric": {
        "type": "choice",
        "instructions": {
            "question": "If `message.text` asks for a figure from the server's own records, which metric is it about? Otherwise none.",
            "rules": [
                "Records questions ask for a count, balance, amount, rank, ranking, leaderboard, 'who has the most/least', 'how much/many does X have', or a server total.",
                "A picture request, banter, an opinion or a question about the outside world is none, even if it mentions money or XP in passing.",
            ],
        },
        "criteria": catalogue_for_jev(),
    },
    "data_shape": {
        "type": "choice",
        "instructions": {
            "question": "If `message.text` asks for something from the server's records, what shape of answer does it want? Otherwise none.",
            "rules": [
                "A superlative about an unnamed member ('who has the most', 'who's the richest', 'biggest yapper', 'most shut member') is a leaderboard: the answer is the top of a ranked list.",
                "person is only for a member identified by name, @mention, or as the caller.",
                "list is for things rather than a number: what someone owns or has done, or a server fact sheet (the house bank, the lottery, the shop, the iceberg, open predictions), or who holds a particular badge or county.",
                "A target figure in the message ('closest to 100k', 'nearest to a million', 'who has about 50') is closest: the members nearest to that number, not the top of the list.",
            ],
        },
        "criteria": {
            "leaderboard": {"what": "A ranked list of members by a number, or the single top or bottom member", "examples": ["top 10 shutcoin users", "who's got the most xp", "richest members", "who's lost the most this week", "biggest yapper", "who's been shut the most"]},
            "person": {"what": "One member's own figure or date", "examples": ["how much xp does steven have", "how many shutcoins have I got", "what rank is @johnny", "when did steven first show up", "when was kim last here"]},
            "compare": {"what": "Two members set against each other on a number", "examples": ["who has more ukpence, me or steven", "compare my xp with kim's"]},
            "total": {"what": "One number for the whole server", "examples": ["how much ukpence is in circulation", "how many messages were sent today", "total badges handed out"]},
            "list": {"what": "A list of things or a fact sheet, not a single number", "examples": ["what badges has steven got", "what's in the house bank", "what counties does kim own", "what predictions are open", "who has the warden badge", "who owns yorkshire", "what did johnny say last"]},
            "closest": {"what": "The members whose figure is nearest to a target number stated in the message", "examples": ["who has closest to 100k xp", "who's nearest to a million ukp", "who has about 50 shutcoins", "who is closest to 10,000 messages"]},
            "none": {"what": "Not a records question"},
        },
    },
    "data_list": {
        "type": "choice",
        "instructions": "If `message.text` asks for a list of things or a fact sheet from the server's records (`data_shape` list), which one? Otherwise none.",
        "criteria": lists_for_jev(),
    },
    "data_source": {
        "type": "choice",
        "instructions": "For a question about UKP earned or spent, does `message.text` name one source of the money? all when it doesn't.",
        "criteria": sources_for_jev(),
    },
    "data_limit": {
        "type": "choice",
        "instructions": "How many entries does `message.text` ask for? A stated number ('top 10', 'the five richest'); single when it asks who THE top or bottom member is; not_given when a list is wanted with no number, or it isn't a ranked list.",
        "criteria": _DATA_LIMIT_OPTIONS,
    },
    "data_lowest": {
        "type": "noul",
        "instructions": "Does `message.text` ask for the LOWEST, least, worst, poorest or bottom end rather than the highest?",
        "criteria": {
            "true": {"examples": ["who's the poorest", "bottom 5 by xp", "who's lost the most", "least active members", "who has the fewest badges"]},
            "false": {"what": "Highest, most, richest, best, top, or no direction stated"},
        },
    },
    "data_window": {
        "type": "choice",
        "instructions": "What time window does `message.text` name for the figure? all_time when none is stated.",
        "criteria": {
            "all_time": "No period stated, or 'ever', 'all time', 'overall'",
            "today": "Today, the last 24 hours, tonight",
            "week": "This week, the last 7 days, recently",
            "month": "This month, the last 30 days",
        },
    },
    "data_game": {
        "type": "choice",
        "instructions": "Does `message.text` name one particular casino or PvP game for the figure? any when it doesn't.",
        "criteria": _DATA_GAME_OPTIONS,
    },
    "data_subject": {
        "type": "choice",
        "instructions": "For a records question about a specific member (`data_shape` person), who is it about?",
        "criteria": {
            "caller": {"what": "The person sending the message: me, my, I, myself", "examples": ["how much xp have I got", "what's my balance"]},
            "mentioned_user": {"what": "A user @mentioned in the message (see `message.mentioned_users`)"},
            "someone_named": {"what": "A member referred to by name or nickname without an @mention", "examples": ["how many shutcoins does steven have", "kim's xp"]},
            "not_applicable": {"what": "Not about one specific member, or not a records question"},
        },
    },
})

_NOUL_KEYS = ("text_creation", "delegation", "attachment_modification", "bot_self", "random_pick", "group", "follow_up_image", "live_query", "data_lowest")
ACTIONS = ("generate", "edit", "reply")
DATA_SHAPES = SHAPES + ("none",)
DATA_SUBJECTS = ("caller", "mentioned_user", "someone_named", "not_applicable")


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
    data_lowest: float
    group_count: Optional[int]
    data_metric: str = "none"
    data_metric_confidence: float = 0.0
    data_shape: str = "none"
    data_shape_confidence: float = 0.0
    data_limit: Optional[int] = None
    data_window: str = "all_time"
    data_game: Optional[str] = None
    data_subject: str = "not_applicable"
    data_list: str = "none"
    data_list_confidence: float = 0.0
    data_source: Optional[str] = None
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

    @property
    def effective_shape(self) -> str:
        """The shape to answer in. A confident list pick wins whenever the shape is 'list' or no metric was
        picked: "what's the lottery pot" comes back shape total with metric none and list lottery, and the
        list is the only one of the three that names something the code can produce."""
        list_sure = self.data_list != "none" and self.data_list_confidence >= DATA_QUERY_CONFIDENCE
        metric_sure = self.data_metric != "none" and self.data_metric_confidence >= DATA_QUERY_CONFIDENCE
        if list_sure and (self.data_shape == "list" or not metric_sure):
            return "list"
        if self.data_shape == "list" or self.data_shape_confidence < DATA_QUERY_CONFIDENCE:
            return "none"
        return self.data_shape

    def data_query_requested(self) -> bool:
        """True when Jev clearly picked something the code can fetch: answer from the database, not a model.

        Never for a picture: "draw the top 5 richest as pigs" wants the roster drawn, not a table.
        """
        if self.action != "reply":
            return False
        shape = self.effective_shape
        if shape == "list":
            return True
        if shape == "none":
            return False
        return self.data_metric != "none" and self.data_metric_confidence >= DATA_QUERY_CONFIDENCE

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
        data = ""
        if self.data_metric != "none" or self.data_shape != "none" or self.data_list != "none":
            data = (f" data={self.data_metric}({self.data_metric_confidence:.2f})/{self.data_shape}({self.data_shape_confidence:.2f})"
                    f"/{self.data_list}({self.data_list_confidence:.2f}) limit={self.data_limit} window={self.data_window}"
                    f" game={self.data_game} source={self.data_source} subject={self.data_subject}")
        return f"action={self.action}({self.action_confidence:.2f}) {flags}{count}{data}"


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

    def choice(key: str, allowed, default: str) -> Tuple[str, float]:
        ans = answers.get(key) or {}
        picked = str(ans.get("choice", default))
        if picked not in allowed:
            picked = default
        try:
            conf = float(ans.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        return picked, conf

    metric_keys = set(QUESTIONS["data_metric"]["criteria"])
    data_metric, metric_conf = choice("data_metric", metric_keys, "none")
    data_shape, shape_conf = choice("data_shape", DATA_SHAPES, "none")
    limit_pick, limit_conf = choice("data_limit", set(_DATA_LIMIT_OPTIONS), "not_given")
    if limit_pick == "single" and limit_conf >= GROUP_COUNT_CONFIDENCE:
        data_limit: Optional[int] = SINGLE_ANSWER_LIMIT
    elif limit_pick not in ("not_given", "single") and limit_conf >= GROUP_COUNT_CONFIDENCE:
        data_limit = int(limit_pick)
    else:
        data_limit = None
    data_window, _ = choice("data_window", set(WINDOWS), "all_time")
    game_pick, _ = choice("data_game", set(_DATA_GAME_OPTIONS), "any")
    data_subject, _ = choice("data_subject", DATA_SUBJECTS, "not_applicable")
    data_list, list_conf = choice("data_list", set(QUESTIONS["data_list"]["criteria"]), "none")
    source_pick, _ = choice("data_source", set(QUESTIONS["data_source"]["criteria"]), "all")

    usage = usage or {}
    return MentionSignals(
        action=action,
        action_confidence=float(action_ans.get("confidence", 0.0)),
        action_probabilities=probs,
        group_count=group_count,
        data_metric=data_metric,
        data_metric_confidence=metric_conf,
        data_shape=data_shape,
        data_shape_confidence=shape_conf,
        data_limit=data_limit,
        data_window=data_window,
        data_game=None if game_pick == "any" else game_pick,
        data_subject=data_subject,
        data_list=data_list,
        data_list_confidence=list_conf,
        data_source=None if source_pick == "all" else source_pick,
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
    body = await _post(payload, key, session=session, timeout=timeout)
    if not isinstance(body, dict):
        return None
    signals = parse_signals(body.get("answers") or {}, body.get("usage") or {})
    if signals is not None:
        logger.info("Jev on %r: %s", (prompt or "")[:80], signals.summary())
    return signals


async def judge_option(
    prompt: str,
    options: Dict[str, Any],
    *,
    what: str,
    instructions: str,
    none_means: str,
    api_key: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    max_options: int = 120,
) -> Tuple[Any, int, int]:
    """One Choice over real candidates: (the chosen option's value or None, input_tokens, output_tokens).

    A second, tiny call for things the first verdict cannot name: a member from the people
    directory, a badge, a county. Selecting from a list is the point: Jev can pick a name it
    could never spell, and it cannot answer with something that does not exist.
    """
    key = api_key or os.getenv("TYPESAFE_API_KEY")
    if not key or not (prompt or "").strip() or not options:
        return None, 0, 0
    labels = [str(k).strip() for k in options if str(k).strip()][:max_options]
    if not labels:
        return None, 0, 0
    criteria: Dict[str, Any] = {"none": none_means}
    criteria.update({label: None for label in labels})
    payload = {
        "model": MENTION_SIGNALS_MODEL,
        "state": {"message": (prompt or "").strip(), what: labels},
        "questions": {"which": {"type": "choice", "instructions": instructions, "criteria": criteria}},
    }
    body = await _post(payload, key, session=session, timeout=timeout)
    if not isinstance(body, dict):
        return None, 0, 0
    usage = body.get("usage") or {}
    in_tok, out_tok = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    ans = (body.get("answers") or {}).get("which") or {}
    picked = ans.get("choice")
    try:
        conf = float(ans.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    logger.info("Jev pick (%s) on %r: %r (%.2f)", what, (prompt or "")[:80], picked, conf)
    if picked in options and picked != "none" and conf >= NAMED_SUBJECT_CONFIDENCE:
        return options[picked], in_tok, out_tok
    return None, in_tok, out_tok


async def judge_named_subject(
    prompt: str,
    candidates: List[Tuple[str, int]],
    *,
    api_key: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> Tuple[Optional[int], int, int]:
    """Which member of the people directory a records question names. (user_id or None, input_tokens, output_tokens)."""
    by_label: Dict[str, int] = {}
    for name, uid in candidates:
        label = (name or "").strip()
        if not label or not isinstance(uid, int):
            continue
        if label in by_label and by_label[label] != uid:
            label = f"{label} ({uid})"
        by_label.setdefault(label, uid)
        if len(by_label) >= 80:
            break
    return await judge_option(
        prompt, by_label, what="members",
        instructions="Which of `members` is the person `message` asks about? Match names, nicknames, partial names and misspellings; none if they are not listed.",
        none_means="The person asked about is not in the list, or the message is not about one particular person",
        api_key=api_key, session=session, timeout=timeout,
    )


async def judge_pick(
    prompt: str,
    names: List[str],
    *,
    what: str,
    api_key: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> Tuple[Optional[str], int, int]:
    """Which named thing (a badge, a county) a records question is about. (name or None, tokens in, tokens out)."""
    return await judge_option(
        prompt, {n: n for n in names}, what=what,
        instructions=f"Which of `{what}` does `message` ask about? Match names, nicknames, partial names and misspellings; none if it is not listed.",
        none_means="Not one of the listed names, or the message is not about one in particular",
        api_key=api_key, session=session,
    )


async def _post(payload: Dict[str, Any], key: str, *, session: Optional[aiohttp.ClientSession], timeout: float) -> Any:
    """One System One request with a single retry on a blip. Returns the JSON body or None."""
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
    return body
