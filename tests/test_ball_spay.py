"""Spayed: catch parsing, XP-style message counting, the chronicle reads and a whole sweep."""

import asyncio
import os
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import database  # noqa: E402
from config import CHANNELS, ROLES, USERS  # noqa: E402
from lib.chronicle.db import ChronicleDB  # noqa: E402
from lib.features import ball_spay  # noqa: E402

DEX = USERS.COUNTRYBALL_BOT
SPAM = CHANNELS.BOT_SPAM
GENERAL = CHANNELS.GENERAL
NOW = int(datetime(2026, 10, 14, 12, tzinfo=timezone.utc).timestamp())


def _catch(uid):
    return f"<@{uid}> You caught **Belize**! `(#76BD4B6, -8%/+1%)`"


class CatchParsingTest(unittest.TestCase):
    def test_every_catch_wording_names_the_catcher(self):
        for text in (
            "<@11> You caught **Mayan Empire**! `(#76BE560, -10%/+0%)`\n\n> *catchphrase <@99>*",
            "<@11>, **Kyrgyzstan** secured! `(#76BD7EF, -7%/-19%)`",
            "**Belize** almost slipped away but <@11> got there first! `(#76BD4B6, -8%/+1%)`",
            "<@11> You caught a wild **Syria**! `(#76B6F98, -20%/-12%)`",
            "<@!11>, **Madagascar** has joined your collection! `(#76B7CBE, +18%/+0%)`",
        ):
            self.assertEqual(ball_spay.catcher_of(text), 11, text)

    def test_misses_and_other_chatter_are_not_catches(self):
        for text in (
            "<@11> Sorry, this countryball was caught already!",
            "<@11>, oops - too slow!",
            "<@11> Try again next time!",
            "💰 <@11> bought 🍬 **Yuan Dynasty** from the wandering tradesman for 837 ballsbux!",
            "", None,
        ):
            self.assertIsNone(ball_spay.catcher_of(text), text)


def _row(ts, content="hello there", channel=GENERAL, parent=None, msg_type="default",
         n_att=0, stickers=None, day=None):
    return (ts, day or f"day{ts // 86400}", msg_type, content, n_att, stickers, channel, parent)


class ContributionTest(unittest.TestCase):
    def test_counted_like_xp(self):
        rows = [
            _row(0),
            _row(60),                                  # inside the two-minute gap
            _row(120),
            _row(300, channel=SPAM),                   # bot-spam
            _row(600, channel=1, parent=SPAM),         # a thread under bot-spam
            _row(900, content="k"),                    # one character
            _row(1000, content="", n_att=1),           # a picture still counts
            _row(1200, content="", stickers='[{"id": 1, "name": "x"}]'),
            _row(1400, msg_type="new_member"),         # system message
            _row(1600, msg_type=None),                 # recorded before msg_type existed
            _row(86400 * 3),
        ]
        self.assertEqual(ball_spay.count_contribution(rows), (6, 2))

    def test_skipped_messages_do_not_start_the_cooldown(self):
        rows = [_row(0, channel=SPAM), _row(30)]
        self.assertEqual(ball_spay.count_contribution(rows), (1, 1))


class PickTest(unittest.TestCase):
    def test_only_eligible_quiet_catchers_over_the_line(self):
        from collections import Counter
        catches = Counter({1: 100, 2: 50, 3: 40, 4: 19, 5: 30})
        recent = {1: (2, 1), 2: (10, 4), 3: (0, 0), 5: (0, 0)}
        picked = ball_spay.pick_spays(catches, recent, eligible={1, 2, 3, 4})
        # 2 talks enough, 4 is under the catch line, 5 isn't eligible (staff, pardoned...).
        self.assertEqual(picked, [(1, 100, 2), (3, 40, 0)])

    def test_lift_needs_both_messages_and_days(self):
        self.assertTrue(ball_spay.due_lift(30, 5))
        self.assertFalse(ball_spay.due_lift(29, 9))
        self.assertFalse(ball_spay.due_lift(80, 4))


