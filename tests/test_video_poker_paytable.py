import os
import unittest

os.environ.setdefault("OPENAI_TOKEN", "mock-token")

import commands.economy.video_poker as vp


class PaytableTests(unittest.TestCase):
    def test_a_better_hand_never_pays_less(self):
        """Royal flush > straight flush > quads > ... > two pair > jacks or better."""
        ladder = [vp.PAYTABLE[cat] for cat in range(9, 1, -1)] + [vp.JACKS_OR_BETTER]
        self.assertEqual(ladder, sorted(ladder, reverse=True))
        self.assertGreater(vp.PAYTABLE[9], vp.PAYTABLE[8])

    def test_a_royal_pays_the_royal_rate(self):
        g = vp.VideoPokerGame("v", 1, "x", 1, 100, [], ["TS", "JS", "QS", "KS", "AS"], drawn=True, state="over")
        vp._decide(g)
        self.assertEqual(g.outcome, "Royal Flush")
        self.assertEqual(g.payout, 100 * vp.PAYTABLE[9])


if __name__ == "__main__":
    unittest.main()
