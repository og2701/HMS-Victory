import asyncio
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("OPENAI_TOKEN", "mock-token")  # translation builds its client at import

from config import GUILD_ID
from lib.bot import event_handlers


def _member(premium_since, guild_id=GUILD_ID):
    return SimpleNamespace(
        id=42, guild=SimpleNamespace(id=guild_id), premium_since=premium_since,
        timed_out_until=None, roles=[], _data={}, _state=MagicMock(),
    )


class BoostCountingTests(unittest.TestCase):
    def _run(self, before, after):
        with patch.object(event_handlers, "update_summary_data") as update, \
             patch.object(event_handlers, "initialize_summary_data"), \
             patch.object(event_handlers, "award_badge_with_notify", new_callable=AsyncMock):
            asyncio.run(event_handlers.on_member_update(before, after))
        return [c.args[0] for c in update.call_args_list]

    def test_new_booster_counts_as_gained(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(self._run(_member(None), _member(now)), ["boosters_gained"])

    def test_stopping_counts_as_lost(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(self._run(_member(now), _member(None)), ["boosters_lost"])

    def test_unrelated_updates_and_other_guilds_are_ignored(self):
        now = datetime.now(timezone.utc)
        self.assertEqual(self._run(_member(now), _member(now)), [])
        self.assertEqual(self._run(_member(None), _member(None)), [])
        self.assertEqual(self._run(_member(None, guild_id=1), _member(now, guild_id=1)), [])


if __name__ == "__main__":
    unittest.main()
