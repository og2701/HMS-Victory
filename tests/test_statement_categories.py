import os
import unittest

os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy.statement import _categorize


class CategoryTests(unittest.TestCase):
    def test_every_casino_game_counts_as_casino(self):
        for reason in ("Glass Bridge bet", "Glass Bridge cashout", "Mines bet", "Mines cashout", "Penalty bet",
                       "Penalty cashout", "Penalty five from five", "Chest bet", "Chest cashout",
                       "Blockade Run bet", "Blockade Run cashout", "Darts bet", "Blackjack win", "Roulette win"):
            self.assertEqual(_categorize(reason)[0], "Casino", reason)

    def test_head_to_head_games_and_greetings(self):
        for reason in ("Connect 4 stake", "Connect 4 vs AI stake", "Battleship win", "Rock Paper Scissors stake"):
            self.assertEqual(_categorize(reason)[0], "Games", reason)
        self.assertEqual(_categorize("You stayed and talked to a new member")[0], "Welcome")
        self.assertEqual(_categorize("Lucky Dip win (+150 UKPence)")[0], "Shop")

    def test_transfers_win_on_a_counterparty(self):
        self.assertEqual(_categorize("/pay Pooja → Alex", counterparty_id=123)[0], "Transfers")
        self.assertEqual(_categorize("Mines bet", counterparty_id=123)[0], "Transfers")

    def test_a_fraud_fine_is_a_fine_not_a_benefit(self):
        self.assertEqual(_categorize("Paid benefits fraud fine for 795003706717372462")[0], "Fines")
        self.assertEqual(_categorize("Paid benefits fraud fine")[0], "Fines")
        self.assertEqual(_categorize("Weekly benefits")[0], "Benefits")
        self.assertEqual(_categorize("Lucky Dip penalty (Parking Fine)")[0], "Shop")


if __name__ == "__main__":
    unittest.main()
