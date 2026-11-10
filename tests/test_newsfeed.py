"""News forum auto-poster: the pure parts (parsing, tracking, clustering, scoring, caps, matching, the decision, the
neutrality filter) and whole polls against a fake forum, without Discord, the network or a real AI call."""
import asyncio
import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.features import newsfeed as N

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "newsfeed")
FEEDS = {
    "bbc_top": ("u", "bbc", "top"),
    "bbc_world": ("u", "bbc", "section"),
    "sky_home": ("u", "sky", "top"),
    "sky_politics": ("u", "sky", "section"),
    "guardian_politics": ("u", "guardian", "section"),
    "independent_top": ("u", "independent", "section"),
}
NOW = 1_794_100_000.0  # Sat 7 Nov 2026, about 19:06 UTC
POLL = 600
KRAKOW = "https://www.bbc.co.uk/news/articles/cmd7q15drwgeo"
BARNIER = "https://www.bbc.co.uk/news/articles/c9kgjj206nzpo"
LIVE = "https://www.bbc.co.uk/news/live/c4g8xk2p1zzt"
SKY_KRAKOW = "https://news.sky.com/story/six-killed-in-krakow-mass-shooting-13609300"
GUARDIAN_BARNIER = "https://www.theguardian.com/politics/2026/nov/07/barnier-uk-cherry-pick-eu-single-market"
INDY_KRAKOW = "https://www.independent.co.uk/news/world/europe/krakow-shooting-poland-gunman-b3081001.html"
SKY_WATER = "https://news.sky.com/story/water-bills-to-rise-9-in-april-as-ofwat-confirms-increase-13609310"
INDY_WATER = "https://www.independent.co.uk/news/uk/home-news/water-bills-rise-april-ofwat-b3081002.html"


def fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as f:
        return N.parse_feed(f.read())


def all_results():
    return {"bbc_top": fixture("bbc_top.xml"), "sky_home": fixture("sky_home.xml"),
            "sky_politics": fixture("sky_politics.xml"), "guardian_politics": fixture("guardian.xml"),
            "independent_top": fixture("independent.xml")}


def tracked(polls=1, start=NOW):
    """A state after `polls` polls of the fixtures, ten minutes apart, with the articles grouped into stories."""
    state = N.empty_state()
    results = all_results()
    for i in range(polls):
        N.observe(state, results, FEEDS, start + i * POLL)
    N.assign_clusters(state)
    return state


def story_of(state, url):
    return state["stories"][url]["cluster"]


def outlets_of(state, url):
    return sorted(N.cluster_outlets(state, story_of(state, url)))


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
    assert N.parse_feed(b"\x1f\x8bnot really gzip") == []


def test_independent_feed_parses_even_gzipped():
    with open(os.path.join(FIXTURES, "independent.xml"), "rb") as f:
        raw = f.read()
    items = N.parse_feed(gzip.compress(raw))                   # the Independent gzips whatever it's asked
    assert items == N.parse_feed(raw) and len(items) == 4
    assert items[0]["url"] == INDY_KRAKOW
    assert items[1]["summary"] == "Households in England and Wales will pay an average of £54 more a year."


def test_url_rules():
    assert N.normalise_url("http://WWW.BBC.CO.UK/news/x/?at_medium=RSS#1") == "https://www.bbc.co.uk/news/x"
    assert N.normalise_url("https://www.bbc.com/news/x") == "https://www.bbc.co.uk/news/x"
    assert N.is_live_url(LIVE) and not N.is_live_url(KRAKOW)
    assert N.is_live_url("https://www.theguardian.com/politics/live/2026/nov/09/uk-politics-latest")
    assert not N.is_live_url("https://www.theguardian.com/politics/2026/nov/09/live-music-venues")
    assert N.is_postable_url(KRAKOW) and N.is_postable_url(LIVE)
    assert not N.is_postable_url("https://www.bbc.co.uk/news/videos/c0abc123")
    assert not N.is_postable_url("https://www.bbc.co.uk/sounds/play/x")
    assert N.is_postable_url(SKY_KRAKOW) and N.is_postable_url(GUARDIAN_BARNIER) and N.is_postable_url(INDY_WATER)
    assert not N.is_postable_url("https://news.sky.com/video/x-123")
    assert not N.is_postable_url("https://www.theguardian.com/commentisfree/2026/nov/07/opinion")
    assert not N.is_postable_url("https://www.independent.co.uk/voices/x-b1.html")
    assert not N.is_postable_url("https://www.independent.co.uk/life-style/x-b1.html")
    assert not N.is_postable_url("https://www.example.com/news/x")
    assert N.is_sport_url("https://www.bbc.co.uk/sport/football/123")
    assert N.is_sport_url("https://www.theguardian.com/football/2026/nov/07/x")
    assert N.outlet_of(SKY_KRAKOW) == "sky" and N.outlet_of(INDY_WATER) == "independent"
    assert N.outlet_of("https://www.telegraph.co.uk/news/2026/11/07/x/") == "telegraph"


