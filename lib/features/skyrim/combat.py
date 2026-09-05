"""Small, deterministic encounter rules shared by the engine and its compact UI.

No random rolls or persistence here. Encounter state belongs to the room, so
intentions, practice limits and story consequences survive resumed boards.
"""

PRACTICE_PER_ROOM = 6
HISTORY_LIMIT = 24

INTENTS = {
    "press": ("Pressing attack", "Guard risks a hit (50%) for +10% on your next attack.", None),
    "charge": ("Winding up", "Guard stops the charge; a missed attack can cost 2 HP.", "guard"),
    "channel": ("Casting a spell", "A landed Blade hit interrupts; other hits invite a reply.", "blade"),
    "exposed": ("Exposed flank", "Bow hits deal 1 extra damage before the opening closes.", "marksman"),
    "regenerate": ("Mending wounds", "Fire stops its heal; other surviving hits heal back 1 HP.", "destruction"),
    "airborne": ("Taking flight", "Bow reaches it; Shout brings it down.", "shout"),
}


def intent(room, enemy, grounded=False):
    state = room.get("combat") or {}
    turn = int(state.get("turn", 0))
    if enemy["type"] == "dragon" and not grounded:
        key = "airborne"
    elif state.get("intent") in INTENTS:
        key = state["intent"]  # authored first turn, e.g. the guided adventure
    elif enemy["type"] == "dragon":
        # Air/ground and reflight already give dragons their own weapon puzzle.
        key = ("press", "charge", "press")[turn % 3]
    elif room["key"] == "troll":
        key = ("regenerate", "press")[turn % 2]
    elif room["key"] in ("necromancer", "the_caller", "hagraven"):
        key = ("channel", "exposed")[turn % 2]
    elif enemy["tier"] >= 4 or enemy["type"] == "construct":
        # First contact keeps the familiar odds; the next exchange reveals a windup.
        key = ("press", "charge", "exposed")[turn % 3]
    elif enemy["type"] == "human":
        key = ("press", "exposed")[turn % 2]
    else:
        key = ("press", "charge")[turn % 2]
    label, hint, counter = INTENTS[key]
    if state.get("opening"):
        hint = "Your guard earned +10% on the next attack. " + hint
    if state.get("guard_used") and key == "charge":
        hint = "Guard is spent; a missed attack can cost 2 HP. Strike, Shout or heal."
    return {"key": key, "label": label, "hint": hint, "counter": counter,
            "guard_available": not bool(state.get("guard_used"))}


def attack_bonus(room):
    return 10 if (room.get("combat") or {}).get("opening") else 0


def damage_bonus(intent_key, style):
    return int(intent_key == "exposed" and style == "marksman")


def advance(room):
    state = room.setdefault("combat", {})
    state["turn"] = int(state.get("turn", 0)) + 1
    state.pop("intent", None)
    state.pop("opening", None)


def practice(profile, room, skill, success, stones):
    """At most one attempt point then a normal learning award per skill/room.

    The allowance is frozen at first use: repeatedly missing, healing, switching
    weapons or reloading a board cannot increase it. Six points across all skills
    allows mixed contributions without multiplying practice by a boss's health.
    """
    if skill not in profile["skills"] or profile["skills"][skill] >= 100:
        return 0
    ledger = room.setdefault("practice", {})
    entry = ledger.setdefault(skill, {
        "cap": max(1, (100 - profile["skills"][skill]) // 25)
               + int(skill in stones[profile["stone"]]["boost"]),
        "awarded": 0,
    })
    target = int(entry["cap"]) if success else 1
    total = sum(int(v.get("awarded", 0)) for v in ledger.values())
    gain = max(0, min(target - int(entry["awarded"]), PRACTICE_PER_ROOM - total,
                      100 - profile["skills"][skill]))
    profile["skills"][skill] += gain
    entry["awarded"] += gain
    return gain


STORIES = {
    "captive": {
        "text": "A captive scout guards the captain's ransom. Free them to block one blow in the final fight.",
        "choices": [("🗝️", "Free scout", "story_help"),
                    ("💰", "Take ransom", "story_greed"),
                    ("🚶", "Pass quietly", "safe")],
        "help": {"guard": True, "note": "The freed scout will catch one blow."},
        "help_line": "You free the scout. They promise to catch one blow in the final fight.",
        "greed": 45,
        "greed_line": "You take 45 septims. The alarm reaches the captain: the final foe gains 1 HP.",
    },
    "brazier": {
        "text": "Rig the hanging brazier: the final foe starts with 1 less HP (minimum 1). A vault lies below.",
        "choices": [("🔥", "Rig brazier", "story_help"),
                    ("💎", "Search vault", "deep"),
                    ("🚶", "Pass quietly", "safe")],
        "help": {"damage": 1, "note": "Your brazier trap burns away 1 HP on entry."},
        "help_line": "You rig the chain. The brazier will hit the final foe for 1 HP, leaving at least 1.",
    },
    "runes": {
        "text": "The runes reveal the guardian's stance: study them for +10% on your first attack.",
        "choices": [("📜", "Read the runes", "story_help"),
                    ("💰", "Take offering", "story_greed"),
                    ("🚶", "Pass quietly", "safe")],
        "help": {"opening": True, "note": "The runes grant +10% on your first attack."},
        "help_line": "You learn its stance. Your first attack on the final foe gains +10%.",
        "greed": 60,
        "greed_line": "You take 60 septims. The stolen offering wakes the ward: the final foe gains 1 HP.",
    },
}


def story(room):
    if room.get("kind") != "event" or room.get("key") != "fork":
        return None
    return STORIES.get(room.get("story"))


def story_choices(room):
    found = story(room)
    return list(found["choices"]) if found else None


def story_text(room):
    found = story(room)
    if not found:
        return None
    consequence = ("Take it: extra coin, but the final foe gains 1 HP."
                   if found.get("greed") else "Vault: one extra elite and a locked chest.")
    return found["text"] + " " + consequence
