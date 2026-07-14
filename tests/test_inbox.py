"""Pure storage tests for the durable notification inbox.

Runnable under pytest or directly with ``python3 tests/test_inbox.py``.
"""

from contextlib import contextmanager
import asyncio
import os
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


SCHEMA = """
CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    jump_url TEXT,
    created_at INTEGER NOT NULL,
    read_at INTEGER
);
CREATE INDEX idx_notifications_user_created
    ON notifications(user_id, created_at);
CREATE INDEX idx_notifications_user_read
    ON notifications(user_id, read_at);
"""


@contextmanager
def _fresh_inbox():
    import database

    old_file = database.DB_FILE
    old_connection = database.DatabaseManager._connection
    database.DatabaseManager._connection = None
    with tempfile.TemporaryDirectory() as tmp:
        database.DB_FILE = os.path.join(tmp, "inbox.db")
        connection = database.DatabaseManager.get_connection()
        connection.executescript(SCHEMA)
        connection.commit()
        try:
            from lib.features import inbox
            yield inbox, database.DatabaseManager
        finally:
            connection.close()
            database.DatabaseManager._connection = old_connection
            database.DB_FILE = old_file


def test_create_list_count_and_pagination():
    with _fresh_inbox() as (inbox, _):
        first = inbox.create_notification(
            10, "economy", "First", "Oldest", created_at=100,
        )
        second = inbox.create_notification(
            10, "shop", "Second", "Same timestamp, newer ID", created_at=200,
        )
        third = inbox.create_notification(
            10,
            "moderation",
            "Third",
            "Newest at same timestamp",
            "https://discord.com/channels/1/2/3",
            created_at=200,
        )
        inbox.create_notification(20, "other", "Not yours", "Private", created_at=300)

        assert (first, second, third) == (1, 2, 3)
        assert inbox.count_notifications(10) == 3
        assert inbox.count_unread_notifications(10) == 3
        assert inbox.count_notifications(20) == 1

        page_one = inbox.list_notifications(10, limit=2)
        page_two = inbox.list_notifications(10, limit=2, offset=2)
        assert [notice.id for notice in page_one] == [3, 2]
        assert [notice.id for notice in page_two] == [1]
        assert page_one[0].jump_url == "https://discord.com/channels/1/2/3"
        assert all(notice.is_unread for notice in page_one + page_two)


def test_mark_one_is_owned_and_idempotent():
    with _fresh_inbox() as (inbox, db):
        notification_id = inbox.create_notification(
            "owner", "shop", "Approved", "Done", created_at=100,
        )

        assert inbox.mark_notification_read("intruder", notification_id, read_at=150) is False
        assert inbox.count_unread_notifications("owner") == 1
        assert inbox.mark_notification_read("owner", notification_id, read_at=200) is True
        assert inbox.mark_notification_read("owner", notification_id, read_at=300) is False
        assert inbox.count_unread_notifications("owner") == 0
        assert db.fetch_one(
            "SELECT read_at FROM notifications WHERE id = ?", (notification_id,),
        ) == (200,)


def test_mark_all_and_clear_read_never_touch_other_users():
    with _fresh_inbox() as (inbox, _):
        for title in ("One", "Two", "Three"):
            inbox.create_notification("alice", "test", title, "Body", created_at=100)
        bob_read = inbox.create_notification("bob", "test", "Bob read", "Body", created_at=100)
        inbox.create_notification("bob", "test", "Bob unread", "Body", created_at=100)
        inbox.mark_notification_read("bob", bob_read, read_at=120)

        assert inbox.mark_all_notifications_read("alice", read_at=200) == 3
        assert inbox.count_unread_notifications("alice") == 0
        assert inbox.count_unread_notifications("bob") == 1

        assert inbox.clear_read_notifications("alice") == 3
        assert inbox.count_notifications("alice") == 0
        assert inbox.count_notifications("bob") == 2
        assert inbox.clear_read_notifications("alice") == 0