# ------------------------------------------------------------ tracking, dedupe and scoring

def test_every_outlet_is_tracked_but_video_and_opinion_are_not():
    state = tracked()
    assert {KRAKOW, LIVE, SKY_KRAKOW, GUARDIAN_BARNIER, INDY_KRAKOW, SKY_WATER} <= set(state["stories"])
    assert {e["outlet"] for e in state["stories"].values()} == {"bbc", "sky", "guardian", "independent"}
    assert not any("/video" in u or "/voices/" in u for u in state["stories"])
    assert not any("Fog rolls" in e["title"] for e in state["stories"].values())   # BBC video


def test_an_old_bbc_only_state_loads(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setattr(N.config, "NEWSFEED_STATE_FILE", str(path))
    old = N.empty_state()
    old.pop("clusters")
    old["others"] = {SKY_KRAKOW: {"outlet": "sky", "title": "t"}}
    old["stories"] = {KRAKOW: {"title": "Krakow", "feeds": ["bbc_top"], "verdict": {"importance": 6}, "judgements": 1},
                      "https://www.bbc.co.uk/news/videos/c0abc": {"title": "Watch"}}
    N.save_state(old)
    state = N.load_state()
    assert "others" not in state and state["clusters"] == {}
    assert list(state["stories"]) == [KRAKOW] and state["stories"][KRAKOW]["outlet"] == "bbc"
    N.assign_clusters(state)
    cluster = state["clusters"][story_of(state, KRAKOW)]
    assert cluster["verdict"] == {"importance": 6} and cluster["judgements"] == 1   # its judgement comes along


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
    assert N.same_story_heuristic("Six killed in Krakow shooting", "Krakow mass shooting: what we know")
    assert not N.same_story_heuristic("Labour sets out housing plan", "Labour MP suspended over remarks")
    assert N.same_story_heuristic("UK cannot 'cherry pick' EU single market deal, Barnier tells BBC",
                                  "Barnier warns UK it cannot cherry pick from EU single market")


def test_the_same_event_across_outlets_becomes_one_story():
    state = tracked()
    assert outlets_of(state, KRAKOW) == ["bbc", "sky"]
    assert story_of(state, SKY_KRAKOW) == story_of(state, KRAKOW) == KRAKOW      # the story keeps the first article's id
    assert outlets_of(state, BARNIER) == ["bbc", "guardian"]
    assert outlets_of(state, SKY_WATER) == ["independent", "sky"]                 # no BBC needed for a story
    assert story_of(state, INDY_WATER) == story_of(state, SKY_WATER)
    # At most one article per outlet: the BBC's live page is its own story, never folded into the BBC article.
    assert story_of(state, LIVE) != story_of(state, KRAKOW)
    assert outlets_of(state, INDY_KRAKOW) == ["bbc", "independent"]               # its best match was the live page
    fog = next(u for u, e in state["stories"].items() if "fog" in e["title"].lower())
    assert outlets_of(state, fog) == ["sky"]


def test_stories_keep_their_articles_across_polls():
    state = tracked(polls=3)
    assert len(state["clusters"]) == 5
    link = "https://www.theguardian.com/world/2026/nov/07/krakow-shooting"
    late = {"title": "Krakow mass shooting: Six killed by gunman in Poland", "summary": "", "link": link,
            "url": N.normalise_url(link), "guid": "", "published": NOW}
    N.observe(state, {"guardian_politics": [late]}, FEEDS, NOW + 3 * POLL)
    joins = N.assign_clusters(state)
    assert joins == [(late["url"], KRAKOW)]
    assert outlets_of(state, KRAKOW) == ["bbc", "guardian", "sky"]


def test_a_second_outlet_on_a_posted_story_is_recorded_against_that_post():
    state = tracked()
    N.mark_story_posted(state, N.cluster_entries(state, KRAKOW)[:1], NOW, 4242, "new", KRAKOW)
    assert not N.already_posted(state, SKY_KRAKOW)
    N.sync_posted(state, NOW)
    assert state["posted"][SKY_KRAKOW] == {"ts": NOW, "thread_id": "4242", "kind": "also covered"}
    assert state["clusters"][KRAKOW]["done"]


def test_near_matches_go_to_the_ai_and_merge_when_it_agrees():
    state = N.empty_state()
    indy = {"title": "Chancellor confirms fuel duty freeze in autumn budget statement", "summary": "",
            "link": "https://www.independent.co.uk/news/uk/politics/fuel-duty-b1.html"}
    guardian = {"title": "Reeves keeps fuel duty frozen in budget, sparing drivers", "summary": "",
                "link": "https://www.theguardian.com/politics/2026/nov/07/fuel-duty"}
    for item in (indy, guardian):
        item.update(url=N.normalise_url(item["link"]), guid="", published=NOW)
    N.observe(state, {"independent_top": [indy], "guardian_politics": [guardian]}, FEEDS, NOW)
    N.assign_clusters(state)
    assert 0.3 <= N.match_score(indy["title"], guardian["title"]) < 0.5           # borderline: not merged on words alone
    assert len(state["clusters"]) == 2
    g, i = story_of(state, guardian["url"]), story_of(state, indy["url"])
    assert [m for _, m in N.maybe_same(state, g)] == [i]
    assert N.merge_clusters(state, g, i, NOW)
    assert list(state["clusters"]) == [g] and outlets_of(state, indy["url"]) == ["guardian", "independent"]


def test_merging_into_a_posted_story_marks_the_whole_story_posted():
    state = tracked()
    water = story_of(state, SKY_WATER)
    N.mark_story_posted(state, N.cluster_entries(state, water), NOW, 77, "new", water)
    # A Guardian-only story the AI says is the same event: it joins the posted story and is never posted itself.
    link = "https://www.theguardian.com/business/2026/nov/07/water-bills"
    item = {"title": "Households face higher water charges next spring", "summary": "", "link": link,
            "url": N.normalise_url(link), "guid": "", "published": NOW}
    N.observe(state, {"guardian_politics": [item]}, FEEDS, NOW)
    N.assign_clusters(state)
    assert N.merge_clusters(state, water, story_of(state, item["url"]), NOW)
    assert state["posted"][item["url"]]["thread_id"] == "77"


def test_signal_score_weights():
    state = tracked()
    barnier = N.cluster_entries(state, story_of(state, BARNIER))
    bbc_only = [e for e in barnier if e["outlet"] == "bbc"]
    assert N.signal_score(bbc_only, NOW, POLL) == (2, ["front page BBC"])
    assert N.signal_score(barnier, NOW, POLL) == (4, ["front page BBC", "2 outlets"])
    live = N.cluster_entries(state, story_of(state, LIVE))
    score, reasons = N.signal_score(live, NOW, POLL)
    assert score == 2 + 3 + 2 and "live page" in reasons
    krakow = N.cluster_entries(state, story_of(state, KRAKOW))
    assert N.signal_score(krakow, NOW, POLL) == (2 + 1 + 2, ["front page BBC, Sky", "2 outlets"])
    long_running = [dict(e, top_first=NOW - 3 * 3600, top_last=NOW) for e in krakow]
    assert N.signal_score(long_running, NOW, POLL)[0] == 2 + 1 + 3 + 2
    capped = [dict(e, top_first=NOW - 10 * 3600) for e in long_running]
    assert N.signal_score(capped, NOW, POLL)[0] == 2 + 1 + 3 + 2
    many = [{"outlet": o, "url": f"https://x/{o}"} for o in ("bbc", "sky", "itv", "guardian", "independent", "telegraph")]
    assert N.signal_score(many, NOW, POLL) == (6, ["6 outlets"])                  # outlets capped at +6
    blog = {"outlet": "guardian", "url": "https://www.theguardian.com/politics/live/2026/nov/09/uk-politics-latest"}
    assert N.signal_score(blog, NOW, POLL) == (1, ["live blog"])                   # a rolling blog counts for less
    assert not N.is_megathread_url(blog["url"]) and N.is_megathread_url(LIVE)


def test_a_story_that_left_top_stories_loses_the_top_point():
    entry = {"top_first": NOW - 7200, "top_last": NOW - 3600, "url": KRAKOW, "feeds": ["bbc_top"], "outlet": "bbc"}
    score, reasons = N.signal_score(entry, NOW, POLL)
    assert not any("front page" == r[:10] for r in reasons) and score == 1


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


def test_commentary_from_live_posts_is_removed():
    # The two phrases that got through in the first live days.
    assert N.strip_commentary("Police are investigating drones seen over RAF Lakenheath, raising security concerns.") \
        == "Police are investigating drones seen over RAF Lakenheath."
    assert N.strip_commentary("Russia expelled two British diplomats, which indicates ongoing international tensions "
                              "and concerns.") == "Russia expelled two British diplomats."
    assert N.strip_commentary("Russia expelled two British diplomats. This indicates ongoing international tensions "
                              "and concerns.") == "Russia expelled two British diplomats."
    # A sentence that is nothing but commentary can't be cleaned, so the "why" goes rather than post loaded wording.
    assert N.strip_commentary("The expulsions indicate ongoing international tensions and concerns.") == ""
    assert N.strip_commentary("It is the end of an era for the town.") == ""
    assert N.strip_commentary("The vote highlights divisions in the party.") == ""


def test_loaded_adjectives_and_clauses_are_rephrased_or_dropped():
    assert N.strip_commentary("The Supreme Court issued a landmark ruling on the definition of a woman.") \
        == "The Supreme Court issued a ruling on the definition of a woman."
    assert N.strip_commentary("MPs passed a controversial bill on assisted dying. It goes to the Lords next.") \
        == "MPs passed a bill on assisted dying. It goes to the Lords next."
    assert N.strip_commentary("An unprecedented alert was issued for Wales.") == "An alert was issued for Wales."
    assert N.strip_commentary("The plant shuts in March, marking the end of an era for steelmaking.") \
        == "The plant shuts in March."
    assert N.strip_commentary("The fire spread to three homes, amid concerns over cladding.") \
        == "The fire spread to three homes."
    assert N.strip_commentary("The vote was delayed, raising questions about the timetable.") == "The vote was delayed."
    # Closing speculation the AI added in the multi-outlet dry check.
    assert N.strip_commentary("Nvidia's chief executive admitted an affair with a suspected Chinese spy. This could "
                              "have implications for company security and international relations.") \
        == "Nvidia's chief executive admitted an affair with a suspected Chinese spy."
    assert N.strip_commentary("Andy Burnham wants closer ties with the EU. This could influence future UK-EU relations "
                              "and domestic political dynamics.") == "Andy Burnham wants closer ties with the EU."
    # Plain facts that only look like it are left alone.
    for fact in ("Ofgem is raising the price cap to £1,800 from January.", "Thousands marched in London, amid heavy rain.",
                 "Three people are feared dead after the U.S. strike.", "The vote marks the bill's third reading."):
        assert N.strip_commentary(fact) == fact


def test_the_verdict_drops_a_why_it_cannot_clean():
    v = N.normalise_verdict({"importance": 8, "newsworthy": True, "tag": "World",
                             "why": "This indicates ongoing international tensions and concerns."}, TAGS, [])
    assert v["why"] == ""
    assert N.build_post_body("https://l", v["why"]).split("\n\n")[1].startswith("-# Posted automatically")
    v = N.normalise_verdict({"why": "Police are investigating drones over RAF Lakenheath, raising security concerns."},
                            TAGS, [])
    assert v["why"] == "Police are investigating drones over RAF Lakenheath."


# ------------------------------------------------------------ main link, also covered

def _e(outlet, url=None, **kw):
    url = url or {"bbc": KRAKOW, "sky": SKY_KRAKOW, "guardian": GUARDIAN_BARNIER, "independent": INDY_KRAKOW,
                  "itv": "https://www.itv.com/news/2026-11-07/krakow-shooting",
                  "telegraph": "https://www.telegraph.co.uk/world-news/2026/11/07/krakow-shooting"}[outlet]
    return dict({"outlet": outlet, "url": url, "link": url + "?ref=rss", "title": outlet}, **kw)


def test_main_link_prefers_the_most_neutral_outlet():
    pick = lambda *entries: N.main_entry(list(entries))["outlet"]
    assert pick(_e("guardian"), _e("sky"), _e("bbc"), _e("independent")) == "bbc"
    assert pick(_e("guardian"), _e("sky"), _e("independent")) == "sky"
    assert pick(_e("guardian"), _e("itv"), _e("telegraph")) == "itv"
    assert pick(_e("independent"), _e("guardian"), _e("telegraph")) == "guardian"
    assert pick(_e("telegraph"), _e("independent")) == "independent"
    assert pick(_e("telegraph")) == "telegraph"            # partly paywalled: only when nothing else has it


def test_a_paywalled_link_is_never_the_main_link(monkeypatch):
    premium = _e("independent", "https://www.independent.co.uk/independentpremium/news-analysis/x-b1.html")
    assert N.main_entry([premium, _e("telegraph")])["outlet"] == "telegraph"
    assert N.main_entry([premium]) is None
    monkeypatch.setitem(N.config.NEWSFEED_OUTLETS, "telegraph", ("the Telegraph", "Telegraph", "full"))
    assert N.main_entry([_e("telegraph")]) is None
    assert N.main_entry([_e("telegraph"), _e("guardian")])["outlet"] == "guardian"


def test_also_covered_lists_the_other_outlets_with_embeds_suppressed():
    entries = [_e("independent"), _e("bbc"), _e("telegraph"), _e("sky")]
    main = N.main_entry(entries)
    also = N.also_covered(entries, main)
    assert [name for name, _ in also] == ["Sky", "Independent", "Telegraph"]
    body = N.build_post_body(main["link"], "Six people were killed.", source="BBC News", also=also)
    parts = body.split("\n\n")
    assert parts[0] == KRAKOW + "?ref=rss"                                        # the one unwrapped link: it previews
    assert parts[1] == "Six people were killed."
    assert parts[2] == (f"-# Also covered by: [Sky](<{SKY_KRAKOW}?ref=rss>) · [Independent](<{INDY_KRAKOW}?ref=rss>)"
                        f" · [Telegraph](<{_e('telegraph')['url']}?ref=rss>)")
    assert parts[3].startswith("-# Posted automatically from BBC News.")
    assert len(parts) == 4 and "?" not in parts[1]
    sky = N.build_post_body(SKY_KRAKOW, "", source=N.outlet_label("sky"), also=[("Guardian", GUARDIAN_BARNIER)])
    assert sky.split("\n\n")[-1].startswith("-# Posted automatically from Sky News.")
    assert N.build_post_body("https://l", "x") == "https://l\n\nx\n\n" + N.FOOTER.format(source="BBC News")


def test_updates_name_the_outlet_they_link():
    body = N.build_update_body("Gunman named", SKY_KRAKOW, "Police named the suspect.", source="Sky News")
    assert body == f"**Update:** Gunman named (Sky News)\n{SKY_KRAKOW}\n-# Police named the suspect."


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
    # These tests are about the flow, not the tuning: pin the signal bars they were written against.
    monkeypatch.setattr(N.config, "NEWSFEED_MIN_SIGNAL", 5)
    monkeypatch.setattr(N.config, "NEWSFEED_UPDATE_MIN_SIGNAL", 4)
    current = {"results": all_results()}

    async def fake_fetch(state, now, feeds=None):
        return current["results"]
    monkeypatch.setattr(N, "fetch_feeds", fake_fetch)

    def run(forum, verdicts, state=None, now=NOW, results=None, calls=None):
        """verdicts: headline word -> AI reply; anything else is judged dull. results replaces the fixtures' feeds."""
        state = state or N.empty_state()
        current["results"] = results if results is not None else all_results()

        def complete(system, payload):
            if calls is not None:
                calls.append(payload)
            for word, reply in verdicts.items():
                if word in payload.split('"covered_by"')[0]:
                    return reply, 1, 1
            return {"importance": 2, "newsworthy": False, "tag": "World", "same_story_post": None}, 1, 1
        summary = asyncio.run(N.run_once(FakeClient(forum), state, now=now, judge=complete))
        return state, summary
    return run


def _aged_state(results=None):
    """The fixtures have already been on the front pages for two hours."""
    state = N.empty_state()
    N.observe(state, results or all_results(), FEEDS, NOW - 7200)
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


# ------------------------------------------------------------ several outlets, one post

def _item(title, link, summary=""):
    return {"title": title, "summary": summary, "link": link, "url": N.normalise_url(link), "guid": "",
            "published": NOW}


def test_a_strong_story_links_bbc_and_lists_the_other_outlets(poll):
    forum = FakeForum()
    poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, _aged_state())
    _, content, _ = forum.created[0]
    parts = content.split("\n\n")
    assert parts[0].startswith("https://www.bbc.co.uk/news/articles/c9kgjj206nzpo?at_medium=RSS")
    assert parts[2].startswith("-# Also covered by: [Guardian](<https://www.theguardian.com/politics/2026/nov/07/")
    assert parts[3].startswith("-# Posted automatically from BBC News.")


