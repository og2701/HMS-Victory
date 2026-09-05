"""Actual Discord component checks. All player state and interactions are fake."""

import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import config
import discord
from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E
from lib.features.skyrim import presentation as T
from lib.features.skyrim import views as V


def items(view):
    def descend(item):
        yield item
        for child in getattr(item, "children", []):
            yield from descend(child)
    for child in view.children:
        yield from descend(child)


def content(view):
    return "\n".join(item.content for item in items(view) if isinstance(item, discord.ui.TextDisplay))


def button(view, label):
    return next(item for item in items(view) if isinstance(item, discord.ui.Button) and item.label == label)


class ReadabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skyrim_ui_")
        self.addCleanup(self.temp.cleanup)
        for name in dir(config):
            if (name.startswith("SKYRIM_") and name.endswith("_FILE")) or name == "PERSISTENT_VIEWS_FILE":
                patcher = patch.object(config, name, str(Path(self.temp.name) / f"{name}.json"))
                patcher.start()
                self.addCleanup(patcher.stop)
        for name, value in (("_gallery_files", Mock(return_value=[])),
                            ("_flush_game_log", AsyncMock()), ("_flush_wonders", AsyncMock()),
                            ("_award_badges", AsyncMock())):
            patcher = patch.object(V, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.profile = E.create_profile(7001, "Dovahkiin", "warrior")

    def interaction(self):
        return SimpleNamespace(
            user=SimpleNamespace(id=7001, display_name="Dovahkiin"), guild_id=99, channel_id=42,
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock(),
            channel=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=818, delete=AsyncMock(), edit=AsyncMock()))),
            client=SimpleNamespace(add_view=Mock()), message=SimpleNamespace(id=818))

    def delve(self, room=None, **kw):
        room = room or {"kind": "enemy", "key": "bandit", "boss": False, "resolved": False}
        return E.Delve(7001, "Dovahkiin", 42, "embershard", [room,
                       {"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}],
                       hearts=E.heart_max(self.profile), shout_charges=0, **kw)

    def assert_readable(self, view, max_text=1000):
        self.assertLessEqual(len(content(view)), max_text)
        self.assertLessEqual(len(list(items(view))), 40)
        for row in view.children:
            if isinstance(row, discord.ui.ActionRow):
                controls = [item for item in row.children if isinstance(item, discord.ui.Button)]
                self.assertLessEqual(len(controls), 3)
                if len(controls) > 1:
                    self.assertLessEqual(sum(len(item.label or "") for item in controls), 30)
        self.assertTrue(view.to_components())

    async def test_live_board_is_compact_persistent_and_heals_vigor(self):
        d = self.delve(buffs={"heart": 1})
        d.hearts = E.heart_max(self.profile)
        self.profile["potions"] = 2
        view, _ = V.build_delve_layout(d, self.profile)
        self.assert_readable(view, 600)
        self.assertTrue(view.is_persistent())
        self.assertIsNotNone(button(view, "Heal +1"))
        self.assertIsNotNone(button(view, "Home"))
        self.assertIsNotNone(button(view, "Inspect"))

    async def test_pact_event_does_not_offer_an_illegal_exit(self):
        d = self.delve({"kind": "event", "key": "shrine", "boss": False, "resolved": False}, pacts=["clavicus"])
        view, _ = V.build_delve_layout(d, self.profile)
        labels = [item.label for item in items(view) if isinstance(item, discord.ui.Button)]
        self.assertFalse(any(label.startswith(("Bank", "Flee")) for label in labels))
        self.assert_readable(view, 600)

    async def test_lethal_risk_is_visible_without_usable_potions(self):
        d = self.delve({"kind":"enemy","key":"bandit_chief","boss":True,"resolved":False})
        d.hearts = 2
        self.profile["potions"] = 0
        view,_ = V.build_delve_layout(d,self.profile)
        self.assertIn("A wound can cost 2 HP; you have 2",content(view))
        self.assert_readable(view,600)
        self.profile["potions"] = 3
        d.pacts = ["namira"]
        self.assertIn("A wound can cost 2 HP",V._delve_text(d,self.profile))

    async def test_story_inspect_matches_the_live_choice(self):
        d = self.delve({"kind":"event","key":"fork","story":"captive","boss":False,"resolved":False})
        self.assertIn("captive scout",V._delve_text(d,self.profile))
        self.assertIn("captive scout",V._delve_details(d,self.profile))

    async def test_flee_button_previews_real_bank_amount(self):
        d = self.delve()
        d.engaged = True
        d.satchel = 101
        view, _ = V.build_delve_layout(d, self.profile)
        self.assertIsNotNone(button(view, f"Flee {int(101 * E.FLEE_KEEP)}"))

    async def test_inspect_and_back_preserve_actions_and_all_detail_pages(self):
        original = "## Shop\n\n" + "\n".join(f"Item {i}: useful description and cost {i}." for i in range(100))
        callback = AsyncMock()
        row = discord.ui.ActionRow()
        row.add_item(V._cb_btn(discord.ButtonStyle.primary, "Buy potion", "🧪", callback))
        view, _ = V._panel_view(original, [row])
        self.assert_readable(view, 750)
        inter = self.interaction()
        await button(view, "Inspect").callback(inter)
        detailed = inter.response.edit_message.call_args.kwargs["view"]
        seen = []
        while True:
            self.assertLessEqual(len(content(detailed)), T.DETAIL_LIMIT + 50)
            seen.append(content(detailed))
            following = next((item for item in items(detailed) if isinstance(item, discord.ui.Button) and item.label == "Next"), None)
            if not following:
                break
            inter = self.interaction()
            await following.callback(inter)
            detailed = inter.response.edit_message.call_args.kwargs["view"]
        self.assertIn("Item 99:", "\n".join(seen))
        inter = self.interaction()
        await button(detailed, "Back").callback(inter)
        restored = inter.response.edit_message.call_args.kwargs["view"]
        self.assertIs(button(restored, "Buy potion").callback, callback)
        self.assert_readable(restored, 750)

    async def test_all_private_panels_fit_components_for_new_and_advanced_players(self):
        panels = [V._hub_character, V._hub_holdings, V._hub_shop, V._hub_perks,
                  V._hub_masteries, V._hub_collection, V._hub_records, V._hub_alchemy,
                  V._hub_grindstone, V._hub_rumours, V._hub_pacts, V._hub_factions,
                  V._hub_notice, V._hub_pit, V._hub_help, V._hub_rankings, V._hub_hall]
        for experienced in (False, True):
            p = copy.deepcopy(self.profile)
            if experienced:
                p.update(xp=100000, septims=1000000, words=3, souls=20, alduin_slain=5)
                p["stats"].update(delves=30, dragons=30)
                p["skills"] = {key: 100 for key in p["skills"]}
                E.homestead(p)["built"] = {key: E._today_str() for key in D.HOMESTEAD}
                p["allegiance"] = "companions"
            E.save_profile(p)
            for panel in panels:
                with self.subTest(panel=panel.__name__, experienced=experienced):
                    inter = self.interaction()
                    await panel(inter)
                    view = inter.response.edit_message.call_args.kwargs["view"]
                    self.assert_readable(view)

    async def test_terminal_summary_shows_losses_skills_and_next_goal(self):
        d = self.delve()
        d.state = "dead"
        d.summary = {"start": {"level": 1}, "banked_gold": 0, "lost_gold": 120,
                     "banked_ingredients": {}, "lost_ingredients": {"dragon_scale": 2},
                     "skill_gains": {"blade": 1}, "task_gains": {"x": 1}}
        text = V._debrief_text(d, self.profile)
        self.assertIn("120 lost", text)
        self.assertIn("2 lost", text)
        self.assertIn("One-Handed +1", text)
        self.assertIn("Progress on 1 task", text)
        self.assertIn("Next:", text)

    async def test_hunt_role_picker_has_three_distinct_actions(self):
        inter = self.interaction()
        await V._choose_march_role(inter, self.profile)
        view = inter.response.edit_message.call_args.kwargs["view"]
        for label in ("Attack", "Expose", "Protect"):
            self.assertIsNotNone(button(view, label))
        self.assert_readable(view, 700)

    async def test_claim_spoils_is_available_alongside_marching(self):
        inter = self.interaction()
        with patch.object(E, "wb_available", return_value=True), patch.object(E, "wb_share_waiting", return_value={"septims":400}):
            await V._hub_notice(inter)
        view = inter.response.edit_message.call_args.kwargs["view"]
        self.assertIsNotNone(button(view,"March on it"))
        self.assertIsNotNone(button(view,"Claim spoils"))
        self.assert_readable(view)

    async def test_exhausted_public_adventure_picker_has_a_way_home(self):
        inter = self.interaction()
        with patch.object(E,"delves_left",return_value=0), patch.object(E,"daily_available",return_value=False):
            await V._show_offers(inter, edit_hub=False)
        view = inter.response.send_message.call_args.kwargs["view"]
        self.assertIsNotNone(button(view,"Back"))

    async def test_slain_hunt_report_does_not_mislabel_next_boss_health(self):
        inter = self.interaction()
        store = E.world_boss()
        upcoming = {**store,"hp":987,"max":987}
        with patch.object(E,"wb_march",return_value=(["The foe falls."],1,True,upcoming)):
            await V._post_march_board(inter,self.profile)
        view = inter.channel.send.call_args.kwargs["view"]
        self.assertIn("Defeated",content(view))
        self.assertNotIn("987/987 remain",content(view))

    async def test_public_next_goal_opens_the_target_in_a_private_reply(self):
        d = self.delve()
        d.state = "cleared"
        inter = self.interaction()
        with patch.object(V.P, "next_goal", return_value={"text": "Upgrade your weapon.", "action": "shop"}):
            await V._handle_delve_click(inter, d, "goal")
        inter.response.edit_message.assert_not_called()
        sent = inter.response.send_message.call_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertIn("Belethor", content(sent["view"]))

    async def test_hub_and_first_adventure_offer_are_short_and_actionable(self):
        with patch.object(E, "tutorial_available", return_value=True, create=True):
            view, _ = V._panel_view(V._hub_text(self.profile), V._hub_rows(self.profile))
            self.assert_readable(view, 550)
            self.assertIsNotNone(button(view, "First adventure"))
            inter = self.interaction()
            await V._open_location_picker(inter, edit_hub=True)
            tutorial = inter.response.edit_message.call_args.kwargs["view"]
            self.assertIsNotNone(button(tutorial, "Begin"))
            self.assertIn("No stamina spent", content(tutorial))
            self.assert_readable(tutorial, 500)

    async def test_pit_and_duel_boards_keep_navigation_and_numeric_health(self):
        self.profile["xp"] = 10000
        E.pit_begin(self.profile)
        view, _ = V._pit_board_layout(self.profile, ["A long tale " * 90])
        self.assert_readable(view, 650)
        self.assertIsNotNone(button(view, "Home"))
        self.assertIsNotNone(button(view, "Inspect"))
        self.assertNotIn("❤️❤️", content(view))
        rival = E.create_profile(7002, "Rival", "mage")
        rival["xp"] = 10000
        E.duel_begin(self.profile, rival)
        duel, _ = V._duel_board_layout(self.profile, ["The round ends."])
        self.assert_readable(duel, 650)
        self.assertIsNotNone(button(duel, "Home"))
        self.assertIsNotNone(button(duel, "Inspect"))
        self.assertNotIn("❤️❤️", content(duel))

    async def test_retirement_requires_explicit_ability_choice_when_available(self):
        p = self.profile
        choice = next(iter(D.DOCTRINES["blade"]))
        p["doctrines"] = {"blade": choice}
        E.save_profile(p)
        inter = self.interaction()
        await V._hall_confirm(inter, next(iter(D.BOONS)))
        view = inter.response.edit_message.call_args.kwargs["view"]
        self.assertTrue(button(view, "Confirm retirement").disabled)
        self.assertIn("cannot be undone", content(view))
        self.assertIsNone(V.P.inherit(p, "blade", choice))
        E.save_profile(p)
        inter = self.interaction()
        await V._hall_confirm(inter, next(iter(D.BOONS)))
        chosen = inter.response.edit_message.call_args.kwargs["view"]
        self.assertFalse(button(chosen, "Confirm retirement").disabled)
        self.assert_readable(chosen, 1400)

    async def test_faction_promotion_is_visible_and_claimable(self):
        p = self.profile
        p["xp"] = 10000
        p["allegiance"] = "companions"
        p["favours"] = {"companions": 2}
        p["promotions"] = {"companions": {"grandfathered": 0, "claimed": [], "progress": {"1": 10}}}
        E.save_profile(p)
        inter = self.interaction()
        await V._hub_factions(inter)
        view = inter.response.edit_message.call_args.kwargs["view"]
        self.assertIn("Promotion", content(view))
        self.assertIsNotNone(button(view, "Claim promotion"))
        self.assert_readable(view)

    async def test_failed_post_leaves_pending_session_uncommitted(self):
        inter = self.interaction()
        inter.channel.send.side_effect = discord.HTTPException(SimpleNamespace(status=500, reason="failed"), "failed")
        pending = SimpleNamespace(profile=self.profile, delve=self.delve(), commit=Mock())
        with patch.object(V.sessions, "prepare", return_value=pending):
            await V._launch_delve(inter, "embershard")
        pending.commit.assert_not_called()
        self.assertIn("supplies are safe", content(inter.edit_original_response.call_args.kwargs["view"]))

    async def test_conflicting_commit_removes_uncommitted_public_board(self):
        inter = self.interaction()
        pending = SimpleNamespace(profile=self.profile, delve=self.delve(), commit=Mock(side_effect=ValueError("changed")))
        with patch.object(V.sessions, "prepare", return_value=pending):
            await V._launch_delve(inter, "embershard")
        inter.channel.send.return_value.delete.assert_awaited_once()
        self.assertIn("Nothing was spent", content(inter.edit_original_response.call_args.kwargs["view"]))


if __name__ == "__main__":
    unittest.main()
