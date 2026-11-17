"""Bank Job's buildings. Each of the Bank of HMS Victory's branches has a few ways in, each a run of rooms down to
its safe, and each room has a couple of things standing in the crew's way, every one a job for one role: a
keypad for the Hacker, a lock for the Safecracker, a patrol for the Lookout, a route for the Wheelman, someone
to blag past for the Face, and loot for the Muscle. heist.py takes a crew through them; this only says what's
where.

A thing in a room has a size: "s" is quick (a three-digit code, one tumbler, four windows), "m" a bit more,
"big" the full puzzle, and "loot" a loading job whose result swells the vault. Its weight in the alarm follows
its size. The pins say where each room sits on the branch's cutaway, as percentages of the picture, so the page
can draw the crew's route through the building."""

# key: (name on the page, what's in it: [(role, name, size)])
ROOMS = {
    "roof": ("THE ROOF", [("hacker", "SKYLIGHT ALARM", "s"), ("lookout", "PATROL BELOW", "s")]),
    "street": ("THE FRONT DOOR", [("face", "THE DOORMAN", "s"), ("safecracker", "THE FRONT LOCK", "s"),
                                  ("hacker", "CCTV OVER THE DOOR", "s")]),
    "drains": ("THE DRAINS", [("wheelman", "THE WAY THROUGH", "m"), ("safecracker", "THE GRATE", "s")]),
    "office": ("THE OFFICE", [("hacker", "THE MANAGER'S COMPUTER", "s"), ("safecracker", "THE DESK SAFE", "s"),
                              ("face", "THE CLEANER", "s")]),
    "security": ("SECURITY ROOM", [("lookout", "CCTV CAMERAS", "s"), ("face", "THE GUARD", "s"), ("hacker", "KEYPAD DOOR", "s")]),
    "hall": ("BANKING HALL", [("muscle", "THE TILLS", "loot"), ("lookout", "MOTION SENSORS", "s"),
                              ("face", "THE NIGHT MANAGER", "s")]),
    "stairs": ("THE STAIRS DOWN", [("hacker", "ALARM PANEL", "s"), ("lookout", "THE PATROL", "s")]),
    "lasers": ("LASER CORRIDOR", [("lookout", "THE LASER GRID", "m"), ("hacker", "MOTION SENSOR", "s")]),
    "lift": ("THE LIFT SHAFT", [("hacker", "LIFT CONTROLS", "s"), ("wheelman", "DOWN THE SHAFT", "s")]),
    "shop": ("THE SHOP", [("lookout", "THE SHOP BELL", "s"), ("face", "MAUREEN", "s"), ("muscle", "THE TILL", "loot")]),
    "yard": ("THE BACK YARD", [("lookout", "THE DOG", "s"), ("safecracker", "THE GATE PADLOCK", "s")]),
    "strongroom": ("THE STRONGROOM", [("safecracker", "THE STRONGROOM SAFE", "m"), ("muscle", "FILL THE BAGS", "loot"),
                                      ("wheelman", "THE GETAWAY", "m")]),
    "vaultdoor": ("THE VAULT DOOR", [("safecracker", "THE VAULT DOOR", "big"), ("hacker", "THE TIME LOCK", "big"),
                                     ("muscle", "LOAD THE VAN", "loot"), ("wheelman", "THE GETAWAY", "big")]),
}