def test_two_outlets_on_one_event_make_one_post_linking_the_preferred_outlet(poll, monkeypatch):
    monkeypatch.setattr(N.config, "NEWSFEED_MIN_SIGNAL", 2)
    forum = FakeForum()
    state, summary = poll(forum, {"Water bills": dict(BIG, tag="UK Politics", why="Bills rise by £54 a year.")})
    water = [c for c in forum.created if "Water" in c[0]]
    assert len(water) == 1                                                        # Sky and the Independent: one post
    name, content, _ = water[0]
    assert name == "Water bills to rise 9% in April as Ofwat confirms increase"   # the linked outlet's headline
    assert content.split("\n\n") == [
        SKY_WATER,
        "Bills rise by £54 a year.",
        f"-# Also covered by: [Independent](<{INDY_WATER}>)",
        N.FOOTER.format(source="Sky News"),
    ]
    assert state["posted"][SKY_WATER]["thread_id"] == state["posted"][INDY_WATER]["thread_id"]
    rec = next(t for t in state["threads"] if t["url"] == SKY_WATER)
    assert rec["outlet"] == "sky" and set(rec["urls"]) == {SKY_WATER, INDY_WATER}


def test_a_story_is_never_posted_twice_through_another_outlet(poll):
    forum = FakeForum()
    state, _ = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, _aged_state())
    assert len(forum.created) == 1
    thread_id = state["posted"][BARNIER]["thread_id"]
    # Next poll the Independent runs it too.
    indy = _item("Barnier tells UK it cannot cherry pick from the EU single market",
                 "https://www.independent.co.uk/news/uk/politics/barnier-uk-cherry-pick-single-market-b3081100.html")
    later = dict(all_results(), independent_top=[indy])
    state, summary = poll(forum, {"Barnier": dict(BIG, tag="UK Politics")}, state, now=NOW + 600, results=later)
    assert len(forum.created) == 1 and summary["posted"] == []
    assert state["posted"][indy["url"]] == {"ts": NOW + 600, "thread_id": thread_id, "kind": "also covered"}


