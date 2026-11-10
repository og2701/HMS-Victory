"""News forum auto-poster: several outlets into the news forum, one post per story.

Every NEWSFEED_POLL_MINUTES the bot reads the feeds in config.NEWSFEED_FEEDS (BBC News, Sky News, the Guardian and the
Independent; ITV and the Telegraph are set up as outlets but have no working feed). Every article is tracked: when it
was first and last seen, which feeds carried it, and when it entered and last sat on its outlet's front page.

Articles on the same event are grouped into one story (a "cluster") across outlets, at most one article per outlet: a
new article joins the story whose headlines (and summaries) share most of its words (NEWSFEED_CROSS_MATCH_MIN). When
the overlap is only borderline (NEWSFEED_CROSS_MATCH_BORDERLINE up to the minimum), the story's AI judgement is shown
those near-matches and says which are the same event, and they are merged then. A story gets a signal score before
any AI is involved:

    on a front page now            +2   (BBC top stories, Sky's home feed)
    on two outlets' front pages    +1
    each hour on a front page      +1   (up to +3)
    a BBC live page                +3   (/news/live/: a major ongoing event, posted as a megathread)
    another outlet's live blog     +1   (Guardian /live/: often a general rolling blog, so it counts for less)
    each outlet beyond the first   +2   (up to +6: the strongest signal)

Only a story over the signal threshold goes to the AI, through the chat responder's own OpenAI client. The AI rates
its importance for this server, picks one to three forum tags (most relevant first), writes a neutral "why it matters", and says whether it is the
same developing story as one of the forum's open posts from the last few days. Then:

    an article of the story already linked in an open post -> nothing (no duplicate)
    same story as an open post  -> an "Update:" reply in that post (unarchived if needed)
    strong enough on its own    -> a new post
    otherwise                   -> nothing; it may be judged again if its signal climbs

"Open posts" means every unlocked post in the forum from the last NEWSFEED_MATCH_DAYS, members' as well as Vic's:
if a member got there first, the article goes into their post as an update. Members' posts are only ever replied
to, never edited, retagged or pinned.

A post links one article, the most neutral outlet that has the story (NEWSFEED_MAIN_LINK_ORDER: BBC, then Sky, ITV,
the Guardian, the Independent; a partly paywalled outlet only when nothing else has it, a paywalled article never),
then the "why it matters", an "Also covered by" line linking the other outlets (embeds suppressed, so only the main
link previews) and a footer naming the outlet linked. Updates link the same way.

New posts are capped per UK day and per poll. A story is only ever posted once: every article URL in it is recorded
(persisted), and an article from another outlet that joins a posted story later is recorded against that post too.
BBC live pages get the Megathread tag and are pinned when nothing else in the forum is. Every post's message count and
reactions are recorded at 24h and 72h so the thresholds can be tuned.

Everything that can be is a pure function (parsing, clustering, scoring, matching, caps, the decision) so it can be
tested without Discord or the network. State: data/json/newsfeed_state.json and data/json/newsfeed_engagement.json.
"""

import asyncio
import email.utils
import functools
import gzip
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
MAX_MAYBE_SAME = 4            # borderline other-outlet stories shown to the AI per judgement
MEGATHREAD_TAG = "Megathread"
SPORT_TAG = "Sport"
MAX_AI_TAGS = 3          # tags the AI may give a post (the primary first)
MAX_POST_TAGS = 5        # Discord's limit on tags per forum post
TITLE_LIMIT = 100             # Discord's thread name limit
STORY_TTL = 3 * 86400         # forget an article this long after it was last seen in any feed
POSTED_TTL = 30 * 86400       # remember posted URLs this long (dedupe)
THREAD_TTL = 7 * 86400
FOOTER = "-# Posted automatically from {source}. Comments here are about this story; something new? Start a new post."

_lock = asyncio.Lock()


# ---------------------------------------------------------------- outlets

def outlet_info(outlet: str) -> Tuple[str, str, str]:
    """(footer name, short name, paywall) for an outlet key."""
    return tuple(config.NEWSFEED_OUTLETS.get(outlet) or (outlet, outlet, "free"))


def outlet_label(outlet: str) -> str:
    return outlet_info(outlet)[0]


def outlet_short(outlet: str) -> str:
    return outlet_info(outlet)[1]


_HOSTS = (("bbc.co.uk", "bbc"), ("bbc.com", "bbc"), ("news.sky.com", "sky"), ("skynews.com", "sky"),
          ("itv.com", "itv"), ("theguardian.com", "guardian"), ("independent.co.uk", "independent"),
          ("telegraph.co.uk", "telegraph"))


def outlet_of(url: str) -> Optional[str]:
    host = (urlsplit(url or "").netloc or "").lower()
    for suffix, outlet in _HOSTS:
        if host == suffix or host.endswith("." + suffix):
            return outlet
    return None


def outlet_rank(outlet: str) -> Tuple[int, int]:
    """Sort key for the main link: free outlets before partly paywalled ones, then NEWSFEED_MAIN_LINK_ORDER."""
    order = list(config.NEWSFEED_MAIN_LINK_ORDER)
    pos = order.index(outlet) if outlet in order else len(order)
    return (0 if outlet_info(outlet)[2] == "free" else 1, pos)


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
    """Items from an RSS 2.0 or Atom feed: [{title, summary, link, url, guid, published}]. Bad XML gives []. A gzipped
    body (the Independent gzips even when not asked to) is unpacked first."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if data and data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError):
            return []
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
    """Live pages: BBC /news/live/..., Guardian .../live/YYYY/... blogs."""
    parts = urlsplit(url or "")
    outlet = outlet_of(url)
    if outlet == "bbc":
        return parts.path.startswith("/news/live/")
    if outlet == "guardian":
        return "/live/" in parts.path
    return False


def is_megathread_url(url: str) -> bool:
    """A BBC live page marks a major ongoing event, so its post is a megathread (tagged, and pinned when nothing else
    is). The Guardian's live blogs are often general rolling ones ("UK politics live"), so they never are."""
    return is_live_url(url) and outlet_of(url) == "bbc"


# What is never posted or tracked: video, audio, galleries, opinion and comment, games and puzzles.
_NOT_POSTABLE = ("/videos/", "/video/", "/av/", "/sounds/", "/iplayer/", "/programmes/", "/reel/", "/newsbeat/",
                 "/news/in_pictures", "/news/resources/", "/audio/", "/gallery/", "/picture/", "/commentisfree/",
                 "/voices/", "/opinion/", "/comment/", "/crosswords/", "/games/", "/podcasts/", "/extra/")
_SECTIONS = {
    "bbc": ("/news/", "/sport/"),
    "sky": ("/story/",),
    "itv": ("/news/",),
    "guardian": ("/",),
    "independent": ("/news/", "/bulletin/", "/climate-change/", "/sport/", "/money/", "/tech/"),
    "telegraph": ("/news/", "/politics/", "/world-news/", "/business/", "/money/", "/sport/", "/football/",
                  "/global-health/", "/royal-family/", "/us/"),
}


