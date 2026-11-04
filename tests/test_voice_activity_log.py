import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("OPENAI_TOKEN", "mock-token")  # translation builds its client at import

import discord

from lib.bot import event_handlers, scheduled_tasks


def _m(uid, name):
    return SimpleNamespace(id=uid, display_name=name)


def _vc(name, members, cid=1):
    return SimpleNamespace(name=name, id=cid, members=members)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.client = SimpleNamespace()

    def _last(self):
        return self.client.voice_activity_log[-1]

    def test_join_lists_the_others_sorted_without_the_joiner(self):
        me = _m(1, "Steamrocker")
        room = _vc("Room", [_m(2, "piggy"), me, _m(3, "MJ")])
        event_handlers.record_voice_activity(self.client, "join", me, after_channel=room)
        self.assertEqual(self._last()["others"], ["MJ", "piggy"])

    def test_leave_lists_who_is_still_in_the_room_left(self):
        me = _m(1, "MJ")
        event_handlers.record_voice_activity(
            self.client, "leave", me, before_channel=_vc("Room", [_m(2, "Piggy")]))
        self.assertEqual(self._last()["others"], ["Piggy"])

    def test_move_lists_the_destination(self):
        me = _m(1, "Ana")
        event_handlers.record_voice_activity(
            self.client, "move", me,
            before_channel=_vc("Lounge", [_m(9, "Zed")]),
            after_channel=_vc("Gaming", [me, _m(2, "Ben")], cid=2))
        self.assertEqual(self._last()["others"], ["Ben"])

    def test_snapshot_does_not_follow_later_changes(self):
        me = _m(1, "A")
        members = [me, _m(2, "B")]
        event_handlers.record_voice_activity(self.client, "join", me, after_channel=_vc("R", members))
        members.append(_m(3, "C"))
        self.assertEqual(self._last()["others"], ["B"])

    def test_channel_without_members_gives_empty_list(self):
        event_handlers.record_voice_activity(
            self.client, "leave", _m(1, "A"), before_channel=SimpleNamespace(name="R", id=1))
        self.assertEqual(self._last()["others"], [])


class CompanyTests(unittest.TestCase):
    def test_tails(self):
        f = scheduled_tasks._voice_company
        self.assertEqual(f({"kind": "join", "others": ["A", "B"]}), " · with A, B")
        self.assertEqual(f({"kind": "move", "others": ["A"]}), " · with A")
        self.assertEqual(f({"kind": "leave", "others": ["A"]}), " · still in: A")
        self.assertEqual(f({"kind": "leave", "others": []}), " · now empty")
        self.assertEqual(f({"kind": "join", "others": []}), " · alone")

    def test_old_events_without_the_field_get_no_tail(self):
        self.assertEqual(scheduled_tasks._voice_company({"kind": "join"}), "")

    def test_cap_and_escaping(self):
        names = [f"n{i}" for i in range(11)]
        out = scheduled_tasks._voice_company({"kind": "join", "others": names})
        self.assertEqual(out, " · with " + ", ".join(names[:8]) + " +3 more")
        out = scheduled_tasks._voice_company({"kind": "join", "others": ["*bold*_x_"]})
        self.assertEqual(out, " · with \\*bold\\*\\_x\\_")


class DigestTests(unittest.TestCase):
    def test_digest_lines_and_no_pings(self):
        thread = MagicMock()
        thread.send = AsyncMock()
        client = MagicMock()
        client.get_channel.return_value = thread
        client.voice_activity_log = [
            {"at": 100, "kind": "join", "who": "Steamrocker", "id": 1, "from": None, "to": "Room",
             "from_id": None, "to_id": 5, "held": None, "others": ["MJ", "Piggy"]},
            {"at": 200, "kind": "leave", "who": "MJ", "id": 2, "from": "Room", "to": None,
             "from_id": 5, "to_id": None, "held": 240, "others": ["Piggy", "Steamrocker"]},
            {"at": 300, "kind": "leave", "who": "Piggy", "id": 3, "from": "Room", "to": None,
             "from_id": 5, "to_id": None, "held": None, "others": []},
        ]
        asyncio.run(scheduled_tasks.process_voice_activity_log(client))
        thread.send.assert_awaited_once()
        kwargs = thread.send.await_args.kwargs
        self.assertEqual(kwargs["embed"].description.splitlines(), [
            "<t:100:T> 🟢 **Steamrocker** joined **Room** · with MJ, Piggy",
            "<t:200:T> 🔴 **MJ** left **Room** after 4m · still in: Piggy, Steamrocker",
            "<t:300:T> 🔴 **Piggy** left **Room** · now empty",
        ])
        am = kwargs["allowed_mentions"]
        self.assertFalse(am.everyone or am.users or am.roles)


if __name__ == "__main__":
    unittest.main()
