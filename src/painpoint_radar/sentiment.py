"""sentiment: aspect-level provider sentiment with two independent scorers and agreement.

For every provider in the catalog, take the sentences in each relevant thread that name it, then:
  scorer A  VADER (lexicon, published formula) -> mean compound -> thread vote (pos/neu/neg, ±0.05)
  scorer B  LLM on a fixed 1-5 rubric (via llm.py)  -> thread vote
One thread = one vote. Per provider: Cohen's kappa between the scorers, net sentiment with a Wilson
interval on each share and a bootstrap interval on the net, and a report/refuse decision
(stats.report_allowed: n >= 10 and kappa >= 0.4). Numbers are only printed for providers that clear it.

Reads  runs/<stamp>/threads.jsonl, runs/<stamp>/extract.jsonl, the product catalog (providers)
Writes runs/<stamp>/sentiment.json

Needs `vaderSentiment` (install the `sentiment` extra). Without it scorer A is missing, kappa is null and nothing is reported.

CLI
  painpoint-radar sentiment --run runs/<stamp> [--catalog PATH] [--all-threads] [--backend ..] [--model ..]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from . import llm
from . import pulse
from . import stats

try:
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
except ImportError:  # pragma: no cover
    SentimentIntensityAnalyzer = None

MAX_SENTENCES = 12
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

RUBRIC_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "provider", "score"],
        "properties": {"id": {"type": "string"}, "provider": {"type": "string"},
                       "score": {"type": "integer", "minimum": 1, "maximum": 5}}}}},
}
RUBRIC_PROMPT = """Rate the sentiment TOWARD THE NAMED PROVIDER in each excerpt, on this fixed scale:
  1  strongly negative: leaving it, calling it broken, unreliable, a scam, warning others off
  2  negative: a complaint, a problem blamed on it, a reluctant "it's meh"
  3  neutral or mixed: named without judgement, a factual comparison, praise and complaint balanced
  4  positive: works well, recommended among others, happy with it
  5  strongly positive: the clear recommendation, "just use X", solved the problem
Judge only what is said about the provider, not the mood of the thread. Technical words like "bounce",
"spam", "kill", "error" are not negative sentiment by themselves. Return one score per (id, provider).

EXCERPTS
"""


def load_providers(catalog: Path | None) -> dict[str, list[str]]:
    if catalog and catalog.exists():
        try:
            p = json.loads(catalog.read_text()).get("providers")
            if isinstance(p, dict) and p:
                return {k: [a.lower() for a in v] for k, v in p.items()}
        except json.JSONDecodeError:
            pass
    return {}


def provider_regexes(providers: dict[str, list[str]]) -> dict[str, re.Pattern]:
    out = {}
    for name, aliases in providers.items():
        alts = sorted({re.escape(a).replace(r"\ ", r"\s+") for a in aliases + [name.lower()]}, key=len, reverse=True)
        out[name] = re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.I)
    return out


def mention_sentences(rec: dict, rx: re.Pattern, cap: int = MAX_SENTENCES) -> list[str]:
    texts = [f"{rec.get('title', '')}. {rec.get('selftext', '')}"] + [c.get("body") or "" for c in rec.get("comments", [])]
    found = []
    for t in texts:
        for s in SENT_SPLIT.split(t):
            s = s.strip()
            if s and rx.search(s):
                found.append(s[:400])
                if len(found) >= cap:
                    return found
    return found


def vader_votes(pairs: list[tuple[str, str, list[str]]]) -> dict[tuple[str, str], str]:
    if SentimentIntensityAnalyzer is None:
        return {}
    an = SentimentIntensityAnalyzer()
    return {(tid, prov): stats.vader_vote([an.polarity_scores(s)["compound"] for s in sents]) for tid, prov, sents in pairs}


def llm_votes(pairs: list[tuple[str, str, list[str]]], backend, model, use_cache, batch: int = 25) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for i in range(0, len(pairs), batch):
        chunk = pairs[i:i + batch]
        body = "\n\n".join(f"id={tid} provider={prov}\n" + "\n".join(f"  - {s}" for s in sents) for tid, prov, sents in chunk)
        res = llm.complete(RUBRIC_PROMPT + body, RUBRIC_SCHEMA, backend=backend, model=model, use_cache=use_cache)
        got = {(str(x.get("id")), str(x.get("provider"))): x.get("score") for x in res.get("items", [])}
        for tid, prov, _ in chunk:
            score = got.get((tid, prov))
            if isinstance(score, int) and 1 <= score <= 5:
                out[(tid, prov)] = stats.rubric_vote(score)
    return out


def run(run_dir: Path, catalog: Path | None, all_threads: bool, backend, model, use_cache) -> dict:
    threads = [json.loads(l) for l in (run_dir / "threads.jsonl").read_text().splitlines() if l.strip()]
    ex_path = run_dir / "extract.jsonl"
    relevant = None
    if ex_path.exists() and not all_threads:
        relevant = {e["id"] for e in (json.loads(l) for l in ex_path.read_text().splitlines() if l.strip()) if e.get("relevant")}
    providers = load_providers(catalog)
    rxs = provider_regexes(providers)
    pairs = []
    for rec in threads:
        if relevant is not None and rec["id"] not in relevant:
            continue
        for name, rx in rxs.items():
            sents = mention_sentences(rec, rx)
            if sents:
                pairs.append((rec["id"], name, sents))
    a = vader_votes(pairs)
    b = llm_votes(pairs, backend, model, use_cache) if pairs else {}
    result = {"threads_considered": len(pairs and {p[0] for p in pairs}) if pairs else 0,
              "scorer_a": "vader" if a else None, "scorer_b": "llm-rubric-1-5", "min_n": 10, "min_kappa": 0.4,
              "providers": {}}
    if not providers:
        result["note"] = "no providers in the catalog; nothing scored"
    for name in providers:
        keys = [(tid, prov) for tid, prov, _ in pairs if prov == name]
        both = [k for k in keys if k in a and k in b]
        va, vb = [a[k] for k in both], [b[k] for k in both]
        kappa = stats.cohen_kappa(va, vb) if both else None
        allowed, reason = stats.report_allowed(len(both), kappa if kappa is not None else -1.0)
        entry = {"mentioning_threads": len(keys), "n_scored": len(both), "kappa": None if kappa is None else round(kappa, 3),
                 "reported": allowed, "reason": "" if allowed else reason,
                 "llm": stats.aggregate(vb) if both else None, "vader": stats.aggregate(va) if both else None,
                 "thread_ids": [k[0] for k in keys]}
        if not a:
            entry["reported"], entry["reason"] = False, "scorer A (VADER) unavailable"
        result["providers"][name] = entry
    (run_dir / "sentiment.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar sentiment", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--catalog", default=str(pulse.DEFAULT_CATALOG))
    ap.add_argument("--all-threads", action="store_true", help="score every thread, not only extract.jsonl relevant ones")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args(argv)
    r = run(Path(a.run), Path(a.catalog), a.all_threads, a.backend, a.model, not a.no_cache)
    for name, e in sorted(r["providers"].items(), key=lambda kv: -kv[1]["n_scored"]):
        if e["mentioning_threads"] == 0:
            continue
        if e["reported"]:
            agg = e["llm"]
            print(f"{name:14s} n={e['n_scored']:3d} kappa={e['kappa']:.2f} net={agg['net']:+.2f} ci=[{agg['net_ci'][0]:+.2f},{agg['net_ci'][1]:+.2f}] pos={agg['pos']} neu={agg['neu']} neg={agg['neg']}")
        else:
            print(f"{name:14s} n={e['n_scored']:3d} not reported: {e['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
