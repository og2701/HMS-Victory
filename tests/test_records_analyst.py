"""The records analyst (lib/features/records_analyst.py): read-only, allowlisted SQL for questions the fixed
records menu can't answer, and the tool loop around it."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest

from lib.features import records_analyst as ra


def _make_db(path):
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE ukpence (user_id TEXT, balance INTEGER);
        INSERT INTO ukpence VALUES ('1', 500), ('2', 40080), ('3', 7);
        CREATE TABLE casino_results (id INTEGER PRIMARY KEY, user_id TEXT, game TEXT, bet INT, staked INT, payout INT, net INT,
                                     outcome TEXT, result TEXT, timestamp INT);
        INSERT INTO casino_results VALUES (1, '2', 'mines', 5000, 5000, 45080, 40080, 'win', 'win', 1784635620);
        INSERT INTO casino_results VALUES (2, '1', 'slots', 100, 100, 0, -100, 'loss', 'loss', 1784635700);
        CREATE TABLE user_transactions (id INTEGER PRIMARY KEY, user_id TEXT, ts INT, amount INT, balance_after INT, reason TEXT,
                                        counterparty_id TEXT, source TEXT);
        INSERT INTO user_transactions VALUES (1, '2', 1784635620, 45080, 46000, 'Mines cashout', NULL, 'live');
        INSERT INTO user_transactions VALUES (2, '2', 1784640000, -30000, 16000, 'Pay', '1', 'live');
        CREATE TABLE message_archive (message_id TEXT, channel_id TEXT, user_id TEXT, content TEXT, attachments TEXT, ts INT);
        INSERT INTO message_archive VALUES ('m1', 'pub', '1', 'hello general', NULL, 1784635000);
        INSERT INTO message_archive VALUES ('m2', 'staff', '2', 'secret staff chat', NULL, 1784635001);
        CREATE TABLE detection_events (id INTEGER PRIMARY KEY, ts INT, user_id TEXT, kind TEXT, meta TEXT);
        INSERT INTO detection_events VALUES (1, 1, '1', 'alt', '{}');
        CREATE TABLE bb_diary (id INTEGER PRIMARY KEY, user_id TEXT, anonymous INT, text TEXT, created_at INT);
        INSERT INTO bb_diary VALUES (1, '1', 1, 'private diary', 1);
    """)
    c.commit()
    c.close()


SCOPE = ra.Scope(members=[("1", "Mahdi", "mahdi"), ("2", "Honey G", "honeyg")], public_channels=[("pub", "general")])


class TestReadOnlyRecords(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "database.db")
        _make_db(self.path)
        self.db = ra.ReadOnlyRecords(self.path, SCOPE)

    def tearDown(self):
        self.db.close()
        self.dir.cleanup()

    def test_reads_allowed_tables_and_joins_names(self):
        out = self.db.run("SELECT m.name, c.game, c.net FROM casino_results c JOIN members m ON m.user_id = c.user_id ORDER BY c.net DESC LIMIT 1")
        self.assertIn("Honey G | mines | 40080", out)
        out = self.db.run("WITH w AS (SELECT user_id, MAX(net) n FROM casino_results GROUP BY user_id) SELECT COUNT(*) FROM w;")
        self.assertIn("2", out.splitlines()[1])

    def test_follows_money_through_the_ledger(self):
        out = self.db.run("SELECT t.reason, t.amount, m.name FROM user_transactions t LEFT JOIN members m ON m.user_id = t.counterparty_id "
                          "WHERE t.user_id = '2' AND t.ts > 1784635620 ORDER BY t.ts")
        self.assertIn("Pay | -30000 | Mahdi", out)

    def test_tables_off_the_map_are_refused(self):
        for q in ("SELECT * FROM detection_events", "SELECT text FROM bb_diary", "SELECT name FROM sqlite_master",
                  "SELECT * FROM message_archive", "SELECT * FROM pragma_table_info('ukpence')",
                  "WITH x AS (SELECT * FROM detection_events) SELECT * FROM x",
                  "SELECT * FROM (SELECT content FROM message_archive)", "SELECT * FROM temp.sqlite_master"):
            self.assertTrue(self.db.run(q).startswith("error"), q)

    def test_staff_channel_messages_never_show(self):
        out = self.db.run("SELECT content FROM messages")
        self.assertIn("hello general", out)
        self.assertNotIn("secret staff chat", out)
        out = self.db.run("SELECT COUNT(*) FROM messages WHERE channel_id = 'staff'")
        self.assertEqual(out.splitlines()[1].strip(), "0")

    def test_nothing_but_select_runs(self):
        for q in ("DELETE FROM ukpence", "UPDATE ukpence SET balance = 0", "DROP TABLE ukpence", "INSERT INTO ukpence VALUES ('9', 1)",
                  "SELECT 1; DROP TABLE ukpence", "ATTACH DATABASE 'x.db' AS x", "PRAGMA query_only = OFF",
                  "SELECT load_extension('evil')", "WITH x AS (SELECT 1) DELETE FROM ukpence"):
            self.assertTrue(self.db.run(q).startswith("error"), q)
        self.assertIn("3", self.db.run("SELECT COUNT(*) FROM ukpence").splitlines()[1])

    def test_runaway_queries_are_cut_off_and_rows_capped(self):
        ra.QUERY_SECONDS, old = 0.3, ra.QUERY_SECONDS
        try:
            out = self.db.run("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r")
        finally:
            ra.QUERY_SECONDS = old
        self.assertIn("longer than", out)
        out = self.db.run("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 500) SELECT n FROM r")
        self.assertEqual(len(out.splitlines()), ra.MAX_ROWS + 2)
        self.assertIn("more rows not shown", out)

    def test_unknown_data_file(self):
        self.assertTrue(ra.read_data_file("join_watch").startswith("error"))


