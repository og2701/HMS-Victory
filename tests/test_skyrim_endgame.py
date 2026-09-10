"""Endgame rules, recoverable actions and real Discord controls on disposable saves."""
import copy
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import config
import discord
from lib.core import file_operations as F
from lib.features.skyrim import data as D, engine as E, endgame as H, sessions as S, views as V


def level_to(p, n):
    p['xp'] = sum(D.xp_needed(l) for l in range(1, n))


def children(view):
    return list(view.walk_children())


def content(view):
    return '\n'.join(x.content for x in children(view) if isinstance(x, discord.ui.TextDisplay))


class EndgameTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='skyrim_endgame_')
        self.addCleanup(temp.cleanup)
        for key in dir(config):
            if key.startswith('SKYRIM_') and key.endswith('_FILE') or key == 'PERSISTENT_VIEWS_FILE':
                self.enterContext(patch.object(config, key, str(Path(temp.name) / (key + '.json'))))
        self.enterContext(patch.object(F, 'PERSISTENT_VIEWS_FILE', config.PERSISTENT_VIEWS_FILE))
        self.enterContext(patch.object(V, '_gallery_files', return_value=[]))
        for key in ('_flush_game_log', '_flush_wonders', '_award_badges'):
            self.enterContext(patch.object(V, key, AsyncMock()))
        E.create_profile(771, 'Hall tester', 'warrior')
        self.p = E.get_profile(771)
        self.p['alduin_slain'] = 38
        self.p['stats']['dragons'] = 160
        self.p['words'] = 3
        level_to(self.p, 32)
        self.p['legacy'] = {'rank': 5, 'boons': list(D.BOONS)[:5],
                            'epitaphs': [{'alduin': 5, 'dragons': 30} for _ in range(5)]}
        H.life_baseline(self.p)
        E.save_profile(self.p)

    def board(self, key='sealed_vault', idx=4):
        d = E.Delve(771, 'Hall tester', 42, key, H.rooms(key), idx=idx,
                    hearts=5, shout_charges=3, message_id=818)
        H.prepare(self.p, d)
        return d

    def active(self, d):
        self.p['active_delve'] = 818
        E.save_profile(self.p)
        E.save_delve(d)
        self.p = E.get_profile(771)
        return d

    def interaction(self):
        return SimpleNamespace(user=SimpleNamespace(id=771), guild_id=1, channel_id=42,
            response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock()),
            client=SimpleNamespace(add_view=Mock()), message=SimpleNamespace(id=818, edit=AsyncMock()))

    def test_existing_cap_save_recovers_proven_current_life(self):
        self.assertEqual(H.life_counts(self.p), (33, 130))
        self.assertTrue(E.retire_ready(self.p)[0])
        saved = copy.deepcopy(self.p)
        E._migrate(self.p)
        self.assertEqual(self.p['legacy'], saved['legacy'])
        self.p['legacy'].pop('life')
        self.p['legacy']['epitaphs'] = []
        self.assertEqual(H.life_counts(self.p), (0, 0))
        self.assertFalse(E.retire_ready(self.p)[0])
        for ep in ({'alduin': 90, 'dragons': 30}, {'alduin': '5', 'dragons': -1}):
            self.p['legacy'].pop('life')
            self.p['legacy']['epitaphs'] = [ep] * 5
            self.assertEqual(H.life_counts(self.p), (0, 0))

    def test_retire_many_lives_preserves_accounts_and_requires_new_win(self):
        self.p['hall']['boons'] = list(D.HALL_BOONS)
        self.p['hall']['stories'] = {'college': {'stage': 1, 'choice': 'careful'}}
        self.p['titles'] = ['Keeper of Names']
        self.p['log']['enemies'] = ['bandit']
        for rank in (5, 6, 7, 8, 9):
            self.assertTrue(E.retire_ready(self.p)[0])
            self.assertEqual(E.boon_offer(self.p), [])
            self.assertIsNone(E.retire(self.p, expected_rank=rank))
            self.assertEqual(E.level(self.p), 1)
            self.assertEqual(E.legacy_rank(self.p), rank + 1)
            self.assertEqual(self.p['hall']['boons'], list(D.HALL_BOONS))
            self.assertEqual(self.p['hall']['stories']['college']['stage'], 1)
            self.assertEqual(self.p['titles'], ['Keeper of Names'])
            self.assertEqual(self.p['log']['enemies'], ['bandit'])
            self.assertEqual(self.p['legacy']['boons'], list(D.BOONS)[:5])
            self.assertIsNone(self.p['legacy']['epitaphs'][-1]['boon'])
            self.assertIsNotNone(E.retire(self.p, expected_rank=rank))
            level_to(self.p, 32)
            self.assertFalse(E.retire_ready(self.p)[0])
            self.p['alduin_slain'] += 1
        self.assertIn('reborn', self.p['hall']['deeds'])

    def test_first_five_retirement_rules_stay_and_scaling_is_bounded(self):
        self.p['legacy'] = {'rank': 4, 'boons': list(D.BOONS)[:4], 'epitaphs': []}
        self.p['alduin_slain'] = 5
        self.assertEqual(E.retire_level_needed(self.p), 32)
        self.assertIsNone(E.retire(self.p, E.boon_offer(self.p)[0]))
        self.assertEqual(len(self.p['legacy']['boons']), 5)
        for rank in (5, 6, 99, 10000):
            self.p['legacy']['rank'] = rank
            self.assertEqual(E.retire_level_needed(self.p), 32)
            self.assertGreater(E.soulcairn_drain(200, self.p), 0)
            self.assertAlmostEqual(E.soulcairn_drain(200, self.p), E.soulcairn_drain(200) * .4)

    def test_alduin_gate_uses_capped_current_life_counters(self):
        self.p['legacy']['life'] = {'rank': 5, 'alduin': 38, 'dragons': 160}
        self.assertFalse(E.alduin_ready(self.p)[0])
        self.p['stats']['dragons'] += 5
        self.assertTrue(E.alduin_ready(self.p)[0])
        self.p['alduin_slain'] += 100
        self.assertFalse(E.alduin_ready(self.p)[0])
        self.p['stats']['dragons'] += 12
        self.assertTrue(E.alduin_ready(self.p)[0])
        self.assertIn('17 dragons slain this life', E.alduin_ready(self.p)[1])

    def test_chapters_award_once_after_five_without_economy_rewards(self):
        from lib.features.skyrim import badges
        existing = badges._earned(self.p)
        self.assertIn('sky_hall_of_legends', existing)
        self.p['legacy']['rank'] = 4
        self.p['hall']['deeds'] = list(D.HALL_DEEDS)
        before = (self.p['septims'], self.p['xp'], copy.deepcopy(self.p.get('badges')))
        self.assertEqual(H.settle(self.p), [])
        self.p['legacy']['rank'] = 5
        self.assertEqual(H.settle(self.p), list(D.HALL_BOONS))
        self.assertEqual(H.settle(self.p), [])
        self.assertEqual((self.p['septims'], self.p['xp'], self.p.get('badges')), before)
        self.assertEqual(badges._earned(self.p), existing)

    def test_boon_rerolls_once_and_survives_reload(self):
        self.p['hall']['boons'] = list(D.HALL_BOONS)
        for skill, key in [('lockpicking', 'steady_hands'), ('speech', 'silver_tongue'), ('sneak', 'quiet_step')]:
            d = self.board()
            rng = Mock(random=Mock(side_effect=[.1, .99, .1, .99]))
            self.assertTrue(H.check(self.p, d, skill, 50, rng))
            self.assertEqual(d.endgame['used'], [])
            self.assertTrue(H.check(self.p, d, skill, 50, rng))
            d = E.Delve.from_dict(d.to_dict())
            self.assertFalse(H.check(self.p, d, skill, 50, rng))
            self.assertEqual(d.endgame['used'], [key])
            self.assertEqual(rng.random.call_count, 4)
        d = self.board()
        self.p['hall']['boons'] = []
        self.assertEqual(d.endgame['boons'], list(D.HALL_BOONS))

    def test_adventure_slot_and_collection_catalogues_are_preserved(self):
        offered = E.offer_locations(self.p)
        with patch.object(H, 'featured', return_value=None):
            original = E.offer_locations(self.p)
        self.assertEqual(len(offered), len(original))
        self.assertEqual(sum(k in D.HALL_ADVENTURES for k in offered), 1)
        self.assertFalse(set(D.HALL_ADVENTURES) & set(D.LOCATIONS))
        before = E.collection_pct(self.p)
        for key in D.HALL_ADVENTURES:
            d = self.board(key)
            d._finish_clear(self.p)
            self.assertNotIn(key, self.p['log'].get('clears', []))
        self.assertEqual(E.collection_pct(self.p), before)
        self.p['alduin_slain'] = 0
        self.assertFalse(any(k in D.HALL_ADVENTURES for k in E.offer_locations(self.p)))
        with self.assertRaises(ValueError):
            S.prepare(self.p, 42, 'rune_warden')

    def test_warden_requires_changed_landed_styles(self):
        d = self.board('rune_warden', 5)
        self.assertFalse(H.ward_hit(d, 'blade'))
        self.assertTrue(H.ward_hit(d, 'blade'))
        d = E.Delve.from_dict(d.to_dict())
        self.assertTrue(H.ward_hit(d, 'blade'))
        self.assertFalse(H.ward_hit(d, 'marksman'))
        self.assertFalse(H.ward_hit(d, 'destruction'))
        d.state = 'cleared'
        H.finish(self.p, d)
        self.assertNotIn('warden_clear', self.p['hall']['deeds'])
        d.endgame.pop('settled')
        d.endgame['warden_killed'] = True
        H.finish(self.p, d)
        self.assertIn('warden_styles', self.p['hall']['deeds'])

    def test_vault_failure_recovers_quietly_with_explicit_cost(self):
        d = self.board()
        with patch.object(E.random, 'random', return_value=.999):
            d.act_event(self.p, 'hall:lock')
        self.assertEqual(d.room['hall_event'], 'jammed')
        old = d.to_dict()
        d.act_event(self.p, 'hall:lock')
        self.assertEqual(d.to_dict(), old)
        d.act_event(self.p, 'hall:wait')
        self.assertEqual(d.room['hall_event'], 'ledger')
        initial = self.p['septims']
        d.act_event(self.p, 'hall:ledger')
        self.assertEqual(d.state, 'cleared')
        self.assertEqual(self.p['septims'] - initial, E._septims(self.p, 60))
        self.assertIn('vault_quiet', self.p['hall']['deeds'])
        self.assertNotIn('vault_parley', self.p['hall']['deeds'])

    def test_failed_negotiation_wait_is_not_a_successful_parley(self):
        d = self.board()
        with patch.object(E.random, 'random', return_value=.999):
            d.act_event(self.p, 'hall:talk')
        d.act_event(self.p, 'hall:wait')
        d.act_event(self.p, 'hall:ledger')
        self.assertNotIn('vault_parley', self.p['hall']['deeds'])

    def test_vault_negotiation_and_force_have_distinct_deeds(self):
        d = self.board()
        with patch.object(E.random, 'random', return_value=0):
            d.act_event(self.p, 'hall:talk')
        d.act_event(self.p, 'hall:ledger')
        self.assertIn('vault_parley', self.p['hall']['deeds'])
        self.p['hall']['deeds'] = []
        d = self.board()
        d.act_event(self.p, 'hall:force')
        self.assertTrue(d.room['boss'])
        d._kill(self.p, d.enemy(), 'blade')
        self.assertEqual(d.state, 'cleared')
        self.assertNotIn('vault_quiet', self.p['hall']['deeds'])

    def test_caravan_guard_and_rescue_are_both_needed(self):
        d = self.board('lost_caravan', 4)
        d.act_event(self.p, 'story_help')
        self.assertEqual(d.idx, 5)
        d.act_guard(self.p)
        self.assertTrue(d.endgame.get('guarded_charge'))
        d._kill(self.p, d.enemy(), 'blade')
        self.assertIn('caravan_rescue', self.p['hall']['deeds'])
        self.assertIn('caravan_guard', self.p['hall']['deeds'])

    def test_all_faction_stories_resolve_both_branches_and_keep_progress(self):
        for faction, spec in D.FACTION_STORIES.items():
            for choice in ('careful', 'bold'):
                with self.subTest(faction=faction, choice=choice):
                    self.p['allegiance'] = faction
                    self.p['promotions'][faction] = {'grandfathered': 4}
                    self.p['hall']['stories'] = {}
                    first = self.board(spec['roads'][0], 1)
                    first.act_event(self.p, 'hall:' + choice)
                    first.state = 'dead'
                    H.finish(self.p, first)
                    self.assertEqual(self.p['hall']['stories'], {})
                    first.state = 'cleared'
                    H.finish(self.p, first)
                    self.assertEqual(H.story_state(self.p)['stage'], 1)
                    second = self.board(spec['roads'][1], 4)
                    self.assertEqual(second.rooms[-1]['faction_effect'].get('guard', False), choice == 'careful')
                    second._advance(self.p)
                    self.assertTrue(second.room['faction_effect']['arrived'])
                    second.state = 'cleared'
                    H.finish(self.p, second)
                    self.assertIsNone(H.story_state(self.p))
                    self.assertIn(spec['title'], self.p['titles'])
                    saved = copy.deepcopy(self.p)
                    H.finish(self.p, second)
                    self.assertEqual(self.p, saved)

    def test_changed_faction_does_not_complete_abandoned_story(self):
        self.p['allegiance'] = 'college'
        self.p['promotions']['college'] = {'grandfathered': 4}
        d = self.board('rune_warden', 1)
        d.act_event(self.p, 'hall:careful')
        self.p['allegiance'] = 'thieves'
        d.state = 'cleared'
        H.finish(self.p, d)
        self.assertEqual(self.p['hall']['stories'], {})

    def test_action_commit_failure_leaves_originals_untouched(self):
        d = self.active(self.board())
        before, board = copy.deepcopy(self.p), d.to_dict()
        pending = S.prepare_action(self.p, d, 'evt:hall:force')
        with patch.object(E, 'save_profile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                pending.commit()
        self.assertEqual(self.p, before)
        self.assertEqual(d.to_dict(), board)
        self.assertEqual(E.get_profile(771), before)
        self.assertEqual(E.load_delve(818).to_dict(), board)

    def test_committed_boon_and_room_recover_without_reroll(self):
        self.p['hall']['boons'] = ['steady_hands']
        d = self.active(self.board())
        with patch.object(E.random, 'random', side_effect=[.999, 0]):
            pending = S.prepare_action(self.p, d, 'evt:hall:lock')
        with patch.object(E, 'save_persistent_views', side_effect=OSError('interrupted')):
            pending.commit()
        self.assertIn('_action_commit', E._profiles()['771'])
        with patch.object(E.random, 'random', side_effect=AssertionError('must not reroll')):
            recovered = E.get_profile(771)
        current = E.load_delve(818)
        self.assertNotIn('_action_commit', recovered)
        self.assertEqual(current.endgame['used'], ['steady_hands'])
        self.assertEqual(current.room['hall_event'], 'ledger')
        with self.assertRaises(ValueError):
            S.prepare_action(recovered, d, 'evt:hall:lock', 0)
        pending.commit()
        self.assertEqual(E.load_delve(818).to_dict(), current.to_dict())

    def test_terminal_reward_recovery_and_stale_click_are_exactly_once(self):
        d = self.board()
        with patch.object(E.random, 'random', return_value=0):
            d.act_event(self.p, 'hall:talk')
        self.active(d)
        pending = S.prepare_action(self.p, d, 'evt:hall:ledger')
        with patch.object(E, 'save_persistent_views', side_effect=OSError('interrupted')):
            pending.commit()
        result = E.get_profile(771)
        self.assertIn('vault_parley', result['hall']['deeds'])
        self.assertIsNone(E.load_delve(818))
        self.assertEqual(E.get_profile(771), result)
        with self.assertRaises(ValueError):
            S.prepare_action(result, d, 'evt:hall:ledger')

    def test_failed_journal_cleanup_and_secondary_writes_replay_safely(self):
        d = self.active(self.board())
        d.daily = True
        d.satchel = 100
        E.save_delve(d)
        self.p['daily'] = {'date': E._today_str()}
        E.save_profile(self.p)
        self.p = E.get_profile(771)
        pending = S.prepare_action(self.p, d, 'lve')
        save = E.save_profile
        calls = 0
        def interrupt(profile):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('cleanup interrupted')
            save(profile)
        with patch.object(E, 'save_profile', side_effect=interrupt):
            pending.commit()
        self.assertIn('_action_commit', E._profiles()['771'])
        result = E.get_profile(771)
        self.assertEqual(result['septims'], self.p['septims'] + 100)
        self.assertEqual(len(E.daily_results()), 1)
        self.assertEqual(E.get_profile(771), result)

    def test_death_and_daily_write_failures_remain_recoverable(self):
        self.p['potions'] = 0
        self.p['daily'] = {'date': E._today_str()}
        d = self.board('lost_caravan', 5)
        d.hearts, d.satchel, d.daily = 1, 50, True
        self.active(d)
        before = copy.deepcopy(self.p)
        with patch.object(E.random, 'random', return_value=.999):
            pending = S.prepare_action(self.p, d, 'atk:blade')
        self.assertEqual(pending.delve.state, 'dead')
        self.assertEqual(E._graveyard(), [])
        with patch.object(E, 'save_profile', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                pending.commit()
        self.assertEqual(E._graveyard(), [])
        self.assertEqual(E.get_profile(771), before)
        save = E.save_json_file
        def fail_grave(path, value):
            if path == config.SKYRIM_GRAVEYARD_FILE:
                raise OSError('grave unavailable')
            return save(path, value)
        with patch.object(E, 'save_json_file', side_effect=fail_grave):
            pending.commit()
        self.assertIn('_action_commit', E._profiles()['771'])
        def fail_daily(path, value):
            if path == config.SKYRIM_DAILY_FILE:
                raise OSError('daily unavailable')
            return save(path, value)
        with patch.object(E, 'save_json_file', side_effect=fail_daily):
            with self.assertRaises(OSError):
                E.get_profile(771)
        self.assertEqual(len(E._graveyard()), 1)
        result = E.get_profile(771)
        self.assertEqual(len(E._graveyard()), 1)
        self.assertEqual(E.daily_results()['771']['state'], 'dead')
        self.assertEqual(result['stats']['deaths'], before['stats']['deaths'] + 1)
        self.assertNotIn('_action_commit', result)

    def test_boosted_checks_use_real_chest_sneak_and_talk_actions(self):
        self.p['hall']['boons'] = list(D.HALL_BOONS)
        d = self.board('lost_caravan', 0)
        with patch.object(E.random, 'random', side_effect=[.999, 0]):
            d.act_sneak(self.p)
        self.assertTrue(d.ambush)
        self.assertEqual(d.endgame['used'], ['quiet_step'])
        d = self.board('lost_caravan', 0)
        with patch.object(E.random, 'random', side_effect=[.999, 0, .999]):
            d.act_persuade(self.p)
        self.assertEqual(d.idx, 1)
        self.assertEqual(d.endgame['used'], ['silver_tongue'])
        with patch.object(E.random, 'random', side_effect=[.999, 0]):
            d.act_event(self.p, 'pick')
        self.assertEqual(d.idx, 2)
        self.assertIn('steady_hands', d.endgame['used'])

    async def test_story_objective_and_consequences_are_visible_on_main_panels(self):
        for faction, spec in D.FACTION_STORIES.items():
            self.p['allegiance'] = faction
            self.p['promotions'][faction] = {'grandfathered': 4}
            E.save_profile(self.p)
            inter = self.interaction()
            await V._hub_factions(inter)
            view = inter.response.edit_message.call_args.kwargs['view']
            self.assertIn(spec['name'], content(view))
            self.assertIn(D.HALL_ADVENTURES[spec['roads'][0]]['name'], content(view))
            self.assertLess(len(content(view)), 600)
            d = self.board(spec['roads'][0], 1)
            self.assertIn('Next chapter:', V._delve_text(d, self.p))
            self.assertLess(len(V._delve_text(d, self.p)), 600)

    def test_action_journal_keeps_only_one_previous_snapshot(self):
        d = self.active(self.board())
        pending = S.prepare_action(self.p, d, 'evt:hall:force')
        pending.commit()
        p, d = E.get_profile(771), E.load_delve(818)
        pending = S.prepare_action(p, d, 'guard')
        pending.commit()
        d = E.load_delve(818)
        self.assertEqual(d.revision, 2)
        self.assertEqual(d.previous_board['revision'], 1)
        self.assertNotIn('previous_board', d.previous_board)

    async def test_old_and_previous_board_controls_resume_after_restart(self):
        d = self.active(self.board())
        old = d.to_dict()
        for k in ('revision', 'legacy_controls', 'previous_board'):
            old.pop(k)
        E.save_persistent_views({'818': old})
        old_d = E.Delve.from_dict(old)
        old_view, _ = V.build_delve_layout(old_d, self.p)
        old_ids = {x.custom_id for x in children(old_view) if hasattr(x, 'custom_id')}
        self.assertIn(f'skyrim:{d.delve_id}:evt:hall:force', old_ids)
        S.prepare_action(self.p, old_d, 'evt:hall:force').commit()
        current = E.load_delve(818)
        self.assertNotIn('previous_board', current.previous_board)
        client = discord.Client(intents=discord.Intents.none())
        V.reattach_skyrim_view(client, 818, current.to_dict())
        dispatch = client._connection._view_store._views[818]
        self.assertIn((2, f'skyrim:{d.delve_id}:evt:hall:force'), dispatch)
        self.assertIn((2, f'skyrim:{d.delve_id}:1:atk:blade'), dispatch)
        inter = self.interaction()
        await dispatch[(2, f'skyrim:{d.delve_id}:evt:hall:force')].callback(inter)
        self.assertEqual(E.load_delve(818).revision, 1)
        self.assertTrue(inter.response.edit_message.called)
        self.assertLessEqual(len(V._REGISTERED_DELVE_VIEWS[818]), 2)
        await client.close()

    async def test_shouts_capture_revision_and_control_budget_stays_compact(self):
        for key in D.HALL_ADVENTURES:
            d = self.board(key, 5)
            self.active(d)
            view, _ = V.build_delve_layout(d, self.p)
            self.assertLessEqual(len(children(view)), 40)
            self.assertLessEqual(len(content(view)), 600)
            self.assertTrue(view.is_persistent())
            for row in view.children:
                if isinstance(row, discord.ui.ActionRow):
                    buttons = [x for x in row.children if isinstance(x, discord.ui.Button)]
                    self.assertLessEqual(len(buttons), 3)
                    self.assertLessEqual(sum(len(x.label or '') for x in buttons), 30)
            shout = next(x for x in children(view) if isinstance(x, discord.ui.Select))
            self.assertIn(':0:shtsel', shout.custom_id)
            shout._values = ['1']
            with patch.object(V, '_handle_delve_click', AsyncMock()) as handle:
                d.revision = 4
                await shout.callback(self.interaction())
                self.assertEqual(handle.call_args.kwargs['expected_revision'], 0)

    async def test_repeat_retirement_ui_has_no_empty_select_or_double_reset(self):
        inter = self.interaction()
        await V._hub_hall(inter)
        view = inter.response.edit_message.call_args.kwargs['view']
        self.assertIn('Hall deeds', content(view))
        self.assertLess(len(content(view)), 600)
        self.assertFalse(any(isinstance(x, discord.ui.Select) for x in children(view)))
        again = next(x for x in children(view) if isinstance(x, discord.ui.Button) and x.label == 'Retire again')
        await again.callback(inter)
        confirm = inter.response.edit_message.call_args.kwargs['view']
        action = next(x for x in children(confirm) if isinstance(x, discord.ui.Button) and x.label == 'Confirm retirement')
        await action.callback(inter)
        self.assertEqual(E.legacy_rank(E.get_profile(771)), 6)
        await action.callback(inter)
        self.assertEqual(E.legacy_rank(E.get_profile(771)), 6)

    def test_new_boon_and_story_reward_are_visible_on_debrief(self):
        d = self.board()
        self.p['hall']['deeds'] = ['warden_clear', 'caravan_rescue']
        with patch.object(E.random, 'random', return_value=0):
            d.act_event(self.p, 'hall:talk')
        d.act_event(self.p, 'hall:ledger')
        self.assertIn('Steady Hands', V._debrief_text(d, self.p))
        self.assertIn('Reroll your first failed lockpick', V._debrief_details(d))
        self.assertIn('vault_quiet', d.summary['hall_deeds'])
