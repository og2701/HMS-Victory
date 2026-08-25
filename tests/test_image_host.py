"""Hosting generated images: the cache, and not uploading the same picture twice.

The draw is tens of milliseconds. The upload is a REST round trip, and it is the only leg
anybody actually sits waiting on - measured on the crossword board, the whole render is
36ms for a 13KB PNG. So everything worth testing here is about avoiding the upload.
"""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.core import image_host as H

PNG_A = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"a" * 512)
PNG_B = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"b" * 512)


class FakeAttachment:
    def __init__(self, n):
        self.url = f"https://cdn.discordapp.com/attachments/1/{n}/board.png"


class FakeMessage:
    def __init__(self, n):
        self.attachments = [FakeAttachment(n)]


class FakeChannel:
    def __init__(self):
        self.uploads = 0
        self.gate = None

    async def send(self, **kwargs):
        self.uploads += 1
        if self.gate is not None:
            await self.gate.wait()
        return FakeMessage(self.uploads)


class FakeClient:
    def __init__(self):
        self.channel = FakeChannel()

    def get_channel(self, _id):
        return self.channel


def _fresh():
    H._url_cache.clear()
    H._in_flight.clear()
    H._stats.update(hits=0, misses=0, upload_ms=0.0)
    return FakeClient()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _bytes(b):
    return io.BytesIO(b.getvalue())


def test_the_same_picture_is_uploaded_once():
    """Every player's first crossword of the day is the identical empty grid, so this is
    the common case rather than a rare one."""
    c = _fresh()
    first = _run(H.host_image(c, _bytes(PNG_A)))
    second = _run(H.host_image(c, _bytes(PNG_A)))
    assert first == second and c.channel.uploads == 1, c.channel.uploads
    assert H.cache_stats()["hits"] == 1


def test_a_different_picture_is_a_different_upload():
    c = _fresh()
    a = _run(H.host_image(c, _bytes(PNG_A)))
    b = _run(H.host_image(c, _bytes(PNG_B)))
    assert a != b and c.channel.uploads == 2


def test_two_people_opening_the_same_board_at_once_upload_once():
    """Both wait on the one upload rather than posting a second copy of it."""
    c = _fresh()
    c.channel.gate = None

    async def both():
        c.channel.gate = asyncio.Event()
        pair = asyncio.gather(H.host_image(c, _bytes(PNG_A)),
                              H.host_image(c, _bytes(PNG_A)))
        await asyncio.sleep(0)
        c.channel.gate.set()
        return await pair

    one, two = _run(both())
    assert one == two, (one, two)
    assert c.channel.uploads == 1, f"uploaded {c.channel.uploads} times"


def test_one_caller_giving_up_does_not_cancel_it_for_the_other():
    """A timed-out interaction must not take the upload everyone else is waiting on with
    it - that is what the shield in host_image is for."""
    c = _fresh()

    async def scenario():
        c.channel.gate = asyncio.Event()
        quitter = asyncio.create_task(H.host_image(c, _bytes(PNG_A)))
        stayer = asyncio.create_task(H.host_image(c, _bytes(PNG_A)))
        await asyncio.sleep(0)
        quitter.cancel()
        c.channel.gate.set()
        return await stayer

    assert _run(scenario()), "the surviving caller lost the upload"
    assert c.channel.uploads == 1


def test_a_failed_upload_returns_none_rather_than_raising():
    """The caller falls back to a plain attachment. A slow board beats no board."""
    class Broken(FakeClient):
        def get_channel(self, _id):
            raise RuntimeError("no channel")

    H._url_cache.clear()
    H._in_flight.clear()
    assert _run(H.host_image(Broken(), _bytes(PNG_A))) is None


def test_stats_say_where_the_time_went():
    c = _fresh()
    _run(H.host_image(c, _bytes(PNG_A)))
    _run(H.host_image(c, _bytes(PNG_A)))
    _run(H.host_image(c, _bytes(PNG_B)))
    s = H.cache_stats()
    assert (s["hits"], s["misses"]) == (1, 2), s
    assert 0 < s["hit_rate"] < 1 and s["cached"] == 2, s


def test_an_untouched_crossword_board_is_the_same_bytes_for_everyone():
    """What the prewarm rests on. If a board carried anything player-specific - a name, a
    timestamp - then hosting it up front would warm a cache nobody ever hits."""
    try:
        import datetime
        import hashlib
        import json
        from lib.features import crossword as C
    except Exception as e:                    # no Pillow/pytz on this machine
        print(f"      (skipped: {e})")
        return
    date = C._today()
    try:
        C._todays_puzzle(date)
    except Exception:
        date = datetime.date.fromisoformat(
            json.load(open("data/words/crosswords.json"))[0]["from"])
    digests = {hashlib.sha256(C.draw_board(uid, date).getvalue()).hexdigest()
               for uid in (0, 123456789, 987654321)}
    assert len(digests) == 1, "the empty board differs per player, so prewarming is useless"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:
            import traceback
            print(f"ERROR {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
