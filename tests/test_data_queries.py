import time
import unittest
from unittest.mock import patch

from lib.features import data_queries as dq
from lib.features.data_queries import METRICS, QuerySpec, compute, render, catalogue_for_jev, normalise_limit


def _fake_fetch(calls=None):
    """A stand-in for the DB: dispatches on the table in the SQL, records (sql, params)."""
    def fetch(sql, params=()):
        if calls is not None:
            calls.append((sql, tuple(params)))
        if "FROM shutcoins" in sql:
            return [("1", 14), ("2", 9), ("3", 0), ("4", 2), ("999", 50)]
        if "FROM shop_purchases" in sql:
            return [("1", 20), ("2", 9), ("5", 3)]
        if "FROM xp" in sql:
            return [("1", 1240), ("2", 300), ("3", 5000), ("4", 300)]
        if "FROM casino_results" in sql:
            if params and params[0] == "blackjack":
                return [("1", -500), ("2", 120)]
            return [("1", -2000), ("2", 800), ("3", 0)]
        if "FROM pvp_results" in sql:
            if "winner_id, COUNT" in sql:
                return [("1", 3), ("2", 1)]
            if "loser_id, COUNT" in sql:
                return [("1", 1), ("2", 3), ("4", 2)]
        return []
    return fetch


class TestRegistry(unittest.TestCase):
    def test_catalogue_offers_none_plus_every_metric(self):
        crit = catalogue_for_jev()
        self.assertIn("none", crit)
        for key, m in METRICS.items():
            self.assertIn(key, crit)
            self.assertEqual(crit[key]["what"], m.what)
            self.assertTrue(crit[key]["examples"])

    def test_every_metric_has_examples_and_a_unit_label(self):
        for m in METRICS.values():
            self.assertTrue(m.label, m.key)
            self.assertGreaterEqual(len(m.examples), 2, m.key)

    def test_missing_table_is_no_rows_not_an_error(self):
        with patch("database.DatabaseManager.fetch_all", side_effect=Exception("no such table: county_instances")):
            self.assertEqual(dq._fetch("SELECT 1 FROM county_instances"), [])
            self.assertEqual(METRICS["counties"].rows(None, None), [])

    def test_limit_is_clamped(self):
        self.assertEqual(normalise_limit(None), 10)
        self.assertEqual(normalise_limit(1), 3)
        self.assertEqual(normalise_limit(50), 20)
        self.assertEqual(normalise_limit(5), 5)


