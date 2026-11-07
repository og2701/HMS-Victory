"""News forum auto-poster: BBC News into the news forum, one post per story.

Every NEWSFEED_POLL_MINUTES the bot reads BBC News (top stories and the section feeds) and, for cross-checking only,
Sky News and the Guardian. Each BBC story is tracked: when it was first and last seen, which feeds carried it, and
when it entered and last sat in BBC top stories. Those give a signal score before any AI is involved:

    in BBC top stories now        +2
    each hour it has stayed there +1 (up to +4)
    a BBC live page (/news/live/) +3   (a major ongoing event: a megathread)
    each other outlet running it  +2 (up to +4; Sky, Guardian, matched on headline words)
    in three or more BBC feeds    +1   (top stories plus one section is the norm)

Only a story over the signal threshold goes to the AI, through the chat responder's own OpenAI client. The AI rates
its importance for this server, picks the forum tag, writes a neutral "why it matters" and a discussion question,
and says whether it is the same developing story as one of the forum's open posts from the last few days. Then:

    article already linked in an open post -> nothing (no duplicate)
    same story as an open post  -> an "Update:" reply in that post (unarchived if needed)

"Open posts" means every unlocked post in the forum from the last NEWSFEED_MATCH_DAYS, members' as well as Vic's:
if a member got there first, the article goes into their post as an update. Members' posts are only ever replied
to, never edited, retagged or pinned.
    strong enough on its own    -> a new post (title = headline, body = link, why it matters, question)
    otherwise                   -> nothing; it may be judged again if its signal climbs

New posts are capped per UK day and per poll. A story is only ever posted once (by URL, persisted). Live pages get
the Megathread tag and are pinned when nothing else in the forum is. Every post's message count and reactions are
recorded at 24h and 72h so the thresholds can be tuned.

Everything that can be is a pure function (parsing, scoring, matching, caps, the decision) so it can be tested
without Discord or the network. State: data/json/newsfeed_state.json and data/json/newsfeed_engagement.json.
"""

import asyncio
import email.utils
import html
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import pytz

import config
from lib.core.file_operations import atomic_write_json, load_json_file

logger = logging.getLogger(__name__)

UK_TZ = pytz.timezone("Europe/London")
USER_AGENT = "Mozilla/5.0 (compatible; HMSVictoryBot/1.0; news forum)"
FETCH_TIMEOUT = 20            # seconds per feed
MAX_FEED_BYTES = 3 * 1024 * 1024
MAX_JUDGE_PER_RUN = 6         # AI calls per poll at most
MEGATHREAD_TAG = "Megathread"
SPORT_TAG = "Sport"
TITLE_LIMIT = 100             # Discord's thread name limit
STORY_TTL = 3 * 86400         # forget a story this long after it was last seen in any feed
POSTED_TTL = 30 * 86400       # remember posted URLs this long (dedupe)
OTHERS_TTL = 86400            # other outlets' headlines count for cross-checking this long
THREAD_TTL = 7 * 86400
FOOTER = "-# Posted automatically from {source}. Comments here are about this story; something new? Start a new post."

_lock = asyncio.Lock()


# ---------------------------------------------------------------- parsing

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: Optional[str]) -> str:
    """Plain text from a feed description that may hold HTML (the Guardian's do) or entities."""
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def normalise_url(url: Optional[str]) -> str:
    """One key per article: https, lower-case host, no query (BBC adds at_medium tracking), no fragment (BBC guids end
    in #1), no trailing slash, and bbc.com folded into bbc.co.uk."""
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.netloc or "").lower()
    if host.startswith("www.bbc.com") or host == "bbc.com":
        host = "www.bbc.co.uk"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, "", ""))


def parse_date(text: Optional[str]) -> Optional[float]:
    """Epoch seconds from an RSS (RFC 822) or Atom (ISO 8601) date, or None."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(el, name: str) -> str:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


def parse_feed(data) -> List[Dict[str, Any]]:
    """Items from an RSS 2.0 or Atom feed: [{title, summary, link, url, guid, published}]. Bad XML gives []."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not data or len(data) > MAX_FEED_BYTES:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    items = []
    for el in root.iter():
        kind = _local(el.tag)
        if kind == "item":
            link = _child_text(el, "link")
            guid = _child_text(el, "guid")
            if not link and guid.startswith("http"):
                link = guid
            published = parse_date(_child_text(el, "pubDate") or _child_text(el, "date"))
            summary = _child_text(el, "description")
        elif kind == "entry":
            link = ""
            for child in el:
                if _local(child.tag) == "link" and child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                    break
            guid = _child_text(el, "id")
            published = parse_date(_child_text(el, "published") or _child_text(el, "updated"))
            summary = _child_text(el, "summary") or _child_text(el, "content")
        else:
            continue
        title = strip_html(_child_text(el, "title"))
        if not title or not link:
            continue
        items.append({
            "title": title,
            "summary": strip_html(summary)[:500],
            "link": link.strip(),
            "url": normalise_url(link),
            "guid": normalise_url(guid) if guid.startswith("http") else guid,
            "published": published,
        })
    return items


def is_live_url(url: str) -> bool:
    """BBC live pages (/news/live/...) mark major ongoing events."""
    return "/news/live/" in (url or "")


_NOT_POSTABLE = ("/videos/", "/av/", "/sounds/", "/iplayer/", "/programmes/", "/reel/", "/newsbeat/", "/news/in_pictures",
                 "/news/resources/")


def is_postable_url(url: str) -> bool:
    """BBC articles and live pages only: no video, audio, iPlayer or picture galleries."""
    parts = urlsplit(url or "")
    if not parts.netloc.endswith("bbc.co.uk"):
        return False
    if not (parts.path.startswith("/news/") or parts.path.startswith("/sport/")):
        return False
    return not any(bit in parts.path for bit in _NOT_POSTABLE)


def is_sport_url(url: str) -> bool:
    return urlsplit(url or "").path.startswith("/sport/")


