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
config.SKYRIM_WORLDBOSS_FILE = os.path.join(_TMP, "worldboss.json")

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


def test_streaks_grow_and_forgive():
    p = _profile()
    count, first = E.update_streak(p)
    assert (count, first) == (1, True)
    assert E.update_streak(p) == (1, False)            # same day: no double-dip
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    p["streak"] = {"count": 4, "date": yesterday, "grace": None}
    assert E.update_streak(p) == (5, True)             # consecutive day extends
    # one missed day per week is quietly forgiven...
    two_ago = (datetime.date.today() - datetime.timedelta(days=2)).isoformat()
    p["streak"] = {"count": 6, "date": two_ago, "grace": None}
    assert E.update_streak(p)[0] == 7
    # ...but only once: a second gap the same week resets
    p["streak"]["date"] = two_ago
    assert E.update_streak(p)[0] == 1
    assert E.streak_bonus_pct(3) == 6 and E.streak_bonus_pct(99) == 20
    assert (p.get("records") or {}).get("streak", 0) >= 1


def test_records_and_collection():
    p = _profile()
    assert E.record_best(p, "satchel", 500)
    assert not E.record_best(p, "satchel", 400)        # lesser marks don't overwrite
    assert E.records_of(p)["satchel"] == 500
    # a cleared delve logs the place, the kill logs the beast, brewing logs the recipe
    rooms = [{"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}]
    d = E.Delve(p["user_id"], "T", 0, "embershard", rooms, hearts=3, shout_charges=0)
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d.act_attack(p)
    finally:
        _restore_random()
    book = p["log"]
    assert book["kills"].get("skeever") == 1 and "embershard" in book["clears"]
    assert E.records_of(p)["satchel"] >= 50            # the clear set a real record
    pct = E.collection_pct(p)
    assert 0 < pct < 100
    assert len(E.collection_summary(p)) == 12          # + Wonders
    # backfill: an old cairn best becomes a record on migrate
    q = E.create_profile(7, "Old", "warrior")
    q["soulcairn"] = {"best": 12}
    E.save_profile(q)
    assert E.records_of(E.get_profile(7)).get("depth") == 12


def test_collection_backfills_what_stats_prove():
    q = E.create_profile(8, "Vet", "warrior")
    q["stats"]["dragons"] = 7
    q["stats"]["sweetrolls"] = 3
    q["stats"]["launched"] = 1
    q["alduin_slain"] = 2
    q.pop("log", None)                                 # a pre-collection profile
    E.save_profile(q)
    q = E.get_profile(8)                               # migrate claims the provables
    book = q["log"]
    assert book["kills"]["dragon"] == 7 and book["kills"]["alduin"] == 2
    assert "skuldafn" in book["clears"]
    assert "sweetroll" in book["events"] and "giant" in book["events"]
    # idempotent: a second load neither doubles nor resets...
    E.save_profile(q)
    q = E.get_profile(8)
    assert q["log"]["kills"]["dragon"] == 7
    # ...and live play increments from the seeded count
    E.log_add(q, "kills", "dragon")
    assert q["log"]["kills"]["dragon"] == 8


def test_companions():
    p = _profile()
    found = E.befriend_stray(p)
    assert found in D.COMPANIONS and p["companion"] == found
    # each stray is a NEW friend until the menagerie is full, then None
    for _ in range(6):
        E.befriend_stray(p)
    assert sorted(p["companions"]) == sorted(D.COMPANIONS)
    assert E.befriend_stray(p) is None
    # passives: the fox forages, the crab barters, the raven sharpens, Meeko guards
    p["companion"] = "pincer"
    base = E._septims(dict(p, companion=None), 100)
    assert E._septims(p, 100) > base
    p["companion"] = "corvus"
    assert E.crit_chance(p, "bandit", "blade") >= E.CRIT_CHANCE + 0.02
    p["companion"] = "meeko"
    d = _enemy_room_delve(p, "troll")
    d.engaged = True
    E.random = _fixed_rolls(0.999)                     # attack misses, soak fails
    try:
        hearts = d.hearts
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.hearts == hearts and d.pet_used           # Meeko took it
    assert d.to_dict()["pet_used"] is True


def test_rumours_and_legends():
    p = _profile()
    p["xp"] = 60_000
    p["septims"] = 10_000
    assert E.buy_rumour(p, "ebony_warrior") is None
    assert p["septims"] == 7_500 and E.heard_rumours(p) == ["ebony_warrior"]
    assert E.buy_rumour(p, "ebony_warrior") is not None      # no double-buy
    # legend lairs: pure duels - no forks, corpses or route conditions, never stirred
    d = E.start_delve(p, 0, "last_vigil")
    assert [r["key"] for r in d.rooms] == ["draugr_deathlord", "ebony_warrior"]
    assert d.route is None and d.stirred == 0
    # the Ebony Warrior shrugs off the Thu'um without costing a charge
    d.idx = 1
    d.enemy_hp = d._hp_for(d.room)
    d.shout_charges = 3
    d.act_shout(p, 3)
    assert d.shout_charges == 3 and d.enemy_hp == D.ENEMIES["ebony_warrior"]["hp"]
    # Karstaag gates fire; the twins' second dragon reflights once
    q = _profile("mage")
    assert (E.fight_pct(q, "karstaag", "destruction")
            < E.fight_pct(q, "karstaag", "blade"))
    p["rumours"]["vale_twins"] = "heard"
    d2 = E.start_delve(p, 0, "forgotten_vale")
    d2.idx = 1                                          # Voslaarum, hp 5, reflight at 3
    d2.enemy_hp = 5
    d2.grounded = True
    E.random = _fixed_rolls(0.0, 0.99, 0.99, 0.0, 0.99, 0.99)  # two clean hits (no answer): 5 -> 3
    try:
        d2.act_attack(p)
        d2.act_attack(p)
    finally:
        _restore_random()
    assert d2.enemy_hp == 3 and not d2.grounded         # he takes wing again
    # the killing blow settles the rumour forever
    d2.grounded = True
    d2.enemy_hp = 1
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d2.act_attack(p)
    finally:
        _restore_random()
    assert p["rumours"]["vale_twins"] == "slain"
    assert "vale_twins" in p["log"]["legends"]
    # picker and daily never offer legend lairs uninvited
    assert all(not D.LOCATIONS[k].get("rumour") for k in E.offer_locations(p))


def test_the_pit():
    p = _profile()
    p["xp"] = 60_000
    for s in p["skills"]:
        p["skills"][s] = 100
    p["weapon_tier"] = 6
    assert E.pit_available(p)
    # the bout is INTERACTIVE: begin opens it, each action plays one round
    intro = E.pit_begin(p)
    assert intro and E.pit_bout_active(p) and not E.pit_available(p)
    E.random = _fixed_rolls(0.0)                        # everyone lands everything
    try:
        state1, _ = E.pit_action(p, "strike")           # Snilf 2hp: chip, take a hit
        assert state1 == "playing" and E.pit_bout_active(p)["foe"] == 1
        state2, log = E.pit_action(p, "strike")         # and down he goes
    finally:
        _restore_random()
    assert state2 == "won" and E.pit_bout_active(p) is None
    assert E.pit_state(p)["rank"] == 1
    assert E.pit_available(p)                           # you fight while you WIN...
    assert E.pit_fatigue(p) == E.PIT_FATIGUE_PER_BOUT   # ...but the arms remember
    hurt = E.pit_state(p)["hearts_today"]
    assert hurt == E.heart_max(p) - 1                   # Snilf's round-one hit stays with you
    assert E.pit_begin(p) and E.pit_bout_active(p)["fatigue"] == E.PIT_FATIGUE_PER_BOUT
    assert E.pit_bout_active(p)["me"] == hurt           # wounds carry between bouts
    E.pit_state(p)["bout"] = None                       # walk away from the rematch
    E.pit_state(p)["last"] = "lost"
    assert not E.pit_available(p)                       # a loss ends the day
    assert D.PIT_CHAMPS[0]["name"] in p["log"]["pit"]
    assert E.records_of(p)["pit_rank"] == 1
    assert E.pit_title(1) == D.PIT_TITLES[0]
    # the month turning resets the rank (and any hanging bout), remembers the best
    p["pit"]["season"] = "[1999, 1]"
    p["pit"]["bout"] = {"rank": 0}
    s = E.pit_state(p)
    assert s["rank"] == 0 and s["best"] == 1 and s["bout"] is None and E.pit_available(p)
    # every champion declares a quirk the engine knows how to run
    known = {None, "drunk", "quick", "shieldwall", "butcher", "riposte",
             "veteran", "silent", "reckless", "bear",
             "unyielding", "twin", "blood", "stone", "master"}
    assert all(c.get("quirk") in known and c.get("quirk_desc") for c in D.PIT_CHAMPS)
    assert len(D.PIT_CHAMPS) == len(D.PIT_TITLES) == 15
    # Hjoromir refuses the first killing blow...
    p["pit"] = {"season": str(E._iso_week()), "rank": 10, "date": None, "best": 0}
    E.pit_begin(p)
    b = E.pit_bout_active(p)
    b["foe"] = 1
    E.random = _fixed_rolls(0.0, 0.99, 0.99, 0.99)     # my hit lands; he misses back
    try:
        state, lines = E.pit_action(p, "strike")
    finally:
        _restore_random()
    assert state == "playing" and E.pit_bout_active(p)["foe"] == 1   # back up at 1
    assert any("gets back up" in l for l in lines)
    # ...and the Stone Guest turns power blows into chip damage
    p["pit"] = {"season": str(E._iso_week()), "rank": 13, "date": None, "best": 0}
    E.pit_begin(p)
    hp0 = E.pit_bout_active(p)["foe"]
    E.random = _fixed_rolls(0.0, 0.99, 0.99, 0.99)
    try:
        E.pit_action(p, "power")
    finally:
        _restore_random()
    assert E.pit_bout_active(p)["foe"] == hp0 - 1      # 2-damage swing chips for 1
    # Old Ulfberth shrugs off your first landed blow...
    p["pit"] = {"season": str(E._iso_week()), "rank": 6, "date": None, "best": 0}
    E.pit_begin(p)
    seen = []
    E.random = _fixed_rolls(*([0.0, 0.99] * 12))       # I always hit, he always misses
    try:
        state = "playing"
        while state == "playing":
            state, lines = E.pit_action(p, "strike")
            seen += lines
    finally:
        _restore_random()
    assert state == "won" and any("Forty years" in l for l in seen)
    # ...the bear's hits crush when you swing openly, but a set guard can't be crushed
    p["pit"] = {"season": str(E._iso_week()), "rank": 9, "date": None, "best": 0}
    E.pit_begin(p)
    hearts = E.pit_bout_active(p)["me"]
    E.random = _fixed_rolls(0.5, 0.0, 0.5)             # not distracted; its hit lands
    try:
        _state, lines = E.pit_action(p, "guard")
    finally:
        _restore_random()
    assert E.pit_bout_active(p)["me"] == hearts - 1    # guarded: one heart, not two
    assert not any("crushing" in l for l in lines)
    E.random = _fixed_rolls(0.99, 0.5, 0.0)            # I miss; not distracted; it lands
    try:
        state, lines = E.pit_action(p, "strike")
    finally:
        _restore_random()
    assert any("crushing" in l for l in lines)         # open swing: crushed for two...
    assert state == "lost" and E.pit_bout_active(p) is None   # ...and counted out
    assert E.pit_state(p)["rank"] == 9                 # no rank lost on a loss


def test_daily_moods():
    # deterministic per date, and every mood turns up across a couple of months
    assert E.daily_mood("2026-07-15") == E.daily_mood("2026-07-15")
    seen = {E.daily_mood(f"2026-{m:02d}-{d:02d}") for m in (6, 7, 8) for d in range(1, 29)}
    assert seen == set(D.DAILY_MOODS)
    # the mood reshapes the dungeon: extra_rooms grows and shrinks the fill
    base = E.build_rooms("bleak_falls", rng=random.Random(3))
    long = E.build_rooms("bleak_falls", rng=random.Random(3), extra_rooms=3)
    short = E.build_rooms("bleak_falls", rng=random.Random(3), extra_rooms=-2)
    count = lambda rooms: sum(1 for r in rooms if r["kind"] == "enemy" and not r["boss"])
    assert count(long) == count(base) + 3
    assert count(short) < count(base)
    # a NIGHTMARE daily stirs everyone and pays accordingly
    real = E.daily_mood
    try:
        E.daily_mood = lambda date_str=None: "nightmare"
        q = E.create_profile(21, "Doomed", "warrior")
        d = E.start_delve(q, 0, None, kind="daily")
        assert d.mood == "nightmare" and d.stirred == 5
        assert d.to_dict()["mood"] == "nightmare"
    finally:
        E.daily_mood = real
    # the mood's clear multiplier applies on top of the daily bonus
    q2 = E.create_profile(22, "Paid", "warrior")
    rooms = [{"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}]
    d2 = E.Delve(q2["user_id"], "P", 0, "embershard", rooms, hearts=3, shout_charges=0,
                 daily=True, mood="quiet")
    E.random = _fixed_rolls(0.0, 0.99)
    try:
        d2.act_attack(q2)
    finally:
        _restore_random()
    expected = int(int(D.LOCATIONS["embershard"]["clear_septims"])
                   * E.DAILY_CLEAR_MULT * D.DAILY_MOODS["quiet"]["clear_mult"])
    assert d2.state == "cleared" and abs(d2.satchel - expected) <= expected  # scaled down


def test_faction_news_and_members():
    news = E.faction_news()
    assert news == E.faction_news()                    # stable within the week
    assert 3 <= len(news) <= 4
    assert all(fk in D.FACTIONS and line for fk, line in news)
    a = E.create_profile(31, "Sworn A", "warrior")
    a["xp"] = 3000
    E.join_faction(a, "companions")
    E.save_profile(a)
    b = E.create_profile(32, "Sworn B", "thief")
    b["xp"] = 3000
    E.join_faction(b, "thieves")
    E.save_profile(b)
    members = E.faction_members(E.all_profiles())
    names = [m[1] for m in members]
    assert "Sworn A" in names and "Sworn B" in names
    row = next(m for m in members if m[1] == "Sworn A")
    assert row[0] == "companions" and row[5] == D.FACTIONS["companions"]["goal"]


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


def test_bosses_answer_your_blows():
    p = _profile()
    # a landed non-lethal hit on a tier-4+ boss can draw an immediate answer
    d = _enemy_room_delve(p, "draugr_deathlord", boss=True, extra_rooms=1)
    assert d.enemy_hp == 2
    E.random = _fixed_rolls(0.0, 0.99, 0.0, 0.999)     # hit, no crit, ANSWER fires, soak fails
    try:
        hearts = d.hearts
        d.act_attack(p)
    finally:
        _restore_random()
    assert d.enemy_hp == 1
    assert d.hearts == hearts - 1                      # the answer is a jab, never crushing
    assert any("answers" in l for l in d.log)
    # tier-3 multi-hp foes (a Quickened wolf, a Mimic) never answer
    q = _profile()
    d2 = _enemy_room_delve(q, "mimic", boss=False, extra_rooms=1)
    d2.enemy_hp = 2
    E.random = _fixed_rolls(0.0, 0.99, 0.0, 0.0)       # would-be answer rolls go unused
    try:
        hearts = d2.hearts
        d2.act_attack(q)
    finally:
        _restore_random()
    assert d2.hearts == hearts
    # and Skuldafn is a sealed set-piece: two rooms, no corpses, no forks
    for _ in range(60):
        rooms = E.build_rooms("skuldafn")
        assert [r["key"] for r in rooms] == ["draugr_deathlord", "alduin"]


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
    E.random = _fixed_rolls(0.0, 0.99, 0.99, 0.0, 0.99, 0.99, 0.0, 0.99)
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
    E.random = _fixed_rolls(0.0, 0.99, 0.99, 0.0, 0.99, 0.99)   # two clean hits (no answer): 8 -> 6
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
    assert E.choose_doctrine(p, "blade", "bulwark") is not None     # the other needs a reset first
    assert "blade" not in E.doctrine_choices_open(p)
    # warmaster adds blade attack (feeds overkill at the ceiling); legacy saves stored
    # the pick as a bare string, which still has to read
    q = _profile()
    q["skills"]["blade"] = 100
    base = E._fight_raw(q, "bandit", "blade")
    q["doctrines"] = {"blade": "warmaster"}
    assert E._fight_raw(q, "bandit", "blade") == base + 8
    # legendary resets the skill, keeps the doctrine, banks a star
    assert E.make_legendary(p, "blade") is None
    assert p["skills"]["blade"] == 15 and E.legendary_stars(p) == 1
    assert p["doctrines"]["blade"] == ["warmaster"]
    assert "blade" not in E.doctrine_choices_open(p)   # not until it is back at 100
    # carry it back to 100 and the OTHER doctrine unlocks - the prestige payout
    p["skills"]["blade"] = 100
    assert "blade" in E.doctrine_choices_open(p)
    assert E.doctrine_options_open(p, "blade") == ["bulwark"]
    soak_before = E.soak_pct(p)
    assert E.choose_doctrine(p, "blade", "bulwark") is None
    assert p["doctrines"]["blade"] == ["warmaster", "bulwark"]
    assert E.soak_pct(p) > soak_before                       # both are live at once
    assert E._fight_raw(p, "bandit", "blade") >= base + 8
    # a third mastery of the same skill has nothing left to give
    assert E.make_legendary(p, "blade") is None
    p["skills"]["blade"] = 100
    assert "blade" not in E.doctrine_choices_open(p)


def test_alchemy_and_tempering():
    p = _profile()
    p["home"] = ["breezehome", "alchemy_lab"]
    p["ingredients"] = {"nightshade": 1, "hagraven_claw": 1}
    assert E.can_brew(p, "fury")
    assert E.brew(p, "fury") is None
    assert E.elixir_stock(p) == {"fury": 1}        # brews STOCKPILE on the shelf
    assert "nightshade" not in p["ingredients"]    # consumed
    p["ingredients"] = {"nightshade": 1, "hagraven_claw": 1}
    assert E.brew(p, "fury") is None
    assert E.elixir_stock(p) == {"fury": 2}        # multiples stack
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


def test_wonders_chase_drops():
    p = _profile()
    # a forced hit picks an UNOWNED wonder gated to the roll's sources
    E.random = _fixed_rolls(0.0)
    try:
        found = E.roll_wonder(p, {"room"}, E.WONDER_ROOM_CHANCE)
        assert found in D.WONDERS and "room" in D.WONDERS[found]["sources"]
        assert p["wonders"] == [found]
        # no duplicates: the pool shrinks to the remaining room-locked trophies
        room_pool = {k for k, w in D.WONDERS.items() if "room" in w["sources"]}
        for _ in range(len(room_pool) - 1):
            nxt = E.roll_wonder(p, {"room"}, 1.0)
            assert nxt in room_pool and p["wonders"].count(nxt) == 1
        assert E.roll_wonder(p, {"room"}, 1.0) is None      # the shelf is full
        # boss rolls chase the boss pool; dragon kills the dragon pool
        found_boss = E.roll_wonder(p, {"room", "boss"}, 1.0)
        assert found_boss and "boss" in D.WONDERS[found_boss]["sources"]
    finally:
        _restore_random()
    # a miss banks nothing
    E.random = _fixed_rolls(0.99)
    try:
        assert E.roll_wonder(p, {"dragon"}, E.WONDER_BOSS_CHANCE) is None
    finally:
        _restore_random()
    # the collection log gained a Wonders category counting the shelf
    rows = {label: (done, total) for _e, label, done, total, _m in E.collection_summary(p)}
    assert rows["Wonders"][1] == len(D.WONDERS)
    assert rows["Wonders"][0] == len(p["wonders"])
    # a kill actually rolls it: force the wonder roll to hit on a fresh profile
    p2 = E.create_profile(2, "Chaser", "warrior")
    d = _enemy_room_delve(p2, "bandit")
    E.random = _fixed_rolls(0.0)                            # hit, crit, ...all zeros hit
    try:
        d.act_attack(p2, "blade")
    finally:
        _restore_random()
    assert p2["wonders"], "a kill with a lucky roll should bank a wonder"
    assert any("A WONDER" in l for l in d.log)


def test_weekly_task_board():
    # the pinned 8: deterministic per week, 3 easy / 3 medium / 2 hard
    week = E.weekly_tasks("2026-07-20")
    assert week == E.weekly_tasks("2026-07-22")        # same all week
    assert week != E.weekly_tasks("2026-07-27")        # fresh on Monday
    bands = [D.TASKS[k]["band"] for k in E.weekly_tasks()]
    assert bands.count("easy") == 3 and bands.count("medium") == 3 and bands.count("hard") == 2
    # every authored task kind has a matcher the engine actually emits
    assert {t["kind"] for t in D.TASKS.values()} <= {
        "kill", "clear", "chest", "daily", "sneak", "persuade", "pit_win", "march"}
    p = _profile()
    # events count against matching tasks and cap at n
    for _ in range(50):
        E.task_event(p, "kill", style="blade", bounty=False, dragon=False, potions_used=0)
    ts = E.task_state(p)
    for key in E.weekly_tasks():
        t = D.TASKS[key]
        if t["kind"] == "kill" and not t.get("bounty") and not t.get("dragon") \
                and t.get("style") in (None, "blade"):
            assert ts["prog"][key] == t["n"]           # capped, complete
        elif t["kind"] == "kill" and t.get("style") not in (None, "blade"):
            assert ts["prog"].get(key, 0) == 0         # wrong style never counts
    # choice-filtered clears: a potion-free, blade-pure, deep, stirred clear
    E.task_event(p, "clear", diff="Hard", potions_used=0, styles=["blade"],
                 stirred=3, deep=True)
    rows = {k: (done, comp) for k, _t, done, comp, _c in E.task_progress(p)}
    for key in E.weekly_tasks():
        t = D.TASKS[key]
        if t["kind"] == "clear" and t.get("style_only") in (None, "blade"):
            assert rows[key][0] >= 1
    # ...but a drinking clear fails the dry task
    p2 = E.create_profile(3, "Thirsty", "warrior")
    E.task_event(p2, "clear", diff="Easy", potions_used=2, styles=["blade"], stirred=0, deep=False)
    for key, t, done, _comp, _c in E.task_progress(p2):
        if t.get("no_potion"):
            assert done == 0
    # claiming pays once; completed points show in the weekly race
    pts, total = E.task_points(p)
    assert 0 < pts <= total
    before = p["septims"]
    res = E.claim_tasks(p)
    assert res and p["septims"] > before
    assert E.claim_tasks(p) is None                    # nothing left unclaimed
    E.save_profile(p)
    assert any(name == "Tester" for name, _pts in E.task_leaders(E.all_profiles()))
    # a real delve emits the hooks end to end (kill + clear)
    p3 = E.create_profile(4, "Doer", "warrior")
    rooms = [{"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}]
    d = E.Delve(p3["user_id"], "T", 0, "embershard", rooms, hearts=3, shout_charges=0)
    E.random = _fixed_rolls(0.5, 0.99)                 # hit (roll 50 < pct), no crit
    try:
        d.act_attack(p3, "blade")
    finally:
        _restore_random()
    assert d.state == "cleared"
    prog = E.task_state(p3)["prog"]
    assert any(D.TASKS[k]["kind"] == "kill" for k in prog) or \
           any(D.TASKS[k]["kind"] == "clear" for k in prog)


def _wipe_profiles():
    """A hermetic world for tests whose maths read EVERY profile (the hunt's
    actives head-count) - other tests leak saved characters into the store, and
    the stdlib runner orders tests alphabetically, not by definition."""
    E.save_json_file(config.SKYRIM_PROFILES_FILE, {})


def test_the_weeks_hunt():
    _wipe_profiles()
    E._wb_save({})                                     # force a fresh spawn
    # a fresh week posts a full-pool wave-1 boss from the roster
    store = E.world_boss()
    assert store["boss"] in D.WORLD_BOSSES
    # nobody has delved in this fresh test world, so the pool sits at the floor
    assert store["hp"] == store["max"] == store["base"] == E.WB_MIN_HP
    assert store["wave"] == 1 and store["kills"] == 0 and store["actives"] == 0
    # novices don't march; a proven blade does, once per day
    p = _profile()
    assert not E.wb_available(p)
    p["xp"] = 2000                                     # comfortably past level 5
    assert E.wb_available(p)
    # a march of clean, uncrit hits, every answer slipped: exactly 6 off the pool
    E.random = _fixed_rolls(*([0.2, 0.9, 0.99] * 6 + [0.9]))
    try:
        lines, dealt, slain, store = E.wb_march(p)
    finally:
        _restore_random()
    assert dealt == 6 and not slain and store["hp"] == store["max"] - 6
    assert E.wb_marched_today(p, store) and not E.wb_available(p)
    assert any("the pool stands at" in l for l in lines)
    # the killing blow: a second striker finishes a 2-heart remnant with one crit -
    # and a GREATER wave rises in the fallen one's place, same week
    store["hp"] = 2
    E._wb_save(store)
    wave1_boss = store["boss"]
    q = E.create_profile(9, "Finisher", "warrior")
    q["xp"] = 2000
    E.random = _fixed_rolls(0.0, 0.0, 0.0, 0.9)        # hit, crit, (wonder), padding
    try:
        w_lines, dealt, slain, store = E.wb_march(q)
    finally:
        _restore_random()
    assert slain and store["kills"] == 1 and store["wave"] == 2
    assert store["boss"] != wave1_boss                 # a NEW terror answers
    expected = int(round(E.WB_MIN_HP * E.WB_WAVE_GROWTH))
    assert store["hp"] == store["max"] == expected     # 1.2x the wave-1 base
    assert any("rises in its place" in l for l in w_lines)
    # strikes carry across waves; the fresh wave is immediately marchable tomorrow
    assert str(p["user_id"]) in store["strikes"]
    # everyone who marched holds a share; the killer's carries the head-price
    assert E.wb_share_waiting(p) and E.wb_share_waiting(q)
    assert store["shares"][str(q["user_id"])]["septims"] > \
           store["shares"][str(p["user_id"])]["septims"] - 1  # same days, +400 head
    before = p["septims"]
    res = E.wb_claim(p)
    assert res and p["septims"] > before
    assert E.wb_claim(p) is None                       # a share pays once
    # a second kill ACCUMULATES fresh shares (wave 2 pays a 1.25x premium)
    store = E.world_boss()
    store["hp"] = 1
    E._wb_save(store)
    E.random = _fixed_rolls(0.0, 0.0, 0.0, 0.9)
    try:
        _l, _d, slain2, store = E.wb_march(q)
    finally:
        _restore_random()
    assert slain2 and store["kills"] == 2 and store["wave"] == 3
    assert E.wb_share_waiting(p)                       # a new share after claiming
    # Monday resets the whole ladder; the boss never repeats back-to-back
    old_boss = store["boss"]
    store["week"] = "2020-1"
    E._wb_save(store)
    nxt = E.world_boss()
    # the new pool is sized against last week's 2 marchers (the truer head-count)
    assert nxt["max"] == max(E.WB_MIN_HP, E.WB_HP_PER_ACTIVE * 2)
    assert nxt["wave"] == 1 and nxt["kills"] == 0 and nxt["actives"] == 2
    # the closed week is kept for the notice board, kills and all
    lw = nxt["last_week"]
    assert lw["boss"] == old_boss and lw["kills"] == 2 and lw["marchers"] == 2
    assert lw["top"]["damage"] >= 6
    assert nxt["boss"] != old_boss


def test_hunt_pool_scales_with_active_hunters():
    _wipe_profiles()
    for i in range(4):
        p = E.create_profile(800 + i, f"Active{i}", "warrior")
        p["last_delve_date"] = E._today_str()
        E.save_profile(p)
    idle = E.create_profile(899, "Idle", "warrior")
    idle["last_delve_date"] = "2020-01-01"
    E.save_profile(idle)
    E._wb_save({})                                     # force a fresh spawn
    store = E.world_boss()
    assert store["actives"] == 4
    assert store["hp"] == store["max"] == E.WB_HP_PER_ACTIVE * 4


def test_ghost_duels():
    p = _profile()
    rival = E.create_profile(11, "Rival", "mage")
    rival["xp"] = 5000
    rival["weapon_tier"] = 3
    E.save_profile(rival)
    # the ghost is a frozen snapshot of the rival's real numbers, stone-quirked
    g = E.ghost_of(rival)
    assert g["hp"] == E.heart_max(rival) and g["quirk"] == "veteran"
    assert g["fight"] <= E.DUEL_GHOST_FIGHT_CAP
    assert "Rival" in g["name"]
    # rivals list offers everyone else, once per day each
    assert any(int(r["user_id"]) == 11 for r in E.duel_rivals(p))
    intro = E.duel_begin(p, rival)
    assert E.duel_bout_active(p) and any("duelling circle" in l for l in intro)
    assert all(int(r["user_id"]) != 11 for r in E.duel_rivals(p))   # spent for today
    # beat the ghost down: every swing lands, no crits, its answers all miss.
    # The mage ghost WARDS the first landed blow - budget one extra swing.
    b = E.duel_bout_active(p)
    E.random = _fixed_rolls(*([0.2, 0.99] * (g["hp"] + 1) + [0.99]))
    state = "playing"
    try:
        for _ in range(g["hp"] + 1):
            state, lines = E.duel_action(p, "strike")
            if state != "playing":
                break
    finally:
        _restore_random()
    assert state == "won"
    assert p["stats"]["duel_wins"] == 1 and p["duel"] is None
    # both ledgers remember: my h2h, their ghost's tale
    assert p["rivals"]["11"]["w"] == 1
    fresh_rival = E.get_profile(11)
    assert fresh_rival["rivals"][str(p["user_id"])]["l"] == 1
    assert any("fell to Tester" in t for t in fresh_rival["ghost_log"])


def test_homestead_builds_and_yields():
    p = _profile()
    p["septims"] = 50000
    p["ingredients"] = {"blue_flower": 2, "troll_fat": 1, "void_salts": 1, "dragon_scale": 2}
    # the deed is instant; everything else takes real hours
    assert E.start_building(p, "garden") is not None       # needs the hall first
    assert E.start_building(p, "land") is None
    assert E.homestead_built(p, "land")
    assert E.start_building(p, "hall") is None
    hs = E.homestead(p)
    assert hs["building"] == "hall" and E.homestead_hours_left(p) > 0
    assert E.start_building(p, "watchtower") == \
        "Your builders are already at work - one project at a time."
    # time passes: the build finishes on the next open
    hs["done_at"] = "2000-01-01T00:00:00+00:00"
    note = E.homestead_check(p)
    assert note and "Small Hall" in note and E.homestead_built(p, "hall")
    assert E.homestead_check(p) is None                    # idempotent
    # a finished garden accrues a yield per UK day, capped at 3
    assert E.start_building(p, "garden") is None
    hs["done_at"] = "2000-01-01T00:00:00+00:00"
    E.homestead_check(p)
    hs["built"]["garden"] = "2000-01-05"                   # long ago
    assert E.homestead_yield_days(p) == 3                  # capped, never punitive
    pouch_before = sum((p.get("ingredients") or {}).values())
    res = E.collect_homestead(p)
    assert res and sum(p["ingredients"].values()) == pouch_before + 3
    assert E.homestead_yield_days(p) == 0                  # collected through today
    assert E.collect_homestead(p) is None
    # the shrine's standing blessing feeds the combat maths
    hs["built"]["shrine_wing"] = "2000-01-05"
    assert E.set_shrine(p, "warding") is None
    assert E.homestead_bonus(p, "soak") == 2
    base = E.soak_pct(p)
    E.set_shrine(p, "battle")
    assert E.soak_pct(p) == base - 2 and E.homestead_bonus(p, "fight") == 2
    # the quarters open a second expedition slot with a different housecarl
    p["xp"] = 3000
    assert E.expedition_slots(p) == [1]
    hs["built"]["quarters"] = "2000-01-05"
    assert E.expedition_slots(p) == [1, 2]
    assert E.start_expedition(p, "roads") is None          # fills slot 1
    assert E.start_expedition(p, "hunt") is None           # spills into slot 2
    assert E.start_expedition(p, "ruin") == "Both your housecarls are out."
    e1, e2 = E.expedition(p, 1), E.expedition(p, 2)
    assert e1["key"] == "roads" and e2["key"] == "hunt"
    assert e1["carl"] != e2["carl"]
    # each slot collects separately
    e1["return"] = "2000-01-02"
    assert E.expedition_ready(p, 1) and not E.expedition_ready(p, 2)
    res = E.collect_expedition(p, 1)
    assert res and E.expedition(p, 1) is None and E.expedition(p, 2) is not None
    # the great hall stretches the yield cap to 4
    hs["built"]["great_hall"] = "2000-01-05"
    hs["last_collect"] = "2000-01-06"
    assert E.homestead_yield_days(p) == 4


def test_task_attainability_rule():
    """The design rule: per-delve tasks are CHOICE-gated, dice-count tasks are
    cumulative with generous weekly headroom for a once-a-day player (~21 delves,
    ~5 kills and ~0.7 chests per delve, 7 dailies, several Pit bouts)."""
    weekly_capacity = {"kill": 60, "chest": 10, "sneak": 8, "persuade": 8,
                       "daily": 7, "clear": 12, "pit_win": 6, "march": 7}
    for key, t in D.TASKS.items():
        # every target fits inside half the realistic weekly capacity
        cap = weekly_capacity[t["kind"]]
        if t.get("dragon"):
            cap = 7                                        # a lair is OFFERED daily at L8+ -
                                                           # hunting dragons is a choice, not dice
        if t.get("bounty"):
            cap = 5                                        # ~6% of trash rooms, boostable x4
        assert t["n"] * 2 <= cap or t["n"] == 1, \
            f"{key} wants {t['n']} of a ~{cap}/week event - luck-gated"
        # single-shot tasks must be choice-based, not dice-count-based
        if t["n"] == 1:
            assert any(t.get(f) for f in
                       ("diff", "no_potion", "style_only", "stirred_min", "deep",
                        "unwounded", "no_potion_delve", "dragon")), \
                f"{key} is a one-shot task with no skill/choice gate"


def test_legacy_rebirth():
    p = _profile()
    # the gate: retirement N demands Alduin undone N times
    ready, line = E.retire_ready(p)
    assert not ready and "Alduin" in line
    assert E.retire(p, "old_soul") is not None
    p["alduin_slain"] = 1
    ready, _ = E.retire_ready(p)
    assert ready
    # the offer is 3 unowned boons, stable between opens
    offer = E.boon_offer(p)
    assert len(offer) == 3 and offer == E.boon_offer(p)
    assert all(k in D.BOONS for k in offer)
    assert E.retire(p, next(k for k in D.BOONS if k not in offer)) is not None
    # a rich veteran retires: progression resets, the account persists
    p["xp"] = 50000
    p["septims"] = 9999
    p["weapon_tier"] = 5
    p["armour_tier"] = 4
    p["perks"] = {"stalwart": 2}
    p["words"] = 3
    p["souls"] = 7
    p["wonders"] = ["golden_sweetroll"]
    p["stats"]["dragons"] = 30
    E.homestead(p)["built"]["land"] = "2020-01-01"
    boon = offer[0]
    assert E.retire(p, boon) is None
    assert E.level(p) == 1 and p["septims"] == 0 and p["words"] == 0
    assert p["perks"] == {} and p["armour_tier"] == 0
    if boon == "heirloom":
        assert p["weapon_tier"] == 5                       # the blade passes down
    else:
        assert p["weapon_tier"] == 0
    assert p["wonders"] == ["golden_sweetroll"]            # the shelf persists
    assert p["stats"]["dragons"] == 30                     # career deeds persist
    assert "land" in E.homestead(p)["built"]               # the estate persists
    lg = E.legacy(p)
    assert lg["rank"] == 1 and lg["boons"] == [boon]
    ep = lg["epitaphs"][0]
    assert ep["dragons"] == 30 and ep["alduin"] == 1 and ep["boon"] == boon
    # the next retirement needs a SECOND Alduin kill (at a harder Echo)
    ready, _ = E.retire_ready(p)
    assert not ready
    p["alduin_slain"] = 2
    ready, _ = E.retire_ready(p)
    assert ready
    assert boon not in E.boon_offer(p)                     # boons never repeat
    # boons actually bite
    q = E.create_profile(21, "Boonful", "warrior")
    base_hearts = E.heart_max(q)
    base_delves = E.delves_left(q)
    E.legacy(q)["boons"] = ["blooded", "long_stride", "coin_wise", "old_soul"]
    assert E.heart_max(q) == base_hearts + 1
    assert E.delves_left(q) == base_delves + 1
    assert E.shop_price(q, 100) == 90
    q2 = E.create_profile(22, "Plain", "warrior")
    E.random = _fixed_rolls(0.5)
    got_boon, _ = E.add_xp(q, 100)
    got_plain, _ = E.add_xp(q2, 100)
    _restore_random()
    assert got_boon > got_plain                            # Old Soul pays


def test_game_log_queue():
    E.drain_log()                                      # start clean
    # creation, delving and dying all leave audit lines
    p = E.create_profile(31, "Watched", "warrior")
    rooms = [{"kind": "enemy", "key": "skeever", "boss": True, "resolved": False}]
    d = E.Delve(p["user_id"], "Watched", 0, "embershard", rooms, hearts=3, shout_charges=0)
    E.random = _fixed_rolls(0.5, 0.99)                 # hit, no crit -> cleared
    try:
        d.act_attack(p, "blade")
    finally:
        _restore_random()
    lines = E.drain_log()
    joined = "\n".join(lines)
    assert "Watched" in joined and "woke up on the cart" in joined
    assert "cleared" in joined and "Embershard" in joined
    assert E.drain_log() == []                         # draining empties the queue
    # the buffer is bounded even if nothing ever drains (headless runs)
    for i in range(500):
        E.glog(f"line {i}")
    assert len(E.drain_log()) == 200


def test_elixir_shelf_and_loadout():
    p = _profile()
    p["elixirs"] = {"vigor": 2, "fury": 1}
    # the loadout: pick what to drink; unknown/unstocked picks are dropped
    E.select_elixirs(p, ["vigor", "fury", "true_shot", "nonsense"])
    assert p["nextelixirs"] == ["vigor", "fury"]
    d = E.start_delve(p, 0, "embershard")
    # both fired, stacked, and left the shelf; the loadout is spent
    assert d.buffs.get("heart") == 1 and d.buffs.get("fight") == 6
    assert d.hearts == E.heart_max(p) + 1
    assert E.elixir_stock(p) == {"vigor": 1}
    assert p["nextelixirs"] == []
    assert any("elixirs course through you" in l for l in d.log)
    # nothing picked = nothing drunk, shelf untouched
    E.abandon_active(p)
    d2 = E.start_delve(p, 0, "embershard")
    assert d2.buffs.get("heart") is None and E.elixir_stock(p) == {"vigor": 1}
    # a legacy single-queue profile converts to shelf + pre-picked loadout
    q = E.create_profile(41, "Old", "warrior")
    q["nextdelve"] = {"crit": 6}
    E.save_profile(q)
    q = E.get_profile(41)
    assert "nextdelve" not in q
    assert E.elixir_stock(q) == {"true_shot": 1} and q["nextelixirs"] == ["true_shot"]


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


def test_duel_prize_scales_with_level_gap():
    from lib.features.skyrim.engine import duel_prize_mult

    # Peers pay the full purse; brave challenges pay more, capped at +80%.
    assert duel_prize_mult(10, 10) == 1.0
    assert duel_prize_mult(10, 15) == 1.4
    assert duel_prize_mult(5, 40) == 1.8

    # Stomping downward decays to nothing by 15 levels below.
    assert duel_prize_mult(10, 5) == abs(1.0 - 5 / 15.0)
    assert duel_prize_mult(30, 15) == 0.0
    assert duel_prize_mult(30, 1) == 0.0


def test_holding_wings_bonuses_and_banner():
    p = _profile()
    hs = E.homestead(p)
    hs["built"] = {"land": "2000-01-01", "hall": "2000-01-01",
                   "great_hall": "2000-01-01"}
    # the wings grant standing bonuses that stack with the shrine
    base_soak = E.soak_pct(p)
    hs["built"]["armoury"] = "2000-01-02"
    assert E.soak_pct(p) == base_soak + 2
    assert E.homestead_bonus(p, "xp") == 0
    hs["built"]["library"] = "2000-01-02"
    assert E.homestead_bonus(p, "xp") == 0.05
    hs["built"]["observatory"] = "2000-01-02"
    assert E.homestead_bonus(p, "sneak") == 3
    # the war room buys an extra exchange on the hunt march
    assert "war_room" in D.HOMESTEAD and D.HOMESTEAD["war_room"]["requires"] == "armoury"
    # banners are cosmetic, gated on the hall, and shown once chosen
    assert E.set_banner(p, "wolf") is None
    assert E.house_banner(p)["emoji"] == "🐺"
    assert E.set_banner(p, "nonsense") is not None
    fresh = E.create_profile(901, "Hall-less", "warrior")
    assert E.set_banner(fresh, "wolf") is not None


def test_greenhouse_and_cellar_double_yields():
    p = _profile()
    hs = E.homestead(p)
    hs["built"] = {"land": "2000-01-01", "hall": "2000-01-01",
                   "garden": "2000-01-05", "greenhouse": "2000-01-05",
                   "brewery": "2000-01-05", "cellar": "2000-01-05"}
    p["potions"] = 0
    pouch_before = sum((p.get("ingredients") or {}).values())
    res = E.collect_homestead(p)
    assert res
    # 3 capped days, doubled: 6 ingredients; the still brews toward the cap too
    assert sum(p["ingredients"].values()) == pouch_before + 6
    assert p["potions"] > 0
