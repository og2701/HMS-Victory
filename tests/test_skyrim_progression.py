"""Progression integration checks. Every engine file points into a temporary directory."""

import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E
from lib.features.skyrim import progression as P


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="skyrim_progression_")
        self.addCleanup(self.tmp.cleanup)
        for name in ("SKYRIM_PROFILES_FILE", "PERSISTENT_VIEWS_FILE", "SKYRIM_WORLDBOSS_FILE",
                     "SKYRIM_DAILY_FILE", "SKYRIM_GRAVEYARD_FILE"):
            p = patch.object(config, name, os.path.join(self.tmp.name, name + ".json"), create=True)
            p.start()
            self.addCleanup(p.stop)
        self.profile = E.create_profile(101, "Tester", "warrior")
        # Stable, transparent payouts: these tests focus on settlement, not weather.
        p = patch.object(E, "weather_today", return_value={"xp": 1.0, "loot": 1.0})
        p.start()
        self.addCleanup(p.stop)

    def _old_tasks(self, complete_all=False):
        keys = E.weekly_tasks("2026-08-24")
        selected = keys if complete_all else keys[:1]
        self.profile["tasks"] = {
            "week": "2026-35", "prog": {k: D.TASKS[k]["n"] for k in selected},
            "claimed": [], "bonus": False,
        }
        return keys

    def _hunt(self, coin=650):
        return {"week": "2026-35", "boss": next(iter(D.WORLD_BOSSES)),
                "shares": {"101": {"septims": coin, "xp": 170, "claimed": False}}}

    def test_completed_tasks_settle_once_and_partial_tasks_do_not(self):
        keys = self._old_tasks()
        self.profile["tasks"]["prog"][keys[1]] = D.TASKS[keys[1]]["n"] - 1
        band = D.TASKS[keys[0]]["band"]
        coin, xp = D.TASK_REWARDS[band]
        self.assertIsNotNone(P.settle_task_rollover(self.profile, "2026-36"))
        self.assertEqual(self.profile["septims"], coin)
        self.assertEqual(self.profile["xp"], xp)
        self.assertEqual(self.profile["stats"]["tasks_done"], 1)
        self.assertIsNone(P.settle_task_rollover(self.profile, "2026-36"))
        self.assertEqual(self.profile["septims"], coin)

    def test_sweep_and_saved_profile_retry(self):
        keys = self._old_tasks(True)
        expected = sum(D.TASK_REWARDS[D.TASKS[k]["band"]][0] for k in keys) + D.TASK_ALL_BONUS[0]
        before = copy.deepcopy(self.profile)
        P.settle_task_rollover(self.profile, "2026-36")
        E.save_profile(self.profile)
        persisted = E.get_profile(101)
        P.settle_task_rollover(persisted, "2026-36")
        self.assertEqual(persisted["septims"], expected)
        # Crash before save: fresh old profile computes exactly the same result.
        P.settle_task_rollover(before, "2026-36")
        self.assertEqual(before["septims"], expected)

    def test_current_or_malformed_task_week_is_not_settled(self):
        self._old_tasks(True)
        self.assertIsNone(P.settle_task_rollover(self.profile, "2026-35"))
        self.profile["tasks"]["week"] = "broken"
        self.assertIsNone(P.settle_task_rollover(self.profile, "2026-36"))
        self.assertEqual(self.profile["septims"], 0)

    def test_hunt_rollover_carries_rewards_and_claims_once(self):
        old = self._hunt()
        self.assertTrue(P.capture_hunt_rewards(old))
        self.assertFalse(P.capture_hunt_rewards(old))
        new = {"week": "2026-36", "shares": {}}
        P.preserve_hunt_mailbox(old, new)
        self.assertEqual(P.hunt_rewards_waiting(self.profile, new)["septims"], 650)
        self.assertIsNotNone(P.claim_hunt_rewards(self.profile, new))
        self.assertEqual(self.profile["septims"], 650)
        self.assertIsNone(P.claim_hunt_rewards(self.profile, new))
        self.assertIsNone(P.hunt_rewards_waiting(self.profile, new))

    def test_multiple_waves_pay_only_new_increments(self):
        store = self._hunt()
        P.capture_hunt_rewards(store)
        store["shares"]["101"]["septims"] += 800
        store["shares"]["101"]["xp"] += 200
        P.capture_hunt_rewards(store)
        P.claim_hunt_rewards(self.profile, store)
        self.assertEqual(self.profile["septims"], 1450)
        self.assertEqual(self.profile["xp"], 370)
        # Existing engine replaces a claimed share for a later wave.
        store["shares"]["101"] = {"septims": 900, "xp": 250, "claimed": False}
        P.capture_hunt_rewards(store)
        P.claim_hunt_rewards(self.profile, store)
        self.assertEqual(self.profile["septims"], 2350)
        self.assertEqual(len(self.profile["hunt_receipts"]), 3)

    def test_hunt_crash_orderings_do_not_lose_or_duplicate_rewards(self):
        store = self._hunt()
        P.capture_hunt_rewards(store)
        persisted_store = json.loads(json.dumps(store))
        original_profile = copy.deepcopy(self.profile)
        P.claim_hunt_rewards(self.profile, store)
        # Profile saved; shared claim flag save failed. Receipt ID prevents replay.
        saved_profile = json.loads(json.dumps(self.profile))
        self.assertIsNone(P.claim_hunt_rewards(saved_profile, persisted_store))
        self.assertEqual(saved_profile["septims"], 650)
        # Shared advisory flag saved; profile save failed. Mailbox is still payable.
        self.assertIsNotNone(P.claim_hunt_rewards(original_profile, store))
        self.assertEqual(original_profile["septims"], 650)

    def test_legacy_claimed_hunt_share_does_not_pay_again(self):
        store = self._hunt()
        store["shares"]["101"]["claimed"] = True
        self.assertFalse(P.capture_hunt_rewards(store))
        self.assertIsNone(P.claim_hunt_rewards(self.profile, store))

    def test_support_is_one_ally_use_bounded_and_attributed(self):
        store = {}
        P.finish_hunt_support(store, self.profile, "expose", now=100)
        own = P.consume_hunt_support(store, self.profile, "expose", now=101)
        self.assertEqual(own["attack_bonus"], -10)
        self.assertIn("expose", store["ally_support"])
        ally = {"user_id": 102, "name": "Ally"}
        hit = P.consume_hunt_support(store, ally, "attack", now=102)
        self.assertEqual(hit["attack_bonus"], 12)
        self.assertIn("Tester", hit["lines"][0])
        self.assertEqual(hit["helpers"], ["101"])
        again = P.consume_hunt_support(store, ally, "attack", now=103)
        self.assertEqual(again["attack_bonus"], 0)
        P.finish_hunt_support(store, self.profile, "protect", now=100)
        expired = P.consume_hunt_support(store, ally, "attack", now=100 + P.SUPPORT_SECONDS)
        self.assertEqual(expired["guard_bonus"], 0)

    def test_expose_and_protect_can_combine_but_not_stack(self):
        store = {}
        P.finish_hunt_support(store, self.profile, "expose", now=100)
        P.finish_hunt_support(store, self.profile, "expose", now=101)
        P.finish_hunt_support(store, self.profile, "protect", now=101)
        self.assertEqual(len(store["ally_support"]), 2)
        effects = P.consume_hunt_support(store, {"user_id": 200}, "protect", now=102)
        self.assertEqual(effects["attack_bonus"], 2)  # +12 ally help minus 10 support role
        self.assertEqual(effects["guard_bonus"], 12)
        self.assertEqual(store["ally_support"], {})
        with self.assertRaises(ValueError):
            P.consume_hunt_support(store, self.profile, "invalid")

    def test_promotions_require_trial_and_favour_and_reward_once(self):
        p = self.profile
        p["allegiance"] = "companions"
        P.ensure_promotions(p)
        p["favours"] = {"companions": 2}
        self.assertEqual(P.faction_rank(p), "Initiate")
        self.assertIsNotNone(P.claim_promotion(p))
        for _ in range(10):
            P.promotion_event(p, "kill", style="blade")
        self.assertTrue(P.promotion(p)["claimable"])
        self.assertIsNone(P.claim_promotion(p))
        self.assertEqual(P.faction_rank(p), "Member")
        self.assertEqual(p["elixirs"]["vigor"], 2)
        self.assertIsNotNone(P.claim_promotion(p))
        self.assertEqual(p["elixirs"]["vigor"], 2)
        # Second promotion requires the specific Hard clear, not any clear.
        P.promotion_event(p, "clear", diff="Easy")
        self.assertEqual(P.promotion(p)["progress"], 0)
        P.promotion_event(p, "clear", diff="Hard")
        self.assertTrue(P.promotion(p)["complete"])
        self.assertFalse(P.promotion(p)["claimable"])

    def test_existing_faction_ranks_are_grandfathered(self):
        self.profile["allegiance"] = "thieves"
        self.profile["favours"] = {"thieves": 6, "college": 8}
        self.assertEqual(P.faction_rank(self.profile), "Champion")
        self.assertEqual(P.promotion(self.profile)["tier"], 4)
        self.assertEqual(P.faction_rank(self.profile, "college"), "Harbinger")
        self.profile["allegiance"] = "college"
        self.assertIsNone(P.promotion(self.profile))

    def test_final_promotion_cosmetic_and_reward_once(self):
        p = self.profile
        p["allegiance"] = "thieves"
        p["favours"] = {"thieves": 6}
        P.ensure_promotions(p)
        p["favours"]["thieves"] = 8
        P.promotion_event(p, "clear", deep=True)
        self.assertIsNone(P.claim_promotion(p))
        self.assertIn("Shadow of Riften", p["titles"])
        self.assertEqual(p["elixirs"]["true_shot"], 2)
        self.assertIsNotNone(P.claim_promotion(p))
        self.assertEqual(p["elixirs"]["true_shot"], 2)

    def test_inheritance_preserves_exactly_one_learned_doctrine(self):
        p = self.profile
        p["doctrines"] = {"blade": ["warmaster", "bulwark"], "sneak": ["ghost"]}
        self.assertEqual(len(P.inheritance_options(p)), 3)
        self.assertIsNotNone(P.inherit(p, "marksman", "deadeye"))
        self.assertIsNone(P.inherit(p, "sneak", "ghost"))
        selected = P.prepare_inheritance(p)
        p["doctrines"] = {}
        p["skills"]["sneak"] = 15
        P.apply_inheritance(p, selected)
        self.assertEqual(p["doctrines"], {"sneak": ["ghost"]})
        self.assertEqual(p["skills"]["sneak"], 15)
        P.apply_inheritance(p, None)
        self.assertEqual(p["doctrines"], {})
        self.assertNotIn("inheritance", p)

    def test_forged_or_stale_inheritance_selection_is_ignored(self):
        self.profile["inheritance"] = {"skill": "blade", "choice": "warmaster"}
        self.assertIsNone(P.prepare_inheritance(self.profile))

    def test_next_goal_is_one_short_routable_objective(self):
        with patch.object(E, "tutorial_available", return_value=True, create=True):
            goal = P.next_goal(self.profile)
            self.assertEqual(goal["action"], "tutorial")
        with patch.object(E, "tutorial_available", return_value=False, create=True):
            self.profile["xp"] = D.xp_needed(1)
            goal = P.next_goal(self.profile)
            self.assertEqual(goal["action"], "perks")
        self.assertEqual(set(goal), {"text", "action"})
        self.assertLess(len(goal["text"]), 100)


if __name__ == "__main__":
    unittest.main()