FUEL_BBC = ("Chancellor confirms fuel duty freeze in autumn budget statement",
            "https://www.bbc.co.uk/news/articles/cfuel1")
FUEL_GUARDIAN = ("Reeves keeps fuel duty frozen in budget, sparing drivers",
                 "https://www.theguardian.com/politics/2026/nov/07/fuel-duty")


def test_the_ai_settles_a_borderline_match_and_the_stories_merge(poll):
    bbc, guardian = _item(*FUEL_BBC), _item(*FUEL_GUARDIAN)
    sky = _item("Fuel duty freeze confirmed by chancellor in budget statement", "https://news.sky.com/story/fuel-1")
    results = {"bbc_top": [bbc], "sky_home": [sky], "guardian_politics": [guardian]}
    state = _aged_state(results)
    N.assign_clusters(state)
    assert outlets_of(state, bbc["url"]) == ["bbc", "sky"] and outlets_of(state, guardian["url"]) == ["guardian"]
    guardian_story = story_of(state, guardian["url"])
    forum, calls = FakeForum(), []
    verdict = dict(BIG, tag="UK Politics", why="Fuel duty stays at its current rate.", same_coverage=[guardian_story])
    state, summary = poll(forum, {"Chancellor": verdict}, state, results=results, calls=calls)
    # The AI was shown the Guardian article as a possible match, said it's the same event, and it joined the story.
    assert '"maybe_same_coverage": [{"id": "%s"' % guardian_story in calls[0]
    assert len(forum.created) == 1
    _, content, _ = forum.created[0]
    assert content.startswith(bbc["link"]) and "[Sky](<" in content and "[Guardian](<" in content
    assert outlets_of(state, guardian["url"]) == ["bbc", "guardian", "sky"]
    assert N.already_posted(state, guardian["url"])


