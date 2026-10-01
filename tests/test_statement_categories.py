import os
import unittest

os.environ.setdefault("OPENAI_TOKEN", "mock-token")

from lib.economy.statement import _categorize, _describe


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
        for reason in ("Shop purchase: Lucky Dip", "Lucky Dip win (+150 UKPence)", "Lucky Dip penalty (Council Tax)",
                       "Lucky Dip penalty (HMRC Tax Raid)", "Lucky Dip penalty (Parking Fine)",
                       "Shop refund: Lucky Dip (execute error: NotFound)"):
            self.assertEqual(_categorize(reason)[0], "Lucky Dip", reason)
        self.assertEqual(_categorize("Shop purchase: Rank Card Theme")[0], "Shop")

    def test_transfers_win_on_a_counterparty(self):
        self.assertEqual(_categorize("/pay Pooja → Alex", counterparty_id=123)[0], "Transfers")
        self.assertEqual(_categorize("Mines bet", counterparty_id=123)[0], "Transfers")

    def test_a_fraud_fine_is_a_fine_not_a_benefit(self):
        self.assertEqual(_categorize("Paid benefits fraud fine for 795003706717372462")[0], "Fines")
        self.assertEqual(_categorize("Paid benefits fraud fine")[0], "Fines")
        self.assertEqual(_categorize("Weekly benefits")[0], "Benefits")
        self.assertEqual(_categorize("Lucky Dip penalty (Parking Fine)")[0], "Lucky Dip")


class DescribeTests(unittest.TestCase):
    class _Client:
        def get_user(self, uid):
            if uid == 795003706717372462:
                return type("U", (), {"display_name": "Pengrin"})()
            return None

    def test_raw_ids_become_names(self):
        self.assertEqual(_describe("Paid benefits fraud fine for 795003706717372462", None, -83, self._Client()),
                         "Paid benefits fraud fine for Pengrin")

    def test_unknown_members_become_mentions_not_digits(self):
        desc = _describe("Paid benefits fraud fine for 123456789012345678", None, -83, self._Client())
        self.assertTrue(desc.endswith("<@123456789012345678>"), desc)

    def test_ordinary_reasons_are_untouched(self):
        self.assertEqual(_describe("Mines bet", None, -50, self._Client()), "Mines bet")


if __name__ == "__main__":
    unittest.main()
