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
        if "FROM shutcoin_ledger" in sql:
            if "amount > 0" in sql:
                return [("5", 4), ("2", 1)]       # lucky-dip wins
            if "amount < 0" in sql:
                return [("1", 6), ("5", 7)]
        if "FROM casino_results" in sql:
            if params and params[0] == "blackjack":
                return [("1", -500), ("2", 120)]
            return [("1", -2000), ("2", 800), ("3", 0)]
        if "FROM pvp_results" in sql:
            if "winner_id, COUNT" in sql:
                return [("1", 3), ("2", 1)]
            if "loser_id, COUNT" in sql:
                return [("1", 1), ("2", 3), ("4", 2)]
            if "SELECT game, winner_id, loser_id" in sql:
                return [("connect4", "1", "2", "win", 50), ("connect4", "2", "1", "win", 50), ("battleship", "1", "4", "win", 100), ("rps", None, None, "draw", 10)]
        if "FROM user_transactions" in sql and "SUM(amount)" in sql:
            return [("1", 900), ("2", 100)]
        if "FROM user_transactions" in sql and "reason LIKE '%tax%'" in sql:
            return [("1", 40, "Server booster daily bonus [gross: 100, tax: -60 (60%)]"),
                    ("1", 20, "Top chatter daily reward [gross: 50, tax: -30 (60%)]"),
                    ("2", -25, "Lucky Dip penalty (Council Tax)"),
                    ("3", 200, "Syntax reward")]
        if "FROM member_profile" in sql:
            return [("1", 1_700_000_000), ("2", 1_720_000_000), ("3", 1_710_000_000)]
        if "FROM balance_history" in sql:
            return [("1", 5000), ("2", 40)]
        if "JOIN badges b" in sql and "b.name, b.rarity" in sql:
            return [("Warden", "Gold", 1_720_000_000), ("Shut Victim", "Bronze", 1_710_000_000), ("Night Owl", "Silver", 1_730_000_000)]
        if "JOIN badges b" in sql and "b.name = ?" in sql:
            return [("1", 1_700_000_000), ("2", 1_710_000_000)] if params[0] == "Warden" else []
        if "FROM county_instances WHERE user_id = ? GROUP BY county" in sql:
            return [("bedfordshire", 3), ("london", 1)]
        if "FROM county_instances WHERE county = ? GROUP BY user_id" in sql:
            return [("2", 2), ("1", 1)] if params[0] == "london" else []
        if "FROM bank" in sql:
            return [(1, 123456, 50000, 7000, 1000, 400, 300, 900)]
        if "PRAGMA table_info(bank)" in sql:
            return [(0, "id"), (1, "balance"), (2, "total_revenue"), (3, "total_tax_collected"), (4, "total_blackjack_in"), (5, "total_blackjack_out"), (6, "total_slots_in"), (7, "total_slots_out")]
        if "FROM lottery_rounds WHERE status = 'open'" in sql:
            return [(7, 10, 500, 10, 1_800_000_000)]
        if "FROM lottery_entries WHERE round_id = ?" in sql:
            return [(120, 9)]
        if "FROM lottery_rounds WHERE winner_id IS NOT NULL" in sql:
            return [("3", 900, 1_790_000_000)]
        if "FROM badges ORDER BY name" in sql:
            return [("Night Owl",), ("Shut Victim",), ("Warden",)]
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

    def test_derived_shutcoins_used_is_bought_plus_wins_minus_held(self):
        res = compute(QuerySpec(metric="shutcoins_used", shape="leaderboard"), exclude_ids={"999"})
        # 1: 20+0-14=6, 2: 9+1-9=1, 5: 3+4-0=7, 3/4 bought nothing -> 0 (dropped)
        self.assertEqual(res.rows, [("5", 7), ("1", 6), ("2", 1)])
        self.assertEqual(compute(QuerySpec(metric="shutcoins_won", shape="leaderboard")).rows, [("5", 4), ("2", 1)])
        self.assertEqual(compute(QuerySpec(metric="shuts_given", shape="leaderboard")).rows, [("5", 7), ("1", 6)])

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


