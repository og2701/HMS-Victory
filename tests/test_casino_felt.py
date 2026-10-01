import os
import re
import unittest
from unittest import mock

os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy import casino_felt as felt

LEDGER = [("Bet", "500"), ("Balance", "12,340"), ("Session", "+350"), ("Career", "−1,840")]


def _no_db(*_args, **_kwargs):
    return LEDGER


class PiecesTests(unittest.TestCase):
    def test_cards(self):
        ten = felt.card("TH", "lg")
        self.assertIn("<b>10</b>", ten)
        self.assertIn("card lg red", ten)
        self.assertIn("back", felt.card(None, "md"))
        self.assertIn(" lit", felt.card("AS", "sm", lit=True))

    def test_long_fans_stay_on_the_table(self):
        html = felt.fan(["2H", "3C", "4S", "2D", "AC", "5H", "9S", "3D"], "lg")
        margins = [int(m) for m in re.findall(r"margin-left:(-?\d+)px", html)]
        self.assertEqual(len(margins), 7)
        width = felt.CARD_W["lg"] + sum(felt.CARD_W["lg"] + m for m in margins)
        self.assertLessEqual(width, felt.TABLE_W)
        # A short hand keeps the full step.
        self.assertIn(f"margin-left:{felt.FAN_STEP['lg'] - felt.CARD_W['lg']}px", felt.fan(["KS", "7D"]))

    def test_signed_and_words(self):
        self.assertEqual(felt.signed(1000), "+1,000")
        self.assertEqual(felt.signed(-500), "−500")
        self.assertEqual(felt.signed(0), "0")
        self.assertEqual(felt.rank_with_article("AS"), "an Ace")
        self.assertEqual(felt.rank_with_article("8H"), "an 8")
        self.assertEqual(felt.rank_with_article("KD"), "a King")

    def test_rail(self):
        win = felt.rail("You win", "20 against 17", money="+500", tone="win", ledger=LEDGER)
        self.assertIn('money serif win">+500', win)
        self.assertEqual(win.count('class="c"'), 4)
        crowded = felt.rail("1,420 banked", actions="Keep going or cash out")
        self.assertIn("head serif small", crowded)
        self.assertNotIn("small", felt.rail("Your move", actions="Hit · Stand"))

    def test_page_is_filled_in_one_pass(self):
        body = felt.seat("{{RAIL}} <b>bold</b>", felt.fan(["AS"]), "21")
        page = felt.build_page("Blackjack", "Hand 1", body, felt.rail("Push"))
        self.assertIn("{{RAIL}} &lt;b&gt;bold&lt;/b&gt;", page)
        self.assertEqual(page.count('class="rail"'), 1)
        self.assertNotIn("{{", page.replace("{{RAIL}} &lt;b&gt;", ""))

    def test_paytable_and_held_hand(self):
        pay = felt.paytable([(6, "Full House", 9), (1, "Jacks or Better", 1)], hit=6)
        self.assertIn('class="r hit"><span class="n">Full House', pay)
        held = felt.held_hand(["JS", "JH", "4D", "9C", "KC"], [True, True, False, False, False])
        self.assertEqual(held.count('class="held"'), 2)
        self.assertNotIn("held", felt.held_hand(["JS", "JH", "4D", "9C", "KC"], None))


@mock.patch.object(felt, "player_ledger", _no_db)
class GameTableTests(unittest.TestCase):
    def test_blackjack_hides_the_hole_card(self):
        import commands.economy.blackjack as bj
        game = bj.BlackjackGame("g", 1, "spookyfox", 1, 500, [], ["TH", "6C"], ["KS", "7D"])
        page = bj.build_table_html(game)
        self.assertIn("card lg back", page)
        self.assertNotIn("<b>7</b>", page)
        self.assertIn("Dealer shows a King", page)
        self.assertIn("Hit · Stand · Double", page)

    def test_blackjack_bust(self):
        import commands.economy.blackjack as bj
        game = bj.BlackjackGame("g", 1, "spookyfox", 1, 500, [], ["TH", "6C", "9S"], ["KS", "7D"],
                                state="over", hole_revealed=True)
        bj._decide(game)
        page = bj.build_table_html(game)
        self.assertIn(">Bust<", page)
        self.assertIn('money serif lose">−500', page)
        self.assertIn("total serif bust", page)

    def test_higher_lower_marks_the_busting_move(self):
        import commands.economy.higher_lower as hl
        deck = [r + s for s in hl.SUITS for r in hl.RANKS if r + s not in ("9H", "4S", "JC")]
        game = hl.HigherLowerGame("h", 1, "spookyfox", 1, 500, deck, "9H", history=["4S", "JC"], steps=1)
        game.state, game.outcome = "over", "lose"
        page = hl.build_hl_html(game)
        self.assertEqual(page.count('class="step"'), 1)
        self.assertEqual(page.count('class="step bad"'), 1)
        self.assertIn("Busted", page)

    def test_video_poker_highlights_the_current_hand(self):
        import commands.economy.video_poker as vp
        game = vp.VideoPokerGame("v", 1, "spookyfox", 1, 500, [], ["JS", "JH", "4D", "9C", "KC"],
                                 held=[True, True, False, False, False])
        page = vp.build_html(game)
        self.assertIn('class="r hit"><span class="n">Jacks or Better', page)
        self.assertIn("Already pays your bet back", page)

    def test_red_dog_consecutive_has_no_third_card(self):
        import commands.economy.red_dog as rd
        game = rd.RedDogGame("r", 1, "spookyfox", 1, 500, [], "4C", "5H", state="over")
        game.outcome, game.net = "push", 0
        page = rd.build_html(game)
        self.assertEqual(page.count('class="card md'), 2)
        self.assertIn("Consecutive cards", page)


if __name__ == "__main__":
    unittest.main()
