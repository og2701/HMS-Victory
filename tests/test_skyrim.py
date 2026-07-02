"""Skyrim engine tests - profiles, delve state machine, combat maths, events.

engine.py is deliberately discord-free, so unlike the casino game tests no
stubbing is needed: point the state files at a temp dir and drive the real code.
Runnable under pytest or straight from the stdlib (`python3 tests/test_skyrim.py`).
"""
import os
import sys
import types
import random
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config

_TMP = tempfile.mkdtemp(prefix="skyrim_test_")
config.SKYRIM_PROFILES_FILE = os.path.join(_TMP, "profiles.json")
config.PERSISTENT_VIEWS_FILE = os.path.join(_TMP, "views.json")

from lib.features.skyrim import data as D
from lib.features.skyrim import engine as E


def _fixed_rolls(*vals):
    """Replace engine.random with a namespace whose random() pops from `vals`
    (repeating the last), while everything else stays truly random."""
    seq = list(vals)

    def _r():
        return seq.pop(0) if len(seq) > 1 else seq[0]
    return types.SimpleNamespace(random=_r, randint=random.randint,
                                 choices=random.choices, shuffle=random.shuffle,
                                 choice=random.choice, Random=random.Random)


def _restore_random():
    E.random = random


def _profile(class_key="warrior"):
    return E.create_profile(1, "Tester", class_key)


def _enemy_room_delve(profile, enemy_key="bandit", boss=False, extra_rooms=1):
    """A delve whose current room is a chosen enemy (plus trailing filler rooms)."""
    rooms = [{"kind": "enemy", "key": enemy_key, "boss": boss, "resolved": False}]
    rooms += [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}
              for _ in range(extra_rooms)]
    d = E.Delve(profile["user_id"], "Tester", 0, "embershard", rooms,
                hearts=E.heart_max(profile), shout_charges=profile["words"])
    return d


def _event_room_delve(profile, event_key, extra_rooms=1):
    rooms = [{"kind": "event", "key": event_key, "boss": False, "resolved": False}]
    rooms += [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}
              for _ in range(extra_rooms)]
    return E.Delve(profile["user_id"], "Tester", 0, "embershard", rooms,
                   hearts=E.heart_max(profile), shout_charges=profile["words"])


# ---------------------------------------------------------------------------
# Profiles / maths
# ---------------------------------------------------------------------------
def test_profile_roundtrip():
    p = _profile()
    assert E.get_profile(1)["class"] == "warrior"
    p["septims"] = 123
    E.save_profile(p)
    assert E.get_profile(1)["septims"] == 123


def test_level_curve():
    assert D.level_from_xp(0) == 1
    assert D.level_from_xp(D.xp_needed(1)) == 2
    into, need = D.xp_into_level(D.xp_needed(1) + 5)
    assert into == 5 and need == D.xp_needed(2)
    # perk points: one per level above 1, minus spent
    p = _profile()
    p["xp"] = D.xp_needed(1) + D.xp_needed(2)      # level 3
    assert E.level(p) == 3
    assert E.perk_points(p) == 2
    assert E.take_perk(p, "stalwart") is None
    assert E.perk_points(p) == 1
    assert E.heart_max(p) == E.BASE_HEARTS + 1


def test_percentages_clamped_and_typed():
    p = _profile("thief")
    for key in D.ENEMIES:
        f = E.fight_pct(p, key)
        assert E.ROLL_MIN <= f <= E.ROLL_MAX
    assert E.sneak_pct(p, "dragon") is None            # can't sneak past the boss arena
    assert E.persuade_pct(p, "wolf") is None           # can't reason with a wolf
    assert E.persuade_pct(p, "bandit") is not None
    # class affinity: warriors out-fight mages against bandits, mages against draugr
    w, m = _profile("warrior"), _profile("mage")
    w["skills"]["weapon"] = m["skills"]["weapon"] = 50
    assert E.fight_pct(w, "bandit") > E.fight_pct(m, "bandit")
    assert E.fight_pct(m, "draugr") > E.fight_pct(w, "draugr")


def test_skill_up_diminishes():
    p = _profile()
    p["skills"]["weapon"] = 15
    early = E._skill_up(p, "weapon")
    p["skills"]["weapon"] = 95
    late = E._skill_up(p, "weapon")
    assert early > late >= 1
    p["skills"]["weapon"] = 100
    assert E._skill_up(p, "weapon") == 0
    assert p["skills"]["weapon"] == 100