class TestSourcesDatesAndAsOf(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._p = patch("lib.features.data_queries._fetch", _fake_fetch(self.calls))
        self._p.start()
        self.addCleanup(self._p.stop)
        self.names = {"1": "Johnny", "2": "Steven", "3": "Kim"}

    def test_earned_from_a_source_filters_the_ledger(self):
        res = compute(QuerySpec(metric="ukp_earned", shape="leaderboard", source="chatting", window="week"))
        sql, params = self.calls[-1]
        self.assertIn("amount > 0", sql)
        self.assertIn("reason LIKE ?", sql)
        self.assertEqual(params[0], "Chatting activity reward%")
        self.assertIn("ts >= ?", sql)
        self.assertEqual(res.rows, [("1", 900), ("2", 100)])
        self.assertIn("Top 2 by UKP earned (from chatting, last 7 days)", render(res, self.names))

    def test_spent_on_a_source_uses_debits(self):
        compute(QuerySpec(metric="ukp_spent", shape="total", source="casino"))
        sql, params = self.calls[-1]
        self.assertIn("amount < 0", sql)
        self.assertIn("-SUM(amount)", sql)
        self.assertGreater(len(params), 5)   # every casino spelling

    def test_tax_paid_is_parsed_from_the_reason_text(self):
        res = compute(QuerySpec(metric="tax_paid", shape="leaderboard"))
        self.assertEqual(res.rows, [("1", 90), ("2", 25)])      # withheld 60+30; an outright tax debit; "Syntax" is not tax
        compute(QuerySpec(metric="tax_paid", shape="total", window="week"))
        sql, params = self.calls[-1]
        self.assertIn("ts >= ?", sql)

    def test_unknown_source_means_everything(self):
        compute(QuerySpec(metric="ukp_earned", shape="total", source="bitcoin"))
        sql, params = self.calls[-1]
        self.assertNotIn("reason LIKE", sql)
        self.assertEqual(params, ())

    def test_date_metric_renders_dates_and_earliest_first(self):
        res = compute(QuerySpec(metric="first_seen", shape="leaderboard", lowest=True, limit=5))
        text = render(res, self.names)
        self.assertTrue(text.startswith("Earliest 3 by first seen"), text)
        self.assertIn(" 1. 14 Nov 2023  Johnny", text)
        person = compute(QuerySpec(metric="first_seen", shape="person", subjects=[("Kim", "3")]))
        self.assertEqual(render(person, self.names), "```\nKim: first seen 09 Mar 2024\n```")
        cmp_ = compute(QuerySpec(metric="first_seen", shape="compare", subjects=[("Johnny", "1"), ("Steven", "2")]))
        self.assertIn("Johnny earlier by 231 days.", render(cmp_, self.names))

    def test_balance_with_a_window_reads_the_history(self):
        res = compute(QuerySpec(metric="ukpence", shape="leaderboard", window="month"))
        sql, params = self.calls[-1]
        self.assertIn("FROM balance_history", sql)
        self.assertAlmostEqual(params[0], int(time.time()) - 30 * 86400, delta=5)
        self.assertEqual(res.rows, [("1", 5000), ("2", 40)])
        self.assertIn("(as of a month ago)", render(res, self.names))
        compute(QuerySpec(metric="ukpence", shape="leaderboard"))
        self.assertIn("FROM ukpence", self.calls[-1][0])


class TestLists(unittest.TestCase):
    def setUp(self):
        self._p = patch("lib.features.data_queries._fetch", _fake_fetch())
        self._p.start()
        self.addCleanup(self._p.stop)
        self.names = {"1": "Johnny", "2": "Steven", "3": "Kim", "4": "Hadidas"}

    def test_every_list_is_offered_to_jev(self):
        crit = dq.lists_for_jev()
        self.assertEqual(set(crit), set(dq.LISTS) | {"none"})
        self.assertEqual(set(dq.sources_for_jev()), set(dq.SOURCES) | {"all"})

    def test_profile_sheet_pulls_the_main_figures_with_ranks(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="profile", subjects=[("Steven", "2")]))
        text = render(res, self.names)
        self.assertTrue(text.startswith("```\nSteven's stats\n"), text)
        self.assertIn("\n300 XP (rank 3 of 4)", text)
        self.assertIn("\n9 shutcoins held (rank 3 of 4)", text)
        # departed members drop out of the ranks when the guild roster is known
        scoped = compute(QuerySpec(metric="none", shape="list", list_kind="profile", subjects=[("Steven", "2")]), member_ids={"1", "2"})
        self.assertIn("\n300 XP (rank 2 of 2)", render(scoped, self.names))
        with patch("lib.features.data_queries._fetch", lambda sql, params=(): [("2", 77)] if "FROM message_archive" in sql else []):
            msgs = render(compute(QuerySpec(metric="none", shape="list", list_kind="profile", subjects=[("Steven", "2")])), self.names)
            self.assertIn("\n77 messages sent (last 30 days) (rank 1 of 1)", msgs)
        self.assertIn("\n+800 UKP (casino profit and loss) (rank 1 of 2)", text)
        self.assertIn("\nfirst seen 03 Jul 2024", text)
        self.assertNotIn("counties", text)   # nothing on record is left out, not shown as zero
        self.assertIn("profile", dq.lists_for_jev())

    def test_badges_sorted_by_rarity(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="badges", subjects=[("Steven", "2")]))
        text = render(res, self.names)
        self.assertTrue(text.startswith("```\nSteven's badges\nWarden [Gold]"), text)
        self.assertLess(text.index("Warden [Gold]"), text.index("Night Owl [Silver]"))
        self.assertLess(text.index("Night Owl [Silver]"), text.index("Shut Victim [Bronze]"))

    def test_counties_named_and_tiered(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="counties", subjects=[("Kim", "3")]))
        text = render(res, self.names)
        self.assertIn("\nLondon x1 [legendary]", text)
        self.assertIn("\nBedfordshire x3 [common]", text)
        self.assertLess(text.index("London"), text.index("Bedfordshire"))

    def test_empty_list_says_so(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="bonds", subjects=[("Kim", "3")]))
        self.assertEqual(render(res, self.names), "```\nKim's bonds: no bonds.\n```")

    def test_pvp_record_per_game_and_opponent(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="pvp_record", subjects=[("Johnny", "1")]))
        text = render(res, self.names)
        self.assertIn("\nBattleship: 1W 0L 0D", text)
        self.assertIn("\nConnect 4: 1W 1L 0D", text)
        self.assertIn("\nSteven: 1W 1L 0D", text)
        self.assertIn("\nHadidas: 1W 0L 0D", text)

    def test_bank_fact_sheet(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="bank"))
        text = render(res, {})
        self.assertTrue(text.startswith("```\nhouse bank\n"), text)
        self.assertIn("house balance 123,456 UKP", text)
        self.assertIn("blackjack: house +600 UKP (took 1,000, paid 400)", text)
        self.assertIn("slots: house -600 UKP", text)

    def test_lottery_round_and_names_in_lines(self):
        res = compute(QuerySpec(metric="none", shape="list", list_kind="lottery"))
        text = render(res, self.names)
        self.assertIn("round 7: 120 tickets sold to 9 players at 10 UKP each (cap 500)", text)
        self.assertIn("pot about 1,080 UKP after 10% rake", text)
        self.assertIn("\nKim: won the last draw: 900 UKP on", text)

    def test_pick_lists_filter_bots_and_departed(self):
        holders = compute(QuerySpec(metric="none", shape="list", list_kind="badge_holders", pick="Warden"), exclude_ids={"2"})
        text = render(holders, self.names)
        self.assertIn("holders of a badge: Warden\n", text)
        self.assertIn("\nJohnny: since", text)
        self.assertNotIn("Steven", text)
        owners = compute(QuerySpec(metric="none", shape="list", list_kind="county_owners", pick="London"), member_ids={"2"})
        self.assertEqual(render(owners, self.names), "```\nowners of a county: London\nSteven: x2\n```")
        nothing = compute(QuerySpec(metric="none", shape="list", list_kind="badge_holders", pick="Nonexistent"))
        self.assertIn("nobody holds it", render(nothing, self.names))
        self.assertEqual(dq.pick_candidates("badge"), ["Night Owl", "Shut Victim", "Warden"])
        self.assertIn("Yorkshire", dq.pick_candidates("county"))

    def test_json_backed_metrics_survive_missing_files(self):
        with patch("lib.features.data_queries._json", return_value=None):
            for key in ("night_owl", "weekend_warrior", "skyrim_level", "skyrim_septims", "prediction_streak"):
                self.assertEqual(compute(QuerySpec(metric=key, shape="leaderboard")).rows, [], key)
            self.assertEqual(render(compute(QuerySpec(metric="none", shape="list", list_kind="graveyard")), {}), "```\nSkyrim graveyard: nobody has died lately.\n```")

    def test_json_backed_metrics_read_counts(self):
        files = {"NIGHT_OWL_COUNTS_FILE": {"1": 40, "2": 12}, "WEEKEND_WARRIOR_COUNTS_FILE": {"2026-w1": {"1": 5}, "2026-w2": {"1": 7, "3": 2}},
                 "SKYRIM_PROFILES_FILE": {"1": {"xp": 0, "septims": 300, "stats": {"dragons": 2}}}}
        with patch("lib.features.data_queries._json", side_effect=lambda k: files.get(k)):
            self.assertEqual(compute(QuerySpec(metric="night_owl", shape="leaderboard")).rows, [("1", 40), ("2", 12)])
            self.assertEqual(compute(QuerySpec(metric="weekend_warrior", shape="leaderboard")).rows, [("1", 12), ("3", 2)])
            self.assertEqual(compute(QuerySpec(metric="skyrim_septims", shape="leaderboard")).rows, [("1", 300)])
            self.assertEqual(compute(QuerySpec(metric="skyrim_dragons", shape="leaderboard")).rows, [("1", 2)])


