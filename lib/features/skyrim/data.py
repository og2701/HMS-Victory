"""Skyrim content tables - enemies, locations, events, gear, perks and flavour text.

Pure data (no discord imports) so the engine and the balance sim can both use it.
All player-facing strings live here; the engine picks lines at random so repeated
delves read differently. Numbers are percentages on a clamped success roll
(see engine.fight_pct / sneak_pct / persuade_pct and engine.ROLL_MIN/ROLL_MAX).
"""

import random

# ---------------------------------------------------------------------------
# Classes (the Guardian Stones). Weapon skill is one number under the hood; only
# its NAME differs by class, so a mage "swings" Destruction the way a warrior
# swings One-Handed.
# ---------------------------------------------------------------------------
CLASSES = {
    "warrior": {
        "name": "Warrior", "emoji": "⚔️", "stone": "The Warrior Stone",
        "weapon_skill": "One-Handed", "attack_verb": "swing",
        "start": {"weapon": 30, "sneak": 15, "speech": 15},
        "fight_aff": {"human": 8, "beast": 8, "undead": 0, "monster": 0, "construct": -4, "dragon": 0},
        "sneak_mod": -5,          # plate boots on stone floors
        "persuade_mod": 2,        # intimidation counts as charm in Skyrim
        "blurb": "Steel and stubbornness. Hits harder than anyone, sneaks like a dropped kettle.",
    },
    "mage": {
        "name": "Mage", "emoji": "🔮", "stone": "The Mage Stone",
        "weapon_skill": "Destruction", "attack_verb": "cast",
        "start": {"weapon": 32, "sneak": 18, "speech": 20},
        "fight_aff": {"human": 0, "beast": 4, "undead": 10, "monster": -4, "construct": 8, "dragon": 4},
        "sneak_mod": 5,
        "persuade_mod": 0,
        "blurb": "Fire, frost and shock. Burns the dead and shorts out the Dwemer's toys.",
    },
    "thief": {
        "name": "Thief", "emoji": "🗡️", "stone": "The Thief Stone",
        "weapon_skill": "Marksman", "attack_verb": "loose an arrow",
        "start": {"weapon": 25, "sneak": 28, "speech": 22},
        "fight_aff": {"human": 6, "beast": 4, "undead": -4, "monster": 0, "construct": -4, "dragon": 0},
        "sneak_mod": 10,
        "persuade_mod": 5,
        "blurb": "Shadows, arrows and a silver tongue. Why fight what never saw you?",
    },
}

