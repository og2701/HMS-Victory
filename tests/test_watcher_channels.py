import json
import os
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import tests.test_live_chat_responder  # noqa: F401  (installs the discord stubs when discord isn't installed)
from config import USERS
from lib.features import watcher_channels as wc
from lib.features.chat_responder import handle_chat_message, live_chat_manager

GENERAL = 959493057076666380
OTHER_USER = 111


class WatcherChannelsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.dir.name, "extra_channels.json")
        self.heartbeat = os.path.join(self.dir.name, "heartbeat")
        self.patches = [patch.object(wc, "STATE_FILE", self.state), patch.object(wc, "HEARTBEAT_FILE", self.heartbeat)]
        for p in self.patches:
            p.start()
        wc.clear_cache()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        wc.clear_cache()
        self.dir.cleanup()

    def write(self, expires_in=1800, beat_age=0):
        now = time.time()
        with open(self.state, "w") as f:
            json.dump({"watches": [{"channel_id": str(GENERAL), "users": [str(USERS.OGGERS), str(USERS.HADIDAS)],
                                    "expires_at": now + expires_in, "request_id": "1"}]}, f)
        open(self.heartbeat, "w").close()
        os.utime(self.heartbeat, (now - beat_age, now - beat_age))
        wc.clear_cache()

    def test_no_file_means_no_watch(self):
        self.assertIsNone(wc.watch_for(GENERAL, USERS.OGGERS))

    def test_allowed_user_in_watched_channel(self):
        self.write()
        self.assertIsNotNone(wc.watch_for(GENERAL, USERS.OGGERS))
        self.assertIsNotNone(wc.watch_for(GENERAL, USERS.HADIDAS))

    def test_other_users_and_channels_untouched(self):
        self.write()
        self.assertIsNone(wc.watch_for(GENERAL, OTHER_USER))
        self.assertIsNone(wc.watch_for(GENERAL + 1, USERS.OGGERS))

    def test_expired_watch_stops_counting(self):
        self.write(expires_in=-1)
        self.assertIsNone(wc.watch_for(GENERAL, USERS.OGGERS))

    def test_stale_heartbeat_stops_counting(self):
        self.write(beat_age=wc.HEARTBEAT_MAX_AGE + 30)
        self.assertIsNone(wc.watch_for(GENERAL, USERS.OGGERS))

    def test_missing_heartbeat_stops_counting(self):
        self.write()
        os.remove(self.heartbeat)
        self.assertIsNone(wc.watch_for(GENERAL, USERS.OGGERS))

    def test_corrupt_file_is_no_watch(self):
        with open(self.state, "w") as f:
            f.write("{nope")
        open(self.heartbeat, "w").close()
        self.assertIsNone(wc.watch_for(GENERAL, USERS.OGGERS))


class ResponderStandsDownTest(unittest.IsolatedAsyncioTestCase):
    def _message(self, client, author_id):
        message = MagicMock()
        message.author.bot = False
        message.author.id = author_id
        message.channel.id = GENERAL
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> hello"
        return message

    @patch("lib.features.chat_responder.handle_one_off_owner_mention", new_callable=AsyncMock)
    async def test_watched_user_is_left_to_the_watcher(self, one_off):
        client = MagicMock()
        client.user.id = 999999999
        live_chat_manager.active = False
        with patch("lib.features.watcher_channels.watch_for", return_value={"request_id": "1", "expires_at": time.time() + 60}):
            self.assertFalse(await handle_chat_message(client, self._message(client, USERS.OGGERS)))
        one_off.assert_not_called()

    @patch("lib.features.chat_responder.handle_one_off_owner_mention", new_callable=AsyncMock)
    async def test_without_a_watch_vic_answers(self, one_off):
        one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999
        live_chat_manager.active = False
        with patch("lib.features.watcher_channels.watch_for", return_value=None), \
                patch("lib.features.persona_control.persona_live", return_value=False):
            self.assertTrue(await handle_chat_message(client, self._message(client, USERS.OGGERS)))
        one_off.assert_called_once()


if __name__ == "__main__":
    unittest.main()
