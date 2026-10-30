"""Offline tests for the pipeline modules: extract, cluster, sentiment, delta, digest, seed."""
import json, sys, time, tempfile, unittest
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import extract, cluster, sentiment, delta, digest, seed  # noqa: E402

NOW = int(time.time()); DAY = 86400


def thread(tid="t1", sub="django", created=NOW - 10 * DAY, comments=None, author="alice_op", **kw):
    rec = {"id": tid, "subreddit": sub, "title": "Emails land in spam with SendGrid", "flair": None,
           "created_utc": created, "last_activity_utc": created + DAY, "score": 5, "num_comments_reported": 2,
           "num_comments_archived": 2, "num_comments_in_window": 2, "keyword_hits_post": 2, "keyword_hits_comments": 1,
           "pain_terms": 1, "activity_score": 3.0, "permalink": f"https://www.reddit.com/r/{sub}/comments/{tid}/",
           "author": author, "selftext": "SendGrid keeps putting my mail in spam. Postmark just works though.",
           "comments": comments if comments is not None else [
               {"id": "c1", "parent_id": f"t3_{tid}", "author": "bob_helper", "created_utc": created + 100, "score": 9,
                "body": "Check DMARC. Honestly SendGrid support is useless."},
               {"id": "c2", "parent_id": f"t3_{tid}", "author": author, "created_utc": created + DAY, "score": 1,
                "body": "Thanks, will try."}]}
    rec.update(kw)
    return rec


class Extract(unittest.TestCase):
    def test_render_is_authorless_and_marks_op(self):
        s = extract.render_thread(thread())
        self.assertNotIn("alice_op", s); self.assertNotIn("bob_helper", s)
        self.assertIn("- OP (score 1)", s); self.assertIn("- commenter (score 9)", s)
        self.assertIn("id=t1 sub=r/django", s)

    def test_blank_record_matches_contract(self):
        b = extract._blank("x", "why")
        for k in extract.SCHEMA["properties"]["items"]["items"]["required"]:
            self.assertIn(k, b)
        self.assertFalse(b["relevant"])


class Cluster(unittest.TestCase):
    def test_summarise_counts_threads_and_rising(self):
        threads = {f"t{i}": thread(f"t{i}", created=NOW - (30 - i) * DAY) for i in range(6)}
        extracted = {t: {"tools_named": ["SendGrid"], "answered": i % 2 == 0} for i, t in enumerate(threads)}
        clusters = [{"label": "Spam placement", "description": "d", "example_ids": []},
                    {"label": "Empty", "description": "d", "example_ids": []}]
        assignment = {"t0": "Spam placement", "t1": "Spam placement", "t4": "Spam placement", "t5": "Spam placement",
                      "t2": "none", "t3": "none"}
        out = cluster.summarise(clusters, assignment, threads, extracted)
        self.assertEqual(len(out), 1)  # empty cluster dropped
        c = out[0]
        self.assertEqual(c["n"], 4); self.assertEqual(c["tools"], {"SendGrid": 4}); self.assertEqual(c["unanswered"], 2)
        self.assertEqual(c["recent_share"], 0.5); self.assertTrue(c["rising"])


class Sentiment(unittest.TestCase):
    def test_provider_regex_word_bounded_and_aliases(self):
        rx = sentiment.provider_regexes({"Amazon SES": ["ses", "aws ses"], "Resend": ["resend"]})
        self.assertTrue(rx["Amazon SES"].search("we moved to AWS  SES last week"))
        self.assertFalse(rx["Amazon SES"].search("sesame streets"))
        self.assertFalse(rx["Resend"].search("please resend-me"))  # hyphen-joined token is not a mention
        self.assertTrue(rx["Resend"].search("Resend is great"))

    def test_mention_sentences_split_and_cap(self):
        rx = sentiment.provider_regexes({"SendGrid": ["sendgrid"]})
        sents = sentiment.mention_sentences(thread(), rx["SendGrid"])
        self.assertEqual(len(sents), 3)  # title, OP sentence, one comment sentence
        self.assertTrue(all("SendGrid" in s for s in sents))
        self.assertNotIn("Postmark just works", " ".join(sents))

    def test_no_providers_when_catalog_missing(self):
        self.assertEqual(sentiment.load_providers(Path("/nonexistent/catalog.json")), {})