def test_mark_page_read_is_owned_bounded_and_idempotent():
    with _fresh_inbox() as (inbox, _):
        alice_ids = [
            inbox.create_notification("alice", "test", title, "Body", created_at=100)
            for title in ("One", "Two", "Three")
        ]
        bob_id = inbox.create_notification(
            "bob", "test", "Private", "Body", created_at=100
        )

        assert inbox.mark_notifications_read(
            "alice", [alice_ids[0], alice_ids[2], bob_id], read_at=200
        ) == 2
        assert inbox.count_unread_notifications("alice") == 1
        assert inbox.count_unread_notifications("bob") == 1
        assert inbox.mark_notifications_read(
            "alice", [alice_ids[0], alice_ids[2]], read_at=300
        ) == 0


def test_unread_filter_and_validation():
    with _fresh_inbox() as (inbox, _):
        read_id = inbox.create_notification(1, "test", "Read", "Body", created_at=100)
        unread_id = inbox.create_notification(1, "test", "Unread", "Body", created_at=101)
        inbox.mark_notification_read(1, read_id, read_at=200)

        rows = inbox.list_notifications(1, unread_only=True)
        assert [row.id for row in rows] == [unread_id]

        for call in (
            lambda: inbox.create_notification("", "test", "Title", "Body"),
            lambda: inbox.create_notification(1, "", "Title", "Body"),
            lambda: inbox.list_notifications(1, limit=0),
            lambda: inbox.list_notifications(1, offset=-1),
            lambda: inbox.mark_notification_read(1, 0),
        ):
            try:
                call()
            except ValueError:
                pass
            else:
                raise AssertionError("invalid inbox input should raise ValueError")


def test_ui_text_and_jump_url_safety():
    from commands.social.inbox import _safe_jump_url, _safe_text

    rendered = _safe_text("@everyone **bold** [link](bad)", 200)
    assert "@everyone" not in rendered
    assert "\\*\\*bold\\*\\*" in rendered
    assert _safe_jump_url("https://discord.com/channels/1/2/3") is not None
    assert _safe_jump_url("https://evil.invalid/channels/1/2/3") is None
    assert _safe_jump_url("https://discord.com/channels/1/2/3) malicious") is None


def test_ui_embed_paginates_and_reports_unread_state():
    with _fresh_inbox() as (inbox, _):
        for number in range(6):
            inbox.create_notification(
                42,
                "category **unsafe**",
                f"Notice {number}",
                "Hello @everyone",
                created_at=100 + number,
            )

        async def render():
            from commands.social.inbox import InboxView

            view = InboxView(42)
            first = view.build_embed()
            first_state = (
                len(first.fields),
                first.description,
                first.fields[0].name,
                first.fields[0].value,
                view.next_button.disabled,
                view.mark_page_button.disabled,
            )
            marked = view.mark_current_page_read()
            after_mark = view.build_embed().description
            view.page = 1
            second = view.build_embed()
            return first_state, marked, after_mark, len(second.fields), second.footer.text

        first, marked, after_mark, second_field_count, second_footer = asyncio.run(render())
        field_count, description, field_name, field_value, next_disabled, mark_page_disabled = first
        assert field_count == 5
        assert "6 unread" in description
        assert "Unread" in field_name
        assert "\\*\\*unsafe\\*\\*" in field_name
        assert "@everyone" not in field_value
        assert next_disabled is False
        assert mark_page_disabled is False
        assert marked == 5
        assert "1 unread" in after_mark
        assert second_field_count == 1
        assert second_footer == "Page 2 of 2"


if __name__ == "__main__":
    import traceback

    tests = [
        value
        for name, value in sorted(globals().items())
        if name.startswith("test_") and callable(value)
    ]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS  {test.__name__}")
        except Exception:
            failures += 1
            print(f"FAIL  {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed, {failures} failed")
    raise SystemExit(1 if failures else 0)
