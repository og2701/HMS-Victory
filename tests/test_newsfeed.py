"""News forum auto-poster: the pure parts (parsing, tracking, scoring, caps, matching, the decision) without Discord,
the network or a real AI call."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.features import newsfeed as N

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "newsfeed")
FEEDS = {
    "bbc_top": ("u", "bbc", "top"),
    "bbc_world": ("u", "bbc", "section"),
    "sky_home": ("u", "sky", "top"),
    "guardian_politics": ("u", "guardian", "section"),
}
NOW = 1_794_100_000.0  # Sat 7 Nov 2026, about 19:06 UTC
POLL = 600
KRAKOW = "https://www.bbc.co.uk/news/articles/cmd7q15drwgeo"
BARNIER = "https://www.bbc.co.uk/news/articles/c9kgjj206nzpo"
LIVE = "https://www.bbc.co.uk/news/live/c4g8xk2p1zzt"


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return N.parse_feed(f.read())


def tracked(polls=1, start=NOW):
    """A state after `polls` polls of the fixtures, ten minutes apart."""
    state = N.empty_state()
    results = {"bbc_top": fixture("bbc_top.xml"), "sky_home": fixture("sky_home.xml"),
               "guardian_politics": fixture("guardian.xml")}
    for i in range(polls):
        N.observe(state, results, FEEDS, start + i * POLL)
    return state


# ------------------------------------------------------------ parsing

def test_bbc_feed_parses_with_cdata_and_clean_urls():
    items = fixture("bbc_top.xml")
    assert [i["url"] for i in items[:3]] == [KRAKOW, BARNIER, LIVE]
    first = items[0]
    assert first["title"] == "What we know about Krakow mass shooting so far"
    assert first["summary"].startswith("At least six people were killed")
    assert "at_medium" in first["link"]                      # the link posted is the real one
    assert first["guid"] == KRAKOW                           # the #1 on BBC guids is dropped
    assert first["published"] == N.parse_date("Sat, 07 Nov 2026 18:22:11 GMT")


def test_items_without_a_link_are_dropped():
    assert all(i["title"] != "Broken item with no link" for i in fixture("bbc_top.xml"))


def test_guardian_html_descriptions_become_plain_text():
    item = fixture("guardian.xml")[0]
    assert "<" not in item["summary"] and "&#8220;" not in item["summary"]
    assert "EU states would reject" in item["summary"]


def test_atom_feeds_parse_too():
    (item,) = fixture("atom.xml")
    assert item["title"] == "Chancellor sets out autumn budget"
    assert item["url"] == "https://example.com/budget"
    assert item["summary"] == "Tax and spending plans"
    assert item["published"] == N.parse_date("2026-11-07T12:30:00Z")


def test_bad_xml_gives_nothing_rather_than_raising():
    assert N.parse_feed(b"<rss><channel><item>") == []
    assert N.parse_feed(b"") == []
    assert N.parse_feed("not xml at all") == []


def test_url_rules():
    assert N.normalise_url("http://WWW.BBC.CO.UK/news/x/?at_medium=RSS#1") == "https://www.bbc.co.uk/news/x"
    assert N.normalise_url("https://www.bbc.com/news/x") == "https://www.bbc.co.uk/news/x"
    assert N.is_live_url(LIVE) and not N.is_live_url(KRAKOW)
    assert N.is_postable_url(KRAKOW) and N.is_postable_url(LIVE)
    assert not N.is_postable_url("https://www.bbc.co.uk/news/videos/c0abc123")
    assert not N.is_postable_url("https://www.bbc.co.uk/sounds/play/x")
    assert not N.is_postable_url("https://news.sky.com/story/x")
    assert N.is_sport_url("https://www.bbc.co.uk/sport/football/123")


# ------------------------------------------------------------ tracking, dedupe and scoring

def test_only_bbc_stories_are_tracked_and_others_kept_for_cross_checking():
    state = tracked()
    assert KRAKOW in state["stories"] and LIVE in state["stories"]
    assert all("bbc.co.uk" in u for u in state["stories"])
    assert {o["outlet"] for o in state["others"].values()} == {"sky", "guardian"}


def test_time_in_top_stories_is_tracked_from_first_to_last_seen():
    state = tracked(polls=13)  # two hours of polls
    entry = state["stories"][KRAKOW]
    assert entry["top_first"] == NOW and entry["top_last"] == NOW + 12 * POLL
    assert N.hours_in_top(entry) == 2.0
    assert entry["first_seen"] == NOW and entry["feeds"] == ["bbc_top"]


def test_dedupe_by_url_and_guid_persists_through_the_state():
    state = tracked()
    assert not N.already_posted(state, KRAKOW)
    N.mark_posted(state, KRAKOW, KRAKOW, NOW, 123, "new")
    assert N.already_posted(state, KRAKOW)
    N.mark_posted(state, BARNIER, "bbc-guid-42", NOW, 124, "new")
    assert N.already_posted(state, "https://other/url", "bbc-guid-42")
    N.prune(state, NOW + 29 * 86400)
    assert N.already_posted(state, KRAKOW)
    N.prune(state, NOW + 31 * 86400)
    assert not N.already_posted(state, KRAKOW)


def test_cross_outlet_matching_on_headlines():
    state = tracked()
    assert N.cross_outlets(state["stories"][KRAKOW], state["others"]) == {"sky"}
    assert N.cross_outlets(state["stories"][BARNIER], state["others"]) == {"guardian"}
    assert N.same_story_heuristic("Six killed in Krakow shooting", "Krakow mass shooting: what we know")
    assert not N.same_story_heuristic("Labour sets out housing plan", "Labour MP suspended over remarks")


def test_signal_score_weights():
    state = tracked()
    fresh = state["stories"][BARNIER]
    assert N.signal_score(fresh, NOW, set(), POLL) == (2, ["BBC top"])
    score, reasons = N.signal_score(state["stories"][LIVE], NOW, set(), POLL)
    assert score == 5 and "live page" in reasons
    assert N.signal_score(dict(fresh, feeds=["bbc_top", "bbc_politics"]), NOW, set(), POLL)[0] == 2
    long_running = dict(fresh, top_first=NOW - 3 * 3600, top_last=NOW, feeds=["bbc_top", "bbc_politics", "bbc_uk"])
    score, reasons = N.signal_score(long_running, NOW, {"sky", "guardian"}, POLL)
    assert score == 2 + 3 + 4 + 1
    capped = dict(long_running, top_first=NOW - 10 * 3600)
    assert N.signal_score(capped, NOW, {"sky", "guardian"}, POLL)[0] == 2 + 4 + 4 + 1


def test_a_story_that_left_top_stories_loses_the_top_point():
    entry = {"top_first": NOW - 7200, "top_last": NOW - 3600, "url": KRAKOW, "feeds": ["bbc_top"]}
    score, reasons = N.signal_score(entry, NOW, set(), POLL)
    assert "BBC top" not in reasons and score == 1


def test_signal_thresholds_from_config(monkeypatch):
    monkeypatch.setattr(N.config, "NEWSFEED_MIN_SIGNAL", 9)
    th = N.Thresholds()
    verdict = {"importance": 10, "newsworthy": True, "tag": "World"}
    assert N.decide(8, verdict, th)[0] == "skip"
    assert N.decide(9, verdict, th)[0] == "new"


def test_old_articles_are_not_fresh():
    assert N.is_fresh({"published": NOW - 3600}, NOW, 18)
    assert not N.is_fresh({"published": NOW - 19 * 3600}, NOW, 18)
    assert N.is_fresh({"first_seen": NOW - 60}, NOW, 18)


# ------------------------------------------------------------ caps, backoff, text

def test_daily_cap_counts_per_uk_day():
    state = N.empty_state()
    for _ in range(8):
        assert N.cap_allows(state, "daily", NOW, 8)
        N.bump_cap(state, "daily", NOW)
    assert not N.cap_allows(state, "daily", NOW, 8)
    assert N.cap_allows(state, "daily_updates", NOW, 8)      # updates have their own count
    assert N.cap_allows(state, "daily", NOW + 86400, 8)       # a new day starts again


def test_uk_day_follows_london_time_not_utc():
    summer_late = N.parse_date("2026-07-01T23:30:00Z")        # 00:30 BST on 2 July
    assert N.uk_day(summer_late) == "2026-07-02"
    winter_late = N.parse_date("2026-11-07T23:30:00Z")
    assert N.uk_day(winter_late) == "2026-11-07"


def test_failing_feeds_back_off_and_cap():
    assert N.backoff_seconds(1, 600) == 0
    assert N.backoff_seconds(2, 600) == 600
    assert N.backoff_seconds(3, 600) == 1200
    assert N.backoff_seconds(20, 600) == 7200


def test_titles_fit_discords_limit():
    assert N.trim_title("Short headline") == "Short headline"
    long = "Prime minister announces " + "a very long list of sweeping policy changes " * 4
    trimmed = N.trim_title(long)
    assert len(trimmed) <= 100 and trimmed.endswith("…")
    assert not trimmed[:-1].endswith(" ")
    assert len(N.trim_title("x" * 250)) == 100
    assert N.trim_title("  spaced   out  ") == "spaced out"
    assert N.trim_title("") == "News"


def test_ai_text_is_cleaned_of_pings():
    assert N.clean_text("Hi @everyone <@123> and <@&456>", 100) == "Hi and"
    assert len(N.clean_text("word " * 100, 50)) <= 50


def test_editorial_tails_are_trimmed_from_why_it_matters():
    assert N.strip_commentary("Two men were arrested after a sword attack, highlighting a serious crime incident.") \
        == "Two men were arrested after a sword attack."
    assert N.strip_commentary("Rates rose to 5%.") == "Rates rose to 5%."
    assert N.strip_commentary("") == ""


def test_post_body_matches_the_forum_style():
    body = N.build_post_body("https://bbc.co.uk/news/x", "It matters.")
    lines = body.split("\n\n")
    assert lines[0] == "https://bbc.co.uk/news/x"
    assert lines[1] == "It matters."
    assert len(lines) == 3
    assert lines[2].startswith("-# Posted automatically from BBC News")
    assert N.build_update_body("Suspect named", "https://l").startswith("**Update:** Suspect named\nhttps://l")


# ------------------------------------------------------------ the AI verdict and the decision

TAGS = ["UK Politics", "World", "Sport", "Crime & Courts"]


def test_verdict_is_checked():
    v = N.normalise_verdict({"importance": "14", "newsworthy": "true", "tag": "world", "same_story_post": "999",
                             "why": "Six people died. @everyone", "question": "", "reason": "major"}, TAGS, ["111"])
    assert v["importance"] == 10 and v["newsworthy"] is True and v["tag"] == "World"
    assert v["same_story_thread"] is None                    # only a post it was shown
    assert "@everyone" not in v["why"]
    assert N.normalise_verdict("nonsense", TAGS, [])["error"]
    assert N.normalise_verdict({"tag": "Made Up"}, TAGS, [])["tag"] is None


def test_decision_rules():
    th = N.Thresholds(min_signal=5, update_min_signal=4, min_importance=7, update_min_importance=5,
                      sport_min_importance=9)
    strong = {"importance": 8, "newsworthy": True, "tag": "UK Politics"}
    assert N.decide(6, strong, th)[0] == "new"
    assert N.decide(4, strong, th)[0] == "skip"               # signal too weak for a new post
    assert N.decide(6, dict(strong, importance=6), th)[0] == "skip"
    assert N.decide(6, dict(strong, newsworthy=False), th)[0] == "skip"
    assert N.decide(6, dict(strong, tag="Sport"), th)[0] == "skip"
    assert N.decide(6, dict(strong, tag="Sport", importance=9), th)[0] == "new"
    assert N.decide(6, strong, th, sport=True)[0] == "skip"
    same = dict(strong, importance=5, same_story_thread="111")
    assert N.decide(4, same, th)[0] == "update"
    assert N.decide(4, dict(same, importance=4), th)[0] == "skip"   # same story, too minor: never a new post
    assert N.decide(9, {"error": "timeout"}, th)[0] == "skip"


def _stub(reply):
    calls = []

    def complete(system, payload):
        calls.append(payload)
        return reply, 10, 10
    return complete, calls


def test_update_vs_new_with_the_ai_stubbed():
    state = tracked()
    entry = dict(state["stories"][LIVE], url=LIVE)
    member_post = {"id": "555", "title": "Krakow shooting", "summary": "Gunman kills six in Poland", "own": False}
    unrelated = {"id": "556", "title": "Budget: what it means for you", "summary": "", "own": False}
    shown = N.candidate_threads(entry, [member_post, unrelated])
    assert [t["id"] for t in shown] == ["555"]                # only the close one goes to the AI

    th = N.Thresholds(min_signal=5, update_min_signal=4, min_importance=7, update_min_importance=5)
    complete, calls = _stub({"importance": 9, "newsworthy": True, "tag": "World", "same_story_post": "555",
                             "why": "Six people were killed.", "question": "", "reason": "same event"})
    verdict = asyncio.run(N.judge_story(entry, 5, ["live page"], shown, TAGS, complete=complete))
    assert '"id": "555"' in calls[0] and "Krakow shooting" in calls[0]
    assert N.decide(5, verdict, th) == ("update", "same story, importance 9")

    complete, _ = _stub({"importance": 9, "newsworthy": True, "tag": "World", "same_story_post": None,
                         "why": "x", "question": "y", "reason": "new"})
    verdict = asyncio.run(N.judge_story(entry, 5, ["live page"], shown, TAGS, complete=complete))
    assert N.decide(5, verdict, th)[0] == "new"


def test_a_failed_ai_call_never_posts():
    def broken(system, payload):
        raise RuntimeError("timeout")
    verdict = asyncio.run(N.judge_story({"title": "t", "url": KRAKOW}, 9, [], [], TAGS, complete=broken))
    assert verdict["error"] and N.decide(9, verdict, N.Thresholds())[0] == "skip"


def test_a_member_post_already_linking_the_article_is_found():
    text = "Big news <https://www.bbc.co.uk/news/articles/cmd7q15drwgeo?at_medium=RSS> what do we think?"
    post = {"id": "777", "urls": sorted(N.urls_in(text)), "own": False}
    assert N.thread_linking(KRAKOW, [post])["id"] == "777"
    assert N.thread_linking(BARNIER, [post]) is None


def test_rejudging_is_limited():
    th = N.Thresholds(min_importance=7, max_judgements=3)
    assert N.needs_judgement({}, 5, [], th)
    judged = {"verdict": {"importance": 6}, "judgements": 1, "judged_signal": 5, "judged_threads": ["1"]}
    assert not N.needs_judgement(judged, 6, [{"id": "1"}], th)       # hasn't climbed enough
    assert N.needs_judgement(judged, 7, [{"id": "1"}], th)           # borderline and climbing
    assert N.needs_judgement(judged, 5, [{"id": "2"}], th)           # a new open post to compare with
    dull = dict(judged, verdict={"importance": 2})
    assert not N.needs_judgement(dull, 9, [{"id": "1"}], th)         # clearly dull stays rejected
    assert not N.needs_judgement(dict(judged, judgements=3), 9, [{"id": "2"}], th)


def test_update_gap_per_post():
    assert N.thread_gap_ok({"created": NOW - 3600, "last_update": None}, NOW, 45)
    assert not N.thread_gap_ok({"created": NOW - 3600, "last_update": NOW - 600}, NOW, 45)


def test_engagement_snapshots_fall_due_at_24h_and_72h():
    eng = {"1": {"created": NOW}, "2": {"created": NOW - 80 * 3600, "h24": {}}}
    assert N.due_snapshots(eng, NOW + 3600) == [("2", "h72")]
    assert sorted(N.due_snapshots(eng, NOW + 25 * 3600)) == [("1", "h24"), ("2", "h72")]


# ------------------------------------------------------------ one whole poll against a fake forum

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


class FakeThread:
    def __init__(self, tid, name, content="", created=NOW - 3600, locked=False, archived=False):
        self.id, self.name, self.locked, self.archived = tid, name, locked, archived
        self.created_at = datetime.fromtimestamp(created, timezone.utc)
        self.flags = SimpleNamespace(pinned=False)
        self.sent, self.edits = [], []
        self.starter_message = SimpleNamespace(content=content, embeds=[], reactions=[])
        self.message_count = 3

    async def send(self, content, **kw):
        self.sent.append(content)

    async def edit(self, **kw):
        self.edits.append(kw)
        if "pinned" in kw:
            self.flags.pinned = kw["pinned"]

    async def fetch_message(self, mid):
        return self.starter_message


class FakeForum:
    def __init__(self, threads=(), archived=()):
        self.id = N.config.NEWSFEED_FORUM_ID
        self.available_tags = [SimpleNamespace(name=n) for n in TAGS + [N.MEGATHREAD_TAG]]
        self.threads = list(threads)
        self._archived = list(archived)
        self.created = []

    async def archived_threads(self, limit=100):
        for t in self._archived:
            yield t

    async def create_thread(self, name, content, applied_tags, reason=None):
        thread = FakeThread(9000 + len(self.created), name, content, created=NOW)
        self.created.append((name, content, [t.name for t in applied_tags]))
        self.threads.append(thread)
        return SimpleNamespace(thread=thread, message=thread.starter_message)


class FakeClient:
    def __init__(self, forum):
        self.forum = forum

    def get_channel(self, cid):
        if cid == self.forum.id:
            return self.forum
        return next((t for t in self.forum.threads + self.forum._archived if t.id == int(cid)), None)

    async def fetch_channel(self, cid):
        return self.get_channel(cid)


@pytest.fixture
def poll(tmp_path, monkeypatch):
    monkeypatch.setattr(N.config, "NEWSFEED_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(N.config, "NEWSFEED_ENGAGEMENT_FILE", str(tmp_path / "eng.json"))
    monkeypatch.setattr(N.config, "NEWSFEED_DRY_RUN", False)
    monkeypatch.setattr(N.config, "NEWSFEED_FEEDS", FEEDS)
    results = {"bbc_top": fixture("bbc_top.xml"), "sky_home": fixture("sky_home.xml"),
               "guardian_politics": fixture("guardian.xml")}

    async def fake_fetch(state, now, feeds=None):
        return results
    monkeypatch.setattr(N, "fetch_feeds", fake_fetch)

    def run(forum, verdicts, state=None, now=NOW):
        """verdicts: headline word -> AI reply; anything else is judged dull."""
        state = state or N.empty_state()

        def complete(system, payload):
            for word, reply in verdicts.items():
                if word in payload.split('"open_posts"')[0]:
                    return reply, 1, 1
            return {"importance": 2, "newsworthy": False, "tag": "World", "same_story_post": None}, 1, 1
        summary = asyncio.run(N.run_once(FakeClient(forum), state, now=now, judge=complete))
        return state, summary
    return run


def _aged_state():
    """The fixtures have already been in BBC top stories for two hours."""
    state = N.empty_state()
    results = {"bbc_top": fixture("bbc_top.xml"), "sky_home": fixture("sky_home.xml"),
               "guardian_politics": fixture("guardian.xml")}
    N.observe(state, results, FEEDS, NOW - 7200)
    return state


BIG = {"importance": 9, "newsworthy": True, "tag": "World", "same_story_post": None,
       "why": "Six people were killed in Poland's second city.", "question": "How should Europe respond?"}


def test_a_strong_story_opens_one_tagged_post(poll):
    forum = FakeForum()
    state, summary = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, _aged_state())
    assert len(forum.created) == 1
    name, content, tags = forum.created[0]
    assert name == "UK cannot 'cherry pick' EU single market deal, Barnier tells BBC" and tags == ["UK Politics"]
    assert content.startswith("https://www.bbc.co.uk/news/articles/c9kgjj206nzpo?at_medium=RSS")
    assert N.already_posted(state, BARNIER)
    assert N.load_engagement()["9000"]["url"] == BARNIER
    state, _ = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, state, now=NOW + 600)
    assert len(forum.created) == 1                             # never posted twice


def test_a_live_page_becomes_a_pinned_megathread(poll):
    forum = FakeForum()
    poll(forum, {"Live updates": BIG}, _aged_state())
    name, _, tags = forum.created[0]
    assert "Live updates" in name and tags == ["World", "Megathread"]
    assert forum.threads[0].flags.pinned


def test_a_member_post_on_the_same_story_gets_the_update(poll):
    member = FakeThread(555, "Krakow mass shooting", "Gunman kills six people in Krakow, Poland. Thoughts?",
                        archived=True)
    forum = FakeForum(archived=[member])
    state, summary = poll(forum, {"Live updates": dict(BIG, same_story_post="555")}, _aged_state())
    assert forum.created == []                                 # no duplicate post
    assert member.sent and member.sent[0].startswith("**Update:** Krakow shooting: Live updates")
    assert member.edits == []                                  # a member's post is never edited, even to unarchive
    assert state["posted"][LIVE]["thread_id"] == "555"


def test_a_member_post_that_already_links_the_article_is_left_alone(poll):
    member = FakeThread(556, "Barnier on the single market",
                        "https://www.bbc.co.uk/news/articles/c9kgjj206nzpo what do we reckon")
    forum = FakeForum(threads=[member])
    state, _ = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, _aged_state())
    assert forum.created == [] and member.sent == []
    assert state["posted"][BARNIER]["kind"] == "already linked"


def test_locked_posts_are_not_matched(poll):
    member = FakeThread(557, "Krakow mass shooting", "Gunman kills six in Krakow", locked=True)
    forum = FakeForum(threads=[member])
    poll(forum, {"Live updates": dict(BIG, same_story_post="557")}, _aged_state())
    assert member.sent == [] and len(forum.created) == 1       # the AI was never offered the locked post


def test_dry_run_posts_nothing(poll):
    forum = FakeForum()
    state = _aged_state()
    state["dry_run"] = True
    state, summary = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, state)
    assert forum.created == [] and summary["dry"] and summary["posted"]
    assert state["daily"] == {} and sum(state["daily_dry"].values()) == 1


def test_the_daily_cap_holds(poll, monkeypatch):
    monkeypatch.setattr(N.config, "NEWSFEED_DAILY_CAP", 0)
    forum = FakeForum()
    state, _ = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, _aged_state())
    assert forum.created == [] and not N.already_posted(state, BARNIER)


def test_post_body_is_just_link_and_description():
    body = N.build_post_body("https://bbc.co.uk/news/x", "Six people were killed.")
    assert body.startswith("https://bbc.co.uk/news/x\n\nSix people were killed.")
    assert "?" not in body.split("\n\n")[1]
    assert '"question"' not in N.JUDGE_PROMPT


def test_new_post_never_includes_a_question(poll):
    forum = FakeForum()
    poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, _aged_state())
    _, content, _ = forum.created[0]
    assert "How should Europe respond?" not in content