class _Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        if database.DatabaseManager._connection is not None:
            database.DatabaseManager._connection.close()
            database.DatabaseManager._connection = None
        database.DB_FILE = os.path.join(self.tmp, "t.db")
        database.init_db()
        self.chron = os.path.join(self.tmp, "chronicle.db")
        ChronicleDB.set_path(self.chron)
        ChronicleDB.get_connection()
        self._next_id = 1000
        self._old_path = ball_spay.CHRONICLE_DB_FILE
        ball_spay.CHRONICLE_DB_FILE = self.chron

    def tearDown(self):
        ChronicleDB.close()
        ball_spay.CHRONICLE_DB_FILE = self._old_path
        if database.DatabaseManager._connection is not None:
            database.DatabaseManager._connection.close()
            database.DatabaseManager._connection = None

    def msg(self, uid, ts, content, channel=GENERAL, bot=False, day=None):
        self._next_id += 1
        ChronicleDB.execute(
            "INSERT INTO messages (message_id, channel_id, user_id, is_bot, content, ts, "
            "local_day, msg_type) VALUES (?, ?, ?, ?, ?, ?, ?, 'default')",
            (self._next_id, channel, uid, int(bot), content, ts, day or f"d{ts // 86400}"))

    def catches(self, uid, n, ts=NOW - 86400):
        for i in range(n):
            self.msg(DEX, ts + i, _catch(uid), channel=SPAM, bot=True)

    def chat(self, uid, n, start, days=1):
        per_day = max(1, n // days)
        for i in range(n):
            self.msg(uid, start + (i // per_day) * 86400 + (i % per_day) * 300, f"message {i}")


class GatherTest(_Fixture):
    def test_reads_the_window_and_since_each_spay(self):
        self.catches(1, 25)
        self.catches(2, 25)
        self.catches(3, 5)
        self.catches(1, 50, ts=NOW - 40 * 86400)      # outside the window
        self.chat(2, 12, NOW - 5 * 86400)
        self.chat(9, 30, NOW - 10 * 86400, days=6)
        catches, recent, lifts = ball_spay.gather(NOW, {9: NOW - 20 * 86400}, path=self.chron)
        self.assertEqual(catches[1], 25)
        self.assertEqual(set(recent), {1, 2})
        self.assertEqual(recent[1], (0, 0))
        self.assertEqual(recent[2][0], 12)
        self.assertEqual(lifts[9], (30, 6))


class FakeRole:
    def __init__(self, guild, role_id):
        self.guild, self.id = guild, role_id

    @property
    def members(self):
        return [m for m in self.guild.members.values() if self in m.roles]


class FakeMember:
    def __init__(self, guild, uid, roles=(), booster=False, bot=False):
        self.guild, self.id, self.bot = guild, uid, bot
        self.roles = list(roles)
        self.premium_since = datetime(2026, 1, 1, tzinfo=timezone.utc) if booster else None
        self.display_name = f"member{uid}"
        self.mention = f"<@{uid}>"

    async def add_roles(self, role, reason=None):
        self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        self.roles.remove(role)


class FakeGuild:
    def __init__(self):
        self.id = ball_spay.GUILD_ID
        self.members = {}
        self.spayed = FakeRole(self, ROLES.SPAYED)
        self.staff = FakeRole(self, ROLES.BORDER_FORCE)

    def add(self, uid, **kw):
        self.members[uid] = FakeMember(self, uid, **kw)
        return self.members[uid]

    def get_member(self, uid):
        return self.members.get(uid)

    def get_role(self, role_id):
        return self.spayed if role_id == ROLES.SPAYED else None


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, view=None, allowed_mentions=None):
        self.sent.append(view or content)


class SweepTest(_Fixture):
    def setUp(self):
        super().setUp()
        self.guild = FakeGuild()
        self.police = FakeChannel()
        self.client = SimpleNamespace(get_guild=lambda gid: self.guild,
                                      get_channel=lambda cid: self.police)

    def run_sweep(self):
        asyncio.run(ball_spay._sweep(self.client))

    def test_spays_quiet_farmers_only(self):
        farmer = self.guild.add(1)
        chatter = self.guild.add(2)
        staff = self.guild.add(3, roles=[self.guild.staff])
        booster = self.guild.add(4, booster=True)
        for uid in (1, 2, 3, 4, 5):                    # 5 has left the server
            self.catches(uid, 30)
        self.chat(2, 15, NOW - 10 * 86400, days=5)
        with unittest.mock.patch.object(ball_spay.time, "time", return_value=NOW):
            self.run_sweep()
        self.assertIn(self.guild.spayed, farmer.roles)
        for m in (chatter, staff, booster):
            self.assertNotIn(self.guild.spayed, m.roles)
        self.assertEqual(ball_spay._row(1), ("auto", NOW))
        self.assertEqual(len(self.police.sent), 1)
        # Running again changes nothing and posts nothing.
        with unittest.mock.patch.object(ball_spay.time, "time", return_value=NOW):
            self.run_sweep()
        self.assertEqual(len(self.police.sent), 1)

    def test_hand_spays_are_adopted_and_never_lifted(self):
        held = self.guild.add(7)
        held.roles.append(self.guild.spayed)
        self.chat(7, 40, NOW - 10 * 86400, days=8)
        self.run_sweep()
        self.assertEqual(ball_spay._row(7)[0], "staff")
        self.assertIn(self.guild.spayed, held.roles)

    def test_lifts_once_they_chat(self):
        member = self.guild.add(8, roles=[self.guild.spayed])
        ball_spay._record(8, "auto", NOW - 20 * 86400, 40, 0)
        self.chat(8, 30, NOW - 10 * 86400, days=6)
        with unittest.mock.patch.object(ball_spay.time, "time", return_value=NOW):
            self.run_sweep()
        self.assertNotIn(self.guild.spayed, member.roles)
        self.assertIsNone(ball_spay._row(8))

    def test_pardoned_members_are_left_alone(self):
        member = self.guild.add(1)
        self.catches(1, 30)
        ball_spay.pardon(1, by_id=3)
        with unittest.mock.patch.object(ball_spay.time, "time", return_value=NOW):
            self.run_sweep()
        self.assertNotIn(self.guild.spayed, member.roles)

    def test_shadow_mode_reports_without_touching_roles(self):
        member = self.guild.add(1)
        self.catches(1, 30)
        with unittest.mock.patch.object(ball_spay.config, "BALL_SPAY_LIVE", False), \
                unittest.mock.patch.object(ball_spay.time, "time", return_value=NOW):
            self.run_sweep()
        self.assertNotIn(self.guild.spayed, member.roles)
        self.assertIsNone(ball_spay._row(1))
        self.assertEqual(len(self.police.sent), 1)

    def test_rejoin_brings_the_role_back(self):
        ball_spay._record(1, "auto", NOW)
        member = self.guild.add(1)
        asyncio.run(ball_spay.on_member_join(member))
        self.assertIn(self.guild.spayed, member.roles)

    def test_staff_taking_an_auto_spay_off_is_a_pardon(self):
        ball_spay._record(1, "auto", NOW)
        member = self.guild.add(1)
        member._state = SimpleNamespace(_get_client=lambda: self.client)
        before = SimpleNamespace(roles=[self.guild.spayed], guild=self.guild)
        asyncio.run(ball_spay.on_member_update(before, member))
        self.assertIsNone(ball_spay._row(1))
        self.assertIn(1, ball_spay._pardoned())

    def test_bot_lifts_are_not_mistaken_for_staff(self):
        # The bot deletes its row before removing the role, so no pardon follows.
        member = self.guild.add(1)
        before = SimpleNamespace(roles=[self.guild.spayed], guild=self.guild)
        asyncio.run(ball_spay.on_member_update(before, member))
        self.assertNotIn(1, ball_spay._pardoned())


class CardTest(unittest.TestCase):
    def test_card_has_a_pardon_per_member_and_fits(self):
        async def build():
            members = [SimpleNamespace(id=i, mention=f"<@{i}>", display_name=f"m{i}")
                       for i in range(ball_spay.CARD_LIMIT)]
            return ball_spay.spay_card([(m, 30, 1) for m in members])
        view = asyncio.run(build())
        buttons = [c for c in view.walk_children() if hasattr(c, "custom_id") and c.custom_id]
        self.assertEqual(len(buttons), ball_spay.CARD_LIMIT)
        self.assertLessEqual(len(list(view.walk_children())), 40)


if __name__ == "__main__":
    unittest.main()
