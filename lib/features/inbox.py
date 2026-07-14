"""Durable, user-scoped notification inbox storage.

The database schema is created centrally by :func:`database.init_db`; this module
only exposes parameterised CRUD helpers.  Every mutation includes ``user_id`` in
its predicate so a caller can never mark or delete another user's notifications
by supplying an ID they do not own.
"""

from dataclasses import dataclass
import time

from database import DatabaseManager


MAX_PAGE_SIZE = 100
_COLUMNS = "id, user_id, category, title, body, jump_url, created_at, read_at"


@dataclass(frozen=True, slots=True)
class Notification:
    id: int
    user_id: str
    category: str
    title: str
    body: str
    jump_url: str | None
    created_at: int
    read_at: int | None

    @property
    def is_unread(self) -> bool:
        return self.read_at is None


def _required_text(name: str, value) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _timestamp(value, *, default_now: bool = False) -> int:
    if value is None and default_now:
        return int(time.time())
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be an integer") from exc
    if result < 0:
        raise ValueError("timestamp cannot be negative")
    return result


def _row_to_notification(row) -> Notification:
    return Notification(
        id=int(row[0]),
        user_id=str(row[1]),
        category=str(row[2]),
        title=str(row[3]),
        body=str(row[4]),
        jump_url=str(row[5]) if row[5] else None,
        created_at=int(row[6]),
        read_at=int(row[7]) if row[7] is not None else None,
    )


def create_notification(
    user_id,
    category,
    title,
    body,
    jump_url=None,
    *,
    created_at=None,
) -> int:
    """Create a notification and return its integer primary key.

    ``created_at`` is injectable for migrations/tests; normal producers should
    omit it.  Text is stored verbatim apart from surrounding whitespace.  The UI
    layer is responsible for escaping it for Discord.
    """
    uid = _required_text("user_id", user_id)
    category_text = _required_text("category", category)
    title_text = _required_text("title", title)
    body_text = _required_text("body", body)
    jump = None if jump_url is None else str(jump_url).strip() or None
    created = _timestamp(created_at, default_now=True)

    return DatabaseManager.execute_insert(
        "INSERT INTO notifications "
        "(user_id, category, title, body, jump_url, created_at, read_at) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL)",
        (uid, category_text, title_text, body_text, jump, created),
    )


def list_notifications(
    user_id,
    *,
    limit: int = 10,
    offset: int = 0,
    unread_only: bool = False,
) -> list[Notification]:
    """List a user's newest notifications with bounded limit/offset pagination."""
    uid = _required_text("user_id", user_id)
    try:
        page_limit = int(limit)
        page_offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers") from exc
    if not 1 <= page_limit <= MAX_PAGE_SIZE:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if page_offset < 0:
        raise ValueError("offset cannot be negative")

    unread_clause = " AND read_at IS NULL" if unread_only else ""
    rows = DatabaseManager.fetch_all(
        f"SELECT {_COLUMNS} FROM notifications "
        f"WHERE user_id = ?{unread_clause} "
        "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (uid, page_limit, page_offset),
    ) or []
    return [_row_to_notification(row) for row in rows]


def count_notifications(user_id, *, unread_only: bool = False) -> int:
    """Count all (or only unread) notifications owned by ``user_id``."""
    uid = _required_text("user_id", user_id)
    unread_clause = " AND read_at IS NULL" if unread_only else ""
    row = DatabaseManager.fetch_one(
        f"SELECT COUNT(*) FROM notifications WHERE user_id = ?{unread_clause}",
        (uid,),
    )
    return int(row[0]) if row else 0


def count_unread_notifications(user_id) -> int:
    """Convenience wrapper used by command badges and the inbox UI."""
    return count_notifications(user_id, unread_only=True)


def mark_notification_read(user_id, notification_id, *, read_at=None) -> bool:
    """Mark one owned notification read; return whether a row changed."""
    uid = _required_text("user_id", user_id)
    try:
        notification_id = int(notification_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("notification_id must be an integer") from exc
    if notification_id < 1:
        raise ValueError("notification_id must be positive")
    read_timestamp = _timestamp(read_at, default_now=True)
    changed = DatabaseManager.execute(
        "UPDATE notifications SET read_at = ? "
        "WHERE id = ? AND user_id = ? AND read_at IS NULL",
        (read_timestamp, notification_id, uid),
    )
    return changed > 0


def mark_notifications_read(user_id, notification_ids, *, read_at=None) -> int:
    """Mark a bounded set of owned notifications read in one transaction."""
    uid = _required_text("user_id", user_id)
    try:
        ids = sorted({int(notification_id) for notification_id in notification_ids})
    except (TypeError, ValueError) as exc:
        raise ValueError("notification_ids must contain integers") from exc
    if any(notification_id < 1 for notification_id in ids):
        raise ValueError("notification_ids must be positive")
    if len(ids) > MAX_PAGE_SIZE:
        raise ValueError(
            f"notification_ids cannot contain more than {MAX_PAGE_SIZE} entries"
        )
    if not ids:
        return 0
    read_timestamp = _timestamp(read_at, default_now=True)
    placeholders = ", ".join("?" for _ in ids)
    return DatabaseManager.execute(
        f"UPDATE notifications SET read_at = ? "
        f"WHERE user_id = ? AND read_at IS NULL AND id IN ({placeholders})",
        (read_timestamp, uid, *ids),
    )


def mark_all_notifications_read(user_id, *, read_at=None) -> int:
    """Mark every unread notification owned by ``user_id`` and return the count."""
    uid = _required_text("user_id", user_id)
    read_timestamp = _timestamp(read_at, default_now=True)
    return DatabaseManager.execute(
        "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
        (read_timestamp, uid),
    )


def clear_read_notifications(user_id) -> int:
    """Delete only read notifications owned by ``user_id`` and return the count."""
    uid = _required_text("user_id", user_id)
    return DatabaseManager.execute(
        "DELETE FROM notifications WHERE user_id = ? AND read_at IS NOT NULL",
        (uid,),
    )