class TestAnswerLoop(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "database.db")
        _make_db(self.path)

    async def asyncTearDown(self):
        self.dir.cleanup()

    async def test_queries_then_answers_and_counts_usage(self):
        seen = []

        async def fake(messages, tools):
            seen.append(messages[-1])
            if len(seen) == 1:
                self.assertIn("casino_results", messages[0]["content"])
                self.assertEqual(json.loads(messages[1]["content"])["replied_to"]["author"], "HMS Victory")
                return {"content": None, "usage": (1000, 50), "tool_calls": [{"id": "a", "name": "run_sql", "arguments": json.dumps(
                    {"query": "SELECT c.game FROM casino_results c WHERE c.user_id = '2' ORDER BY c.net DESC LIMIT 1"})}]}
            self.assertEqual(messages[-1]["role"], "tool")
            self.assertIn("mines", messages[-1]["content"])
            return {"content": "On mines, 21 Jul 2026.", "usage": (1200, 30), "tool_calls": []}

        out = await ra.answer("on what game?", scope=SCOPE, asker=("oggers", "9"), replied_to=("HMS Victory", "1. 40,080 UKP Honey G"),
                              db_path=self.path, complete=fake)
        self.assertEqual(out.text, "On mines, 21 Jul 2026.")
        self.assertEqual((out.prompt_tokens, out.completion_tokens), (2200, 80))
        self.assertEqual(len(out.queries), 1)

    async def test_a_model_that_never_answers_gives_up(self):
        async def fake(messages, tools):
            return {"content": None, "usage": (10, 1), "tool_calls": [{"id": "x", "name": "run_sql", "arguments": '{"query": "SELECT 1"}'}]}
        self.assertIsNone(await ra.answer("q", scope=SCOPE, asker=("a", "1"), db_path=self.path, complete=fake))

    async def test_bad_sql_comes_back_as_an_error_the_model_can_fix(self):
        calls = []

        async def fake(messages, tools):
            calls.append(1)
            if len(calls) == 1:
                return {"content": None, "usage": (1, 1), "tool_calls": [{"id": "x", "name": "run_sql", "arguments": '{"query": "SELECT * FROM detection_events"}'}]}
            self.assertTrue(messages[-1]["content"].startswith("error"))
            return {"content": "That isn't something I can share.", "usage": (1, 1), "tool_calls": []}
        out = await ra.answer("who's been flagged as an alt", scope=SCOPE, asker=("a", "1"), db_path=self.path, complete=fake)
        self.assertEqual(out.text, "That isn't something I can share.")

    async def test_a_missing_database_is_no_answer(self):
        async def fake(messages, tools):
            raise AssertionError("never called")
        self.assertIsNone(await ra.answer("q", scope=SCOPE, asker=("a", "1"), db_path=os.path.join(self.dir.name, "nope.db"), complete=fake))


if __name__ == "__main__":
    unittest.main()
