"""Offline scoring and CLI contract tests for recommend.py."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import recommend  # noqa: E402

NOW = 1_800_000_000
DAY = 86400


def thread(**changes: object) -> dict:
    row = {"id": "abc", "subreddit": "node", "title": "SMTP help", "author": "distinctive_author_123",
           "last_activity_utc": NOW, "num_comments_reported": 12, "num_comments_archived": 1,
           "keyword_hits_post": 0, "keyword_hits_comments": 0, "comments": [],
           "permalink": "https://www.reddit.com/r/node/comments/abc/"}
    row.update(changes)
    return row


class ComponentTests(unittest.TestCase):
    def test_active_boundaries(self) -> None:
        for age, expected in ((-1, 1), (0, 1), (15 * DAY, .5), (30 * DAY, 0), (31 * DAY, 0)):
            with self.subTest(age=age):
                self.assertEqual(recommend.components(thread(last_activity_utc=NOW-age), now=NOW)["active"], expected)

    def test_op_boundaries(self) -> None:
        for age, expected in ((0, 1), (14 * DAY, 1), (14 * DAY + 1, .5)):
            comments = [{"author": "distinctive_author_123", "created_utc": NOW-age}]
            self.assertEqual(recommend.components(thread(comments=comments), now=NOW)["op_engaged"], expected)
        self.assertEqual(recommend.components(thread(), now=NOW)["op_engaged"], 0)
        for name in (None, "[deleted]", "[removed]", "someone_else"):
            row = thread(comments=[{"author": name, "created_utc": NOW}])
            if name != "someone_else":
                row["author"] = name
            self.assertEqual(recommend.components(row, now=NOW)["op_engaged"], 0)

    def test_unanswered_boundaries(self) -> None:
        for score, expected in ((-3, 1), (0, 1), (5, .5), (10, 0), (20, 0), (None, 1)):
            row = thread(comments=[{"score": score}])
            self.assertEqual(recommend.components(row, now=NOW)["unanswered"], expected)
        self.assertEqual(recommend.components(thread(), {"answered": True}, now=NOW)["unanswered"], 0)
        self.assertEqual(recommend.components(thread(), now=NOW)["unanswered"], 1)
        self.assertEqual(recommend.components(thread(comments=[{"score": 1}, {"score": 8}]), now=NOW)["unanswered"], 1-.8)

    def test_density_boundaries(self) -> None:
        for post, comments, expected in ((0, 0, 0), (1, 2, .5), (2, 4, 1), (4, 9, 1), (-1, 0, 0)):
            self.assertEqual(recommend.components(thread(keyword_hits_post=post, keyword_hits_comments=comments), now=NOW)["density"], expected)

    def test_vendor_boundaries(self) -> None:
        for extract, expected in (({}, 1), ({"vendor_accepted": False, "vendor_present": ["AcmeMail"]}, 1), ({"vendor_accepted": True}, 0)):
            self.assertEqual(recommend.components(thread(), extract, now=NOW)["no_vendor"], expected)

    def test_weighted_score(self) -> None:
        row = thread(last_activity_utc=NOW-15*DAY, keyword_hits_post=3,
                     comments=[{"author": "distinctive_author_123", "created_utc": NOW-20*DAY, "score": 2}])
        result = recommend.recommend([row], now=NOW)[0]
        self.assertEqual(result["J"], .61)  # .15 + .125 + .16 + .075 + .10
        for options in ({"active_within": 0}, {"op_window": -1}, {"answer_threshold": 0}, {"density_cap": float("nan")}):
            with self.assertRaises(ValueError):
                recommend.components(row, now=NOW, **options)


class RecommendationTests(unittest.TestCase):
    def test_closed_last_irrelevant_excluded_and_top(self) -> None:
        rows = [thread(id="closed", subreddit="PHPhelp"), thread(id="low", last_activity_utc=NOW-30*DAY),
                thread(id="irrelevant"), thread(id="high"),
                thread(id="removed", title="[ Removed by moderator ]"), thread(id="deleted", title="[deleted]")]
        catalog = {"subreddits": {"phphelp": {"comment_policy": "closed"}}}
        extracts = [{"id": "irrelevant", "relevant": False}, {"id": "removed", "relevant": True}]
        result = recommend.recommend(rows, extracts, catalog, now=NOW)
        self.assertEqual([r["id"] for r in result], ["high", "low", "closed"])
        for title, expected in (("[ Removed by moderator ]", True), ("[REMOVED]", True), (" [deleted] ", True),
                                ("Removed my old server, now what", False), ("", False), (None, False)):
            self.assertEqual(recommend.is_removed(thread(title=title)), expected, title)
        self.assertEqual([r["rank"] for r in result], [1, 2, 3])
        self.assertIn("DO NOT COMMENT (sub rules)", result[-1]["why"])
        self.assertEqual(len(recommend.recommend(rows, extracts, catalog, now=NOW, top=2)), 2)
        self.assertEqual(recommend.recommend(rows, now=NOW, top=0), [])

    def test_sort_before_rounding_and_stable_ties(self) -> None:
        result = recommend.recommend([thread(id="older", last_activity_utc=NOW-1), thread(id="newer"), thread(id="tie")], now=NOW)
        self.assertEqual([r["id"] for r in result], ["newer", "tie", "older"])
        self.assertEqual(len({r["J"] for r in result}), 1)

    def test_cli_missing_files_schema_privacy_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            fixture = thread(title="distinctive_author_123 and u/other_person | SMTP\n" + "x" * 100,
                             comments=[{"author": "distinctive_comment_author", "score": 5, "body": "secret body"}])
            (run / "threads.jsonl").write_text(json.dumps(fixture) + "\n" + json.dumps(thread(id="second")) + "\n")
            self.assertEqual(recommend.main(["--run", directory, "--catalog", str(run / "absent.json")], now=NOW), 0)
            rows = [json.loads(line) for line in (run / "recommend.jsonl").read_text().splitlines()]
            self.assertEqual(set(rows[0]), {"rank", "id", "J", "components", "subreddit", "title", "last_activity_utc", "num_comments", "comment_policy", "why", "permalink"})
            self.assertEqual(set(rows[0]["components"]), {"active", "op_engaged", "unanswered", "density", "no_vendor"})
            self.assertEqual(rows[1]["components"]["unanswered"], .5)
            self.assertEqual(rows[0]["comment_policy"], "unknown")
            self.assertEqual(rows[0]["num_comments"], 1)  # archived count, not the archive-time reported 12
            md = (run / "recommend.md").read_text()
            for path in (run / "recommend.md", run / "recommend.jsonl"):
                output = path.read_text()
                for secret in ("author", "other_person", "secret body"):
                    self.assertNotIn(secret, output)
            table = md.splitlines()[4:]
            self.assertEqual(len(table), 2)
            for row, line in zip(rows, table):
                self.assertIn(row["permalink"], line)
                self.assertIn(f'| {row["rank"]} | {row["J"]:.3f} |', line)
            self.assertIn("&#124;", md)
            self.assertIn("…", md)
            self.assertNotIn("x" * 81, md)

    def test_cli_extract_catalog_and_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / "threads.jsonl").write_text(json.dumps(thread(last_activity_utc=NOW-DAY, keyword_hits_post=2,
                comments=[{"author": "distinctive_author_123", "created_utc": NOW-2*DAY, "score": 2}])) + "\n")
            (run / "extract.jsonl").write_text(json.dumps({"id": "abc", "answered": True, "vendor_accepted": True}) + "\n")
            (run / "catalog.json").write_text(json.dumps({"subreddits": {"node": {"comment_policy": "closed"}}}))
            recommend.main(["--run", directory, "--catalog", str(run / "catalog.json"), "--active-within", "2d",
                            "--op-window", "1d", "--answer-threshold", "4", "--density-cap", "4", "--top", "1"], now=NOW)
            row = json.loads((run / "recommend.jsonl").read_text())
            self.assertEqual(row["components"], dict(active=.5, op_engaged=.5, unanswered=0, density=.5, no_vendor=0))
            self.assertIn("DO NOT COMMENT (sub rules)", (run / "recommend.md").read_text())

    def test_help_and_invalid_cli(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(Path(recommend.__file__).resolve().parents[1])}
        result = subprocess.run([sys.executable, "-B", "-m", "painpoint_radar.recommend", "--help"], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertIn("--op-window", result.stdout)
        for option, value in (("--active-within", "0d"), ("--op-window", "bad"), ("--answer-threshold", "0"), ("--density-cap", "nan"), ("--top", "-1")):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                recommend.main(["--run", "/missing", option, value])
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