# each branch: its ways in (name, rooms in order, seconds for the vault to fill; any of them quiet or loud) and
# where its rooms sit on its cutaway
BUILDINGS = {
    "post": {
        "routes": {
            "front": {"name": "FRONT DOOR", "rooms": ["shop", "office", "strongroom"], "secs": 26.0},
            "yard": {"name": "BACK YARD", "rooms": ["yard", "office", "strongroom"], "secs": 26.0},
        },
        "pins": {"shop": (30, 43), "yard": (88, 40), "office": (61, 41), "strongroom": (55, 60)},
    },
    "society": {
        "routes": {
            "roof": {"name": "ROOF", "rooms": ["roof", "office", "security", "stairs", "vaultdoor"], "secs": 30.0},
            "front": {"name": "FRONT DOOR", "rooms": ["street", "hall", "security", "stairs", "vaultdoor"], "secs": 28.0},
            "drains": {"name": "DRAINS", "rooms": ["drains", "vaultdoor"], "secs": 20.0},
        },
        "pins": {"roof": (42, 11), "office": (30, 25), "security": (75, 27), "street": (15, 44), "hall": (50, 44),
                 "stairs": (86, 45), "vaultdoor": (50, 64), "drains": (50, 90)},
    },
    "vault": {
        "routes": {
            "roof": {"name": "ROOF", "rooms": ["roof", "office", "security", "hall", "lasers", "lift", "vaultdoor"],
                     "secs": 32.0},
            "front": {"name": "FRONT DOOR", "rooms": ["street", "hall", "security", "lasers", "lift", "vaultdoor"],
                      "secs": 30.0},
            "drains": {"name": "DRAINS", "rooms": ["drains", "lift", "vaultdoor"], "secs": 22.0},
        },
        "pins": {"roof": (50, 9), "office": (40, 21), "security": (65, 31), "hall": (50, 45), "street": (15, 45),
                 "lasers": (60, 55), "lift": (16, 67), "vaultdoor": (60, 70), "drains": (50, 87)},
    },
}

# a quick job for anyone whose role a room hasn't got, so nobody in the crew stands about: one of these per room,
# a different one each room (Terry never gets these, only a player does)
SPARE = {
    "hacker": [("A MOTION SENSOR", "s"), ("A CCTV CAMERA", "s"), ("AN ALARM WIRE", "s"), ("A DOOR KEYPAD", "s")],
    "safecracker": [("A LOCKED DOOR", "s"), ("A PADLOCK", "s"), ("A LOCKED HATCH", "s"), ("A KEY BOX", "s")],
    "lookout": [("THE NIGHT GUARD", "s"), ("A TORCH BEAM", "s"), ("A PATROL", "s"), ("THE WINDOWS OPPOSITE", "s")],
    "wheelman": [("THE WAY OUT", "s"), ("A BACK ROUTE", "s"), ("A SHORTCUT OUT", "s"), ("THE ESCAPE ROUTE", "s")],
    "face": [("THE RADIO CHECK", "s"), ("A PHONE CALL", "s"), ("A NIGHT CLEANER", "s"), ("A NOSY NEIGHBOUR", "s")],
    "muscle": [("A CASH BOX", "loot"), ("PETTY CASH", "loot"), ("A BAG OF COINS", "loot"), ("A DEPOSIT BOX", "loot")],
}

# how much a thing weighs in the alarm, by size; the twist doubles one role's
WEIGHT = {"s": 1.0, "m": 1.4, "big": 2.0, "loot": 1.0}
# how long a room gives the crew: a little to look round, then enough for its biggest thing, a bit more for each other
ROOM_BASE, ROOM_PER_EXTRA = 20.0, 6.0
SECONDS = {"s": 27.0, "m": 32.0, "big": 40.0, "loot": 28.0}       # a small thing plays a middle-sized game (heist.PLAYS)


def routes(target: str) -> dict:
    return BUILDINGS[target]["routes"]


def things(key: str, i: int, held: set[str]) -> list[tuple[str, str, str, bool]]:
    """What's in the way in room key (the route's i-th): its own things, then a spare job for each role in ``held``
    (the roles players have) that it hasn't got. Each is (role, name, size, spare)."""
    own = [(role, name, size, False) for role, name, size in ROOMS[key][1]]
    have = {role for role, _, _, _ in own}
    spare = [(role, *SPARE[role][i % len(SPARE[role])], True) for role in SPARE if role in held and role not in have]
    return own + spare


def room_seconds(things: list[tuple[str, str, str]]) -> float:
    """The room's own things set its clock: the spares are quick ones, done alongside them."""
    sizes = sorted((SECONDS[size] for _, _, size in things), reverse=True)
    return ROOM_BASE + sizes[0] + ROOM_PER_EXTRA * (len(sizes) - 1)


def leans(target: str, route: str) -> list[str]:
    """The two roles a way in leans on most (for the page's route sticker)."""
    count: dict[str, float] = {}
    for key in routes(target)[route]["rooms"]:
        for role, _, size in ROOMS[key][1]:
            count[role] = count.get(role, 0) + WEIGHT[size]
    return sorted(count, key=lambda r: -count[r])[:2]