class Delta(unittest.TestCase):
    def test_match_by_label_then_overlap(self):
        cur = [{"label": "Spam placement", "n": 5, "thread_ids": ["a", "b", "c", "d", "e"]},
               {"label": "New wording", "n": 3, "thread_ids": ["x", "y", "z"]},
               {"label": "Brand new", "n": 2, "thread_ids": ["q", "r"]}]
        prev = [{"label": "spam placement", "n": 3, "thread_ids": ["a", "b", "k"]},
                {"label": "Old wording", "n": 4, "thread_ids": ["x", "y", "w", "v"]},
                {"label": "Vanished", "n": 2, "thread_ids": ["m", "n"]}]
        matched, gone = delta.match_clusters(cur, prev)
        self.assertEqual([m["matched_by"] for m in matched], ["label", "overlap", None])
        self.assertEqual(matched[0]["delta"], 2); self.assertEqual(matched[1]["previous_label"], "Old wording")
        self.assertEqual([g["label"] for g in gone], ["Vanished"])

    def test_find_previous_same_preset_earlier(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d)
            for name, when, preset in (("a", "2026-09-01T00:00:00+00:00", "p"), ("b", "2026-09-10T00:00:00+00:00", "p"),
                                       ("c", "2026-09-20T00:00:00+00:00", "p"), ("z", "2026-09-15T00:00:00+00:00", "other")):
                (runs / name).mkdir()
                (runs / name / "summary.json").write_text(json.dumps({"collected_at": when, "preset": preset, "subreddits": []}))
                (runs / name / "threads.jsonl").write_text("")
            self.assertEqual(delta.find_previous(runs / "c", runs).name, "b")
            self.assertIsNone(delta.find_previous(runs / "a", runs))


class Digest(unittest.TestCase):
    def test_digest_has_sections_and_no_usernames(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            t = thread()
            (run / "threads.jsonl").write_text(json.dumps(t) + "\n")
            (run / "summary.json").write_text(json.dumps({"preset": "email-devs", "collected_at": "2026-09-25T10:00:00+00:00",
                "subreddits": ["django"], "created_within": "90d", "active_within": "30d", "min_hits": 2, "threads_kept": 1,
                "threads_dropped": {"few-hits": 3}, "kept_by_sub": {"django": 1}, "requests": 12}))
            (run / "clusters.json").write_text(json.dumps({"clusters": [{"label": "Spam placement", "description": "x",
                "n": 1, "thread_ids": ["t1"], "subs": {"django": 1}, "tools": {"SendGrid": 1}, "first_created": t["created_utc"],
                "last_created": t["created_utc"], "recent_share": 1.0, "rising": False, "unanswered": 1}]}))
            (run / "sentiment.json").write_text(json.dumps({"min_n": 10, "min_kappa": 0.4, "providers": {
                "SendGrid": {"mentioning_threads": 1, "n_scored": 1, "kappa": 1.0, "reported": False,
                              "reason": "n=1 is below minimum 10", "thread_ids": ["t1"]}}}))
            (run / "recommend.jsonl").write_text(json.dumps({"rank": 1, "id": "t1", "J": 0.8, "subreddit": "django",
                "title": t["title"], "last_activity_utc": t["last_activity_utc"], "num_comments": 2,
                "comment_policy": "unknown", "why": "act 1 · op 1", "permalink": t["permalink"]}) + "\n")
            (run / "delta.json").write_text(json.dumps({"previous_run": None, "note": "no earlier run of this preset"}))
            md = digest.build(run)
            for h in ("## 1. Run facts", "## 2. Threads worth joining", "## 3. Pain-point themes",
                      "## 4. Provider sentiment", "## 5. Since the previous run", "## 6. Evidence index"):
                self.assertIn(h, md)
            self.assertIn("Not reported (below the bar): SendGrid", md)
            self.assertNotIn("alice_op", md); self.assertNotIn("bob_helper", md)
            self.assertIn("`t1`", md)


class Seed(unittest.TestCase):
    def test_tokens_from_slugs(self):
        urls = ["https://x.io/blog/smtp-relay-setup/", "https://x.io/blog/smtp-relay-guide/", "https://x.io/blog/smtp-relay-2024/",
                "https://x.io/blog/best-email-api/", "https://x.io/"]
        uni, bi = seed.tokens_from_urls(urls)
        self.assertEqual(uni["smtp"], 3); self.assertEqual(bi["smtp relay"], 3)
        self.assertNotIn("best", uni); self.assertNotIn("2024", uni)


if __name__ == "__main__":
    unittest.main()
