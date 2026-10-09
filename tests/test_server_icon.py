"""The Halloween server icons: which one each day gets, and that it's only uploaded when it isn't already up."""

import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.features import server_icon as S  # noqa: E402


def name(day):
    f = S.icon_for(day)
    return os.path.basename(f) if f else None


def test_a_different_halloween_icon_each_day_then_the_usual_one():
    assert name(dt.date(2026, 10, 9)) == "witch.gif"
    assert [name(dt.date(2026, 10, d)) for d in range(10, 15)] == ["franken.gif", "hellfire.gif", "scare.gif", "mummy.gif", "lantern.gif"]
    assert name(dt.date(2026, 10, 31)) in {f"{n}.gif" for n in S.HALLOWEEN}
    assert name(dt.date(2026, 11, 1)) == "default.png"
    assert name(dt.date(2026, 11, 20)) is None and name(dt.date(2026, 10, 1)) is None
    for day in range(9, 32):
        assert os.path.exists(S.icon_for(dt.date(2026, 10, day)))
    assert os.path.exists(S.icon_for(dt.date(2026, 11, 1)))


def test_it_only_uploads_when_the_icon_isnt_the_days_already(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "STATE", str(tmp_path / "server_icon.json"))
    edits = []

    class Guild:
        async def edit(self, icon, reason):
            edits.append((len(icon), reason))

    class Client:
        def get_guild(self, _):
            return Guild()

    day = dt.date(2026, 10, 12)
    assert asyncio.run(S.update_server_icon(Client(), day)).endswith("scare.gif")
    assert asyncio.run(S.update_server_icon(Client(), day)) is None
    assert asyncio.run(S.update_server_icon(Client(), dt.date(2026, 11, 1))).endswith("default.png")
    assert asyncio.run(S.update_server_icon(Client(), dt.date(2026, 11, 2))) is None
    assert len(edits) == 2 and "scare.gif" in edits[0][1]
