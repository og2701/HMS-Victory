"""Exercise new rules through real engine entry points, not helper-only calls."""
import copy
import datetime
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch


class AdventureIntegrationTests(unittest.TestCase):
    def setUp(self):
        import config
        from lib.features.skyrim import engine as E, data as D
        from lib.core import file_operations as F
        self.E,self.D=E,D
        self.folder=tempfile.TemporaryDirectory(prefix="skyrim_integrated_")
        self.addCleanup(self.folder.cleanup)
        changes={k:str(Path(self.folder.name)/(k+'.json')) for k in
                 ('SKYRIM_PROFILES_FILE','SKYRIM_DAILY_FILE','SKYRIM_GRAVEYARD_FILE',
                  'SKYRIM_WORLDBOSS_FILE','PERSISTENT_VIEWS_FILE')}
        for obj,key,val in [(config,k,v) for k,v in changes.items()] + [(F,'PERSISTENT_VIEWS_FILE',changes['PERSISTENT_VIEWS_FILE'])]:
            p=patch.object(obj,key,val);p.start();self.addCleanup(p.stop)
        self.p=E.create_profile(711,'Adventurer','warrior')
        E.drain_log()

    def veteran(self, uid=711):
        p=self.p if uid==711 else self.E.create_profile(uid,'Ally','mage')
        p['xp']=sum(self.D.xp_needed(i) for i in range(1,21))
        p['skills']={s:75 for s in self.E.SKILLS}
        p['stats']['delves']=30
        self.E.save_profile(p)
        return p

    def test_tutorial_is_free_contextual_and_once_only(self):
        from lib.features.skyrim import sessions as S
        p=self.E.get_profile(711)
        self.assertTrue(self.E.tutorial_available(p))
        charges=self.E.delves_left(p)
        pending=S.prepare(p,1,'embershard',kind='tutorial')
        self.assertEqual(len(pending.delve.rooms),4)
        self.assertTrue(all(r.get('lesson') for r in pending.delve.rooms))
        self.assertTrue(self.E.tutorial_available(self.E.get_profile(711)))
        pending.commit(901)
        saved=self.E.get_profile(711)
        self.assertFalse(self.E.tutorial_available(saved))
        self.assertEqual(self.E.delves_left(saved),charges)
        self.assertEqual(self.E.load_delve(901).kind,'tutorial')

    def test_abandon_obeys_flee_and_clavicus_rules(self):
        E=self.E
        d=E.Delve(711,'Adventurer',1,'embershard',
                  [{'kind':'enemy','key':'bandit','boss':False,'resolved':False}],
                  hearts=3,shout_charges=0,satchel=100,ingredients={'troll_fat':2},engaged=True)
        d.message_id=801;self.p['active_delve']=801
        E.save_delve(d)
        E.abandon_active(self.p)
        self.assertEqual(self.p['septims'],70)
        self.assertEqual(self.p['ingredients']['troll_fat'],2)
        d.pacts=['clavicus'];self.p['active_delve']=801;E.save_delve(d)
        before=copy.deepcopy(self.p)
        with self.assertRaisesRegex(ValueError,'Bargain|pact|exit'):
            E.abandon_active(self.p)
        self.assertEqual(before,self.p)

    def test_weekly_task_reward_persists_on_read_once(self):
        E=self.E; D=self.D
        old_date=(datetime.date.fromisoformat(E._today_str())-datetime.timedelta(days=7)).isoformat()
        y,w=E._iso_week(old_date);keys=E.weekly_tasks(old_date);key=keys[0]
        self.p['tasks']={'week':f'{y}-{w}','prog':{key:D.TASKS[key]['n']},'claimed':[],'bonus':False}
        E.save_profile(self.p)
        saved=E.get_profile(711)
        self.assertGreater(saved['septims'],0)
        self.assertEqual(E.get_profile(711)['septims'],saved['septims'])
        self.assertNotEqual(saved['tasks']['week'],f'{y}-{w}')

    def test_hunt_rollover_retains_claimable_reward_through_engine(self):
        E=self.E
        old_date=(datetime.date.fromisoformat(E._today_str())-datetime.timedelta(days=7)).isoformat()
        old=E.world_boss(old_date)
        old['shares']['711']={'septims':650,'xp':170,'claimed':False}
        E._wb_save(old)
        now=E.world_boss()
        self.assertEqual(E.wb_share_waiting(self.p,now)['septims'],650)
        self.assertIsNotNone(E.wb_claim(self.p))
        paid=self.p['septims']
        self.assertGreater(paid,0)
        self.assertIsNone(E.wb_claim(E.get_profile(711)))
        self.assertEqual(E.get_profile(711)['septims'],paid)

    def test_hunt_roles_reach_next_ally_and_cannot_repeat(self):
        E=self.E
        p=self.veteran();ally=self.veteran(712)
        lines,damage,slain,store=E.wb_march(p,role='expose')
        self.assertIn('expose',store.get('ally_support',{}))
        E.save_profile(p)
        lines,_,_,store=E.wb_march(ally,role='attack')
        self.assertTrue(any('Adventurer' in line and ('12' in line or 'expos' in line.lower()) for line in lines))
        self.assertNotIn('expose',store.get('ally_support',{}))
        with self.assertRaises(ValueError):
            E.wb_march(ally,role='attack')

    def test_faction_missions_receive_actual_game_events(self):
        from lib.features.skyrim import progression as P
        E=self.E
        p=self.veteran();P.ensure_promotions(p)
        p['allegiance']='companions';p['favours']={'companions':2}
        current=P.promotion(p)
        for _ in range(current['goal']):
            E.task_event(p,'kill',style='blade')
        self.assertTrue(P.promotion(p)['claimable'])
        self.assertEqual(E.faction_rank(p),self.D.FACTION_RANKS[0])
        self.assertIsNone(P.claim_promotion(p))
        self.assertEqual(E.faction_rank(p),self.D.FACTION_RANKS[1])

    def test_hunt_support_still_helps_at_the_normal_hit_cap(self):
        E=self.E
        p=self.veteran();p['skills']={s:100 for s in E.SKILLS};p['weapon_tier']=5
        store=E.world_boss()
        from lib.features.skyrim import progression as P
        P.finish_hunt_support(store,self.veteran(712),'expose')
        E._wb_save(store)
        self.assertEqual(E._wb_attack_pct(p,E.wb_boss(store)),86)
        # A 90% roll misses at the ordinary cap but should land with +12 support.
        with patch.object(E,'WB_EXCHANGES',1), patch.object(E.random,'random',side_effect=[.90,.99,.99]):
            _,damage,_,_=E.wb_march(p)
        self.assertEqual(damage,1)

    def test_hunt_auto_style_uses_the_boss_matchup(self):
        p=self.veteran();p['skills']={s:100 for s in self.E.SKILLS};p['weapon_tier']=0
        self.assertEqual(self.E._wb_attack_pct(p,{'type':'undead'}),80)

    def test_retirement_keeps_selected_ability_and_receipts(self):
        from lib.features.skyrim import progression as P
        E=self.E;D=self.D
        p=self.veteran();p['alduin_slain']=1
        skill='blade';choice=next(iter(D.DOCTRINES[skill]))
        other=next(iter(D.DOCTRINES['marksman']))
        p['doctrines']={skill:[choice],'marksman':[other]};p['hunt_receipts']=['already-paid']
        self.assertIsNone(P.inherit(p,skill,choice))
        self.assertIsNone(E.retire(p,E.boon_offer(p)[0]))
        self.assertEqual(p['doctrines'],{skill:[choice]})
        self.assertEqual(p['hunt_receipts'],['already-paid'])
        self.assertEqual(E.level(p),1)

    def test_replaced_daily_keeps_original_day(self):
        from lib.features.skyrim import sessions as S
        E=self.E
        yesterday=(datetime.date.fromisoformat(E._today_str())-datetime.timedelta(days=1)).isoformat()
        self.p['daily']={'date':yesterday}
        self.p['active_delve']=601;E.save_profile(self.p)
        d=E.Delve(711,'Adventurer',1,'embershard',
                  [{'kind':'enemy','key':'bandit','boss':False,'resolved':False}],
                  daily=True,hearts=3,shout_charges=0,satchel=50,message_id=601)
        E.save_delve(d)
        pending=S.prepare(E.get_profile(711),1,'embershard')
        pending.commit(602)
        self.assertNotIn('711',E.daily_results())
        self.assertIn('711',E._daily_store()[yesterday])
