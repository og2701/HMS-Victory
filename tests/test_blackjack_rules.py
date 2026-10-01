import os
import unittest

os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import commands.economy.blackjack as bj


def _game(player, dealer, deck):
    # The deck is drawn from the end, so the next card dealt is deck[-1].
    return bj.BlackjackGame("g", 1, "spookyfox", 1, 100, list(deck), list(player), list(dealer))


class AutoStandTests(unittest.TestCase):
    def test_hitting_to_21_stands_for_you(self):
        g = _game(["7H", "4C"], ["KS", "6D"], ["2C", "5S", "KD"])   # hit the K for 21
        g.hit_player()
        self.assertEqual(g.player_total(), 21)
        self.assertEqual(g.state, "over")
        self.assertTrue(g.hole_revealed)
        self.assertGreaterEqual(g.dealer_total(), 17)              # the dealer played it out

    def test_a_soft_21_stands_too(self):
        g = _game(["AH", "5C"], ["KS", "7D"], ["5S"])               # A-5-5 = soft 21
        g.hit_player()
        self.assertEqual(g.state, "over")

    def test_under_21_you_can_keep_hitting(self):
        g = _game(["TH", "6C"], ["KS", "7D"], ["9C", "2S"])         # 16 + 2 = 18
        g.hit_player()
        self.assertEqual(g.player_total(), 18)
        self.assertEqual(g.state, "player")

    def test_a_bust_still_ends_without_the_dealer_drawing(self):
        g = _game(["TH", "6C"], ["KS", "2D"], ["9C", "KD"])         # 16 + K = 26
        g.hit_player()
        self.assertEqual(g.state, "over")
        self.assertEqual(len(g.dealer_cards), 2)


if __name__ == "__main__":
    unittest.main()
