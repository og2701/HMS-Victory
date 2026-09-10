"""Reproducible offline balance sample; no bot, network, live saves or payouts.

Run with the bundled Python interpreter. Outputs JSON to stdout. This policy
heals before lethal risk, guards charges, varies Warden weapons, rescues scouts
and tries negotiation before waiting. It is a balance probe, not player telemetry.
"""
import copy
import json
from pathlib import Path
import random
import statistics
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    import discord
except ImportError:
    sys.path.append(str(Path.home() / '.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/lib/python3.12/site-packages'))
import config


def play(E, D, template, key, seed):
    random.seed(seed)
    p = copy.deepcopy(template)
    d = E.start_delve(p, 42, key)
    d._defer_fallen = True
    clicks = 0
    while d.playing() and clicks < 200:
        clicks += 1
        if p['potions'] and d.hearts <= 2 and d.hearts < E.delve_heart_max(d, p):
            d.act_potion(p)
        elif d.room['kind'] == 'event':
            r = d.room
            choices = E.story_choices(d)
            allowed = [x[2] for x in choices] if choices else []
            preferred = ['hall:talk','hall:wait','hall:ledger','story_help','story_study','safe','pray','pick','open','skip']
            if allowed:
                action = next((x for x in preferred if x in allowed), allowed[0])
            else:
                action = {'shrine':'pray','chest':'pick' if r.get('locked') else 'open',
                          'knee_trap':'continue','wordwall':'approach','giant':'retreat'}.get(r['key'],'skip')
            d.act_event(p, action)
        else:
            intent = E.combat_intent(d)
            if intent['key'] == 'charge' and intent['guard_available']:
                d.act_guard(p)
            else:
                styles = list(D.STYLES)
                if d.room.get('hall_warden'):
                    ward = d.room.get('hall_ward') or {}
                    styles = [s for s in styles if s != ward.get('style')]
                    untried = [s for s in styles if s not in ward.get('landed', [])]
                    styles = untried or styles
                style = max(styles, key=lambda s: E.fight_pct(p,d.room['key'],s,d) *
                            (1 + E.crit_chance(p,d.room['key'],s,d) + E.C.damage_bonus(intent['key'],s)))
                d.act_attack(p, style)
    if clicks == 200:
        raise AssertionError(f'Run did not terminate: {key}, seed {seed}')
    return {'won': d.state == 'cleared', 'clicks': clicks,
            'gold': p['septims'] - template['septims'], 'xp': p['xp'] - template['xp'],
            'potions': d.potions_used, 'deeds': p['hall']['deeds']}


def main():
    with tempfile.TemporaryDirectory(prefix='skyrim_balance_') as folder:
        for key in dir(config):
            if key.startswith('SKYRIM_') and key.endswith('_FILE') or key == 'PERSISTENT_VIEWS_FILE':
                setattr(config, key, str(Path(folder) / (key + '.json')))
        from lib.features.skyrim import engine as E, data as D
        E.create_profile(789, 'Simulated adventurer', 'warrior')
        base = E.get_profile(789)
        output = []
        for label, lvl, skill, gear, rank in [('early_endgame',20,60,3,0), ('veteran',32,95,6,5)]:
            for boons in (False, True):
                p = copy.deepcopy(base)
                p.update(xp=sum(D.xp_needed(l) for l in range(1,lvl)), alduin_slain=5,
                         skills={k:skill for k in E.SKILLS}, weapon_tier=gear, armour_tier=gear,
                         words=3,potions=3, perks={})
                points = lvl - 1
                for key, spec in D.PERKS.items():
                    allocated = min(points, spec['ranks'])
                    p['perks'][key] = allocated
                    points -= allocated
                p['stats']['dragons'] = 20
                p['legacy'] = {'rank':rank,'boons':list(D.BOONS)[:5] if rank else [], 'epitaphs':[]}
                p['hall']['boons'] = list(D.HALL_BOONS) if boons else []
                for key in [*D.HALL_ADVENTURES, 'bleak_falls', 'labyrinthian']:
                    runs = [play(E,D,p,key,seed) for seed in range(200)]
                    total_clicks = sum(r['clicks'] for r in runs)
                    output.append({'cohort':label,'extra_boons':boons,'road':key,'runs':len(runs),
                        'win_pct':round(100*sum(r['won'] for r in runs)/len(runs),1),
                        'mean_clicks':round(statistics.mean(r['clicks'] for r in runs),1),
                        'mean_gold':round(statistics.mean(r['gold'] for r in runs),1),
                        'gold_per_click':round(sum(r['gold'] for r in runs)/total_clicks,1),
                        'xp_per_click':round(sum(r['xp'] for r in runs)/total_clicks,1),
                        'mean_potions':round(statistics.mean(r['potions'] for r in runs),1),
                        'deed_pct':{d:round(100*sum(d in r['deeds'] for r in runs)/len(runs),1)
                                    for d in D.HALL_ADVENTURES.get(key,{}).get('deeds',[])}})
                E.drain_log()
        print(json.dumps({'seeds':'0..199','date':E._today_str(),'results':output},indent=2))


if __name__ == '__main__':
    main()