# ---------------------------------------------------------------- matching

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be been before being below between both but by can
could did do does doing down during each few for from further had has have having he her here hers him his how i if in
into is it its itself just me more most my new no nor not now of off on once only or other our out over own s same says
said she should so some such than that the their them then there these they this those through to too under until up
very was we were what when where which while who whom why will with would you your yet so far amid over after us uk
how what why who year years day days week weeks live latest update updates news bbc first last back know get gets got
make makes made take takes told tells call calls one two three four five six seven eight nine ten
""".split())
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def tokens(text: str) -> set:
    """Significant words: lower-case, no stopwords, no short words, a plain plural 's' folded."""
    out = set()
    for word in _WORD_RE.findall((text or "").lower().replace("’", "'")):
        word = word.split("'")[0]
        if len(word) < 3 or word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return out


def overlap(a: str, b: str, min_shared: int = 2) -> float:
    """Share of the shorter text's significant words that the other also has (0 when fewer than min_shared match)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    if shared < min_shared:
        return 0.0
    return shared / min(len(ta), len(tb))


def same_story_heuristic(title_a: str, title_b: str, summary_a: str = "", summary_b: str = "",
                         threshold: Optional[float] = None) -> bool:
    """Cheap cross-outlet check: the headlines share most of their words, or headline plus summary share a lot."""
    threshold = config.NEWSFEED_CROSS_MATCH_MIN if threshold is None else threshold
    if overlap(title_a, title_b) >= threshold:
        return True
    if summary_a and summary_b:
        full_a, full_b = f"{title_a} {summary_a}", f"{title_b} {summary_b}"
        return overlap(full_a, full_b, min_shared=4) >= threshold * 0.8
    return False


def cross_outlets(entry: Dict[str, Any], others: Dict[str, Dict[str, Any]], threshold: Optional[float] = None) -> set:
    """Which other outlets (sky, guardian) are running this BBC story."""
    found = set()
    for other in others.values():
        outlet = other.get("outlet")
        if not outlet or outlet in found:
            continue
        if same_story_heuristic(entry.get("title", ""), other.get("title", ""), entry.get("summary", ""),
                                other.get("summary", ""), threshold):
            found.add(outlet)
    return found


def candidate_threads(entry: Dict[str, Any], threads: Iterable[Dict[str, Any]], threshold: Optional[float] = None,
                      limit: int = 4) -> List[Dict[str, Any]]:
    """Open posts that look close enough to show the AI as possible 'same story' matches, best first."""
    threshold = config.NEWSFEED_THREAD_PREFILTER_MIN if threshold is None else threshold
    text = f"{entry.get('title', '')} {entry.get('summary', '')}"
    scored = []
    for thread in threads:
        other = f"{thread.get('title', '')} {thread.get('summary', '')}"
        score = max(overlap(entry.get("title", ""), thread.get("title", "")), overlap(text, other, min_shared=3))
        if score >= threshold:
            scored.append((score, thread))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [thread for _, thread in scored[:limit]]


# ---------------------------------------------------------------- tracking and scoring

def empty_state() -> Dict[str, Any]:
    return {
        "stories": {},       # BBC url -> tracking + verdict
        "others": {},        # other outlets' url -> {outlet, title, summary, last_seen}
        "posted": {},        # url (and guid) -> {ts, thread_id, kind}: dedupe
        "threads": [],       # posts Vic opened: {id, title, summary, url, tag, created, live, last_update, updates, pinned}
        "daily": {},         # UK day -> new posts
        "daily_updates": {},
        "daily_dry": {},
        "daily_dry_updates": {},
        "feed_health": {},   # feed name -> {failures, until, ok}
        "member_posts": {},  # members' post id -> {summary, urls, fetched, created}: their opening message, cached
        "member_updates": {},  # members' post id -> when Vic last added an update there
        "paused": False,
        "dry_run": None,     # None = follow config.NEWSFEED_DRY_RUN
        "last_run": None,
        "last_error": None,
    }


def load_state() -> Dict[str, Any]:
    try:
        state = load_json_file(config.NEWSFEED_STATE_FILE) or {}
    except Exception:
        logger.exception("[NEWSFEED] could not read %s; starting empty", config.NEWSFEED_STATE_FILE)
        state = {}
    for key, value in empty_state().items():
        state.setdefault(key, value)
    return state


def save_state(state: Dict[str, Any]) -> None:
    atomic_write_json(config.NEWSFEED_STATE_FILE, state, indent=1)


def is_dry_run(state: Dict[str, Any]) -> bool:
    return bool(config.NEWSFEED_DRY_RUN if state.get("dry_run") is None else state["dry_run"])


def observe(state: Dict[str, Any], results: Dict[str, List[Dict[str, Any]]], feeds: Dict[str, Tuple[str, str, str]],
            now: float) -> None:
    """Fold one poll's feed items into the state: BBC stories are tracked, other outlets kept for cross-checking."""
    stories, others = state["stories"], state["others"]
    for name, items in results.items():
        _, outlet, kind = feeds[name]
        for item in items:
            url = item["url"]
            if not url:
                continue
            if outlet != "bbc":
                others[url] = {"outlet": outlet, "title": item["title"], "summary": item["summary"][:300],
                               "last_seen": now}
                continue
            entry = stories.setdefault(url, {"first_seen": now, "feeds": []})
            entry.update({"title": item["title"], "summary": item["summary"], "link": item["link"],
                          "guid": item.get("guid") or "", "last_seen": now})
            if item.get("published"):
                entry["published"] = item["published"]
            if name not in entry["feeds"]:
                entry["feeds"].append(name)
            if kind == "top":
                entry.setdefault("top_first", now)
                entry["top_last"] = now


def prune(state: Dict[str, Any], now: float) -> None:
    state["stories"] = {u: e for u, e in state["stories"].items() if now - e.get("last_seen", now) < STORY_TTL}
    state["others"] = {u: e for u, e in state["others"].items() if now - e.get("last_seen", now) < OTHERS_TTL}
    state["posted"] = {u: e for u, e in state["posted"].items() if now - e.get("ts", now) < POSTED_TTL}
    state["threads"] = [t for t in state["threads"] if now - t.get("created", now) < THREAD_TTL]
    cutoff = uk_day(now - 14 * 86400)
    for key in ("daily", "daily_updates", "daily_dry", "daily_dry_updates"):
        state[key] = {d: n for d, n in state[key].items() if d >= cutoff}


def in_top_now(entry: Dict[str, Any], now: float, poll_seconds: float) -> bool:
    """Still in BBC top stories: seen there on this poll (or the last, allowing for a slow fetch)."""
    top_last = entry.get("top_last")
    return top_last is not None and now - top_last <= poll_seconds * 1.6


def hours_in_top(entry: Dict[str, Any]) -> float:
    if entry.get("top_first") is None or entry.get("top_last") is None:
        return 0.0
    return max(0.0, (entry["top_last"] - entry["top_first"]) / 3600)