def test_a_borderline_match_the_ai_rejects_stays_apart(poll):
    bbc, guardian = _item(*FUEL_BBC), _item(*FUEL_GUARDIAN)
    results = {"bbc_top": [bbc], "guardian_politics": [guardian]}
    forum, calls = FakeForum(), []
    state, _ = poll(forum, {"Chancellor": dict(BIG, tag="UK Politics", same_coverage=[])}, _aged_state(results),
                    results=results, calls=calls)
    # The BBC article alone (front page 2h: 4) is short of the bar of 5, but the Guardian near-match could have made
    # it 6, so the AI was asked. It said no: the stories stay apart, and the BBC one on its own isn't posted.
    assert len(calls) == 1 and '"maybe_same_coverage": [{"id"' in calls[0]
    assert story_of(state, bbc["url"]) != story_of(state, guardian["url"])
    assert forum.created == [] and not N.already_posted(state, guardian["url"])
    # It isn't asked again about the same near-match next poll.
    state, _ = poll(forum, {}, state, now=NOW + 600, results=results, calls=calls)
    assert len(calls) == 1


def test_an_update_can_come_from_another_outlet(poll):
    member = FakeThread(558, "Water bills going up 9%", "Ofwat says water bills will rise 9% in April")
    forum = FakeForum(threads=[member])
    later = {"sky_home": fixture("sky_politics.xml"), "independent_top": fixture("independent.xml")}
    state, _ = poll(forum, {"Water bills": dict(BIG, tag="UK Politics", same_story_post="558")}, _aged_state(later),
                    results=later)
    assert forum.created == []
    assert member.sent[0].startswith("**Update:** Water bills to rise 9% in April as Ofwat confirms increase "
                                     f"(Sky News)\n{SKY_WATER}")
    assert state["posted"][INDY_WATER]["thread_id"] == "558"