# ---------------------------------------------------------------------------
# Dungeon generation
# ---------------------------------------------------------------------------
def test_build_rooms_shape():
    for loc_key, loc in D.LOCATIONS.items():
        for _ in range(20):
            rooms = E.build_rooms(loc_key)
            assert len(rooms) in (loc["rooms"], loc["rooms"] + 1)  # +1 when a word wall is placed
            assert rooms[-1]["kind"] == "enemy" and rooms[-1]["boss"]
            assert rooms[-1]["key"] == loc["boss"]
            if loc.get("word_wall"):
                assert rooms[-2] == {"kind": "event", "key": "wordwall",
                                     "boss": False, "resolved": False}
            assert any(r["kind"] == "enemy" and not r["boss"] for r in rooms)
            for r in rooms:
                pool = D.ENEMIES if r["kind"] == "enemy" else D.EVENTS
                assert r["key"] in pool


def test_offer_locations_gates_dragons():
    p = _profile()
    assert all(not D.LOCATIONS[k].get("dragon_lair") for k in E.offer_locations(p))
    p["xp"] = 10_000
    assert E.level(p) >= getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8)
    assert any(D.LOCATIONS[k].get("dragon_lair") for k in E.offer_locations(p))


# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------
def test_attack_kill_and_loot():
    p = _profile()
    d = _enemy_room_delve(p, "bandit")
    E.random = _fixed_rolls(0.0)               # every roll succeeds
    try:
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.idx == 1 and d.kills == 1
    assert d.satchel > 0 and p["stats"]["kills"] == 1
    assert d.xp_gained > 0 and p["xp"] > 0


def test_attack_fail_wounds_then_kills_player():
    p = _profile()
    p["armour_tier"] = 0
    d = _enemy_room_delve(p, "bandit")
    E.random = _fixed_rolls(0.999)             # every roll fails (soak fails too)
    try:
        start_hearts = d.hearts
        d.act_attack(p)
        assert d.hearts == start_hearts - 1 and d.engaged
        while d.playing():
            d.act_attack(p)
    finally:
        _restore_random()
    assert d.state == "dead"
    assert p["stats"]["deaths"] == 1
    assert p["septims"] == 0                   # satchel lost
    assert p["active_delve"] is None


def test_boss_hp_staggers():
    p = _profile()
    d = _enemy_room_delve(p, "dragon", boss=False, extra_rooms=1)
    assert d.enemy_hp == 3
    # alternate: attack roll succeeds (0.0), crit roll fails (0.99)
    E.random = _fixed_rolls(0.0, 0.99, 0.0, 0.99, 0.0, 0.99)
    try:
        d.act_attack(p)
        assert d.enemy_hp == 2 and d.engaged and d.playing()
        d.act_attack(p)
        assert d.enemy_hp == 1
        d.act_attack(p)                         # the kill
    finally:
        _restore_random()
    assert d.idx == 1
    assert p["souls"] == 1 and p["stats"]["dragons"] == 1


def test_crit_double_damage():
    p = _profile()
    d = _enemy_room_delve(p, "draugr_deathlord", boss=True, extra_rooms=1)
    assert d.enemy_hp == 2
    E.random = _fixed_rolls(0.0)                # attack succeeds AND crits
    try:
        d.act_attack(p)                         # 2 damage: straight through the boss
    finally:
        _restore_random()
    assert d.idx == 1 and d.kills == 1