def signal_score(entry: Dict[str, Any], now: float, outlets: set, poll_seconds: float) -> Tuple[int, List[str]]:
    """The pre-AI importance signal and the reasons for it (see the module docstring for the weights)."""
    score, reasons = 0, []
    if in_top_now(entry, now, poll_seconds):
        score += 2
        reasons.append("BBC top")
    hours = int(hours_in_top(entry))
    if hours:
        score += min(hours, 4)
        reasons.append(f"{hours}h in top")
    if is_live_url(entry.get("link") or entry.get("url") or ""):
        score += 3
        reasons.append("live page")
    if outlets:
        score += min(2 * len(outlets), 4)
        reasons.append("also " + ", ".join(sorted(outlets)))
    if len(set(entry.get("feeds") or [])) >= 3:
        score += 1
        reasons.append(f"{len(set(entry['feeds']))} BBC feeds")
    return score, reasons


def is_fresh(entry: Dict[str, Any], now: float, max_age_hours: Optional[float] = None) -> bool:
    max_age_hours = config.NEWSFEED_MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    published = entry.get("published") or entry.get("first_seen") or now
    return now - published <= max_age_hours * 3600


def already_posted(state: Dict[str, Any], url: str, guid: str = "") -> bool:
    posted = state.get("posted") or {}
    return bool((url and url in posted) or (guid and guid in posted))


def mark_posted(state: Dict[str, Any], entry_url: str, guid: str, now: float, thread_id, kind: str) -> None:
    record = {"ts": now, "thread_id": str(thread_id) if thread_id else None, "kind": kind}
    state["posted"][entry_url] = record
    if guid and guid != entry_url:
        state["posted"][guid] = record


def uk_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, UK_TZ).strftime("%Y-%m-%d")


def cap_allows(state: Dict[str, Any], key: str, now: float, cap: int) -> bool:
    return int(state.get(key, {}).get(uk_day(now), 0)) < cap


def bump_cap(state: Dict[str, Any], key: str, now: float) -> None:
    day = uk_day(now)
    state.setdefault(key, {})[day] = int(state[key].get(day, 0)) + 1


def backoff_seconds(failures: int, base: float, cap: float = 7200) -> float:
    """How long to leave a failing feed alone: retried on the next poll after one failure, then doubling to a cap."""
    if failures <= 1:
        return 0.0
    return min(cap, base * 2 ** (failures - 2))


# ---------------------------------------------------------------- text

_MENTION_RE = re.compile(r"<@[!&]?\d+>|<#\d+>|@(everyone|here)", re.I)


def clean_text(text: Optional[str], limit: int) -> str:
    """AI text made safe to post: one line, no pings or mentions, capped."""
    text = _MENTION_RE.sub("", str(text or ""))
    text = _WS_RE.sub(" ", text).strip().strip('"').strip()
    if len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:-") + "…"
    return text


