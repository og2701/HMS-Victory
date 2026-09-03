"""Voice note transcription: offering the button, and paying for a note exactly once.

The transcription itself is a network call and is not under test. What is: that the
button only appears on real voice notes from real people, that two presses cannot both
pay, and that a failure hands the claim back rather than leaving the note marked done.
"""
import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "test-key")   # translation.py builds a client on import

import discord

from lib.core.constants import TRANSLATION_BLACKLIST_CHANNELS
from lib.features import voice_notes as V

VN_ID, CH_ID = 555, 777


class Att:
    def __init__(self, duration=4.2, content_type="audio/ogg", data=b"OggS...", name="voice-message.ogg"):
        self.duration, self.content_type, self.filename = duration, content_type, name
        self._data = data

    async def read(self):
        return self._data


class Msg:
    def __init__(self, voice=True, bot=False, channel_id=CH_ID, attachments=None):
        self.id = VN_ID
        self.flags = types.SimpleNamespace(voice=voice)
        self.author = types.SimpleNamespace(bot=bot, mention="<@1>")
        self.channel = types.SimpleNamespace(id=channel_id)
        self.guild = object()
        self.attachments = [Att()] if attachments is None else attachments
        self.replies = []

    async def reply(self, **kw):
        self.replies.append(kw)


class Resp:
    def __init__(self, log):
        self.log = log

    async def send_message(self, content=None, **kw):
        self.log.append(("ephemeral", content))

    async def defer(self, **kw):
        self.log.append(("deferred", None))


class Inter:
    def __init__(self, note):
        self.log = []
        self.response = Resp(self.log)
        self.followup = types.SimpleNamespace(send=self._fu)
        self.user = types.SimpleNamespace(display_name="Mod")
        self.message = types.SimpleNamespace(edits=[], edit=self._edit)
        self.client = types.SimpleNamespace(
            get_channel=lambda _cid: types.SimpleNamespace(fetch_message=self._fetch),
            fetch_channel=None)
        self._note = note

    async def _fu(self, content=None, **kw):
        self.log.append(("followup", content))

    async def _edit(self, **kw):
        self.message.edits.append(kw)

    async def _fetch(self, _mid):
        return self._note


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _clear():
    V._release_translation(VN_ID, V.DEDUP_TARGET)


# --- offering the button ----------------------------------------------------------------
def test_a_voice_note_gets_the_button():
    m = Msg()
    assert _run(V.offer_transcription(None, m)) is True
    assert m.replies and m.replies[0]["mention_author"] is False
    view = m.replies[0]["view"]
    buttons = [c for row in view.children for c in getattr(row, "children", [])]
    assert len(buttons) == 1 and buttons[0].item.custom_id == f"vn:{CH_ID}:{VN_ID}"


def test_an_ordinary_message_gets_nothing():
    assert _run(V.offer_transcription(None, Msg(voice=False))) is False
    m = Msg(voice=True, attachments=[])
    assert _run(V.offer_transcription(None, m)) is False


def test_the_bots_own_mirror_copies_get_nothing():
    """The log thread is full of the bot's re-uploads of every note. A button on each of
    those would be a second button for the same audio."""
    assert _run(V.offer_transcription(None, Msg(bot=True))) is False


def test_blacklisted_channels_get_nothing():
    assert _run(V.offer_transcription(None, Msg(channel_id=TRANSLATION_BLACKLIST_CHANNELS[0]))) is False


def test_the_switch_turns_it_off():
    import config
    old = getattr(config, "VOICE_NOTE_TRANSCRIBE_ENABLED", True)
    config.VOICE_NOTE_TRANSCRIBE_ENABLED = False
    try:
        assert _run(V.offer_transcription(None, Msg())) is False
    finally:
        config.VOICE_NOTE_TRANSCRIBE_ENABLED = old


def test_the_button_survives_a_restart():
    """Rebuilt from the custom_id alone - there is no state anywhere else."""
    b = V.TranscribeButton(CH_ID, VN_ID)
    m = V.TranscribeButton.__discord_ui_compiled_template__.match(b.item.custom_id)
    assert m and (int(m["cid"]), int(m["mid"])) == (CH_ID, VN_ID)


# --- pressing it -------------------------------------------------------------------------
def _press(note, transcriber):
    real = V.transcribe
    V.transcribe = transcriber
    try:
        i = Inter(note)
        _run(V.TranscribeButton(CH_ID, VN_ID).callback(i))
        return i
    finally:
        V.transcribe = real


def test_pressing_puts_the_words_on_the_reply():
    _clear()
    try:
        async def fake(data, name):
            assert data == b"OggS..." and name == "voice-message.ogg"
            return "  hello from the pub  "
        i = _press(Msg(), fake)
        assert i.message.edits, "the reply was never updated"
        view = i.message.edits[0]["view"]
        texts = [c.content for box in view.children for c in getattr(box, "children", [])
                 if isinstance(c, discord.ui.TextDisplay)]
        assert any("hello from the pub" in t for t in texts), texts
        assert any("requested by Mod" in t for t in texts), texts
    finally:
        _clear()


def test_a_note_is_only_paid_for_once():
    _clear()
    try:
        calls = []

        async def fake(data, name):
            calls.append(1)
            return "once"
        _press(Msg(), fake)
        second = _press(Msg(), fake)
        assert len(calls) == 1, "the second press transcribed it again"
        assert second.log and second.log[0][0] == "ephemeral", second.log
        assert "already" in second.log[0][1]
    finally:
        _clear()


def test_a_failure_hands_the_claim_back():
    """Otherwise the note is marked done and nobody can ever retry it."""
    _clear()
    try:
        async def broken(data, name):
            raise RuntimeError("api down")
        i = _press(Msg(), broken)
        assert not V._already_translated(VN_ID, V.DEDUP_TARGET), "the failed claim stuck"
        assert any(k == "followup" and "another go" in (c or "") for k, c in i.log), i.log

        async def fine(data, name):
            return "second time lucky"
        i2 = _press(Msg(), fine)
        assert i2.message.edits, "a retry after a failure did nothing"
    finally:
        _clear()


def test_a_long_transcript_is_cut_rather_than_rejected():
    view = V._result_view("<@1>", "x" * (V.MAX_CHARS + 500), "Mod", 90.0)
    texts = [c.content for box in view.children for c in getattr(box, "children", [])
             if isinstance(c, discord.ui.TextDisplay)]
    assert all(len(t) <= 4000 for t in texts)
    assert any(t.endswith("…") for t in texts)
    assert any("90s" in t for t in texts)


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
