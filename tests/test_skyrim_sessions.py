"""Launch/settlement regressions. All storage is isolated, including views."""
import asyncio
import copy
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


class SessionTests(unittest.TestCase):
    def setUp(self):
        import config
        from lib.features.skyrim import engine as E
        from lib.core import file_operations as F
        self.E = E
        self.folder = tempfile.TemporaryDirectory(prefix="skyrim_session_test_")
        self.addCleanup(self.folder.cleanup)
        changes = {key: str(Path(self.folder.name) / (key + ".json")) for key in
                   ("SKYRIM_PROFILES_FILE", "SKYRIM_DAILY_FILE", "SKYRIM_GRAVEYARD_FILE",
                    "SKYRIM_WORLDBOSS_FILE", "PERSISTENT_VIEWS_FILE")}
        self.patch = patch.multiple(config, **changes)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.view_patch = patch.object(F, "PERSISTENT_VIEWS_FILE", changes["PERSISTENT_VIEWS_FILE"])
        self.view_patch.start()
        self.addCleanup(self.view_patch.stop)
        self.p = E.create_profile(991, "Session tester", "warrior")
        E.drain_log()

    def active(self, *, pacts=None, engaged=False):
        E = self.E
        d = E.Delve(self.p["user_id"], self.p["name"], 1, "embershard",
                    [{"kind": "enemy", "key": "bandit", "boss": False, "resolved": False}],
                    hearts=3, shout_charges=0, satchel=100, ingredients={"troll_fat": 2},
                    engaged=engaged, pacts=pacts)
        d.message_id = 111
        self.p["active_delve"] = 111
        E.save_profile(self.p)
        E.save_delve(d)
        return d

    def test_failed_post_changes_no_profile_or_existing_run(self):
        from lib.features.skyrim import sessions as S
        old = self.active()
        before = self.E.get_profile(self.p["user_id"])
        old_data = old.to_dict()
        pending = S.prepare(before, 1, "embershard")
        self.assertIsNotNone(pending.delve)
        # Simulated send failure: no commit is made.
        self.assertEqual(self.E.get_profile(self.p["user_id"]), before)
        self.assertEqual(self.E.load_delve(111).to_dict(), old_data)
        self.assertEqual(self.E.drain_log(), [])

    def test_commit_uses_flee_rules_and_banks_ingredients_once(self):
        from lib.features.skyrim import sessions as S
        self.active(engaged=True)
        before = self.E.get_profile(self.p["user_id"])
        pending = S.prepare(before, 1, "embershard")
        pending.commit(222)
        after = self.E.get_profile(self.p["user_id"])
        self.assertEqual(after["septims"], before["septims"] + 70)
        self.assertEqual(after["ingredients"]["troll_fat"], 2)
        self.assertEqual(after["active_delve"], 222)
        self.assertIsNone(self.E.load_delve(111))
        self.assertIsNotNone(self.E.load_delve(222))
        pending.commit(222)
        self.assertEqual(self.E.get_profile(self.p["user_id"]), after)

    def test_no_exit_pact_cannot_be_bypassed_by_replacement(self):
        from lib.features.skyrim import sessions as S
        self.active(pacts=["clavicus"])
        before = self.E.get_profile(self.p["user_id"])
        with self.assertRaisesRegex(ValueError, "pact|Bargain|exit"):
            S.prepare(before, 1, "embershard")
        self.assertEqual(self.E.get_profile(self.p["user_id"]), before)
        self.assertIsNotNone(self.E.load_delve(111))

    def test_failed_post_preserves_selected_supplies_and_daily_attempt(self):
        from lib.features.skyrim import sessions as S
        self.p["elixirs"] = {"vigor": 1}
        self.p["nextelixirs"] = ["vigor"]
        self.E.save_profile(self.p)
        before = self.E.get_profile(self.p["user_id"])
        pending = S.prepare(before, 1, "embershard", kind="daily")
        self.assertEqual(pending.profile["elixirs"].get("vigor", 0), 0)
        after = self.E.get_profile(self.p["user_id"])
        self.assertEqual(after, before)
        self.assertTrue(self.E.daily_available(after))

    def test_profile_commit_recovers_after_board_file_failure(self):
        from lib.features.skyrim import sessions as S
        self.active(engaged=True)
        pending = S.prepare(self.E.get_profile(self.p["user_id"]), 1, "embershard")
        with patch.object(self.E, "save_persistent_views", side_effect=OSError("disk busy")):
            with self.assertLogs(S.logger, level="ERROR"):
                pending.commit(222)
        self.assertTrue(pending.committed)
        raw = self.E._profiles()[str(self.p["user_id"])]
        self.assertIn("_launch_commit", raw)
        self.assertEqual(raw["septims"], 70)
        S.recover_all()
        S.recover_all()
        after = self.E.get_profile(self.p["user_id"])
        self.assertNotIn("_launch_commit", after)
        self.assertEqual(after["septims"], 70)
        self.assertEqual(after["ingredients"]["troll_fat"], 2)
        self.assertIsNone(self.E.load_delve(111))
        self.assertEqual(self.E.load_delve(222).player_id, self.p["user_id"])

    def test_concurrent_character_change_rejects_stale_commit(self):
        from lib.features.skyrim import sessions as S
        before = self.E.get_profile(self.p["user_id"])
        pending = S.prepare(before, 1, "embershard")
        changed = copy.deepcopy(before)
        changed["septims"] += 40
        self.E.save_profile(changed)
        with self.assertRaisesRegex(ValueError, "character changed"):
            pending.commit(222)
        self.assertEqual(self.E.get_profile(self.p["user_id"]), changed)
        self.assertIsNone(self.E.load_delve(222))

    def test_board_only_action_rejects_stale_clean_exit(self):
        from lib.features.skyrim import sessions as S
        self.active()
        before = self.E.get_profile(self.p["user_id"])
        pending = S.prepare(before, 1, "embershard")
        latest = self.E.load_delve(111)
        latest.engaged = True
        self.E.save_delve(latest)
        self.assertEqual(self.E.get_profile(self.p["user_id"]), before)
        with self.assertRaisesRegex(ValueError, "adventure changed"):
            pending.commit(222)
        self.assertEqual(self.E.get_profile(self.p["user_id"]), before)
        self.assertTrue(self.E.load_delve(111).engaged)

    def test_per_player_launch_lock_serialises_double_clicks(self):
        from lib.features.skyrim import sessions as S
        async def scenario():
            entered = []
            ready = asyncio.Event()
            release = asyncio.Event()
            async def first():
                async with S.launch_lock(991):
                    entered.append("first")
                    ready.set()
                    await release.wait()
            async def second():
                await ready.wait()
                async with S.launch_lock(991):
                    entered.append("second")
            a = asyncio.create_task(first())
            b = asyncio.create_task(second())
            await ready.wait()
            await asyncio.sleep(0)
            self.assertEqual(entered, ["first"])
            release.set()
            await asyncio.gather(a, b)
            self.assertEqual(entered, ["first", "second"])
        asyncio.run(scenario())
