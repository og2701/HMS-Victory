import os
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from config import USERS
import lib.features.persona_control as pc
from lib.features.chat_responder import handle_chat_message, live_chat_manager

MAIN_GUILD, TEST_GUILD = 111, 222
GENERAL, LOUNGE, BOT_TEST = 1001, 1002, 2001


def _client():
    channels = {
        GENERAL: types.SimpleNamespace(name="general", guild=types.SimpleNamespace(id=MAIN_GUILD)),
        LOUNGE: types.SimpleNamespace(name="lounge", guild=types.SimpleNamespace(id=MAIN_GUILD)),
        BOT_TEST: types.SimpleNamespace(name="bot-test", guild=types.SimpleNamespace(id=TEST_GUILD)),
    }
    client = MagicMock()
    client.get_channel.side_effect = channels.get
    return client


class PersonaControlTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = (pc._state, pc._client, pc._settle_until)
        pc._client = _client()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self._card_file = patch.object(pc, "CARD_STATE_FILE", os.path.join(tmp.name, "persona_card.json"))
        self._card_file.start()
        self.addCleanup(self._card_file.stop)

    def tearDown(self):
        pc._state, pc._client, pc._settle_until = self._saved

    def test_systemctl_show_is_parsed(self):
        self.assertEqual(
            pc.parse_systemctl_show("ActiveState=active\nActiveEnterTimestampMonotonic=123456789\n"),
            ("active", 123456789),
        )
        self.assertEqual(pc.parse_systemctl_show(""), ("unknown", 0))

    def test_live_only_where_a_channel_is_on_and_the_service_runs(self):
        pc._state = pc.PersonaState(service="active", api_up=True,
                                    channels={GENERAL: "ambient", LOUNGE: "off", BOT_TEST: "off"})
        self.assertTrue(pc.persona_live(MAIN_GUILD))
        self.assertFalse(pc.persona_live(TEST_GUILD))
        self.assertFalse(pc.persona_live(None))  # DMs stay with Direct Replies
        pc._state = pc.PersonaState(service="active", api_up=True, channels={GENERAL: "off"})
        self.assertFalse(pc.persona_live(MAIN_GUILD))
        pc._state = pc.PersonaState(service="inactive", channels={GENERAL: "ambient"})
        self.assertFalse(pc.persona_live(MAIN_GUILD))

    async def test_restart_keeps_the_last_modes_while_the_api_is_down(self):
        pc._state = pc.PersonaState(service="active", api_up=True, channels={GENERAL: "ambient"})
        pc._settle_until = pc.time.time() + 60
        with patch.object(pc, "_run", AsyncMock(return_value=(0, "ActiveState=inactive\n"))), \
                patch.object(pc, "_api", AsyncMock(return_value=None)):
            state = await pc.refresh_state()
        self.assertEqual(state.service, "activating")
        self.assertTrue(pc.persona_live(MAIN_GUILD))

    async def test_stopped_service_shows_the_modes_it_comes_back_with(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write('{"channels": {"1001": {"mode": "ambient"}, "2001": {"mode": "weird"}}}')
        self.addCleanup(os.unlink, f.name)
        with patch.object(pc, "PERSONA_RUNTIME_FILE", f.name), \
                patch.object(pc, "_run", AsyncMock(return_value=(0, "ActiveState=inactive\n"))):
            state = await pc.refresh_state()
        self.assertEqual(state.channels, {GENERAL: "ambient", BOT_TEST: "off"})
        self.assertFalse(pc.persona_live(MAIN_GUILD))

    async def test_setting_a_mode_sends_every_other_channel_back_unchanged(self):
        runtime = {"channels": {"1001": {"mode": "off", "chattiness": 7, "cooldown": 3},
                                "2001": {"mode": "mention", "chattiness": 10, "cooldown": 3}}}
        api = AsyncMock(side_effect=[runtime, {"ok": True}])
        with patch.object(pc, "_api", api):
            self.assertTrue(await pc.set_channel_mode(GENERAL, "ambient"))
        method, path, payload = api.await_args_list[1].args
        self.assertEqual((method, path), ("POST", "/api/runtime"))
        self.assertEqual(payload["channels"]["1001"], {"mode": "ambient", "chattiness": 7, "cooldown": 3})
        self.assertEqual(payload["channels"]["2001"]["mode"], "mention")

    # test_live_chat_responder stubs discord when it's collected first, and the stub has no UI kit.
    @unittest.skipUnless(hasattr(pc.discord, "SelectOption"), "needs the real discord.py")
    async def test_card_offers_modes_only_while_running(self):
        running = pc.PersonaState(service="active", api_up=True, started_mono=1,
                                  channels={GENERAL: "ambient", BOT_TEST: "off"}, timers={GENERAL: 2e9})
        view = pc.PersonaCardView(running)
        selects = [i for i in view.walk_children() if isinstance(i, discord.ui.Select)]
        self.assertEqual([s.custom_id for s in selects], ["persona_ctl:mode:1001", "persona_ctl:mode:2001"])
        self.assertEqual([o.value for o in selects[0].options if o.default], ["ambient"])
        self.assertIn("keep", [o.value for o in selects[0].options])
        self.assertEqual([o.value for o in selects[0].options if o.value.startswith("sleep:")][:3],
                         ["sleep:5", "sleep:10", "sleep:15"])
        self.assertNotIn("keep", [o.value for o in selects[1].options])
        buttons = {b.custom_id: b.disabled for b in view.walk_children() if isinstance(b, discord.ui.Button)}
        self.assertEqual(buttons, {"persona_ctl:start": True, "persona_ctl:stop": False,
                                   "persona_ctl:restart": False, "persona_ctl:refresh": False})

        stopped = pc.PersonaCardView(pc.PersonaState(service="inactive", channels={GENERAL: "ambient"}))
        self.assertFalse([i for i in stopped.walk_children() if isinstance(i, discord.ui.Select)])
        buttons = {b.custom_id: b.disabled for b in stopped.walk_children() if isinstance(b, discord.ui.Button)}
        self.assertFalse(buttons["persona_ctl:start"])
        self.assertTrue(buttons["persona_ctl:stop"])

    def test_persona_card_is_told_apart_from_the_direct_replies_card(self):
        button = types.SimpleNamespace(custom_id="persona_ctl:refresh")
        card = types.SimpleNamespace(id=5, components=[types.SimpleNamespace(
            custom_id=None, children=[types.SimpleNamespace(custom_id=None, children=[button])])])
        other = types.SimpleNamespace(id=6, components=[types.SimpleNamespace(
            custom_id=None, children=[types.SimpleNamespace(custom_id="vic_live_refresh")])])
        self.assertTrue(pc.is_persona_card(card))
        self.assertFalse(pc.is_persona_card(other))
        pc.atomic_write_json(pc.CARD_STATE_FILE, {"message_id": "6"})
        self.assertTrue(pc.is_persona_card(other))

    @patch("lib.features.chat_responder.handle_one_off_owner_mention")
    async def test_direct_tags_stand_down_while_the_persona_bot_is_on(self, one_off):
        one_off.return_value = True
        client = MagicMock()
        client.user.id = 999999999
        message = MagicMock()
        message.author.bot = False
        message.author.id = USERS.OGGERS
        message.mentions = [client.user]
        message.content = f"<@{client.user.id}> you there"
        message.guild.id = MAIN_GUILD
        live_chat_manager.active = False
        live_chat_manager.owner_mentions_paused = False
        with patch("lib.features.chat_responder.plan_mention", return_value=None), \
                patch("lib.features.chat_responder.judge_mention", new_callable=AsyncMock, return_value=None):
            pc._state = pc.PersonaState(service="active", api_up=True, channels={GENERAL: "ambient"})
            self.assertFalse(await handle_chat_message(client, message))
            one_off.assert_not_called()
            pc._state = pc.PersonaState(service="active", api_up=True, channels={GENERAL: "off"})
            self.assertTrue(await handle_chat_message(client, message))
            one_off.assert_called_once_with(client, message)


if __name__ == "__main__":
    unittest.main()