class TestClosest(unittest.TestCase):
    def setUp(self):
        self._p = patch("lib.features.data_queries._fetch", _fake_fetch())
        self._p.start()
        self.addCleanup(self._p.stop)
        self.names = {"1": "Johnny", "2": "Steven", "3": "Kim", "4": "Hadidas"}

    def test_target_number_is_read_from_the_words(self):
        parse = dq.parse_target_number
        self.assertEqual(parse("who has closest to 100k xp"), 100_000)
        self.assertEqual(parse("who's nearest to 1.5m ukp"), 1_500_000)
        self.assertEqual(parse("closest to 100,000 messages"), 100_000)
        self.assertEqual(parse("who has about 50 shutcoins"), 50)
        self.assertEqual(parse("nearest to half a million"), 500_000)
        self.assertEqual(parse("who's around a million ukp"), 1_000_000)
        self.assertEqual(parse("top 5 closest to 100k xp"), 100_000)    # the cued number, not the 5
        self.assertEqual(parse("who is at 300 xp exactly, top 3"), 300)  # no cue: the larger
        self.assertIsNone(parse("who is closest to the top"))
        self.assertIsNone(parse(""))

    def test_closest_orders_by_distance_and_shows_the_gap(self):
        res = compute(QuerySpec(metric="xp", shape="closest", target=1000, limit=3))
        self.assertEqual(res.rows, [("1", 1240), ("2", 300), ("4", 300)])
        self.assertEqual(res.ranks, {"1": 1, "2": 2, "4": 3})
        text = render(res, self.names)
        self.assertTrue(text.startswith("Closest 3 to 1,000 XP\n```"), text)
        self.assertIn(" 1. 1,240 XP  +240 XP  Johnny", text)
        self.assertIn(" 2.   300 XP  -700 XP  Steven", text)
        exact = compute(QuerySpec(metric="xp", shape="closest", target=5000, limit=3))
        self.assertRegex(render(exact, self.names), r" 1\. 5,000 XP  spot on\s+Kim")

    def test_closest_without_a_target_is_empty(self):
        res = compute(QuerySpec(metric="xp", shape="closest", target=None))
        self.assertEqual(res.rows, [])
        self.assertIn("Nobody has any XP", render(res, self.names))

    def test_closest_heading_for_a_unit_that_is_not_in_the_label(self):
        res = compute(QuerySpec(metric="shutcoins", shape="closest", target=10, limit=3), exclude_ids={"999"})
        self.assertTrue(render(res, self.names).startswith("Closest 3 to 10 shutcoins held\n"), render(res, self.names))


