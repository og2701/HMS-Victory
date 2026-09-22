"""The chronicle: a permanent, never-purged log of everything that happens in the server.

Separate from ``lib/features/message_archive.py``, which keeps 30 days of message text
purely so bulk deletes can be logged with content. This one is the analytics substrate -
messages, edits, deletes, reactions, mentions, emoji, voice, member churn and slash
command use, kept forever in their own ``chronicle.db``, for year-in-review stats.
"""

from lib.chronicle.db import ChronicleDB
from lib.chronicle.recorder import (
    flush,
    record_audit_entry,
    record_delete,
    record_edit,
    record_interaction,
    record_member_event,
    record_member_update,
    record_message,
    record_poll_vote,
    record_raw_edit,
    record_raw_reaction,
    record_voice,
    stats,
)

__all__ = [
    "ChronicleDB", "flush", "stats",
    "record_message", "record_edit", "record_raw_edit", "record_delete",
    "record_raw_reaction", "record_voice", "record_member_event",
    "record_member_update", "record_interaction", "record_audit_entry",
    "record_poll_vote",
]