class TestCompute(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._p = patch("lib.features.data_queries._fetch", _fake_fetch(self.calls))
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_leaderboard_drops_zeros_and_excluded_ids(self):
        res = compute(QuerySpec(metric="shutcoins", shape="leaderboard", limit=10), exclude_ids={"999"})
        self.assertEqual(res.rows, [("1", 14), ("2", 9), ("4", 2)])
        self.assertEqual(res.ranks, {"1": 1, "2": 2, "4": 3})
        self.assertEqual(res.population, 3)
        self.assertEqual(res.total, 25)

    def test_leaderboard_respects_limit_and_member_filter(self):
        res = compute(QuerySpec(metric="xp", shape="leaderboard", limit=3), member_ids={"1", "2", "4"})
        self.assertEqual(res.rows, [("1", 1240), ("2", 300), ("4", 300)])
        # 2 and 4 tie on 300: competition rank gives both 2nd
        self.assertEqual(res.ranks["2"], 2)
        self.assertEqual(res.ranks["4"], 2)

    def test_lowest_keeps_zeros(self):
        res = compute(QuerySpec(metric="shutcoins", shape="leaderboard", limit=3, lowest=True), exclude_ids={"999"})
        self.assertEqual(res.rows, [("3", 0), ("4", 2), ("2", 9)])
        self.assertEqual(res.ranks["3"], 1)

    def test_person_has_rank_out_of_population(self):
        res = compute(QuerySpec(metric="xp", shape="person", subjects=[("Steven", "2")]))
        self.assertEqual(res.rows, [("2", 300)])
        self.assertEqual(res.ranks["2"], 3)   # 5000, 1240 are better
        self.assertEqual(res.population, 4)

    def test_person_with_nothing_on_record(self):
        res = compute(QuerySpec(metric="xp", shape="person", subjects=[("Nobody", "77")]))
        self.assertEqual(res.rows, [("77", 0)])
        self.assertIn("77", res.ids)

    def test_compare_and_total(self):
        res = compute(QuerySpec(metric="xp", shape="compare", subjects=[("A", "1"), ("B", "3")]))
        self.assertEqual(res.rows, [("1", 1240), ("3", 5000)])
        tot = compute(QuerySpec(metric="xp", shape="total"))
        self.assertEqual(tot.total, 6840)
        self.assertEqual(tot.rows, [])

    def test_derived_shutcoins_used_is_bought_minus_held(self):
        res = compute(QuerySpec(metric="shutcoins_used", shape="leaderboard"), exclude_ids={"999"})
        # 1: 20-14=6, 2: 9-9=0 (dropped), 5: 3-0=3, 3/4 bought nothing -> 0 (dropped)
        self.assertEqual(res.rows, [("1", 6), ("5", 3)])

    def test_game_filter_and_window_reach_the_sql(self):
        compute(QuerySpec(metric="casino_net", shape="leaderboard", game="blackjack", window="week"))
        sql, params = self.calls[-1]
        self.assertIn("game = ?", sql)
        self.assertIn("timestamp >= ?", sql)
        self.assertEqual(params[0], "blackjack")
        self.assertAlmostEqual(params[1], int(time.time()) - 7 * 86400, delta=5)

    def test_game_filter_ignored_for_metrics_without_games(self):
        compute(QuerySpec(metric="xp", shape="leaderboard", game="blackjack", window="week"))
        sql, params = self.calls[-1]
        self.assertNotIn("game = ?", sql)
        self.assertEqual(params, ())

    def test_messages_default_to_the_archive_window(self):
        compute(QuerySpec(metric="messages", shape="total"))
        sql, params = self.calls[-1]
        self.assertIn("ts >= ?", sql)
        self.assertAlmostEqual(params[0], int(time.time()) - 30 * 86400, delta=5)

    def test_pvp_games_merges_both_sides(self):
        res = compute(QuerySpec(metric="pvp_games", shape="leaderboard"))
        self.assertEqual(dict(res.rows), {"1": 4, "2": 4, "4": 2})

    def test_signed_metric_keeps_losers_in_lowest_view(self):
        res = compute(QuerySpec(metric="casino_net", shape="leaderboard", lowest=True, limit=3))
        self.assertEqual(res.rows, [("1", -2000), ("3", 0), ("2", 800)])


class TestRender(unittest.TestCase):
    def setUp(self):
        self._p = patch("lib.features.data_queries._fetch", _fake_fetch())
        self._p.start()
        self.addCleanup(self._p.stop)
        self.names = {"1": "Johnny", "2": "Steven", "3": "Kim", "4": "Hadidas", "5": "Chin"}

    def test_leaderboard_block(self):
        res = compute(QuerySpec(metric="shutcoins", shape="leaderboard", limit=10), exclude_ids={"999"})
        text = render(res, self.names)
        self.assertTrue(text.startswith("Top 3 by shutcoins held\n```"))
        self.assertIn(" 1. Johnny   14 shutcoins", text)
        self.assertIn(" 3. Hadidas  2 shutcoins", text)
        self.assertNotIn("Kim", text)

    def test_leaderboard_scope_and_signed_values(self):
        res = compute(QuerySpec(metric="casino_net", shape="leaderboard", game="blackjack", window="week", lowest=True, limit=5))
        text = render(res, self.names)
        self.assertIn("Bottom 2 by casino profit and loss (blackjack, last 7 days)", text)
        self.assertIn("Johnny  -500 UKP", text)
        self.assertIn("Steven  +120 UKP", text)

    def test_empty_leaderboard(self):
        res = compute(QuerySpec(metric="counties", shape="leaderboard"))
        self.assertEqual(render(res, {}), "Nobody has any counties caught on record.")

    def test_person_line(self):
        res = compute(QuerySpec(metric="xp", shape="person", subjects=[("Steven", "2")]))
        self.assertEqual(render(res, self.names), "**Steven**: 300 XP (rank 3 of 4)")

    def test_person_with_note_and_unknown_name(self):
        res = compute(QuerySpec(metric="shutcoins_used", shape="person", subjects=[("?", "5")]))
        text = render(res, {})
        self.assertIn("**user 5**: 3 shutcoins used", text)
        self.assertIn("bought minus held", text)

    def test_compare_verdict(self):
        res = compute(QuerySpec(metric="xp", shape="compare", subjects=[("Johnny", "1"), ("Kim", "3")]))
        text = render(res, self.names)
        self.assertIn("**Johnny**: 1,240 XP", text)
        self.assertIn("**Kim**: 5,000 XP", text)
        self.assertIn("Kim ahead by 3,760 XP.", text)
        net = compute(QuerySpec(metric="casino_net", shape="compare", game="blackjack", subjects=[("Johnny", "1"), ("Steven", "2")]))
        text = render(net, self.names)
        self.assertIn("**Johnny**: -500 UKP (casino profit and loss)", text)
        self.assertIn("Steven ahead by 620 UKP. (blackjack)", text)
        tie = compute(QuerySpec(metric="xp", shape="compare", subjects=[("Steven", "2"), ("Hadidas", "4")]))
        self.assertIn("Dead level.", render(tie, self.names))

    def test_unitless_metric_reads_naturally(self):
        with patch("lib.features.data_queries._fetch", lambda sql, params=(): [("1", 7)] if "shut_counts" in sql else []):
            res = compute(QuerySpec(metric="times_shut", shape="person", subjects=[("Johnny", "1")]))
            self.assertEqual(render(res, self.names), "**Johnny**: 7 times shut (rank 1 of 1)")
            board = compute(QuerySpec(metric="times_shut", shape="leaderboard"))
            self.assertIn(" 1. Johnny  7", render(board, self.names))

    def test_total_line(self):
        res = compute(QuerySpec(metric="xp", shape="total"))
        self.assertEqual(render(res, {}), "Total XP: 6,840 XP across 4 members")


if __name__ == "__main__":
    unittest.main()