# ---------------------------------------------------------------------------
# Enemies. fight/sneak/persuade are BASE success percentages before skills,
# gear, class affinity and perks. persuade=None means it cannot be reasoned
# with. `hint` is whispered one room ahead so shouts can be saved for trouble.
# ---------------------------------------------------------------------------
ENEMIES = {
    "skeever": {
        "name": "Skeever", "emoji": "🐀", "type": "beast", "tier": 1,
        "fight": 62, "sneak": 48, "persuade": None, "art": "skeever",
        "hint": "Tiny claws skitter across the stone ahead...",
        "intro": ["A **Skeever** darts out of a crack in the wall, teeth bared.",
                  "Something furry and hideous scurries into your path - a **Skeever**."],
        "kill": ["You put it down before it can bite. Filthy things.",
                 "One squeak and it's over."],
        "wound": ["It gets a bite in - those teeth carry gods-know-what.",
                  "It scurries up your leg and bites hard before you shake it off."],
    },
    "wolf": {
        "name": "Wolf", "emoji": "🐺", "type": "beast", "tier": 1,
        "fight": 58, "sneak": 42, "persuade": None, "art": "wolf",
        "hint": "A low growl rolls out of the dark ahead...",
        "intro": ["A **Wolf** slinks from the shadows, hackles raised.",
                  "Yellow eyes catch the torchlight - a **Wolf**, and it's hungry."],
        "kill": ["The wolf drops mid-lunge. The den goes quiet.",
                 "It won't be howling at anything again."],
        "wound": ["It closes the distance faster than you'd like - fangs find your arm.",
                  "The wolf tears at your leg before you drive it back."],
    },
    "bandit": {
        "name": "Bandit", "emoji": "🗡️", "type": "human", "tier": 1,
        "fight": 58, "sneak": 55, "persuade": 45, "art": "bandit",
        "hint": "Someone ahead is complaining about guard shifts...",
        "intro": ["A **Bandit** steps out from behind a pillar. \"Should've paid the toll.\"",
                  "\"Well, well. Wandered into the wrong cave, friend.\" A **Bandit** draws steel."],
        "kill": ["He fights like he was trained by a mudcrab. It's over quickly.",
                 "You leave him where better men have fallen."],
        "wound": ["His blade bites your shoulder - sloppy of you.",
                  "He lands a lucky slash and laughs about it."],
        "persuaded": ["\"...fine. You never saw me, I never saw you.\" He waves you past.",
                      "You mention you know the Jarl. He suddenly remembers urgent business elsewhere."],
    },
    "draugr": {
        "name": "Draugr", "emoji": "🧟", "type": "undead", "tier": 2,
        "fight": 46, "sneak": 65, "persuade": None, "art": "draugr",
        "hint": "A dry rasp echoes from the crypts ahead...",
        "intro": ["A **Draugr** lurches from its alcove, eyes burning blue.",
                  "The sarcophagus lid grinds open - a **Draugr** rises, ancient blade in hand."],
        "kill": ["The blue light gutters out. It can rest properly now.",
                 "Dust and old bones. Whatever held it together lets go."],
        "wound": ["Its ancient blade is sharper than it has any right to be.",
                  "Cold fingers rake you - the chill sinks into the wound."],
    },
    "frostbite_spider": {
        "name": "Frostbite Spider", "emoji": "🕷️", "type": "beast", "tier": 2,
        "fight": 46, "sneak": 45, "persuade": None, "art": "spider",
        "hint": "Webs thicken between the pillars ahead...",
        "intro": ["A **Frostbite Spider** drops from the ceiling. Of course it does.",
                  "Egg sacs. Webs. And then the **Frostbite Spider** they belong to."],
        "kill": ["It curls up with a hiss. You will burn the webs on the way out.",
                 "Eight legs, zero survivors. You hate this part of Skyrim."],
        "wound": ["Venom burns where its fangs graze you.",
                  "It spits - you dodge most of it. Most."],
    },
    "necromancer": {
        "name": "Necromancer", "emoji": "🧙", "type": "human", "tier": 2,
        "fight": 46, "sneak": 60, "persuade": 32, "art": "necromancer",
        "hint": "Purple light flickers under the door ahead, and someone is chanting...",
        "intro": ["A **Necromancer** looks up from a ritual circle. \"A fresh subject volunteers.\"",
                  "\"You interrupt my work?\" The **Necromancer**'s hands crackle with purple light."],
        "kill": ["His own thralls do not mourn him.",
                 "The ritual circle makes a fitting resting place."],
        "wound": ["A bolt of dark magic sears past your guard.",
                  "Ice shards rip through your defences."],
        "persuaded": ["You claim to represent the College. He mutters about funding and lets you by.",
                      "\"Yes, yes, take the corridor. Do NOT touch the specimens.\""],
    },
    "troll": {
        "name": "Troll", "emoji": "🧌", "type": "monster", "tier": 3,
        "fight": 32, "sneak": 35, "persuade": None, "art": "troll",
        "hint": "Something big is breathing in the dark ahead...",
        "intro": ["A **Troll** rises from a pile of bones, three eyes blinking in the torchlight.",
                  "The smell hits first. Then the **Troll** does its best to."],
        "kill": ["It finally stops regenerating. Persistence beats regeneration.",
                 "The troll crashes down - the floor shakes."],
        "wound": ["A backhand sends you across the chamber.",
                  "Claws like farm tools tear into you."],
    },
    "hagraven": {
        "name": "Hagraven", "emoji": "🪶", "type": "monster", "tier": 3,
        "fight": 32, "sneak": 40, "persuade": None, "art": "hagraven",
        "hint": "Feathers and bones dangle from the ceiling ahead...",
        "intro": ["A **Hagraven** shrieks from her nest of twigs and trophies.",
                  "Half crow, half crone, all spite - a **Hagraven** turns to face you."],
        "kill": ["The shrieking stops. The silence is a gift.",
                 "She bursts into feathers and fury, then nothing."],
        "wound": ["Fire streams from her talons and washes over you.",
                  "Her claws open ragged lines across your arm."],
    },
    "falmer": {
        "name": "Falmer", "emoji": "👁️", "type": "monster", "tier": 3,
        "fight": 32, "sneak": 42, "persuade": None, "art": "falmer",
        "hint": "Chitin scrapes on stone somewhere ahead, and there is a clicking sound...",
        "intro": ["A **Falmer** turns its eyeless face toward you. It knows you are here.",
                  "From the fungal dark, a **Falmer** rises, blade of chaurus chitin ready."],
        "kill": ["It falls without a sound. Its kin will not find out from it.",
                 "Blind, but not blind enough to dodge that."],
        "wound": ["Its jagged blade finds you in the dark.",
                  "You forget it hunts by sound - it doesn't miss twice."],
    },
    # --- bosses ---------------------------------------------------------------
    "bandit_chief": {
        "name": "Bandit Chief", "emoji": "⚔️", "type": "human", "tier": 4, "boss": True,
        "fight": 34, "sneak": 45, "persuade": 35, "art": "bandit_chief",
        "hint": "Beyond the ramp, someone is barking orders...",
        "intro": ["The **Bandit Chief** cracks his neck. \"So you're the one thinning my crew.\"",
                  "A mountain of fur and iron stands between you and the loot - the **Bandit Chief**."],
        "kill": ["The chief falls like a felled pine. His camp is yours to pick over.",
                 "\"Impossible,\" he wheezes, and proves himself wrong."],
        "wound": ["His war axe crashes through your guard.",
                  "He fights dirtier than his whole crew combined."],
        "persuaded": ["You talk numbers. He decides you're cheaper as a friend and waves you through.",
                      "\"A cut of nothing is nothing.\" You promise him a cut of nothing. It works."],
    },
    "draugr_deathlord": {
        "name": "Draugr Deathlord", "emoji": "💀", "type": "undead", "tier": 4, "boss": True, "hp": 2,
        "fight": 26, "sneak": 55, "persuade": None, "art": "deathlord",
        "hint": "The air turns cold, and something ancient stirs behind the great door...",
        "intro": ["A **Draugr Deathlord** rises from the grand sarcophagus, ebony blade in hand.",
                  "The **Draugr Deathlord** speaks a word in a dead tongue. The candles go out."],
        "kill": ["The Deathlord collapses into ash and ancient mail. The barrow exhales.",
                 "Whatever oath kept it standing is finally paid."],
        "wound": ["FUS - the shout hurls you across the chamber.",
                  "The ebony blade bites deep. Ancient does not mean dull."],
    },
    "the_caller": {
        "name": "The Caller", "emoji": "🔮", "type": "human", "tier": 4, "boss": True, "hp": 2,
        "fight": 26, "sneak": 50, "persuade": 30, "art": "the_caller",
        "hint": "The chanting from the sanctum ahead has stopped. She knows.",
        "intro": ["**The Caller** turns slowly. \"You have disturbed enough of my work. Now you will contribute to it.\"",
                  "Wards flare across the sanctum as **The Caller** rises from her circle."],
        "kill": ["The wards die with her. The keep is just cold stone again.",
                 "\"Impossible,\" she breathes, exactly like the rest of them."],
        "wound": ["Lightning arcs from her fingers and finds you mid-step.",
                  "A ward detonates - the blast takes you off your feet."],
        "persuaded": ["You name-drop the Arch-Mage and promise to lose the paperwork. She waves you out of her sight.",
                      "\"Take the corridor and tell them NOTHING,\" she hisses. Deal."],
    },
    "dwarven_centurion": {
        "name": "Dwarven Centurion", "emoji": "🤖", "type": "construct", "tier": 4, "boss": True, "hp": 2,
        "fight": 26, "sneak": 50, "persuade": None, "art": "centurion",
        "hint": "Pistons hiss and gears grind somewhere below...",
        "intro": ["Steam vents scream as a **Dwarven Centurion** unfolds from its dock.",
                  "The **Dwarven Centurion** comes online with a sound like a falling forge."],
        "kill": ["It winds down with a long metallic sigh. The Dwemer built to last, not to win.",
                 "Gears, gyros and silence."],
        "wound": ["A steam blast scalds you through your armour.",
                  "Its hammer-arm connects. You feel like a struck bell."],
    },
    "dragon": {
        "name": "Dragon", "emoji": "🐉", "type": "dragon", "tier": 5, "boss": True, "hp": 3,
        "fight": 16, "sneak": None, "persuade": None, "art": "dragon",
        "hint": "Outside, a roar rolls across the mountains like thunder...",
        "intro": ["The sky darkens. A **Dragon** lands, and the ground buckles. **\"DOVAHKIIN!\"**",
                  "A **Dragon** wheels overhead, breath gathering. This is what you came for."],
        "kill": ["The dragon collapses, and light streams from its bones into YOU. **Soul absorbed.**",
                 "It crashes into the mountainside. Its soul burns away into yours. **Soul absorbed.**"],
        "wound": ["Dragonfire washes the ridge. You are somewhere in it.",
                  "Its tail catches you like a battering ram."],
    },
}

