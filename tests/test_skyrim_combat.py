"""Combat regressions and decision tests; persistence is always in memory."""
import copy
import random
import types
from contextlib import contextmanager

from lib.features.skyrim import data as D, engine as E


@contextmanager
def _world(*rolls):
    fields = ("load_json_file", "save_json_file", "load_persistent_views",
              "save_persistent_views", "weather_today", "random")
    original = {k: getattr(E, k) for k in fields}
    store = {}
    E.load_json_file = lambda path: copy.deepcopy(store.get(path, {}))
    E.save_json_file = lambda path, value: store.__setitem__(path, copy.deepcopy(value))
    E.load_persistent_views = lambda: copy.deepcopy(store.get("views", {}))
    E.save_persistent_views = lambda value: store.__setitem__("views", copy.deepcopy(value))
    E.weather_today = lambda date_str=None: {"key": "clear", **D.WEATHERS["clear"]}
    if rolls:
        seq = list(rolls)
        E.random = types.SimpleNamespace(
            random=lambda: seq.pop(0) if len(seq) > 1 else seq[0],
            Random=random.Random, randint=lambda low, high: low,
            choice=lambda items: items[0], choices=random.choices)
    try:
        yield store
    finally:
        for key, value in original.items():
            setattr(E, key, value)


def _profile():
    return E.create_profile(424242, "Combat tester", "warrior")


def _delve(profile, enemy="bandit", **kwargs):
    return E.Delve(profile["user_id"], profile["name"], 0, "embershard",
                   [{"kind": "enemy", "key": enemy, "boss": False},
                    {"kind": "enemy", "key": "skeever", "boss": False}],
                   hearts=kwargs.pop("hearts", E.heart_max(profile)), **kwargs)


def test_ambush_roll_uses_the_crit_bonus_that_was_displayed():
    with _world(0.0, 0.10, 0.99):
        p = _profile()
        p["skills"]["blade"] = 65
        d = _delve(p, enemy_hp=2, ambush=True)
        assert E.crit_chance(p, "bandit", "blade", d) > 0.10
        d.act_attack(p, "blade")
        assert d.idx == 1 and d.kills == 1


def test_best_style_breaks_capped_ties_with_expected_damage():
    with _world():
        p = _profile()
        p["skills"].update({s: 100 for s in D.STYLES})
        p["weapon_tier"] = 5
        p["perks"] = {"honed_edge": 3}
        d = _delve(p, "draugr")
        assert {E.fight_pct(p, "draugr", s, d) for s in D.STYLES} == {86}
        assert E.best_style(p, "draugr", d) == "destruction"


def test_two_word_shout_records_a_real_defeat_without_weapon_practice():
    with _world(0.99):
        p = _profile()
        p["words"] = 3
        before = dict(p["skills"])
        d = _delve(p, "draugr_deathlord", enemy_hp=2, shout_charges=3)
        d.room["bounty"] = True
        d.act_shout(p, cost=2)
        assert d.idx == 1 and p["stats"]["kills"] == 1
        assert p["log"]["kills"]["draugr_deathlord"] == 1
        assert d.xp_gained == 12 * D.ENEMIES["draugr_deathlord"]["tier"] * 2
        assert p["skills"] == before


def test_two_word_shout_cannot_erase_a_full_health_legend():
    with _world(0.99):
        p = _profile()
        p["words"] = 3
        p.setdefault("rumours", {})["karstaag"] = "heard"
        d = _delve(p, "karstaag", enemy_hp=7, shout_charges=3)
        d.act_shout(p, cost=2)
        assert d.enemy_hp == 5 and d.idx == 0 and d.kills == 0
        assert p["rumours"]["karstaag"] == "heard"
        assert d.shout_charges == 1


def test_nonlethal_hits_and_misses_give_bounded_practice():
    with _world(0.99):
        p = _profile()
        p["potions"] = 0
        d = _delve(p, enemy_hp=100, hearts=100)
        before = p["skills"]["blade"]
        for _ in range(20):
            d.act_attack(p, "blade")
        assert p["skills"]["blade"] == before + 1
        # A landed contribution tops up the same room's learning allowance.
        E.random.random = lambda: 0.0
        d.act_attack(p, "blade")
        learned = p["skills"]["blade"]
        assert learned > before + 1
        for _ in range(5):
            d.act_attack(p, "blade")
        assert p["skills"]["blade"] == learned