def test_status_shows_each_outlets_feeds():
    state = N.empty_state()
    state["feed_health"] = {"bbc_top": {"failures": 0}, "bbc_world": {"failures": 0},
                            "sky_home": {"failures": 2, "error": "HTTP 503"}, "sky_politics": {"failures": 0},
                            "guardian_politics": {"failures": 3, "error": "timeout"}}
    assert N.outlet_health_line(state, FEEDS) == "BBC ✅ · Sky ⚠️ 1/2 failing · Guardian ❌ · Independent ⏳"


def test_full_status_names_outlets_and_stories(monkeypatch):
    monkeypatch.setattr(N.config, "NEWSFEED_FEEDS", FEEDS)
    state = tracked()
    state["feed_health"] = {"sky_home": {"failures": 2, "error": "HTTP 503"}}
    state["clusters"][KRAKOW]["signal"] = 5
    text = N.status_text(dict(state, last_run=NOW), NOW)
    assert "Outlets: BBC ⏳ · Sky ❌" in text and "sky_home (2x: HTTP 503)" in text
    assert "What we know about Krakow mass shooting so far (BBC, Sky; signal 5)" in text
    assert "Tracking 9 articles in 5 stories." in text


def test_a_big_feed_is_read_to_the_end():
    class Content:
        def __init__(self, parts):
            self.parts = list(parts)

        async def read(self, n):
            return self.parts.pop(0)[:n] if self.parts else b""
    resp = SimpleNamespace(content=Content([b"<rss>", b"<channel>", b"</channel></rss>"]))
    assert asyncio.run(N._read_body(resp, 1000)) == b"<rss><channel></channel></rss>"
    resp = SimpleNamespace(content=Content([b"x" * 600, b"y" * 600]))
    assert len(asyncio.run(N._read_body(resp, 1000))) == 1001                     # over the cap: parse_feed refuses it


