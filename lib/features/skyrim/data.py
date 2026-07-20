"""Skyrim content tables - enemies, locations, events, gear, perks and flavour text.

Pure data (no discord imports) so the engine and the balance sim can both use it.
All player-facing strings live here; the engine picks lines at random so repeated
delves read differently. Numbers are percentages on a clamped success roll
(see engine.fight_pct / sneak_pct / persuade_pct and engine.ROLL_MIN/ROLL_MAX).
"""

import random

# ---------------------------------------------------------------------------
# The Guardian Stones. Skyrim has no classes: you become what you practise.
# A stone is a BLESSING, not a cage - every skill is open to everyone; your
# stone's skills simply level faster (and start a little higher).
# (Profiles from the old class system migrate 1:1 - the keys match on purpose.)
# ---------------------------------------------------------------------------
STONES = {
    "warrior": {
        "name": "The Warrior Stone", "emoji": "⚔️",
        "boost": ["blade"],
        "start": {"blade": 30},
        "blurb": "Blades come easily to you. Everything else comes eventually.",
    },
    "mage": {
        "name": "The Mage Stone", "emoji": "🔮",
        "boost": ["destruction"],
        "start": {"destruction": 30},
        "blurb": "Destruction magic comes easily to you. Fire solves so much.",
    },
    "thief": {
        "name": "The Thief Stone", "emoji": "🗡️",
        "boost": ["marksman", "sneak", "lockpicking"],
        "start": {"marksman": 24, "sneak": 28},
        "blurb": "Bows, shadows and other people's locks come easily to you.",
    },
}

# The three ways to hurt something. Every enemy room offers all three - pick the
# tool that fits the foe, and the skill you use is the skill that grows.
STYLES = {
    "blade": {"name": "One-Handed", "emoji": "⚔️", "label": "Blade"},
    "marksman": {"name": "Marksman", "emoji": "🏹", "label": "Bow"},
    "destruction": {"name": "Destruction", "emoji": "🔥", "label": "Fire"},
}

# Style vs enemy type: the rock-paper-Skyrim layer. Arrows do little to walking
# bones, fire purges them; trolls dread flame; Dwemer plate turns arrows but
# shorts out under shock-flavoured Destruction.
STYLE_AFF = {
    "human":     {"blade": 6, "marksman": 4, "destruction": 0},
    "beast":     {"blade": 4, "marksman": 6, "destruction": 2},
    "undead":    {"blade": 0, "marksman": -5, "destruction": 10},
    "monster":   {"blade": 1, "marksman": -1, "destruction": 6},
    "construct": {"blade": -3, "marksman": -6, "destruction": 7},
    "dragon":    {"blade": 0, "marksman": 4, "destruction": 2},
}

# Derived titles - your build is what you did, not what you picked. Pairs are
# checked first (both skills must be your top two, each 35+), then the single
# top skill; fresh characters are just Adventurers.
ARCHETYPE_PAIRS = {
    frozenset(("sneak", "marksman")): "Stealth Archer",
    frozenset(("sneak", "destruction")): "Nightblade",
    frozenset(("sneak", "blade")): "Assassin",
    frozenset(("blade", "destruction")): "Spellsword",
    frozenset(("marksman", "destruction")): "Arcane Archer",
    frozenset(("blade", "marksman")): "Mercenary",
    frozenset(("speech", "lockpicking")): "Charlatan",
}
ARCHETYPE_SINGLE = {
    "blade": "Blademaster", "marksman": "Ranger", "destruction": "Pyromancer",
    "sneak": "Shadow", "speech": "Silver-Tongue", "lockpicking": "Burglar",
}