def test_guard_answers_a_charge_once_and_persists_the_opening():
    with _world(0.99):
        p = _profile()
        d = _delve(p, "dwarven_centurion", enemy_hp=3)
        d.room["combat"] = {"turn": 0, "intent": "charge"}
        assert E.combat_intent(d)["max_wound"] == 2
        before = (d.hearts, dict(p["skills"]))
        d.act_guard(p)
        assert d.hearts == before[0] and p["skills"] == before[1]
        back = E.Delve.from_dict(copy.deepcopy(d.to_dict()))
        assert back.room["combat"]["opening"]
        state = copy.deepcopy(back.to_dict())
        back.act_guard(p)
        assert back.hearts == state["hearts"]
        assert back.room["combat"] == state["rooms"][0]["combat"]


def test_story_choice_changes_the_later_encounter_and_survives_reload():
    with _world(0.99):
        p = _profile()
        d = _delve(p)
        d.rooms[0] = {"kind": "event", "key": "fork", "story": "captive"}
        d.rooms[1] = {"kind": "enemy", "key": "bandit_chief", "boss": True}
        assert len(E.story_choices(d)) <= 3
        assert "captive" in E.story_text(d).lower()
        d.act_event(p, "story_help")
        assert d.idx == 1 and d.room["story_effect"]["guard"]
        back = E.Delve.from_dict(copy.deepcopy(d.to_dict()))
        hp = back.hearts
        back.act_attack(p, "blade")
        assert back.hearts == hp  # the freed scout catches this one wound
        assert back.room["story_effect"]["guard_used"]


def test_debrief_counts_banked_and_lost_materials_and_resumes():
    with _world():
        p = _profile()
        d = _delve(p)
        d.capture_summary(p)
        p["skills"]["blade"] += 2
        d.satchel = 100
        d.ingredients = {"salt": 2}
        d.engaged = True
        d.act_leave(p)
        assert d.summary["banked_gold"] == 70
        assert d.summary["lost_gold"] == 30
        assert d.summary["banked_ingredients"] == {"salt": 2}
        assert d.summary["skill_gains"]["blade"] == 2
        assert E.Delve.from_dict(copy.deepcopy(d.to_dict())).summary == d.summary
        d2 = _delve(p)
        d2.capture_summary(p)
        d2.satchel, d2.ingredients = 31, {"salt": 1}
        d2._die(p)
        assert d2.summary["lost_gold"] == 31
        assert d2.summary["lost_ingredients"] == {"salt": 1}


def test_each_intent_has_a_mechanical_counter():
    # Fire prevents the troll undoing a nonlethal hit.
    for style, remaining in (("blade", 3), ("destruction", 2)):
        with _world(0.0, 0.99):
            p = _profile()
            d = _delve(p, "troll", enemy_hp=3)
            d.room["soul_hp"] = 2
            assert E.combat_intent(d)["key"] == "regenerate"
            d.act_attack(p, style)
            assert d.enemy_hp == remaining
    # Blade interrupts a spell; Fire can hit harder on the sheet but draws a reply.
    for style, remaining in (("blade", 3), ("destruction", 2)):
        with _world(0.0, 0.99):
            p = _profile()
            d = _delve(p, "the_caller", enemy_hp=3)
            assert E.combat_intent(d)["key"] == "channel"
            d.act_attack(p, style)
            assert d.hearts == remaining
    # An exposed flank changes damage rather than simply relabelling hit odds.
    for style, remaining in (("blade", 2), ("marksman", 1)):
        with _world(0.0, 0.99):
            p = _profile()
            d = _delve(p, enemy_hp=3)
            d.room["combat"] = {"turn": 1}
            assert E.combat_intent(d)["key"] == "exposed"
            d.act_attack(p, style)
            assert d.enemy_hp == remaining


def test_charge_warning_uses_lethal_range_and_respects_a_no_healing_pact():
    with _world(0.99):
        p = _profile()
        d = _delve(p, hearts=2)
        d.room["combat"] = {"intent": "charge"}
        d.act_attack(p)
        assert d.hp_warned and d.hearts == 2 and not d.engaged
        d.act_attack(p)
        assert d.state == "dead"  # the repeated click intentionally accepts the risk
        q = _profile()
        d2 = _delve(q, hearts=2, pacts=["namira"])
        d2.room["combat"] = {"intent": "charge"}
        d2.act_attack(q)
        assert not d2.hp_warned and d2.state == "dead"


