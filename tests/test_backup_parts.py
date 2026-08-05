"""Backups split across messages when they outgrow Discord's upload cap, and rejoin exactly.

Both backups grew into a 413 and then failed silently - the database from 2026-07-15, the
JSON archive by August - so the split has to be lossless and the restore has to find it.
"""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from lib.bot import backup_manager as bm


class _FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, *, file):
        self.sent.append((file.filename, file.fp.read()))


@pytest.mark.asyncio
async def test_oversized_archive_splits_and_rejoins_byte_exact():
    payload = os.urandom(bm.MAX_PART_SIZE * 2 + 12345)
    ch = _FakeChannel()

    n = await bm._send_archive_in_parts(ch, io.BytesIO(payload), "database_backup_", "TS")

    assert n == 3
    assert [f for f, _ in ch.sent] == [
        "database_backup_TS_part1.zip",
        "database_backup_TS_part2.zip",
        "database_backup_TS_part3.zip",
    ]
    assert b"".join(b for _, b in ch.sent) == payload          # lossless
    assert all(len(b) <= bm.MAX_PART_SIZE for _, b in ch.sent)  # every part under the cap


@pytest.mark.asyncio
async def test_small_archive_stays_a_single_unsuffixed_file():
    """The old naming has to survive, or existing backups stop being findable."""
    payload = b"x" * 1024
    ch = _FakeChannel()

    n = await bm._send_archive_in_parts(ch, io.BytesIO(payload), "json_backup_", "TS")

    assert n == 1
    assert ch.sent[0][0] == "json_backup_TS.zip"
    assert ch.sent[0][1] == payload


def test_part_parsing_covers_both_shapes_and_ignores_neighbours():
    assert bm._db_backup_part("database_backup_2026-07-15_21-01-23.zip") == ("2026-07-15_21-01-23", 1)
    assert bm._db_backup_part("database_backup_2026-08-05_10-00-00_part2.zip") == ("2026-08-05_10-00-00", 2)
    assert bm._db_backup_part("database_backup_2026-08-05_10-00-00_part12.zip") == ("2026-08-05_10-00-00", 12)
    assert bm._db_backup_part("database_backup_2026-01-01_00-00-00.db") == ("2026-01-01_00-00-00", 1)
    # The channel is mostly other people's archives - none of them are database backups.
    assert bm._db_backup_part("json_backup_2026-07-28_22-56-31.zip") is None
    assert bm._db_backup_part("daily_summaries_as_of_2026-08-04_part1.zip") is None
    assert bm._backup_part("json_backup_2026-07-28_22-56-31.zip", "json_backup_", (".zip",)) \
        == ("2026-07-28_22-56-31", 1)


def test_restore_scan_reaches_past_a_day_of_five_minute_json_backups():
    """The old limit of 100 reached back about eight hours, so a daily database backup was
    never once inside the window it was searched in."""
    json_backups_per_day = 24 * 60 / 5
    assert bm.DB_RESTORE_SCAN_LIMIT > json_backups_per_day * 7
