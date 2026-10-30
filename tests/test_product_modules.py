"""Prompts, labels and run pairing come from the product block, never from code."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from painpoint_radar import cluster, delta, digest, extract, overlay, pulse, sentiment  # noqa: E402

OLD_PROPOSE_PROMPT = 'You are grouping developer pain points about email (sending, deliverability, inbound, testing,\nmarketing) into themes for a content team. Below are problem statements, one per thread, as "id | topic | problem".\n\nPropose at most {k} themes. A theme is a recurring PROBLEM, not a product category: "emails from a new domain land\nin spam despite DKIM/SPF" is a theme, "deliverability" is not. Each theme needs a short label (max 8 words), a\none-sentence description of the pain, and 2-5 example ids from the list. Prefer fewer, sharper themes over many\nvague ones; leave one-off problems out.\n\nPROBLEMS\n{problems}\n'

ACME = {"name": "Acme", "aliases": ["acme", "acme mail"], "domain": "widgets", "category": "widget hosting",
        "tool_label": "widget hosts", "relevance": "the thread is about hosting widgets.", "topics": ["hosting", "billing"]}


class ExtractConfig(unittest.TestCase):
    def tearDown(self):
        extract.configure({})

    def test_topics_schema_and_prompt_follow_product(self):
        extract.configure(ACME, {"Acme": ["acme"], "Rival": ["rival"], "Third": ["third"], "Fourth": ["fourth"]})
        self.assertEqual(extract.TOPICS, ["hosting", "billing", "other"])
        self.assertEqual(extract.SCHEMA["properties"]["items"]["items"]["properties"]["topic"]["enum"], ["hosting", "billing", "other"])
        for needle in ("widget hosting", "hosting widgets", "hosting | billing | other", "widget hosts", '"Acme", "Rival", "Third"'):
            self.assertIn(needle, extract.PROMPT)
        self.assertNotIn("Fourth", extract.PROMPT)
        self.assertNotIn("email", extract.PROMPT.lower())

    def test_generic_defaults_without_product(self):
        extract.configure({})
        self.assertEqual(extract.TOPICS, ["other"])
        self.assertIn("software tooling", extract.PROMPT)
        self.assertIn("Vendor A", extract.PROMPT)


class ClusterConfig(unittest.TestCase):
    def tearDown(self):
        cluster.configure({})

    def test_catalog_renders_the_reference_prompt_byte_for_byte(self):
        cluster.configure(pulse.product_of(pulse.load_catalog(ROOT / "tests" / "fixtures" / "catalogs" / "acmemail.json")))
        self.assertEqual(cluster.PROPOSE_PROMPT, OLD_PROPOSE_PROMPT)

    def test_other_product(self):
        cluster.configure(ACME)
        self.assertIn("about widgets (hosting, billing)", cluster.PROPOSE_PROMPT)
        self.assertIn("{k}", cluster.PROPOSE_PROMPT)
        self.assertIn("{problems}", cluster.PROPOSE_PROMPT)


class DeltaPairing(unittest.TestCase):
    def test_pairs_same_product_and_preset_only(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d)
            for name, when, product in (("a", "2026-09-01T00:00:00+00:00", "X"), ("b", "2026-09-10T00:00:00+00:00", "Y"),
                                        ("c", "2026-09-20T00:00:00+00:00", "X"), ("old", "2026-09-15T00:00:00+00:00", None)):
                (runs / name).mkdir()
                s = {"collected_at": when, "preset": "p", "subreddits": []}
                if product:
                    s["product"] = product
                (runs / name / "summary.json").write_text(json.dumps(s))
                (runs / name / "threads.jsonl").write_text("")
            self.assertEqual(delta.find_previous(runs / "c", runs, legacy="Z").name, "a")
            self.assertEqual(delta.find_previous(runs / "c", runs, legacy="X").name, "old")
            self.assertIsNone(delta.find_previous(runs / "b", runs, legacy="Z"))


class OverlayOwnBrand(unittest.TestCase):
    def test_alias_fallback_when_no_peec_brand_ids(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "citations.json"
            p.write_text(json.dumps({"columns": ["url", "title", "citation_count", "retrieval_count", "prompt_id", "mentioned_brand_ids"],
                                     "rows": [["https://www.reddit.com/r/x/comments/aaa111/t/", "Acme Mail vs others", 2, 3, "pr_1", []],
                                              ["https://www.reddit.com/r/x/comments/bbb222/t/", "Nothing here", 1, 1, "pr_1", []]]}))
            by_id, meta = overlay.load_citations(p, None, None, own_aliases=["acme", "acme mail"])
            self.assertTrue(by_id["aaa111"]["mentions_own"])
            self.assertFalse(by_id["bbb222"]["mentions_own"])
            self.assertEqual(meta["own_match"], "alias")
            by_id, meta = overlay.load_citations(p, None, None)
            self.assertFalse(by_id["aaa111"]["mentions_own"])
            self.assertEqual(meta["own_match"], "none")

    def test_cells_and_render_use_the_product_name(self):
        row = {"id": "aaa111", "rank": 1, "tier": 1, "state": "open", "subreddit": "x", "title": "T", "permalink": "https://r/x",
               "last_activity_utc": 0, "age_days": 1, "J": 0.5, "comment_policy": "unknown", "google": None,
               "ai": {"citations": 2, "prompts": ["p"], "mentions_own": True}, "status": "new", "owner": ""}
        self.assertIn("**Acme named**", overlay.ai_cell(row, "Acme"))
        result = {"generated_at": "now", "product": "Acme", "serp": {}, "citations": {}, "lookup": "offline",
                  "counts": {"tier1": 1, "tier2": 0, "tier2_active": 0, "tier3": 0, "removed_but_trusted": 0, "mentions_own": 1}, "rows": [row]}
        md = overlay.render(result)
        self.assertIn("Acme named", md)
        self.assertIn("`*` = Acme already named", md)


class DigestLabels(unittest.TestCase):
    def test_brand_from_summary_and_no_provider_note(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "summary.json").write_text(json.dumps({"product": "Acme", "preset": "p", "collected_at": "2026-10-03T00:00:00+00:00",
                                                          "subreddits": ["x"], "threads_kept": 0, "threads_dropped": {}, "kept_by_sub": {}}))
            (run / "sentiment.json").write_text(json.dumps({"providers": {}, "note": "no providers in the catalog; nothing scored"}))
            row = {"id": "aaa111", "rank": 1, "tier": 1, "state": "open", "subreddit": "x", "title": "T", "permalink": "https://r/x",
                   "last_activity_utc": 0, "age_days": 1, "J": 0.5, "comment_policy": "unknown", "google": None,
                   "ai": {"citations": 2, "prompts": ["p"], "mentions_own": True}, "status": "new", "owner": ""}
            (run / "overlay.json").write_text(json.dumps({"product": "Acme", "serp": {}, "citations": {}, "rows": [row],
                                                          "counts": {"tier1": 1, "tier2": 0, "tier2_active": 0, "mentions_own": 1, "removed_but_trusted": 0}}))
            (run / "recommend.jsonl").write_text(json.dumps({"id": "aaa111", "rank": 1, "J": 0.5, "subreddit": "x", "title": "T", "permalink": "https://r/x",
                                                           "last_activity_utc": 0, "num_comments": 1, "comment_policy": "unknown", "why": ""}) + "\n")
            md = digest.build(run)
            self.assertEqual(md.count("**Acme named**"), 2)  # section 2 row and section 2b row
            self.assertIn("Acme already named in 1", md)
            self.assertIn("**Acme named**", md)
            self.assertIn("No providers in the catalog", md)


class NoBrandInCode(unittest.TestCase):
    def test_rule_8_no_product_name_in_modules(self):
        catalogs = [*(ROOT / "tests" / "fixtures" / "catalogs").glob("*.json"), *pulse.CATALOGS_DIR.glob("*.json")]
        brands = {term.casefold() for path in catalogs for product in [json.loads(path.read_text())["product"]]
                  for term in [product["name"], *product["aliases"]]}
        modules = sorted((ROOT / "src" / "painpoint_radar").glob("*.py"))
        self.assertTrue(brands and modules)
        offenders = [f"{py.name}:{n}: {line.strip()[:80]}" for py in modules
                     for n, line in enumerate(py.read_text().splitlines(), 1)
                     if any(brand in line.casefold() for brand in brands)]
        self.assertEqual(offenders, [])

    def test_sentiment_without_providers_notes_it(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "threads.jsonl").write_text("")
            r = sentiment.run(run, Path(d) / "missing.json", False, None, None, False)
            self.assertEqual(r["providers"], {})
            self.assertIn("no providers", r["note"])


if __name__ == "__main__":
    unittest.main()
