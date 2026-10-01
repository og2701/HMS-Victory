import os
import re
import unittest

os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy import roulette_board as RB


class Table:
    def __init__(self, players, result=None):
        self.players, self.result = players, result

    @property
    def pot(self):
        return sum(sum(p["bets"].values()) for p in self.players.values())


def _players():
    return {1: {"name": "spookyfox", "bets": {"red": 500, "dozen2": 200, "straight:17": 100}},
            2: {"name": "Gunner", "bets": {"black": 1000, "straight:0": 50}},
            3: {"name": "<b>{{RAIL}}</b>", "bets": {"odd": 250, "col3": 2500}}}


class RouletteBoardTests(unittest.TestCase):
    def test_every_bet_gets_a_chip_in_its_player_colour(self):
        page = RB.build_table_html(Table(_players()))
        chips = re.findall(r'<circle cx="[^"]+" cy="[^"]+" r="20" fill="(#[0-9A-F]{6})"/>', page)
        self.assertEqual(len(chips), 7)
        self.assertEqual(chips.count(RB.CHIP_COLOURS[0]), 3)   # spookyfox sat down first
        self.assertIn(">2.5K<", page)
        self.assertIn("Place your bets", page)

    def test_results_light_the_winners_and_show_the_wheel(self):
        page = RB.build_results_html(Table(_players(), 17))
        self.assertIn("17 · Black", page)
        self.assertIn("Black · Odd · 1-18", page)
        self.assertIn('id="wwood"', page)                    # the little wheel is in the rail
        self.assertIn("+3,400", page)                        # red lost, 2nd 12 and #17 won
        self.assertIn("−2,250", page)                   # odd won 250, col 3 lost 2,500
        self.assertIn('opacity="0.35"', page)                # losing chips fade

    def test_zero_beats_every_outside_bet(self):
        page = RB.build_results_html(Table(_players(), 0))
        self.assertIn("0 · Green", page)
        self.assertIn("+750", page)                          # Gunner's 50 on zero pays 35:1

    def test_names_are_escaped_and_the_page_fills_once(self):
        page = RB.build_table_html(Table(_players()))
        self.assertIn("&lt;b&gt;{{RAIL}}&lt;/b&gt;", page)
        self.assertEqual(page.count('class="rail"'), 1)

    def test_an_empty_table_still_draws(self):
        page = RB.build_table_html(Table({}))
        self.assertIn("No bets yet", page)

    def test_chip_text_stays_short(self):
        self.assertEqual(RB.chip_text(500), "500")
        self.assertEqual(RB.chip_text(1000), "1K")
        self.assertEqual(RB.chip_text(2500), "2.5K")
        self.assertEqual(RB.chip_text(1_000_000), "1M")


if __name__ == "__main__":
    unittest.main()
