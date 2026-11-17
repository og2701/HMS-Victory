"""Bank Job's mini-games: each role plays a different one in every room of a job, so a heist never repeats one.

Every role has its first game in heist.py (the Hacker's keypad, the Safecracker's dial, the Lookout's patrol, the
Wheelman's route, the Face's cover story, the Muscle's van); the rest live here, a module a role (hacker.py,
safecracker.py, ...), each with GAMES, a dict of key -> Game. heist.py deals them out (deal()), and for a puzzle
whose state carries a "game" key that's in here, makes it, shows it, moves it and judges it through the Game.

The rules are the same as for the first six. The game is made and judged here, and the page only ever sees
public(): a secret (a code, an order, where something is) never goes out. A game the page has to see in full to
play (a picture to spot something in, a schedule to time taps against) is fine too: the most a doctored page can
do is play perfectly, and a perfect job still leaves the bank its 1%. Timing games collect their taps on the page
and send them in one move, which the game checks against the schedule it made.

A game comes in sizes: "s" (a quick one, about 15-20s of play), "m" (about 25s), "big" (the vault's, about 35s)
and, for the Muscle, "loot" (like "s"; its score fills the vault). Scores run 0 to 1: 1 for a clean job, and
partway for partway (a game cut off by the room's clock is scored as it stands, so give credit for progress).
"""

import importlib
import random


class Refuse(Exception):
    """Something the rules don't allow; the message is for the player."""


class Game:
    """One mini-game. Subclass it, set the four names, and fill in the methods."""
    key = ""          # unique within its role, lower case, e.g. "wire"
    role = ""         # hacker, safecracker, lookout, wheelman, face, muscle
    title = ""        # what the page calls it, upper case, e.g. "TRACE THE WIRE"
    pace = (2.0, 5.0)  # seconds a dev bot waits between its moves

    def make(self, rng: random.Random, size: str, story: dict) -> dict:
        """A new puzzle at this size, as plain JSON-able data (it's saved to disk). story is the crew's cover
        story, for any game that wants it (the Face's)."""
        raise NotImplementedError

    def public(self, p: dict) -> dict:
        """What the page sees: never a secret."""
        raise NotImplementedError

    def move(self, p: dict, body: dict) -> None:
        """A move from the page; change p, or raise Refuse with a short message if it isn't one."""
        raise NotImplementedError

    def done(self, p: dict) -> bool:
        raise NotImplementedError

    def score(self, p: dict) -> float:
        """How it went, 0 to 1, as it stands (done or not)."""
        raise NotImplementedError

    def solve(self, p: dict) -> list[dict]:
        """The moves a perfect player would make from here (for the tests): playing them scores 1."""
        raise NotImplementedError

    def bot(self, p: dict, rng: random.Random, skill: float) -> dict:
        """One move a so-so player would make now, for the dev bots (skill 0-1, about 0.5-0.9)."""
        raise NotImplementedError


ROLES = ["hacker", "safecracker", "lookout", "wheelman", "face", "muscle"]
# each role's first game, in heist.py
FIRST = {"hacker": "keypad", "safecracker": "dial", "lookout": "patrol", "wheelman": "route", "face": "cover",
         "muscle": "van"}
FIRST_TITLES = {"keypad": "CRACK THE KEYPAD", "dial": "FEEL FOR THE TUMBLERS", "patrol": "LEARN THE PATROL",
                "route": "PLOT THE GETAWAY", "cover": "BLAG PAST THE GUARD", "van": "LOAD THE VAN"}

GAMES: dict[str, dict[str, Game]] = {}
for _role in ROLES:
    try:
        GAMES[_role] = dict(importlib.import_module(f"{__name__}.{_role}").GAMES)   # (loud.py's are got by get())
    except ImportError:
        GAMES[_role] = {}


def get(role: str, key: str | None) -> Game | None:
    if key == "loud" or (key or "").startswith("loose-"):
        from lib.activities.heist_games.loud import LOOSE, LOUD
        return LOUD.get(role) if key == "loud" else LOOSE.get(key[6:])
    return GAMES.get(role, {}).get(key) if key else None


def keys(role: str) -> list[str]:
    """Every game a role can play: its first, then the rest."""
    return [FIRST[role], *GAMES.get(role, {})]


def deal(rng: random.Random, roles: list[str]) -> list[str]:
    """A game for each thing in a job, in order, given each thing's role: no role plays the same game twice until
    it's played them all."""
    decks: dict[str, list[str]] = {}
    out = []
    for role in roles:
        if not decks.get(role):
            decks[role] = rng.sample(keys(role), len(keys(role)))
        out.append(decks[role].pop())
    return out
