"""Offline tests for overlay.py: URL parsing, SERP and citation loading, tiering, status merge, no usernames."""
import csv, json, sys, tempfile, time, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from painpoint_radar import overlay, digest  # noqa: E402

NOW = int(time.time()); DAY = 86400


def thread(tid, sub="webdev", created=NOW - 10 * DAY, title="Mailgun alternative", author="op_user_77"):
    return {"id": tid, "subreddit": sub, "title": title, "flair": None, "created_utc": created,
            "last_activity_utc": created + DAY, "score": 3, "num_comments_reported": 4, "num_comments_archived": 4,
            "num_comments_in_window": 2, "keyword_hits_post": 2, "keyword_hits_comments": 1, "pain_terms": 1,
            "activity_score": 2.0, "permalink": f"https://www.reddit.com/r/{sub}/comments/{tid}/", "author": author,
            "selftext": "which one?", "comments": [{"id": "c1", "parent_id": f"t3_{tid}", "author": "helper_9",
                                                    "created_utc": created + 50, "score": 2, "body": "Postmark"}]}


def make_run(tmp: Path) -> Path:
    run = tmp / "run"; (run / "overlay").mkdir(parents=True)
    threads = [thread("aaa111"), thread("bbb222", title="[ Removed by moderator ]"), thread("ccc333", sub="node"), thread("ddd444")]
    (run / "threads.jsonl").write_text("".join(json.dumps(t) + "\n" for t in threads))
    (run / "recommend.jsonl").write_text("".join(json.dumps({"id": t, "J": j, "rank": i}) + "\n"
                                                 for i, (t, j) in enumerate([("aaa111", 0.8), ("ccc333", 0.6), ("ddd444", 0.5)], 1)))
    (run / "extract.jsonl").write_text(json.dumps({"id": "ddd444", "relevant": False}) + "\n")
    (run / "summary.json").write_text(json.dumps({"preset": "t", "collected_at": "2026-09-28T09:38", "subreddits": ["webdev"]}))
    (run / "overlay" / "serp.json").write_text(json.dumps({"country": "us", "fetched_at": "2026-09-30", "keywords": {
        "mailgun alternative": {"reddit": [
            {"position": 3, "type": ["organic"], "url": "https://www.reddit.com/r/webdev/comments/aaa111/mailgun_alternative/"},
            {"position": 1, "type": "ai_overview_sitelink", "url": "https://www.reddit.com/r/webdev/comments/aaa111/mailgun_alternative/"},
            {"position": 3, "type": ["sitelink"], "url": "https://www.reddit.com/r/webdev/comments/old999/x/comment/abc/"},
            {"position": 5, "type": ["organic"], "url": "https://www.reddit.com/r/webdev/comments/bbb222/gone/"}]},
        "smtp service": {"reddit": [{"position": 6, "type": ["organic"], "url": "https://www.reddit.com/r/nextjs/comments/old999/y/"}]},
        "resend alternative": {"reddit": [], "no_serp_data": True},
        "postmark alternative": {"reddit": []}}}))
    (run / "overlay" / "citations.json").write_text(json.dumps({"columns": ["url", "title", "citation_count", "retrieval_count", "mentioned_brand_ids", "prompt_id"], "rows": [
        ["https://reddit.com/r/node/comments/ccc333/t", "Node title", 7, 3, ["kw_own"], "pr_1"],
        ["https://reddit.com/r/node/comments/ccc333/t", "Node title", 5, 2, [], "pr_2"],
        ["https://reddit.com/r/aws/comments/new777/ses", "SES alt", 40, 9, ["kw_x"], {"id": "pr_1"}],
        ["https://example.com/not-reddit", "n", 99, 1, [], "pr_1"]], "rowCount": 4}))
    (run / "overlay" / "prompts.json").write_text(json.dumps({"prompts": {"pr_1": "recommend mailgun alternative", "pr_2": "best email api"}}))
    (run / "overlay" / "brands.json").write_text(json.dumps({"own": ["kw_own"], "brands": {"kw_own": "AcmeMail", "kw_x": "Postmark"}}))
    with (tmp / "status.csv").open("w", newline="") as f:
        w = csv.writer(f); w.writerow(["id", "status", "owner", "note", "updated"]); w.writerow(["ccc333", "commented", "ana", "posted 09-29", "2026-09-29"])
    return run


