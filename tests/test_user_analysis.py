import time
import pytest
from unittest.mock import MagicMock, AsyncMock

from database import DatabaseManager, init_db
from commands.moderation.user_analysis import (
    fetch_archived_user_messages,
    gather_user_messages,
    _activity_line,
    _build_prompt,
)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test.db")
    monkeypatch.setattr("database.DB_FILE", test_db)
    DatabaseManager._connection = None
    init_db()
    yield
    DatabaseManager._connection = None


def test_fetch_archived_user_messages_empty():
    res = fetch_archived_user_messages(12345, None, days=30)
    assert res == []


def test_fetch_archived_user_messages_multi_channel():
    now = int(time.time())
    uid = 99999

    # Insert messages across 2 channels
    DatabaseManager.execute(
        "INSERT INTO message_archive (message_id, channel_id, user_id, content, attachments, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("msg1", "101", str(uid), "Hello from general", None, now - 500)
    )
    DatabaseManager.execute(
        "INSERT INTO message_archive (message_id, channel_id, user_id, content, attachments, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("msg2", "202", str(uid), "Discussion in politics\nsecond line", '["http://example.com/img.png"]', now - 200)
    )
    DatabaseManager.execute(
        "INSERT INTO message_archive (message_id, channel_id, user_id, content, attachments, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("msg3", "101", "88888", "Someone else", None, now - 100)
    )

    # Mock guild with channel lookups
    guild = MagicMock()
    ch1 = MagicMock()
    ch1.name = "general"
    ch1.permissions_for.return_value.read_messages = True
    ch2 = MagicMock()
    ch2.name = "politics"
    ch2.permissions_for.return_value.read_messages = True

    guild.get_channel_or_thread.side_effect = lambda cid: ch1 if cid == 101 else (ch2 if cid == 202 else None)

    msgs = fetch_archived_user_messages(uid, guild, days=30)
    assert len(msgs) == 2
    assert msgs[0]["mid"] == "msg1"
    assert msgs[0]["channel"] == "general"
    assert msgs[0]["content"] == "Hello from general"
    assert msgs[0]["jump"] == f"https://discord.com/channels/{guild.id}/101/msg1"

    assert msgs[1]["mid"] == "msg2"
    assert msgs[1]["channel"] == "politics"
    assert "Discussion in politics second line" in msgs[1]["content"]
    assert "[attachment]" in msgs[1]["content"]


@pytest.mark.asyncio
async def test_gather_user_messages_prefers_archive():
    now = int(time.time())
    uid = 77777

    DatabaseManager.execute(
        "INSERT INTO message_archive (message_id, channel_id, user_id, content, attachments, ts) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("m1", "303", str(uid), "Archived post", None, now - 100)
    )

    guild = MagicMock()
    ch = MagicMock()
    ch.name = "lounge"
    ch.permissions_for.return_value.read_messages = True
    guild.get_channel_or_thread.return_value = ch

    user = MagicMock()
    user.id = uid
    user.joined_at = None

    client = MagicMock()

    msgs = await gather_user_messages(client, guild, user, channel_ids=None, days=30)
    assert len(msgs) == 1
    assert msgs[0]["mid"] == "m1"
    assert msgs[0]["channel"] == "lounge"
    assert msgs[0]["content"] == "Archived post"


def test_activity_line_formatting():
    now = int(time.time())
    msgs = [
        {"mid": "1", "channel": "general", "reactions": 2, "ts": now - 3600},
        {"mid": "2", "channel": "general", "reactions": 0, "ts": now - 1800},
        {"mid": "3", "channel": "politics", "reactions": 1, "ts": now},
    ]
    line = _activity_line(msgs)
    assert "**3** msgs" in line
    assert "**2** channels" in line
    assert "#general (2)" in line
    assert "**3** reactions" in line
