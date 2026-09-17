import unittest
from unittest.mock import patch

from lib.economy.economy_manager import ShutcoinManager, add_shutcoins, remove_shutcoin


class TestShutcoinLedger(unittest.TestCase):
    @patch("lib.economy.economy_manager.DatabaseManager.execute")
    @patch("lib.economy.economy_manager.DatabaseManager.fetch_one", return_value=(3,))
    def test_credits_are_logged_with_their_reason(self, _fetch, mock_exec):
        add_shutcoins(42, 5, reason="lucky dip")
        sqls = [c[0][0] for c in mock_exec.call_args_list]
        self.assertTrue(any("INTO shutcoins " in s for s in sqls))
        ledger = [c for c in mock_exec.call_args_list if "shutcoin_ledger" in c[0][0]]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0][0][1][0], "42")
        self.assertEqual(ledger[0][0][1][2:], (5, "lucky dip"))

    @patch("lib.economy.economy_manager.DatabaseManager.execute")
    def test_a_spent_coin_is_logged_only_when_the_balance_allowed_it(self, mock_exec):
        class Cursor:
            rowcount = 1
            def execute(self, *a): pass
        class Conn:
            def cursor(self): return Cursor()
            def commit(self): pass
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with patch("lib.economy.economy_manager.DatabaseManager.locked_connection", return_value=Conn()):
            self.assertTrue(remove_shutcoin(42))
        ledger = [c for c in mock_exec.call_args_list if "shutcoin_ledger" in c[0][0]]
        self.assertEqual(ledger[0][0][1][2:], (-1, "shut"))
        mock_exec.reset_mock()
        Cursor.rowcount = 0
        with patch("lib.economy.economy_manager.DatabaseManager.locked_connection", return_value=Conn()):
            self.assertFalse(remove_shutcoin(42))
        self.assertFalse([c for c in mock_exec.call_args_list if "shutcoin_ledger" in c[0][0]])

    @patch("lib.economy.economy_manager.DatabaseManager.execute", side_effect=[None, Exception("no such table")])
    @patch("lib.economy.economy_manager.DatabaseManager.fetch_one", return_value=(0,))
    def test_ledger_failure_never_blocks_the_coin(self, _fetch, mock_exec):
        ShutcoinManager.add_amount(7, 2, reason="shop")   # balance write ok, ledger write raises: swallowed
        self.assertEqual(mock_exec.call_count, 2)


if __name__ == "__main__":
    unittest.main()