# ---------------------------------------------------------------------------
# Locations. rooms = total encounter slots INCLUDING the boss. min_level gates
# the option; dragon lairs additionally need SKYRIM_DRAGON_MIN_LEVEL.
# ---------------------------------------------------------------------------
LOCATIONS = {
    "embershard": {
        "name": "Embershard Mine", "emoji": "⛏️", "difficulty": "Easy", "min_level": 1,
        "rooms": 5, "events": 1, "pool": {"bandit": 5, "skeever": 2, "wolf": 2},
        "boss": "bandit_chief", "word_wall": False, "clear_septims": 50, "art": "embershard",
        "arrive": "Torchlight and iron ore. Voices echo from deeper in - the mine is claimed.",
        "desc": "A bandit-held mine near Riverwood. A gentle start.",
    },
    "halted_stream": {
        "name": "Halted Stream Camp", "emoji": "🏕️", "difficulty": "Easy", "min_level": 1,
        "rooms": 5, "events": 1, "pool": {"bandit": 5, "wolf": 3, "skeever": 1},
        "boss": "bandit_chief", "word_wall": False, "clear_septims": 55, "art": "halted_stream",
        "arrive": "A palisade of sharpened logs rings the old mine. Poachers, by the mammoth bones.",
        "desc": "A fortified poacher camp north of Whiterun.",
    },
    "cragslane": {
        "name": "Cragslane Cavern", "emoji": "🕳️", "difficulty": "Easy", "min_level": 2,
        "rooms": 5, "events": 1, "pool": {"wolf": 4, "skeever": 3, "bandit": 3},
        "boss": "bandit_chief", "word_wall": False, "clear_septims": 60, "art": "cragslane",
        "arrive": "Cages line the walls. Someone has been running pit fights down here.",
        "desc": "A cave of wolf-pit gamblers and their stock.",
    },
    "bleak_falls": {
        "name": "Bleak Falls Barrow", "emoji": "🏔️", "difficulty": "Medium", "min_level": 3,
        "rooms": 6, "events": 2, "pool": {"draugr": 5, "skeever": 2, "frostbite_spider": 3, "bandit": 1},
        "boss": "draugr_deathlord", "word_wall": True, "clear_septims": 90, "art": "bleak_falls",
        "arrive": "Wind howls through the standing arches. The dead of Skyrim were not buried to rest.",
        "desc": "The classic Nordic barrow above Riverwood. Draugr and webs.",
    },
    "fellglow": {
        "name": "Fellglow Keep", "emoji": "🏰", "difficulty": "Medium", "min_level": 4,
        "rooms": 6, "events": 2, "pool": {"necromancer": 5, "skeever": 2, "draugr": 2},
        "boss": "the_caller", "word_wall": False, "clear_septims": 100, "art": "fellglow",
        "arrive": "Failed College students, someone said. The purple light in the windows says failed at ethics, not magic.",
        "desc": "A ruined keep full of necromancers who left the College on bad terms.",
    },
    "chillwind": {
        "name": "Chillwind Depths", "emoji": "🦇", "difficulty": "Hard", "min_level": 6,
        "rooms": 7, "events": 2, "pool": {"frostbite_spider": 4, "falmer": 4, "troll": 2},
        "boss": "dwarven_centurion", "word_wall": False, "clear_septims": 150, "art": "chillwind",
        "arrive": "The cave swallows the daylight whole. Things live down here that have never seen it.",
        "desc": "Deep caves where the Falmer drag their catches. Bring a light.",
    },
    "labyrinthian": {
        "name": "Labyrinthian", "emoji": "🌀", "difficulty": "Hard", "min_level": 7,
        "rooms": 7, "events": 2, "pool": {"draugr": 4, "troll": 3, "hagraven": 2, "frostbite_spider": 2},
        "boss": "draugr_deathlord", "word_wall": True, "clear_septims": 170, "art": "labyrinthian",
        "arrive": "A city of the dead, older than the Empire. Even the wind sounds like a warning here.",
        "desc": "The great ruin of the ancient mages. Nothing gentle lives here.",
    },
    "ancients_ascent": {
        "name": "Ancient's Ascent", "emoji": "🐉", "difficulty": "DRAGON", "min_level": 8,
        "rooms": 4, "events": 1, "pool": {"wolf": 3, "troll": 2, "hagraven": 2},
        "boss": "dragon", "word_wall": True, "clear_septims": 220, "art": "dragon_lair",
        "arrive": "Bones litter the ledge - elk, mammoth, and some you choose not to identify. Above, wings.",
        "desc": "A dragon roosts at the peak. This is a terrible idea. Go on then.",
        "dragon_lair": True,
    },
    "mount_anthor": {
        "name": "Mount Anthor", "emoji": "🌋", "difficulty": "DRAGON", "min_level": 10,
        "rooms": 4, "events": 1, "pool": {"troll": 3, "falmer": 2, "hagraven": 2},
        "boss": "dragon", "word_wall": True, "clear_septims": 260, "art": "dragon_lair",
        "arrive": "The wind up here could flay paint from a shield. Something answers it, roar for roar.",
        "desc": "A high peak in Winterhold, and the dragon that claims it.",
        "dragon_lair": True,
    },
}