class ParseTests(unittest.TestCase):
    def test_thread_ref(self):
        self.assertEqual(overlay.thread_ref("https://www.reddit.com/r/webdev/comments/1u9g7cl/mailgun_alternative/"), ("1u9g7cl", "webdev"))
        self.assertEqual(overlay.thread_ref("https://reddit.com/r/AI_Agents/comments/1rovyk9/i_built/comment/abc/"), ("1rovyk9", "AI_Agents"))
        self.assertIsNone(overlay.thread_ref("https://www.google.com/search?q=x+site:reddit.com"))
        self.assertIsNone(overlay.thread_ref(""))


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.run = make_run(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_serp_dedupes_per_keyword_and_keeps_best_position(self):
        by_id, meta = overlay.load_serp(self.run / "overlay" / "serp.json")
        self.assertEqual(by_id["aaa111"]["best_position"], 1)
        self.assertEqual(len(by_id["aaa111"]["keywords"]), 1)
        self.assertEqual(by_id["aaa111"]["keywords"][0]["kinds"], ["ai_overview_sitelink", "organic"])
        self.assertEqual([k["keyword"] for k in by_id["old999"]["keywords"]], ["mailgun alternative", "smtp service"])
        self.assertEqual((meta["keywords_total"], meta["keywords_with_reddit"], meta["keywords_no_serp_data"]), (4, 2, 1))

    def test_citations_sum_over_prompts_and_flag_own_brand(self):
        by_id, meta = overlay.load_citations(self.run / "overlay" / "citations.json", self.run / "overlay" / "prompts.json", self.run / "overlay" / "brands.json")
        self.assertEqual(by_id["ccc333"]["citations"], 12)
        self.assertEqual(by_id["ccc333"]["prompts"], ["recommend mailgun alternative", "best email api"])
        self.assertTrue(by_id["ccc333"]["mentions_own"])
        self.assertFalse(by_id["new777"]["mentions_own"])
        self.assertEqual(by_id["new777"]["brands"], ["Postmark"])
        self.assertNotIn("example", "".join(by_id))
        self.assertEqual(meta["threads"], 2)

    def test_missing_inputs_are_empty_not_errors(self):
        self.assertEqual(overlay.load_serp(self.run / "nope.json"), ({}, {}))
        self.assertEqual(overlay.load_citations(self.run / "nope.json"), ({}, {}))


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name); self.run = make_run(self.root)
        self.status = overlay.load_status(self.root / "status.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def lookup(self, ids):
        self.looked_up = ids
        return [{"id": "old999", "subreddit": "nextjs", "title": "old thread", "created_utc": NOW - 400 * DAY, "score": 10,
                 "num_comments": 30, "author": "someone_else", "selftext": ""},
                {"id": "new777", "subreddit": "aws", "title": "SES alt", "created_utc": NOW - 20 * DAY, "score": 1,
                 "num_comments": 3, "author": "ses_op", "selftext": ""}]

    def test_tiers_states_and_order(self):
        res = overlay.build(self.run, {"subreddits": {"node": {"comment_policy": "closed"}}}, status=self.status, now=NOW,
                            lookup=self.lookup, activity=lambda post: [{"id": "k", "parent_id": "x", "author": "z", "created_utc": NOW - 5 * DAY, "score": 1, "body": "hi"}])
        rows = {r["id"]: r for r in res["rows"]}
        self.assertEqual(sorted(self.looked_up), ["new777", "old999"])
        self.assertEqual((rows["aaa111"]["tier"], rows["aaa111"]["state"]), (1, "open"))
        self.assertEqual((rows["ccc333"]["tier"], rows["ccc333"]["state"], rows["ccc333"]["comment_policy"]), (1, "open", "closed"))
        self.assertEqual((rows["old999"]["tier"], rows["old999"]["state"]), (2, "active"))  # 400 d old but a comment 5 d ago
        self.assertEqual((rows["new777"]["tier"], rows["new777"]["state"]), (2, "active"))
        self.assertEqual((rows["bbb222"]["tier"], rows["bbb222"]["state"]), (4, "removed"))
        self.assertNotIn("ddd444", rows)  # judged irrelevant and not trusted by Google or AI
        order = [r["id"] for r in res["rows"]]
        self.assertLess(order.index("ccc333"), order.index("aaa111"))  # 12 citations beats a Google #1 with none
        self.assertLess(order.index("aaa111"), order.index("new777"))  # tier 1 before tier 2
        self.assertLess(order.index("new777"), order.index("old999"))  # both active: 40 citations before 0
        self.assertEqual(rows["ccc333"]["status"], "commented"); self.assertEqual(rows["ccc333"]["owner"], "ana")
        self.assertEqual(rows["aaa111"]["status"], "new")
        self.assertEqual(rows["old999"]["last_activity_utc"], NOW - 5 * DAY)
        self.assertEqual(res["counts"], {"tier1": 2, "tier2": 2, "tier2_active": 2, "tier3": 0, "removed_but_trusted": 1, "mentions_own": 1})

    def test_offline_keeps_unknown_state(self):
        res = overlay.build(self.run, {}, now=NOW)
        rows = {r["id"]: r for r in res["rows"]}
        self.assertEqual(rows["old999"]["state"], "unknown"); self.assertEqual(res["lookup"], "offline")

    def test_render_has_no_usernames_and_digest_picks_up_overlay(self):
        catalog = {"product": {"name": "AcmeMail", "aliases": ["acmemail"], "keywords": {"core": []}}}
        res = overlay.build(self.run, catalog, status=self.status, now=NOW, lookup=self.lookup)
        self.assertEqual(res["product"], "AcmeMail")
        rows = {r["id"]: r for r in res["rows"]}
        self.assertEqual(rows["old999"]["state"], "old"); self.assertEqual(rows["new777"]["state"], "quiet")  # metadata only
        md = overlay.render(res)
        for name in ("op_user_77", "helper_9", "someone_else", "ses_op", "ana_x"):
            self.assertNotIn(name, md)
        self.assertIn("AcmeMail named", md); self.assertIn("mailgun alternative #1", md)
        (self.run / "overlay.json").write_text(json.dumps(res))
        out = digest.build(self.run)
        self.assertIn("## 2b. Backlog", out); self.assertIn("| Google | AI answers |", out)
        self.assertIn("12 citations", out)


if __name__ == "__main__":
    unittest.main()