def test_bounty_room_tougher_and_richer():
    p = _profile()
    rooms = [{"kind": "enemy", "key": "bandit", "boss": False, "resolved": False, "bounty": True},
             {"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "embershard", rooms,
                hearts=3, shout_charges=0)
    assert d.enemy_hp == 2                      # +1 hp for the named variant
    E.random = _fixed_rolls(0.0, 0.99, 0.0, 0.99, 0.5)   # two clean non-crit hits
    try:
        d.act_attack(p)
        assert d.playing() and d.idx == 0
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.idx == 1
    assert d.satchel >= 3 * 12                  # triple loot floor for a tier-1 bounty


def test_adoring_fan_absorbs_a_wound():
    p = _profile()
    d = _enemy_room_delve(p, "troll")
    d.fan = True
    E.random = _fixed_rolls(0.999)              # attack misses, soak fails
    try:
        hearts = d.hearts
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.hearts == hearts                   # the fan took it
    assert not d.fan


def test_alduin_takes_wing_again():
    p = _profile()
    p["words"] = 3
    d = _enemy_room_delve(p, "alduin", boss=True, extra_rooms=0)
    d.shout_charges = 3
    assert d.enemy_hp == D.ENEMIES["alduin"]["hp"]
    d.act_shout(p)
    assert d.grounded
    E.random = _fixed_rolls(0.0, 0.99, 0.0, 0.99)        # two clean non-crit hits: 8 -> 6
    try:
        d.act_attack(p)
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.enemy_hp == max(D.ALDUIN_REFLIGHT_HP)
    assert not d.grounded                        # reflight threshold hit
    d.act_shout(p)                               # ground him again
    assert d.grounded and d.shout_charges == 1


def test_weather_is_deterministic_and_applied():
    w1 = E.weather_today("2026-07-02")
    w2 = E.weather_today("2026-07-02")
    assert w1 == w2
    assert any(E.weather_today(f"2026-07-{d:02d}")["key"] != w1["key"] for d in range(1, 29)) \
        or w1["key"] == "clear"                  # not literally frozen forever
    p = _profile()
    real = E.weather_today
    try:
        E.weather_today = lambda date_str=None: {"key": "x", "name": "T", "emoji": "t",
                                                 "desc": "", "fight": 10, "sneak": 10,
                                                 "loot": 2.0, "xp": 2.0, "heavy": 0.0}
        boosted = E.fight_pct(p, "bandit")
        E.weather_today = lambda date_str=None: {"key": "clear", **D.WEATHERS["clear"]}
        base = E.fight_pct(p, "bandit")
    finally:
        E.weather_today = real
    assert boosted == base + 10


def test_daily_delve_shared_and_once():
    p1 = E.create_profile(11, "A", "warrior")
    p2 = E.create_profile(12, "B", "thief")
    assert E.daily_available(p1)
    d1 = E.start_delve(p1, 0, None, kind="daily")
    d2 = E.start_delve(p2, 0, None, kind="daily")
    assert d1.location == d2.location
    assert d1.rooms == d2.rooms                  # same seeded layout for everyone
    assert d1.daily and not E.daily_available(p1)
    d1.state = "dead"
    E.record_daily_result(p1, d1)
    res = E.daily_results()
    assert res[str(p1["user_id"])]["state"] == "dead"


def test_alduin_gates_and_daily_attempt():
    p = _profile()
    assert not E.alduin_available(p)
    p["xp"] = 60_000
    p["words"] = 3
    p["stats"]["dragons"] = 5
    ready, _line = E.alduin_ready(p)
    assert ready and E.alduin_available(p)
    E.start_delve(p, 0, "skuldafn", kind="alduin")
    assert not E.alduin_available(p)             # one attempt per day


def test_property_chain_and_comforts():
    p = _profile()
    p["septims"] = 20_000
    assert E.buy_home(p, "alchemy_lab") is not None      # needs Breezehome first
    assert E.buy_home(p, "breezehome") is None
    assert E.buy_home(p, "breezehome") is not None       # no double-buy
    assert E.buy_home(p, "alchemy_lab") is None
    p["potions"] = 0
    d = E.start_delve(p, 0, "embershard")
    assert d.blessed                                     # well-rested
    assert p["potions"] == 1                             # the lab brewed one
    d2 = E.start_delve(p, 0, "embershard")
    assert not d2.blessed                                # only the first delve of the day


def test_sneak_success_and_spotted():
    p = _profile("thief")
    d = _enemy_room_delve(p, "bandit")
    E.random = _fixed_rolls(0.0)
    try:
        d.act_sneak(p)
    finally:
        _restore_random()
    assert d.idx == 1 and d.satchel == 0        # no loot from sneaking
    assert p["stats"]["sneaks"] == 1

    d2 = _enemy_room_delve(p, "bandit")
    E.random = _fixed_rolls(0.999)
    try:
        hearts = d2.hearts
        d2.act_sneak(p)
        assert d2.spotted and d2.engaged and d2.hearts == hearts - 1
        # once spotted, another sneak is a no-op
        idx = d2.idx
        d2.act_sneak(p)
        assert d2.idx == idx and d2.hearts == hearts - 1
    finally:
        _restore_random()


def test_shout_clears_room_and_grounds_dragon():
    p = _profile()
    p["words"] = 2
    d = _enemy_room_delve(p, "troll")
    d.shout_charges = 2
    d.act_shout(p)
    assert d.idx == 1 and d.shout_charges == 1 and d.satchel > 0

    d2 = _enemy_room_delve(p, "dragon")
    d2.shout_charges = 1
    d2.act_shout(p)
    assert d2.grounded and d2.idx == 0 and d2.shout_charges == 0
    assert E.fight_pct(p, "dragon", d2) > E.fight_pct(p, "dragon")


def test_potion_and_leave_and_flee():
    p = _profile()
    d = _enemy_room_delve(p, "bandit")
    d.hearts = 1
    pots = p["potions"]
    d.act_potion(p)
    assert d.hearts == 2 and p["potions"] == pots - 1

    d.satchel = 100
    d.act_leave(p)                              # not engaged: clean exit
    assert d.state == "left" and p["septims"] == 100

    p2 = E.create_profile(2, "Fleeer", "warrior")
    d2 = _enemy_room_delve(p2, "bandit")
    d2.satchel = 100
    d2.engaged = True
    d2.act_leave(p2)
    assert d2.state == "fled"
    assert p2["septims"] == int(100 * E.FLEE_KEEP)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
def test_wordwall_needs_soul():
    p = _profile()
    d = _event_room_delve(p, "wordwall")
    d.act_event(p, "approach")
    assert p["words"] == 0 and d.idx == 1        # chants, but no soul to spend

    p["souls"] = 1
    d2 = _event_room_delve(p, "wordwall")
    charges = d2.shout_charges
    d2.act_event(p, "approach")
    assert p["words"] == 1 and p["souls"] == 0
    assert d2.shout_charges == charges + 1


def test_sweetroll_and_satchel_and_maiq():
    p = _profile()
    d = _event_room_delve(p, "sweetroll")
    d.hearts = 1
    d.act_event(p, "take")
    assert d.hearts == 2 and p["stats"]["sweetrolls"] == 1 and d.idx == 1

    p["potions"] = 0
    d2 = _event_room_delve(p, "satchel")
    d2.act_event(p, "take")
    assert p["potions"] == 1 and d2.idx == 1

    d3 = _event_room_delve(p, "maiq")
    xp = p["xp"]
    d3.act_event(p, "talk")
    assert p["xp"] > xp and d3.idx == 1


def test_giant_launch_banks_satchel():
    p = _profile()
    d = _event_room_delve(p, "giant")
    d.satchel = 250
    E.random = _fixed_rolls(0.0)                # 0.0 < 0.5 -> launched
    try:
        d.act_event(p, "approach")
    finally:
        _restore_random()
    assert d.state == "launched"
    assert p["septims"] == 250 and p["stats"]["launched"] == 1


def test_knee_trap_springs_on_entry():
    p = _profile()
    rooms = [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False},
             {"kind": "event", "key": "knee_trap", "boss": False, "resolved": False},
             {"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "embershard", rooms, hearts=3, shout_charges=0)
    E.random = _fixed_rolls(0.0)                # kill roll succeeds; soak roll (0.0 < soak? soak=0 no)
    try:
        d.act_attack(p)                         # clears room 0, enters the trap room
    finally:
        _restore_random()
    assert d.idx == 1
    assert d.room["resolved"]
    assert d.hearts == 2                        # the trap bit


# ---------------------------------------------------------------------------
# Persistence / lifecycle
# ---------------------------------------------------------------------------
def test_delve_serialisation_roundtrip():
    p = _profile()
    d = _enemy_room_delve(p, "draugr_deathlord", boss=True, extra_rooms=0)
    d.message_id = 4242
    d.enemy_hp = 1
    d.engaged = True
    d.satchel = 77
    E.save_delve(d)
    back = E.load_delve(4242)
    assert back is not None
    assert back.enemy_hp == 1 and back.engaged and back.satchel == 77
    assert back.room["key"] == "draugr_deathlord"
    E.delete_delve(4242)
    assert E.load_delve(4242) is None


def test_abandon_banks_satchel():
    p = _profile()
    d = _enemy_room_delve(p, "bandit")
    d.message_id = 555
    d.satchel = 60
    E.save_delve(d)
    p["active_delve"] = 555
    E.abandon_active(p)
    assert p["septims"] >= 60
    assert p["active_delve"] is None
    assert E.load_delve(555) is None


def test_stamina():
    p = _profile()
    per_day = getattr(config, "SKYRIM_DELVES_PER_DAY", 3)
    assert E.delves_left(p) == per_day
    E.spend_stamina(p)
    assert E.delves_left(p) == per_day - 1
    p["stamina"]["date"] = "2000-01-01"          # a new day resets it
    assert E.delves_left(p) == per_day


# ---------------------------------------------------------------------------
# Shop
# ---------------------------------------------------------------------------
def test_shop():
    p = _profile()
    p["septims"] = 10
    assert E.buy_potion(p) is not None           # too poor (and possibly full)
    p["potions"] = 0
    p["septims"] = D.POTION_PRICE
    assert E.buy_potion(p) is None
    assert p["potions"] == 1 and p["septims"] == 0

    p["septims"] = 100_000
    assert E.buy_gear(p, "weapon") is None
    assert p["weapon_tier"] == 1
    # dragonbone is gated on dragons slain, not just coin
    p["weapon_tier"] = len(D.GEAR_TIERS) - 2
    err = E.buy_gear(p, "weapon")
    assert err is not None and "dragon" in err.lower()
    p["stats"]["dragons"] = D.GEAR_TIERS[-1]["dragons"]
    assert E.buy_gear(p, "weapon") is None
    assert E.buy_gear(p, "weapon") == "Nothing finer exists in Tamriel."


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok  {name}")
            except AssertionError:
                failed += 1
                import traceback
                print(f"FAIL  {name}")
                traceback.print_exc()
    print("ALL PASS" if not failed else f"{failed} FAILURES")
    sys.exit(1 if failed else 0)
