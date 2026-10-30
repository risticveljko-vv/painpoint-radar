"""cluster: group extracted pain points into themes and count threads per theme.

Reads  runs/<stamp>/threads.jsonl and runs/<stamp>/extract.jsonl
Writes runs/<stamp>/clusters.json

Two model calls (via llm.py): propose up to N themes from every relevant problem statement, then
assign each thread to one theme (or none). Counts are threads, never comments. "rising" means the
theme's threads skew to the most recent third of the corpus's creation window.

CLI
  painpoint-radar cluster --run runs/<stamp> [--max-clusters 12] [--backend claude|codex] [--model M] [--no-cache]
"""
from __future__ import annotations

import argparse
import json
import sys
import textwrap
from collections import Counter
from pathlib import Path

from . import llm
from . import pulse

PROPOSE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["clusters"],
    "properties": {"clusters": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["label", "description", "example_ids"],
        "properties": {"label": {"type": "string"}, "description": {"type": "string"},
                       "example_ids": {"type": "array", "items": {"type": "string"}}}}}},
}
ASSIGN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["assignments"],
    "properties": {"assignments": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "cluster"],
        "properties": {"id": {"type": "string"}, "cluster": {"type": "string"}}}}},
}

PROPOSE_INTRO = "You are grouping developer pain points about {domain} ({topics}) into themes for a content team."
PROPOSE_BODY = """ Below are problem statements, one per thread, as "id | topic | problem".

Propose at most {k} themes. A theme is a recurring PROBLEM, not a product category: "emails from a new domain land
in spam despite DKIM/SPF" is a theme, "deliverability" is not. Each theme needs a short label (max 8 words), a
one-sentence description of the pain, and 2-5 example ids from the list. Prefer fewer, sharper themes over many
vague ones; leave one-off problems out.

PROBLEMS
{problems}
"""
PROPOSE_PROMPT = ""


def configure(product: dict) -> None:
    """Build the proposal prompt from the product. Wrapped at 100 columns, so an unchanged catalog keeps its llm cache key."""
    global PROPOSE_PROMPT
    domain = product.get("domain") or product.get("category") or "their domain"
    topics = ", ".join(t for t in (product.get("topics") or []) if t != "other") or "any topic"
    PROPOSE_PROMPT = textwrap.fill(PROPOSE_INTRO.format(domain=domain, topics=topics), width=100) + PROPOSE_BODY


configure({})
ASSIGN_PROMPT = """Assign each problem below to exactly one of these themes, or to "none" if it fits none.

THEMES
{themes}

PROBLEMS (id | topic | problem)
{problems}

Return one assignment per id, using the theme label exactly as written or "none".
"""


def _line(e: dict) -> str:
    return f"{e['id']} | {e.get('topic', 'other')} | {(e.get('problem') or '').strip()}"


def propose(items: list[dict], k: int, backend, model, use_cache) -> list[dict]:
    prompt = PROPOSE_PROMPT.format(k=k, problems="\n".join(_line(e) for e in items))
    out = llm.complete(prompt, PROPOSE_SCHEMA, backend=backend, model=model, use_cache=use_cache)
    seen, clusters = set(), []
    for c in out.get("clusters", []):
        label = (c.get("label") or "").strip()
        if label and label.lower() not in seen:
            seen.add(label.lower())
            clusters.append({"label": label, "description": (c.get("description") or "").strip(),
                             "example_ids": [str(i) for i in c.get("example_ids", [])]})
    return clusters[:k]


def assign(items: list[dict], clusters: list[dict], backend, model, use_cache, batch: int = 40) -> dict[str, str]:
    themes = "\n".join(f"- {c['label']}: {c['description']}" for c in clusters)
    labels = {c["label"].lower(): c["label"] for c in clusters}
    result: dict[str, str] = {}
    for i in range(0, len(items), batch):
        chunk = items[i:i + batch]
        prompt = ASSIGN_PROMPT.format(themes=themes, problems="\n".join(_line(e) for e in chunk))
        out = llm.complete(prompt, ASSIGN_SCHEMA, backend=backend, model=model, use_cache=use_cache)
        got = {str(a.get("id")): (a.get("cluster") or "none") for a in out.get("assignments", [])}
        for e in chunk:
            result[e["id"]] = labels.get(got.get(e["id"], "none").strip().lower(), "none")
    return result


def summarise(clusters: list[dict], assignment: dict[str, str], threads: dict[str, dict],
              extracted: dict[str, dict]) -> list[dict]:
    created = [threads[t]["created_utc"] for t in assignment if t in threads]
    lo, hi = (min(created), max(created)) if created else (0, 0)
    cutoff = hi - (hi - lo) / 3 if hi > lo else hi
    out = []
    for c in clusters:
        ids = sorted((t for t, lab in assignment.items() if lab == c["label"]),
                     key=lambda t: threads.get(t, {}).get("activity_score", 0), reverse=True)
        if not ids:
            continue
        subs = Counter(threads[t]["subreddit"] for t in ids if t in threads)
        tools = Counter(tool for t in ids for tool in extracted.get(t, {}).get("tools_named", []))
        cr = [threads[t]["created_utc"] for t in ids if t in threads]
        recent = sum(1 for x in cr if x >= cutoff) / len(cr) if cr else 0.0
        unanswered = sum(1 for t in ids if not extracted.get(t, {}).get("answered"))
        out.append({"label": c["label"], "description": c["description"], "n": len(ids), "thread_ids": ids,
                    "subs": dict(subs.most_common()), "tools": dict(tools.most_common(8)),
                    "first_created": min(cr) if cr else None, "last_created": max(cr) if cr else None,
                    "recent_share": round(recent, 2), "rising": len(ids) >= 3 and recent > 0.45,
                    "unanswered": unanswered})
    out.sort(key=lambda c: c["n"], reverse=True)
    return out


def run(run_dir: Path, max_clusters: int, backend, model, use_cache) -> dict:
    threads = {r["id"]: r for r in (json.loads(l) for l in (run_dir / "threads.jsonl").read_text().splitlines() if l.strip())}
    extracted = {e["id"]: e for e in (json.loads(l) for l in (run_dir / "extract.jsonl").read_text().splitlines() if l.strip())}
    items = [e for e in extracted.values() if e.get("relevant") and (e.get("problem") or "").strip()]
    result = {"relevant_threads": len(items), "clusters": [], "unassigned": []}
    if len(items) < 3:
        result["note"] = "fewer than 3 relevant threads; no clustering"
    else:
        clusters = propose(items, max_clusters, backend, model, use_cache)
        assignment = assign(items, clusters, backend, model, use_cache) if clusters else {}
        result["clusters"] = summarise(clusters, assignment, threads, extracted)
        result["unassigned"] = sorted(t for t, lab in assignment.items() if lab == "none")
    (run_dir / "clusters.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar cluster", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--max-clusters", type=int, default=12)
    ap.add_argument("--backend", default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--catalog", default=str(pulse.DEFAULT_CATALOG), help="product catalog; sets the prompt wording")
    a = ap.parse_args(argv)
    configure(pulse.product_of(pulse.load_catalog(a.catalog)))
    r = run(Path(a.run), a.max_clusters, a.backend, a.model, not a.no_cache)
    for c in r["clusters"]:
        print(f"{c['n']:3d}  {'↑ ' if c['rising'] else '  '}{c['label']}  ({', '.join(f'r/{s} {n}' for s, n in list(c['subs'].items())[:3])})")
    print(f"unassigned: {len(r['unassigned'])} of {r['relevant_threads']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
