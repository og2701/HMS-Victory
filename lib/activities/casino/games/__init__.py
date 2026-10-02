"""One adapter per house game. Each wraps the game's own module in commands/economy."""

from lib.activities.casino.games.blackjack import Blackjack

ADAPTERS = [
    Blackjack(),
]
