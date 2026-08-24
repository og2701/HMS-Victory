import unittest
from lib.features.ukp_rewards import (
    _BENEFITS_POOLS,
    _BENEFITS_ALWAYS,
    _fields,
    _benefits_line,
    _benefits_stats,
    _BENEFITS_PERSONAL,
    _BENEFITS_PERSONAL_RICH,
    _BENEFITS_PERSONAL_ALREADY,
    _BENEFITS_PERSONAL_FRAUD_WARN,
    _BENEFITS_PERSONAL_FRAUD_BAN,
    _BENEFITS_PERSONAL_BANNED,
    _BENEFITS_DATA,
    _BENEFITS_DATA_RICH,
    _BENEFITS_DATA_ALREADY,
    _BENEFITS_DATA_FRAUD_WARN,
    _BENEFITS_DATA_FRAUD_BAN,
    _BENEFITS_DATA_BANNED,
    _BENEFITS_SUCCESS,
    _BENEFITS_RICH,
    _BENEFITS_ALREADY,
    _BENEFITS_FRAUD_WARN,
    _BENEFITS_FRAUD_BAN,
    _BENEFITS_BANNED,
)

SAMPLE_STATS = {
    "uid": "1234567890",
    "name": "TestUser",
    "amount": 50,
    "bal": 100,
    "balance": 100,
    "threshold": 250,
    "ts": 1700000000,
    "streak": 5,
    "days": 7,
    "out": 300,
    "fine": 75,
    "offenses": 2,
    "casino_games": 20,
    "casino_lost": 1500,
    "casino_up": 500,
    "never_gambled": "",
    "no_casino_wins": "",
    "roulette_played": 5,
    "roulette_lost": 200,
    "mines_played": 10,
    "mines_lost": 300,
    "blackjack_played": 5,
    "blackjack_lost": 100,
    "slots_played": 5,
    "slots_lost": 100,
    "higherlower_played": 5,
    "higherlower_lost": 100,
    "chest_played": 2,
    "chest_lost": 50,
    "pct_of_casino": "3.33",
    "pct_of_roulette": "25.00",
    "pct_of_mines": "16.67",
    "worst_loss": 500,
    "best_win": 1000,
    "paid_out_n": 4,
    "paid_out": 400,
    "paid_in_n": 2,
    "paid_in": 200,
    "claims": 15,
    "bonds": 2,
    "bonded": 5000,
    "shop_items": 3,
    "shop_spent": 150,
    "shut": 12,
}

ALL_POOLS = [
    _BENEFITS_SUCCESS,
    _BENEFITS_DATA,
    _BENEFITS_RICH,
    _BENEFITS_DATA_RICH,
    _BENEFITS_ALREADY,
    _BENEFITS_DATA_ALREADY,
    _BENEFITS_FRAUD_WARN,
    _BENEFITS_DATA_FRAUD_WARN,
    _BENEFITS_FRAUD_BAN,
    _BENEFITS_DATA_FRAUD_BAN,
    _BENEFITS_BANNED,
    _BENEFITS_DATA_BANNED,
]

ALL_PERSONAL_DICTS = [
    _BENEFITS_PERSONAL,
    _BENEFITS_PERSONAL_RICH,
    _BENEFITS_PERSONAL_ALREADY,
    _BENEFITS_PERSONAL_FRAUD_WARN,
    _BENEFITS_PERSONAL_FRAUD_BAN,
    _BENEFITS_PERSONAL_BANNED,
]


class TestBenefitsMessages(unittest.TestCase):
    def test_template_syntax_and_formatting(self):
        """Verify that every single template string formats without syntax errors."""
        for pool in ALL_POOLS:
            for line in pool:
                formatted = line.format(**SAMPLE_STATS)
                self.assertIsInstance(formatted, str)
                self.assertTrue(len(formatted) > 0)

        for pdict in ALL_PERSONAL_DICTS:
            for uid, lines in pdict.items():
                for line in lines:
                    formatted = line.format(**SAMPLE_STATS)
                    self.assertIsInstance(formatted, str)
                    self.assertTrue(len(formatted) > 0)

    def test_data_pools_have_placeholders(self):
        """Verify that every data-driven line references at least one stat beyond standard context."""
        for pool in [_BENEFITS_DATA, _BENEFITS_DATA_RICH, _BENEFITS_DATA_ALREADY, _BENEFITS_DATA_FRAUD_WARN, _BENEFITS_DATA_FRAUD_BAN, _BENEFITS_DATA_BANNED]:
            for line in pool:
                fields = _fields(line)
                self.assertTrue(len(fields) > 0)

    def test_benefits_line_resolves_all_categories(self):
        """Verify that _benefits_line works for all categories without crashing."""
        categories = ["success", "rich", "already", "fraud_warn", "fraud_ban", "banned"]
        for cat in categories:
            # Test with an unknown UID (uses data or house pool)
            res1 = _benefits_line(cat, "999999999999999999", amount=50, bal=50, threshold=250, ts=1700000000, days=3, out=100, fine=50, offenses=1)
            self.assertIsInstance(res1, str)
            self.assertTrue(len(res1) > 0)

            # Test with a seeded UID
            res2 = _benefits_line(cat, "285860055570579457", amount=50, bal=500, threshold=250, ts=1700000000, days=3, out=100, fine=50, offenses=1)
            self.assertIsInstance(res2, str)
            self.assertTrue(len(res2) > 0)


if __name__ == "__main__":
    unittest.main()
