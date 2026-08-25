"""Unit tests for Higher or Lower game logic and odds calculation."""

import pytest
from commands.economy.higher_lower import HigherLowerGame, _value, RANKS, SUITS


def test_higher_lower_odds_on_king():
    """A King must offer both Higher (for Aces) and Lower (for 2-Queen)."""
    # Create deck with King dealt, remaining 51 cards
    deck = [r + s for s in SUITS for r in RANKS if (r + s) != "KH"]
    game = HigherLowerGame("test1", 12345, "TestPlayer", 999, 50, deck, "KH")
    
    assert game.mult_higher is not None, "Higher should be available for Aces"
    assert game.mult_lower is not None, "Lower must be available for 2-Queen"
    assert game.mult_higher > game.mult_lower
    assert game.mult_lower >= 1.01


def test_higher_lower_odds_on_ace():
    """An Ace has no higher cards, but Lower must be available."""
    deck = [r + s for s in SUITS for r in RANKS if (r + s) != "AH"]
    game = HigherLowerGame("test2", 12345, "TestPlayer", 999, 50, deck, "AH")
    
    assert game.mult_higher is None, "Higher on Ace is impossible"
    assert game.mult_lower is not None, "Lower on Ace must be offered"
    assert game.mult_lower >= 1.01


def test_higher_lower_odds_on_two():
    """A 2 has no lower cards, but Higher must be available."""
    deck = [r + s for s in SUITS for r in RANKS if (r + s) != "2H"]
    game = HigherLowerGame("test3", 12345, "TestPlayer", 999, 50, deck, "2H")
    
    assert game.mult_lower is None, "Lower on 2 is impossible"
    assert game.mult_higher is not None, "Higher on 2 must be offered"
    assert game.mult_higher >= 1.01


def test_higher_lower_all_ranks_playable():
    """Every rank from 2 to Ace must offer at least one valid direction."""
    deck = [r + s for s in SUITS for r in RANKS]
    for r in RANKS:
        card = r + "S"
        rem_deck = [c for c in deck if c != card]
        game = HigherLowerGame("test_" + r, 12345, "TestPlayer", 999, 50, rem_deck, card)
        
        if r == "A":
            assert game.mult_higher is None
            assert game.mult_lower is not None
        elif r == "2":
            assert game.mult_higher is not None
            assert game.mult_lower is None
        else:
            assert game.mult_higher is not None, f"Rank {r} missing higher"
            assert game.mult_lower is not None, f"Rank {r} missing lower"