def test_mail_and_express_are_read_but_never_beat_neutral_outlets():
    import config
    assert config.NEWSFEED_OUTLETS["mail"][2] == "free" and config.NEWSFEED_OUTLETS["express"][2] == "free"
    order = config.NEWSFEED_MAIN_LINK_ORDER
    for neutral in ("bbc", "sky", "itv", "guardian", "independent"):
        assert order.index(neutral) < order.index("mail") < order.index("express") < order.index("telegraph")
    outlets = {o for _, o, _ in config.NEWSFEED_FEEDS.values()}
    assert {"mail", "express"} <= outlets


# ------------------------------------------------------------ multiple tags

MORE_TAGS = TAGS + ["Business & Economy", "Nations & Regions", "Education", "Environment", "Tech & Science"]


def test_a_verdict_can_carry_several_tags_primary_first():
    v = N.normalise_verdict({"importance": 8, "newsworthy": True, "tags": ["uk politics", "Business & Economy"]},
                            MORE_TAGS, [])
    assert v["tags"] == ["UK Politics", "Business & Economy"] and v["tag"] == "UK Politics"


def test_an_old_single_tag_answer_still_works():
    v = N.normalise_verdict({"importance": 8, "newsworthy": True, "tag": "world"}, MORE_TAGS, [])
    assert v["tags"] == ["World"] and v["tag"] == "World"
    assert N.normalise_verdict({"tags": "Sport"}, MORE_TAGS, [])["tags"] == ["Sport"]
    assert N.post_tags({"tag": "World"}) == ["World"]          # a verdict stored before tags were lists