# ---------------------------------------------------------------------------
# Events (non-combat rooms). Weight is the draw weight within a location's
# event slots; wordwall only spawns where the location allows it.
# ---------------------------------------------------------------------------
EVENTS = {
    "chest": {"weight": 5, "emoji": "🧰", "art": "chest",
              "text": "An old chest sits half-buried in the rubble, lid ajar just enough to tease."},
    "sweetroll": {"weight": 2, "emoji": "🍩", "art": "sweetroll",
                  "text": "On a stone pedestal, in a shaft of light: a **sweetroll**. Untouched. Suspicious."},
    "shrine": {"weight": 3, "emoji": "🙏", "art": "shrine",
               "text": "A small shrine of Talos, hidden from Thalmor eyes. The offering bowl is dusty."},
    "satchel": {"weight": 3, "emoji": "🧪", "art": "satchel",
                "text": "An alchemist's satchel hangs from a skeleton's shoulder. They won't mind."},
    "maiq": {"weight": 1, "emoji": "🐱", "art": "maiq",
             "text": "A robed Khajiit sits by a small fire, entirely at ease. **M'aiq the Liar** nods at you."},
    "knee_trap": {"weight": 2, "emoji": "🏹", "art": "knee_trap",
                  "text": "A tripwire glints - too late."},
    "giant": {"weight": 1, "emoji": "🦣", "art": "giant",
              "text": "The passage opens onto a camp: a cookfire, painted cows, and a **Giant** leaning on a club the size of a rowboat."},
    "wordwall": {"weight": 0, "emoji": "🗣️", "art": "wordwall",   # placed, never drawn
                 "text": "A great curved wall rises out of the dark, carved edge to edge in dragon script. It is **chanting**."},
}

