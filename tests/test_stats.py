"""Offline statistics tests. Run: python3 -m unittest discover -s tests."""

from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import stats


class VoteTests(unittest.TestCase):
    def test_vader_thresholds_and_empty(self) -> None:
        for compounds, expected in [([], "neu"), ([0.05], "pos"),
                                    ([-0.05], "neg"), ([0.049], "neu"),
                                    ([-0.049], "neu"), ([0.9, -0.9], "neu")]:
            with self.subTest(compounds=compounds):
                self.assertEqual(stats.vader_vote(compounds), expected)
        self.assertEqual(stats.vader_vote([0.1], pos=0.2), "neu")
        self.assertEqual(stats.vader_vote([-0.1], neg=-0.2), "neu")

    def test_rubric(self) -> None:
        self.assertEqual([stats.rubric_vote(i) for i in range(1, 6)],
                         ["neg", "neg", "neu", "pos", "pos"])
        for score in (-1, 0, 6):
            with self.assertRaises(ValueError):
                stats.rubric_vote(score)


class KappaTests(unittest.TestCase):
    def test_identical(self) -> None:
        self.assertEqual(stats.cohen_kappa(["pos", "neu", "neg"], ["pos", "neu", "neg"]), 1.0)
        self.assertEqual(stats.cohen_kappa(["yes"] * 4, ["yes"] * 4), 1.0)

    def test_independent(self) -> None:
        # Each rater has half yes, half no: po = 2/4 = pe = .5.
        self.assertEqual(stats.cohen_kappa(["yes", "yes", "no", "no"],
                                           ["yes", "no", "yes", "no"]), 0.0)

    def test_textbook_two_by_two(self) -> None:
        # Table (a rows, b columns; yes/no): [[15, 5], [5, 0]].
        # n=25; a=yes 20/no 5; b=yes 20/no 5; disagreements=10.
        # po=15/25=.60; pe=(20/25)^2+(5/25)^2=.68.
        # kappa=(.60-.68)/(1-.68)=-.08/.32=-.25.
        a = ["yes"] * 20 + ["no"] * 5
        b = ["yes"] * 15 + ["no"] * 5 + ["yes"] * 5
        self.assertAlmostEqual(stats.cohen_kappa(a, b), -0.250, places=3)

    def test_invalid_inputs(self) -> None:
        for a, b in [([], []), (["yes"], [] )]:
            with self.assertRaises(ValueError):
                stats.cohen_kappa(a, b)


class IntervalTests(unittest.TestCase):
    def test_wilson_zero_and_all(self) -> None:
        low, high = stats.wilson(0, 10)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.278, places=3)
        self.assertEqual(stats.wilson(10, 10)[1], 1.0)
        self.assertEqual(stats.wilson(0, 0), (0.0, 1.0))

    def test_wilson_symmetric(self) -> None:
        low, high = stats.wilson(5, 10)
        self.assertAlmostEqual(low + high, 1.0)
        self.assertAlmostEqual(low, 0.2366, places=4)
        self.assertAlmostEqual(high, 0.7634, places=4)

    def test_bootstrap_deterministic_and_bounded(self) -> None:
        votes = ["pos", "pos", "neu", "neg"]
        state = random.getstate()
        interval = stats.bootstrap_net(votes, seed=17)
        self.assertEqual(interval, stats.bootstrap_net(votes, seed=17))
        self.assertEqual(random.getstate(), state)
        self.assertTrue(-1 <= interval[0] <= interval[1] <= 1)

    def test_bootstrap_constant_and_empty(self) -> None:
        for vote, value in [("pos", 1.0), ("neu", 0.0), ("neg", -1.0)]:
            self.assertEqual(stats.bootstrap_net([vote] * 3), (value, value))
        self.assertEqual(stats.bootstrap_net([]), (-1.0, 1.0))

    def test_invalid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            stats.bootstrap_net(["pos"], iters=0)
        with self.assertRaises(ValueError):
            stats.bootstrap_net(["unknown"])
        for k, n in [(-1, 10), (11, 10), (0, -1)]:
            with self.assertRaises(ValueError):
                stats.wilson(k, n)


class AggregateTests(unittest.TestCase):
    def test_counts_and_intervals(self) -> None:
        votes = ["pos", "pos", "neu", "neg"]
        self.assertEqual(stats.aggregate(votes), {
            "n": 4, "pos": 2, "neu": 1, "neg": 1, "net": 0.25,
            "pos_share": 0.5, "neg_share": 0.25,
            "wilson_pos": stats.wilson(2, 4), "wilson_neg": stats.wilson(1, 4),
            "net_ci": stats.bootstrap_net(votes),
        })

    def test_empty(self) -> None:
        result = stats.aggregate([])
        for key in ("n", "pos", "neu", "neg", "net", "pos_share", "neg_share"):
            self.assertEqual(result[key], 0)
        self.assertEqual(result["wilson_pos"], (0.0, 1.0))
        self.assertEqual(result["wilson_neg"], (0.0, 1.0))
        self.assertEqual(result["net_ci"], (-1.0, 1.0))

    def test_reporting_thresholds(self) -> None:
        for n, kappa in [(9, 1.0), (10, 0.39)]:
            allowed, reason = stats.report_allowed(n, kappa)
            self.assertFalse(allowed)
            self.assertTrue(reason)
        self.assertEqual(stats.report_allowed(10, 0.4), (True, ""))
        self.assertEqual(stats.report_allowed(3, 0.2, min_n=3, min_kappa=0.2), (True, ""))


if __name__ == "__main__":
    unittest.main()
