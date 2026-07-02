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
BLESSING_BONUS = 5                   # fight bonus from praying at a shrine on full hearts
HEAVY_HIT_CHANCE = {4: 0.35, 5: 0.50}   # by enemy tier: chance a wound is a crushing 2-heart blow
FIGHT_SKILL_SCALE = 24               # max % a skill adds at 100 (fight)
SNEAK_SKILL_SCALE = 22               # (sneak)
SPEECH_SKILL_SCALE = 30              # (persuade)
CRIT_CHANCE = 0.08                   # clean-strike chance: double damage, double loot on the kill
BOUNTY_CHANCE = 0.06                 # per trash room: a named variant (+1 hp, 3x loot, 2x XP)
DAILY_CLEAR_MULT = 1.5               # the daily delve pays a fatter clear bonus


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
# Profiles
# ---------------------------------------------------------------------------
def _profiles() -> dict:
    return load_json_file(config.SKYRIM_PROFILES_FILE) or {}


def get_profile(user_id) -> dict | None:
    return _profiles().get(str(user_id))


def save_profile(profile: dict):
    store = _profiles()
    store[str(profile["user_id"])] = profile
    save_json_file(config.SKYRIM_PROFILES_FILE, store)


def all_profiles() -> dict:
    return _profiles()


def create_profile(user_id, name: str, class_key: str) -> dict:
    cls = D.CLASSES[class_key]
    profile = {
        "user_id": int(user_id),
        "name": name,
        "class": class_key,
        "xp": 0,
        "skills": dict(cls["start"]),            # weapon / sneak / speech
        "perks": {},                             # perk key -> ranks taken
        "septims": 0,
        "potions": 2,           # kind start: a full belt for the first delve or two
        "weapon_tier": 0,
        "armour_tier": 0,
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
    return BASE_HEARTS + perk_rank(profile, "stalwart")


def potion_cap(profile) -> int:
    return BASE_POTION_CAP + perk_rank(profile, "alchemist")


def weapon_skill_name(profile) -> str:
    return D.CLASSES[profile["class"]]["weapon_skill"]


def gear_name(profile, slot: str) -> str:
    tier = D.GEAR_TIERS[profile[f"{slot}_tier"]]
    kind = "sword" if slot == "weapon" else "armour"
    if slot == "weapon":
        kind = {"warrior": "sword", "mage": "staff", "thief": "bow"}[profile["class"]]
    return f"{tier['emoji']} {tier['name']} {kind}"


def _skill_component(skill: int, scale: int) -> float:
    return scale * (skill - 15) / 85.0


def _clamp(p: float) -> int:
    return int(max(ROLL_MIN, min(ROLL_MAX, round(p))))


def fight_pct(profile, enemy_key: str, delve=None) -> int:
    e = D.ENEMIES[enemy_key]
    cls = D.CLASSES[profile["class"]]
    p = (e["fight"]
         + _skill_component(profile["skills"]["weapon"], FIGHT_SKILL_SCALE)
         + D.WEAPON_FIGHT_PER_TIER * profile["weapon_tier"]
         + cls["fight_aff"].get(e["type"], 0)
         + 4 * perk_rank(profile, "honed_edge")
         + weather_today()["fight"])
    if delve is not None:
        if delve.grounded and e["type"] == "dragon":
            p += GROUNDED_BONUS
        if delve.blessed:
            p += BLESSING_BONUS
    return _clamp(p)


def sneak_pct(profile, enemy_key: str) -> int | None:
    e = D.ENEMIES[enemy_key]
    if e["sneak"] is None:
        return None
    cls = D.CLASSES[profile["class"]]
    p = (e["sneak"]
         + _skill_component(profile["skills"]["sneak"], SNEAK_SKILL_SCALE)
         + cls["sneak_mod"]
         + 6 * perk_rank(profile, "muffled")
         + weather_today()["sneak"])
    return _clamp(p)


def persuade_pct(profile, enemy_key: str) -> int | None:
    e = D.ENEMIES[enemy_key]
    if e.get("persuade") is None:
        return None
    cls = D.CLASSES[profile["class"]]
    p = (e["persuade"]
         + _skill_component(profile["skills"]["speech"], SPEECH_SKILL_SCALE)
         + cls["persuade_mod"]
         + 7 * perk_rank(profile, "persuasive"))
    return _clamp(p)


def soak_pct(profile) -> int:
    return min(SOAK_CAP, D.ARMOUR_SOAK_PER_TIER * profile["armour_tier"]
               + 6 * perk_rank(profile, "juggernaut"))


def _skill_up(profile, which: str) -> int:
    """Improve a skill by use (fast early, slow late). Returns the gain."""
    cur = profile["skills"][which]
    gain = max(1, (100 - cur) // 25) if cur < 100 else 0
    profile["skills"][which] = min(100, cur + gain)
    return gain


def add_xp(profile, amount: int) -> tuple:
    """Bank XP (Quick Study + weather apply). Returns (gained, levels_gained)."""
    amount = int(round(amount * (1 + 0.10 * perk_rank(profile, "quick_study"))
                       * weather_today()["xp"]))
    before = level(profile)
    profile["xp"] += amount
    return amount, level(profile) - before


def _septims(profile, amount: int) -> int:
    """Scale a septim find by Deep Pockets and the day's weather."""
    return int(round(amount * (1 + 0.20 * perk_rank(profile, "deep_pockets"))
                     * weather_today()["loot"]))


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


def build_rooms(loc_key: str, rng=None) -> list:
    """Room list for a fresh delve: shuffled trash + events, optional word wall,
    boss last. Each room: {kind, key, boss, resolved} (+ bounty on rare named
    variants). Pass a seeded rng for the shared daily layout."""
    rng = rng or random
    loc = D.LOCATIONS[loc_key]
    n_fill = loc["rooms"] - 1
    n_events = min(loc["events"], n_fill - 1)      # always at least one trash fight
    enemy_keys = list(loc["pool"].keys())
    enemy_weights = list(loc["pool"].values())
    rooms = []
    for _ in range(n_fill - n_events):
        room = {"kind": "enemy", "key": rng.choices(enemy_keys, weights=enemy_weights, k=1)[0],
                "boss": False, "resolved": False}
        if rng.random() < BOUNTY_CHANCE:
            room["bounty"] = True                  # a named variant: +1 hp, triple loot
        rooms.append(room)
    rooms += [{"kind": "event", "key": k, "boss": False, "resolved": False}
              for k in _draw_events(n_events, rng)]
    rng.shuffle(rooms)
    if loc.get("word_wall"):
        rooms.append({"kind": "event", "key": "wordwall", "boss": False, "resolved": False})
    rooms.append({"kind": "enemy", "key": loc["boss"], "boss": True, "resolved": False})
    return rooms


def offer_locations(profile) -> list:
    """Up to three destinations suited to the character's level: the easiest thing
    still worth doing, something on-level, and the most dangerous thing unlocked.
    Skuldafn is never offered here - the picker adds it via alduin_available."""
    lvl = level(profile)
    dragon_min = int(getattr(config, "SKYRIM_DRAGON_MIN_LEVEL", 8))
    open_locs = [k for k, v in D.LOCATIONS.items()
                 if not v.get("alduin")
                 and lvl >= v["min_level"] and (not v.get("dragon_lair") or lvl >= dragon_min)]
    open_locs.sort(key=lambda k: D.LOCATIONS[k]["min_level"])
    if len(open_locs) <= 3:
        return open_locs
    picks = [open_locs[0], open_locs[len(open_locs) // 2], open_locs[-1]]
    # de-dup while keeping order (possible when few locations are open)
    seen, out = set(), []
    for p in picks:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _room_hp(room: dict) -> int:
    """Hits the room's enemy can take: its base hp, +1 for a bounty variant."""
    if room["kind"] != "enemy":
        return 1
    return D.ENEMIES[room["key"]].get("hp", 1) + (1 if room.get("bounty") else 0)


# ---------------------------------------------------------------------------
# The Delve
# ---------------------------------------------------------------------------
class Delve:
    """One dungeon run. All rolls happen here; views only render and route."""

    def __init__(self, player_id, player_name, channel_id, location, rooms, *,
                 idx=0, hearts=None, satchel=0, shout_charges=None, engaged=False,
                 spotted=False, grounded=False, blessed=False, state="playing",
                 log=None, message_id=None, xp_gained=0, kills=0, result_line="",
                 delve_id=None, enemy_hp=None, daily=False, fan=False):
        import uuid
        self.delve_id = delve_id or uuid.uuid4().hex[:12]
        self.daily = bool(daily)                  # the shared once-a-day dungeon
        self.fan = bool(fan)                      # the Adoring Fan absorbs one wound
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
        # bounty variants +1)
        if enemy_hp is None:
            r = self.rooms[self.idx] if self.rooms else None
            enemy_hp = _room_hp(r) if r else 1
        self.enemy_hp = int(enemy_hp)
        self.busy = False                         # transient: drop double-clicks

    # --- construction ---------------------------------------------------------
    @classmethod
    def start(cls, profile, channel_id, loc_key):
        loc = D.LOCATIONS[loc_key]
        d = cls(profile["user_id"], profile["name"], channel_id, loc_key, build_rooms(loc_key),
                hearts=heart_max(profile), shout_charges=profile["words"])
        d.say(loc["arrive"])
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
        """Whisper what waits in the NEXT room (enemies only) so shouts can be planned."""
        j = self.idx + 1
        if self.state == "playing" and j < len(self.rooms) and self.rooms[j]["kind"] == "enemy":
            return D.ENEMIES[self.rooms[j]["key"]]["hint"]
        return None

    def playing(self) -> bool:
        return self.state == "playing"

    # --- room flow --------------------------------------------------------------
    def _advance(self, profile):
        """Step to the next room, or finish the delve if the boss room is done."""
        self.engaged = self.spotted = self.grounded = False
        if self.idx >= len(self.rooms) - 1:
            self._finish_clear(profile)
            return
        self.idx += 1
        r = self.room
        self.enemy_hp = _room_hp(r)
        if r["kind"] == "event" and r["key"] == "knee_trap":
            self._spring_knee_trap(profile)

    def _finish_clear(self, profile):
        bonus = _septims(profile, self.loc["clear_septims"])
        if self.daily:
            bonus = int(bonus * DAILY_CLEAR_MULT)
        self.satchel += bonus
        gained, _ = add_xp(profile, 25)
        self.xp_gained += gained
        profile["septims"] += self.satchel
        profile["stats"]["clears"] += 1
        profile["active_delve"] = None
        self.state = "cleared"
        self.result_line = (f"Cleared! Banked **{self.satchel:,} septims** "
                            f"(including a {bonus:,} haul from the final chamber) and "
                            f"**{self.xp_gained} XP**.")
        self.say(D.pick(D.CLEAR_LINES, location=self.loc["name"]))

    def _wound(self, profile, lines, knee_chance=0.0, heavy=0.0) -> str:
        """Take a hit: armour may soak it, the Adoring Fan may take it for you,
        otherwise lose a heart (death at 0). `heavy` is the chance of a crushing
        2-heart blow. Returns 'soaked' | 'wounded' | 'dead'."""
        if random.random() * 100 < soak_pct(profile):
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
        if self.hearts <= 0:
            self._die(profile)
            return "dead"
        return "wounded"

    def _die(self, profile):
        profile["stats"]["deaths"] += 1
        profile["active_delve"] = None
        self.state = "dead"
        lost = self.satchel
        self.result_line = (f"**You died.** The satchel - **{lost:,} septims** - stays in "
                            f"{self.loc['name']}. Your XP, gear and souls are safe.")
        self.say(D.pick(D.DEATH_LINES, location=self.loc["name"]))

    # --- enemy actions ------------------------------------------------------------
    def _heavy(self, e) -> float:
        """Chance this enemy's hit is a crushing 2-heart blow (enemy override or
        tier default, plus the day's weather)."""
        base = e.get("heavy", HEAVY_HIT_CHANCE.get(e["tier"], 0.0))
        return base + weather_today()["heavy"]

    def act_attack(self, profile) -> None:
        e = self.enemy()
        p = fight_pct(profile, self.room["key"], self)
        if random.random() * 100 < p:
            crit = random.random() < CRIT_CHANCE
            self.enemy_hp -= 2 if crit else 1
            if self.enemy_hp > 0:
                # a big foe takes the hit and keeps coming - the fight is on
                self.engaged = True
                lines = D.STAGGER_DRAGON_LINES if e["type"] == "dragon" else D.STAGGER_LINES
                line = (D.pick(D.CRIT_LINES) + "  " if crit else "") + D.pick(lines)
                self.say(line + f"  ({'🩸' * self.enemy_hp} to go)")
                # Alduin takes wing again at set thresholds - ground him anew or
                # fight at a heavy disadvantage. His fight is a war over shouts.
                if self.room["key"] == "alduin" and self.enemy_hp in D.ALDUIN_REFLIGHT_HP \
                        and self.grounded:
                    self.grounded = False
                    self.say("**Alduin takes wing again**, laughing in the old tongue. "
                             "Bring him down!")
            else:
                self._kill(profile, e, crit=crit)
        else:
            self.engaged = True
            self._wound(profile, e["wound"], knee_chance=0.10 if e["type"] == "human" else 0.0,
                        heavy=self._heavy(e))

    def _kill(self, profile, e, crit=False):
        gain = _skill_up(profile, "weapon")
        tier = e["tier"]
        bounty = bool(self.room.get("bounty"))
        xp = DRAGON_KILL_XP if e["type"] == "dragon" else 12 * tier
        if bounty:
            xp *= 2
        gained, ups = add_xp(profile, xp)
        self.xp_gained += gained
        loot = _septims(profile, tier * 12 + random.randint(0, 8))
        if bounty:
            loot *= 3
        if crit:
            loot *= 2
        self.satchel += loot
        self.kills += 1
        profile["stats"]["kills"] += 1
        line = ""
        if crit:
            line += D.pick(D.CRIT_LINES) + "  "
        line += f"{D.pick(e['kill'])}  (+{gained} XP, +{loot} septims"
        if bounty:
            line += ", bounty claimed"
        if gain:
            line += f", {weapon_skill_name(profile)} +{gain}"
        line += ")"
        if e["type"] == "dragon":
            profile["souls"] += 1
            profile["stats"]["dragons"] += 1
            line += "  🐉 **+1 dragon soul**"
        if self.room["key"] == "alduin":
            profile["alduin_slain"] = profile.get("alduin_slain", 0) + 1
        if ups:
            line += f"\n🆙 **Level up! You are now level {level(profile)}** (+{ups} perk point)."
        self.say(line)
        self._advance(profile)

    def act_sneak(self, profile) -> None:
        e = self.enemy()
        p = sneak_pct(profile, self.room["key"])
        if p is None or self.engaged or self.spotted:
            return
        if random.random() * 100 < p:
            gain = _skill_up(profile, "sneak")
            gained, ups = add_xp(profile, 8 * e["tier"])
            self.xp_gained += gained
            profile["stats"]["sneaks"] += 1
            line = f"{D.pick(D.SNEAK_LINES)}  (+{gained} XP"
            if gain:
                line += f", Sneak +{gain}"
            line += ")"
            if ups:
                line += f"\n🆙 **Level up! You are now level {level(profile)}** (+{ups} perk point)."
            self.say(line)
            self._advance(profile)
        else:
            self.spotted = True
            self.engaged = True
            self.say(D.pick(D.SPOTTED_LINES))
            self._wound(profile, e["wound"], heavy=self._heavy(e))

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

    def act_shout(self, profile) -> None:
        if self.shout_charges <= 0 or profile["words"] <= 0:
            return
        e = self.enemy()
        if e is None:
            return
        if e["type"] == "dragon" and self.grounded:
            return                       # already grounded - don't waste the charge
        shout = " ".join(D.SHOUT_WORDS[:profile["words"]])
        self.shout_charges -= 1
        if e["type"] == "dragon":
            self.grounded = True
            self.say(D.pick(D.SHOUT_DRAGON_LINES, shout=shout))
        else:
            self.say(D.pick(D.SHOUT_CLEAR_LINES, shout=shout, enemy=e["name"]))
            # a shouted room still yields its loot - the Voice is not subtle but it is thorough
            loot = _septims(profile, e["tier"] * 12 + random.randint(0, 8))
            self.satchel += loot
            gained, _ = add_xp(profile, 6 * e["tier"])
            self.xp_gained += gained
            self.say(f"You pick through the wreckage.  (+{gained} XP, +{loot} septims)")
            self._advance(profile)

    def act_potion(self, profile) -> None:
        if profile["potions"] <= 0 or self.hearts >= heart_max(profile):
            return
        profile["potions"] -= 1
        self.hearts += 1
        self.say("You drink a health potion. The wound knits before your eyes.  ❤️ +1")

    def act_leave(self, profile) -> None:
        """Leave with the satchel; mid-fight it becomes a flee and loot spills."""
        if not self.playing():
            return
        profile["active_delve"] = None
        if self.engaged:
            kept = int(self.satchel * FLEE_KEEP)
            profile["septims"] += kept
            profile["stats"]["flees"] += 1
            self.state = "fled"
            self.result_line = (f"You fled mid-fight - **{kept:,} septims** made it home, "
                                f"the rest spilled behind you. **{self.xp_gained} XP** banked.")
            self.say(D.pick(D.FLEE_LINES))
        else:
            profile["septims"] += self.satchel
            self.state = "left"
            self.result_line = (f"You walk out with **{self.satchel:,} septims** and "
                                f"**{self.xp_gained} XP**.")
            self.say(D.pick(D.LEAVE_LINES))

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
        if choice == "skip":
            self.say("You move on. Curiosity has killed sturdier adventurers.")
            self._advance(profile)
            return

        if key == "chest" and choice == "open":
            if random.random() < 0.25:
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
        elif key == "wordwall" and choice == "approach":
            if profile["words"] >= len(D.SHOUT_WORDS):
                self.say("The wall chants a word you already know. The Voice hums along.")
            elif profile["souls"] > 0:
                profile["souls"] -= 1
                profile["words"] += 1
                self.shout_charges += 1
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
                    profile["septims"] += self.satchel
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
                "daily": self.daily, "fan": self.fan}

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
                   daily=d.get("daily", False), fan=d.get("fan", False))


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
        date, loc_key, rng = _daily_layout()
        profile["daily"] = {"date": date}
        delve = Delve(profile["user_id"], profile["name"], channel_id, loc_key,
                      build_rooms(loc_key, rng), hearts=heart_max(profile),
                      shout_charges=profile["words"], daily=True)
        delve.say(D.LOCATIONS[loc_key]["arrive"])
    elif kind == "alduin":
        profile["alduin"] = {"date": _today_str()}
        delve = Delve.start(profile, channel_id, "skuldafn")
    else:
        spend_stamina(profile)
        delve = Delve.start(profile, channel_id, loc_key)
    profile["stats"]["delves"] += 1
    w = weather_today()
    if w["key"] != "clear":
        delve.say(weather_line(w))
    _first_delve_of_day_comforts(profile, delve)
    return delve


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
                  if not v.get("dragon_lair") and not v.get("alduin"))
    return date, rng.choice(pool), rng


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
            "name": profile["name"], "class": profile["class"], "state": delve.state,
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
def alduin_ready(profile) -> tuple:
    """(ready, requirements_line). Ready means the gates are met, regardless of
    whether today's attempt is spent."""
    need_lvl = int(getattr(config, "SKYRIM_ALDUIN_MIN_LEVEL", 20))
    need_drag = int(getattr(config, "SKYRIM_ALDUIN_MIN_DRAGONS", 5))
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
# Shop / perks / property (called from the hub views)
# ---------------------------------------------------------------------------
def home_owned(profile, key: str) -> bool:
    return key in (profile.get("home") or [])


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