M_AIQ_LINES = [
    "\"M'aiq knows much, and tells some. M'aiq knows many things others do not.\"",
    "\"Lots of people wear armour. M'aiq finds it restrictive, and hard to sneak in.\"",
    "\"M'aiq once walked to High Hrothgar. So many steps. M'aiq prefers to say he did not.\"",
    "\"Dragons were never gone. They were only invisible, and very, very quiet.\"",
    "\"M'aiq is glad he carries a torch. So dark in these caves.\"",
    "\"Some people want to fight everything they meet. M'aiq finds walking around things much easier.\"",
]

GUARD_LINES = [
    "\"Let me guess - someone stole your sweetroll?\"",
    "\"I used to be an adventurer like you. Then I took an arrow in the knee.\"",
    "\"No lollygaggin'.\"",
    "\"What is it? Dragons?\"",
    "\"Everything's in order. Move along.\"",
]

INTRO_TEXT = (
    "Hey, you. You're finally awake.\n"
    "You were trying to cross the border, right? Walked right into that Imperial ambush, "
    "same as us. No headsman today though - a dragon saw to that.\n\n"
    "Skyrim is yours to take: delve its ruins, learn its words of power, and maybe - "
    "if the old blood runs in you - slay its dragons.\n\n"
    "**Choose your Guardian Stone.** It shapes how you fight, sneak and speak. Choose once."
)