class TestBetween(unittest.TestCase):
    def test_pair_figure_runs_both_ways(self):
        calls = []
        def fetch(sql, params=()):
            calls.append((sql, tuple(params)))
            if "WHERE user_id = ? AND counterparty_id = ?" in sql:
                return [(1200,)] if params[:2] == ("9", "3") else [(50,)]
            return []
        with patch("lib.features.data_queries._fetch", fetch):
            res = compute(QuerySpec(metric="paid_out", shape="between", window="month", subjects=[("Snake", "9"), ("Kim", "3")]))
            text = render(res, {"9": "Snake", "3": "Kim"})
        self.assertEqual(res.rows, [("9", 1200), ("3", 50)])
        self.assertIn("Snake paid to Kim: 1,200 UKP", text)
        self.assertIn("Kim paid to Snake: 50 UKP", text)
        self.assertIn("(last 30 days)", text)
        self.assertTrue(all("ts >= ?" in s and "amount < 0" in s and "reason LIKE 'Pay%'" in s for s, _ in calls))

    def test_payments_come_from_the_ledger_not_the_transfers_table(self):
        calls = []
        fetch = lambda sql, params=(): (calls.append(sql) or [("1", 5)])
        with patch("lib.features.data_queries._fetch", fetch):
            compute(QuerySpec(metric="paid_in", shape="leaderboard"))
            compute(QuerySpec(metric="payments_received", shape="leaderboard"))
            compute(QuerySpec(metric="payments_made", shape="leaderboard"))
        self.assertTrue(all("FROM user_transactions" in s and "pay_transfers" not in s for s in calls))
        self.assertIn("amount > 0 AND (reason LIKE 'Pay%' OR reason LIKE '/pay%')", calls[0])
        self.assertIn("COUNT(*)", calls[1])
        self.assertIn("amount < 0", calls[2])

    def test_head_to_head_wins_filter_by_game(self):
        calls = []
        def fetch(sql, params=()):
            calls.append((sql, tuple(params)))
            return [(3,)]
        with patch("lib.features.data_queries._fetch", fetch):
            res = compute(QuerySpec(metric="pvp_wins", shape="between", game="connect4", subjects=[("A", "1"), ("B", "2")]))
            text = render(res, {"1": "A", "2": "B"})
        self.assertIn("A beat B: 3 wins", text)
        self.assertIn("outcome != 'draw'", calls[0][0])
        self.assertIn("game = ?", calls[0][0])
        self.assertEqual(calls[0][1][-1], "connect4")

    def test_between_falls_back_to_compare_without_a_pair_figure(self):
        with patch("lib.features.data_queries._fetch", _fake_fetch()):
            res = compute(QuerySpec(metric="xp", shape="between", subjects=[("Johnny", "1"), ("Kim", "3")]))
        self.assertEqual(res.spec.shape, "compare")
        self.assertIn("Kim ahead by 3,760 XP.", render(res, {"1": "Johnny", "3": "Kim"}))