def trim_title(title: str, limit: int = TITLE_LIMIT) -> str:
    """A forum post title within Discord's 100 characters, cut at a word with an ellipsis when it's too long."""
    title = _WS_RE.sub(" ", (title or "").strip()) or "News"
    if len(title) <= limit:
        return title
    cut = title[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(",;:-–— ") + "…"


def build_post_body(link: str, why: str = "", question: str = "", source: str = "BBC News") -> str:
    parts = [link]
    if why:
        parts.append(why)
    if question:
        parts.append(f"**{question}**")
    parts.append(FOOTER.format(source=source))
    return "\n\n".join(parts)


def build_update_body(headline: str, link: str, why: str = "") -> str:
    lines = [f"**Update:** {clean_text(headline, 200)}", link]
    if why:
        lines.append(f"-# {why}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the AI and the decision

JUDGE_PROMPT = """You choose and frame news stories for the news forum of a large British Discord server. Members are \
from all over the UK, mostly young adults, and most interested in UK politics, the economy and major world events. \
Each story the forum takes gets one post where members discuss it, so only stories people will want to talk about \
should get one.

Judge the one story you are given and reply as JSON.

- "importance" (1-10): how much this server would want a discussion post on it today.
  9-10: a major national or world event (a general election result, a prime minister resigning, a budget, a major \
attack or disaster, a war starting).
  7-8: significant UK politics, policy or economy news, or a major world story many people will be discussing.
  4-6: an ordinary news story.
  1-3: trivia, lifestyle, celebrity, human interest, consumer advice, a local incident, a sport result, an \
explainer, analysis or opinion piece, a quiz, or a video or audio item.
- "newsworthy": true only if it is a real development worth discussing now. A "what we know" round-up counts only \
when the event itself is major.
- "tag": exactly one name from "allowed_tags". Sport only for sport stories.
- "same_story_post": if one of "open_posts" is about the same developing story (the same event or the same specific \
ongoing issue, where this article is a new development or follow-up on it), give that post's "id"; otherwise null. \
Two different stories on the same broad subject (two separate stories about one party, say) are NOT the same story.
- "why": one or two short sentences (at most 220 characters) on why it matters, for someone who has not read it.
- "question": one open, balanced discussion question (at most 120 characters) that does not presume an answer, or \
"" if none fits.
- "reason": a few words on your call.

"why" and "question" must be strictly neutral and factual, to the standard of BBC impartiality: no opinions, no \
loaded or emotive words, no jokes, no persona, and no taking sides on parties, politicians, countries or groups. \
Nothing that promotes non-British nationalism or disparages Britain or the British. No @mentions, no emojis, no \
hashtags. Say plainly what happened and what it directly affects or decides; avoid commentary words such as \
"highlights", "tensions", "concerns", "landmark", "end of an era" or "controversial".

Reply with JSON only: {"importance": int, "newsworthy": bool, "tag": str, "same_story_post": str or null, \
"why": str, "question": str, "reason": str}"""


def judge_payload(entry: Dict[str, Any], signal: int, reasons: List[str], threads: List[Dict[str, Any]],
                  tag_names: List[str]) -> str:
    return json.dumps({
        "headline": entry.get("title", ""),
        "summary": entry.get("summary", ""),
        "url": entry.get("link") or entry.get("url", ""),
        "live_page": is_live_url(entry.get("link") or entry.get("url") or ""),
        "signals": {"score": signal, "reasons": reasons},
        "open_posts": [{"id": str(t["id"]), "title": t.get("title", ""), "summary": (t.get("summary") or "")[:300]}
                       for t in threads],
        "allowed_tags": tag_names,
    }, ensure_ascii=False)


_COMMENTARY_RE = re.compile(r",?\s+(?:highlighting|underscoring|underlining|signalling|signaling|raising concerns|"
                            r"sparking concerns|marking the end of an era)\b[^.]*", re.I)


def strip_commentary(text: Optional[str]) -> str:
    """Drop a trailing editorial clause the AI sometimes adds anyway (", highlighting ongoing tensions")."""
    text = _COMMENTARY_RE.sub("", str(text or "")).strip()
    return text if not text or text.endswith((".", "?", "!")) else text + "."


def normalise_verdict(raw: Any, tag_names: List[str], thread_ids: Iterable[str]) -> Dict[str, Any]:
    """The AI's answer, checked: importance clamped 1-10, a tag from the forum's list, a match only to a post it was
    shown, and the text cleaned. Anything unusable comes back as an error verdict that never posts."""
    if not isinstance(raw, dict):
        return {"error": "bad reply", "importance": 0, "newsworthy": False}
    try:
        importance = int(round(float(raw.get("importance", 0))))
    except (TypeError, ValueError):
        importance = 0
    importance = max(0, min(10, importance))
    tag = raw.get("tag")
    lookup = {t.lower(): t for t in tag_names}
    tag = lookup.get(str(tag or "").strip().lower())
    match = raw.get("same_story_post")
    match = str(match).strip() if match not in (None, "", "null", False) else None
    if match is not None and match not in {str(t) for t in thread_ids}:
        match = None
    newsworthy = raw.get("newsworthy")
    if isinstance(newsworthy, str):
        newsworthy = newsworthy.strip().lower() in ("true", "yes", "1")
    return {
        "importance": importance,
        "newsworthy": bool(newsworthy),
        "tag": tag,
        "same_story_thread": match,
        "why": clean_text(strip_commentary(raw.get("why")), 240),
        "question": clean_text(raw.get("question"), 130),
        "reason": clean_text(raw.get("reason"), 120),
    }


def _openai_complete(system: str, payload: str) -> Tuple[Dict[str, Any], int, int]:
    """The chat responder's JSON-mode OpenAI call, with its retries and its key."""
    from lib.features.chat_responder import _chat_completion_json
    api_key = os.getenv("OPENAI_TOKEN")
    if not api_key:
        raise RuntimeError("OPENAI_TOKEN is not set")
    return _chat_completion_json(system, payload, api_key, model=config.NEWSFEED_MODEL, max_tokens=400,
                                 temperature=0.2, timeout=40, what="Newsfeed judgement")


def _record_usage(prompt_tokens: int, completion_tokens: int) -> None:
    try:
        from lib.features.chat_responder import live_chat_manager
        live_chat_manager.record_usage(config.NEWSFEED_MODEL, prompt_tokens, completion_tokens)
    except Exception:
        logger.debug("[NEWSFEED] could not record AI usage", exc_info=True)


async def judge_story(entry: Dict[str, Any], signal: int, reasons: List[str], threads: List[Dict[str, Any]],
                      tag_names: List[str], complete: Optional[Callable] = None) -> Dict[str, Any]:
    """Ask the AI about one story. `complete(system, payload) -> (json, prompt_tokens, completion_tokens)` can be
    swapped out (tests); by default it is the chat responder's OpenAI client."""
    payload = judge_payload(entry, signal, reasons, threads, tag_names)
    try:
        if complete is None:
            raw, p_tok, c_tok = await asyncio.to_thread(_openai_complete, JUDGE_PROMPT, payload)
            _record_usage(p_tok, c_tok)
        else:
            result = complete(JUDGE_PROMPT, payload)
            if asyncio.iscoroutine(result):
                result = await result
            raw = result[0] if isinstance(result, tuple) else result
    except Exception as e:
        logger.warning("[NEWSFEED] judgement failed for %s: %s", entry.get("url"), e)
        return {"error": str(e)[:200], "importance": 0, "newsworthy": False}
    return normalise_verdict(raw, tag_names, [t["id"] for t in threads])


class Thresholds:
    """The tunable numbers, read from config each poll so a config change and restart is all tuning takes."""

    def __init__(self, **overrides):
        self.min_signal = overrides.get("min_signal", config.NEWSFEED_MIN_SIGNAL)
        self.update_min_signal = overrides.get("update_min_signal", config.NEWSFEED_UPDATE_MIN_SIGNAL)
        self.min_importance = overrides.get("min_importance", config.NEWSFEED_MIN_IMPORTANCE)
        self.update_min_importance = overrides.get("update_min_importance", config.NEWSFEED_UPDATE_MIN_IMPORTANCE)
        self.sport_min_importance = overrides.get("sport_min_importance", config.NEWSFEED_SPORT_MIN_IMPORTANCE)
        self.max_judgements = overrides.get("max_judgements", config.NEWSFEED_MAX_JUDGEMENTS)


def decide(signal: int, verdict: Dict[str, Any], th: Thresholds, sport: bool = False) -> Tuple[str, str]:
    """('new' | 'update' | 'skip', why) for a judged story. Caps and gaps are checked by the caller."""
    if not verdict or verdict.get("error"):
        return "skip", "no usable judgement"
    importance = int(verdict.get("importance") or 0)
    if verdict.get("same_story_thread"):
        if signal >= th.update_min_signal and importance >= th.update_min_importance:
            return "update", f"same story, importance {importance}"
        return "skip", f"same story but too minor for an update ({importance})"
    if signal < th.min_signal:
        return "skip", f"signal {signal} under {th.min_signal}"
    if not verdict.get("newsworthy"):
        return "skip", "not newsworthy: " + (verdict.get("reason") or "")
    needed = th.sport_min_importance if (sport or verdict.get("tag") == SPORT_TAG) else th.min_importance
    if importance < needed:
        return "skip", f"importance {importance} under {needed}"
    return "new", f"importance {importance}"


def needs_judgement(entry: Dict[str, Any], signal: int, shown_threads: List[Dict[str, Any]], th: Thresholds) -> bool:
    """Ask the AI (again)? Yes when never judged, when an open post it hasn't been compared with now looks close,
    or when a borderline story's signal has climbed by 2 or more since, up to max_judgements calls."""
    verdict = entry.get("verdict")
    if not verdict:
        return True
    count = int(entry.get("judgements", 1))
    if count >= th.max_judgements:
        return False
    seen = set(entry.get("judged_threads") or [])
    if any(str(t["id"]) not in seen for t in shown_threads):
        return True
    if verdict.get("error"):
        return signal >= entry.get("judged_signal", 0) + 1
    borderline = int(verdict.get("importance") or 0) >= th.min_importance - 2
    return borderline and signal >= entry.get("judged_signal", 0) + 2


def thread_gap_ok(thread: Dict[str, Any], now: float, gap_minutes: Optional[float] = None) -> bool:
    gap_minutes = config.NEWSFEED_UPDATE_MIN_GAP_MINUTES if gap_minutes is None else gap_minutes
    last = thread.get("last_update") or thread.get("created") or 0
    return now - last >= gap_minutes * 60


# ---------------------------------------------------------------- engagement

def load_engagement() -> Dict[str, Any]:
    try:
        return load_json_file(config.NEWSFEED_ENGAGEMENT_FILE) or {}
    except Exception:
        logger.exception("[NEWSFEED] could not read %s", config.NEWSFEED_ENGAGEMENT_FILE)
        return {}


def due_snapshots(engagement: Dict[str, Any], now: float) -> List[Tuple[str, str]]:
    """(thread_id, 'h24' | 'h72') pairs whose snapshot time has come and that haven't been taken."""
    due = []
    for tid, rec in engagement.items():
        for label, hours in (("h24", 24), ("h72", 72)):
            if label not in rec and now >= rec.get("created", now) + hours * 3600:
                due.append((tid, label))
    return due


# ---------------------------------------------------------------- Discord side

async def _log(client, text: str) -> None:
    logger.info("[NEWSFEED] %s", text)
    try:
        import discord
        channel = client.get_channel(config.NEWSFEED_LOG_CHANNEL_ID) if client else None
        if channel:
            await channel.send(f"📰 newsfeed: {text}"[:1900], allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        logger.debug("[NEWSFEED] log channel send failed", exc_info=True)


async def fetch_feeds(state: Dict[str, Any], now: float, feeds: Optional[Dict[str, Tuple[str, str, str]]] = None
                      ) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch every feed not in backoff, concurrently, each with its own timeout. A dead feed is skipped, logged and
    left alone for longer each time; it never raises."""
    import aiohttp
    feeds = feeds or config.NEWSFEED_FEEDS
    health = state.setdefault("feed_health", {})
    base = config.NEWSFEED_POLL_MINUTES * 60
    due = {n: f for n, f in feeds.items() if float(health.get(n, {}).get("until", 0)) <= now}
    results: Dict[str, List[Dict[str, Any]]] = {}

    async def one(session, name, url):
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.content.read(MAX_FEED_BYTES + 1)
            items = parse_feed(data)
            if not items:
                raise RuntimeError("no items parsed")
            results[name] = items
            health[name] = {"failures": 0, "until": 0, "ok": now}
        except Exception as e:
            rec = health.setdefault(name, {})
            rec["failures"] = int(rec.get("failures", 0)) + 1
            rec["until"] = now + backoff_seconds(rec["failures"], base)
            rec["error"] = str(e)[:120] or type(e).__name__
            logger.warning("[NEWSFEED] feed %s failed (%d in a row): %s", name, rec["failures"], rec["error"])

    timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
        await asyncio.gather(*(one(session, n, f[0]) for n, f in due.items()))
    return results


def _thread_created(thread) -> float:
    created = getattr(thread, "created_at", None)
    if created is None:
        import discord
        created = discord.utils.snowflake_time(thread.id)
    return created.timestamp()


async def _get_thread(client, thread_id):
    import discord
    thread = client.get_channel(int(thread_id))
    if thread is None:
        try:
            thread = await client.fetch_channel(int(thread_id))
        except (discord.NotFound, discord.Forbidden):
            return None
        except discord.HTTPException:
            return None
    return thread


_URL_RE = re.compile(r"https?://[^\s<>()\[\]]+")


def urls_in(text: str) -> set:
    """Every link in a message, normalised the same way story URLs are."""
    return {normalise_url(u.rstrip(".,!?;:'\"*_~|")) for u in _URL_RE.findall(text or "")}


def thread_linking(url: str, threads: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The open post (anyone's) that already links this article, if one does."""
    for thread in threads:
        if url and url in set(thread.get("urls") or ()):
            return thread
    return None


async def _member_post_text(client, state: Dict[str, Any], thread, now: float) -> Dict[str, Any]:
    """A member's post's opening message (cached in the state): its text for matching and the links it carries."""
    cache = state.setdefault("member_posts", {})
    tid = str(thread.id)
    cached = cache.get(tid)
    if cached and now - cached.get("fetched", 0) < 6 * 3600:
        return cached
    text = ""
    try:
        starter = getattr(thread, "starter_message", None) or await thread.fetch_message(thread.id)
        text = starter.content or ""
        for embed in getattr(starter, "embeds", []) or []:
            text += f" {embed.url or ''} {embed.title or ''} {embed.description or ''}"
    except Exception:
        logger.debug("[NEWSFEED] could not read the opening message of %s", tid, exc_info=True)
    cached = {"summary": strip_html(_URL_RE.sub(" ", text))[:400], "urls": sorted(urls_in(text)), "fetched": now,
              "created": _thread_created(thread)}
    cache[tid] = cached
    return cached


async def build_thread_pool(client, forum, state: Dict[str, Any], now: float) -> List[Dict[str, Any]]:
    """Every open post in the forum from the last NEWSFEED_MATCH_DAYS, whoever made it: members' posts (active or
    archived, with their opening message and links) and Vic's own (with the summary it posted). Locked posts are left
    out, since a mod locking a post means it's done."""
    horizon = now - config.NEWSFEED_MATCH_DAYS * 86400
    pool: Dict[str, Dict[str, Any]] = {}
    own_ids = {str(t["id"]) for t in state["threads"]}
    forum_threads = list(getattr(forum, "threads", []) or [])
    try:
        async for thread in forum.archived_threads(limit=100):
            if _thread_created(thread) >= horizon:  # listed by archive time, not creation, so no early stop
                forum_threads.append(thread)
    except Exception:
        logger.debug("[NEWSFEED] could not list archived posts", exc_info=True)
    for thread in forum_threads:
        try:
            if thread.locked or _thread_created(thread) < horizon or str(thread.id) in own_ids:
                continue
        except Exception:
            continue
        info = await _member_post_text(client, state, thread, now)
        pool[str(thread.id)] = {"id": str(thread.id), "title": thread.name, "summary": info["summary"],
                                "urls": info["urls"], "own": False, "created": _thread_created(thread),
                                "last_update": state.get("member_updates", {}).get(str(thread.id))}
    state["member_posts"] = {k: v for k, v in state.get("member_posts", {}).items()
                             if v.get("created", now) >= horizon}
    state["member_updates"] = {k: v for k, v in state.get("member_updates", {}).items() if now - v < THREAD_TTL}
    dry = is_dry_run(state)
    for rec in state["threads"]:
        if rec.get("created", 0) < horizon:
            continue
        if rec.get("dry"):
            if dry:  # a dry run's pretend posts still catch their own follow-ups, so it doesn't "post" them twice
                pool[str(rec["id"])] = {"id": str(rec["id"]), "title": rec.get("title", ""), "own": True,
                                        "summary": rec.get("summary", ""), "created": rec.get("created", now),
                                        "last_update": rec.get("last_update"), "urls": rec.get("urls") or [rec.get("url")]}
            continue
        thread = await _get_thread(client, rec["id"])
        if thread is None or getattr(thread, "locked", False):
            pool.pop(str(rec["id"]), None)
            continue
        pool[str(rec["id"])] = {"id": str(rec["id"]), "title": rec.get("title", ""), "summary": rec.get("summary", ""),
                                "own": True, "created": rec.get("created", now),
                                "last_update": rec.get("last_update"), "urls": rec.get("urls") or [rec.get("url")]}
    return list(pool.values())


def _forum_tags(forum, names: List[str]):
    by_name = {t.name.lower(): t for t in getattr(forum, "available_tags", [])}
    tags = []
    for name in names:
        tag = by_name.get((name or "").lower())
        if tag and tag not in tags:
            tags.append(tag)
    if not any(t.name != MEGATHREAD_TAG for t in tags):
        # The forum requires a tag; never fail a post over a tag the AI got wrong.
        fallback = next((t for t in getattr(forum, "available_tags", []) if t.name != MEGATHREAD_TAG), None)
        if fallback:
            tags.insert(0, fallback)
    return tags[:5]


async def _create_post(client, forum, state, entry, verdict, signal, reasons, now, dry: bool) -> Optional[Dict[str, Any]]:
    live = is_live_url(entry.get("link") or entry["url"])
    title = trim_title(entry["title"])
    body = build_post_body(entry["link"], verdict.get("why", ""), verdict.get("question", ""))
    tag_names = [verdict.get("tag") or ""] + ([MEGATHREAD_TAG] if live else [])
    detail = (f"\"{title}\" <{entry['link']}> (signal {signal}: {', '.join(reasons)}; AI {verdict.get('importance')}/10, "
              f"{verdict.get('tag')}{', megathread' if live else ''})")
    if dry:
        await _log(client, f"[dry run] would post {detail}")
        rec = {"id": f"dry-{int(now)}-{len(state['threads'])}", "dry": True}
    else:
        tags = _forum_tags(forum, tag_names)
        created = await forum.create_thread(name=title, content=body, applied_tags=tags, reason="News auto-poster")
        thread = created.thread
        rec = {"id": str(thread.id)}
        if live and config.NEWSFEED_PIN_MEGATHREADS:
            try:
                if not any(getattr(t.flags, "pinned", False) for t in forum.threads if t.id != thread.id):
                    await thread.edit(pinned=True, reason="News auto-poster: live megathread")
                    rec["pinned"] = True
            except Exception:
                logger.warning("[NEWSFEED] could not pin megathread %s", thread.id, exc_info=True)
        await _log(client, f"posted <#{thread.id}> {detail}")
        engagement = load_engagement()
        engagement[rec["id"]] = {"title": title, "url": entry["url"], "tag": verdict.get("tag"), "live": live,
                                 "created": now, "signal": signal, "importance": verdict.get("importance")}
        atomic_write_json(config.NEWSFEED_ENGAGEMENT_FILE, engagement, indent=1)
    rec.update({"title": title, "summary": entry.get("summary", ""), "url": entry["url"], "tag": verdict.get("tag"),
                "created": now, "live": live, "last_update": None, "updates": 0})
    state["threads"].append(rec)
    mark_posted(state, entry["url"], entry.get("guid", ""), now, rec["id"], "dry" if dry else "new")
    bump_cap(state, "daily_dry" if dry else "daily", now)
    return {"id": rec["id"], "title": title, "summary": entry.get("summary", ""), "own": True, "created": now,
            "last_update": None, "urls": [entry["url"]]}


async def _post_update(client, state, entry, verdict, target, now, dry: bool) -> bool:
    body = build_update_body(entry["title"], entry["link"], verdict.get("why", ""))
    tid = target["id"]
    detail = f"\"{entry['title']}\" <{entry['link']}> (AI {verdict.get('importance')}/10)"
    if dry or str(tid).startswith("dry-"):
        await _log(client, f"[dry run] would add an update to {target.get('title')!r}: {detail}")
    else:
        thread = await _get_thread(client, tid)
        if thread is None or getattr(thread, "locked", False):
            return False
        if getattr(thread, "archived", False) and target.get("own"):
            # Only Vic's own posts are ever edited. A member's archived post is left alone: replying in an unlocked
            # thread reopens it by itself, and its title, tags and settings are never touched.
            await thread.edit(archived=False, reason="News auto-poster: update")
        import discord
        await thread.send(body, allowed_mentions=discord.AllowedMentions.none())
        whose = "" if target.get("own") else " (a member's post)"
        await _log(client, f"update in <#{tid}>{whose} {detail}")
    for rec in state["threads"]:
        if str(rec["id"]) == str(tid):
            rec["last_update"] = now
            rec["updates"] = int(rec.get("updates", 0)) + 1
            rec.setdefault("urls", [rec.get("url")]).append(entry["url"])
    if not target.get("own"):
        state.setdefault("member_updates", {})[str(tid)] = now
    target["last_update"] = now
    target.setdefault("urls", []).append(entry["url"])
    mark_posted(state, entry["url"], entry.get("guid", ""), now, tid, "dry-update" if dry else "update")
    bump_cap(state, "daily_dry_updates" if dry else "daily_updates", now)
    return True


async def take_snapshots(client, state: Dict[str, Any], now: float) -> int:
    """Record message count and reactions for posts that have reached 24h or 72h."""
    engagement = load_engagement()
    due = due_snapshots(engagement, now)
    if not due:
        return 0
    updates_by_thread = {str(t["id"]): int(t.get("updates", 0)) for t in state["threads"]}
    for tid, label in due:
        thread = await _get_thread(client, tid)
        if thread is None:
            engagement[tid][label] = {"at": now, "missing": True}
            continue
        reactions = 0
        try:
            starter = await thread.fetch_message(thread.id)
            reactions = sum(r.count for r in starter.reactions)
        except Exception:
            pass
        engagement[tid][label] = {
            "at": now,
            "messages": getattr(thread, "message_count", None),
            "total_sent": getattr(thread, "total_message_sent", None),
            "members": getattr(thread, "member_count", None),
            "reactions": reactions,
            "bot_updates": updates_by_thread.get(tid, 0),
            "locked": getattr(thread, "locked", None),
        }
        logger.info("[NEWSFEED] engagement %s %s: %s", label, tid, engagement[tid][label])
    atomic_write_json(config.NEWSFEED_ENGAGEMENT_FILE, engagement, indent=1)
    return len(due)


async def unpin_stale_megathreads(client, state: Dict[str, Any], now: float) -> None:
    for rec in state["threads"]:
        if not rec.get("pinned") or rec.get("dry"):
            continue
        story = state["stories"].get(rec.get("url"), {})
        last_top = story.get("top_last") or rec.get("created", now)
        if now - last_top < config.NEWSFEED_UNPIN_AFTER_HOURS * 3600:
            continue
        thread = await _get_thread(client, rec["id"])
        try:
            if thread is not None and getattr(thread.flags, "pinned", False):
                await thread.edit(pinned=False, reason="News auto-poster: live page has left BBC top stories")
                await _log(client, f"unpinned <#{rec['id']}> (live page off BBC top stories)")
        except Exception:
            logger.warning("[NEWSFEED] could not unpin %s", rec["id"], exc_info=True)
        rec["pinned"] = False


async def run_once(client, state: Dict[str, Any], now: Optional[float] = None, judge: Optional[Callable] = None
                   ) -> Dict[str, Any]:
    """One poll: fetch, track, judge the strong candidates, post or update. Returns a summary for the control panel's Run button."""
    now = now or time.time()
    poll_seconds = config.NEWSFEED_POLL_MINUTES * 60
    th = Thresholds()
    dry = is_dry_run(state)
    summary = {"feeds_ok": 0, "feeds_failed": 0, "candidates": 0, "judged": 0, "posted": [], "updated": [],
               "dry": dry}

    results = await fetch_feeds(state, now)
    summary["feeds_ok"] = len(results)
    summary["feeds_failed"] = sum(1 for n in config.NEWSFEED_FEEDS if n not in results)
    observe(state, results, config.NEWSFEED_FEEDS, now)
    prune(state, now)
    state["last_run"] = now

    try:
        await take_snapshots(client, state, now)
        await unpin_stale_megathreads(client, state, now)
    except Exception:
        logger.exception("[NEWSFEED] engagement/unpin pass failed")

    if "bbc_top" not in results and not any(n.startswith("bbc") for n in results):
        return summary  # nothing new from the BBC this poll

    forum = client.get_channel(config.NEWSFEED_FORUM_ID)
    if forum is None:
        await _log(client, f"forum {config.NEWSFEED_FORUM_ID} not found; nothing posted")
        return summary
    tag_names = [t.name for t in getattr(forum, "available_tags", []) if t.name != MEGATHREAD_TAG]

    candidates = []
    for url, entry in state["stories"].items():
        entry["url"] = url
        if entry.get("done") or already_posted(state, url, entry.get("guid", "")):
            continue
        if not is_postable_url(url) or not is_fresh(entry, now):
            continue
        outlets = cross_outlets(entry, state["others"])
        signal, reasons = signal_score(entry, now, outlets, poll_seconds)
        entry["signal"] = signal
        if signal >= min(th.min_signal, th.update_min_signal):
            candidates.append((signal, reasons, entry))
    candidates.sort(key=lambda c: c[0], reverse=True)
    summary["candidates"] = len(candidates)
    if not candidates:
        return summary

    pool = await build_thread_pool(client, forum, state, now)
    new_this_run, judged = 0, 0
    for signal, reasons, entry in candidates:
        linked = thread_linking(entry["url"], pool)
        if linked:
            # Someone (often a member) has already posted this very article: nothing to add, no duplicate.
            mark_posted(state, entry["url"], entry.get("guid", ""), now, linked["id"], "already linked")
            entry["done"] = True
            logger.info("[NEWSFEED] %r is already linked in post %s; skipped", entry["title"], linked["id"])
            continue
        shown = candidate_threads(entry, pool)
        if signal < th.min_signal and not shown and not (entry.get("verdict") or {}).get("same_story_thread"):
            continue  # only strong enough to be an update, and there's no open post it could update
        if needs_judgement(entry, signal, shown, th):
            if judged >= MAX_JUDGE_PER_RUN:
                continue
            judged += 1
            verdict = await judge_story(entry, signal, reasons, shown, tag_names, complete=judge)
            entry["verdict"] = verdict
            entry["judgements"] = int(entry.get("judgements", 0)) + 1
            entry["judged_signal"] = signal
            entry["judged_threads"] = sorted(set(entry.get("judged_threads") or []) | {str(t["id"]) for t in shown})
            logger.info("[NEWSFEED] judged %r: signal %d %s -> %s", entry["title"], signal, reasons, verdict)
        verdict = entry.get("verdict") or {}
        action, why = decide(signal, verdict, th, sport=is_sport_url(entry["url"]))
        target = None
        if action == "update":
            target = next((t for t in pool if str(t["id"]) == str(verdict.get("same_story_thread"))), None)
            if target is None:
                # The post it matched has gone (locked or deleted): judge it as a story of its own.
                action, why = decide(signal, dict(verdict, same_story_thread=None), th, sport=is_sport_url(entry["url"]))
        try:
            if action == "new":
                if new_this_run >= config.NEWSFEED_MAX_NEW_PER_RUN:
                    continue
                if not cap_allows(state, "daily_dry" if dry else "daily", now, config.NEWSFEED_DAILY_CAP):
                    continue
                made = await _create_post(client, forum, state, entry, verdict, signal, reasons, now, dry)
                if made:
                    pool.append(made)
                    new_this_run += 1
                    entry["done"] = True
                    summary["posted"].append(entry["title"])
            elif action == "update":
                if not thread_gap_ok(target, now):
                    continue
                if not cap_allows(state, "daily_dry_updates" if dry else "daily_updates", now, config.NEWSFEED_DAILY_UPDATE_CAP):
                    continue
                if await _post_update(client, state, entry, verdict, target, now, dry):
                    entry["done"] = True
                    summary["updated"].append(entry["title"])
        except Exception as e:
            logger.exception("[NEWSFEED] %s failed for %s", action, entry["url"])
            entry["failures"] = int(entry.get("failures", 0)) + 1
            if entry["failures"] >= 3:
                entry["done"] = True  # stop retrying (and logging) a story Discord keeps refusing
            await _log(client, f"{action} failed for <{entry['link']}> (try {entry['failures']}): "
                               f"{type(e).__name__}: {str(e)[:200]}")
    summary["judged"] = judged
    return summary


async def newsfeed_tick(client, manual: bool = False) -> Optional[Dict[str, Any]]:
    """The scheduled job. Never raises: a bad poll is logged and the next one tries again."""
    if not config.NEWSFEED_ENABLED:
        return None
    if _lock.locked():
        return None
    async with _lock:
        state = load_state()
        if state.get("paused") and not manual:
            return None
        try:
            summary = await run_once(client, state)
            state["last_error"] = None
            return summary
        except Exception as e:
            logger.exception("[NEWSFEED] poll failed")
            first = state.get("last_error") is None
            state["last_error"] = {"at": time.time(), "error": f"{type(e).__name__}: {str(e)[:200]}"}
            if first:
                await _log(client, f"poll failed: {state['last_error']['error']}")
            return None
        finally:
            try:
                save_state(state)
            except Exception:
                logger.exception("[NEWSFEED] could not save state")


# ---------------------------------------------------------------- control panel (was /newsfeed)

def status_text(state: Dict[str, Any], now: Optional[float] = None) -> str:
    now = now or time.time()
    if not config.NEWSFEED_ENABLED:
        running = "**off** (NEWSFEED_ENABLED is False in config)"
    elif state.get("paused"):
        running = "**paused** (Resume on the control panel starts it again)"
    else:
        running = "**running**"
    lines = [
        f"State: {running}",
        f"Mode: {'**dry run** (nothing is posted; decisions go to the log)' if is_dry_run(state) else '**live**'}",
        f"Forum: <#{config.NEWSFEED_FORUM_ID}>, every {config.NEWSFEED_POLL_MINUTES} min",
    ]
    if state.get("last_run"):
        lines.append(f"Last poll: <t:{int(state['last_run'])}:R>")
    day = uk_day(now)
    lines.append(f"Today: {state['daily'].get(day, 0)}/{config.NEWSFEED_DAILY_CAP} posts, "
                 f"{state['daily_updates'].get(day, 0)}/{config.NEWSFEED_DAILY_UPDATE_CAP} updates"
                 + (f", {state['daily_dry'].get(day, 0)} dry-run decisions" if state['daily_dry'].get(day) else ""))
    failing = {n: h for n, h in state.get("feed_health", {}).items() if h.get("failures")}
    if failing:
        lines.append("Failing feeds: " + ", ".join(f"{n} ({h['failures']}x: {h.get('error', '')})"
                                                   for n, h in sorted(failing.items())))
    if state.get("last_error"):
        lines.append(f"Last error: {state['last_error'].get('error')}")
    top = sorted((e for e in state["stories"].values() if not e.get("done")),
                 key=lambda e: e.get("signal", 0), reverse=True)[:5]
    if top:
        lines.append("\n**Strongest waiting**")
        for e in top:
            v = e.get("verdict") or {}
            ai = f", AI {v.get('importance')}/10" if v and not v.get("error") else ""
            lines.append(f"- {trim_title(e.get('title', ''), 80)} (signal {e.get('signal', 0)}{ai})")
    recent = [t for t in state["threads"] if not t.get("dry")][-5:]
    if recent:
        lines.append("\n**Recent posts**")
        for t in reversed(recent):
            lines.append(f"- <#{t['id']}> {t.get('updates', 0)} updates")
    lines.append(f"\n-# Thresholds: signal {config.NEWSFEED_MIN_SIGNAL} (updates {config.NEWSFEED_UPDATE_MIN_SIGNAL}), "
                 f"AI {config.NEWSFEED_MIN_IMPORTANCE}/10 (updates {config.NEWSFEED_UPDATE_MIN_IMPORTANCE}, sport "
                 f"{config.NEWSFEED_SPORT_MIN_IMPORTANCE}). Tracking {len(state['stories'])} BBC stories.")
    return "\n".join(lines)


async def handle_newsfeed_command(interaction, action: str) -> None:
    import discord
    client = interaction.client
    who = f"{interaction.user} ({interaction.user.id})"
    if action in ("pause", "resume", "dry-run", "live"):
        async with _lock:
            state = load_state()
            if action == "pause":
                state["paused"] = True
            elif action == "resume":
                state["paused"] = False
            elif action == "dry-run":
                state["dry_run"] = True
            else:
                state["dry_run"] = False
            save_state(state)
        await _log(client, f"{action} by {who}")
        note = ""
        if not config.NEWSFEED_ENABLED:
            note = "\n-# NEWSFEED_ENABLED is False in config, so it stays off until that's changed."
        await interaction.response.send_message(f"📰 News auto-poster: **{action}** done.{note}", ephemeral=True)
        return
    if action == "run":
        if not config.NEWSFEED_ENABLED:
            await interaction.response.send_message("📰 NEWSFEED_ENABLED is False in config; nothing to run.",
                                                    ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _log(client, f"manual poll by {who}")
        summary = await newsfeed_tick(client, manual=True)
        if summary is None:
            await interaction.followup.send("📰 A poll is already running, or it failed (see the logs).", ephemeral=True)
            return
        lines = [f"📰 Poll done{' (dry run)' if summary['dry'] else ''}: {summary['feeds_ok']} feeds read, "
                 f"{summary['feeds_failed']} skipped or failed, {summary['candidates']} candidates, "
                 f"{summary['judged']} judged."]
        lines += [f"- posted: {t}" for t in summary["posted"]] + [f"- update: {t}" for t in summary["updated"]]
        if not summary["posted"] and not summary["updated"]:
            lines.append("Nothing strong enough this time.")
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)
        return
    embed = discord.Embed(title="📰 News forum auto-poster", description=status_text(load_state())[:4000],
                          colour=0x00247D)
    await interaction.response.send_message(embed=embed, ephemeral=True)