DEATH_LINES = [
    "The last thing you hear is your satchel hitting the floor.",
    "Sovngarde has a fine mead hall, they say. You are about to check.",
    "You never should have come here.",
    "Skyrim belongs to the Nords. Your septims now belong to {location}.",
]

FLEE_LINES = [
    "You sprint for the entrance, loot spilling from your satchel as you run.",
    "Discretion, valour, etc. You dive out of the entrance with what you could hold.",
]

LEAVE_LINES = [
    "You slip back out into the cold air, satchel heavy.",
    "Enough for one day. The road home is downhill, at least.",
]

CLEAR_LINES = [
    "The way stands clear behind you. {location} is yours.",
    "Silence settles over {location}. You take your time with the loot.",
]

WOUND_KNEE_LINE = "An arrow skips off the stone and finds your **knee**. You know exactly what this means."

SNEAK_LINES = [
    "You melt into the shadows and slip past without a sound.",
    "One patient breath at a time, you ghost through unseen.",
    "You count the footsteps, pick your moment, and simply walk by.",
]

SPOTTED_LINES = [
    "A loose stone turns under your foot - every head snaps toward you.",
    "You hold your breath too long and cough. Wonderful.",
    "Your shadow falls exactly where you did not want it to.",
]

STAGGER_LINES = [
    "Your blow lands true - it staggers, but does not fall!",
    "A telling hit! It reels back, wounded and furious.",
]

STAGGER_DRAGON_LINES = [
    "Your strike tears through a wing membrane - the dragon SCREAMS.",
    "Scales shatter under the blow. The dragon is bleeding now.",
]

