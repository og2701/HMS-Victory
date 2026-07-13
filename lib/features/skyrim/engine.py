"""Skyrim engine - persistent character profiles and the delve state machine.

Standalone from the casino/economy: no UKPence flows anywhere in here. Progression
is septims (in-game only), XP/levels, skills that improve BY USE, gear tiers,
dragon souls and shout words. Discord rendering lives in views.py; this module is
pure logic so the balance sim (scratch/skyrim_balance.py) can drive it headless.

Persistence:
  • Profiles: config.SKYRIM_PROFILES_FILE, keyed by str(user_id). Read-modify-write
    per action on the single event loop (same pattern as HMS Wordle's state file).
  • Active delves: the shared persistent-views file, keyed by message id with
    type="skyrim", so buttons resume across restarts like the other games.

Design rule: XP, gear, souls and potions bank INSTANTLY (progress never rolls
back); only the septims in the delve satchel are at stake - die and they are lost,
flee mid-fight and a third spills. That is the whole risk model.
"""

import datetime
import logging
import random

import pytz

import config
from lib.core.file_operations import (
    load_json_file, save_json_file, load_persistent_views, save_persistent_views,
)
from lib.features.skyrim import data as D

logger = logging.getLogger(__name__)

_UK = pytz.timezone("Europe/London")

ROLL_MIN, ROLL_MAX = 5, 86           # success chances are clamped into this band
SOAK_CAP = 30                        # max % chance armour absorbs a wound
BASE_HEARTS = 3
BASE_POTION_CAP = 2
FLEE_KEEP = 0.7                      # fraction of the satchel kept when fleeing a fight
DRAGON_KILL_XP = 120
GROUNDED_BONUS = 20                  # fight bonus after shouting a dragon out of the sky
# Skyfire - while a dragon is still AIRBORNE (not grounded), your tools matter: a
# bow is the weapon for the sky, blades and staves barely reach it. Grounding it with
# the Voice (or just bringing marksman) is the read. Applied per attack style.
SKYFIRE_AIR = {"blade": -16, "destruction": -9, "marksman": 6}
BLESSING_BONUS = 5                   # fight bonus from praying at a shrine on full hearts
HEAVY_HIT_CHANCE = {4: 0.35, 5: 0.50}   # by enemy tier: chance a wound is a crushing 2-heart blow
FIGHT_SKILL_SCALE = 24               # max % a skill adds at 100 (fight)
SNEAK_SKILL_SCALE = 22               # (sneak)
SPEECH_SKILL_SCALE = 30              # (persuade)
CRIT_CHANCE = 0.08                   # clean-strike chance: double damage, double loot on the kill
# Overkill - odds earned PAST the display cap don't vanish. Every OVERKILL_PER_CRIT
# points of surplus (raw attack % above ROLL_MAX) become +1% crit, up to a cap. This
# is the keystone: it's what stops every attack button reading a flat 86% at endgame,
# so gear, affinity, ambush, grounding and doctrines all keep mattering at the ceiling.
OVERKILL_PER_CRIT = 3
OVERKILL_CRIT_CAP = 14               # max extra crit % from overkill (on top of CRIT_CHANCE)
BOUNTY_CHANCE = 0.06                 # per trash room: a named variant (+1 hp, 3x loot, 2x XP)
DAILY_CLEAR_MULT = 1.5               # the daily delve pays a fatter clear bonus
AMBUSH_BONUS = 20                    # attack bonus when striking from successful stealth
LOCKED_CHEST_CHANCE = 0.25           # chance a chest room is master-locked (Lockpicking territory)
MIMIC_CHANCE = 0.18                  # chance a chest room is secretly a Mimic (it bites)
FORK_CHANCE = 0.45                   # chance a delve offers a branching Fork before the boss
SOULCAIRN_DRAIN = 2                  # attack % the Soul Cairn steals per depth descended
FALLEN_CHANCE = 0.20                 # chance a delve holds a Fallen Adventurer's corpse
PACT_MULT_CAP = 4.0                  # max combined satchel multiplier from stacked pacts
PACT_MIN_LEVEL = 10                  # pacts unlock once the ordinary maps start feeling easy
PACT_ROLL_MAX = 72                   # Boethiah's Proving: the attack ceiling drops to this
SKILLS = ("blade", "marksman", "destruction", "sneak", "speech", "lockpicking")
# Tempering (The Grindstone): gear can be sharpened past its tier with septims +
# looted materials. Grades stack ON TOP of tier, and the fight bonus deliberately
# feeds overkill at the ceiling rather than the clamp.
TEMPER_MAX_GRADE = 5
TEMPER_FIGHT_PER_GRADE = 3           # +% attack per weapon grade
TEMPER_SOAK_PER_GRADE = 2            # +% soak per armour grade


def _today_str() -> str:
    return datetime.datetime.now(_UK).date().isoformat()


# ---------------------------------------------------------------------------
# Weather - one deterministic roll per UK day, identical for everyone. Purely
# reactive: computed when someone looks, never posted on a schedule.
# ---------------------------------------------------------------------------
def weather_today(date_str: str = None) -> dict:
    date_str = date_str or _today_str()
    rng = random.Random(f"skyrim-weather-{date_str}")
    keys = list(D.WEATHERS)
    w = rng.choices(keys, weights=[D.WEATHERS[k]["weight"] for k in keys], k=1)[0]
    return {"key": w, **D.WEATHERS[w]}


def weather_line(w: dict = None) -> str:
    w = w or weather_today()
    return f"{w['emoji']} **{w['name']}** - {w['desc']}"


# ---------------------------------------------------------------------------
# Named Dragon of the Week - one shared, deterministic roster pick per UK week,
# same for everyone, rotating each Monday. Purely reactive, like the weather.
# ---------------------------------------------------------------------------
def dragon_of_the_week(date_str: str = None) -> str:
    date_str = date_str or _today_str()
    y, w, _ = datetime.date.fromisoformat(date_str).isocalendar()
    rng = random.Random(f"skyrim-dragon-{y}-{w}")
    return rng.choice(sorted(D.DRAGON_ROSTER))


def route_condition(loc_key: str, date_str: str = None) -> str | None:
    """The location's route condition for the day (or None for a plain road) -
    deterministic per UK date + location, shared by everyone, like the weather."""
    rng = random.Random(f"skyrim-route-{date_str or _today_str()}-{loc_key}")
    keys = [None] + list(D.ROUTE_CONDITIONS)
    weights = [D.ROUTE_NONE_WEIGHT] + [D.ROUTE_CONDITIONS[k]["weight"] for k in D.ROUTE_CONDITIONS]
    return rng.choices(keys, weights=weights, k=1)[0]


_TYPE_LABEL = {"human": "men", "beast": "beasts", "undead": "undead",
               "monster": "monsters", "construct": "constructs", "dragon": "dragons"}


def ingredient_sources() -> dict:
    """ingredient key -> list of enemy-type labels that drop it (dragons included) -
    the reverse of INGREDIENT_DROPS, for showing players WHERE to hunt things."""
    out = {"dragon_scale": ["dragons"]}
    for etype, drops in D.INGREDIENT_DROPS.items():
        for k in drops:
            out.setdefault(k, [])
            label = _TYPE_LABEL.get(etype, etype)
            if label not in out[k]:
                out[k].append(label)
    return out


def location_drops(loc_key: str, cap: int = 3) -> str:
    """A compact emoji hint of what a location's foes drop, for the picker line.
    The boss's drop leads (a lair's headline is the 🐲 scale, not the trash herbs)."""
    loc = D.LOCATIONS.get(loc_key) or {}
    seen, out = set(), []
    for ekey in [loc.get("boss")] + list(loc.get("pool", {})):
        e = D.ENEMIES.get(ekey) or {}
        drops = (["dragon_scale"] if e.get("type") == "dragon"
                 else D.INGREDIENT_DROPS.get(e.get("type"), []))
        for k in drops:
            if k not in seen:
                seen.add(k)
                out.append(D.INGREDIENTS[k]["emoji"])
    return "".join(out[:cap])


def _voice(profile) -> dict:
    return profile.setdefault("voice", {"charges": int(profile.get("words", 0)),
                                        "date": _today_str()})


def voice_charges(profile) -> int:
    """Persistent shout charges - the Voice no longer refills free at every door.
    It regains one charge at each UK dawn (capped at words known), and absorbing a
    dragon's soul renews it in full. Skuldafn alone grants a full Voice at the gate:
    the Alduin fight is designed as a war over three charges."""
    v = _voice(profile)
    cap = int(profile.get("words", 0))
    today = _today_str()
    if v.get("date") != today:
        try:
            days = max(0, (datetime.date.fromisoformat(today)
                           - datetime.date.fromisoformat(v.get("date", today))).days)
        except ValueError:
            days = 1
        v["charges"] = int(v.get("charges", 0)) + days
        v["date"] = today
    v["charges"] = max(0, min(cap, int(v.get("charges", 0))))
    return v["charges"]


def _sync_voice(profile, delve, charges: int):
    """Mirror a delve's remaining charges back onto the character - except at
    Skuldafn, where the full Voice is the Greybeards' loan, not yours to keep."""
    if getattr(delve, "kind", None) == "alduin":
        return
    v = _voice(profile)
    v["charges"] = max(0, min(int(profile.get("words", 0)), int(charges)))
    v["date"] = _today_str()


def prowess(profile) -> int:
    """The character's rough power: level plus a nod to gear and tempering. What
    the wilds answer to when a location stirs."""
    gear = (profile.get("weapon_tier", 0) + profile.get("armour_tier", 0)
            + (profile.get("temper") or {}).get("weapon", 0)
            + (profile.get("temper") or {}).get("armour", 0))
    return level(profile) + gear // 4