def test_unknown_tags_are_dropped_deduped_and_capped():
    v = N.normalise_verdict({"tags": ["Made Up", "World", "world", "Megathread", "Sport", "Education",
                                      "Environment"]}, MORE_TAGS + [N.MEGATHREAD_TAG], [])
    assert v["tags"] == ["World", "Sport", "Education"]       # never Megathread, at most three
    v = N.normalise_verdict({"tags": ["Made Up"]}, MORE_TAGS, [])
    assert v["tags"] == [] and v["tag"] is None
    assert N.normalise_verdict({"tags": [], "tag": "Sport"}, MORE_TAGS, [])["tags"] == ["Sport"]


def test_the_sport_bar_applies_only_when_sport_is_the_primary_tag():
    th = N.Thresholds(min_signal=5, update_min_signal=4, min_importance=7, update_min_importance=5,
                      sport_min_importance=9)
    v = {"importance": 8, "newsworthy": True}
    assert N.decide(6, dict(v, tags=["Sport", "Business & Economy"], tag="Sport"), th)[0] == "skip"
    assert N.decide(6, dict(v, tags=["Business & Economy", "Sport"], tag="Business & Economy"), th)[0] == "new"
    assert N.decide(6, dict(v, tag="Sport"), th)[0] == "skip"   # an older stored verdict


def test_forum_tags_keep_order_and_the_megathread_within_discords_cap():
    forum = SimpleNamespace(available_tags=[SimpleNamespace(name=n) for n in MORE_TAGS + [N.MEGATHREAD_TAG]])

    def names(tags):
        return [t.name for t in tags]
    assert names(N._forum_tags(forum, ["Crime & Courts", "Nations & Regions"])) == ["Crime & Courts",
                                                                                     "Nations & Regions"]
    assert names(N._forum_tags(forum, ["World", "world", "Nope"])) == ["World"]
    many = ["World", "Sport", "Education", "Environment", "Tech & Science", N.MEGATHREAD_TAG]
    assert names(N._forum_tags(forum, many)) == ["World", "Sport", "Education", "Environment", N.MEGATHREAD_TAG]
    assert names(N._forum_tags(forum, ["Nope"])) == [MORE_TAGS[0]]   # the forum needs a tag


def test_the_prompt_defines_every_tag_and_asks_for_a_list():
    for name in ("World", "UK Politics", "Nations & Regions", "Local", "Business & Economy", "Elections",
                 "Crime & Courts", "Conflict & Defence", "Health", "Education", "Environment", "Tech & Science",
                 "Sport"):
        assert f"  {name}: " in N.JUDGE_PROMPT
    assert '"tags": [str]' in N.JUDGE_PROMPT and "US Politics" not in N.JUDGE_PROMPT
    assert '"tag": exactly one' not in N.JUDGE_PROMPT


def test_a_post_gets_every_tag_and_the_megathread_and_records_them(poll):
    forum = FakeForum()
    forum.available_tags = [SimpleNamespace(name=n) for n in MORE_TAGS + [N.MEGATHREAD_TAG]]
    state, _ = poll(forum, {"Live updates": dict(BIG, tags=["World", "Environment"])}, _aged_state())
    _, _, tags = forum.created[0]
    assert tags == ["World", "Environment", "Megathread"]
    rec = state["threads"][0]
    assert rec["tags"] == ["World", "Environment"] and rec["tag"] == "World"
    assert N.load_engagement()[rec["id"]]["tags"] == ["World", "Environment"]