SHOUT_CLEAR_LINES = [
    "**\"{shout}!\"** The Voice hits like a falling mountain - the {enemy} is hurled across the chamber and does not get up.",
    "**\"{shout}!\"** The walls shed dust. Where the {enemy} stood, there is a dent.",
]

SHOUT_DRAGON_LINES = [
    "**\"{shout}!\"** The dragon staggers mid-wingbeat and crashes to the ground, pinned and furious.",
]

# ---------------------------------------------------------------------------
# Gear. One tier list shared by weapons and armour; armour price is scaled in
# the shop. Weapons add fight%, armour adds soak% (chance a hit is absorbed).
# Dragonbone needs dragon kills, not septims alone.
# ---------------------------------------------------------------------------
GEAR_TIERS = [
    {"key": "iron", "name": "Iron", "emoji": "🪨", "price": 0, "dragons": 0},
    {"key": "steel", "name": "Steel", "emoji": "⚙️", "price": 300, "dragons": 0},
    {"key": "elven", "name": "Elven", "emoji": "🌿", "price": 700, "dragons": 0},
    {"key": "glass", "name": "Glass", "emoji": "💚", "price": 1500, "dragons": 0},
    {"key": "ebony", "name": "Ebony", "emoji": "⬛", "price": 3000, "dragons": 0},
    {"key": "daedric", "name": "Daedric", "emoji": "😈", "price": 6000, "dragons": 0},
    {"key": "dragonbone", "name": "Dragonbone", "emoji": "🐲", "price": 9000, "dragons": 3},
]
WEAPON_FIGHT_PER_TIER = 4      # +4% attack per tier above Iron
ARMOUR_SOAK_PER_TIER = 5       # +5% chance per tier to shrug off a wound
POTION_PRICE = 40

# ---------------------------------------------------------------------------
# Perks - one point per character level, spent in the hub. `ranks` caps stacking.
# ---------------------------------------------------------------------------
PERKS = {
    "stalwart": {"name": "Stalwart Heart", "emoji": "❤️", "ranks": 2,
                 "desc": "+1 max heart per rank."},
    "honed_edge": {"name": "Honed Edge", "emoji": "⚔️", "ranks": 3,
                   "desc": "+4% attack success per rank."},
    "muffled": {"name": "Muffled Movement", "emoji": "🥷", "ranks": 2,
                "desc": "+6% sneak success per rank."},
    "persuasive": {"name": "Golden Tongue", "emoji": "💬", "ranks": 2,
                   "desc": "+7% persuade success per rank."},
    "juggernaut": {"name": "Juggernaut", "emoji": "🛡️", "ranks": 2,
                   "desc": "+6% chance per rank that armour absorbs a wound."},
    "alchemist": {"name": "Alchemist", "emoji": "🧪", "ranks": 2,
                  "desc": "+1 potion pocket per rank."},
    "deep_pockets": {"name": "Deep Pockets", "emoji": "💰", "ranks": 2,
                     "desc": "+20% septims found per rank."},
    "quick_study": {"name": "Quick Study", "emoji": "📚", "ranks": 2,
                    "desc": "+10% XP earned per rank."},
}

SHOUT_WORDS = ["FUS", "RO", "DAH"]           # each costs 1 dragon soul at a Word Wall

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def pick(lines, **fmt):
    """Random line from a pool, with optional format args."""
    line = random.choice(lines)
    return line.format(**fmt) if fmt else line


def xp_needed(level: int) -> int:
    """XP required to go from `level` to `level + 1`."""
    return 75 + 35 * (level - 1)


def level_from_xp(xp: int) -> int:
    level = 1
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
    return level


def xp_into_level(xp: int) -> tuple:
    """(xp progressed into the current level, xp needed for the next)."""
    level = 1
    while xp >= xp_needed(level):
        xp -= xp_needed(level)
        level += 1
    return xp, xp_needed(level)