# ---------------------------------------------------------------------------
# Enemies. fight/sneak/persuade are BASE success percentages before skills,
# gear, class affinity and perks. persuade=None means it cannot be reasoned
# with. `hint` is whispered one room ahead so shouts can be saved for trouble.
# ---------------------------------------------------------------------------
ENEMIES = {
    "skeever": {
        "name": "Skeever", "emoji": "🐀", "type": "beast", "tier": 1,
        "fight": 64, "sneak": 48, "persuade": None, "art": "skeever",
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
        "fight": 60, "sneak": 42, "persuade": None, "art": "wolf",
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
        "fight": 60, "sneak": 55, "persuade": 45, "art": "bandit",
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
    "mimic": {
        "name": "Mimic", "emoji": "🧰", "type": "monster", "tier": 3,
        "fight": 42, "sneak": None, "persuade": None, "art": "mimic",
        "hint": "A chest sits a little too still in the dark ahead...",
        "intro": ["The chest sprouts a ring of teeth and lunges - a **Mimic**!",
                  "You reach for the lid and the lid reaches back. **Mimic!**"],
        "kill": ["The Mimic splinters into ordinary planks - and a very real pile of gold.",
                 "It gives up its hoard with a wooden shriek."],
        "wound": ["Wooden teeth clamp down hard.",
                  "It headbutts you with the full weight of a loaded strongbox."],
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
    # --- legends (Rumours at Belethor's) ---------------------------------------
    # Fixed brutal statlines - deliberately NOT stirred; each demands a different
    # answer. Slain once, remembered forever.
    "ebony_warrior": {
        "name": "The Ebony Warrior", "emoji": "🖤", "type": "human", "tier": 5, "boss": True,
        "hp": 6, "heavy": 0.5, "fight": 12, "sneak": None, "persuade": None,
        "art": "ebony_warrior", "shout_immune": True,
        "hint": "A lone figure in ebony waits ahead, arms folded. He has been waiting for you.",
        "intro": ["**The Ebony Warrior** rises. \"A final challenge, before Sovngarde takes me. "
                  "Do not hold back.\"",
                  "**The Ebony Warrior** draws his blade with terrible calm. \"Make it worthy.\""],
        "kill": ["He falls to one knee, smiling behind the helm. \"Sovngarde... at last.\" "
                 "You have granted a warrior his rest.",
                 "\"Well... fought...\" The Ebony Warrior dies content. Few gifts are greater."],
        "wound": ["His ebony blade finds the seam in your guard. A lesson.",
                  "He counters faster than thought. This is what a lifetime of war looks like."],
    },
    "karstaag": {
        "name": "Karstaag", "emoji": "❄️", "type": "monster", "tier": 5, "boss": True,
        "hp": 7, "heavy": 0.6, "fight": 10, "sneak": None, "persuade": None,
        "art": "karstaag", "style_gate": {"destruction": -40},
        "hint": "The cold ahead is wrong - old, angry, and aware of you.",
        "intro": ["**KARSTAAG** crashes through the ice wall, frost boiling off his hide. "
                  "The king of trolls has been dead before. It didn't take.",
                  "The mist parts around a mountain that moves. **KARSTAAG** has your scent."],
        "kill": ["Karstaag shatters like a glacier calving - a roar, then avalanche, then silence.",
                 "The frost king falls, and the valley's cold lifts like a held breath released."],
        "wound": ["A fist of ice the size of a cart door hammers you flat.",
                  "His frost closes over you like a fist. Your bones ache for days ahead."],
    },
    "naaslaarum": {
        "name": "Naaslaarum", "emoji": "🐉", "type": "dragon", "tier": 5, "hp": 4,
        "heavy": 0.5, "fight": 10, "sneak": None, "persuade": None, "art": "vale_dragon",
        "hint": "Beneath the frozen lake, something vast is circling.",
        "intro": ["The lake EXPLODES - **Naaslaarum** erupts through the ice in a pillar of "
                  "frost and fury.",
                  "**Naaslaarum** breaches like a whale of scale and hate, ice sheeting off her wings."],
        "kill": ["Naaslaarum crashes through the ice and does not rise. Somewhere above, "
                 "her twin SCREAMS.",
                 "Her soul tears free - and the sky answers with a scream of grief and rage."],
        "wound": ["Frost breath turns the world white. You are somewhere in the white.",
                  "Her dive shatters the ice beneath you both."],
    },
    "voslaarum": {
        "name": "Voslaarum", "emoji": "🐲", "type": "dragon", "tier": 5, "boss": True,
        "hp": 5, "heavy": 0.6, "fight": 8, "sneak": None, "persuade": None,
        "art": "vale_dragon", "reflight": (3,),
        "hint": "The second shadow under the ice has stopped circling. It is coming up.",
        "intro": ["**VOSLAARUM** lands where his twin fell, and the glacier cracks to the "
                  "horizon. He is not here to duel. He is here to avenge.",
                  "**VOSLAARUM** descends screaming her name. There will be no parley."],
        "kill": ["The twins lie still beneath the vale. The oldest silence returns to the lake.",
                 "Voslaarum falls beside his twin. Even his soul comes to you grieving."],
        "wound": ["Grief makes him savage - the tail sweep catches you mid-dodge.",
                  "**\"FO KRAH DIIN!\"** The vale itself seems to freeze with his breath."],
    },
    "alduin": {
        "name": "Alduin", "emoji": "🌑", "type": "dragon", "tier": 5, "boss": True, "hp": 8,
        "heavy": 0.6, "retaliate": 0.3, "fight": 6, "sneak": None, "persuade": None, "art": "alduin",
        "hint": "The sky itself is wrong up there. He is waiting.",
        "intro": ["**ALDUIN** descends through a burning sky. **\"Zu'u lost daal. I have returned.\"**",
                  "The World-Eater lands, and the temple groans under him. **ALDUIN** turns his gaze on you."],
        "kill": ["Alduin unravels into burning threads of light, screaming his refusal into the void. "
                 "The sky clears. **The World-Eater is undone.**",
                 "\"Dovahkiin... you cannot...\"  You can. You did. **Alduin is no more.**"],
        "wound": ["**\"YOL TOOR SHUL!\"** A wall of dragonfire swallows the terrace - and you with it.",
                  "His tail sweep hits like a falling longhouse.",
                  "**\"FUS RO DAH!\"** The World-Eater Shouts back, and the world obliges him."],
    },
}

# Alduin takes wing again at these hp thresholds - he must be grounded with a
# shout each time, so the fight is a war over your shout charges.
ALDUIN_REFLIGHT_HP = (6, 4, 2)

# ---------------------------------------------------------------------------
# Named Dragons of the Week. Both dragon lairs share ONE named dragon per UK week,
# chosen deterministically (see engine.dragon_of_the_week) - the same for everyone,
# rotating every Monday. Each is the base Dragon (tier 5, 3 hp, 16 fight) with a
# small delta and a twist, so the road to Alduin stops being ten identical fights.
# A per-character Dragon Wall records first kills. Reuses the existing dragon art.
#   hp/fight     - deltas on the base dragon;  crush - added crushing-blow chance
#   breath       - flavour word;  twist - one-line hook shown in the intro
# ---------------------------------------------------------------------------
DRAGON_ROSTER = {
    "mirmulnir":  {"name": "Mirmulnir", "breath": "YOL TOOR SHUL", "hp": 0, "fight": 4,
                   "crush": 0.0, "twist": "The first dragon of the new age - and the most straightforward."},
    "sahloknir":  {"name": "Sahloknir", "breath": "FO KRAH DIIN", "hp": 0, "fight": 0,
                   "crush": 0.10, "twist": "Raised from the dead once already - its frost breath bites to the bone."},
    "vuljotnaak": {"name": "Vuljotnaak", "breath": "YOL TOOR SHUL", "hp": 1, "fight": 2,
                   "crush": 0.0, "twist": "An old, earthbound brute - thick-scaled and slow to fall."},
    "nahagliiv":  {"name": "Nahagliiv", "breath": "FO KRAH DIIN", "hp": 0, "fight": -2,
                   "crush": 0.15, "twist": "Reckless and furious - it hits like a rockslide but overreaches."},
    "viinturuth": {"name": "Viinturuth", "breath": "YOL TOOR SHUL", "hp": 1, "fight": 0,
                   "crush": 0.08, "twist": "A conjured guardian, grimly patient. It does not tire."},
    "kruziikrel": {"name": "Kruziikrel", "breath": "FO KRAH DIIN", "hp": 0, "fight": 3,
                   "crush": 0.0, "twist": "A Dragon Cultist's pet - eager, but never fully wild."},
    "relonikiv":  {"name": "Relonikiv", "breath": "YOL TOOR SHUL", "hp": 0, "fight": 5,
                   "crush": 0.0, "twist": "Young and impatient. It burns hot and drops fast."},
    "vulthuryol": {"name": "Vulthuryol", "breath": "YOL TOOR SHUL", "hp": 2, "fight": -2,
                   "crush": 0.05, "twist": "Roused from the deep dark of Blackreach - vast, and slow to notice pain."},
    "naaslaarum": {"name": "Naaslaarum", "breath": "FO KRAH DIIN", "hp": 1, "fight": 0,
                   "crush": 0.12, "twist": "One of a hunting pair - it fights like it expects a sister at your back."},
    "odahviing":  {"name": "Odahviing", "breath": "YOL TOOR SHUL", "hp": 2, "fight": 2,
                   "crush": 0.10, "twist": "Proud, canny, and genuinely dangerous - the finest flier on the roster."},
    "paarthurnax_kin": {"name": "Voslaarum", "breath": "FO KRAH DIIN", "hp": 2, "fight": 0,
                        "crush": 0.15, "twist": "Ancient and cold-hearted - the closest thing to Alduin you'll meet at a lair."},
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
    "redorans_retreat": {
        "name": "Redoran's Retreat", "emoji": "🦊", "difficulty": "Easy", "min_level": 1,
        "rooms": 5, "events": 1, "pool": {"bandit": 6, "skeever": 2, "wolf": 2},
        "boss": "bandit_chief", "word_wall": False, "clear_septims": 55, "art": "redorans",
        "arrive": "A small cave, a big fire, and voices splitting yesterday's takings.",
        "desc": "A modest bandit hideaway on the tundra's edge.",
    },
    "white_river": {
        "name": "White River Watch", "emoji": "🏹", "difficulty": "Easy", "min_level": 2,
        "rooms": 5, "events": 1, "pool": {"bandit": 5, "wolf": 4, "skeever": 1},
        "boss": "bandit_chief", "word_wall": False, "clear_septims": 60, "art": "white_river",
        "arrive": "A lookout who cannot see far, and stairs cut into the river cliff.",
        "desc": "A bandit cave above the White River falls.",
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
    "silent_moons": {
        "name": "Silent Moons Camp", "emoji": "🌙", "difficulty": "Medium", "min_level": 3,
        "rooms": 6, "events": 2, "pool": {"bandit": 5, "necromancer": 3, "wolf": 2},
        "boss": "the_caller", "word_wall": False, "clear_septims": 90, "art": "silent_moons",
        "arrive": "A ruined forge under the open sky. The anvils only sing when the moons are up.",
        "desc": "Bandits squatting on an old lunar forge - and something stranger with them.",
    },
    "hillgrunds_tomb": {
        "name": "Hillgrund's Tomb", "emoji": "⚰️", "difficulty": "Medium", "min_level": 4,
        "rooms": 6, "events": 2, "pool": {"draugr": 6, "frostbite_spider": 2, "skeever": 2},
        "boss": "draugr_deathlord", "word_wall": False, "clear_septims": 100, "art": "hillgrund",
        "arrive": "A family crypt with the door forced from the inside. Wonderful.",
        "desc": "A noble family's barrow, no longer at rest.",
    },
    "rannveigs_fast": {
        "name": "Rannveig's Fast", "emoji": "🌬️", "difficulty": "Hard", "min_level": 5,
        "rooms": 6, "events": 2, "pool": {"draugr": 5, "necromancer": 3, "frostbite_spider": 2},
        "boss": "draugr_deathlord", "word_wall": True, "clear_septims": 130, "art": "rannveig",
        "arrive": "Sorrow on the wind, and fresh footprints going in. Only one set.",
        "desc": "A haunted fort above Hjaalmarch, its word wall singing to no one.",
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
    "alftand": {
        "name": "Alftand", "emoji": "⚙️", "difficulty": "Hard", "min_level": 6,
        "rooms": 7, "events": 2, "pool": {"falmer": 5, "frostbite_spider": 3, "troll": 2},
        "boss": "dwarven_centurion", "word_wall": False, "clear_septims": 160, "art": "alftand",
        "arrive": "A glacier has swallowed half the ruin. The machinery underneath never noticed.",
        "desc": "A Dwemer delve under the ice, crawling with what the Dwemer left behind.",
    },
    "forelhost": {
        "name": "Forelhost", "emoji": "🏯", "difficulty": "Hard", "min_level": 7,
        "rooms": 7, "events": 2, "pool": {"draugr": 6, "hagraven": 2, "necromancer": 2},
        "boss": "draugr_deathlord", "word_wall": True, "clear_septims": 170, "art": "forelhost",
        "arrive": "The last refuge of the dragon cult, sealed on itself like a fist.",
        "desc": "A mountaintop monastery where the dragon cult made its final stand.",
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
    "dragontooth": {
        "name": "Dragontooth Crater", "emoji": "🦷", "difficulty": "DRAGON", "min_level": 9,
        "rooms": 4, "events": 1, "pool": {"hagraven": 3, "troll": 2, "wolf": 2},
        "boss": "dragon", "word_wall": True, "clear_septims": 240, "art": "dragon_lair",
        "arrive": "The Reach falls away below. In the crater's bowl, old fire has glassed the stone.",
        "desc": "A jagged crater in the far Reach, warmed by something that never left.",
        "dragon_lair": True,
    },
    "soul_cairn": {
        "name": "The Soul Cairn", "emoji": "💀", "difficulty": "ENDLESS", "min_level": 20,
        "rooms": 1, "events": 0, "pool": {"draugr": 1},
        "boss": "draugr_deathlord", "word_wall": False, "clear_septims": 0, "art": "soul_cairn",
        "arrive": "A grey waste of bone and soul-light stretches past seeing. There is no floor "
                  "to this place - only *down*. How deep do you dare?",
        "desc": "An endless descent unlocked by slaying Alduin. Depth drains your odds; the only "
                "prize is how deep you dared. One attempt per day.",
        "soulcairn": True,       # never offered as a normal delve; the picker adds it when earned
    },
    # --- legend lairs (unlocked by Rumours at Belethor's, hidden until heard) ----
    "last_vigil": {
        "name": "The Last Vigil", "emoji": "🖤", "difficulty": "LEGEND", "min_level": 15,
        "rooms": 2, "events": 0, "pool": {"draugr_deathlord": 1},
        "boss": "ebony_warrior", "word_wall": False, "clear_septims": 900, "art": "last_vigil",
        "arrive": "A mountain shrine, swept clean. A campfire, one bedroll, and armour stands "
                  "polished for a funeral. The honoured dead test all who approach him.",
        "desc": "A veteran of every war awaits his last, best death. The Voice will not move him.",
        "rumour": True,
    },
    "castle_karstaag": {
        "name": "Castle Karstaag", "emoji": "🏔️", "difficulty": "LEGEND", "min_level": 15,
        "rooms": 2, "events": 0, "pool": {"troll": 1},
        "boss": "karstaag", "word_wall": False, "clear_septims": 900, "art": "castle_karstaag",
        "arrive": "A castle carved of glacier ice, older than any map. The cold here does not "
                  "care about your fire.",
        "desc": "The dead frost-troll king, twice-crowned and twice-buried. Fire is useless here.",
        "rumour": True,
    },
    "forgotten_vale": {
        "name": "The Forgotten Vale", "emoji": "🧊", "difficulty": "LEGEND", "min_level": 18,
        "rooms": 2, "events": 0, "pool": {"naaslaarum": 1},
        "boss": "voslaarum", "word_wall": False, "clear_septims": 1500, "art": "forgotten_vale",
        "arrive": "A hidden vale of frozen waterfalls, and a lake of black ice. Two vast "
                  "shadows circle beneath it. They have already seen you.",
        "desc": "Twin dragons beneath the ice. Two groundings, one Voice, no mercy. The "
                "hardest fight in Skyrim.",
        "rumour": True,
    },
    "skuldafn": {
        "name": "Skuldafn", "emoji": "🌑", "difficulty": "THE WORLD-EATER", "min_level": 20,
        "rooms": 2, "events": 0, "pool": {"draugr_deathlord": 1},
        "boss": "alduin", "word_wall": False, "clear_septims": 1000, "art": "skuldafn",
        "arrive": "The dragon temple at the roof of the world. No road leads home from here but victory.",
        "desc": "Alduin's seat. One attempt per day - bring everything you have.",
        "alduin": True,       # never offered normally; the picker adds it when you are ready
    },
}

# ---------------------------------------------------------------------------
# Stirred ranks - locations answer the delver's strength, BY BAND. Easy maps never
# stir (safe farms, retraining grounds, forever). Medium firms up mildly as you
# outgrow it. Hard and dragon maps stay genuinely dangerous at any power level:
# foes fight harder (a flat, clamp-proof malus on your rolls), the boss toughens
# at rank 3+, and the haul scales to match. Rank grows with prowess (level + gear)
# above the location's gate - see engine.stirred_rank.
#   band: (levels_per_rank, rank_cap)  ·  0 per_rank = never stirs
# ---------------------------------------------------------------------------
STIRRED_RANKS = ["Restless", "Roused", "Seething", "Deadly", "Nightmare"]
STIRRED_BANDS = {"Easy": (0, 0), "Medium": (5, 3), "Hard": (3, 5), "DRAGON": (3, 4)}
STIRRED_FIGHT_PER_RANK = 4          # -% on every attack roll per rank
STIRRED_SOAK_PER_RANK = 6           # stirred foes pierce armour: -% soak per rank
STIRRED_CRUSH_PER_RANK = 0.05       # +crushing-blow chance per rank
STIRRED_CLEAR_PER_RANK = 0.25       # +25% clear haul per rank
STIRRED_LOOT_PER_RANK = 0.08        # +8% kill loot per rank

# ---------------------------------------------------------------------------
# Daedric Pacts - opt-in curses sworn before a delve, each multiplying the satchel
# you bank if you make it out. This is the difficulty dial for characters who have
# outgrown the ordinary maps: the game never gets harder unless you ask it to, and
# then it pays you for the trouble. Stack any combination (total capped in engine).
# Death under a pact is a normal death - the whole satchel stays in the dungeon.
# ---------------------------------------------------------------------------
PACTS = {
    "boethiah": {"name": "Boethiah's Proving", "emoji": "⚔️", "mult": 1.5,
                 "desc": "Your attack ceiling drops to 72% - every swing can miss again."},
    "namira": {"name": "Namira's Fast", "emoji": "🐀", "mult": 1.4,
               "desc": "No potions. What you carry in hearts is all you get."},
    "dagon": {"name": "Dagon's Toll", "emoji": "🔥", "mult": 1.6,
              "desc": "Every wound you take is a crushing blow (-2 ❤️)."},
    "clavicus": {"name": "Clavicus Vile's Bargain", "emoji": "😈", "mult": 1.2,
                 "per_other": 0.25, "mult_note": "x1.2, +0.25 per other pact",
                 "desc": "No leaving, no fleeing. Clear it or die in it. His cut grows "
                         "with the company he traps you with."},
}

# ---------------------------------------------------------------------------
# Daily moods - the shared dungeon changes SHAPE day to day, deterministic per UK
# date and identical for everyone (a fair board). Quiet sprints, marathon hauls,
# and the rare NIGHTMARE the whole server wipes on together. `stirred` reuses the
# stirred machinery (fight/soak/crush maluses, +25% clear & +8% loot per rank);
# `rooms` shifts the dungeon's length.
# ---------------------------------------------------------------------------
DAILY_MOODS = {
    "plain": {"weight": 4, "name": "an ordinary day", "emoji": "", "rooms": 0,
              "stirred": 0, "clear_mult": 1.0,
              "desc": "the dungeon as the gods intended"},
    "quiet": {"weight": 1, "name": "A Quiet Road", "emoji": "🕊️", "rooms": -2,
              "stirred": 0, "clear_mult": 0.8,
              "desc": "short and shallow - in, out, home for supper (lighter clear)"},
    "long": {"weight": 2, "name": "The Long Haul", "emoji": "🥾", "rooms": 3,
             "stirred": 0, "clear_mult": 1.4,
             "desc": "the deep survey: far more rooms - pace your hearts and potions"},
    "deadly": {"weight": 4, "name": "A Deadly Day", "emoji": "☠️", "rooms": 0,
               "stirred": 3, "clear_mult": 1.0,
               "desc": "everything inside is Seething - harder to hit, armour-piercing, crushing"},
    "nightmare": {"weight": 1, "name": "NIGHTMARE", "emoji": "😱", "rooms": 2,
                  "stirred": 5, "clear_mult": 1.3,
                  "desc": "longer, and everything at its worst - most will die. Glory to any who clear it"},
}

# ---------------------------------------------------------------------------
# Route conditions - a date-seeded tag each location may carry for the day, shown
# on the picker and applied to delves there. Same for everyone, rotating daily like
# the weather, so the SAME map plays differently across the week and picking a
# destination is a real decision, not a habit. `weight` is the draw weight;
# ROUTE_NONE_WEIGHT is the chance of a plain, untagged road.
# ---------------------------------------------------------------------------
ROUTE_NONE_WEIGHT = 8
ROUTE_CONDITIONS = {
    "rich": {"weight": 2, "name": "Rich Pickings", "emoji": "💰", "short": "clear x1.5",
             "desc": "the clear haul pays half again (x1.5)", "clear_mult": 1.5},
    "overrun": {"weight": 2, "name": "Overrun", "emoji": "🐀", "short": "+1 foe",
                "desc": "one extra foe prowls it - more risk, more glory", "extra_room": True},
    "quiet": {"weight": 1, "name": "Quiet Roads", "emoji": "🕊️", "short": "blessed +5%",
              "desc": "you arrive rested: Blessed (+5% attack) from the door", "blessed": True},
    "hunted": {"weight": 2, "name": "Marked Prey", "emoji": "🏴", "short": "bounties x4",
               "desc": "bounty heads are about - marked foes far likelier", "bounty_mult": 4},
    "elites": {"weight": 1, "name": "Elite Nest", "emoji": "💀", "short": "an elite waits",
               "desc": "something elite has moved in (guaranteed affixed foe)", "force_affix": True},
    "waylaid": {"weight": 1, "name": "A Fallen Soul", "emoji": "⚰️", "short": "a corpse waits",
                "desc": "someone didn't make it out - their satchel waits inside", "force_fallen": True},
    "caravan": {"weight": 1, "name": "Caravan Nearby", "emoji": "🦀", "short": "trader inside",
                "desc": "a trader has camped along the way", "force_mudcrab": True},
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
    "mudcrab": {"weight": 2, "emoji": "🦀", "art": "mudcrab",
                "text": "A **mudcrab** blocks the path, clacking imperiously. It appears to be... a merchant?"},
    "nazeem": {"weight": 1, "emoji": "☁️", "art": "nazeem",
               "text": "Impossibly, **Nazeem** is here. \"Do you get to the Cloud District very often? "
                       "Oh, what am I saying - of course you don't.\""},
    "adoring_fan": {"weight": 1, "emoji": "🤩", "art": "adoring_fan",
                    "text": "A wood elf in yellow bursts from behind a pillar. **\"By Azura! By Azura! "
                            "By Azura! It's YOU! The Grand Champion!\"** (Wrong game, but he is undeterred.)"},
    "wordwall": {"weight": 0, "emoji": "🗣️", "art": "wordwall",   # placed, never drawn
                 "text": "A great curved wall rises out of the dark, carved edge to edge in dragon script. It is **chanting**."},
    "fork": {"weight": 0, "emoji": "🔀", "art": "fork",           # placed, never drawn
             "text": "The passage splits. A **low, safe way** curves toward the exit - and a "
                     "**deep way** drops into the dark, where the air smells of gold and trouble."},
    "fallen": {"weight": 0, "emoji": "⚰️", "art": "fallen",       # placed from the graveyard
               "text": "A body slumps against the wall, satchel still clutched in cold hands."},
    "stray": {"weight": 1, "emoji": "🐾", "art": "stray",
              "text": "Something small has been following you for three rooms. It steps into "
                      "the torchlight and sits down, hopeful."},
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
    "**Touch a Guardian Stone.** A blessing, not a cage: every skill is open to you, and "
    "you become whatever you practise. Your stone's arts simply come faster."
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

AMBUSH_READY_LINES = [
    "You settle into the shadows, utterly unseen. It has no idea you are here.",
    "Hidden. Patient. Its back is to you and the moment is yours to choose.",
]

AMBUSH_KILL_LINES = [
    "It never hears the strike that ends it. The room stays silent.",
    "One clean blow from the dark - over before it began.",
]

LOCKED_CHEST_TEXT = ("A strongbox squats in the corner, banded in iron - and fitted with a "
                     "**master lock**. Whatever is inside, someone wanted it kept.")

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
    {"key": "glass", "name": "Glass", "emoji": "💚", "price": 1800, "dragons": 0},
    {"key": "ebony", "name": "Ebony", "emoji": "⬛", "price": 4000, "dragons": 0},
    {"key": "daedric", "name": "Daedric", "emoji": "😈", "price": 9000, "dragons": 0},
    {"key": "dragonbone", "name": "Dragonbone", "emoji": "🐲", "price": 15000, "dragons": 25},
]
# The Grindstone tempering ladder - cost to reach each next grade (weapon or armour).
# Materials come from the at-risk ingredient pouch; dragon scales gate the top grades,
# tying the finest gear to dragon-hunting.
TEMPER_COSTS = [
    {"septims": 500,  "mats": {"bone_meal": 2}},
    {"septims": 1200, "mats": {"frost_salts": 2}},
    {"septims": 2500, "mats": {"void_salts": 2}},
    {"septims": 5000, "mats": {"dragon_scale": 1}},
    {"septims": 9000, "mats": {"dragon_scale": 2}},
]
WEAPON_FIGHT_PER_TIER = 4      # +4% attack per tier above Iron (all three styles)
ARMOUR_SOAK_PER_TIER = 5       # heavy armour: +5% chance per tier to shrug off a wound
# Armour comes in two styles, switchable free at Belethor's:
#   heavy - the full soak above, worn loud
#   light - reduced soak, but you move like a rumour
LIGHT_SOAK_PER_TIER = 3
LIGHT_SNEAK_BONUS = 6
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
# Alchemy - the Ingredient Pouch & the Lab Bench. Enemies drop ingredients that
# ride in the AT-RISK satchel (lost on death, kept when you walk out). At the Lab
# Bench (a Breezehome upgrade) you brew them into potions using known recipes. This
# is the big lever on the late-game economy: a reason to fight past "I own everything".
# ---------------------------------------------------------------------------
INGREDIENTS = {
    "blue_flower":   {"name": "Blue Mountain Flower", "emoji": "🌼"},
    "nightshade":    {"name": "Nightshade", "emoji": "🌸"},
    "spider_egg":    {"name": "Spider Egg", "emoji": "🕸️"},
    "troll_fat":     {"name": "Troll Fat", "emoji": "🫙"},
    "bone_meal":     {"name": "Bone Meal", "emoji": "🦴"},
    "frost_salts":   {"name": "Frost Salts", "emoji": "❄️"},
    "hagraven_claw": {"name": "Hagraven Claw", "emoji": "🪶"},
    "void_salts":    {"name": "Void Salts", "emoji": "🔮"},
    "dragon_scale":  {"name": "Dragon Scale", "emoji": "🐲"},
    "deathbell":     {"name": "Deathbell", "emoji": "🔔"},
}
# What each enemy TYPE tends to drop (dragons handled specially -> dragon_scale).
INGREDIENT_DROPS = {
    "beast":     ["blue_flower", "spider_egg", "deathbell"],
    "human":     ["blue_flower", "nightshade", "deathbell"],
    "undead":    ["bone_meal", "frost_salts", "void_salts"],
    "monster":   ["troll_fat", "hagraven_claw", "nightshade"],
    "construct": ["void_salts", "frost_salts"],
}

# Recipes - a fixed cost in ingredients brews one of a few useful potions at the
# Lab Bench. Kept short deliberately (a button game can't carry a full alchemy tree).
#   makes: a profile effect key the bench applies;  small, readable outcomes only.
RECIPES = {
    "healing":   {"name": "Potion of Healing", "emoji": "🧪", "makes": "potion",
                  "cost": {"blue_flower": 1, "bone_meal": 1},
                  "desc": "One health potion for your belt (respects your pocket cap)."},
    "vigor":     {"name": "Draught of Vigor", "emoji": "❤️", "makes": "heart_delve",
                  "cost": {"troll_fat": 1, "blue_flower": 1},
                  "desc": "Your NEXT delve begins with +1 max heart."},
    "fortitude": {"name": "Elixir of Fortitude", "emoji": "🛡️", "makes": "soak_delve",
                  "cost": {"troll_fat": 1, "frost_salts": 1, "bone_meal": 1},
                  "desc": "Your NEXT delve: +10% armour soak."},
    "fury":      {"name": "Philtre of Fury", "emoji": "🔥", "makes": "fight_delve",
                  "cost": {"nightshade": 1, "hagraven_claw": 1},
                  "desc": "Your NEXT delve: +6% attack."},
    "true_shot": {"name": "Draught of True Shot", "emoji": "🎯", "makes": "crit_delve",
                  "cost": {"void_salts": 1, "deathbell": 1, "spider_egg": 1},
                  "desc": "Your NEXT delve: +6% crit chance."},
}

# ---------------------------------------------------------------------------
# Weather - ONE roll per UK day, deterministic from the date (see engine.weather_today),
# the same for every player. Purely reactive: it is only ever shown when someone opens
# the hub or delves; nothing is posted on a schedule.
#   fight/sneak: additive % on those rolls · loot/xp: multipliers · heavy: added
#   chance that a boss wound is a crushing blow.
# ---------------------------------------------------------------------------
WEATHERS = {
    "clear": {"weight": 4, "name": "Clear Skies", "emoji": "☀️",
              "desc": "A rare kind day in Skyrim. No modifiers.",
              "fight": 0, "sneak": 0, "loot": 1.0, "xp": 1.0, "heavy": 0.0},
    "blizzard": {"weight": 2, "name": "Blizzard", "emoji": "🌨️",
                 "desc": "Howling snow hides you well, but numbs your hands.",
                 "fight": -4, "sneak": 8, "loot": 1.0, "xp": 1.0, "heavy": 0.0},
    "fog": {"weight": 2, "name": "Sea Fog", "emoji": "🌫️",
            "desc": "A thick coastal fog. Perfect sneaking weather.",
            "fight": 0, "sneak": 6, "loot": 1.0, "xp": 1.0, "heavy": 0.0},
    "bounty": {"weight": 2, "name": "Merchant's Day", "emoji": "🪙",
               "desc": "Caravans lost a lot of cargo lately. Finders keepers.",
               "fight": 0, "sneak": 0, "loot": 1.3, "xp": 1.0, "heavy": 0.0},
    "bloodmoon": {"weight": 1, "name": "Blood Moon", "emoji": "🌕",
                  "desc": "Everything out there is angrier tonight. Glory pays double.",
                  "fight": 0, "sneak": -5, "loot": 1.0, "xp": 1.5, "heavy": 0.15},
}

# ---------------------------------------------------------------------------
# Crits - a clean strike does double damage (and doubles the loot on a killing
# blow). Bounty rooms - rare named variants worth triple, one extra hit tough.
# ---------------------------------------------------------------------------
CRIT_LINES = [
    "**A perfect strike** - clean through the guard, no answer possible.",
    "**Critical hit!** You read the opening a heartbeat early and make it count.",
    "**A devastating blow** - the kind bards exaggerate later. Not this time.",
]

BOUNTY_TITLES = {
    "human": "Notorious", "beast": "Alpha", "undead": "Ancient",
    "monster": "Dread", "construct": "Master-wrought", "dragon": "Elder",
}

# ---------------------------------------------------------------------------
# Marked Affixes - elite modifiers rolled onto ordinary enemies (like bounties,
# but they change HOW the fight plays, not just its numbers). Effects SUBTRACT or
# GATE rather than add, so they survive the 86% clamp and stay meaningful at the
# ceiling. Telegraphed one room ahead through the existing hint channel.
#   all_fight   - flat % on every attack style (negative = harder)
#   gate_style  - a style that barely works, +gate_penalty (default -40)
#   ward_break  - the ONE style that shatters its ward cleanly; any other style's
#                 first landed hit is absorbed (wasted) breaking the ward
#   hp          - extra telling blows it takes
#   crush       - added chance a wound it deals is a crushing 2-heart blow
#   carry       - a wound it deals bleeds into the NEXT room unless you drink first
#   loot_mult / xp_mult - the reward for the trouble
#   types       - enemy types it can attach to;  min_tier - earliest enemy tier
# ---------------------------------------------------------------------------
AFFIXES = {
    "frenzied": {"tag": "Frenzied", "emoji": "🩸", "min_tier": 1,
                 "types": {"beast", "human", "monster"},
                 "telegraph": "...something ahead is snarling, wild and past all reason.",
                 "all_fight": -12, "crush": 0.18, "loot_mult": 2.0, "xp_mult": 1.5,
                 "desc": "Wild and past reason - its guard is gone, but it hits like a landslide."},
    "warded": {"tag": "Warded", "emoji": "🔵", "min_tier": 2,
               "types": {"human", "undead", "monster", "construct"},
               "telegraph": "...a cold blue ward-light pulses on the walls ahead.",
               "ward_break": "destruction", "loot_mult": 1.6, "xp_mult": 1.3,
               "desc": "A ward turns the first blow aside - only **Fire** shatters it cleanly."},
    "bonebound": {"tag": "Bonebound", "emoji": "🦴", "min_tier": 2,
                  "types": {"undead"},
                  "telegraph": "...bones rattle ahead, bound in something arrows won't bite.",
                  "gate_style": "marksman", "loot_mult": 1.5, "xp_mult": 1.3,
                  "desc": "Arrows pass clean through the wrappings - bring a **Blade** or **Fire**."},
    "venomous": {"tag": "Venomous", "emoji": "🟢", "min_tier": 2,
                 "types": {"beast", "monster"},
                 "telegraph": "...a sick green ichor drips from something in the dark ahead.",
                 "carry": True, "all_fight": -4, "loot_mult": 1.6, "xp_mult": 1.4,
                 "desc": "Its venom lingers - a wound it lands **bleeds into the next room** unless you drink."},
    "quickened": {"tag": "Quickened", "emoji": "💨", "min_tier": 2,
                  "types": {"beast", "human", "monster"},
                  "telegraph": "...whatever waits ahead is moving fast. Too fast.",
                  "hp": 1, "all_fight": -6, "loot_mult": 1.7, "xp_mult": 1.5,
                  "desc": "Fast and slippery - it shrugs off the first telling blow and needs another."},
    "dread": {"tag": "Dread", "emoji": "💀", "min_tier": 3,
              "types": {"undead", "monster", "construct"},
              "telegraph": "...the air ahead goes grave-cold. Something the dead themselves fear.",
              "all_fight": -8, "crush": 0.22, "hp": 1, "loot_mult": 2.5, "xp_mult": 2.0,
              "desc": "An elite horror: tougher, and every blow it lands could crush you."},
}
AFFIX_CHANCE_BY_LEVEL = ((8, 0.0), (10, 0.20), (15, 0.35), (999, 0.42))   # ramps in at L8+

# ---------------------------------------------------------------------------
# Capstone Doctrines - each skill hitting 100 unlocks a permanent pick-ONE-of-two
# mastery, so two maxed characters fight the same room differently. Effects use a
# small set of hooks the engine reads (so they compose cleanly):
#   fight (+% attack, optional vs / vs_any / style)  ·  crit (+chance, optional style)
#   soak · sneak · persuade (+%)  ·  loot_mult (x septims)  ·  heart / potion_cap (+N)
# ---------------------------------------------------------------------------
DOCTRINES = {
    "blade": {
        "warmaster": {"name": "Warmaster", "emoji": "⚔️", "fight": 8, "style": "blade",
                      "desc": "+8% attack with the Blade."},
        "bulwark": {"name": "Bulwark", "emoji": "🛡️", "soak": 8,
                    "desc": "+8% chance your armour soaks a wound."},
    },
    "marksman": {
        "deadeye": {"name": "Deadeye", "emoji": "🎯", "crit": 0.08, "style": "marksman",
                    "desc": "+8% crit chance with the Bow."},
        "hunter": {"name": "Hunter", "emoji": "🐺", "fight": 10, "vs": "beast",
                   "desc": "+10% attack against beasts."},
    },
    "destruction": {
        "incinerate": {"name": "Incinerate", "emoji": "🔥", "fight": 12, "style": "destruction",
                       "vs_any": ["undead", "monster"],
                       "desc": "+12% Fire attack against undead & monsters."},
        "impact": {"name": "Impact", "emoji": "💥", "crit": 0.06, "style": "destruction",
                   "desc": "+6% crit chance with Fire."},
    },
    "sneak": {
        "ghost": {"name": "Ghost", "emoji": "👤", "sneak": 10,
                  "desc": "+10% Sneak."},
        "nightstalker": {"name": "Nightstalker", "emoji": "🌘", "crit": 0.10,
                         "desc": "+10% crit chance on every attack - a killer's timing."},
    },
    "speech": {
        "silver_tongue": {"name": "Silver Tongue", "emoji": "💬", "persuade": 10,
                          "desc": "+10% Persuade."},
        "haggler": {"name": "Haggler", "emoji": "💰", "loot_mult": 1.25,
                    "desc": "+25% septims from every source."},
    },
    "lockpicking": {
        "locksmith": {"name": "Locksmith", "emoji": "🔓", "loot_mult": 1.15,
                      "desc": "+15% septims - you find the caches others miss."},
        "survivor": {"name": "Survivor", "emoji": "🧪", "potion_cap": 1,
                     "desc": "+1 potion pocket."},
    },
}

# ---------------------------------------------------------------------------
# Rumours at Belethor's - buy a whisper, unlock a LEGEND lair on the picker. Each
# is a one-time hunt with a fixed brutal statline (never stirred): beat it once,
# keep the trophy forever. The coin sink the late game deserves.
# ---------------------------------------------------------------------------
RUMOURS = {
    "ebony_warrior": {"loc": "last_vigil", "price": 2500, "min_level": 15,
                      "name": "A warrior in ebony", "emoji": "🖤",
                      "blurb": "\"Fellow came through asking for the strongest soul in Skyrim. "
                               "Left a map to a mountain shrine. Wanted to be followed.\""},
    "karstaag": {"loc": "castle_karstaag", "price": 2500, "min_level": 15,
                 "name": "The frost king wakes", "emoji": "❄️",
                 "blurb": "\"Hunters won't go near the old ice castle any more. Say the cold "
                          "there has a NAME again.\""},
    "vale_twins": {"loc": "forgotten_vale", "price": 4000, "min_level": 18,
                   "name": "Two shadows under the ice", "emoji": "🧊",
                   "blurb": "\"A Falmer-tale: a hidden vale, a frozen lake, and TWO dragons "
                            "beneath it. Nobody who checked has come back to laugh about it.\""},
}
# which slain enemy marks which rumour done
RUMOUR_BOSS = {"ebony_warrior": "ebony_warrior", "karstaag": "karstaag",
               "voslaarum": "vale_twins"}

# ---------------------------------------------------------------------------
# Companions - befriended at the rare 🐾 Stray event, kept forever, one active at
# a time. Small passives, big attachment. {carl}-style delve flavour included.
# ---------------------------------------------------------------------------
COMPANIONS = {
    "meeko": {"name": "Meeko", "emoji": "🐕", "species": "a shaggy grey dog", "art": "pet_meeko",
              "passive": "Takes the first wound for you once per delve. Good boy.",
              "found": "The dog pads over and leans against your leg with his whole weight. "
                       "You have been chosen.",
              "guard": True},
    "vix": {"name": "Vix", "emoji": "🦊", "species": "a snow fox", "art": "pet_vix",
            "passive": "A forager's nose: +8% ingredient drop chance.",
            "found": "The fox drops a frost mirriam sprig at your feet and looks insufferably "
                     "pleased with herself.",
            "forage": 0.08},
    "pincer": {"name": "Pincer", "emoji": "🦀", "species": "an unusually shrewd mudcrab", "art": "pet_pincer",
               "passive": "A merchant's instincts: +5% septims from all sources.",
               "found": "The mudcrab clacks twice, produces a tiny coin from somewhere, and "
                        "offers it up. A business partnership is proposed.",
               "barter": 1.05},
    "corvus": {"name": "Corvus", "emoji": "🐦‍⬛", "species": "a one-eyed raven", "art": "pet_corvus",
               "passive": "Sees the openings you miss: +2% crit chance.",
               "found": "The raven lands on your shoulder, inspects your ear, and stays. "
                        "Apparently that's settled then.",
               "crit": 0.02},
}

# ---------------------------------------------------------------------------
# Wonders - ultra-rare cosmetic trophies rolled on kills (and won in the Pit,
# the duelling circle, the weekly hunt and under your own floorboards). Pure
# chase: no power, no pity timer, announced to the whole channel when one hits.
# `sources` gates WHERE each can drop, so every system carries a lottery ticket.
# ---------------------------------------------------------------------------
WONDERS = {
    "golden_sweetroll": {"name": "The Golden Sweetroll", "emoji": "🍩", "sources": {"room"},
                         "blurb": "a sweetroll cast in solid gold. Nobody is stealing this one."},
    "ysgramor_tankard": {"name": "Ysgramor's Chipped Tankard", "emoji": "🍺", "sources": {"room"},
                         "blurb": "five hundred companions drank from it. Now you do."},
    "septim_misprint": {"name": "The Two-Faced Septim", "emoji": "🪙", "sources": {"room"},
                        "blurb": "a septim mis-struck with Tiber Septim's face on BOTH sides. "
                                 "It always lands your way."},
    "dwemer_music_box": {"name": "A Dwemer Music Box", "emoji": "🎵", "sources": {"room"},
                         "blurb": "still playing, four thousand years on. Nobody knows the tune's name."},
    "priest_mask": {"name": "A Dragon Priest's Cracked Mask", "emoji": "👺", "sources": {"boss"},
                    "blurb": "split clean down the middle. You did not ask what split it."},
    "emerald_claw": {"name": "The Emerald Dragon Claw", "emoji": "🐾", "sources": {"boss"},
                     "blurb": "a door key to somewhere no door remains."},
    "white_pelt": {"name": "The White Sabre Pelt", "emoji": "🐆", "sources": {"boss"},
                   "blurb": "hunters swear the white sabre is a myth. The myth sheds."},
    "dragon_tear": {"name": "A Dragon's Tear", "emoji": "💧", "sources": {"dragon"},
                    "blurb": "crystallised mid-fall. They do grieve, then."},
    "ancient_word": {"name": "A Word Wall Fragment", "emoji": "🗿", "sources": {"dragon"},
                     "blurb": "a fist of carved stone that hums your name in the old tongue."},
    "bloodied_laurel": {"name": "The Bloodied Laurel", "emoji": "🏵️", "sources": {"pit"},
                        "blurb": "thrown from the stands once a generation. The Pit's highest honour."},
    "marauder_horn": {"name": "The Marauder's Cracked Warhorn", "emoji": "📯", "sources": {"worldboss"},
                      "blurb": "it took a warband to earn and one good blow to crack."},
    "hearth_idol": {"name": "A Little Hearth God", "emoji": "🪆", "sources": {"homestead"},
                    "blurb": "carved centuries ago, found under your own floorboards. It approves of you."},
    "rival_buckle": {"name": "A Rival's Belt Buckle", "emoji": "🥇", "sources": {"duel"},
                     "blurb": "claimed in the circle, polished nightly, mentioned constantly."},
}

# ---------------------------------------------------------------------------
# The Pit - Windhelm's unsanctioned arena. One bout per UK day against a ladder
# of named champions, simulated round by round with your real build. Rank resets
# monthly (best rank remembered); no satchel at stake, glory only.
# ---------------------------------------------------------------------------
# Each champion fights DIFFERENTLY - a signature quirk, declared before the bout,
# so the ladder is ten puzzles rather than one loop with bigger numbers:
#   drunk      - 10%/round he stumbles and hurts himself
#   quick      - she strikes FIRST each round
#   shieldwall - after you land a hit, your next swing is -15%
#   butcher    - his hits crush (25% chance, -2 ❤️)
#   riposte    - when you miss, 50% she answers with a free strike
#   veteran    - reads your first landed hit and shrugs it off entirely
#   silent     - her thrusts slip armour: your guard counts half
#   reckless   - a brawl: BOTH of you +15% to hit
#   bear       - every hit crushes (-2 ❤️), but 20%/round it's distracted
PIT_CHAMPS = [
    {"name": "Snilf the Bold", "fight": 30, "hp": 2, "guard": 0, "art": "pit_snilf", "quirk": None,
     "quirk_desc": "no tricks - everyone starts somewhere",
     "taunt": "\"I've beaten three men and a goat!\"", "style": "wild swings"},
    {"name": "Rolff Stone-Fist", "fight": 36, "hp": 2, "guard": 5, "art": "pit_rolff", "quirk": "drunk",
     "quirk_desc": "fights drunk - sometimes his worst enemy is the floor",
     "taunt": "\"Go back to the Grey Quarter- oh, you're here to FIGHT me? Ha!\"", "style": "brawler's hooks"},
    {"name": "Adelaisa the Quick", "fight": 42, "hp": 2, "guard": 10, "art": "pit_adelaisa", "quirk": "quick",
     "quirk_desc": "strikes first every round - end it fast or bleed early",
     "taunt": "\"Blink and it's over.\"", "style": "darting cuts"},
    {"name": "Uzoga gra-Shurkul", "fight": 46, "hp": 3, "guard": 10, "art": "pit_uzoga", "quirk": "shieldwall",
     "quirk_desc": "closes her guard after taking a hit - your follow-up swings at -15%",
     "taunt": "\"Malacath watches. Entertain him.\"", "style": "methodical strikes"},
    {"name": "Bero the Butcher", "fight": 50, "hp": 3, "guard": 15, "art": "pit_bero", "quirk": "butcher",
     "quirk_desc": "his cleavers can crush - some hits cost two hearts",
     "taunt": "\"I name my cleavers. You'll meet both.\"", "style": "cleaver work"},
    {"name": "Sword-Singer Hama", "fight": 54, "hp": 3, "guard": 20, "art": "pit_hama", "quirk": "riposte",
     "quirk_desc": "punishes misses with a free riposte - swing true or not at all",
     "taunt": "\"The blade sings. Try to keep the rhythm.\"", "style": "flowing bladework"},
    {"name": "Old Ulfberth", "fight": 58, "hp": 4, "guard": 20, "art": "pit_ulfberth", "quirk": "veteran",
     "quirk_desc": "reads your first landed blow and shrugs it off entirely",
     "taunt": "\"Forty years in the Pit, whelp. Sit down.\"", "style": "veteran's patience"},
    {"name": "The Widow of Windhelm", "fight": 62, "hp": 4, "guard": 25, "art": "pit_widow", "quirk": "silent",
     "quirk_desc": "finds the seams - your armour counts for half",
     "taunt": "\"...\"", "style": "silent, perfect thrusts"},
    {"name": "Yrsarald Thrice-Pierced", "fight": 66, "hp": 5, "guard": 25, "art": "pit_yrsarald", "quirk": "reckless",
     "quirk_desc": "turns it into a brawl - you BOTH hit far more often",
     "taunt": "\"Three spears couldn't do it. What are you bringing?\"", "style": "reckless power"},
    {"name": "The Caged Bear", "fight": 70, "hp": 6, "guard": 30, "art": "pit_bear", "quirk": "bear", "kind": "beast",
     "quirk_desc": "every hit crushes two hearts... but bears are easily distracted",
     "taunt": "(It is an actual bear.)", "style": "being a bear"},
    # --- beyond the bear: the rungs nobody sane climbs -------------------------
    {"name": "Hjoromir the Twice-Dead", "fight": 64, "hp": 4, "guard": 25,
     "art": "pit_hjoromir", "quirk": "unyielding",
     "quirk_desc": "died twice already and didn't care for it - the first killing blow won't keep him down",
     "taunt": "\"Sovngarde sent me back. Twice. Guess why.\"", "style": "corpse-cold patience"},
    {"name": "The Sisters Vess & Vex", "fight": 66, "hp": 5, "guard": 25,
     "art": "pit_sisters", "quirk": "twin",
     "quirk_desc": "two fighters, one entry fee - when one blade lands, the second follows",
     "taunt": "\"She softens them.\" \"She finishes them.\" (They point at each other.)",
     "style": "mirrored bladework"},
    {"name": "Bloodmarked Korst", "fight": 64, "hp": 5, "guard": 28,
     "art": "pit_korst", "quirk": "blood",
     "quirk_desc": "smells blood - once you're below half hearts he becomes something else",
     "taunt": "\"You'll do fine. You're already bleeding somewhere, aren't you?\"",
     "style": "a hunter's escalation"},
    {"name": "The Stone Guest", "fight": 58, "hp": 6, "guard": 34,
     "art": "pit_stone_guest", "quirk": "stone",
     "quirk_desc": "a statue that walked out of an old temple - power blows just chip it",
     "taunt": "(It says nothing. It has been waiting here longer than Windhelm.)",
     "style": "geological indifference"},
    {"name": "The Pit Master", "fight": 66, "hp": 5, "guard": 30,
     "art": "pit_master", "quirk": "master",
     "quirk_desc": "founded this arena and has never lost in it - strikes first AND reads your best blow",
     "taunt": "\"I built this pit. I've watched every trick you know get invented.\"",
     "style": "everything, perfected"},
]
PIT_TITLES = ["Pit Dog", "Scrapper", "Bloodied", "Contender", "Crowd's Favourite",
              "Duellist", "Veteran", "Widowmaker", "Thrice-Feared", "Bear-Slayer",
              "Deathless", "Twin-Breaker", "Blood-Proof", "Stone-Breaker", "PIT CHAMPION"]

# ---------------------------------------------------------------------------
# NPC Factions - a light allegiance you swear at Lv 8+. Each week your faction sets
# a task built from a verb the endgame maths tends to abandon; complete it for favour
# (a rank ladder + a septim/XP stipend). The rival factions' standings are NPC-
# simulated and deterministic-by-week, so the world feels populated with only a few
# real players. Deliberately light: no guild-vs-guild backend (that's for the
# standalone bot); this is flavour + a reason to use your neglected skills.
# ---------------------------------------------------------------------------
FACTIONS = {
    "companions": {"name": "The Companions", "emoji": "🛡️", "stat": "kills", "goal": 40,
                   "verb": "killing blows", "seat": "Jorrvaskr, Whiterun",
                   "blurb": "Warriors of honour. They respect a body count."},
    "thieves": {"name": "The Thieves Guild", "emoji": "🗝️", "stat": "sneaks", "goal": 14,
                "verb": "clean sneaks", "seat": "The Ratway, Riften",
                "blurb": "Shadows and profit. Be unseen, be paid."},
    "college": {"name": "College of Winterhold", "emoji": "🔮", "stat": "persuades", "goal": 12,
                "verb": "parleys won", "seat": "Winterhold",
                "blurb": "Scholars who would rather talk than fight. Usually."},
}
FACTION_RANKS = ["Initiate", "Member", "Sworn", "Champion", "Harbinger"]
# The guild-hall gossip: named NPCs with faction ties and deed lines ({n} filled
# with a seeded weekly count). Rendered on the Factions panel beneath the REAL
# players' standings, so the halls feel busy even on a quiet server.
FACTION_NPCS = [
    {"name": "Vilkas", "faction": "companions",
     "deeds": ["put down a giant that wandered too near Pelagia Farm ({n} swings, he counted)",
               "cleared {n} bandits out of a mill they'd 'liberated'"]},
    {"name": "Aela the Huntress", "faction": "companions",
     "deeds": ["dragged a sabre cat carcass into Jorrvaskr like it weighed nothing ({n} arrows in it)",
               "tracked a poacher ring for {n} days and ended it in one"]},
    {"name": "Njada Stonearm", "faction": "companions",
     "deeds": ["won {n} straight shield-brawls in the yard and is unbearable about it"]},
    {"name": "Brynjolf", "faction": "thieves",
     "deeds": ["lifted {n} purses at the Solitude market and bought everyone a round",
               "swapped {n} ledgers in the Emperor's own counting-house, allegedly"]},
    {"name": "Karliah", "faction": "thieves",
     "deeds": ["walked through {n} guard patrols unseen, out of professional boredom"]},
    {"name": "Delvin Mallory", "faction": "thieves",
     "deeds": ["fenced {n} 'lost' heirlooms back to their original owners. Twice each"]},
    {"name": "Tolfdir", "faction": "college",
     "deeds": ["talked a draugr into standing down ({n} minutes of patient lecturing)",
               "negotiated {n} new manuscripts out of a very suspicious courier"]},
    {"name": "Faralda", "faction": "college",
     "deeds": ["turned {n} would-be applicants away with a single raised eyebrow"]},
    {"name": "Sergius Turrianus", "faction": "college",
     "deeds": ["sweet-talked {n} jarls into funding 'essential enchantment research'"]},
]

# ---------------------------------------------------------------------------
# Idle Expeditions - send your housecarl on a multi-day errand and collect the haul
# when they return. Date-based (whole UK days), collected only when you open the hub,
# so nothing is ever posted on a schedule. The idle complement to active delves - it
# answers "what is there to do once my delves are done today?".
# ---------------------------------------------------------------------------
EXPEDITIONS = {
    "roads":  {"name": "Patrol the roads", "emoji": "🛡️", "days": 1, "septims": 350, "xp": 90,
               "ingredient": None, "desc": "A day guarding the trade road. Steady, safe coin."},
    "hunt":   {"name": "Hunt the Reach", "emoji": "🏹", "days": 2, "septims": 850, "xp": 220,
               "ingredient": "troll_fat", "desc": "Two days hunting game and trolls in the hills."},
    "ruin":   {"name": "Chart a far ruin", "emoji": "🗺️", "days": 3, "septims": 1700, "xp": 480,
               "ingredient": "void_salts", "desc": "Three days mapping a distant barrow. Real spoils."},
}
HOUSECARLS = ["Lydia", "Jordis", "Argis", "Iona", "Valdimar", "Rayya"]

# Away-logs: what the housecarl gets up to out there. Rendered (never posted) when
# the Expeditions panel opens - 5-7 timestamped dispatches per day at seeded times,
# deterministic per expedition so the story stays put and simply accretes; the panel
# shows the latest handful. {carl} is the housecarl's name.
EXPEDITION_LOGS_COMMON = [
    "{carl} makes camp early and mends a bootstrap that has been complaining for miles.",
    "A mudcrab challenges {carl} for the path. It is stepped over.",
    "{carl} writes a short letter home. It is mostly about the weather.",
    "Rations check: hardtack, dried snowberries, and exactly one sweetroll, guarded closely.",
    "{carl} whets the blade by firelight until it sings.",
    "Cold rain from the north. {carl} pulls the hood lower and keeps walking.",
    "A passing guard nods at {carl}. 'No lollygaggin'.' None was planned.",
    "{carl} counts the coin purse twice. It comes out the same both times, reassuringly.",
    "Clear night. {carl} names the constellations wrong with total confidence.",
    "{carl} shares the fire with a quiet pilgrim of Stendarr. Good company, few words.",
]
EXPEDITION_LOGS = {
    "roads": [
        "{carl} waves a merchant caravan through and pockets a grateful tip.",
        "{carl} escorts a pilgrim as far as the shrine and gets a blessing for it.",
        "A wolf tried the horses. The wolf regrets this.",
        "{carl} breaks up a toll scam run by two bandits in stolen guard armour.",
        "A carriage wheel shattered at the crossroads - {carl} lifted the axle alone.",
        "{carl} shares a fire with a travelling bard and learns exactly one new verse.",
        "Someone reported a dragon. It was a very large hawk. {carl} logged it anyway.",
        "{carl} points a lost Khajiit caravan back toward the city gates.",
        "Quiet watch. {carl} sharpens the blade twice just for something to do.",
        "A drunk Nord challenged {carl} to a fistfight, lost, and paid up laughing.",
        "{carl} helps a farmer wrestle a painted cow back onto the road. Neither enjoys it.",
        "Two Vigilants of Stendarr pass heading north. {carl} wishes them luck, quietly.",
        "{carl} settles a fare dispute between a carriage driver and a very tall passenger.",
        "The toll ledger balances for once. {carl} celebrates with an apple.",
        "{carl} chases a pickpocket half a mile, then lets the winded fool crawl off.",
        "A courier sprints past in no boots whatsoever. {carl} decides not to ask.",
    ],
    "hunt": [
        "{carl} takes an elk at eighty paces. Dinner is handled.",
        "Troll tracks by the river - {carl} marks the cave and gives it a wide berth.",
        "{carl} trades pelts with a Forsworn scout under a very tense truce.",
        "Rain all day. {carl} waits it out under a rock ledge, cursing the Reach.",
        "A sabre cat stalked the camp for an hour. {carl} stared it down.",
        "{carl} finds a hunter's shrine of Hircine and leaves a strip of venison.",
        "The snare line came up full. {carl} whistles the whole way back to camp.",
        "{carl} skins a troll and swears never to smell anything again.",
        "Hagraven feathers on the wind - {carl} moves camp two valleys over.",
        "{carl} loses an arrow to a goat on a cliff. The goat seemed smug about it.",
        "{carl} smokes the day's catch over juniper. The whole camp smells expensive.",
        "Fresh bear prints, big ones. {carl} elects to hunt in the other direction.",
        "{carl} frees a fox from an old snare and pretends not to have done it.",
        "A thunderstorm rolls down the valley. {carl} waits it out and counts the seconds.",
        "{carl} finds an abandoned hunter's blind and claims it in your name.",
        "The venison is good tonight. {carl} eats like a Jarl and regrets nothing.",
    ],
    "ruin": [
        "{carl} sketches the outer arches by torchlight. The stonework is older than the Empire.",
        "A pressure plate clicks under {carl}'s boot. Nothing fires. Nobody breathes.",
        "{carl} pries a soul gem from a sconce and wraps it in a bedroll.",
        "Draugr in the lower gallery - {carl} bars the door and maps around them.",
        "{carl} copies a wall of dragon-script by candle stub. It hums faintly.",
        "The bridge is out. {carl} finds the flooded stair the old texts promised.",
        "{carl} shares hardtack with a terrified scholar found hiding in an alcove.",
        "Frostbite spiders in the antechamber. {carl} burns the webs and moves on.",
        "{carl} lifts a burial crown, thinks better of it, and puts it back. Mostly.",
        "Deep tremors in the night. {carl} sleeps in armour and keeps the torch lit.",
        "{carl} rubs charcoal over a carved lintel and rolls up a rather good impression.",
        "The lower stair is flooded to the knee. {carl} charts it anyway, swearing richly.",
        "An old cave-in blocks the east wing. {carl} marks it 'later' with real optimism.",
        "{carl} finds a skeleton mid-reach for a coin purse. The purse is claimed. Respectfully.",
        "Something answered the echo test. {carl} does not run the echo test again.",
        "{carl} lights the great brazier just to see the hall once. Worth the whale oil.",
    ],
}

# NPC names for auto-generated Fallen Adventurer corpses (so death-content appears
# even with only a few live players). Real players' deaths are used first.
NPC_FALLEN = ["Bjorn One-Eye", "Ingrid the Lost", "Ralof's cousin", "Sven the Bard",
              "Mjoll's rival", "a nameless sellsword", "Gjalund of Dawnstar",
              "Hroki Fair-Hair", "an Imperial deserter"]

# ---------------------------------------------------------------------------
# Property - Belethor's septim sinks. Breezehome first, then furnishings.
# Small comforts, not power spikes: effects apply to the FIRST delve of each day.
# ---------------------------------------------------------------------------
HOME_ITEMS = {
    "breezehome": {"name": "Breezehome", "emoji": "🏠", "price": 5000, "requires": None,
                   "desc": "A house in Whiterun. Well-rested: your first delve each day "
                           "starts Blessed (+5% attack)."},
    "alchemy_lab": {"name": "Alchemy Lab", "emoji": "⚗️", "price": 3000, "requires": "breezehome",
                    "desc": "A home laboratory. Brews you 1 free potion before your first "
                            "delve each day (up to your cap)."},
    "trophy_room": {"name": "Trophy Room", "emoji": "🏆", "price": 8000, "requires": "breezehome",
                    "desc": "Somewhere to hang the dragon skulls. Pure bragging rights - "
                            "adds a 🏆 to your name on the rankings."},
}

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def pick(lines, **fmt):
    """Random line from a pool, with optional format args."""
    line = random.choice(lines)
    return line.format(**fmt) if fmt else line


def xp_needed(level: int) -> int:
    """XP required to go from `level` to `level + 1`. Linear early (levels feel
    quick in week one), quadratic past level 8 so the climb genuinely stretches:
    roughly L20 in a month of daily play, L30 a multi-month grind."""
    need = 75 + 35 * (level - 1)
    if level > 8:
        need += 8 * (level - 8) ** 2
    return need


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