def stirred_rank(profile, loc_key: str) -> int:
    """How Stirred a location runs for this character - BY BAND. Easy maps never
    stir; Medium firms up mildly; Hard and dragon lairs stay genuinely dangerous
    at any power level. Rank grows with prowess above the location's gate."""
    loc = D.LOCATIONS.get(loc_key) or {}
    per, cap = D.STIRRED_BANDS.get(loc.get("difficulty"), (0, 0))
    if not per:
        return 0
    return max(0, min(cap, (prowess(profile) - int(loc.get("min_level", 1))) // per))


def stirred_name(rank: int) -> str:
    return D.STIRRED_RANKS[min(rank, len(D.STIRRED_RANKS)) - 1] if rank > 0 else ""


def pact_mult(delve) -> float:
    """The combined satchel multiplier from this delve's sworn pacts, capped.
    Clavicus prices himself by the company: alone his bargain is nearly free
    (a strong delver never fled anyway), so his cut grows with each other pact
    that makes being trapped genuinely dangerous."""
    pacts = getattr(delve, "pacts", None) or []
    m = 1.0
    for k in pacts:
        p = D.PACTS.get(k, {})
        mult = p.get("mult", 1.0)
        if p.get("per_other"):
            mult += p["per_other"] * (len(pacts) - 1)
        m *= mult
    return min(PACT_MULT_CAP, m)


def swear_pacts(profile, keys: list) -> str | None:
    """Queue pacts for the NEXT normal delve. Returns an error line, or None."""
    if level(profile) < PACT_MIN_LEVEL:
        return f"The Princes don't bargain with the unproven (level {PACT_MIN_LEVEL}+)."
    keys = [k for k in keys if k in D.PACTS]
    profile["nextpacts"] = keys
    return None


def named_dragon(delve) -> dict | None:
    """The roster entry for THIS delve's dragons, if the current foe is a plain
    dragon (Alduin is always himself, never a roster pick)."""
    key = getattr(delve, "dragon", None)
    r = delve.room if delve and delve.rooms else None
    if key and r and r["kind"] == "enemy" and r["key"] == "dragon":
        return D.DRAGON_ROSTER.get(key)
    return None


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------
def _profiles() -> dict:
    return load_json_file(config.SKYRIM_PROFILES_FILE) or {}


def _migrate(profile: dict) -> dict:
    """Upgrade an old class-era profile in place. The fixed classes are gone -
    the old class key becomes the Guardian Stone (keys match), and the single
    class-flavoured weapon skill moves into the matching attack style. Idempotent
    and safe on new profiles."""
    if "stone" not in profile:
        profile["stone"] = profile.pop("class", "warrior")
    skills = profile["skills"]
    if "weapon" in skills:
        style = {"warrior": "blade", "mage": "destruction", "thief": "marksman"}.get(
            profile["stone"], "blade")
        skills[style] = max(skills.get(style, 15), skills.pop("weapon"))
    for s in SKILLS:
        skills.setdefault(s, 15)
    profile.setdefault("armour_style", "heavy")
    # Expansion fields - all default empty so old profiles keep working untouched.
    profile.setdefault("doctrines", {})          # skill -> chosen mastery (Capstone Doctrines)
    profile.setdefault("legendary", {})          # skill -> times made Legendary (prestige)
    profile.setdefault("temper", {"weapon": 0, "armour": 0})   # The Grindstone grades
    profile.setdefault("ingredients", {})        # banked alchemy ingredients
    profile.setdefault("materials", {})          # banked tempering materials
    profile.setdefault("recipes", ["healing"])   # known brewing recipes (start with healing)
    profile.setdefault("nextdelve", {})          # brewed one-delve elixir queued for next delve
    profile.setdefault("nextpacts", [])          # Daedric pacts sworn for the next delve
    profile.setdefault("dragon_wall", [])        # named dragons slain (bestiary)
    profile.setdefault("allegiance", None)       # chosen NPC faction key
    profile.setdefault("faction", {})            # faction -> {rank, favour}
    profile.setdefault("soulcairn", {"best": 0}) # deepest Soul Cairn descent
    profile.setdefault("expedition", None)       # an out-on-a-timer expedition, if any
    profile.setdefault("exp_log", [])            # the last few expedition returns
    profile.setdefault("exp_totals", {"count": 0, "septims": 0, "xp": 0})
    # the persistent Voice - existing characters are grandfathered in at full breath
    profile.setdefault("voice", {"charges": int(profile.get("words", 0)),
                                 "date": _today_str()})
    return profile


def get_profile(user_id) -> dict | None:
    p = _profiles().get(str(user_id))
    if p is None:
        return None
    if "stone" not in p or "weapon" in p.get("skills", {}):
        _migrate(p)
        save_profile(p)          # one-time upgrade persists on first touch
    return _migrate(p)           # cheap idempotent defaults for newer fields


def save_profile(profile: dict):
    store = _profiles()
    store[str(profile["user_id"])] = profile
    save_json_file(config.SKYRIM_PROFILES_FILE, store)


def all_profiles() -> dict:
    return {k: _migrate(v) for k, v in _profiles().items()}


def create_profile(user_id, name: str, stone_key: str) -> dict:
    stone = D.STONES[stone_key]
    skills = {s: 15 for s in SKILLS}
    skills.update(stone["start"])
    profile = {
        "user_id": int(user_id),
        "name": name,
        "stone": stone_key,
        "xp": 0,
        "skills": skills,
        "perks": {},                             # perk key -> ranks taken
        "septims": 0,
        "potions": 2,           # kind start: a full belt for the first delve or two
        "weapon_tier": 0,
        "armour_tier": 0,
        "armour_style": "heavy",                 # heavy (soak) or light (sneak) - free to switch
        "souls": 0,
        "words": 0,                              # shout words known (0..3)
        "stats": {"delves": 0, "clears": 0, "deaths": 0, "kills": 0, "sneaks": 0,
                  "persuades": 0, "dragons": 0, "sweetrolls": 0, "flees": 0,
                  "launched": 0},
        "stamina": {"date": _today_str(), "used": 0},
        "active_delve": None,
        "created": _today_str(),
    }
    save_profile(profile)
    return profile


# --- derived numbers ---------------------------------------------------------
def level(profile) -> int:
    return D.level_from_xp(profile["xp"])


def perk_points(profile) -> int:
    spent = sum(profile["perks"].values())
    return max(0, level(profile) - 1 - spent)


def perk_rank(profile, key) -> int:
    return profile["perks"].get(key, 0)


def heart_max(profile) -> int:
    return BASE_HEARTS + perk_rank(profile, "stalwart") + int(doctrine_flat(profile, "heart"))


def delve_heart_max(delve, profile) -> int:
    """Heart cap for a specific delve - includes a brewed Draught of Vigor's bonus."""
    return heart_max(profile) + (int(delve.buffs.get("heart", 0)) if delve else 0)


def potion_cap(profile) -> int:
    return (BASE_POTION_CAP + perk_rank(profile, "alchemist")
            + int(doctrine_flat(profile, "potion_cap")))


def archetype(profile) -> str:
    """Your build is what you practised. Top-two pair titles first, then the
    single top skill; undeveloped characters are just Adventurers."""
    ranked = sorted(profile["skills"].items(), key=lambda kv: kv[1], reverse=True)
    (s1, v1), (s2, v2) = ranked[0], ranked[1]
    # 35+: a stone-blessed START (30) is not yet a title - titles are practised into
    if v1 >= 35 and v2 >= 35:
        pair = D.ARCHETYPE_PAIRS.get(frozenset((s1, s2)))
        if pair:
            return pair
    if v1 >= 35:
        return D.ARCHETYPE_SINGLE.get(s1, "Adventurer")
    return "Adventurer"


def gear_name(profile, slot: str) -> str:
    """Weapon flavour follows your most-practised attack style."""
    tier = D.GEAR_TIERS[profile[f"{slot}_tier"]]
    if slot == "weapon":
        best = max(D.STYLES, key=lambda s: profile["skills"][s])
        kind = {"blade": "sword", "marksman": "bow", "destruction": "staff"}[best]
    else:
        kind = f"{profile.get('armour_style', 'heavy')} armour"
    return f"{tier['emoji']} {tier['name']} {kind}"


def _skill_component(skill: int, scale: int) -> float:
    return scale * (skill - 15) / 85.0


def _clamp(p: float) -> int:
    return int(max(ROLL_MIN, min(ROLL_MAX, round(p))))


def _fight_raw(profile, enemy_key: str, style: str, delve=None) -> float:
    """The UNCLAMPED attack percentage. fight_pct clamps this for display and the
    hit roll; overkill_crit reads the surplus above the cap so earned odds past the
    ceiling convert to crit instead of silently vanishing."""
    e = D.ENEMIES[enemy_key]
    p = (e["fight"]
         + _skill_component(profile["skills"][style], FIGHT_SKILL_SCALE)
         + D.WEAPON_FIGHT_PER_TIER * profile["weapon_tier"]
         + temper_fight_bonus(profile)
         + doctrine_fight_bonus(profile, e, style)   # a permanent mastery, always applies
         + D.STYLE_AFF[e["type"]][style]
         + 4 * perk_rank(profile, "honed_edge")
         + weather_today()["fight"])
    if delve is not None:
        if e["type"] == "dragon":
            if delve.grounded:
                p += GROUNDED_BONUS
            else:
                p += SKYFIRE_AIR.get(style, 0)     # airborne: bring a bow or ground it
        if delve.blessed:
            p += BLESSING_BONUS
        if delve.ambush:
            p += AMBUSH_BONUS
        p += _affix_fight_delta(profile, enemy_key, style, delve)
        p += (delve.buffs or {}).get("fight", 0)   # a brewed Philtre of Fury
        p -= D.STIRRED_FIGHT_PER_RANK * getattr(delve, "stirred", 0)   # the deep offer bites
        p -= 3 * getattr(delve, "echo", 0)          # an Echoed Skuldafn fights back harder
        if getattr(delve, "kind", None) == "soulcairn":
            p -= SOULCAIRN_DRAIN * delve.depth      # the deep gnaws your odds
        nd = named_dragon(delve)
        if nd:
            p += nd.get("fight", 0)              # this week's dragon is easier/harder to land
    return p


def fight_pct(profile, enemy_key: str, style: str, delve=None) -> int:
    """Success chance for attacking with one of the three styles - the style's
    skill and its matchup against the enemy type both matter. Boethiah's Proving
    lowers the ceiling itself: every swing can miss again."""
    hi = PACT_ROLL_MAX if delve and "boethiah" in getattr(delve, "pacts", []) else ROLL_MAX
    return int(max(ROLL_MIN, min(hi, round(_fight_raw(profile, enemy_key, style, delve)))))


def overkill_crit(profile, enemy_key: str, style: str, delve=None) -> float:
    """Extra crit chance (0..OVERKILL_CRIT_CAP%) earned by attack odds pushed past
    the display cap - the keystone that keeps choices meaningful at the ceiling."""
    surplus = _fight_raw(profile, enemy_key, style, delve) - ROLL_MAX
    if surplus <= 0:
        return 0.0
    return min(OVERKILL_CRIT_CAP, int(surplus // OVERKILL_PER_CRIT)) / 100.0


def crit_chance(profile, enemy_key: str, style: str, delve=None) -> float:
    """The full clean-strike chance for this attack: base crit + overkill surplus +
    any crit-granting doctrines that apply to this foe/style."""
    e = D.ENEMIES[enemy_key]
    buff = (delve.buffs or {}).get("crit", 0) / 100.0 if delve else 0.0
    return (CRIT_CHANCE + overkill_crit(profile, enemy_key, style, delve)
            + doctrine_crit_bonus(profile, e, style) + buff)


# --- hooks filled in by later systems (defined here so _fight_raw always resolves;
#     each reads a profile/room field that _migrate defaults, so they are safe on any
#     profile and simply contribute 0 until that system is in play) --------------------
def temper_fight_bonus(profile) -> float:
    """+% attack from the weapon's tempering grade (The Grindstone)."""
    return TEMPER_FIGHT_PER_GRADE * (profile.get("temper") or {}).get("weapon", 0)


def _doctrines(profile) -> list:
    """The mastery dicts a character has chosen (Capstone Doctrines)."""
    return [D.DOCTRINES[s][c] for s, c in (profile.get("doctrines") or {}).items()
            if c in D.DOCTRINES.get(s, {})]


def doctrine_flat(profile, key: str) -> float:
    """Sum an unconditional numeric doctrine hook (soak, sneak, persuade, heart, ...)."""
    return sum(d.get(key, 0) for d in _doctrines(profile))


def doctrine_loot_mult(profile) -> float:
    m = 1.0
    for d in _doctrines(profile):
        m *= d.get("loot_mult", 1.0)
    return m


def _doc_applies(doc: dict, enemy: dict, style: str) -> bool:
    if doc.get("style") not in (None, style):
        return False
    if doc.get("vs") not in (None, enemy["type"]):
        return False
    if doc.get("vs_any") and enemy["type"] not in doc["vs_any"]:
        return False
    return True


def doctrine_fight_bonus(profile, enemy: dict, style: str) -> float:
    """Attack % from chosen masteries that apply to this foe/style."""
    return sum(d["fight"] for d in _doctrines(profile)
               if d.get("fight") and _doc_applies(d, enemy, style))


def doctrine_crit_bonus(profile, enemy: dict, style: str) -> float:
    return sum(d["crit"] for d in _doctrines(profile)
               if d.get("crit") and _doc_applies(d, enemy, style))


def _affix_fight_delta(profile, enemy_key: str, style: str, delve) -> float:
    """Attack-% swing from the current room's elite affix (Marked Affixes). Affixes
    subtract or gate rather than add, so they survive the clamp."""
    room = getattr(delve, "room", None)
    if not room or not room.get("affix"):
        return 0.0
    aff = D.AFFIXES.get(room["affix"])
    if not aff:
        return 0.0
    delta = 0.0
    if aff.get("all_fight"):
        delta += aff["all_fight"]
    if aff.get("gate_style") == style:      # this style barely works on it
        delta += aff.get("gate_penalty", -40)
    return delta


def best_style(profile, enemy_key: str, delve=None) -> str:
    return max(D.STYLES, key=lambda s: fight_pct(profile, enemy_key, s, delve))


def sneak_pct(profile, enemy_key: str) -> int | None:
    e = D.ENEMIES[enemy_key]
    if e["sneak"] is None:
        return None
    p = (e["sneak"] + 4
         + _skill_component(profile["skills"]["sneak"], SNEAK_SKILL_SCALE)
         + 6 * perk_rank(profile, "muffled")
         + doctrine_flat(profile, "sneak")
         + weather_today()["sneak"])
    if profile.get("armour_style") == "light" and profile["armour_tier"] >= 1:
        p += D.LIGHT_SNEAK_BONUS
    return _clamp(p)


def persuade_pct(profile, enemy_key: str) -> int | None:
    e = D.ENEMIES[enemy_key]
    if e.get("persuade") is None:
        return None
    p = (e["persuade"] + 2
         + _skill_component(profile["skills"]["speech"], SPEECH_SKILL_SCALE)
         + 7 * perk_rank(profile, "persuasive")
         + doctrine_flat(profile, "persuade"))
    return _clamp(p)


def lockpick_pct(profile) -> int:
    return _clamp(35 + _skill_component(profile["skills"]["lockpicking"], 45))


def chest_trap_chance(profile) -> float:
    """A practised eye spots the needle trap before it fires."""
    return max(0.08, 0.25 - 0.17 * (profile["skills"]["lockpicking"] - 15) / 85)


def soak_pct(profile) -> int:
    per_tier = (D.LIGHT_SOAK_PER_TIER if profile.get("armour_style") == "light"
                else D.ARMOUR_SOAK_PER_TIER)
    raw = (per_tier * profile["armour_tier"]
           + 6 * perk_rank(profile, "juggernaut")
           + TEMPER_SOAK_PER_GRADE * (profile.get("temper") or {}).get("armour", 0)
           + int(doctrine_flat(profile, "soak")))
    return min(SOAK_CAP + 15, raw)          # tempering/doctrines can push past the base cap


def _skill_up(profile, which: str) -> int:
    """Improve a skill by use (fast early, slow late). Your Guardian Stone's
    skills learn faster. Returns the gain."""
    cur = profile["skills"][which]
    if cur >= 100:
        return 0
    gain = max(1, (100 - cur) // 25)
    if which in D.STONES[profile["stone"]]["boost"]:
        gain += 1
    profile["skills"][which] = min(100, cur + gain)
    return profile["skills"][which] - cur


def add_xp(profile, amount: int) -> tuple:
    """Bank XP (Quick Study + weather apply). Returns (gained, levels_gained)."""
    amount = int(round(amount * (1 + 0.10 * perk_rank(profile, "quick_study"))
                       * weather_today()["xp"]))
    before = level(profile)
    profile["xp"] += amount
    return amount, level(profile) - before


def _septims(profile, amount: int) -> int:
    """Scale a septim find by Deep Pockets, the day's weather and any Haggler-style
    doctrine multiplier."""
    return int(round(amount * (1 + 0.20 * perk_rank(profile, "deep_pockets"))
                     * weather_today()["loot"] * doctrine_loot_mult(profile)))


# --- stamina -----------------------------------------------------------------
def delves_left(profile) -> int:
    per_day = int(getattr(config, "SKYRIM_DELVES_PER_DAY", 3))
    st = profile.get("stamina") or {}
    if st.get("date") != _today_str():
        return per_day
    return max(0, per_day - int(st.get("used", 0)))


def spend_stamina(profile):
    st = profile.get("stamina") or {}
    if st.get("date") != _today_str():
        st = {"date": _today_str(), "used": 0}
    st["used"] = int(st.get("used", 0)) + 1
    profile["stamina"] = st


# ---------------------------------------------------------------------------
# Delve state persistence (shared persistent-views file, like the games)
# ---------------------------------------------------------------------------
def save_delve(delve: "Delve"):
    if delve.message_id is None or delve.state != "playing":
        return
    views = load_persistent_views()
    views[str(delve.message_id)] = delve.to_dict()
    save_persistent_views(views)


def delete_delve(message_id):
    if message_id is None:
        return
    views = load_persistent_views()
    if str(message_id) in views:
        del views[str(message_id)]
        save_persistent_views(views)


def load_delve(message_id) -> "Delve | None":
    entry = load_persistent_views().get(str(message_id))
    if isinstance(entry, dict) and entry.get("type") == "skyrim":
        try:
            return Delve.from_dict(entry)
        except Exception:
            logger.error("skyrim: malformed delve entry %s", message_id, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Delve generation
# ---------------------------------------------------------------------------
def _draw_events(count: int, rng=random) -> list:
    pool = [(k, v["weight"]) for k, v in D.EVENTS.items() if v["weight"] > 0]
    keys = [k for k, _ in pool]
    weights = [w for _, w in pool]
    return [rng.choices(keys, weights=weights, k=1)[0] for _ in range(count)]


def _affix_chance(char_level: int) -> float:
    for cap, chance in D.AFFIX_CHANCE_BY_LEVEL:
        if char_level <= cap:
            return chance
    return 0.0


def _eligible_affix(enemy_key: str, rng) -> str | None:
    """Any affix that can attach to this enemy's type/tier (ignores the level gate)."""
    e = D.ENEMIES[enemy_key]
    eligible = [k for k, a in D.AFFIXES.items()
                if e["type"] in a["types"] and e["tier"] >= a["min_tier"]]
    return rng.choice(eligible) if eligible else None


def _roll_affix(enemy_key: str, char_level: int, rng) -> str | None:
    """Maybe mark an ordinary enemy with an elite affix eligible for its type/tier.
    Gated on character level so newer players never meet a Dread horror."""
    if rng.random() >= _affix_chance(char_level):
        return None
    return _eligible_affix(enemy_key, rng)


def build_rooms(loc_key: str, rng=None, affix_level: int = 0, route: str = None) -> list:
    """Room list for a fresh delve: shuffled trash + events, optional word wall,
    boss last. Each room: {kind, key, boss, resolved} (+ bounty on rare named
    variants, + affix on rare elite variants). Pass a seeded rng for the shared
    daily layout; affix_level gates elite modifiers (0 = none, e.g. the daily);
    route applies the day's route condition (extra room, forced spawns...)."""
    rng = rng or random
    loc = D.LOCATIONS[loc_key]
    cond = D.ROUTE_CONDITIONS.get(route) or {}
    n_fill = loc["rooms"] - 1 + (1 if cond.get("extra_room") else 0)
    n_events = min(loc["events"], n_fill - 1)      # always at least one trash fight
    bounty_chance = BOUNTY_CHANCE * cond.get("bounty_mult", 1)
    enemy_keys = list(loc["pool"].keys())
    enemy_weights = list(loc["pool"].values())
    rooms = []
    for _ in range(n_fill - n_events):
        room = {"kind": "enemy", "key": rng.choices(enemy_keys, weights=enemy_weights, k=1)[0],
                "boss": False, "resolved": False}
        if rng.random() < bounty_chance:
            room["bounty"] = True                  # a named variant: +1 hp, triple loot
        elif affix_level:                          # bounty OR affix, never both
            aff = _roll_affix(room["key"], affix_level, rng)
            if aff:
                room["affix"] = aff
        rooms.append(room)
    if cond.get("force_affix") and affix_level >= 8:
        plain = [r for r in rooms if not r.get("affix") and not r.get("bounty")]
        if plain:
            target = rng.choice(plain)
            aff = _eligible_affix(target["key"], rng)
            if aff:
                target["affix"] = aff              # the nest's elite, guaranteed
    for k in _draw_events(n_events, rng):
        room = {"kind": "event", "key": k, "boss": False, "resolved": False}
        if k == "chest":
            if rng.random() < MIMIC_CHANCE:
                room["mimic"] = True               # it bites when you open it
            elif rng.random() < LOCKED_CHEST_CHANCE:
                room["locked"] = True              # a master lock: Lockpicking territory
        rooms.append(room)
    if cond.get("force_mudcrab"):
        rooms.append({"kind": "event", "key": "mudcrab", "boss": False, "resolved": False})
    if cond.get("force_fallen") or rng.random() < FALLEN_CHANCE:
        # corpse picking reads the (mutable) graveyard, so it gets a DERIVED rng -
        # exactly one draw from the main stream - or the shared daily layout would
        # drift between players whenever someone died mid-day
        crng = random.Random(rng.random())
        rooms.append({"kind": "event", "key": "fallen", "boss": False, "resolved": False,
                      "corpse": _make_fallen_corpse(loc_key, crng)})
    rng.shuffle(rooms)
    # A Fork before the boss: a genuine risk/reward choice with honest hints.
    if len(rooms) >= 2 and rng.random() < FORK_CHANCE:
        rooms.append({"kind": "event", "key": "fork", "boss": False, "resolved": False})
    if loc.get("word_wall"):
        rooms.append({"kind": "event", "key": "wordwall", "boss": False, "resolved": False})
    rooms.append({"kind": "enemy", "key": loc["boss"], "boss": True, "resolved": False})
    return rooms


def offer_locations(profile, date_str: str = None) -> list:
    """The day's destinations. One pick from each difficulty band of what the
    character has unlocked (easy / mid / deep), rotating deterministically per UK
    day like the weather - so the picker changes each dawn instead of showing the
    same three maps forever. Once dragon lairs are unlocked, one is always offered
    too (soul-hunting is never blocked for a day). Skuldafn and the Soul Cairn are
    never offered here - the picker adds those via their own availability checks."""
    lvl = level(profile)
    dragon_min = int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    rng = random.Random(f"skyrim-offers-{date_str or _today_str()}")
    open_locs = [k for k, v in D.LOCATIONS.items()
                 if not v.get("alduin") and not v.get("soulcairn")
                 and not v.get("dragon_lair") and lvl >= v["min_level"]]
    open_locs.sort(key=lambda k: D.LOCATIONS[k]["min_level"])
    if len(open_locs) <= 3:
        picks = list(open_locs)
    else:
        n = len(open_locs)
        bands = (open_locs[:n // 3], open_locs[n // 3:(2 * n) // 3], open_locs[(2 * n) // 3:])
        picks = [rng.choice(band) for band in bands if band]
    lairs = sorted(k for k, v in D.LOCATIONS.items()
                   if v.get("dragon_lair") and lvl >= dragon_min and lvl >= v["min_level"])
    if lairs:
        picks.append(rng.choice(lairs))
    return picks


def _soulcairn_room(depth: int, rng) -> dict:
    """One floor of the endless descent - tougher pools and richer affixes the deeper
    you go. Each floor's foe also gains hp every few depths."""
    if depth < 3:
        pool = ["draugr", "frostbite_spider", "necromancer"]
    elif depth < 7:
        pool = ["draugr", "necromancer", "troll", "falmer", "draugr_deathlord"]
    else:
        pool = ["draugr_deathlord", "the_caller", "dwarven_centurion", "troll", "hagraven"]
    key = rng.choice(pool)
    room = {"kind": "enemy", "key": key, "boss": False, "resolved": False, "soul": True}
    if depth >= 2 and rng.random() < min(0.65, 0.2 + 0.05 * depth):
        aff = _eligible_affix(key, rng)
        if aff:
            room["affix"] = aff
    room["soul_hp"] = depth // 4                 # +1 hp every 4 depths, on top of base
    return room


def _room_hp(room: dict) -> int:
    """Hits the room's enemy can take: base hp, +1 for a bounty, + any affix hp."""
    if room["kind"] != "enemy":
        return 1
    hp = D.ENEMIES[room["key"]].get("hp", 1) + (1 if room.get("bounty") else 0)
    if room.get("affix"):
        hp += D.AFFIXES.get(room["affix"], {}).get("hp", 0)
    hp += int(room.get("soul_hp", 0))            # Soul Cairn depth scaling
    return hp


# ---------------------------------------------------------------------------
# The Delve
# ---------------------------------------------------------------------------
class Delve:
    """One dungeon run. All rolls happen here; views only render and route."""

    def __init__(self, player_id, player_name, channel_id, location, rooms, *,
                 idx=0, hearts=None, satchel=0, shout_charges=None, engaged=False,
                 spotted=False, grounded=False, blessed=False, state="playing",
                 log=None, message_id=None, xp_gained=0, kills=0, result_line="",
                 delve_id=None, enemy_hp=None, daily=False, fan=False,
                 ambush=False, hp_warned=False, venom=False, ingredients=None,
                 dragon=None, phase=None, depth=0, kind="normal", buffs=None,
                 route=None, pacts=None, stirred=0, echo=0):
        import uuid
        self.delve_id = delve_id or uuid.uuid4().hex[:12]
        self.daily = bool(daily)                  # the shared once-a-day dungeon
        self.kind = kind                          # normal | daily | alduin | soulcairn | expedition
        self.fan = bool(fan)                      # the Adoring Fan absorbs one wound
        self.ambush = bool(ambush)                # hidden and in position to strike
        self.hp_warned = bool(hp_warned)          # the one-heart potion nudge was shown
        self.venom = bool(venom)                  # a Venomous wound waiting to bleed next room
        self.ingredients = dict(ingredients or {})  # at-risk alchemy drops (lost on death)
        self.dragon = dragon                      # named dragon key for this run's dragons
        self.phase = phase                        # Skyfire phase: air | dive | grounded | None
        self.depth = int(depth)                   # Soul Cairn depth reached
        self.buffs = dict(buffs or {})            # brewed one-delve elixir effects
        self.route = route                        # the day's route condition key
        self.pacts = list(pacts or [])            # Daedric pacts sworn for this delve
        self.stirred = int(stirred)               # deep-offer danger rank (0 = plain)
        self.echo = int(echo)                     # Alduin's Echoes: past kills harden him
        self.player_id = int(player_id)
        self.player_name = player_name
        self.channel_id = channel_id
        self.location = location                  # location key
        self.rooms = rooms
        self.idx = int(idx)
        self.hearts = hearts                      # set from profile at start
        self.satchel = int(satchel)
        self.shout_charges = shout_charges
        self.engaged = engaged                    # a fight is on: no sneak/persuade/clean leave
        self.spotted = spotted
        self.grounded = grounded                  # dragon shouted down (+fight)
        self.blessed = blessed                    # shrine blessing (+fight)
        self.state = state                        # playing | cleared | left | fled | dead | launched | abandoned
        self.log = list(log or [])                # recent lines, newest last
        self.message_id = message_id
        self.xp_gained = int(xp_gained)           # display total for the summary
        self.kills = int(kills)
        self.result_line = result_line
        # remaining hits the CURRENT enemy can take (bosses 2, dragons 3+, trash 1,
        # bounty variants +1, affix/named-dragon bonuses on top)
        if enemy_hp is None:
            r = self.rooms[self.idx] if self.rooms else None
            enemy_hp = self._hp_for(r) if r else 1
        self.enemy_hp = int(enemy_hp)
        self.busy = False                         # transient: drop double-clicks

    def _hp_for(self, room) -> int:
        hp = _room_hp(room)
        nd_key = getattr(self, "dragon", None)
        if nd_key and room and room["kind"] == "enemy" and room["key"] == "dragon":
            hp += D.DRAGON_ROSTER.get(nd_key, {}).get("hp", 0)
        if room and room.get("boss") and self.stirred >= 3:
            hp += 1                              # a Deadly+ den breeds a tougher master
        if room and room.get("key") == "alduin":
            hp += self.echo                      # each Echo returns him a heart stronger
        return hp

    # --- construction ---------------------------------------------------------
    @classmethod
    def start(cls, profile, channel_id, loc_key):
        loc = D.LOCATIONS[loc_key]
        route = None if loc.get("alduin") else route_condition(loc_key)
        d = cls(profile["user_id"], profile["name"], channel_id, loc_key,
                build_rooms(loc_key, affix_level=level(profile), route=route),
                hearts=heart_max(profile), shout_charges=voice_charges(profile),
                dragon=dragon_of_the_week(), route=route)
        d.say(loc["arrive"])
        # locations answer strength by band: Hard/DRAGON stay dangerous at any power
        d.stirred = stirred_rank(profile, loc_key)
        if d.stirred:
            d.say(f"🔥 The place is **{stirred_name(d.stirred)}** (rank {d.stirred}) - foes "
                  f"fight -{D.STIRRED_FIGHT_PER_RANK * d.stirred}% harder to face, pierce "
                  f"armour and crush; the haul runs "
                  f"+{int(D.STIRRED_CLEAR_PER_RANK * d.stirred * 100)}%.")
        cond = D.ROUTE_CONDITIONS.get(route)
        if cond:
            if cond.get("blessed"):
                d.blessed = True
            d.say(f"{cond['emoji']} **{cond['name']}** - {cond['desc']}.")
        return d

    # --- helpers ---------------------------------------------------------------
    @property
    def room(self) -> dict:
        return self.rooms[self.idx]

    @property
    def loc(self) -> dict:
        return D.LOCATIONS[self.location]

    def enemy(self) -> dict | None:
        r = self.room
        return D.ENEMIES[r["key"]] if r["kind"] == "enemy" else None

    def say(self, line: str):
        self.log.append(line)
        self.log = self.log[-3:]

    def next_hint(self) -> str | None:
        """Whisper what waits in the NEXT room (enemies only) so shouts and the right
        tool can be planned - including a telegraph for any elite affix on it."""
        j = self.idx + 1
        if self.state == "playing" and j < len(self.rooms) and self.rooms[j]["kind"] == "enemy":
            r = self.rooms[j]
            hint = D.ENEMIES[r["key"]]["hint"]
            if r.get("affix"):
                hint += "  " + D.AFFIXES[r["affix"]]["telegraph"]
            return hint
        return None

    def playing(self) -> bool:
        return self.state == "playing"

    # --- room flow --------------------------------------------------------------
    def _advance(self, profile):
        """Step to the next room, or finish the delve if the boss room is done."""
        self.engaged = self.spotted = self.grounded = False
        self.ambush = self.hp_warned = False
        self.phase = None
        if self.idx >= len(self.rooms) - 1:
            if self.kind == "soulcairn":
                self._descend(profile)
                return
            self._finish_clear(profile)
            return
        self.idx += 1
        r = self.room
        self.enemy_hp = self._hp_for(r)
        if self.venom:                       # a Venomous wound bleeds into this room
            self.venom = False
            self.say("🟢 The lingering venom flares as you press on - it sears before it fades.")
            if self._wound(profile, ["Venom burns through your veins."]) == "dead":
                return
        if r["kind"] == "event" and r["key"] == "knee_trap":
            self._spring_knee_trap(profile)

    def _descend(self, profile):
        """The Soul Cairn never ends - clearing a floor drops you to the next, deeper
        and richer, until you die or choose to climb out with your haul."""
        self.depth += 1
        self.rooms.append(_soulcairn_room(self.depth, random))
        self.idx = len(self.rooms) - 1
        self.enemy_hp = self._hp_for(self.room)
        sc = profile.setdefault("soulcairn", {"best": 0})
        if self.depth > int(sc.get("best", 0)):
            sc["best"] = self.depth
        self.say(f"⬇️ You descend. **Depth {self.depth}.** The soul-light thins; the cold "
                 f"gnaws {SOULCAIRN_DRAIN * self.depth}% off your every strike now.")

    def _finish_clear(self, profile):
        bonus = _septims(profile, self.loc["clear_septims"])
        if self.daily:
            bonus = int(bonus * DAILY_CLEAR_MULT)
        cond = D.ROUTE_CONDITIONS.get(self.route)
        if cond:
            bonus = int(bonus * cond.get("clear_mult", 1.0))   # Rich Pickings pays out
        if self.stirred:
            bonus = int(bonus * (1 + D.STIRRED_CLEAR_PER_RANK * self.stirred))
        if self.echo:
            bonus = int(bonus * (1 + 0.25 * self.echo))        # his soul burns brighter each return
        self.satchel += bonus
        gained, _ = add_xp(profile, 25)
        self.xp_gained += gained
        tail = ""
        mult = pact_mult(self)
        if mult > 1.0:
            self.satchel = int(self.satchel * mult)
            st = profile["stats"]
            st["pact_clears"] = int(st.get("pact_clears", 0)) + 1
            tail = f"  ⚖️ The Princes honour the pact: **x{mult:g}**."
        profile["septims"] += self.satchel
        self._bank_ingredients(profile)
        profile["stats"]["clears"] += 1
        profile["active_delve"] = None
        self.state = "cleared"
        self.result_line = (f"Cleared! Banked **{self.satchel:,} septims** "
                            f"(including a {bonus:,} haul from the final chamber) and "
                            f"**{self.xp_gained} XP**.{tail}")
        self.say(D.pick(D.CLEAR_LINES, location=self.loc["name"]))

    def _wound(self, profile, lines, knee_chance=0.0, heavy=0.0) -> str:
        """Take a hit: armour may soak it, the Adoring Fan may take it for you,
        otherwise lose a heart (death at 0). `heavy` is the chance of a crushing
        2-heart blow. Returns 'soaked' | 'wounded' | 'dead'."""
        soak = min(SOAK_CAP + 15, soak_pct(profile) + self.buffs.get("soak", 0))
        soak = max(0, soak - D.STIRRED_SOAK_PER_RANK * self.stirred)   # stirred foes pierce armour
        if random.random() * 100 < soak:
            self.say("Your armour turns the blow - no harm done.")
            return "soaked"
        if self.fan:
            self.fan = False
            self.say("The Adoring Fan hurls himself into the blow with a delighted shriek. "
                     "He'll... he'll be fine. Probably.  (wound absorbed)")
            return "soaked"
        loss = 2 if random.random() < heavy else 1
        self.hearts -= loss
        if random.random() < knee_chance:
            self.say(D.WOUND_KNEE_LINE)
        else:
            self.say(D.pick(lines) + ("  💥 **A crushing blow!** (-2 ❤️)" if loss == 2 else ""))
        aff = self.affix()                       # a Venomous elite leaves a bleed behind
        if aff and aff.get("carry"):
            self.venom = True
            self.say("🟢 The wound festers - drink before you leave this room, or it bleeds on.")
        if self.hearts <= 0:
            self._die(profile)
            return "dead"
        return "wounded"

    def _die(self, profile):
        profile["stats"]["deaths"] += 1
        profile["active_delve"] = None
        self.state = "dead"
        lost = self.satchel
        if self.kind not in ("soulcairn",) and lost > 0:
            record_fallen(profile, self)          # leave a corpse for the next delver here
        self.result_line = (f"**You died.** The satchel - **{lost:,} septims** - stays in "
                            f"{self.loc['name']}. Your XP, gear and souls are safe.")
        self.say(D.pick(D.DEATH_LINES, location=self.loc["name"]))

    # --- enemy actions ------------------------------------------------------------
    def _heavy(self, e) -> float:
        """Chance this enemy's hit is a crushing 2-heart blow (enemy override or
        tier default, + the day's weather, + any elite affix / named-dragon menace).
        Under Dagon's Toll, every wound crushes."""
        if "dagon" in self.pacts:
            return 1.0
        base = e.get("heavy", HEAVY_HIT_CHANCE.get(e["tier"], 0.0))
        base += D.STIRRED_CRUSH_PER_RANK * self.stirred   # stirred foes hit harder
        aff = self.affix()
        if aff:
            base += aff.get("crush", 0.0)
        nd = named_dragon(self)
        if nd:
            base += nd.get("crush", 0.0)
        return base + weather_today()["heavy"]

    def _confirm_low_hp(self, profile) -> bool:
        """One heart + potions in the belt = warn once before a risky swing, so
        newer players learn what the 🧪 button is for. Returns True if the click
        was consumed by the warning."""
        if self.hearts == 1 and profile["potions"] > 0 and not self.hp_warned:
            self.hp_warned = True
            self.say("⚠️ **One heart left - and you're carrying a potion!** 🧪 heals you "
                     "first. If you truly want to fight on one heart, press the attack again.")
            return True
        return False

    def affix(self) -> dict | None:
        """The current room's elite modifier, if any (Marked Affixes)."""
        r = self.rooms[self.idx] if self.rooms else None
        if r and r.get("affix"):
            return D.AFFIXES.get(r["affix"])
        return None

    def _ward_absorbs(self, style: str) -> bool:
        """A Warded elite turns the first landed blow aside unless it's the style
        that shatters the ward (Fire). Returns True if the hit was wasted."""
        aff = self.affix()
        if not aff or not aff.get("ward_break") or self.room.get("ward_broken"):
            return False
        self.room["ward_broken"] = True
        if style == aff["ward_break"]:
            self.say(f"{aff['emoji']} **Fire shatters the ward** in a spray of blue sparks!")
            return False
        self.say(f"{aff['emoji']} The **ward flares** and swallows your blow whole - "
                 f"it takes **Fire** to break it. The fight is on.")
        return True

    def act_attack(self, profile, style: str = None) -> None:
        e = self.enemy()
        if self._confirm_low_hp(profile):
            return
        style = style if style in D.STYLES else best_style(profile, self.room["key"], self)
        p = fight_pct(profile, self.room["key"], style, self)
        was_ambush = self.ambush
        self.ambush = False
        if random.random() * 100 < p:
            # a landed hit may be soaked by an elite ward before it does anything
            if self._ward_absorbs(style):
                self.engaged = True
                return
            crit = random.random() < crit_chance(profile, self.room["key"], style, self)
            self.enemy_hp -= 2 if crit else 1
            if self.enemy_hp > 0:
                # a big foe takes the hit and keeps coming - the fight is on
                self.engaged = True
                lines = D.STAGGER_DRAGON_LINES if e["type"] == "dragon" else D.STAGGER_LINES
                line = (D.pick(D.CRIT_LINES) + "  " if crit else "") + D.pick(lines)
                self.say(line + f"  ({'🩸' * self.enemy_hp} to go)")
                self._alduin_reflight_check()
            else:
                self._kill(profile, e, style, crit=crit, ambush=was_ambush)
        else:
            self.engaged = True
            if was_ambush:
                self.say("Your strike goes wide - the ambush is blown!")
            self._wound(profile, e["wound"], knee_chance=0.10 if e["type"] == "human" else 0.0,
                        heavy=self._heavy(e))

    def _drop_ingredient(self, profile, e, aff) -> str | None:
        """A kill may drop an alchemy ingredient into the at-risk pouch. Elites and
        bounties drop more often; dragons always yield a scale."""
        chance = 0.16
        if aff:
            chance += 0.45
        if self.room.get("bounty"):
            chance += 0.30
        if e["type"] == "dragon":
            key = "dragon_scale"
        else:
            if random.random() > chance:
                return None
            table = D.INGREDIENT_DROPS.get(e["type"])
            if not table:
                return None
            key = random.choice(table)
        self.ingredients[key] = self.ingredients.get(key, 0) + 1
        ing = D.INGREDIENTS[key]
        return f"{ing['emoji']} {ing['name']}"

    def _bank_ingredients(self, profile):
        """Move the pouch into the character's stores - called wherever the satchel
        banks (leave/flee/clear/launch). Death is the only thing that loses them."""
        if not self.ingredients:
            return
        store = profile.setdefault("ingredients", {})
        for k, n in self.ingredients.items():
            store[k] = store.get(k, 0) + n
        self.ingredients = {}

    def _kill(self, profile, e, style, crit=False, ambush=False):
        gain = _skill_up(profile, style)
        tier = e["tier"]
        bounty = bool(self.room.get("bounty"))
        aff = self.affix()
        xp = DRAGON_KILL_XP if e["type"] == "dragon" else 12 * tier
        if bounty:
            xp *= 2
        if aff:
            xp = int(xp * aff.get("xp_mult", 1.0))
        gained, ups = add_xp(profile, xp)
        self.xp_gained += gained
        loot = _septims(profile, tier * 12 + random.randint(0, 8))
        if bounty:
            loot *= 3
        if crit:
            loot *= 2
        if aff:
            loot = int(loot * aff.get("loot_mult", 1.0))
        if self.stirred:
            loot = int(loot * (1 + D.STIRRED_LOOT_PER_RANK * self.stirred))
        self.satchel += loot
        self.kills += 1
        profile["stats"]["kills"] += 1
        drop = self._drop_ingredient(profile, e, aff)      # at-risk alchemy loot
        line = ""
        if ambush:
            line += D.pick(D.AMBUSH_KILL_LINES) + "  "
        if crit:
            line += D.pick(D.CRIT_LINES) + "  "
        if aff:
            line += f"{aff['emoji']} The **{aff['tag']} {e['name']}** falls.  "
        line += f"{D.pick(e['kill'])}  (+{gained} XP, +{loot} septims"
        if bounty:
            line += ", bounty claimed"
        if gain:
            line += f", {D.STYLES[style]['name']} +{gain}"
        if drop:
            line += f", {drop}"
        line += ")"
        if e["type"] == "dragon":
            profile["souls"] += 1
            profile["stats"]["dragons"] += 1
            line += "  🐉 **+1 dragon soul**"
            if profile.get("words", 0) > 0 and self.shout_charges < profile["words"]:
                self.shout_charges = int(profile["words"])
                _sync_voice(profile, self, self.shout_charges)
                line += "  🗣️ **the soul renews your Thu'um**"
            nd_key = getattr(self, "dragon", None)
            if self.room["key"] == "dragon" and nd_key:
                wall = profile.setdefault("dragon_wall", [])
                nd = D.DRAGON_ROSTER[nd_key]
                if nd_key not in wall:
                    wall.append(nd_key)
                    line += f"  🐲 **{nd['name']}** joins your Dragon Wall!"
        if self.room["key"] == "alduin":
            profile["alduin_slain"] = profile.get("alduin_slain", 0) + 1
        if ups:
            line += f"\n🆙 **Level up! You are now level {level(profile)}** (+{ups} perk point)."
        self.say(line)
        self._advance(profile)

    def act_sneak(self, profile) -> None:
        """Slip into stealth. Success doesn't skip the room any more - it earns a
        CHOICE: ambush (attack at +AMBUSH_BONUS) or slip past quietly."""
        e = self.enemy()
        p = sneak_pct(profile, self.room["key"])
        if p is None or self.engaged or self.spotted or self.ambush:
            return
        if random.random() * 100 < p:
            gain = _skill_up(profile, "sneak")
            self.ambush = True
            line = D.pick(D.AMBUSH_READY_LINES)
            if gain:
                line += f"  (Sneak +{gain})"
            self.say(line)
        else:
            self.spotted = True
            self.engaged = True
            self.say(D.pick(D.SPOTTED_LINES))
            self._wound(profile, e["wound"], heavy=self._heavy(e))

    def act_slip(self, profile) -> None:
        """From ambush position: take the quiet exit instead of the knife."""
        if not self.ambush:
            return
        e = self.enemy()
        self.ambush = False
        gained, ups = add_xp(profile, 8 * e["tier"])
        self.xp_gained += gained
        profile["stats"]["sneaks"] += 1
        line = f"{D.pick(D.SNEAK_LINES)}  (+{gained} XP)"
        if ups:
            line += f"\n🆙 **Level up! You are now level {level(profile)}** (+{ups} perk point)."
        self.say(line)
        self._advance(profile)

    def act_persuade(self, profile) -> None:
        e = self.enemy()
        p = persuade_pct(profile, self.room["key"])
        if p is None or self.engaged:
            return
        if random.random() * 100 < p:
            gain = _skill_up(profile, "speech")
            gained, ups = add_xp(profile, 10 * e["tier"])
            self.xp_gained += gained
            profile["stats"]["persuades"] += 1
            line = D.pick(e.get("persuaded", ["They let you pass."]))
            extra = ""
            if random.random() < 0.5:
                bribe = _septims(profile, e["tier"] * 8)
                self.satchel += bribe
                extra = f", +{bribe} septims"
            line += f"  (+{gained} XP{extra}"
            if gain:
                line += f", Speech +{gain}"
            line += ")"
            if ups:
                line += f"\n🆙 **Level up! You are now level {level(profile)}** (+{ups} perk point)."
            self.say(line)
            self._advance(profile)
        else:
            self.engaged = True
            self.say("Your silver tongue turns to lead - steel comes out instead.")
            self._wound(profile, e["wound"], heavy=self._heavy(e))

    def _alduin_reflight_check(self):
        """After ANY damage to Alduin, he takes wing again at set thresholds - the
        fight is a war over shout charges. Shared by weapon hits and DAH."""
        if self.room["key"] == "alduin" and self.enemy_hp in D.ALDUIN_REFLIGHT_HP \
                and self.grounded:
            self.grounded = False
            self.say("**Alduin takes wing again**, laughing in the old tongue. Bring him down!")

    def shout_cost_available(self, profile) -> int:
        """The largest shout (in words) you could spend right now."""
        return min(profile.get("words", 0), self.shout_charges)

    def act_shout(self, profile, cost=None) -> None:
        """The Words of Power loadout. Spend 1, 2 or 3 charges from your pool for a
        different effect - a rationing puzzle, not a spam button:
          FUS (1)         - stagger: ground a dragon, or chip one telling blow.
          FUS RO (2)      - the old room-flatten (loot + move on); grounds+chips a dragon.
          FUS RO DAH (3)  - the true Thu'um: 2 damage to ANYTHING (dragons included)."""
        words = profile.get("words", 0)
        if words <= 0 or self.shout_charges <= 0:
            return
        e = self.enemy()
        if e is None:
            return
        cost = 1 if cost is None else int(cost)          # plain shout = FUS (cheap ground)
        cost = max(1, min(cost, words, self.shout_charges))
        is_dragon = e["type"] == "dragon"
        if is_dragon and self.grounded and cost == 1:
            return                                       # already grounded - FUS is wasted
        shout = " ".join(D.SHOUT_WORDS[:cost])
        self.shout_charges -= cost
        _sync_voice(profile, self, self.shout_charges)   # spent breath stays spent

        if cost >= 3:                                    # FUS RO DAH - true damage
            if is_dragon:
                self.grounded = True
            self.enemy_hp -= 2
            self.say(f"**\"{shout}!\"** The whole Thu'um lands like a god's own fist.")
            if self.enemy_hp <= 0:
                self._kill(profile, e, best_style(profile, self.room["key"], self))
            else:
                self.engaged = True
                self.say(f"  ({'🩸' * self.enemy_hp} to go)")
                self._alduin_reflight_check()
            return

        if is_dragon:                                    # FUS / FUS RO on a dragon: ground it
            self.grounded = True
            self.say(D.pick(D.SHOUT_DRAGON_LINES, shout=shout))
            if cost == 2:                                # RO also chips a blow off it
                self.enemy_hp = max(1, self.enemy_hp - 1)
                self.say(f"The Voice cracks scale from bone.  ({'🩸' * self.enemy_hp} to go)")
                self._alduin_reflight_check()
            return

        if cost == 1:                                    # FUS on a non-dragon: a stagger
            self.enemy_hp -= 1
            if self.enemy_hp <= 0:
                self._kill(profile, e, best_style(profile, self.room["key"], self))
            else:
                self.engaged = True
                self.say(f"**\"{shout}!\"** The {e['name']} is hurled back, reeling.  "
                         f"({'🩸' * self.enemy_hp} to go)")
            return

        # FUS RO on a non-dragon - the thorough room-flatten (loot + move on)
        self.say(D.pick(D.SHOUT_CLEAR_LINES, shout=shout, enemy=e["name"]))
        loot = _septims(profile, e["tier"] * 12 + random.randint(0, 8))
        self.satchel += loot
        gained, _ = add_xp(profile, 6 * e["tier"])
        self.xp_gained += gained
        self.say(f"You pick through the wreckage.  (+{gained} XP, +{loot} septims)")
        self._advance(profile)

    def act_potion(self, profile) -> None:
        if "namira" in self.pacts:
            self.say("🐀 **Namira's Fast holds.** The bottle stays corked.")
            return
        cap = delve_heart_max(self, profile)
        if profile["potions"] <= 0 or (self.hearts >= cap and not self.venom):
            return
        profile["potions"] -= 1
        self.hp_warned = False
        cured = self.venom
        self.venom = False
        if self.hearts < cap:
            self.hearts += 1
        line = "You drink a health potion. The wound knits before your eyes.  ❤️ +1"
        if cured:
            line += "  🟢 The venom neutralises."
        self.say(line)

    def act_leave(self, profile) -> None:
        """Leave with the satchel; mid-fight it becomes a flee and loot spills.
        Clavicus Vile's Bargain permits neither."""
        if not self.playing():
            return
        if "clavicus" in self.pacts:
            self.say("😈 **The Bargain holds.** There is no way out but through - or under.")
            return
        profile["active_delve"] = None
        self._bank_ingredients(profile)          # the pouch comes home either way
        mult = pact_mult(self)
        if self.engaged:
            kept = int(self.satchel * FLEE_KEEP * mult)
            profile["septims"] += kept
            profile["stats"]["flees"] += 1
            self.state = "fled"
            self.result_line = (f"You fled mid-fight - **{kept:,} septims** made it home, "
                                f"the rest spilled behind you. **{self.xp_gained} XP** banked.")
            self.say(D.pick(D.FLEE_LINES))
        else:
            self.satchel = int(self.satchel * mult)
            profile["septims"] += self.satchel
            self.state = "left"
            if self.kind == "soulcairn":
                best = int((profile.get("soulcairn") or {}).get("best", 0))
                self.result_line = (f"You climb out of the Cairn at **depth {self.depth}** with "
                                    f"**{self.satchel:,} septims** and **{self.xp_gained} XP**. "
                                    f"Deepest ever: **{best}**.")
            else:
                self.result_line = (f"You walk out with **{self.satchel:,} septims** and "
                                    f"**{self.xp_gained} XP**.")
            self.say(D.pick(D.LEAVE_LINES))

    def _take_deep_fork(self, profile):
        """The deep way: an extra elite (guaranteed affix) guarding a locked strongbox,
        inserted right before the boss. Richer, riskier - an honest choice."""
        loc = self.loc
        pool = list(loc["pool"].keys())
        weights = list(loc["pool"].values())
        ekey = random.choices(pool, weights=weights, k=1)[0]
        elite = {"kind": "enemy", "key": ekey, "boss": False, "resolved": False}
        aff = _eligible_affix(ekey, random)
        if aff:
            elite["affix"] = aff
        chest = {"kind": "event", "key": "chest", "boss": False, "resolved": False, "locked": True}
        self.rooms[self.idx + 1:self.idx + 1] = [elite, chest]
        self.say("You take the deep way down. The air thickens - something elite is guarding "
                 "a strongbox in the dark.")
        self._advance(profile)

    # --- event actions --------------------------------------------------------------
    def _spring_knee_trap(self, profile):
        self.room["resolved"] = True
        res = self._wound(profile, [D.WOUND_KNEE_LINE], knee_chance=0.0)
        if res == "soaked":
            self.say("A dart trap! The arrow glances off your greave, just below the knee. Too close.")
        elif res == "wounded":
            self.say(D.WOUND_KNEE_LINE)

    def act_event(self, profile, choice: str) -> None:
        """Resolve an event-room button. choice: open|skip|take|pray|approach|talk|retreat|continue."""
        r = self.room
        if r["kind"] != "event":
            return
        key = r["key"]
        if key == "knee_trap" and choice == "continue":
            self._advance(profile)
            return
        if key == "chest" and r.get("mimic") and choice == "open":
            # it bites: the chest becomes a fight in place
            r["kind"] = "enemy"
            r["key"] = "mimic"
            r["was_mimic"] = True
            r.pop("mimic", None)
            self.enemy_hp = self._hp_for(r)
            self.engaged = True
            self.say(D.pick(D.ENEMIES["mimic"]["intro"]))
            return
        if key == "fork":
            if choice == "deep":
                self._take_deep_fork(profile)
            else:
                self.say("You take the low, safe road. The exit is close now.")
                self._advance(profile)
            return
        if choice == "skip":
            self.say("You move on. Curiosity has killed sturdier adventurers.")
            self._advance(profile)
            return

        if key == "chest" and choice == "pick":
            # a master-locked strongbox: one careful attempt, double the loot
            p = lockpick_pct(profile)
            if random.random() * 100 < p:
                gain = _skill_up(profile, "lockpicking")
                loot = _septims(profile, 2 * (40 + random.randint(0, 80)))
                self.satchel += loot
                gained, _ = add_xp(profile, 12)
                self.xp_gained += gained
                line = (f"The lock gives with a whisper. Inside: **{loot} septims**."
                        f"  (+{gained} XP")
                if gain:
                    line += f", Lockpicking +{gain}"
                self.say(line + ")")
            else:
                self.say("The pick snaps deep in the mechanism. Whatever is in there stays there.")
            self._advance(profile)
            return
        if key == "chest" and choice == "open" and not r.get("locked"):
            if random.random() < chest_trap_chance(profile):
                loot = _septims(profile, 20 + random.randint(0, 40))
                self.satchel += loot
                if self._wound(profile, ["A needle trap! Poison burns up your arm."]) != "dead":
                    self.say(f"Trapped! You still claw {loot} septims from the bottom. Never should have come here.")
            else:
                loot = _septims(profile, 40 + random.randint(0, 80))
                self.satchel += loot
                line = f"You crack the lid: **{loot} septims**."
                if random.random() < 0.10 and profile["potions"] < potion_cap(profile):
                    profile["potions"] += 1
                    line += "  And a health potion, tucked in the corner. 🧪"
                self.say(line)
            if self.playing():
                self._advance(profile)
        elif key == "sweetroll" and choice == "take":
            profile["stats"]["sweetrolls"] += 1
            if self.hearts < heart_max(profile):
                self.hearts += 1
                self.say("You eat the sweetroll. It is, impossibly, still warm.  ❤️ +1")
            else:
                self.say("You are at full health, but you eat the sweetroll anyway. Obviously.")
            self._advance(profile)
        elif key == "shrine" and choice == "pray":
            if self.hearts < heart_max(profile):
                healed = min(2, heart_max(profile) - self.hearts)
                self.hearts += healed
                self.say(f"Warmth spreads from the shrine - the Nine mend what they can.  ❤️ +{healed}")
            else:
                self.blessed = True
                self.say(f"The Nine watch over you.  (+{BLESSING_BONUS}% attack for this delve)")
            self._advance(profile)
        elif key == "satchel" and choice == "take":
            if profile["potions"] < potion_cap(profile):
                profile["potions"] += 1
                self.say("A health potion, still sealed.  🧪 +1")
            else:
                pocket = _septims(profile, 25)
                self.satchel += pocket
                self.say(f"Your potion pockets are full - you take the coin purse instead. +{pocket} septims")
            self._advance(profile)
        elif key == "maiq" and choice == "talk":
            gained, _ = add_xp(profile, 5)
            self.xp_gained += gained
            self.say(f"{D.pick(D.M_AIQ_LINES)}  (+{gained} XP. Wisdom, probably.)")
            self._advance(profile)
        elif key == "fallen":
            corpse = r.get("corpse") or {}
            who = corpse.get("name", "a fallen soul")
            if choice == "loot":
                loot = _septims(profile, int(corpse.get("satchel", 100)))
                self.satchel += loot
                if random.random() < 0.35:
                    self.say(f"You pry the satchel free (+{loot} septims) - and the corpse's "
                             f"hand snaps shut on your wrist!")
                    if self._wound(profile, ["The restless dead do not forgive grave-robbers."]) == "dead":
                        return
                else:
                    self.say(f"You take what **{who}** no longer needs. +{loot} septims.")
                self._advance(profile)
            else:                                 # honour / bury them
                gained, _ = add_xp(profile, 20)
                self.xp_gained += gained
                self.blessed = True
                self.say(f"You lay **{who}** to rest with a word to Arkay. The Divines mark it.  "
                         f"(+{gained} XP, Blessed +{BLESSING_BONUS}% attack this delve)")
                self._advance(profile)
        elif key == "wordwall" and choice == "approach":
            if profile["words"] >= len(D.SHOUT_WORDS):
                self.say("The wall chants a word you already know. The Voice hums along.")
            elif profile["souls"] > 0:
                profile["souls"] -= 1
                profile["words"] += 1
                self.shout_charges += 1
                _sync_voice(profile, self, self.shout_charges)
                word = D.SHOUT_WORDS[profile["words"] - 1]
                known = " ".join(D.SHOUT_WORDS[:profile["words"]])
                self.say(f"A dragon's soul burns away and the word **{word}** sears into your mind."
                         f"  🗣️ Your Voice: **{known}** ({profile['words']}/3 words)")
            else:
                self.say("The wall chants, but the word slides off your mind. It needs the "
                         "strength of a **dragon's soul** to stick.")
            self._advance(profile)
        elif key == "mudcrab" and choice == "trade":
            coin = _septims(profile, 30 + random.randint(0, 30))
            self.satchel += coin
            self.say("You lay out your spare junk. The mudcrab appraises it with one claw, "
                     f"clacks twice, and pays. +{coin} septims. He drives a hard bargain, for a crab.")
            self._advance(profile)
        elif key == "nazeem":
            if choice == "yes":
                gained, _ = add_xp(profile, 10)
                self.xp_gained += gained
                self.say("\"Yes, actually. Most days.\" Nazeem is visibly shaken. He leaves "
                         f"without another word.  (+{gained} XP. Worth it.)")
            else:
                gained, _ = add_xp(profile, 5)
                self.xp_gained += gained
                self.say(f"You sigh, deeply, from the soul. Even the dungeon feels it.  (+{gained} XP)")
            self._advance(profile)
        elif key == "adoring_fan":
            if choice == "adopt":
                self.fan = True
                self.say("\"I'll follow you FOREVER, Grand Champion!\" He will follow you for "
                         "exactly one wound, which he will heroically absorb.  🤩 (fan acquired)")
            else:
                gained, _ = add_xp(profile, 5)
                self.xp_gained += gained
                self.say("You point at the horizon and tell him the Grand Champion went that way. "
                         f"He sprints off, weeping with joy.  (+{gained} XP)")
            self._advance(profile)
        elif key == "giant":
            if choice == "retreat":
                self.say("You back away slowly. The giant watches you go, then returns to its cows. Wise.")
                self._advance(profile)
            elif choice == "approach":
                if random.random() < 0.5:
                    self.satchel = int(self.satchel * pact_mult(self))
                    profile["septims"] += self.satchel
                    self._bank_ingredients(profile)
                    profile["stats"]["launched"] += 1
                    profile["active_delve"] = None
                    self.state = "launched"
                    self.result_line = (f"Banked **{self.satchel:,} septims** and "
                                        f"**{self.xp_gained} XP**. And some airtime.")
                    self.say("The club catches you mid-hello. Skyrim physics take over.\n"
                             "You regain consciousness outside the entrance, somehow intact, "
                             "loot and all. The clouds were lovely.")
                else:
                    cheese = _septims(profile, 60)
                    self.satchel += cheese
                    self.say("The giant looks at you, decides you are not worth the swing, and "
                             f"nods at a mammoth cheese wheel. You roll it out. +{cheese} septims 🧀")
                    self._advance(profile)

    # --- serialisation ---------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"type": "skyrim", "delve_id": self.delve_id,
                "player_id": self.player_id, "player_name": self.player_name,
                "channel_id": self.channel_id, "location": self.location, "rooms": self.rooms,
                "idx": self.idx, "hearts": self.hearts, "satchel": self.satchel,
                "shout_charges": self.shout_charges, "engaged": self.engaged,
                "spotted": self.spotted, "grounded": self.grounded, "blessed": self.blessed,
                "state": self.state, "log": self.log, "message_id": self.message_id,
                "xp_gained": self.xp_gained, "kills": self.kills,
                "result_line": self.result_line, "enemy_hp": self.enemy_hp,
                "daily": self.daily, "fan": self.fan,
                "ambush": self.ambush, "hp_warned": self.hp_warned,
                "venom": self.venom, "ingredients": self.ingredients,
                "dragon": self.dragon, "phase": self.phase, "depth": self.depth,
                "kind": self.kind, "buffs": self.buffs, "route": self.route,
                "pacts": self.pacts, "stirred": self.stirred, "echo": self.echo}

    @classmethod
    def from_dict(cls, d: dict) -> "Delve":
        return cls(d["player_id"], d.get("player_name", "Adventurer"), d.get("channel_id"),
                   d["location"], d["rooms"], idx=d.get("idx", 0), hearts=d.get("hearts", 3),
                   satchel=d.get("satchel", 0), shout_charges=d.get("shout_charges", 0),
                   engaged=d.get("engaged", False), spotted=d.get("spotted", False),
                   grounded=d.get("grounded", False), blessed=d.get("blessed", False),
                   state=d.get("state", "playing"), log=d.get("log"),
                   message_id=d.get("message_id"), xp_gained=d.get("xp_gained", 0),
                   kills=d.get("kills", 0), result_line=d.get("result_line", ""),
                   delve_id=d.get("delve_id"), enemy_hp=d.get("enemy_hp"),
                   daily=d.get("daily", False), fan=d.get("fan", False),
                   ambush=d.get("ambush", False), hp_warned=d.get("hp_warned", False),
                   venom=d.get("venom", False), ingredients=d.get("ingredients"),
                   dragon=d.get("dragon"), phase=d.get("phase"), depth=d.get("depth", 0),
                   kind=d.get("kind", "normal"), buffs=d.get("buffs"), route=d.get("route"),
                   pacts=d.get("pacts"), stirred=d.get("stirred", 0), echo=d.get("echo", 0))


# ---------------------------------------------------------------------------
# Starting / abandoning delves
# ---------------------------------------------------------------------------
def abandon_active(profile):
    """Close a previous still-open delve safely: bank its satchel (an implicit
    Leave - never punitive) and drop its persisted state so the old buttons die.
    An abandoned daily still counts as that day's attempt and is recorded as left."""
    mid = profile.get("active_delve")
    if not mid:
        return
    old = load_delve(mid)
    if old is not None and old.playing():
        profile["septims"] += old.satchel
        if old.daily:
            old.state = "left"
            record_daily_result(profile, old)
        logger.info("skyrim: auto-banked %s septims from %s's abandoned delve",
                    old.satchel, profile["user_id"])
    delete_delve(mid)
    profile["active_delve"] = None


def _first_delve_of_day_comforts(profile, delve: Delve):
    """Breezehome comforts, applied once per UK day on whichever delve is first."""
    if profile.get("last_delve_date") == _today_str():
        return
    profile["last_delve_date"] = _today_str()
    if home_owned(profile, "breezehome"):
        delve.blessed = True
        delve.say(f"🏠 Well-rested from a night in Breezehome.  (+{BLESSING_BONUS}% attack today's first delve)")
    if home_owned(profile, "alchemy_lab") and profile["potions"] < potion_cap(profile):
        profile["potions"] += 1
        delve.say("⚗️ Your alchemy lab left a fresh potion by the door.  🧪 +1")


def start_delve(profile, channel_id, loc_key, kind: str = "normal") -> Delve:
    """Begin a delve. kind: 'normal' (spends stamina) | 'daily' (the shared seeded
    dungeon, once per day, no stamina) | 'alduin' (Skuldafn, once per day, no stamina).
    Callers must have checked availability; this marks the attempt."""
    abandon_active(profile)
    if kind == "daily":
        # the shared layout rolls elites like a seasoned delver's map and always
        # features at least one - seeded, so everyone faces the same marked foes
        date, loc_key, route, rooms = _daily_rooms()
        profile["daily"] = {"date": date}
        delve = Delve(profile["user_id"], profile["name"], channel_id, loc_key,
                      rooms, hearts=heart_max(profile),
                      shout_charges=voice_charges(profile), daily=True, route=route)
        delve.say(D.LOCATIONS[loc_key]["arrive"])
        cond = D.ROUTE_CONDITIONS.get(route)
        if cond:
            if cond.get("blessed"):
                delve.blessed = True
            delve.say(f"{cond['emoji']} **{cond['name']}** - {cond['desc']}.")
    elif kind == "alduin":
        profile["alduin"] = {"date": _today_str()}
        delve = Delve.start(profile, channel_id, "skuldafn")
        # the fight is designed as a war over three charges: the Greybeards' song
        # grants a full Voice at the gate (a loan - it doesn't refill your own)
        delve.shout_charges = int(profile.get("words", 0))
        delve.echo = alduin_echo(profile)
        if delve.echo:
            delve.say(f"🌑 **Echo {delve.echo}** - you have undone him before, and he "
                      f"remembers. Harder to face, a heart stronger, and a richer soul "
                      f"to take (+{25 * delve.echo}%).")
    else:
        spend_stamina(profile)
        delve = Delve.start(profile, channel_id, loc_key)
        # sworn pacts bind to the next normal delve only
        pacts = profile.get("nextpacts") or []
        if pacts:
            profile["nextpacts"] = []
            delve.pacts = pacts
            names = ", ".join(f"{D.PACTS[k]['emoji']} {D.PACTS[k]['name']}" for k in pacts)
            delve.say(f"⚖️ **Pacts sworn:** {names}  (satchel x{pact_mult(delve):g} if you bank it)")
    delve.kind = kind
    profile["stats"]["delves"] += 1
    w = weather_today()
    if w["key"] != "clear":
        delve.say(weather_line(w))
    _apply_brew_buffs(profile, delve)
    _first_delve_of_day_comforts(profile, delve)
    return delve


def _apply_brew_buffs(profile, delve: Delve):
    """Consume any brewed one-delve elixir queued at the Lab Bench (Ingredient Pouch)."""
    nxt = profile.get("nextdelve") or {}
    if not nxt:
        return
    delve.buffs = dict(nxt)
    profile["nextdelve"] = {}
    if nxt.get("heart"):
        delve.hearts += int(nxt["heart"])
    bits = []
    if nxt.get("fight"):
        bits.append(f"+{nxt['fight']}% attack")
    if nxt.get("crit"):
        bits.append(f"+{nxt['crit']}% crit")
    if nxt.get("soak"):
        bits.append(f"+{nxt['soak']}% soak")
    if nxt.get("heart"):
        bits.append(f"+{nxt['heart']} heart")
    if bits:
        delve.say("🧪 Your brewed elixir courses through you.  (" + ", ".join(bits) + " this delve)")


# ---------------------------------------------------------------------------
# The Daily Delve - one shared, seeded dungeon per UK day. Same rooms for
# everyone (your own dice), one attempt each, results on a shared board.
# Entirely button-driven: nothing is ever posted on a schedule.
# ---------------------------------------------------------------------------
def _daily_layout() -> tuple:
    """(date_str, loc_key, seeded_rng) for today's shared dungeon."""
    date = _today_str()
    rng = random.Random(f"skyrim-daily-{date}")
    pool = sorted(k for k, v in D.LOCATIONS.items()
                  if not v.get("dragon_lair") and not v.get("alduin")
                  and not v.get("soulcairn"))
    return date, rng.choice(pool), rng


def _ensure_affix(rooms: list, rng) -> None:
    """Guarantee at least one Marked (affixed) foe in a room list - the daily
    always features one, so the counter-play read comes up every day."""
    if any(r.get("affix") for r in rooms if r["kind"] == "enemy"):
        return
    cands = [r for r in rooms if r["kind"] == "enemy"
             and not r["boss"] and not r.get("bounty")]
    if not cands:
        return
    target = rng.choice(cands)
    aff = _eligible_affix(target["key"], rng)
    if aff:
        target["affix"] = aff


def _daily_rooms() -> tuple:
    """(date, loc_key, route, rooms) for today's shared dungeon - THE single
    builder, so the delve and any preview always agree on the layout."""
    date, loc_key, rng = _daily_layout()
    route = route_condition(loc_key)
    rooms = build_rooms(loc_key, rng, affix_level=15, route=route)
    _ensure_affix(rooms, rng)
    return date, loc_key, route, rooms


def daily_affixes() -> list:
    """The affix keys marked on today's shared board (for the daily panel tease)."""
    _date, _loc, _route, rooms = _daily_rooms()
    return sorted({r["affix"] for r in rooms if r.get("affix")})


def daily_location() -> dict:
    _date, loc_key, _rng = _daily_layout()
    return {"key": loc_key, **D.LOCATIONS[loc_key]}


def daily_available(profile) -> bool:
    return (profile.get("daily") or {}).get("date") != _today_str()


def _daily_store() -> dict:
    return load_json_file(config.SKYRIM_DAILY_FILE) or {}


def record_daily_result(profile, delve: Delve):
    """Write this attempt onto today's shared board (best-effort, last write wins)."""
    try:
        store = _daily_store()
        today = _today_str()
        day = store.get(today) or {}
        day[str(profile["user_id"])] = {
            "name": profile["name"], "stone": profile["stone"], "state": delve.state,
            "satchel": delve.satchel, "kills": delve.kills,
            "rooms": delve.idx + (1 if delve.state == "cleared" else 0),
            "total_rooms": len(delve.rooms), "xp": delve.xp_gained,
        }
        # keep only today + yesterday; the board is ephemeral history
        keep = sorted(store.keys())[-1:]
        store = {k: v for k, v in store.items() if k in keep}
        store[today] = day
        save_json_file(config.SKYRIM_DAILY_FILE, store)
    except Exception:
        logger.error("skyrim: failed to record daily result", exc_info=True)


def daily_results() -> dict:
    """Today's attempts, keyed by user id string."""
    return _daily_store().get(_today_str()) or {}


# ---------------------------------------------------------------------------
# Alduin - the endgame. Gated hard, one attempt per day, never auto-triggered:
# the location picker offers Skuldafn only when the character has earned it.
# ---------------------------------------------------------------------------
def alduin_echo(profile) -> int:
    """How many times this character has undone the World-Eater (capped for the
    fight's scaling). Each echo hardens the rematch and raises its dragon price."""
    return min(int(profile.get("alduin_slain") or 0), 4)


def alduin_ready(profile) -> tuple:
    """(ready, requirements_line). Ready means the gates are met, regardless of
    whether today's attempt is spent. Every past kill raises the dragon price -
    the World-Eater does not grant rematches cheaply."""
    need_lvl = int(getattr(config, "SKYRIM_ALDUIN_MIN_LEVEL", 20))
    need_drag = (int(getattr(config, "SKYRIM_ALDUIN_MIN_DRAGONS", 5))
                 + int(getattr(config, "SKYRIM_ALDUIN_DRAGONS_PER_ECHO", 3))
                 * int(profile.get("alduin_slain") or 0))
    lvl_ok = level(profile) >= need_lvl
    words_ok = profile["words"] >= len(D.SHOUT_WORDS)
    drag_ok = profile["stats"]["dragons"] >= need_drag
    words_str = "✅" if words_ok else f"{profile['words']}/3"
    drags_str = "✅" if drag_ok else str(profile["stats"]["dragons"])
    line = (f"level {need_lvl}+ ({'✅' if lvl_ok else level(profile)}) · "
            f"FUS RO DAH ({words_str}) · "
            f"{need_drag} dragons slain ({drags_str})")
    return (lvl_ok and words_ok and drag_ok), line


def alduin_available(profile) -> bool:
    ready, _ = alduin_ready(profile)
    return ready and (profile.get("alduin") or {}).get("date") != _today_str()


# ---------------------------------------------------------------------------
# The Soul Cairn - post-Alduin endless descent. Unlocked by slaying Alduin, one
# attempt per UK day (matching Alduin's cap, so it never becomes a binge). The
# only prize is the depth record - it's you versus how deep you dare.
# ---------------------------------------------------------------------------
def soulcairn_unlocked(profile) -> bool:
    return bool(profile.get("alduin_slain"))


def soulcairn_available(profile) -> bool:
    return (soulcairn_unlocked(profile)
            and (profile.get("soulcairn") or {}).get("date") != _today_str())


def soulcairn_best(profile) -> int:
    return int((profile.get("soulcairn") or {}).get("best", 0))


def start_soulcairn(profile, channel_id) -> Delve:
    """Begin the day's descent. Callers must have checked availability."""
    abandon_active(profile)
    sc = profile.setdefault("soulcairn", {"best": 0})
    sc["date"] = _today_str()
    d = Delve(profile["user_id"], profile["name"], channel_id, "soul_cairn",
              [_soulcairn_room(0, random)], hearts=heart_max(profile),
              shout_charges=voice_charges(profile), kind="soulcairn", dragon=dragon_of_the_week())
    d.say(D.LOCATIONS["soul_cairn"]["arrive"])
    profile["stats"]["delves"] += 1
    _apply_brew_buffs(profile, d)
    _first_delve_of_day_comforts(profile, d)
    return d


# ---------------------------------------------------------------------------
# Shop / perks / property (called from the hub views)
# ---------------------------------------------------------------------------
def home_owned(profile, key: str) -> bool:
    return key in (profile.get("home") or [])


def toggle_armour_style(profile) -> str:
    """Swap between heavy (full soak) and light (quieter) armour. Free, like
    re-equipping - the smith just looks at you differently. Returns the new style."""
    profile["armour_style"] = "light" if profile.get("armour_style") != "light" else "heavy"
    return profile["armour_style"]


def buy_home(profile, key: str) -> str | None:
    """Buy Breezehome or a furnishing. Returns an error line, or None on success."""
    item = D.HOME_ITEMS.get(key)
    if item is None:
        return "Belethor has never heard of it."
    if home_owned(profile, key):
        return f"You already own {item['name']}."
    if item["requires"] and not home_owned(profile, item["requires"]):
        return f"You need {D.HOME_ITEMS[item['requires']]['name']} first."
    if profile["septims"] < item["price"]:
        return f"{item['name']} costs {item['price']:,} septims - you have {profile['septims']:,}."
    profile["septims"] -= item["price"]
    profile["home"] = sorted((profile.get("home") or []) + [key])
    return None
def buy_potion(profile) -> str | None:
    if profile["potions"] >= potion_cap(profile):
        return "Your potion pockets are full."
    if profile["septims"] < D.POTION_PRICE:
        return f"A health potion is {D.POTION_PRICE} septims. \"Come back with coin, friend.\""
    profile["septims"] -= D.POTION_PRICE
    profile["potions"] += 1
    return None


def buy_gear(profile, slot: str) -> str | None:
    """Upgrade weapon/armour to the next tier. Returns an error line, or None on success."""
    tier_now = profile[f"{slot}_tier"]
    if tier_now >= len(D.GEAR_TIERS) - 1:
        return "Nothing finer exists in Tamriel."
    nxt = D.GEAR_TIERS[tier_now + 1]
    price = nxt["price"] if slot == "weapon" else int(nxt["price"] * 0.8)
    if profile["stats"]["dragons"] < nxt["dragons"]:
        return (f"{nxt['name']} gear is forged from dragon bone - Belethor eyes you doubtfully. "
                f"\"Slay {nxt['dragons']} dragons and we'll talk.\" "
                f"({profile['stats']['dragons']}/{nxt['dragons']})")
    if profile["septims"] < price:
        return f"{nxt['name']} costs {price:,} septims - you have {profile['septims']:,}."
    profile["septims"] -= price
    profile[f"{slot}_tier"] = tier_now + 1
    return None


def take_perk(profile, key: str) -> str | None:
    if key not in D.PERKS:
        return "No such perk."
    if perk_points(profile) <= 0:
        return "No perk points to spend - level up first."
    if perk_rank(profile, key) >= D.PERKS[key]["ranks"]:
        return "That perk is already at its highest rank."
    profile["perks"][key] = perk_rank(profile, key) + 1
    return None


# ---------------------------------------------------------------------------
# Capstone Doctrines - each skill hitting 100 unlocks a permanent pick-one-of-two
# mastery. Two maxed characters fight the same room differently.
# ---------------------------------------------------------------------------
def doctrine_choices_open(profile) -> list:
    """Skills at 100 that haven't chosen a doctrine yet."""
    return [s for s in SKILLS if profile["skills"].get(s, 0) >= 100
            and s not in (profile.get("doctrines") or {}) and s in D.DOCTRINES]


def choose_doctrine(profile, skill: str, choice: str) -> str | None:
    if skill not in D.DOCTRINES or choice not in D.DOCTRINES[skill]:
        return "No such doctrine."
    if profile["skills"].get(skill, 0) < 100:
        return "You must master that skill (100) first."
    if skill in (profile.get("doctrines") or {}):
        return "That mastery is already chosen - it is permanent."
    profile.setdefault("doctrines", {})[skill] = choice
    return None


# ---------------------------------------------------------------------------
# Legendary Skills - reset a mastered (100) skill to 15 for a permanent star. The
# doctrine you earned stays; the climb begins again. Skyrim's own prestige loop.
# ---------------------------------------------------------------------------
def legendary_ready(profile) -> list:
    return [s for s in SKILLS if profile["skills"].get(s, 0) >= 100]


def make_legendary(profile, skill: str) -> str | None:
    if skill not in SKILLS:
        return "No such skill."
    if profile["skills"].get(skill, 0) < 100:
        return "Only a mastered skill (100) can be made Legendary."
    profile["skills"][skill] = 15
    profile.setdefault("legendary", {})[skill] = int((profile.get("legendary") or {}).get(skill, 0)) + 1
    return None


def legendary_stars(profile) -> int:
    return sum(int(v) for v in (profile.get("legendary") or {}).values())


# ---------------------------------------------------------------------------
# The Lab Bench - brew looted ingredients into potions / one-delve elixirs. Needs
# the Alchemy Lab. Ingredients ride at risk in the satchel, so brewing rewards
# surviving the delve, not just clearing it.
# ---------------------------------------------------------------------------
def can_brew(profile, recipe_key: str) -> bool:
    r = D.RECIPES.get(recipe_key)
    if not r:
        return False
    have = profile.get("ingredients") or {}
    return all(have.get(k, 0) >= n for k, n in r["cost"].items())


def brew(profile, recipe_key: str) -> str | None:
    """Consume ingredients to brew a recipe. Returns an error line, or None on success."""
    r = D.RECIPES.get(recipe_key)
    if not r:
        return "No such recipe."
    if not home_owned(profile, "alchemy_lab"):
        return "You need an Alchemy Lab (a Breezehome upgrade) to brew."
    if not can_brew(profile, recipe_key):
        return "You lack the ingredients for that."
    store = profile["ingredients"]
    for k, n in r["cost"].items():
        store[k] -= n
        if store[k] <= 0:
            del store[k]
    makes = r["makes"]
    if makes == "potion":
        if profile["potions"] >= potion_cap(profile):
            # refund gracefully rather than waste ingredients
            for k, n in r["cost"].items():
                store[k] = store.get(k, 0) + n
            return "Your potion pockets are already full."
        profile["potions"] += 1
        return None
    # otherwise a one-delve elixir, queued for the next delve (overwrites any queued)
    effect = {"heart_delve": ("heart", 1), "soak_delve": ("soak", 10),
              "fight_delve": ("fight", 6), "crit_delve": ("crit", 6)}.get(makes)
    if effect:
        profile["nextdelve"] = {effect[0]: effect[1]}
    return None


# ---------------------------------------------------------------------------
# The Grindstone - temper weapon/armour past its tier with septims + looted
# ingredients (dragon scales at the top). A clamp-proof septim/materials sink.
# ---------------------------------------------------------------------------
def temper_cost(grade: int) -> dict:
    """Cost to go from `grade` to grade+1 (shared by weapon and armour)."""
    idx = min(grade, len(D.TEMPER_COSTS) - 1)
    return D.TEMPER_COSTS[idx]


def temper(profile, slot: str) -> str | None:
    if slot not in ("weapon", "armour"):
        return "Temper what, exactly?"
    grade = (profile.get("temper") or {}).get(slot, 0)
    if grade >= TEMPER_MAX_GRADE:
        return "That gear is honed as fine as the Grindstone allows."
    cost = temper_cost(grade)
    if profile["septims"] < cost["septims"]:
        return f"Tempering costs {cost['septims']:,} septims - you have {profile['septims']:,}."
    have = profile.get("ingredients") or {}
    missing = [f"{n}× {D.INGREDIENTS[k]['name']}" for k, n in cost["mats"].items()
               if have.get(k, 0) < n]
    if missing:
        return "You still need " + ", ".join(missing) + "."
    profile["septims"] -= cost["septims"]
    for k, n in cost["mats"].items():
        have[k] -= n
        if have[k] <= 0:
            del have[k]
    profile.setdefault("temper", {"weapon": 0, "armour": 0})[slot] = grade + 1
    return None


# ---------------------------------------------------------------------------
# NPC Factions - swear an allegiance at Lv 8+, complete a weekly verb-task for
# favour and a stipend. Light by design (see data.FACTIONS).
# ---------------------------------------------------------------------------
def _iso_week(date_str: str = None) -> list:
    y, w, _ = datetime.date.fromisoformat(date_str or _today_str()).isocalendar()
    return [y, w]


def join_faction(profile, key: str) -> str | None:
    if key not in D.FACTIONS:
        return "No such faction."
    if level(profile) < int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8)):
        return "The great factions only take proven adventurers (level 8+)."
    if profile.get("allegiance") == key:
        return f"You already run with {D.FACTIONS[key]['name']}."
    profile["allegiance"] = key
    # reset the weekly tracker to snapshot against the new faction's stat
    profile["faction"] = {}
    faction_state(profile)
    return None


def faction_state(profile) -> dict:
    """Weekly tracker - snapshots the tracked stat at the start of each ISO week."""
    f = profile.setdefault("faction", {})
    wk = _iso_week()
    if f.get("week") != wk:
        fac = profile.get("allegiance")
        stat = D.FACTIONS[fac]["stat"] if fac in D.FACTIONS else None
        f["week"] = wk
        f["snap"] = int(profile["stats"].get(stat, 0)) if stat else 0
        f["claimed"] = False
    return f


def faction_progress(profile) -> tuple:
    """(goal, progress, done) for this week's task, or (0, 0, False) if unaffiliated."""
    fac = profile.get("allegiance")
    if fac not in D.FACTIONS:
        return (0, 0, False)
    f = faction_state(profile)
    prog = int(profile["stats"].get(D.FACTIONS[fac]["stat"], 0)) - int(f.get("snap", 0))
    goal = D.FACTIONS[fac]["goal"]
    return (goal, max(0, prog), prog >= goal)


def faction_favour(profile) -> int:
    return int((profile.get("faction") or {}).get("favour", 0))


def faction_rank(profile) -> str:
    fav = faction_favour(profile)
    return D.FACTION_RANKS[min(len(D.FACTION_RANKS) - 1, fav // 2)]


def claim_faction(profile) -> str | None:
    goal, prog, done = faction_progress(profile)
    if profile.get("allegiance") not in D.FACTIONS:
        return "You owe no faction your allegiance yet."
    if not done:
        return f"The week's work isn't finished ({prog}/{goal})."
    f = faction_state(profile)
    if f.get("claimed"):
        return "You've already claimed this week's favour. Come back next week."
    f["claimed"] = True
    f["favour"] = faction_favour(profile) + 1
    rank_i = min(len(D.FACTION_RANKS) - 1, f["favour"] // 2)
    reward = _septims(profile, 400 + 150 * rank_i)
    profile["septims"] += reward
    gained, _ = add_xp(profile, 120 + 40 * rank_i)
    return f"favour +1 ({D.FACTION_RANKS[rank_i]}), +{reward} septims, +{gained} XP"


def faction_rivals(profile) -> list:
    """Deterministic NPC standings for flavour - the world feels busy with few players."""
    wk = _iso_week()
    rng = random.Random(f"skyrim-faction-{wk[0]}-{wk[1]}")
    names = list(D.FACTION_NPC_RIVALS)
    rng.shuffle(names)
    return [(n, rng.randint(2, 9)) for n in names[:3]]


# ---------------------------------------------------------------------------
# Idle Expeditions - send a housecarl on a multi-day errand, collect on open.
# ---------------------------------------------------------------------------
def _date_plus(days: int) -> str:
    return (datetime.date.fromisoformat(_today_str()) + datetime.timedelta(days=days)).isoformat()


def expedition(profile) -> dict | None:
    return profile.get("expedition")


def expedition_ready(profile) -> bool:
    e = profile.get("expedition")
    return bool(e) and _today_str() >= e["return"]


def start_expedition(profile, key: str) -> str | None:
    if key not in D.EXPEDITIONS:
        return "No such expedition."
    if profile.get("expedition"):
        return "Your housecarl is already out on an errand."
    if level(profile) < int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8)):
        return "You have no housecarl to send yet (level 8+ earns you one)."
    exp = D.EXPEDITIONS[key]
    import random as _r
    carl = D.HOUSECARLS[_r.Random(f"{profile['user_id']}-{_today_str()}").randrange(len(D.HOUSECARLS))]
    profile["expedition"] = {"key": key, "start": _today_str(),
                             "return": _date_plus(exp["days"]), "carl": carl}
    return None


EXPEDITION_LOG_SHOW = 10             # the panel shows only the latest dispatches


def _expedition_schedule(profile, e, exp) -> list:
    """The expedition's FULL dispatch schedule, deterministic from its seed:
    5-7 entries per day at sorted times between 07:30 and 21:30, lines drawn from
    the errand's pool + the common pool via seeded shuffle (recycled if a long
    trip outruns the pool). Returns [(day_no, minute_of_day, line), ...]."""
    rng = random.Random(f"skyrim-expedition-{profile['user_id']}-{e['start']}-{e['key']}")
    pool = D.EXPEDITION_LOGS.get(e["key"], []) + D.EXPEDITION_LOGS_COMMON
    order = []
    schedule = []
    for day in range(1, exp["days"] + 1):
        for minute in sorted(rng.randint(450, 1290) for _ in range(rng.randint(5, 7))):
            if not order:
                order = rng.sample(pool, len(pool))      # reshuffle when exhausted
            schedule.append((day, minute, order.pop()))
    return schedule


def expedition_log(profile, limit: int = EXPEDITION_LOG_SHOW) -> list:
    """The housecarl's away-log: every dispatch whose (seeded) send-time has passed,
    newest last, trimmed to the latest `limit`. Deterministic per expedition, so the
    story stays put between opens and simply accretes through the day. Rendered on
    open, never posted."""
    e = profile.get("expedition")
    if not e:
        return []
    exp = D.EXPEDITIONS.get(e["key"])
    if not exp:
        return []
    start = datetime.date.fromisoformat(e["start"])
    now = datetime.datetime.now(_UK)
    carl = e.get("carl", "Your housecarl")
    out = []
    for day, minute, line in _expedition_schedule(profile, e, exp):
        stamp = _UK.localize(datetime.datetime.combine(
            start + datetime.timedelta(days=day - 1),
            datetime.time(minute // 60, minute % 60)))
        if stamp <= now:
            out.append(f"Day {day} · {minute // 60:02d}:{minute % 60:02d} - "
                       f"{line.format(carl=carl)}")
    return out[-limit:] if limit else out


def collect_expedition(profile) -> str | None:
    e = profile.get("expedition")
    if not e:
        return "No expedition to collect."
    if not expedition_ready(profile):
        return f"Still out - returns {e['return']}."
    exp = D.EXPEDITIONS[e["key"]]
    septims = _septims(profile, exp["septims"])
    profile["septims"] += septims
    gained, _ = add_xp(profile, exp["xp"])
    parts = [f"+{septims} septims", f"+{gained} XP"]
    ing = None
    if exp.get("ingredient"):
        ing = exp["ingredient"]
        store = profile.setdefault("ingredients", {})
        store[ing] = store.get(ing, 0) + 1
        parts.append(f"{D.INGREDIENTS[ing]['emoji']} {D.INGREDIENTS[ing]['name']}")
    carl = e.get("carl", "Your housecarl")
    profile["expedition"] = None
    # the ledger: the last few returns, and the all-time tally
    log = profile.setdefault("exp_log", [])
    log.append({"key": e["key"], "carl": carl, "date": _today_str(),
                "septims": septims, "xp": gained, "ing": ing})
    profile["exp_log"] = log[-3:]
    tot = profile.setdefault("exp_totals", {"count": 0, "septims": 0, "xp": 0})
    tot["count"] += 1
    tot["septims"] += septims
    tot["xp"] += gained
    return f"{carl} returns from **{exp['name']}** with " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Fallen Adventurers - a death leaves a lootable corpse for the next delver there,
# and an obituary. Real deaths are used first; NPC corpses fill in for a small server.
# ---------------------------------------------------------------------------
def _graveyard() -> list:
    return load_json_file(config.SKYRIM_GRAVEYARD_FILE) or []


def record_fallen(profile, delve):
    """Add a death to the shared graveyard (kept short). Best-effort."""
    try:
        grave = _graveyard()
        grave.append({"name": profile.get("name", "an adventurer"), "loc": delve.location,
                      "room": delve.idx + 1, "satchel": int(delve.satchel), "date": _today_str(),
                      "user_id": profile.get("user_id")})
        grave = grave[-40:]                       # ephemeral history
        save_json_file(config.SKYRIM_GRAVEYARD_FILE, grave)
    except Exception:
        logger.error("skyrim: failed to record fallen adventurer", exc_info=True)


def latest_obituary() -> str | None:
    grave = _graveyard()
    if not grave:
        return None
    g = grave[-1]
    loc = D.LOCATIONS.get(g["loc"], {}).get("name", "the wilds")
    return f"⚰️ RIP **{g['name']}** - fell in {loc}, room {g['room']}, {g['satchel']:,} septims lost."


def _make_fallen_corpse(loc_key: str, rng) -> dict:
    """A corpse for a delve at loc_key: a real death here if one exists, else an NPC."""
    grave = [g for g in _graveyard() if g.get("loc") == loc_key and int(g.get("satchel", 0)) > 0]
    if grave and rng.random() < 0.7:
        g = rng.choice(grave)
        return {"name": g["name"], "satchel": min(int(g["satchel"]), 4000), "real": True}
    return {"name": rng.choice(D.NPC_FALLEN),
            "satchel": rng.randint(1, 4) * 60, "real": False}
