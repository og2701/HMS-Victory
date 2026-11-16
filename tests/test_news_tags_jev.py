import asyncio

from lib.features import news_tags_jev as J


def test_tags_over_the_line_most_likely_first_world_last_capped():
    probs = {"World": 0.99, "Conflict & Defence": 0.97, "Tech & Science": 0.8, "Business & Economy": 0.7,
             "Health": 0.59}
    assert J.pick_tags(probs) == ["Conflict & Defence", "Tech & Science", "Business & Economy"]
    assert J.pick_tags({"World": 0.98, "Crime & Courts": 0.9}) == ["Crime & Courts", "World"]
    assert J.pick_tags({"Health": 0.4}) == []


def test_only_known_tags_get_questions():
    qs = J.questions_for(["UK Politics", "Megathread", "Sport"])
    assert len(qs) == 2


def test_no_key_means_no_verdict(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    tags, probs = asyncio.run(J.jev_tags("Headline", "", "Sky News", ["UK Politics"]))
    assert tags is None and probs is None


def test_answers_become_tags(monkeypatch):
    async def fake_post(payload, key, *, session, timeout):
        return {"answers": {qid: {"noul": 0.9 if q["instructions"].startswith("Is this story about crime") else 0.1}
                            for qid, q in payload["questions"].items()}}
    import lib.features.mention_signals as M
    monkeypatch.setattr(M, "_post", fake_post)
    tags, probs = asyncio.run(J.jev_tags("Man jailed", "", "BBC News", ["Crime & Courts", "Health"], api_key="x"))
    assert tags == ["Crime & Courts"] and probs["Health"] == 0.1