def is_postable_url(url: str) -> bool:
    """Articles and live pages from a known outlet: no video, audio, galleries, opinion pieces or puzzles."""
    outlet = outlet_of(url)
    if outlet is None:
        return False
    path = urlsplit(url or "").path
    if not any(path.startswith(prefix) for prefix in _SECTIONS.get(outlet, ("/",))) or path in ("", "/"):
        return False
    return not any(bit in path for bit in _NOT_POSTABLE)


def is_paywalled_url(url: str) -> bool:
    outlet = outlet_of(url)
    if outlet and outlet_info(outlet)[2] == "full":
        return True
    path = urlsplit(url or "").path
    return any(p in path for p in config.NEWSFEED_PAYWALL_URL_PATTERNS)


_SPORT_SECTIONS = ("/sport/", "/football/", "/rugby-union/", "/rugby-league/", "/cricket/", "/formula-1/", "/tennis/",
                   "/golf/", "/boxing/")


def is_sport_url(url: str) -> bool:
    path = urlsplit(url or "").path
    return any(path.startswith(s) or (outlet_of(url) == "independent" and s in path) for s in _SPORT_SECTIONS)


# ---------------------------------------------------------------- matching

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be been before being below between both but by can
could did do does doing down during each few for from further had has have having he her here hers him his how i if in
into is it its itself just me more most my new no nor not now of off on once only or other our out over own s same says
said she should so some such than that the their them then there these they this those through to too under until up
very was we were what when where which while who whom why will with would you your yet so far amid over after us uk
how what why who year years day days week weeks live latest update updates news bbc first last back know get gets got
make makes made take takes told tells call calls one two three four five six seven eight nine ten
sky guardian independent itv telegraph
""".split())
_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


@functools.lru_cache(maxsize=20000)
def _tokens(text: str) -> frozenset:
    out = set()
    for word in _WORD_RE.findall((text or "").lower().replace("’", "'").replace("‘", "'")):
        word = word.split("'")[0]
        if len(word) < 3 or word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.add(word)
    return frozenset(out)


def tokens(text: str) -> set:
    """Significant words: lower-case, no stopwords, no short words, a plain plural 's' folded."""
    return set(_tokens(text or ""))


def overlap(a: str, b: str, min_shared: int = 2) -> float:
    """Share of the shorter text's significant words that the other also has (0 when fewer than min_shared match)."""
    ta, tb = _tokens(a or ""), _tokens(b or "")
    if not ta or not tb:
        return 0.0
    shared = len(ta & tb)
    if shared < min_shared:
        return 0.0
    return shared / min(len(ta), len(tb))


def match_score(title_a: str, title_b: str, summary_a: str = "", summary_b: str = "") -> float:
    """How alike two articles are (0-1): the headlines' word overlap, or headline plus summary overlap (needing 4 shared
    words, and scaled so 0.8 of the threshold there counts as the threshold)."""
    score = overlap(title_a, title_b)
    if summary_a and summary_b:
        score = max(score, overlap(f"{title_a} {summary_a}", f"{title_b} {summary_b}", min_shared=4) / 0.8)
    return score


def same_story_heuristic(title_a: str, title_b: str, summary_a: str = "", summary_b: str = "",
                         threshold: Optional[float] = None) -> bool:
    """Cheap cross-outlet check: the headlines share most of their words, or headline plus summary share a lot."""
    threshold = config.NEWSFEED_CROSS_MATCH_MIN if threshold is None else threshold
    return match_score(title_a, title_b, summary_a, summary_b) >= threshold


