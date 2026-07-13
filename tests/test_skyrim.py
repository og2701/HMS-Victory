"""Skyrim engine tests - profiles, delve state machine, combat maths, events.

engine.py is deliberately discord-free, so unlike the casino game tests no
stubbing is needed: point the state files at a temp dir and drive the real code.
Runnable under pytest or straight from the stdlib (`python3 tests/test_skyrim.py`).
"""
import os
import sys
import types
import random
import datetime
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
    assert E.get_profile(1)["stone"] == "warrior"
    p["septims"] = 123
    E.save_profile(p)
    assert E.get_profile(1)["septims"] == 123


def test_class_era_profile_migrates():
    """Profiles from before the stones rework upgrade seamlessly on first read."""
    import json
    store = json.load(open(config.SKYRIM_PROFILES_FILE)) if os.path.exists(
        config.SKYRIM_PROFILES_FILE) else {}
    store["999"] = {
        "user_id": 999, "name": "OldTimer", "class": "thief", "xp": 500,
        "skills": {"weapon": 47, "sneak": 33, "speech": 25},
        "perks": {"stalwart": 1}, "septims": 800, "potions": 1,
        "weapon_tier": 2, "armour_tier": 1, "souls": 1, "words": 1,
        "stats": {"delves": 9, "clears": 5, "deaths": 2, "kills": 20, "sneaks": 8,
                  "persuades": 3, "dragons": 1, "sweetrolls": 1, "flees": 1, "launched": 0},
        "stamina": {"date": "2026-01-01", "used": 0}, "active_delve": None,
        "created": "2026-01-01",
    }
    json.dump(store, open(config.SKYRIM_PROFILES_FILE, "w"))
    p = E.get_profile(999)
    assert p["stone"] == "thief"
    assert "class" not in p and "weapon" not in p["skills"]
    assert p["skills"]["marksman"] == 47          # thief's weapon skill -> Marksman
    assert p["skills"]["sneak"] == 33 and p["skills"]["speech"] == 25
    assert p["skills"]["blade"] == 15 and p["skills"]["lockpicking"] == 15
    assert p["armour_style"] == "heavy"
    assert p["septims"] == 800 and p["souls"] == 1     # nothing else touched
    # and the upgrade persisted
    assert "weapon" not in (json.load(open(config.SKYRIM_PROFILES_FILE))["999"]["skills"])


def test_archetype_titles():
    p = _profile()
    assert E.archetype(p) == "Adventurer"          # nothing at 30 yet
    p["skills"]["sneak"] = 45
    p["skills"]["marksman"] = 40
    assert E.archetype(p) == "Stealth Archer"
    p["skills"]["destruction"] = 60
    p["skills"]["blade"] = 50
    assert E.archetype(p) == "Spellsword"


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
        for style in D.STYLES:
            f = E.fight_pct(p, key, style)
            assert E.ROLL_MIN <= f <= E.ROLL_MAX
    assert E.sneak_pct(p, "dragon") is None            # can't sneak past the boss arena
    assert E.persuade_pct(p, "wolf") is None           # can't reason with a wolf
    assert E.persuade_pct(p, "bandit") is not None
    # style-vs-type: at equal skill, fire beats arrows against the walking dead
    q = _profile("warrior")
    for st in D.STYLES:
        q["skills"][st] = 50
    assert E.fight_pct(q, "draugr", "destruction") > E.fight_pct(q, "draugr", "marksman")
    assert E.fight_pct(q, "bandit", "blade") > E.fight_pct(q, "bandit", "destruction")
    assert E.best_style(q, "draugr") == "destruction"


def test_skill_up_diminishes_and_stone_boosts():
    p = _profile()                       # warrior stone boosts blade
    p["skills"]["blade"] = 15
    early = E._skill_up(p, "blade")
    p["skills"]["blade"] = 95
    late = E._skill_up(p, "blade")
    assert early > late >= 1
    p["skills"]["blade"] = 100
    assert E._skill_up(p, "blade") == 0
    # the stone-blessed skill learns faster than an unblessed one at equal level
    p["skills"]["blade"] = p["skills"]["speech"] = 40
    assert E._skill_up(p, "blade") > E._skill_up(p, "speech")


