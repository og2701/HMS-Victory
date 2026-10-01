import unittest

from lib.features.rank_card import build_rank_card_html, fit_badge_cell, name_size, tier_progress

LADDER = [(1000, 1), (2500, 2), (500_000, 3), (750_000, 4)]


def _card(**overrides):
    card = {
        "username": "TeaAndToast", "title": "First Lieutenant", "rank_label": "#4", "xp": 426_184,
        "current_role": "Viceroy", "next_role": "Lord High Chancellor", "to_next": 73_816, "progress": 0.508,
        "ukpence": 23_798, "shutcoins": 12, "ukpence_icon": "data:ukp", "shutcoin_icon": "data:shut",
        "avatar_url": "https://cdn.example/a.png", "background": "data:image/png;base64,BG",
        "primary": "#CF142B", "secondary": "#00247D", "tertiary": "#FFFFFF",
        "badges": [{"src": "b1", "rarity": "Bronze", "name": "x"}, {"src": "s1", "rarity": "Secret", "name": "y"},
                   {"src": "g1", "rarity": "Gold", "name": "z"}],
    }
    card.update(overrides)
    return card


class TierProgressTests(unittest.TestCase):
    def test_progress_starts_from_the_current_threshold(self):
        t = tier_progress(2_500, LADDER)
        self.assertEqual((t["current_id"], t["next_id"], t["progress"]), (2, 3, 0.0))
        t = tier_progress(251_250, LADDER)
        self.assertAlmostEqual(t["progress"], 0.5)
        self.assertEqual(t["to_next"], 248_750)

    def test_below_first_rung_and_maxed(self):
        t = tier_progress(250, LADDER)
        self.assertEqual((t["current_id"], t["next_id"], t["to_next"]), (None, 1, 750))
        self.assertAlmostEqual(t["progress"], 0.25)
        top = tier_progress(800_000, LADDER)
        self.assertEqual((top["current_id"], top["next_id"], top["progress"]), (4, None, 1.0))


class LayoutHelperTests(unittest.TestCase):
    def test_badges_shrink_to_fit(self):
        self.assertEqual(fit_badge_cell(34), 34)
        big = fit_badge_cell(114)
        self.assertLess(big, 34)
        cols = (928 + 6) // (big + 6)
        rows = -(-114 // cols)
        self.assertLessEqual(rows * (big + 6) - 6, 126)

    def test_name_size_steps_down(self):
        self.assertEqual(name_size("Gazza"), 42)
        self.assertEqual(name_size("x" * 30), 26)


class RankCardHtmlTests(unittest.TestCase):
    def test_card_content(self):
        page = build_rank_card_html(_card())
        self.assertIn("73,816 XP to Lord High Chancellor", page)
        self.assertIn("width:50.8%", page)
        self.assertIn("426,184 XP · 3 badges", page)
        self.assertIn("--primary: #CF142B", page)
        self.assertIn("url('data:image/png;base64,BG')", page)
        # Secret badges lead, then gold, then the rest.
        self.assertLess(page.index('src="s1"'), page.index('src="g1"'))
        self.assertLess(page.index('src="g1"'), page.index('src="b1"'))
        self.assertIn('class="badge secret"', page)

    def test_maxed_member_and_hidden_shutcoins(self):
        page = build_rank_card_html(_card(next_role=None, to_next=None, progress=1.0, shutcoins=None))
        self.assertIn("Highest peerage reached", page)
        self.assertIn("width:100.0%", page)
        self.assertNotIn("data:shut", page)

    def test_names_and_titles_are_escaped(self):
        page = build_rank_card_html(_card(username="<img src=x>{{body}}", title="<b>Admiral</b>"))
        self.assertIn("&lt;img src=x&gt;{{body}}", page)
        self.assertIn("&lt;b&gt;Admiral&lt;/b&gt;", page)
        self.assertNotIn("<img src=x>", page)

    def test_no_peerage_yet(self):
        page = build_rank_card_html(_card(current_role=None, badges=[]))
        self.assertIn("No peerage yet", page)
        self.assertIn("0 badges", page)


if __name__ == "__main__":
    unittest.main()