def _entry_score(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    return match_score(a.get("title", ""), b.get("title", ""), a.get("summary", ""), b.get("summary", ""))


def _full_tokens(entry: Dict[str, Any]) -> frozenset:
    return _tokens(f"{entry.get('title', '')} {entry.get('summary', '')}")


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


# ---------------------------------------------------------------- tracking

def empty_state() -> Dict[str, Any]:
    return {
        "stories": {},       # article url -> {outlet, title, summary, link, feeds, first_seen, top_first, cluster, ...}
        "clusters": {},      # cluster id -> {members: [urls], created, verdict, judgements, signal, done, thread_id}
        "posted": {},        # url (and guid) -> {ts, thread_id, kind}: dedupe
        "threads": [],       # posts Vic opened: {id, title, summary, url, urls, outlet, tag, created, live, ...}
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
    # The BBC-only version kept other outlets' headlines apart (they are stories now, re-read on the next poll) and
    # tracked BBC video and audio items too (never postable, so they'd only take the BBC's place in a story).
    state.pop("others", None)
    state["stories"] = {u: e for u, e in state["stories"].items() if is_postable_url(u)}
    for url, entry in state["stories"].items():
        entry.setdefault("outlet", outlet_of(url))
    return state


def save_state(state: Dict[str, Any]) -> None:
    atomic_write_json(config.NEWSFEED_STATE_FILE, state, indent=1)


def is_dry_run(state: Dict[str, Any]) -> bool:
    return bool(config.NEWSFEED_DRY_RUN if state.get("dry_run") is None else state["dry_run"])


def observe(state: Dict[str, Any], results: Dict[str, List[Dict[str, Any]]], feeds: Dict[str, Tuple[str, str, str]],
            now: float) -> None:
    """Fold one poll's feed items into the state. Every outlet's articles are tracked; video, audio, opinion and the
    like are left out (they could never be posted, and would only crowd a story's one place per outlet)."""
    stories = state["stories"]
    for name, items in results.items():
        _, outlet, kind = feeds[name]
        for item in items:
            url = item["url"]
            if not url or not is_postable_url(url) or outlet_of(url) != outlet:
                continue
            entry = stories.setdefault(url, {"first_seen": now, "feeds": []})
            entry.update({"outlet": outlet, "title": item["title"], "summary": item["summary"], "link": item["link"],
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
    state["posted"] = {u: e for u, e in state["posted"].items() if now - e.get("ts", now) < POSTED_TTL}
    state["threads"] = [t for t in state["threads"] if now - t.get("created", now) < THREAD_TTL]
    cutoff = uk_day(now - 14 * 86400)
    for key in ("daily", "daily_updates", "daily_dry", "daily_dry_updates"):
        state[key] = {d: n for d, n in state[key].items() if d >= cutoff}


# ---------------------------------------------------------------- clustering

_CLUSTER_FIELDS = ("verdict", "judgements", "judged_signal", "judged_threads", "done", "failures")


def cluster_outlets(state: Dict[str, Any], cid: str) -> set:
    stories = state["stories"]
    return {stories[u]["outlet"] for u in state["clusters"].get(cid, {}).get("members", []) if u in stories}


def assign_clusters(state: Dict[str, Any], threshold: Optional[float] = None) -> List[Tuple[str, str]]:
    """Put every article not yet in a story into one: the story it matches best (score at or over the threshold,
    against any of the story's articles) where that outlet isn't already represented, else a new story of its own.
    Stories keep their id (the first article's URL) and their judgement as articles join. Returns the (url, cluster)
    joins made, for logging and tests."""
    threshold = config.NEWSFEED_CROSS_MATCH_MIN if threshold is None else threshold
    stories, clusters = state["stories"], state.setdefault("clusters", {})
    for cid, cluster in list(clusters.items()):
        cluster["members"] = [u for u in cluster.get("members", []) if u in stories and stories[u].get("cluster") == cid]
        if not cluster["members"]:
            del clusters[cid]
    index: Dict[str, set] = {}
    for cid, cluster in clusters.items():
        for url in cluster["members"]:
            for tok in _full_tokens(stories[url]):
                index.setdefault(tok, set()).add(cid)
    fresh = [u for u, e in stories.items() if e.get("cluster") not in clusters]
    fresh.sort(key=lambda u: (outlet_rank(stories[u].get("outlet", "")), stories[u].get("first_seen", 0), u))
    joins = []
    for url in fresh:
        entry = stories[url]
        toks = _full_tokens(entry)
        counts: Dict[str, int] = {}
        for tok in toks:
            for cid in index.get(tok, ()):
                counts[cid] = counts.get(cid, 0) + 1
        best, best_score = None, 0.0
        for cid, shared in counts.items():
            if shared < 2:
                continue
            members = clusters[cid]["members"]
            if any(stories[m].get("outlet") == entry.get("outlet") for m in members):
                continue
            score = max(_entry_score(entry, stories[m]) for m in members)
            if score > best_score:
                best, best_score = cid, score
        if best is not None and best_score >= threshold:
            clusters[best]["members"].append(url)
            entry["cluster"] = best
            joins.append((url, best))
            cid = best
        else:
            cid = url
            cluster = {"members": [url], "created": entry.get("first_seen")}
            for field in _CLUSTER_FIELDS:  # an article tracked by the BBC-only version brings its judgement along
                if field in entry:
                    cluster[field] = entry.pop(field)
            clusters[cid] = cluster
            entry["cluster"] = cid
        for tok in toks:
            index.setdefault(tok, set()).add(cid)
    return joins


def cluster_entries(state: Dict[str, Any], cid: str) -> List[Dict[str, Any]]:
    stories = state["stories"]
    out = []
    for url in state["clusters"].get(cid, {}).get("members", []):
        if url in stories:
            out.append(dict(stories[url], url=url))
    return out


def sync_posted(state: Dict[str, Any], now: float) -> None:
    """A story with any article already posted (or linked in a post) is done: every other article in it is recorded
    against the same post, so a second outlet's version is never posted again."""
    posted = state["posted"]
    for cid, cluster in state["clusters"].items():
        members = cluster.get("members", [])
        hit = None
        for url in members:
            entry = state["stories"].get(url, {})
            hit = posted.get(url) or (posted.get(entry.get("guid")) if entry.get("guid") else None)
            if hit:
                break
        if not hit:
            continue
        cluster["done"] = True
        if hit.get("thread_id"):
            cluster.setdefault("thread_id", hit.get("thread_id"))
        for url in members:
            if url not in posted:
                posted[url] = {"ts": now, "thread_id": hit.get("thread_id"), "kind": "also covered"}


def merge_clusters(state: Dict[str, Any], keep: str, other: str, now: float) -> bool:
    """The AI says `other` is the same event as `keep`. With no outlet in common, other's articles move into keep (a
    posted side makes the whole story posted). With one in common they can't share a story, but if `other` was
    posted, `keep` is marked as covered by that post. Returns whether anything changed."""
    clusters, stories = state["clusters"], state["stories"]
    if keep not in clusters or other not in clusters or keep == other:
        return False
    a, b = clusters[keep], clusters[other]
    if not (cluster_outlets(state, keep) & cluster_outlets(state, other)):
        for url in b["members"]:
            stories[url]["cluster"] = keep
            a["members"].append(url)
        if b.get("done"):
            a["done"] = True
            a.setdefault("thread_id", b.get("thread_id"))
        del clusters[other]
        sync_posted(state, now)
        return True
    if b.get("done"):
        hit = {"ts": now, "thread_id": b.get("thread_id"), "kind": "same story"}
        for url in a["members"]:
            state["posted"].setdefault(url, dict(hit))
        a["done"] = True
        a.setdefault("thread_id", b.get("thread_id"))
        return True
    return False


def maybe_same(state: Dict[str, Any], cid: str, index: Optional[Dict[str, set]] = None,
               low: Optional[float] = None, high: Optional[float] = None, limit: int = MAX_MAYBE_SAME
               ) -> List[Tuple[float, str]]:
    """Other stories that might be the same event (score from the borderline up to the match threshold), best first,
    from outlets this story lacks (another article from an outlet it already has is a follow-up, which is the update
    path's job). These go to the AI to settle."""
    low = config.NEWSFEED_CROSS_MATCH_BORDERLINE if low is None else low
    high = config.NEWSFEED_CROSS_MATCH_MIN if high is None else high
    clusters, stories = state["clusters"], state["stories"]
    mine = cluster_entries(state, cid)
    if not mine:
        return []
    outlets = {e["outlet"] for e in mine}
    if index is None:
        index = cluster_index(state)
    counts: Dict[str, int] = {}
    for entry in mine:
        for tok in _full_tokens(entry):
            for other in index.get(tok, ()):
                if other != cid:
                    counts[other] = counts.get(other, 0) + 1
    scored = []
    for other, shared in counts.items():
        if shared < 2 or other not in clusters:
            continue
        theirs = cluster_entries(state, other)
        if not theirs or {e["outlet"] for e in theirs} & outlets:
            continue
        score = max(_entry_score(x, y) for x in mine for y in theirs)
        if low <= score < high:
            scored.append((score, other))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return scored[:limit]


def cluster_index(state: Dict[str, Any]) -> Dict[str, set]:
    index: Dict[str, set] = {}
    for cid, cluster in state["clusters"].items():
        for url in cluster.get("members", []):
            entry = state["stories"].get(url)
            if entry:
                for tok in _full_tokens(entry):
                    index.setdefault(tok, set()).add(cid)
    return index


def main_entry(entries: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The article to link: postable and not paywalled, from the most neutral outlet that has the story (a partly
    paywalled outlet only when no free one does)."""
    usable = [e for e in entries if is_postable_url(e["url"]) and not is_paywalled_url(e["url"])]
    if not usable:
        return None
    return min(usable, key=lambda e: (outlet_rank(e["outlet"]), e.get("first_seen", 0)))


def also_covered(entries: List[Dict[str, Any]], main: Dict[str, Any]) -> List[Tuple[str, str]]:
    """(short outlet name, link) for every other outlet in the story, most neutral first."""
    others = [e for e in entries if e["url"] != main["url"]]
    others.sort(key=lambda e: outlet_rank(e["outlet"]))
    return [(outlet_short(e["outlet"]), e.get("link") or e["url"]) for e in others]


# ---------------------------------------------------------------- scoring

def in_top_now(entry: Dict[str, Any], now: float, poll_seconds: float) -> bool:
    """Still on its outlet's front page: seen there on this poll (or the last, allowing for a slow fetch)."""
    top_last = entry.get("top_last")
    return top_last is not None and now - top_last <= poll_seconds * 1.6


def hours_in_top(entry: Dict[str, Any]) -> float:
    if entry.get("top_first") is None or entry.get("top_last") is None:
        return 0.0
    return max(0.0, (entry["top_last"] - entry["top_first"]) / 3600)


def signal_score(entries, now: float, poll_seconds: float) -> Tuple[int, List[str]]:
    """The pre-AI importance signal for a story (all its outlets' articles) and the reasons for it. A single article
    dict is taken as a story of one."""
    if isinstance(entries, dict):
        entries = [entries]
    score, reasons = 0, []
    fronts = sorted({e.get("outlet", "?") for e in entries if in_top_now(e, now, poll_seconds)},
                    key=outlet_rank)
    if fronts:
        score += 2
        reasons.append("front page " + ", ".join(outlet_short(o) for o in fronts))
        if len(fronts) >= 2:
            score += 1
    hours = int(max((hours_in_top(e) for e in entries), default=0))
    if hours:
        score += min(hours, 3)
        reasons.append(f"{hours}h on a front page")
    lives = [e.get("link") or e.get("url") or "" for e in entries]
    if any(is_megathread_url(u) for u in lives):
        score += 3
        reasons.append("live page")
    elif any(is_live_url(u) for u in lives):
        score += 1
        reasons.append("live blog")
    outlets = {e.get("outlet") for e in entries}
    if len(outlets) > 1:
        score += min(2 * (len(outlets) - 1), 6)
        reasons.append(f"{len(outlets)} outlets")
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


def mark_story_posted(state: Dict[str, Any], entries: List[Dict[str, Any]], now: float, thread_id, kind: str,
                      cid: Optional[str] = None) -> None:
    """Record every article of a story (URL and guid) against one post, and the story as done."""
    for entry in entries:
        mark_posted(state, entry["url"], entry.get("guid", ""), now, thread_id, kind)
    cluster = state.get("clusters", {}).get(cid) if cid else None
    if cluster is not None:
        cluster["done"] = True
        cluster["thread_id"] = str(thread_id) if thread_id else None


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


def also_covered_line(others: List[Tuple[str, str]]) -> str:
    """'Also covered by: [Sky](<url>) · [Guardian](<url>)'. The <> stops Discord embedding those links, so only the
    main link shows a preview."""
    if not others:
        return ""
    links = [f"[{name}](<{url.replace('>', '%3E').replace(')', '%29')}>)" for name, url in others]
    return "-# Also covered by: " + " · ".join(links)


def build_post_body(link: str, why: str = "", source: str = "BBC News",
                    also: Optional[List[Tuple[str, str]]] = None) -> str:
    """A post is the link, a brief neutral description, the other outlets covering it, and the footer naming the
    outlet linked. No discussion question (at Oggers' ask)."""
    parts = [link]
    if why:
        parts.append(why)
    line = also_covered_line(also or [])
    if line:
        parts.append(line)
    parts.append(FOOTER.format(source=source))
    return "\n\n".join(parts)


def build_update_body(headline: str, link: str, why: str = "", source: str = "") -> str:
    first = f"**Update:** {clean_text(headline, 200)}"
    if source:
        first += f" ({source})"
    lines = [first, link]
    if why:
        lines.append(f"-# {why}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the neutrality filter

# Participle clauses that are commentary whatever follows them (", highlighting ...", ", which underscores ...").
_ALWAYS_COMMENTARY = (r"highlight(?:s|ing)|underscor(?:es|ing)|underlin(?:es|ing)|signal(?:s|l?ing)|showcas(?:es|ing)|"
                      r"illustrat(?:es|ing)|demonstrat(?:es|ing)|reflect(?:s|ing)|indicat(?:es|ing)|"
                      r"emphasi[sz](?:es|ing)|point(?:s|ing) to")
# Clauses that are commentary only when they carry a loaded word (", raising security concerns" goes,
# ", raising the cap to £1,800" stays).
_MAYBE_COMMENTARY = (r"rais(?:es|ing)|spark(?:s|ing)|prompt(?:s|ing)|fuel(?:s|l?ing)|add(?:s|ing) to|amid|"
                     r"mark(?:s|ing)|caus(?:es|ing)|leav(?:es|ing)|deepen(?:s|ing)|stok(?:es|ing)|renew(?:s|ing)|"
                     r"intensify(?:ing)?|intensifies")
_CLAUSE_RE = re.compile(r",\s+(?:which\s+|this\s+|in a move that\s+|a move that\s+)?"
                        rf"(?P<verb>{_ALWAYS_COMMENTARY}|{_MAYBE_COMMENTARY})\b(?P<rest>[^.!?;]*)", re.I)
_ALWAYS_RE = re.compile(rf"^(?:{_ALWAYS_COMMENTARY})$", re.I)
# Words that make a sentence commentary. A sentence still holding one after the clauses and adjectives are dealt
# with is dropped, and the "why" with it if nothing is left.
_LOADED_RE = re.compile(
    r"\b(?:concern(?:s|ed|ing)?|tensions?|highlight(?:s|ed|ing)?|underscor\w*|landmark|controvers\w*|"
    r"end of an era|alarm(?:s|ed|ing)?|outrage\w*|backlash|uproar|furore|turmoil|unprecedented|historic|"
    r"dramatic(?:ally)?|shock(?:ing|ed)?|stark|fears? (?:of|over|that|for)|sparks? fears|raises? questions|"
    r"questions over|blow to|major blow|escalating|deepening|bombshell|explosive|seismic|watershed|"
    r"game[- ]changer|implications|dynamics)\b", re.I)
# A closing sentence that only speculates about what it might mean ("This could influence future UK-EU relations.").
_SPECULATION_RE = re.compile(r"^(?:this|that|it|these|the (?:move|decision|development|ruling|change|incident|case|"
                             r"announcement|plan|vote|result))\s+(?:could|may|might|would|will likely|is likely to|"
                             r"is set to have|has the potential)\b", re.I)
# Evaluative adjectives that can simply be dropped in front of a noun ("a landmark ruling" -> "a ruling").
_ADJECTIVE_RE = re.compile(r"\b(?:(?P<art>[Aa]n?|[Tt]he)\s+)?(?:landmark|controversial|historic|dramatic|shocking|"
                           r"unprecedented|bombshell|explosive|seismic|sweeping|stark)\s+(?=(?P<next>[A-Za-z0-9£$€]))")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'‘“])")


def _drop_clause(match) -> str:
    verb, rest = match.group("verb"), match.group("rest") or ""
    if _ALWAYS_RE.match(verb) or _LOADED_RE.search(f"{verb}{rest}") or re.search(r"\bfears?\b|\bquestions\b",
                                                                                  rest, re.I):
        return ""
    return match.group(0)


def _drop_adjective(match) -> str:
    art = match.group("art")
    if not art:
        return ""
    if art.lower() == "the":
        return art + " "
    vowel = match.group("next").lower() in "aeiou8"
    article = ("an" if vowel else "a")
    return (article.capitalize() if art[0].isupper() else article) + " "


def strip_commentary(text: Optional[str]) -> str:
    """Make the AI's "why it matters" neutral or drop it: trailing editorial clauses (", highlighting ...",
    ", raising security concerns", ", which indicates ongoing tensions") are cut, evaluative adjectives in front of a
    noun ("a landmark ruling") are dropped, and any sentence still built on a loaded word (concerns, tensions,
    highlights, landmark, controversial, end of an era and the like) or only speculating ("This could influence ...")
    is removed. Returns "" when nothing clean is left,
    so the post goes out without a "why" rather than with loaded wording."""
    text = _WS_RE.sub(" ", str(text or "")).strip()
    if not text:
        return ""
    kept = []
    for sentence in _SENTENCE_RE.split(text):
        sentence = _CLAUSE_RE.sub(_drop_clause, sentence)
        sentence = _ADJECTIVE_RE.sub(_drop_adjective, sentence)
        sentence = _WS_RE.sub(" ", sentence).strip(" ,;:-")
        if not sentence or _LOADED_RE.search(sentence) or _SPECULATION_RE.match(sentence):
            continue
        if not sentence.endswith((".", "?", "!", "…")):
            sentence += "."
        kept.append(sentence[0].upper() + sentence[1:])
    return " ".join(kept)


# ---------------------------------------------------------------- the AI and the decision

JUDGE_PROMPT = """You choose and frame news stories for the news forum of a large British Discord server. Members are \
from all over the UK, mostly young adults, and most interested in UK politics, the economy and major world events. \
Each story the forum takes gets one post where members discuss it, so only stories people will want to talk about \
should get one.

Judge the one story you are given and reply as JSON. "covered_by" lists the outlets running it.

- "importance" (1-10): how much this server would want a discussion post on it today.
  9-10: a major national or world event (a general election result, a prime minister resigning, a budget, a major \
attack or disaster, a war starting).
  7-8: significant UK politics, policy or economy news, or a major world story many people will be discussing.
  4-6: an ordinary news story.
  1-3: trivia, lifestyle, celebrity, human interest, consumer advice, a local incident, a sport result, an \
explainer, analysis or opinion piece, a quiz, or a video or audio item.
- "newsworthy": true only if it is a real development worth discussing now. A "what we know" round-up counts only \
when the event itself is major.
- "tags": one to three names from "allowed_tags", the most relevant first. The first is the story's primary tag; \
add others only when they clearly apply too. Use the most specific tag that fits rather than a general one:
  World: international news without a better specific tag. US stories go here, with others such as Conflict & \
Defence or Elections when they apply.
  UK Politics: Westminster, the government, parties and legislation, but not a policy area that has its own tag \
unless the politics itself is central.
  Nations & Regions: Scotland, Wales, Northern Ireland and the regions of England.
  Local: a story about one specific town or city.
  Business & Economy: companies, markets, jobs, the cost of living, taxes and the economy.
  Elections: any election anywhere, its campaigns and its results.
  Crime & Courts: crime, police, trials, sentencing and prisons.
  Conflict & Defence: wars, strikes and attacks by states or armed groups, the military and defence policy.
  Health: the NHS, public health, medicine and care.
  Education: schools, exams, universities and pupils.
  Environment: climate, extreme weather, nature and the energy transition.
  Tech & Science: technology, AI, science and research.
  Sport: sport stories only.
- "same_story_post": if one of "open_posts" is about the same developing story (the same event or the same specific \
ongoing issue, where this article is a new development or follow-up on it), give that post's "id"; otherwise null. \
Two different stories on the same broad subject (two separate stories about one party, say) are NOT the same story.
- "same_coverage": the "id" of every item in "maybe_same_coverage" that reports the same news event as this story \
(another outlet's article on the same thing); [] if none. Same subject but a different event does NOT count.
- "why": one or two short sentences (at most 220 characters) on why it matters, for someone who has not read it.
- "reason": a few words on your call.

"why" must be strictly neutral and factual, to the standard of BBC impartiality: no opinions, no \
loaded or emotive words, no jokes, no persona, and no taking sides on parties, politicians, countries or groups. \
Nothing that promotes non-British nationalism or disparages Britain or the British. No @mentions, no emojis, no \
hashtags. Say plainly what happened and what it directly affects or decides. Never use commentary words or \
phrases such as "highlights", "underscores", "tensions", "concerns", "raising concerns", "landmark", "historic", \
"end of an era", "controversial" or "indicates", and never end on a clause or sentence interpreting what it means or \
speculating about what it could lead to ("This could ...").

Reply with JSON only: {"importance": int, "newsworthy": bool, "tags": [str], "same_story_post": str or null, \
"same_coverage": [str], "why": str, "reason": str}"""


def judge_payload(entry: Dict[str, Any], signal: int, reasons: List[str], threads: List[Dict[str, Any]],
                  tag_names: List[str], maybes: Optional[List[Dict[str, Any]]] = None) -> str:
    url = entry.get("link") or entry.get("url", "")
    covered = entry.get("covered_by") or [outlet_label(entry.get("outlet") or outlet_of(url) or "bbc")]
    return json.dumps({
        "headline": entry.get("title", ""),
        "summary": entry.get("summary", ""),
        "url": url,
        "covered_by": covered,
        "live_page": is_live_url(url),
        "signals": {"score": signal, "reasons": reasons},
        "open_posts": [{"id": str(t["id"]), "title": t.get("title", ""), "summary": (t.get("summary") or "")[:300]}
                       for t in threads],
        "maybe_same_coverage": [{"id": str(m["id"]), "outlet": m.get("outlet", ""), "headline": m.get("title", ""),
                                 "summary": (m.get("summary") or "")[:300]} for m in (maybes or [])],
        "allowed_tags": tag_names,
    }, ensure_ascii=False)


def verdict_tag_names(raw: Dict[str, Any], tag_names: List[str]) -> List[str]:
    """The AI's tags matched to the forum's names (any case), unknown ones and Megathread dropped (that one is only
    ever set for live pages), deduped and capped at MAX_AI_TAGS. Reads "tags" (a list, or a single string) and falls
    back to an old single "tag"."""
    lookup = {t.lower(): t for t in tag_names if t != MEGATHREAD_TAG}
    given = raw.get("tags")
    if isinstance(given, str):
        given = [given]
    if not isinstance(given, list) or not given:
        given = [raw.get("tag")]
    tags = []
    for name in given:
        tag = lookup.get(str(name or "").strip().lower())
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:MAX_AI_TAGS]


def post_tags(verdict: Dict[str, Any]) -> List[str]:
    """A verdict's tags, primary first: its "tags", or a stored older verdict's single "tag"."""
    tags = [t for t in (verdict.get("tags") or []) if t]
    if not tags and verdict.get("tag"):
        tags = [verdict["tag"]]
    return tags


def normalise_verdict(raw: Any, tag_names: List[str], thread_ids: Iterable[str],
                      maybe_ids: Iterable[str] = ()) -> Dict[str, Any]:
    """The AI's answer, checked: importance clamped 1-10, one to three tags from the forum's list (deduped, in the
    AI's order; an old single "tag" answer still works), a match only to a post (or a
    near-match story) it was shown, and the text made neutral. Anything unusable comes back as an error verdict that
    never posts."""
    if not isinstance(raw, dict):
        return {"error": "bad reply", "importance": 0, "newsworthy": False}
    try:
        importance = int(round(float(raw.get("importance", 0))))
    except (TypeError, ValueError):
        importance = 0
    importance = max(0, min(10, importance))
    tags = verdict_tag_names(raw, tag_names)
    match = raw.get("same_story_post")
    match = str(match).strip() if match not in (None, "", "null", False) else None
    if match is not None and match not in {str(t) for t in thread_ids}:
        match = None
    allowed = {str(m) for m in maybe_ids}
    same = raw.get("same_coverage")
    same = [str(s).strip() for s in same] if isinstance(same, list) else []
    same = [s for i, s in enumerate(same) if s in allowed and s not in same[:i]]
    newsworthy = raw.get("newsworthy")
    if isinstance(newsworthy, str):
        newsworthy = newsworthy.strip().lower() in ("true", "yes", "1")
    return {
        "importance": importance,
        "newsworthy": bool(newsworthy),
        "tag": tags[0] if tags else None,   # the primary tag (kept for older readers of the state)
        "tags": tags,
        "same_story_thread": match,
        "same_coverage": same,
        "why": clean_text(strip_commentary(clean_text(raw.get("why"), 400)), 240),
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
                      tag_names: List[str], complete: Optional[Callable] = None,
                      maybes: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Ask the AI about one story. `complete(system, payload) -> (json, prompt_tokens, completion_tokens)` can be
    swapped out (tests); by default it is the chat responder's OpenAI client."""
    payload = judge_payload(entry, signal, reasons, threads, tag_names, maybes)
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
    return normalise_verdict(raw, tag_names, [t["id"] for t in threads], [m["id"] for m in (maybes or [])])


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
    primary = (post_tags(verdict) or [None])[0]   # the sport bar applies when Sport is the primary tag
    needed = th.sport_min_importance if (sport or primary == SPORT_TAG) else th.min_importance
    if importance < needed:
        return "skip", f"importance {importance} under {needed}"
    return "new", f"importance {importance}"


def needs_judgement(entry: Dict[str, Any], signal: int, shown_threads: List[Dict[str, Any]], th: Thresholds,
                    shown_maybes: Iterable[str] = ()) -> bool:
    """Ask the AI (again)? Yes when never judged, when an open post or a near-match story it hasn't been compared with
    now looks close, or when a borderline story's signal has climbed by 2 or more since, up to max_judgements calls.
    `entry` is the story's record (its cluster)."""
    verdict = entry.get("verdict")
    if not verdict:
        return True
    count = int(entry.get("judgements", 1))
    if count >= th.max_judgements:
        return False
    seen = set(entry.get("judged_threads") or [])
    if any(str(t["id"]) not in seen for t in shown_threads):
        return True
    if any(str(m) not in set(entry.get("judged_maybe") or []) for m in shown_maybes):
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


async def _read_body(resp, limit: int) -> bytes:
    """The whole response body up to limit+1 bytes. StreamReader.read(n) returns whatever has arrived, which for a big
    feed (the Guardian's UK front is ~500KB) is only the first chunk, so read until the end."""
    chunks, size = [], 0
    while size <= limit:
        chunk = await resp.content.read(min(65536, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


async def fetch_feeds(state: Dict[str, Any], now: float, feeds: Optional[Dict[str, Tuple[str, str, str]]] = None
                      ) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch every feed not in backoff, concurrently, each with its own timeout. A dead feed is skipped, logged and
    left alone for longer each time; it never raises."""
    import aiohttp
    feeds = feeds or config.NEWSFEED_FEEDS
    health = state.setdefault("feed_health", {})
    for name in list(health):
        if name not in feeds:
            del health[name]
    base = config.NEWSFEED_POLL_MINUTES * 60
    due = {n: f for n, f in feeds.items() if float(health.get(n, {}).get("until", 0)) <= now}
    results: Dict[str, List[Dict[str, Any]]] = {}

    async def one(session, name, url):
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await _read_body(resp, MAX_FEED_BYTES)
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


def thread_linking(url, threads: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The open post (anyone's) that already links this article (or any of these articles), if one does."""
    wanted = {url} if isinstance(url, str) else set(url or ())
    wanted.discard("")
    for thread in threads:
        if wanted & set(thread.get("urls") or ()):
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
    """The forum's tag objects for `names`, in order and deduped. Megathread is always kept and the rest trimmed so a
    post stays within Discord's limit; a fallback tag goes in when none of the names is usable, as the forum needs one."""
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
    mega = [t for t in tags if t.name == MEGATHREAD_TAG]
    rest = [t for t in tags if t.name != MEGATHREAD_TAG]
    return rest[:MAX_POST_TAGS - len(mega)] + mega


def story_view(state: Dict[str, Any], cid: str) -> Optional[Dict[str, Any]]:
    """Everything about one story needed to judge and post it: its articles, the main one, the other outlets."""
    entries = cluster_entries(state, cid)
    main = main_entry(entries)
    if main is None:
        return None
    entries.sort(key=lambda e: outlet_rank(e["outlet"]))
    return {
        "cid": cid, "entries": entries, "main": main, "url": main["url"], "link": main.get("link") or main["url"],
        "title": main.get("title", ""), "summary": main.get("summary", ""), "guid": main.get("guid", ""),
        "outlet": main["outlet"], "also": also_covered(entries, main),
        "covered_by": [outlet_label(e["outlet"]) for e in entries],
    }


async def _create_post(client, forum, state, view, verdict, signal, reasons, now, dry: bool) -> Optional[Dict[str, Any]]:
    live = is_megathread_url(view["link"])
    title = trim_title(view["title"])
    source = outlet_label(view["outlet"])
    body = build_post_body(view["link"], verdict.get("why", ""), source=source, also=view["also"])
    chosen = post_tags(verdict)
    tag_names = chosen + ([MEGATHREAD_TAG] if live else [])
    also = ", ".join(name for name, _ in view["also"])
    detail = (f"\"{title}\" <{view['link']}> via {outlet_short(view['outlet'])}{f' (also {also})' if also else ''} "
              f"(signal {signal}: {', '.join(reasons)}; AI {verdict.get('importance')}/10, "
              f"{', '.join(chosen) or 'no tag'}{', megathread' if live else ''})")
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
        engagement[rec["id"]] = {"title": title, "url": view["url"], "outlet": view["outlet"],
                                 "outlets": sorted({e["outlet"] for e in view["entries"]}), "tag": verdict.get("tag"),
                                 "tags": chosen, "live": live, "created": now, "signal": signal,
                                 "importance": verdict.get("importance")}
        atomic_write_json(config.NEWSFEED_ENGAGEMENT_FILE, engagement, indent=1)
    urls = [e["url"] for e in view["entries"]]
    rec.update({"title": title, "summary": view.get("summary", ""), "url": view["url"], "urls": urls,
                "outlet": view["outlet"], "tag": verdict.get("tag"), "tags": chosen, "created": now, "live": live,
                "last_update": None, "updates": 0})
    state["threads"].append(rec)
    mark_story_posted(state, view["entries"], now, rec["id"], "dry" if dry else "new", view["cid"])
    bump_cap(state, "daily_dry" if dry else "daily", now)
    return {"id": rec["id"], "title": title, "summary": view.get("summary", ""), "own": True, "created": now,
            "last_update": None, "urls": list(urls)}


async def _post_update(client, state, view, verdict, target, now, dry: bool) -> bool:
    source = outlet_label(view["outlet"])
    body = build_update_body(view["title"], view["link"], verdict.get("why", ""), source=source)
    tid = target["id"]
    detail = f"\"{view['title']}\" <{view['link']}> via {outlet_short(view['outlet'])} (AI {verdict.get('importance')}/10)"
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
    urls = [e["url"] for e in view["entries"]]
    for rec in state["threads"]:
        if str(rec["id"]) == str(tid):
            rec["last_update"] = now
            rec["updates"] = int(rec.get("updates", 0)) + 1
            rec.setdefault("urls", [rec.get("url")]).extend(urls)
    if not target.get("own"):
        state.setdefault("member_updates", {})[str(tid)] = now
    target["last_update"] = now
    target.setdefault("urls", []).extend(urls)
    mark_story_posted(state, view["entries"], now, tid, "dry-update" if dry else "update", view["cid"])
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
                await thread.edit(pinned=False, reason="News auto-poster: live page has left the front page")
                await _log(client, f"unpinned <#{rec['id']}> (live page off the front page)")
        except Exception:
            logger.warning("[NEWSFEED] could not unpin %s", rec["id"], exc_info=True)
        rec["pinned"] = False


def candidate_stories(state: Dict[str, Any], now: float, th: Thresholds, poll_seconds: float
                      ) -> List[Tuple[int, List[str], Dict[str, Any], List[Tuple[float, str]]]]:
    """Stories worth looking at this poll, strongest first: (signal, reasons, view, near-matches). A story whose own
    signal is short of the update bar can still be a candidate when it has some signal of its own (a front page or a
    second outlet) and near-matches from other outlets would lift it over the new-post bar, since the AI settles
    those in the same call."""
    out = []
    index = cluster_index(state)
    floor = min(th.min_signal, th.update_min_signal)
    for cid, cluster in state["clusters"].items():
        if cluster.get("done"):
            continue
        view = story_view(state, cid)
        if view is None or not any(is_fresh(e, now) for e in view["entries"]):
            continue
        signal, reasons = signal_score(view["entries"], now, poll_seconds)
        cluster["signal"] = signal
        maybes = maybe_same(state, cid, index) if signal >= 2 else []
        if signal < floor:
            extra = {o for _, m in maybes for o in cluster_outlets(state, m)} - {e["outlet"] for e in view["entries"]}
            room = max(0, 6 - 2 * (len(view["entries"]) - 1))
            if signal < 2 or signal + min(2 * len(extra), room) < th.min_signal:
                continue
        out.append((signal, reasons, view, maybes))
    out.sort(key=lambda c: (c[0], len(c[2]["entries"])), reverse=True)
    return out


async def run_once(client, state: Dict[str, Any], now: Optional[float] = None, judge: Optional[Callable] = None
                   ) -> Dict[str, Any]:
    """One poll: fetch, track, cluster, judge the strong candidates, post or update. Returns a summary for the control
    panel's Run button (and the dry check)."""
    now = now or time.time()
    poll_seconds = config.NEWSFEED_POLL_MINUTES * 60
    th = Thresholds()
    dry = is_dry_run(state)
    summary = {"feeds_ok": 0, "feeds_failed": 0, "candidates": 0, "judged": 0, "posted": [], "updated": [],
               "dry": dry, "decisions": []}

    results = await fetch_feeds(state, now)
    summary["feeds_ok"] = len(results)
    summary["feeds_failed"] = sum(1 for n in config.NEWSFEED_FEEDS if n not in results)
    observe(state, results, config.NEWSFEED_FEEDS, now)
    prune(state, now)
    assign_clusters(state)
    sync_posted(state, now)
    state["last_run"] = now

    try:
        await take_snapshots(client, state, now)
        await unpin_stale_megathreads(client, state, now)
    except Exception:
        logger.exception("[NEWSFEED] engagement/unpin pass failed")

    if not results:
        return summary  # nothing new from any outlet this poll

    forum = client.get_channel(config.NEWSFEED_FORUM_ID)
    if forum is None:
        await _log(client, f"forum {config.NEWSFEED_FORUM_ID} not found; nothing posted")
        return summary
    tag_names = [t.name for t in getattr(forum, "available_tags", []) if t.name != MEGATHREAD_TAG]

    candidates = candidate_stories(state, now, th, poll_seconds)
    summary["candidates"] = len(candidates)
    if not candidates:
        return summary

    pool = await build_thread_pool(client, forum, state, now)
    new_this_run, judged = 0, 0
    for signal, reasons, view, maybes in candidates:
        cid = view["cid"]
        cluster = state["clusters"].get(cid)
        if cluster is None or cluster.get("done"):
            continue  # merged into another story, or posted, earlier this poll
        linked = thread_linking([e["url"] for e in view["entries"]], pool)
        if linked:
            # Someone (often a member) has already posted one of its articles: nothing to add, no duplicate.
            mark_story_posted(state, view["entries"], now, linked["id"], "already linked", cid)
            logger.info("[NEWSFEED] %r is already linked in post %s; skipped", view["title"], linked["id"])
            continue
        maybes = [(s, m) for s, m in maybes if m in state["clusters"]]
        maybe_views = []
        for _, m in maybes:
            other = cluster_entries(state, m)
            if other:
                best = min(other, key=lambda e: outlet_rank(e["outlet"]))
                maybe_views.append({"id": m, "outlet": outlet_label(best["outlet"]), "title": best.get("title", ""),
                                    "summary": best.get("summary", "")})
        shown = candidate_threads(view, pool)
        if (signal < th.min_signal and not shown and not maybe_views
                and not (cluster.get("verdict") or {}).get("same_story_thread")):
            continue  # only strong enough to be an update, and there's no open post it could update
        if needs_judgement(cluster, signal, shown, th, [m["id"] for m in maybe_views]):
            if judged >= MAX_JUDGE_PER_RUN:
                continue
            judged += 1
            verdict = await judge_story(view, signal, reasons, shown, tag_names, complete=judge, maybes=maybe_views)
            cluster["verdict"] = verdict
            cluster["judgements"] = int(cluster.get("judgements", 0)) + 1
            cluster["judged_signal"] = signal
            cluster["judged_threads"] = sorted(set(cluster.get("judged_threads") or []) | {str(t["id"]) for t in shown})
            cluster["judged_maybe"] = sorted(set(cluster.get("judged_maybe") or []) | {m["id"] for m in maybe_views})
            logger.info("[NEWSFEED] judged %r: signal %d %s -> %s", view["title"], signal, reasons, verdict)
            merged = []
            for m in verdict.get("same_coverage") or []:
                if verdict.get("same_story_thread") and state["clusters"].get(m, {}).get("done"):
                    continue  # a development on a posted story: it goes in as an update, not folded away
                if merge_clusters(state, cid, m, now):
                    merged.append(m)
            if merged:
                if cluster.get("done"):
                    summary["decisions"].append((view["title"], "skip", "same story as an existing post"))
                    continue  # one of the near-matches was already posted: this is that story
                view = story_view(state, cid) or view
                signal, reasons = signal_score(view["entries"], now, poll_seconds)
                cluster["signal"] = signal
                logger.info("[NEWSFEED] merged %s into %r: now %s", merged, view["title"],
                            [e["outlet"] for e in view["entries"]])
        verdict = cluster.get("verdict") or {}
        action, why = decide(signal, verdict, th, sport=is_sport_url(view["url"]))
        target = None
        if action == "update":
            target = next((t for t in pool if str(t["id"]) == str(verdict.get("same_story_thread"))), None)
            if target is None:
                # The post it matched has gone (locked or deleted): judge it as a story of its own.
                action, why = decide(signal, dict(verdict, same_story_thread=None), th, sport=is_sport_url(view["url"]))
        summary["decisions"].append((view["title"], action, why))
        try:
            if action == "new":
                if new_this_run >= config.NEWSFEED_MAX_NEW_PER_RUN:
                    continue
                if not cap_allows(state, "daily_dry" if dry else "daily", now, config.NEWSFEED_DAILY_CAP):
                    continue
                made = await _create_post(client, forum, state, view, verdict, signal, reasons, now, dry)
                if made:
                    pool.append(made)
                    new_this_run += 1
                    summary["posted"].append(view["title"])
            elif action == "update":
                if not thread_gap_ok(target, now):
                    continue
                if not cap_allows(state, "daily_dry_updates" if dry else "daily_updates", now, config.NEWSFEED_DAILY_UPDATE_CAP):
                    continue
                if await _post_update(client, state, view, verdict, target, now, dry):
                    summary["updated"].append(view["title"])
        except Exception as e:
            logger.exception("[NEWSFEED] %s failed for %s", action, view["url"])
            cluster["failures"] = int(cluster.get("failures", 0)) + 1
            if cluster["failures"] >= 3:
                cluster["done"] = True  # stop retrying (and logging) a story Discord keeps refusing
            await _log(client, f"{action} failed for <{view['link']}> (try {cluster['failures']}): "
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

def outlet_health(state: Dict[str, Any], feeds: Optional[Dict[str, Tuple[str, str, str]]] = None
                  ) -> List[Tuple[str, int, int, int]]:
    """(outlet, feeds working, feeds failing, feeds never fetched) for each outlet that has feeds, in link order."""
    feeds = feeds or config.NEWSFEED_FEEDS
    health = state.get("feed_health") or {}
    per: Dict[str, List[int]] = {}
    for name, (_, outlet, _) in feeds.items():
        counts = per.setdefault(outlet, [0, 0, 0])
        rec = health.get(name)
        if not rec:
            counts[2] += 1
        elif rec.get("failures"):
            counts[1] += 1
        else:
            counts[0] += 1
    return [(o, *per[o]) for o in sorted(per, key=outlet_rank)]


def outlet_health_line(state: Dict[str, Any], feeds: Optional[Dict[str, Tuple[str, str, str]]] = None) -> str:
    """'BBC ✅ · Sky ✅ · Guardian ⚠️ 1/3 failing · Independent ❌' (or ⏳ before the first poll)."""
    bits = []
    for outlet, ok, failing, never in outlet_health(state, feeds):
        name = outlet_short(outlet)
        if failing and not ok:
            bits.append(f"{name} ❌")
        elif failing:
            bits.append(f"{name} ⚠️ {failing}/{ok + failing + never} failing")
        elif ok:
            bits.append(f"{name} ✅")
        else:
            bits.append(f"{name} ⏳")
    return " · ".join(bits)


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
        f"Outlets: {outlet_health_line(state)}",
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
    clusters = state.get("clusters") or {}
    waiting = sorted((cid for cid, c in clusters.items() if not c.get("done") and c.get("signal")),
                     key=lambda cid: clusters[cid].get("signal", 0), reverse=True)[:5]
    if waiting:
        lines.append("\n**Strongest waiting**")
        for cid in waiting:
            c = clusters[cid]
            view = story_view(state, cid)
            title = view["title"] if view else state["stories"].get(cid, {}).get("title", "")
            outlets = ", ".join(outlet_short(e["outlet"]) for e in view["entries"]) if view else ""
            v = c.get("verdict") or {}
            ai = f", AI {v.get('importance')}/10" if v and not v.get("error") else ""
            lines.append(f"- {trim_title(title, 70)} ({outlets}; signal {c.get('signal', 0)}{ai})")
    recent = [t for t in state["threads"] if not t.get("dry")][-5:]
    if recent:
        lines.append("\n**Recent posts**")
        for t in reversed(recent):
            via = f" via {outlet_short(t['outlet'])}" if t.get("outlet") else ""
            lines.append(f"- <#{t['id']}>{via}, {t.get('updates', 0)} updates")
    lines.append(f"\n-# Thresholds: signal {config.NEWSFEED_MIN_SIGNAL} (updates {config.NEWSFEED_UPDATE_MIN_SIGNAL}), "
                 f"AI {config.NEWSFEED_MIN_IMPORTANCE}/10 (updates {config.NEWSFEED_UPDATE_MIN_IMPORTANCE}, sport "
                 f"{config.NEWSFEED_SPORT_MIN_IMPORTANCE}). Tracking {len(state['stories'])} articles in "
                 f"{len(clusters)} stories.")
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
