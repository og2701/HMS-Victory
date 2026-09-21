"""Instagram links fixed in place: what counts as one, and what gets posted back.

kkinstagram itself is a network call and is not under test. What is: that only real post
links are picked up (a profile or an /reels/audio/ page is not one), that a link someone
deliberately wrapped in <angle brackets> is left alone, that an unresolvable post - the
deleted and private ones, where kkinstagram bounces back to instagram.com - produces
silence rather than a useless link, and that anything too big to re-host falls back to the
link instead of being dropped.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiohttp

from lib.features import link_embeds as L

MEDIA = "https://instagram.flim1-1.fna.fbcdn.net/o1/v/t2/f2/m82/AQPnf.mp4?_nc_cat=100"


class Response:
    """Enough of an aiohttp response for the two calls the module makes."""

    def __init__(self, status=302, headers=None, body=b"", error=None):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self._error = error

    async def __aenter__(self):
        if self._error:
            raise self._error
        return self

    async def __aexit__(self, *_):
        return False

    @property
    def content(self):
        chunks = [self._body[i:i + 8] for i in range(0, len(self._body), 8)]

        class Stream:
            async def iter_chunked(self, _size):
                for chunk in chunks:
                    yield chunk

        return Stream()


class Session:
    def __init__(self, *responses, closed=False):
        self._responses = list(responses)
        self.closed = closed
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return self._responses.pop(0) if self._responses else Response(status=404)


class Message:
    def __init__(self, content, bot=False, filesize_limit=10 * 1024 * 1024):
        self.content = content
        self.author = types.SimpleNamespace(id=42, bot=bot)
        self.guild = types.SimpleNamespace(filesize_limit=filesize_limit)
        self.channel = types.SimpleNamespace(send=self._send)
        self.posts = []

    async def reply(self, **kwargs):
        self.posts.append(kwargs)

    async def _send(self, **kwargs):
        self.posts.append(kwargs)


def client_with(session):
    return types.SimpleNamespace(session=session)


def run(coro):
    L._recent.clear()
    return asyncio.run(coro)


# --- what counts as a post link ------------------------------------------------------

def test_every_share_sheet_shape_is_recognised():
    cases = {
        "https://www.instagram.com/reel/CkdGjonI08u/": ("reel", "CkdGjonI08u"),
        "http://instagram.com/p/DHiT76Bihqa": ("p", "DHiT76Bihqa"),
        "https://m.instagram.com/tv/CkdGjonI08u/": ("tv", "CkdGjonI08u"),
        # the app hands out both the bare and the username-prefixed form
        "https://www.instagram.com/edsheeran/reel/CkdGjonI08u/": ("reel", "CkdGjonI08u"),
        # /reels/ is the same post as /reel/, and kkinstagram wants the singular
        "https://www.instagram.com/reels/DCEwmESvYJz/": ("reel", "DCEwmESvYJz"),
        # the tracking parameter the app staples on must not break the match
        "https://www.instagram.com/p/DHiT76Bihqa/?igsh=MXY5NA%3D%3D": ("p", "DHiT76Bihqa"),
        "have a look at https://instagram.com/reel/CkdGjonI08u lads": ("reel", "CkdGjonI08u"),
    }
    for content, expected in cases.items():
        assert L.find_instagram_posts(content) == [expected], content


def test_things_that_are_not_posts_are_left_alone():
    for content in [
        "",
        "https://www.instagram.com/edsheeran/",
        "https://www.instagram.com/reels/audio/1234567890/",
        "https://www.instagram.com/stories/edsheeran/123456/",
        "https://example.com/p/CkdGjonI08u",
        "instagram is down again",
    ]:
        assert L.find_instagram_posts(content) == [], content


def test_a_link_wrapped_in_angle_brackets_stays_unpreviewed():
    assert L.find_instagram_posts("<https://www.instagram.com/reel/CkdGjonI08u/>") == []
    # only the wrapped one is skipped
    assert L.find_instagram_posts(
        "<https://www.instagram.com/reel/CkdGjonI08u/> https://instagram.com/p/DHiT76Bihqa"
    ) == [("p", "DHiT76Bihqa")]
    # a stray < earlier in the sentence is not a wrapper
    assert L.find_instagram_posts("3 < 4 https://instagram.com/reel/CkdGjonI08u") == [
        ("reel", "CkdGjonI08u")]


def test_repeats_collapse_and_a_wall_of_links_is_capped():
    same = "https://instagram.com/reel/CkdGjonI08u https://www.instagram.com/edsheeran/reel/CkdGjonI08u/"
    assert L.find_instagram_posts(same) == [("reel", "CkdGjonI08u")]

    wall = " ".join(f"https://instagram.com/p/Code{n}xxxxx" for n in range(6))
    assert len(L.find_instagram_posts(wall)) == L.MAX_LINKS_PER_MESSAGE


def test_filenames_follow_the_served_type():
    assert L.filename_for("ABC12", "video/mp4") == "ABC12.mp4"
    assert L.filename_for("ABC12", "image/jpeg; charset=binary") == "ABC12.jpg"
    assert L.filename_for("ABC12", "") == "ABC12.mp4"


def test_one_member_cannot_flood_the_channel():
    posted = [L._rate_limited(7) for _ in range(L.RATE_LIMIT_PER_USER + 2)]
    assert posted[:L.RATE_LIMIT_PER_USER] == [False] * L.RATE_LIMIT_PER_USER
    assert posted[L.RATE_LIMIT_PER_USER:] == [True, True]
    # somebody else is unaffected, and the window eventually clears
    assert L._rate_limited(8) is False
    assert L._rate_limited(7, now=__import__("time").time() + L.RATE_WINDOW + 1) is False
    L._recent.clear()


# --- resolving ------------------------------------------------------------------------

def test_only_a_redirect_to_the_cdn_counts_as_resolved():
    session = Session(Response(302, {"Location": MEDIA}))
    assert run(L.resolve_media(session, "reel", "CkdGjonI08u")) == MEDIA
    assert session.requests[0][0] == "https://kkinstagram.com/reel/CkdGjonI08u"
    assert session.requests[0][1]["allow_redirects"] is False


def test_both_of_metas_media_cdns_are_accepted():
    """Which CDN a post comes back on varies; only knowing fbcdn.net dropped most reels."""
    for host in ("instagram.flim1-1.fna.fbcdn.net", "scontent-waw2-1.cdninstagram.com"):
        target = f"https://{host}/o1/v/t2/f2/m86/AQPPWYcC.mp4?_nc_cat=100"
        assert run(L.resolve_media(Session(Response(302, {"Location": target})), "reel", "x12345")) == target


def test_a_post_it_cannot_scrape_resolves_to_nothing():
    # deleted/private/age-gated: kkinstagram hands the crawler straight back to Instagram
    bounced = Session(Response(302, {"Location": "https://www.instagram.com/reel/Cl5xJY1AjAO/"}))
    assert run(L.resolve_media(bounced, "reel", "Cl5xJY1AjAO")) is None

    # and a lookalike host must not pass for the CDN
    for lookalike in ("https://fbcdn.net.evil.example/x.mp4", "https://cdninstagram.com.evil.example/x.mp4"):
        assert run(L.resolve_media(Session(Response(302, {"Location": lookalike})), "reel", "CkdGjonI08u")) is None

    # the service's intermittent 502s, and its 404 for a bad shortcode
    assert run(L.resolve_media(Session(Response(504)), "p", "ZZZZnotreal9")) is None
    assert run(L.resolve_media(Session(Response(200, {}, b"<html>")), "p", "x12345")) is None


def test_a_dead_service_is_not_an_error():
    for failure in (aiohttp.ClientError("boom"), asyncio.TimeoutError()):
        session = Session(Response(error=failure))
        assert run(L.resolve_media(session, "reel", "CkdGjonI08u")) is None


# --- downloading ----------------------------------------------------------------------

def test_media_comes_back_with_its_type():
    session = Session(Response(200, {"Content-Type": "video/mp4", "Content-Length": "9"}, b"mp4 bytes"))
    assert run(L.download_media(session, MEDIA, 1000)) == (b"mp4 bytes", "video/mp4")


def test_oversized_media_is_refused_by_header_and_by_stream():
    declared = Session(Response(200, {"Content-Type": "video/mp4", "Content-Length": "999999"}, b"x"))
    assert run(L.download_media(declared, MEDIA, 10)) is None

    # a server that does not declare a length is still cut off mid-stream
    silent = Session(Response(200, {"Content-Type": "video/mp4"}, b"x" * 500))
    assert run(L.download_media(silent, MEDIA, 10)) is None


def test_an_empty_or_failed_body_is_not_posted():
    assert run(L.download_media(Session(Response(403, {}, b"")), MEDIA, 1000)) is None
    assert run(L.download_media(Session(Response(200, {"Content-Type": "video/mp4"}, b"")), MEDIA, 1000)) is None


# --- the whole path -------------------------------------------------------------------

def test_a_reel_is_posted_as_a_playable_file():
    session = Session(
        Response(302, {"Location": MEDIA}),
        Response(200, {"Content-Type": "video/mp4", "Content-Length": "9"}, b"mp4 bytes"),
    )
    message = Message("look at this https://www.instagram.com/reel/CkdGjonI08u/")
    run(L.fix_instagram_links(client_with(session), message))

    assert len(message.posts) == 1
    assert message.posts[0]["file"].filename == "CkdGjonI08u.mp4"


def oversized_session(body=b"x" * 2048):
    """A resolvable reel whose media is past a 1KB upload limit."""
    return Session(
        Response(302, {"Location": MEDIA}),
        Response(200, {"Content-Type": "video/mp4", "Content-Length": str(len(body))}, body),
    )


def test_a_reel_over_the_limit_is_re_encoded_rather_than_given_up_on(monkeypatch):
    asked = {}

    async def fake_compress(data, target_bytes):
        asked["target"] = target_bytes
        asked["source"] = len(data)
        return b"smaller"

    monkeypatch.setattr(L, "compress_video", fake_compress)
    message = Message("https://www.instagram.com/reel/CkdGjonI08u/", filesize_limit=1024 + L.UPLOAD_HEADROOM)
    run(L.fix_instagram_links(client_with(oversized_session()), message))

    assert asked == {"target": 1024, "source": 2048}
    assert len(message.posts) == 1
    assert message.posts[0]["file"].filename == "CkdGjonI08u.mp4"


def test_a_reel_that_will_not_come_down_far_enough_falls_back_to_the_link(monkeypatch):
    async def cannot_compress(_data, _target):
        return None

    monkeypatch.setattr(L, "compress_video", cannot_compress)
    message = Message("https://www.instagram.com/reel/CkdGjonI08u/", filesize_limit=1024 + L.UPLOAD_HEADROOM)
    run(L.fix_instagram_links(client_with(oversized_session()), message))

    assert len(message.posts) == 1
    assert message.posts[0]["content"] == "https://kkinstagram.com/reel/CkdGjonI08u"
    assert message.posts[0]["mention_author"] is False


def test_an_oversized_photo_is_linked_not_re_encoded(monkeypatch):
    """There is nothing worth trading away on a still, so it is not put through ffmpeg."""
    async def unexpected(_data, _target):
        raise AssertionError("a photo should never reach the encoder")

    monkeypatch.setattr(L, "compress_video", unexpected)
    session = Session(
        Response(302, {"Location": MEDIA}),
        Response(200, {"Content-Type": "image/jpeg", "Content-Length": "2048"}, b"x" * 2048),
    )
    message = Message("https://www.instagram.com/p/DHiT76Bihqa/", filesize_limit=1024 + L.UPLOAD_HEADROOM)
    run(L.fix_instagram_links(client_with(session), message))

    assert message.posts[0]["content"] == "https://kkinstagram.com/p/DHiT76Bihqa"


def test_a_reel_bigger_than_the_download_ceiling_is_never_pulled_down(monkeypatch):
    monkeypatch.setattr(config_module(), "INSTAGRAM_EMBED_SOURCE_MAX_BYTES", 1000)
    message = Message("https://www.instagram.com/reel/CkdGjonI08u/")
    run(L.fix_instagram_links(client_with(oversized_session()), message))

    assert message.posts[0]["content"] == "https://kkinstagram.com/reel/CkdGjonI08u"


def config_module():
    import config
    return config


def test_nothing_is_posted_when_there_is_nothing_to_add():
    # unresolvable post
    bounced = Session(Response(302, {"Location": "https://www.instagram.com/reel/Cl5xJY1AjAO/"}))
    message = Message("https://www.instagram.com/reel/Cl5xJY1AjAO/")
    run(L.fix_instagram_links(client_with(bounced), message))
    assert message.posts == []

    # another bot's message, and a session that is already shut down
    for client, content in [
        (client_with(Session(Response(302, {"Location": MEDIA}))), "https://instagram.com/reel/CkdGjonI08u"),
        (client_with(Session(closed=True)), "https://instagram.com/reel/CkdGjonI08u"),
        (client_with(None), "https://instagram.com/reel/CkdGjonI08u"),
    ]:
        quiet = Message(content, bot=client.session is not None and not getattr(client.session, "closed", False))
        run(L.fix_instagram_links(client, quiet))
        assert quiet.posts == []


def test_the_toggle_turns_it_off():
    import config
    original = getattr(config, "INSTAGRAM_EMBED_FIX_ENABLED", True)
    config.INSTAGRAM_EMBED_FIX_ENABLED = False
    try:
        session = Session(Response(302, {"Location": MEDIA}))
        message = Message("https://instagram.com/reel/CkdGjonI08u")
        run(L.fix_instagram_links(client_with(session), message))
        assert message.posts == []
        assert session.requests == []
    finally:
        config.INSTAGRAM_EMBED_FIX_ENABLED = original


def test_the_upload_cap_stays_under_the_guilds_limit():
    assert L.upload_limit(Message("", filesize_limit=10 * 1024 * 1024)) == 10 * 1024 * 1024 - L.UPLOAD_HEADROOM
    # a boosted guild is still capped by config, and a DM with no guild has a floor
    assert L.upload_limit(Message("", filesize_limit=500 * 1024 * 1024)) == L._max_bytes()
    no_guild = Message("")
    no_guild.guild = None
    assert L.upload_limit(no_guild) == 8 * 1024 * 1024 - L.UPLOAD_HEADROOM


# --- the encoder ----------------------------------------------------------------------

def _ffmpeg_missing():
    import shutil as _shutil
    return _shutil.which("ffmpeg") is None or _shutil.which("ffprobe") is None


def _sample_video(seconds=5, path=None):
    """A synthetic clip fat enough that it has to be squeezed to hit a small target."""
    import subprocess, tempfile, os
    path = path or os.path.join(tempfile.mkdtemp(prefix="ig-test-"), "sample.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=1280x720:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-b:v", "6000k", "-c:a", "aac", path],
        check=True, capture_output=True,
    )
    with open(path, "rb") as handle:
        return handle.read()


def test_the_encoder_lands_under_the_target_and_keeps_the_clip_playable():
    if _ffmpeg_missing():
        import pytest
        pytest.skip("ffmpeg is not installed")

    import subprocess, tempfile, os
    source = _sample_video()
    target = len(source) // 3
    shrunk = run(L.compress_video(source, target))

    assert shrunk is not None and len(shrunk) <= target

    # still a real video of the same length, not a truncated file
    path = os.path.join(tempfile.mkdtemp(prefix="ig-test-"), "out.mp4")
    with open(path, "wb") as handle:
        handle.write(shrunk)
    assert abs(run(L.video_duration(path)) - 5) < 0.5


def test_the_encoder_refuses_what_it_cannot_do_honestly():
    if _ffmpeg_missing():
        import pytest
        pytest.skip("ffmpeg is not installed")

    source = _sample_video()
    # a target so small the picture would be a slideshow - better to post the link
    assert run(L.compress_video(source, 20_000)) is None
    # and nothing that is not a video gets through
    assert run(L.compress_video(b"not a video at all", 1_000_000)) is None
    assert run(L.compress_video(b"", 1_000_000)) is None
    assert run(L.compress_video(b"anything", 0)) is None
