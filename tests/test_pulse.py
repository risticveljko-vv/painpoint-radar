"""Offline tests for the selection logic. Run: python3 -m unittest discover -s tests -v"""
import sys, time, unittest, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import pulse  # noqa: E402

NOW = int(time.time())
DAY = 86400


def post(**kw):
    base = {"id": "abc123", "subreddit": "learnpython", "title": "Receiving email notifications in python",
            "selftext": "I poll imaplib every 10 seconds and I worry it might upset the email server. Any idea?",
            "author": "op", "created_utc": NOW - 10 * DAY, "score": 11, "num_comments": 3, "link_flair_text": None}
    base.update(kw)
    return base


def comment(i, age_days, body="use IMAP IDLE", score=1):
    return {"id": f"c{i}", "link_id": "t3_abc123", "parent_id": "t3_abc123", "author": f"u{i}",
            "created_utc": NOW - age_days * DAY, "score": score, "body": body}


def args(**kw):
    a = argparse.Namespace(exclude_flair={"solved", "resolved"}, min_comments=1, min_score=0)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


class WindowParsing(unittest.TestCase):
    def test_units(self):
        self.assertEqual(pulse.parse_window("90d"), 90 * DAY)
        self.assertEqual(pulse.parse_window("2w"), 14 * DAY)
        self.assertEqual(pulse.parse_window("3m"), 90 * DAY)
        self.assertEqual(pulse.parse_window("12h"), 12 * 3600)

    def test_bad(self):
        with self.assertRaises(argparse.ArgumentTypeError):
            pulse.parse_window("soon")


class Keywords(unittest.TestCase):
    def test_word_bounded_and_phrases(self):
        kw = pulse.build_keyword_re(["email", "imap idle", "ses"])
        self.assertTrue(kw.search("my Email bounced"))
        self.assertTrue(kw.search("try IMAP   IDLE instead"))
        self.assertFalse(kw.search("uses sesame"))  # 'ses' must not match inside a word
        self.assertFalse(kw.search("emailing"))    # word boundary

    def test_empty_means_none(self):
        self.assertIsNone(pulse.build_keyword_re(["", " "]))


class Records(unittest.TestCase):
    def test_last_activity_and_window_count(self):
        rec = pulse.thread_record(post(), [comment(1, 9), comment(2, 40), comment(3, 2)], NOW, NOW - 30 * DAY, None)
        self.assertEqual(rec["last_activity_utc"], NOW - 2 * DAY)
        self.assertEqual(rec["num_comments_in_window"], 2)
        self.assertEqual(rec["num_comments_archived"], 3)

    def test_removed_comments_ignored(self):
        rec = pulse.thread_record(post(created_utc=NOW - 60 * DAY), [comment(1, 1, body="[removed]"), comment(2, 20)], NOW, NOW - 30 * DAY, None)
        self.assertEqual(rec["num_comments_archived"], 1)
        self.assertEqual(rec["last_activity_utc"], NOW - 20 * DAY)

    def test_pain_terms_counted_once_each(self):
        rec = pulse.thread_record(post(selftext="can't can't CAN'T error error help"), [], NOW, 0, None)
        self.assertEqual(rec["pain_terms"], 3)

    def test_keyword_hits(self):
        kw = pulse.build_keyword_re(["email"])
        rec = pulse.thread_record(post(), [comment(1, 1, body="email email")], NOW, 0, kw)
        self.assertEqual(rec["keyword_hits_post"], 2)
        self.assertEqual(rec["keyword_hits_comments"], 2)


class Filters(unittest.TestCase):
    def rec(self, **kw):
        comments = kw.pop("comments", [comment(1, 5)])
        return pulse.thread_record(post(**kw), comments, NOW, NOW - 30 * DAY, None)

    def test_solved_flair_dropped_case_insensitive(self):
        ok, why = pulse.passes(self.rec(link_flair_text="Solved"), args(), NOW - 30 * DAY)
        self.assertFalse(ok); self.assertEqual(why, "flair:Solved")

    def test_inactive_dropped(self):
        ok, why = pulse.passes(self.rec(created_utc=NOW - 60 * DAY, comments=[comment(1, 45)]), args(), NOW - 30 * DAY)
        self.assertFalse(ok); self.assertEqual(why, "inactive")

    def test_active_kept(self):
        ok, why = pulse.passes(self.rec(), args(), NOW - 30 * DAY)
        self.assertTrue(ok)

    def test_min_comments(self):
        ok, why = pulse.passes(self.rec(comments=[]), args(min_comments=1), NOW - 30 * DAY)
        self.assertFalse(ok); self.assertEqual(why, "few-comments")


class Output(unittest.TestCase):
    def test_trim(self):
        self.assertEqual(pulse.trim("a  b\n\nc", 100), "a b c")
        self.assertTrue(pulse.trim("x" * 50, 10).endswith("…"))

    def test_corpus_omits_authors_by_default(self):
        import tempfile
        rec = pulse.thread_record(post(), [comment(1, 5)], NOW, NOW - 30 * DAY, None)
        a = argparse.Namespace(created_within="90d", active_within="30d", op_chars=100, top_comments=5,
                               comment_chars=100, with_authors=False)
        meta = {"collected_at": "t", "subreddits": ["learnpython"], "keywords": "", "backend": "arctic",
                "requests": 0, "cache_hits": 0}
        with tempfile.TemporaryDirectory() as d:
            pulse.write_corpus([rec], Path(d), a, meta)
            md = (Path(d) / "threads.md").read_text()
            self.assertNotIn("u1", md)
            self.assertIn("[c1 ·", md)
            self.assertIn("abc123", md)
            self.assertTrue((Path(d) / "threads.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