class TestDayGrain(unittest.TestCase):
    def test_worst_day_carries_the_day_and_reports_a_loss_as_a_size(self):
        calls = []
        def fetch(sql, params=()):
            calls.append((sql, tuple(params)))
            if "GROUP BY user_id, day" in sql and "MIN(d)" in sql:
                return [("1", -12000, "2026-09-03"), ("2", 300, "2026-09-05"), ("3", -50, "2026-08-30")]
            return []
        with patch("lib.features.data_queries._fetch", fetch):
            res = compute(QuerySpec(metric="casino_worst_day", shape="leaderboard", game="blackjack", window="month"))
            text = render(res, {"1": "Johnny", "3": "Kim"})
        self.assertEqual(res.rows, [("1", 12000), ("3", 50)])          # 2 never had a losing day
        self.assertEqual(res.details, {"1": "on 03 Sep 2026", "3": "on 30 Aug 2026"})
        self.assertIn("Top 2 by biggest single-day casino loss (blackjack, last 30 days)", text)
        self.assertIn(" 1. 12,000 UKP  Johnny  (on 03 Sep 2026)", text)
        sql, params = calls[0]
        self.assertIn("date(timestamp, 'unixepoch')", sql)
        self.assertIn("game = ?", sql)
        self.assertIn("timestamp >= ?", sql)
        self.assertEqual(params[0], "blackjack")
        person = compute(QuerySpec(metric="casino_worst_day", shape="person", subjects=[("Johnny", "1")]))
        with patch("lib.features.data_queries._fetch", fetch):
            person = compute(QuerySpec(metric="casino_worst_day", shape="person", subjects=[("Johnny", "1")]))
        self.assertIn("Johnny: 12,000 UKP (biggest single-day casino loss) on 03 Sep 2026", render(person, {"1": "Johnny"}))

    def test_best_day_keeps_only_winning_days(self):
        fetch = lambda sql, params=(): [("1", 900, "2026-09-01"), ("2", -40, "2026-09-02")] if "MAX(d)" in sql else []
        with patch("lib.features.data_queries._fetch", fetch):
            self.assertEqual(compute(QuerySpec(metric="casino_best_day", shape="leaderboard")).rows, [("1", 900)])
            self.assertEqual(compute(QuerySpec(metric="messages_best_day", shape="leaderboard")).rows, [("1", 900)])

    def test_server_day_lists(self):
        def fetch(sql, params=()):
            if "FROM message_archive" in sql and "GROUP BY day" in sql:
                return [("2026-09-16", 4100, 97)]
            if "FROM casino_results GROUP BY day" in sql:
                return [("2026-09-10", 25000, 400, 30), ("2026-09-11", -8000, 120, 12)]
            return []
        with patch("lib.features.data_queries._fetch", fetch):
            busiest = render(compute(QuerySpec(metric="none", shape="list", list_kind="busiest_days")), {})
            casino = render(compute(QuerySpec(metric="none", shape="list", list_kind="casino_days")), {})
        self.assertIn("16 Sep 2026: 4,100 messages from 97 members", busiest)
        self.assertIn("10 Sep 2026: house took 25,000 UKP over 400 rounds by 30 players", casino)
        self.assertIn("11 Sep 2026: house paid out 8,000 UKP", casino)


