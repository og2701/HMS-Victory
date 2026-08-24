"""Coordinated messages, and established members behaving unlike themselves."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.core import behaviour_watch as B

NOW = 1_760_000_000
DAY = 86400


def setup_function():
    B.reset_windows()


def profile(days=90, messages=1000, links=0, now=NOW):
    return {"first_seen": now - days * DAY, "last_seen": now,
            "messages": messages, "link_messages": links}


# ---------------------------------------------------------------------------
# Normalisation - what makes two messages "the same"
# ---------------------------------------------------------------------------
def test_case_punctuation_and_spacing_are_ignored():
    assert B.normalise("FREE NITRO!!!  click   here") == B.normalise("free nitro click here")


def test_a_rotating_domain_still_matches():
    """Campaigns change the link per message; the sentence around it is the constant."""
    a = B.normalise("free nitro here https://scam-one.xyz/a")
    b = B.normalise("free nitro here https://other-two.ru/b")
    assert a == b and B.LINK_TOKEN in a


def test_the_link_marker_survives_punctuation_stripping():
    """It is built from word characters on purpose: an angle-bracket marker was being
    erased, which silently disabled the short-message-with-a-link case."""
    assert B.LINK_TOKEN in B.normalise("look https://scam.xyz/x")
    assert B.normalise("look https://scam.xyz/x") != B.normalise("look link")


def test_different_messages_do_not_collide():
    assert B.normalise("anyone up for overwatch") != B.normalise("free nitro click here")


# ---------------------------------------------------------------------------
# Detector 1: several accounts saying the same thing
# ---------------------------------------------------------------------------
def _post(uid, text, ch=1, at=NOW, established=False):
    return B.check_coordinated(uid, text, ch, "", f"user{uid}", now=at,
                               established=established)


def _post_many(text, n=None, start=1, **kw):
    """The same thing from n distinct accounts. Reads the threshold rather than hard-coding
    it, so turning the sensitivity dial does not mean rewriting every test."""
    n = B.COORD_MIN_AUTHORS if n is None else n
    last = None
    for uid in range(start, start + n):
        last = _post(uid, text, **kw)
    return last


def test_enough_accounts_posting_the_same_thing_is_a_finding():
    msg = "free discord nitro for everyone claim it now"
    for uid in range(1, B.COORD_MIN_AUTHORS):
        assert _post(uid, msg) is None, "fired below the threshold"
    finding = _post(B.COORD_MIN_AUTHORS, msg)
    assert finding and len(finding["authors"]) == B.COORD_MIN_AUTHORS


def test_one_person_repeating_themselves_is_not_a_raid():
    msg = "has anyone seen my keys anywhere in this server"
    for _ in range(5):
        assert _post(7, msg) is None


def test_common_chat_never_fires():
    """Four people typing 'welcome' when somebody joins is the whole point of the server."""
    for phrase in ("welcome", "gm", "lol", "happy birthday"):
        B.reset_windows()
        for uid in (1, 2, 3, 4):
            assert _post(uid, phrase) is None, phrase


def test_short_text_is_ignored_unless_it_carries_a_link():
    assert _post_many("sounds good") is None
    B.reset_windows()
    assert _post_many("look https://scam.xyz/x") is not None, \
        "a short message with a link is exactly the case to catch"


def test_a_reaction_emoji_everybody_uses_is_not_a_raid():
    """What actually tripped this in #general. A custom emoji arrives as
    <a:name:1540334892173733599>, and stripping punctuation left a thirty-four character
    run that read as a shared sentence."""
    assert _post_many("<a:bouncy_yaris:1540334892173733599>") is None
    B.reset_windows()
    assert _post_many("<:pepe:123456789012345678>") is None


def test_unicode_emoji_and_mentions_are_not_wording_either():
    for text in ("😂😂😂", "<@1234567890123456> <@2345678901234567>", "🇬🇧🇬🇧", "<#999888777666555>"):
        B.reset_windows()
        assert _post_many(text) is None, text


def test_the_same_line_dressed_in_different_emoji_still_matches():
    """The other side of stripping them: decorating a scam line with a different emoji per
    account must not split one campaign into separate findings."""
    base = "free discord nitro for everyone claim it now"
    for uid in range(1, B.COORD_MIN_AUTHORS):
        assert _post(uid, f"{base} 🎉") is None
    assert _post(B.COORD_MIN_AUTHORS, f"{base} 😀") is not None


def test_regulars_landing_on_the_same_line_are_not_counted():
    """A month here and two hundred messages is not something a farmed account has, so a
    crowd of regulars saying the same thing is a meme. A regular who has actually been
    taken over is check_takeover's problem, and that one requires tenure rather than
    excluding it."""
    msg = "free discord nitro for everyone claim it now"
    assert _post_many(msg, n=B.COORD_MIN_AUTHORS + 3, established=True) is None


def test_one_regular_among_new_accounts_does_not_hide_them():
    msg = "free discord nitro for everyone claim it now"
    assert _post(99, msg, established=True) is None
    assert _post_many(msg) is not None, "the new accounts still have to be reported"


def test_the_window_expires():
    msg = "identical wording posted a long way apart"
    for uid in range(1, B.COORD_MIN_AUTHORS):
        _post(uid, msg, at=NOW + uid)
    assert _post(B.COORD_MIN_AUTHORS, msg,
                 at=NOW + B.COORD_WINDOW_SECONDS + 60) is None


def test_it_does_not_re_report_the_same_text_immediately():
    msg = "free discord nitro for everyone claim it now"
    assert _post_many(msg) is not None
    assert _post(B.COORD_MIN_AUTHORS + 1, msg) is None, \
        "a cooldown stops one campaign spamming the channel"


# ---------------------------------------------------------------------------
# Detector 2: this does not look like them
# ---------------------------------------------------------------------------
def _link(uid, prof, at=NOW, text="check this out https://scam.xyz/a", ch=1):
    return B.check_takeover(uid, text, ch, prof, f"user{uid}", now=at)


def test_a_burst_of_links_from_someone_who_never_posts_them_fires():
    prof = profile(messages=1000, links=2)
    assert _link(1, prof, NOW) is None
    assert _link(1, prof, NOW + 10) is None
    finding = _link(1, prof, NOW + 20)
    assert finding and finding["signal"] == "link_burst"


def test_a_new_member_posting_links_is_just_a_new_member():
    fresh = profile(days=3, messages=20, links=0)
    for i in range(6):
        assert _link(1, fresh, NOW + i) is None


def test_someone_who_always_posts_links_is_not_suspicious_for_posting_links():
    linker = profile(messages=1000, links=400)
    for i in range(6):
        assert _link(1, linker, NOW + i) is None


def test_slow_links_are_not_a_burst():
    prof = profile(messages=1000, links=0)
    for i in range(5):
        assert _link(1, prof, NOW + i * (B.LINK_BURST_SECONDS + 30)) is None


def test_the_same_message_across_several_channels_fires():
    prof = profile(messages=1000, links=0)
    text = "hey check out this amazing opportunity right now"
    assert B.check_takeover(1, text, 100, prof, now=NOW) is None
    assert B.check_takeover(1, text, 200, prof, now=NOW + 5) is None
    finding = B.check_takeover(1, text, 300, prof, now=NOW + 10)
    assert finding and finding["signal"] == "cross_channel"


def test_talking_in_one_channel_repeatedly_is_fine():
    prof = profile(messages=1000, links=0)
    text = "hey check out this amazing opportunity right now"
    for i in range(5):
        assert B.check_takeover(1, text, 100, prof, now=NOW + i) is None


def test_established_needs_both_age_and_volume():
    assert B.is_established(profile(days=90, messages=1000), NOW)
    assert not B.is_established(profile(days=90, messages=10), NOW)
    assert not B.is_established(profile(days=2, messages=5000), NOW)


# ---------------------------------------------------------------------------
# The cards
# ---------------------------------------------------------------------------
def _json(view):
    import json
    return json.dumps(view.to_components(), ensure_ascii=False)


def test_the_coordinated_card_explains_itself_and_offers_a_reversible_option():
    msg = "free discord nitro for everyone claim it now"
    finding = _post_many(msg)
    payload = _json(B.build_coordinated_view(finding))
    assert "how a raid starts" in payload
    assert "bw:coordto:" in payload and "bw:coordban:" in payload and "bw:coorddis:" in payload
    assert "copypasta" in payload, "identical wording has innocent explanations"
    for uid in range(1, B.COORD_MIN_AUTHORS + 1):
        assert f"<@{uid}>" in payload


def test_the_takeover_card_says_what_is_odd_and_leans_on_timeout():
    prof = profile(messages=1000, links=1)
    finding = None
    for i in range(3):
        finding = _link(1, prof, NOW + i)
    payload = _json(B.build_takeover_view(finding, prof))
    assert "doesn't look like them" in payload
    assert "stolen account" in payload
    assert "bw:tkto:1" in payload
    # Timing out is reversible; banning a real member on a heuristic is not offered.
    assert "bw:coordban" not in payload
    assert "reversible" in payload


def test_a_handled_card_drops_its_controls():
    prof = profile(messages=1000, links=1)
    finding = None
    for i in range(3):
        finding = _link(1, prof, NOW + i)
    payload = _json(B.build_takeover_view(finding, prof, handled=True))
    assert "bw:" not in payload
    assert "Dealt with" in payload


# ---------------------------------------------------------------------------
# Regressions from replaying three months of real #general. The first version
# produced 380 coordinated findings and 13 takeover findings, every one a false
# positive; these are the two mistakes that caused them.
# ---------------------------------------------------------------------------
def test_three_people_posting_different_gifs_share_nothing():
    """The original bug: a bare link normalised to the link token alone, so any three
    people posting any GIFs matched each other. 4 findings a day, all noise."""
    gifs = ["https://tenor.com/view/cat-dancing-123",
            "https://klipy.com/gifs/voldemort-death",
            "https://tenor.com/view/harry-potter-456"]
    for uid, gif in zip((1, 2, 3), gifs):
        assert _post(uid, gif) is None


def test_a_crowd_posting_the_same_scam_link_is_a_finding():
    """The case the media fix must not break: no words at all, but one identical URL."""
    finding = _post_many("https://free-nitro-claim.xyz/gift")
    assert finding and finding["match"] == "url"


def test_the_same_link_with_tracking_junk_still_matches():
    for uid in range(1, B.COORD_MIN_AUTHORS):
        assert _post(uid, f"https://scam.xyz/a?ref={uid}") is None
    assert _post(B.COORD_MIN_AUTHORS, "https://scam.xyz/a#x") is not None


def test_gif_spam_is_not_a_takeover():
    """One regular tripped the old version five times for posting reaction gifs."""
    prof = profile(messages=1000, links=0)
    for i, gif in enumerate(["https://tenor.com/view/a", "https://klipy.com/gifs/b",
                             "https://cdn.discordapp.com/attachments/1/2/c.png",
                             "https://youtu.be/abc"]):
        assert B.check_takeover(1, gif, 1, prof, now=NOW + i) is None


def test_an_unfamiliar_external_link_burst_still_fires():
    prof = profile(messages=1000, links=0)
    hits = [B.check_takeover(1, f"https://scam-{i}.xyz/claim", 1, prof, now=NOW + i)
            for i in range(3)]
    assert hits[-1] is not None and hits[-1]["signal"] == "link_burst"


def test_media_links_do_not_count_towards_the_baseline_either():
    """Otherwise a heavy gif poster looks like someone who 'often posts links' and
    becomes immune to the detector."""
    assert B.has_external_link("https://tenor.com/view/x") is False
    assert B.has_external_link("look at this https://scam.xyz/a") is True
    assert B.has_external_link("no links here at all") is False