# ---------------------------------------------------------------------------
# Dungeon generation
# ---------------------------------------------------------------------------
def test_build_rooms_shape():
    for loc_key, loc in D.LOCATIONS.items():
        for _ in range(40):
            rooms = E.build_rooms(loc_key)
            # base rooms, +1 each optional: word wall, Fork, Fallen corpse
            assert loc["rooms"] <= len(rooms) <= loc["rooms"] + 3
            assert rooms[-1]["kind"] == "enemy" and rooms[-1]["boss"]
            assert rooms[-1]["key"] == loc["boss"]
            if loc.get("word_wall"):
                # the word wall always sits immediately before the boss
                assert rooms[-2]["key"] == "wordwall"
            # a Fork, if placed, is a valid event before any word wall / boss
            forks = [i for i, r in enumerate(rooms) if r["key"] == "fork"]
            assert len(forks) <= 1
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


def test_route_conditions_deterministic_and_applied():
    # deterministic per (date, location), and not always plain
    assert E.route_condition("embershard", "2026-07-09") == E.route_condition("embershard", "2026-07-09")
    rolled = {E.route_condition(k, f"2026-07-{d:02d}")
              for k in D.LOCATIONS for d in range(1, 15)}
    assert None in rolled and len(rolled) > 3          # plain roads AND real conditions
    # structural effects, checked against identical seeds
    base = E.build_rooms("embershard", rng=random.Random(7))
    over = E.build_rooms("embershard", rng=random.Random(7), route="overrun")
    assert (sum(1 for r in over if r["kind"] == "enemy")
            == sum(1 for r in base if r["kind"] == "enemy") + 1)
    way = E.build_rooms("embershard", rng=random.Random(7), route="waylaid")
    assert any(r["key"] == "fallen" for r in way)
    crab = E.build_rooms("embershard", rng=random.Random(7), route="caravan")
    assert any(r["key"] == "mudcrab" for r in crab)
    nest = E.build_rooms("labyrinthian", rng=random.Random(7), affix_level=20, route="elites")
    assert any(r.get("affix") for r in nest)
    # Rich Pickings multiplies the clear bonus; Quiet Roads blesses from the door
    p = _profile()
    rooms = [{"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "embershard", rooms, hearts=3, shout_charges=0,
                route="rich")
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.state == "cleared"
    rich_expected = int(int(D.LOCATIONS["embershard"]["clear_septims"])
                        * D.ROUTE_CONDITIONS["rich"]["clear_mult"])
    assert d.satchel >= rich_expected                  # clear bonus scaled (plus kill loot)
    real = E.route_condition
    try:
        E.route_condition = lambda loc, date_str=None: "quiet"
        d2 = E.Delve.start(p, 0, "embershard")
        assert d2.blessed and d2.route == "quiet"
    finally:
        E.route_condition = real


def test_expanded_map_pool_sound():
    # every location still generates valid rooms and the new ones are reachable
    assert len([k for k, v in D.LOCATIONS.items()
                if not v.get("alduin") and not v.get("soulcairn")]) >= 15
    for key in ("redorans_retreat", "white_river", "silent_moons", "hillgrunds_tomb",
                "rannveigs_fast", "alftand", "forelhost", "dragontooth"):
        loc = D.LOCATIONS[key]
        assert loc["boss"] in D.ENEMIES and D.ENEMIES[loc["boss"]].get("boss")
        assert all(k in D.ENEMIES for k in loc["pool"])
        rooms = E.build_rooms(key)
        assert rooms[-1]["key"] == loc["boss"]


def test_ingredient_sources_and_location_drops():
    src = E.ingredient_sources()
    assert set(src) == set(D.INGREDIENTS)              # every ingredient has a source
    assert src["dragon_scale"] == ["dragons"]
    assert "undead" in src["bone_meal"]
    # a bandit mine hints herbs; a dragon lair hints scales
    assert E.location_drops("embershard")
    assert D.INGREDIENTS["dragon_scale"]["emoji"] in E.location_drops("ancients_ascent")


def test_daily_always_features_a_marked_foe():
    p1 = E.create_profile(21, "A", "warrior")
    p2 = E.create_profile(22, "B", "thief")
    d1 = E.start_delve(p1, 0, None, kind="daily")
    d2 = E.start_delve(p2, 0, None, kind="daily")
    assert d1.rooms == d2.rooms                        # still the same board for everyone
    assert any(r.get("affix") for r in d1.rooms)       # and it always has a marked foe
    assert E.daily_affixes()                           # the panel tease agrees
    assert set(E.daily_affixes()) == {r["affix"] for r in d1.rooms if r.get("affix")}
    # the daily never rolls the Soul Cairn as its location
    assert not D.LOCATIONS[d1.location].get("soulcairn")


def test_stirred_band_scaling():
    p = _profile()
    p["xp"] = 60_000                                   # far past every map's gate
    p["weapon_tier"] = 6
    p["armour_tier"] = 6
    # easy never stirs; medium mildly (capped 3); hard fully (5); lairs capped at 4
    assert E.stirred_rank(p, "embershard") == 0
    assert 1 <= E.stirred_rank(p, "fellglow") <= 3
    assert E.stirred_rank(p, "labyrinthian") == 5
    assert 1 <= E.stirred_rank(p, "ancients_ascent") <= 4
    # gear counts toward prowess
    naked = dict(p, weapon_tier=0, armour_tier=0)
    assert E.prowess(p) > E.prowess(naked)
    # applied on ANY launch of a hard map: malus on every roll, tougher master
    d = E.Delve.start(p, 0, "labyrinthian")
    r = d.stirred
    assert r == 5
    key = next(rm["key"] for rm in d.rooms if rm["kind"] == "enemy")
    with_rank = E._fight_raw(p, key, "blade", d)
    d.stirred = 0
    base = E._fight_raw(p, key, "blade", d)
    d.stirred = r
    assert with_rank == base - D.STIRRED_FIGHT_PER_RANK * r
    boss_room = d.rooms[-1]
    hp_with = d._hp_for(boss_room)
    d.stirred = 0
    hp_base = d._hp_for(boss_room)
    d.stirred = r
    assert hp_with == hp_base + 1
    # Skuldafn never stirs (Alduin has Echoes for that); easy maps never stir
    assert E.stirred_rank(p, "skuldafn") == 0
    # fresh characters see no stirring anywhere they can reach
    q = _profile()
    assert all(E.stirred_rank(q, k) == 0 for k in E.offer_locations(q))


def test_meditation_sink():
    p = _profile()
    p["xp"] = 60_000                                   # a pile of levels -> spare points
    p["words"] = 3
    for key, perk in D.PERKS.items():                  # perk table fully maxed
        p["perks"][key] = perk["ranks"]
    spare = E.perk_points(p)
    assert spare > 0                                   # points still accrue past the table
    p["voice"] = {"charges": 0, "date": E._today_str()}
    assert E.meditate(p) is None                       # ...and now have somewhere to go
    assert E.voice_charges(p) == 3
    assert E.perk_points(p) == spare - 1               # the point is truly spent
    assert E.meditate(p) == "Your breath is already full."
    q = _profile()
    q["xp"] = 1_000
    assert E.meditate(q) is not None                   # no Voice yet, no meditation


def test_voice_is_persistent():
    p = _profile()
    p["words"] = 3
    assert E.voice_charges(p) == 3                     # grandfathered in at full breath
    # spend the whole Voice in one delve...
    d = _enemy_room_delve(p, "troll", extra_rooms=3)
    assert d.shout_charges == 3
    d.act_shout(p, 2)                                  # FUS RO flattens the troll
    d.act_shout(p, 1)                                  # FUS staggers the next
    assert d.shout_charges == 0 and E.voice_charges(p) == 0
    # ...and the next delve starts empty: no free refill at the door
    d2 = E.Delve.start(p, 0, "embershard")
    assert d2.shout_charges == 0
    # a new dawn returns one charge (two days -> two)
    p["voice"]["date"] = "2000-01-01"
    p["voice"]["charges"] = 0
    assert E.voice_charges(p) == 3                     # long gap caps at words known
    p["voice"]["date"] = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    p["voice"]["charges"] = 0
    assert E.voice_charges(p) == 1
    # absorbing a dragon soul renews the Thu'um in full
    p["voice"]["charges"] = 0
    d3 = _enemy_room_delve(p, "dragon", boss=False, extra_rooms=1)
    d3.shout_charges = 0
    d3.enemy_hp = 1
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d3.act_attack(p)                               # the kill
    finally:
        _restore_random()
    assert d3.shout_charges == 3 and E.voice_charges(p) == 3
    # Skuldafn grants a full Voice at the gate - as a loan, not a refill
    p["voice"]["charges"] = 0
    p["voice"]["date"] = E._today_str()
    p["xp"] = 60_000
    p["stats"]["dragons"] = 5
    d4 = E.start_delve(p, 0, "skuldafn", kind="alduin")
    assert d4.shout_charges == 3
    d4.act_shout(p, 1)                                 # grounding Alduin's trash floor... spends the loan
    assert E.voice_charges(p) == 0                     # your own breath untouched


def test_alduin_echoes():
    p = _profile()
    p["xp"] = 60_000
    p["words"] = 3
    p["stats"]["dragons"] = 5
    assert E.alduin_ready(p)[0]                        # first meeting: 5 dragons suffice
    p["alduin_slain"] = 2
    assert not E.alduin_ready(p)[0]                    # rematch price: 5 + 3 per kill = 11
    p["stats"]["dragons"] = 11
    assert E.alduin_ready(p)[0]
    d = E.start_delve(p, 0, "skuldafn", kind="alduin")
    assert d.echo == 2
    # he returns a heart stronger per echo, and harder to face
    alduin_room = d.rooms[-1]
    assert d._hp_for(alduin_room) == D.ENEMIES["alduin"]["hp"] + 2
    d0 = E.Delve(p["user_id"], "T", 0, "skuldafn", list(d.rooms), hearts=5, shout_charges=3)
    assert (E._fight_raw(p, "alduin", "blade", d)
            == E._fight_raw(p, "alduin", "blade", d0) - 6)
    # the cap holds: past kills beyond 4 don't stack forever
    p["alduin_slain"] = 9
    assert E.alduin_echo(p) == 4


def test_daedric_pacts():
    p = _profile()
    assert E.swear_pacts(p, ["boethiah"]) is not None  # level-gated
    p["xp"] = 10_000                                   # level 10+
    assert E.swear_pacts(p, ["boethiah", "namira", "dagon", "clavicus", "bogus"]) is None
    assert p["nextpacts"] == ["boethiah", "namira", "dagon", "clavicus"]
    d = E.start_delve(p, 0, "embershard")
    assert d.pacts == p.get("nextpacts", []) or p["nextpacts"] == []   # consumed
    assert d.pacts == ["boethiah", "namira", "dagon", "clavicus"]
    assert E.pact_mult(d) == E.PACT_MULT_CAP           # full stack caps at 4
    # Clavicus prices himself by the company: nearly free alone, richer stacked
    solo = E.Delve(p["user_id"], "T", 0, "embershard",
                   [{"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}],
                   hearts=3, shout_charges=0, pacts=["clavicus"])
    assert E.pact_mult(solo) == 1.2
    duo = E.Delve(p["user_id"], "T", 0, "embershard", list(solo.rooms),
                  hearts=3, shout_charges=0, pacts=["clavicus", "boethiah"])
    assert abs(E.pact_mult(duo) - 1.45 * 1.5) < 1e-9
    # Boethiah: the ceiling drops to 72
    for st in D.STYLES:
        p["skills"][st] = 100
    p["weapon_tier"] = 6
    assert E.fight_pct(p, "skeever", "blade", d) == E.PACT_ROLL_MAX
    # Namira: the bottle stays corked
    p["potions"] = 2
    d.hearts = 1
    d.act_potion(p)
    assert p["potions"] == 2 and d.hearts == 1
    # Dagon: every wound crushes
    assert d._heavy(D.ENEMIES["skeever"]) == 1.0
    # Clavicus: no way out
    d.act_leave(p)
    assert d.playing()
    # banking honours the pact: clear a one-room pact delve and check the x4
    q = E.create_profile(3, "Sworn", "warrior")
    q["xp"] = 10_000
    rooms = [{"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}]
    d2 = E.Delve(q["user_id"], "S", 0, "embershard", rooms, hearts=3, shout_charges=0,
                 pacts=["clavicus", "dagon", "boethiah", "namira"])
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d2.act_attack(q)
    finally:
        _restore_random()
    assert d2.state == "cleared"
    plain = int(D.LOCATIONS["embershard"]["clear_septims"])
    assert d2.satchel >= plain * E.PACT_MULT_CAP       # whole satchel multiplied
    assert q["stats"].get("pact_clears") == 1


def test_offers_rotate_daily():
    p = _profile()
    p["xp"] = 60_000                                   # everything unlocked
    seen = set()
    for d in range(1, 15):
        date = f"2026-07-{d:02d}"
        offers = E.offer_locations(p, date)
        assert offers == E.offer_locations(p, date)    # stable within a day
        assert 1 <= len(offers) <= 4
        assert len(set(offers)) == len(offers)         # no duplicates
        # every offer is genuinely unlocked, and exactly one dragon lair rides along
        assert all(E.level(p) >= D.LOCATIONS[k]["min_level"] for k in offers)
        assert sum(1 for k in offers if D.LOCATIONS[k].get("dragon_lair")) == 1
        assert not any(D.LOCATIONS[k].get("alduin") or D.LOCATIONS[k].get("soulcairn")
                       for k in offers)
        seen.add(tuple(offers))
    assert len(seen) > 1                               # the roads actually change
    # a fresh character still gets the gentle fixed openers
    q = _profile()
    assert E.offer_locations(q, "2026-07-01") == E.offer_locations(q, "2026-07-02")


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
        boosted = E.fight_pct(p, "bandit", "blade")
        E.weather_today = lambda date_str=None: {"key": "clear", **D.WEATHERS["clear"]}
        base = E.fight_pct(p, "bandit", "blade")
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
    base = E.fight_pct(p, "bandit", "blade", d)
    E.random = _fixed_rolls(0.0)
    try:
        d.act_sneak(p)
    finally:
        _restore_random()
    assert d.ambush and d.idx == 0              # hidden: the room is not passed yet
    assert E.fight_pct(p, "bandit", "blade", d) == min(E.ROLL_MAX, base + E.AMBUSH_BONUS)
    d.act_slip(p)
    assert d.idx == 1 and d.satchel == 0        # slipping past takes no loot
    assert p["stats"]["sneaks"] == 1 and not d.ambush

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
    assert E.fight_pct(p, "dragon", "blade", d2) > E.fight_pct(p, "dragon", "blade")


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


def test_ambush_attack_and_blown_ambush():
    p = _profile("thief")
    d = _enemy_room_delve(p, "bandit")
    d.ambush = True
    E.random = _fixed_rolls(0.0, 0.99)             # hit lands, no crit
    try:
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.idx == 1 and d.kills == 1 and not d.ambush

    d2 = _enemy_room_delve(p, "bandit")
    d2.ambush = True
    E.random = _fixed_rolls(0.999)                 # strike misses: ambush blown
    try:
        d2.act_attack(p)
    finally:
        _restore_random()
    assert d2.engaged and not d2.ambush


def test_low_hp_warning_consumes_first_click():
    p = _profile()
    p["potions"] = 1
    d = _enemy_room_delve(p, "bandit")
    d.hearts = 1
    hp_before = d.enemy_hp
    d.act_attack(p)                                # consumed by the warning: no roll
    assert d.hp_warned and d.hearts == 1 and d.enemy_hp == hp_before and d.playing()
    assert any("One heart left" in l for l in d.log)
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d.act_attack(p)                            # second click really attacks
    finally:
        _restore_random()
    assert d.idx == 1
    # no potions = no warning: the choice doesn't exist
    p2 = E.create_profile(2, "Potionless", "warrior")
    p2["potions"] = 0
    d2 = _enemy_room_delve(p2, "bandit")
    d2.hearts = 1
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d2.act_attack(p2)
    finally:
        _restore_random()
    assert d2.idx == 1                             # went straight through


def test_locked_chest_and_trap_eye():
    p = _profile()
    rooms = [{"kind": "event", "key": "chest", "boss": False, "resolved": False, "locked": True},
             {"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "embershard", rooms, hearts=3, shout_charges=0)
    d.act_event(p, "open")                         # locked: "open" must do nothing
    assert d.idx == 0 and d.satchel == 0
    lock_before = p["skills"]["lockpicking"]
    E.random = _fixed_rolls(0.0)                   # the pick succeeds
    try:
        d.act_event(p, "pick")
    finally:
        _restore_random()
    assert d.idx == 1 and d.satchel >= 80          # double-loot floor
    assert p["skills"]["lockpicking"] > lock_before
    # a practised eye makes ordinary chests safer
    novice, master = dict(p), dict(p)
    novice["skills"] = dict(p["skills"]); master["skills"] = dict(p["skills"])
    novice["skills"]["lockpicking"] = 15
    master["skills"]["lockpicking"] = 100
    assert E.chest_trap_chance(master) < E.chest_trap_chance(novice)


def test_armour_styles():
    p = _profile()
    p["armour_tier"] = 3
    heavy_soak = E.soak_pct(p)
    sneak_heavy = E.sneak_pct(p, "bandit")
    assert E.toggle_armour_style(p) == "light"
    assert E.soak_pct(p) < heavy_soak
    assert E.sneak_pct(p, "bandit") == min(E.ROLL_MAX, sneak_heavy + D.LIGHT_SNEAK_BONUS)
    assert E.toggle_armour_style(p) == "heavy"
    assert E.soak_pct(p) == heavy_soak


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


# ---------------------------------------------------------------------------
# Expansion: Overkill, affixes, named dragons, shouts, doctrines, crafting, endgame
# ---------------------------------------------------------------------------
def test_overkill_converts_surplus_to_crit():
    p = _profile("warrior")
    for st in D.STYLES:
        p["skills"][st] = 100
    p["weapon_tier"] = 6
    # a maxed warrior vs a weak foe blows past the cap -> real crit bonus, clamped
    assert E.fight_pct(p, "skeever", "blade") == E.ROLL_MAX
    over = E.overkill_crit(p, "skeever", "blade")
    assert 0 < over <= E.OVERKILL_CRIT_CAP / 100.0
    assert E.crit_chance(p, "skeever", "blade") > E.CRIT_CHANCE
    # a fresh character at the low end gets no overkill
    assert E.overkill_crit(_profile(), "troll", "destruction") == 0.0


def test_named_dragon_weekly_and_deltas():
    d1 = E.dragon_of_the_week("2026-07-08")
    assert d1 in D.DRAGON_ROSTER
    assert E.dragon_of_the_week("2026-07-08") == d1          # deterministic within a week
    p = _profile()
    rooms = [{"kind": "enemy", "key": "dragon", "boss": True, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "ancients_ascent", rooms, hearts=5,
                shout_charges=0, dragon="odahviing")
    assert d.enemy_hp == D.ENEMIES["dragon"]["hp"] + D.DRAGON_ROSTER["odahviing"]["hp"]
    assert E.named_dragon(d)["name"] == "Odahviing"


def test_skyfire_airborne_penalty():
    p = _profile()
    for st in D.STYLES:
        p["skills"][st] = 60
    rooms = [{"kind": "enemy", "key": "dragon", "boss": True, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "ancients_ascent", rooms, hearts=5, shout_charges=1)
    air_blade = E.fight_pct(p, "dragon", "blade", d)
    air_bow = E.fight_pct(p, "dragon", "marksman", d)
    assert air_bow > air_blade                              # bow is the sky weapon
    d.grounded = True
    assert E.fight_pct(p, "dragon", "blade", d) > air_blade  # grounding rewards melee


def test_shout_loadout_costs():
    p = _profile()
    p["words"] = 3
    # FUS RO DAH deals 2 true damage to a dragon and grounds it
    rooms = [{"kind": "enemy", "key": "dragon", "boss": True, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "ancients_ascent", rooms, hearts=5, shout_charges=3)
    hp = d.enemy_hp
    d.act_shout(p, 3)
    assert d.enemy_hp == hp - 2 and d.grounded and d.shout_charges == 0
    # FUS (1) grounds without wasting the whole pool
    d2 = E.Delve(p["user_id"], "T", 0, "ancients_ascent",
                 [{"kind": "enemy", "key": "dragon", "boss": True, "resolved": False}],
                 hearts=5, shout_charges=3)
    d2.act_shout(p, 1)
    assert d2.grounded and d2.shout_charges == 2


def test_marked_affix_ward_and_venom():
    p = _profile("mage")
    p["skills"]["destruction"] = 80
    # a Warded foe absorbs the first non-fire hit
    rooms = [{"kind": "enemy", "key": "draugr", "boss": False, "resolved": False, "affix": "warded"},
             {"kind": "enemy", "key": "skeever", "boss": False, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "bleak_falls", rooms, hearts=3, shout_charges=0)
    hp = d.enemy_hp
    E.random = _fixed_rolls(0.0)                    # the blow lands...
    try:
        d.act_attack(p, "blade")                   # ...but the ward eats it
        assert d.enemy_hp == hp and d.room.get("ward_broken")
    finally:
        _restore_random()


def test_doctrines_and_legendary():
    p = _profile()
    for st in E.SKILLS:
        p["skills"][st] = 100
    assert "blade" in E.doctrine_choices_open(p)
    assert E.choose_doctrine(p, "blade", "warmaster") is None
    assert E.choose_doctrine(p, "blade", "warmaster") is not None   # permanent, no re-pick
    # warmaster adds blade attack (feeds overkill at the ceiling)
    q = _profile()
    q["skills"]["blade"] = 100
    base = E._fight_raw(q, "bandit", "blade")
    q["doctrines"] = {"blade": "warmaster"}
    assert E._fight_raw(q, "bandit", "blade") == base + 8
    # legendary resets the skill, keeps the doctrine, banks a star
    assert E.make_legendary(p, "blade") is None
    assert p["skills"]["blade"] == 15 and E.legendary_stars(p) == 1
    assert p["doctrines"]["blade"] == "warmaster"


def test_alchemy_and_tempering():
    p = _profile()
    p["home"] = ["breezehome", "alchemy_lab"]
    p["ingredients"] = {"nightshade": 1, "hagraven_claw": 1}
    assert E.can_brew(p, "fury")
    assert E.brew(p, "fury") is None
    assert p["nextdelve"] == {"fight": 6}          # queued for the next delve
    assert "nightshade" not in p["ingredients"]    # consumed
    # tempering spends septims + materials and raises the grade
    p["septims"] = 1000
    p["ingredients"] = {"bone_meal": 2}
    assert E.temper(p, "weapon") is None
    assert p["temper"]["weapon"] == 1
    assert E.temper_fight_bonus(p) == E.TEMPER_FIGHT_PER_GRADE


def test_soulcairn_gated_and_drains():
    p = _profile()
    assert not E.soulcairn_unlocked(p)             # need Alduin down first
    p["alduin_slain"] = 1
    p["xp"] = 60_000
    assert E.soulcairn_available(p)
    d = E.start_soulcairn(p, 0)
    assert d.kind == "soulcairn" and d.depth == 0
    assert not E.soulcairn_available(p)            # one attempt per day
    d.depth = 5
    base = E._fight_raw(p, "draugr", "blade")      # no delve
    drained = E._fight_raw(p, "draugr", "blade", d)
    assert drained <= base - E.SOULCAIRN_DRAIN * 5


def test_factions_weekly_task():
    p = _profile()
    p["xp"] = 3000                                 # level 8+
    assert E.join_faction(p, "companions") is None
    goal, prog, done = E.faction_progress(p)
    assert prog == 0 and not done
    p["stats"]["kills"] = p["faction"]["snap"] + goal
    assert E.faction_progress(p)[2]                # done
    res = E.claim_faction(p)
    assert res and "favour" in res
    assert E.faction_favour(p) >= 1
    assert "already claimed" in E.claim_faction(p).lower()   # once a week


def test_expedition_roundtrip():
    p = _profile()
    p["xp"] = 3000
    assert E.start_expedition(p, "roads") is None
    assert E.expedition(p)["key"] == "roads"
    assert E.start_expedition(p, "hunt") is not None   # only one at a time
    p["expedition"]["return"] = "2000-01-01"           # force it home
    assert E.expedition_ready(p)
    before = p["septims"]
    msg = E.collect_expedition(p)
    assert msg and p["septims"] > before and p["expedition"] is None
    # the ledger: last returns recorded, capped at 3, totals accumulate
    assert len(p["exp_log"]) == 1 and p["exp_log"][0]["key"] == "roads"
    assert p["exp_totals"]["count"] == 1 and p["exp_totals"]["septims"] > 0
    for _ in range(4):
        E.start_expedition(p, "hunt")
        p["expedition"]["return"] = "2000-01-01"
        E.collect_expedition(p)
    assert len(p["exp_log"]) == 3                      # only the latest three kept
    assert all(entry["key"] == "hunt" for entry in p["exp_log"])
    assert p["exp_totals"]["count"] == 5               # ...but the tally never forgets


def test_expedition_log_dispatches_and_window():
    p = _profile()
    p["xp"] = 3000
    assert E.start_expedition(p, "ruin") is None       # 3-day errand
    carl = p["expedition"]["carl"]
    # a finished trip has its full schedule: 5-7 dispatches per day, 3 days
    p["expedition"]["start"] = "2000-01-01"
    full = E.expedition_log(p, limit=0)
    assert 15 <= len(full) <= 21
    assert full == E.expedition_log(p, limit=0)        # deterministic between opens
    # entries are timestamped, in day order, and the carl features by name
    assert all(l.startswith("Day ") and " · " in l and " - " in l for l in full)
    days = [int(l.split(" ")[1]) for l in full]
    assert days == sorted(days) and days[0] == 1 and days[-1] == 3
    assert any(carl in l for l in full)
    # the default window shows only the latest 10
    assert E.expedition_log(p) == full[-E.EXPEDITION_LOG_SHOW:]
    # a trip started today only shows dispatches whose time has already passed
    p["expedition"]["start"] = datetime.date.today().isoformat()
    today_log = E.expedition_log(p, limit=0)
    assert len(today_log) <= 7 and all(l.startswith("Day 1") for l in today_log)


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