class TestSplit(unittest.TestCase):
    def test_splits_only_at_a_joiner_followed_by_a_question(self):
        split = dq.split_records_questions
        self.assertEqual(split("who has paid out the most, and who has received the most ukpence"),
                         ["who has paid out the most", "who has received the most ukpence"])
        self.assertEqual(split("how much xp has steven got and how many badges"), ["how much xp has steven got", "how many badges"])
        self.assertEqual(split("top 5 by messages; top 5 by xp"), ["top 5 by messages", "top 5 by xp"])
        self.assertEqual(split("who's the richest? and who's the poorest"), ["who's the richest", "who's the poorest"])
        self.assertEqual(split("who has more xp, me and steven"), ["who has more xp, me and steven"])
        self.assertEqual(split("how much has snake paid to kim and gunner"), ["how much has snake paid to kim and gunner"])
        self.assertEqual(split(""), [])


class TestDigest(unittest.TestCase):
    def test_digest_takes_the_top_few_per_metric_with_names(self):
        with patch("lib.features.data_queries._fetch", _fake_fetch()):
            sections, ids = dq.stats_digest(exclude_ids={"999"}, top=2)
        headings = [h for h, _ in sections]
        self.assertIn("XP", headings)
        self.assertIn("shutcoins used", headings)
        self.assertIn("casino profit and loss", headings)
        self.assertNotIn("counties caught", headings)      # nothing on record: left out
        text = dq.render_digest(sections, {"1": "Johnny", "3": "Kim", "5": "Chin"})
        self.assertIn("XP: Kim 5,000 XP; Johnny 1,240 XP", text)
        self.assertIn("shutcoins used: Chin 7 shutcoins; Johnny 6 shutcoins", text)
        self.assertIn("3", ids)


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
        self.assertIn(" 1. 14 shutcoins  Johnny", text)
        self.assertIn(" 3.  2 shutcoins  Hadidas", text)
        self.assertNotIn("Kim", text)

    def test_leaderboard_scope_and_signed_values(self):
        res = compute(QuerySpec(metric="casino_net", shape="leaderboard", game="blackjack", window="week", lowest=True, limit=5))
        text = render(res, self.names)
        self.assertIn("Bottom 2 by casino profit and loss (blackjack, last 7 days)", text)
        self.assertIn(" 1. -500 UKP  Johnny", text)
        self.assertIn(" 2. +120 UKP  Steven", text)

    def test_empty_leaderboard(self):
        res = compute(QuerySpec(metric="counties", shape="leaderboard"))
        self.assertEqual(render(res, {}), "Nobody has any counties caught on record.")

    def test_person_line(self):
        res = compute(QuerySpec(metric="xp", shape="person", subjects=[("Steven", "2")]))
        self.assertEqual(render(res, self.names), "```\nSteven: 300 XP (rank 3 of 4)\n```")

    def test_person_with_note_and_unknown_name(self):
        res = compute(QuerySpec(metric="shutcoins_used", shape="person", subjects=[("?", "5")]))
        text = render(res, {})
        self.assertIn("\nuser 5: 7 shutcoins used", text)
        self.assertIn("minus held", text)

    def test_compare_verdict(self):
        res = compute(QuerySpec(metric="xp", shape="compare", subjects=[("Johnny", "1"), ("Kim", "3")]))
        text = render(res, self.names)
        self.assertIn("\nJohnny: 1,240 XP", text)
        self.assertIn("\nKim: 5,000 XP", text)
        self.assertIn("Kim ahead by 3,760 XP.", text)
        net = compute(QuerySpec(metric="casino_net", shape="compare", game="blackjack", subjects=[("Johnny", "1"), ("Steven", "2")]))
        text = render(net, self.names)
        self.assertIn("\nJohnny: -500 UKP (casino profit and loss)", text)
        self.assertIn("Steven ahead by 620 UKP. (blackjack)", text)
        tie = compute(QuerySpec(metric="xp", shape="compare", subjects=[("Steven", "2"), ("Hadidas", "4")]))
        self.assertIn("Dead level.", render(tie, self.names))

    def test_unitless_metric_reads_naturally(self):
        with patch("lib.features.data_queries._fetch", lambda sql, params=(): [("1", 7)] if "shut_counts" in sql else []):
            res = compute(QuerySpec(metric="times_shut", shape="person", subjects=[("Johnny", "1")]))
            self.assertEqual(render(res, self.names), "```\nJohnny: 7 times shut (rank 1 of 1)\n```")
            board = compute(QuerySpec(metric="times_shut", shape="leaderboard"))
            self.assertIn(" 1. 7  Johnny", render(board, self.names))

    def test_total_line(self):
        res = compute(QuerySpec(metric="xp", shape="total"))
        self.assertEqual(render(res, {}), "```\nTotal XP: 6,840 XP across 4 members\n```")


if __name__ == "__main__":
    unittest.main()