def test_practice_cannot_be_farmed_by_reloading_skipping_or_guarding():
    with _world(0.99):
        p = _profile()
        p["potions"] = 0
        d = _delve(p, hearts=100, enemy_hp=100)
        d.act_attack(p, "blade")
        before = dict(p["skills"])
        for _ in range(8):
            d = E.Delve.from_dict(copy.deepcopy(d.to_dict()))
            d.act_attack(p, "blade")
        assert p["skills"] == before
        d.act_guard(p)
        d.act_guard(p)
        assert p["skills"] == before
        d.ambush = True
        d.act_slip(p)
        assert p["skills"] == before
        d.act_leave(p)
        finished = copy.deepcopy(p)
        d.act_attack(p, "blade")
        d.act_sneak(p)
        d.act_persuade(p)
        d.act_shout(p)
        d.act_event(p, "take")
        assert p == finished


def test_practice_budget_is_shared_across_skills_and_credits_contributors():
    with _world(0.0, 0.99):
        p = _profile()
        d = _delve(p, hearts=100, enemy_hp=100)
        before = dict(p["skills"])
        for style in D.STYLES:
            E.random.random = lambda: 0.0
            d.act_attack(p, style)
        gains = {s: p["skills"][s] - before[s] for s in D.STYLES}
        assert gains["blade"] > 0 and gains["marksman"] > 0
        assert sum(gains.values()) <= 6
        assert d.kills == 0  # both contributors learned before anyone landed a kill


def test_story_risk_and_trap_have_visible_later_consequences():
    for story, choice, coin, hp in (("captive", "story_greed", 45, 3),
                                    ("brazier", "story_help", 0, 1),
                                    ("runes", "story_help", 0, 2)):
        with _world():
            p = _profile()
            d = _delve(p)
            d.rooms[0] = {"kind": "event", "key": "fork", "story": story}
            d.rooms[1] = {"kind": "enemy", "key": "draugr_deathlord", "boss": True}
            d.act_event(p, choice)
            assert d.enemy_hp == hp and d.satchel == coin
            if story == "runes":
                assert d.room["combat"]["opening"]
            back = E.Delve.from_dict(copy.deepcopy(d.to_dict()))
            assert back.enemy_hp == hp  # no second application on resume
            assert back.summary["stories"][0]["choice"] == choice


def test_story_deep_branch_is_shared_across_player_rng_states():
    layouts = []
    with _world():
        for seed in (7, 12345):
            p = _profile()
            d = _delve(p)
            d.rooms[0] = {"kind": "event", "key": "fork", "story": "brazier",
                          "route_seed": 0.314159}
            d.rooms[1] = {"kind": "enemy", "key": "bandit_chief", "boss": True}
            random.seed(seed)
            d.act_event(p, "deep")
            layouts.append(copy.deepcopy(d.rooms))
            assert d.took_deep and d.idx == 1 and len(d.rooms) == 4
    assert layouts[0] == layouts[1]


def test_old_boards_get_safe_defaults_and_history_stays_bounded():
    with _world():
        p = _profile()
        old = _delve(p).to_dict()
        old.pop("summary", None)
        old.pop("history", None)
        d = E.Delve.from_dict(old)
        assert d.summary == {} and d.history == []
        for i in range(100):
            d.say(f"outcome {i}")
        assert len(d.history) == 24 and len(d.log) == 3
        before = copy.deepcopy(d.to_dict())
        for _ in range(10):
            E.combat_intent(d)
        assert d.to_dict() == before  # inspection cannot reroll or alter the encounter


def test_large_hits_cannot_skip_a_dragon_reflight_threshold():
    for shout in (False, True):
        with _world(0.0, 0.0, 0.99):
            p = _profile()
            p["words"] = 3
            d = _delve(p, "alduin", enemy_hp=7, grounded=True, shout_charges=3)
            if shout:
                d.act_shout(p, cost=3)
            else:
                d.act_attack(p, "blade")
            assert d.enemy_hp == 5 and not d.grounded  # crossed 6 without landing on it


def test_terminal_summary_and_rewards_are_not_applied_twice():
    with _world():
        p = _profile()
        d = _delve(p)
        d.satchel = 100
        d.ingredients = {"salt": 2}
        d.act_leave(p)
        expected_profile, expected_summary = copy.deepcopy(p), copy.deepcopy(d.summary)
        d.act_leave(p)
        d._die(p)
        assert p == expected_profile and d.summary == expected_summary
