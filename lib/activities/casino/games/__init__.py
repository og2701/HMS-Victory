"""One adapter per house game. Each wraps the game's own module in commands/economy."""

from lib.activities.casino.games.blackjack import Blackjack
from lib.activities.casino.games.cards import HigherLower, RedDog, ThreeCard, VideoPoker
from lib.activities.casino.games.ladders import Blockade, Chest, Darts, Glass, Mines, Penalty
from lib.activities.casino.games.spins import Roulette, Slots

ADAPTERS = [
    Blackjack(), Slots(), Mines(), Roulette(), HigherLower(), VideoPoker(), RedDog(),
    ThreeCard(), Chest(), Glass(), Blockade(), Darts(), Penalty(),
]
