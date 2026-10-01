import unittest
from unittest.mock import patch

from lib.features import leaderboard_cards as lc


def _people(n, start=1, **extra):
    return [dict({"rank": start + i, "rank_label": str(start + i), "user_id": str(100 + i),
                  "name": f"user{start + i}", "avatar": None, "colour": "#3B5BDB",
                  "title": None, "background": None}, **extra) for i in range(n)]


class PeerageTests(unittest.TestCase):
    def test_ladder_names_come_from_role_constants(self):
        names = [name for _, name in lc.PEERAGES]
        self.assertEqual(names[0], "Serf")
        self.assertIn("Lord High Chancellor", names)
        self.assertEqual(names[-1], "Royal Duke")

    def test_progress_and_gap(self):
        p = lc.peerage(499_847)
        self.assertEqual((p["name"], p["next"], p["gap"]), ("Viceroy", "Lord High Chancellor", 153))
        self.assertGreater(p["progress"], 0.99)

    def test_below_first_rung_and_at_the_top(self):
        self.assertEqual(lc.peerage(400), {"name": None, "next": "Serf", "gap": 600, "progress": 0.4})
        top = lc.peerage(5_000_000)
        self.assertEqual((top["name"], top["next"], top["progress"]), ("Royal Duke", None, 1.0))

    def test_compact(self):
        self.assertEqual(lc.compact(17_003_145), "17.0M")
        self.assertEqual(lc.compact(605_836), "605.8k")
        self.assertEqual(lc.compact(9_682), "9,682")


class WeekHistoryTests(unittest.TestCase):
    NOW = 1_800_000_000
    START = NOW - 7 * 86400

    def test_change_against_the_balance_a_week_ago(self):
        rows = [(self.START + 2 * 86400 + 10, 12_000), (self.START + 5 * 86400, 18_000)]
        with patch.object(lc.DatabaseManager, "fetch_one", return_value=(11_579,)), \
             patch.object(lc.DatabaseManager, "fetch_all", return_value=rows):
            change, samples = lc.week_history("1", 19_106, now=self.NOW)
        self.assertEqual(change, 7_527)
        self.assertEqual(len(samples), 8)
        self.assertEqual(samples[:3], [11_579, 11_579, 11_579])
        self.assertEqual(samples[3], 12_000)
        self.assertEqual(samples[5], 18_000)
        self.assertEqual(samples[-1], 19_106)

    def test_no_week_old_record_means_no_change(self):
        with patch.object(lc.DatabaseManager, "fetch_one", return_value=None), \
             patch.object(lc.DatabaseManager, "fetch_all", return_value=[]):
            change, samples = lc.week_history("1", 500, now=self.NOW)
        self.assertIsNone(change)
        self.assertEqual(samples, [500] * 8)


class XpCardTests(unittest.TestCase):
    def _html(self, page, people):
        return lc.build_xp_html(people, page=page, pages=989, members=19_771, total_xp=17_003_145,
                                top_peerage="Grand Duke", top_peerage_holders=1)

    def test_first_page_has_podium_and_seventeen_rows(self):
        people = _people(20)
        for i, p in enumerate(people):
            p["xp"] = 1_302_845 - i * 40_000
        people[3]["xp"] = 499_847
        page = self._html(1, people)
        self.assertIn('class="place first"', page)
        self.assertEqual(page.count('class="row'), 17)
        self.assertIn("PAGE 1 OF 989", page)
        self.assertIn("17.0M", page)
        self.assertIn("held by 1 member", page)
        self.assertIn("153 XP", page)
        self.assertIn("#4 to Lord High Chancellor", page)

    def test_later_pages_are_all_rows(self):
        people = _people(20, start=21)
        for p in people:
            p["xp"] = 150_000
        page = self._html(2, people)
        self.assertNotIn('class="place', page)
        self.assertEqual(page.count('class="row'), 20)

    def test_names_titles_and_backgrounds(self):
        people = _people(4)
        for p in people:
            p["xp"] = 300_000
        people[3]["name"] = "<b>{{body}}</b>"
        people[3]["title"] = "Ship's <i>Rat</i>"
        people[3]["background"] = "data:image/png;base64,AAAA"
        page = self._html(1, people)
        self.assertIn("&lt;b&gt;{{body}}&lt;/b&gt;", page)
        self.assertIn("Ship&#x27;s &lt;i&gt;Rat&lt;/i&gt;", page)
        self.assertIn('class="row custom"', page)
        self.assertIn("url('data:image/png;base64,AAAA')", page)


class RichCardTests(unittest.TestCase):
    def test_changes_and_stats(self):
        people = _people(5)
        for i, p in enumerate(people):
            p.update(balance=30_000 - i * 1000, change=[1_963, -676, None, 7_527, 0][i], series=[1, 2, 3, 4, 5, 6, 7, 8])
        page = lc.build_rich_html(people, page=1, pages=406, holders=8_103, circulation=605_836,
                                  treasury=194_164, top_share=52.7)
        self.assertIn("+1,963 this week", page)
        self.assertIn("−676 this week", page)
        self.assertIn(">new<", page)
        self.assertIn("+7,527", page)
        self.assertIn("no change", page)
        self.assertIn("194.2k", page)
        self.assertIn("53%", page)
        self.assertIn("<polyline", page)


class MedalCardTests(unittest.TestCase):
    def test_shared_ranks_secret_rings_and_full_set(self):
        people = _people(5)
        counts = [(20, 47, 38), (12, 35, 37), (8, 23, 28), (7, 29, 30), (7, 29, 30)]
        for p, (g, s, b) in zip(people, counts):
            p.update(gold=g, silver=s, bronze=b, total=g + s + b, secret=False)
        people[3]["rank_label"] = people[4]["rank_label"] = "=4"
        people[4]["secret"] = True
        page = lc.build_medal_html(people, page=1, pages=480, ranked=9_582,
                                   awarded={"Gold": 222, "Silver": 4_525, "Bronze": 9_954},
                                   defined={"Gold": 20, "Silver": 47, "Bronze": 38, "Secret": 9},
                                   secret_holders=45)
        self.assertIn("Full set · all 105 badges", page)
        self.assertIn("84 of 105 badges", page)
        self.assertEqual(page.count(">=4<"), 2)
        self.assertEqual(page.count("ring secret"), 1)
        self.assertIn("66 of 105", page)
        self.assertIn("4,525", page)


if __name__ == "__main__":
    unittest.main()
