"""extract: turn a corpus into structured pain-point records with an LLM.

Reads  runs/<stamp>/threads.jsonl
Writes runs/<stamp>/extract.jsonl   one record per thread, see docs/ARCHITECTURE.md "Data contracts"
       runs/<stamp>/extract.summary.json

Each thread is sent once, in batches, as title + OP + top comments with authors replaced by
"OP" / "commenter". Results are cached per prompt and thread id under ~/.cache/painpoint-radar/extract/ so a re-run only
pays for new threads. Standard library only; the model call goes through llm.py.

CLI
  painpoint-radar extract --run runs/<stamp> [--catalog PATH] [--batch 8] [--limit N] [--no-cache] [--backend claude|codex] [--model M]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from . import llm
from . import pulse

TOPICS: list[str] = ["other"]
EXTRACT_CACHE = Path(os.environ.get("PAINPOINT_RADAR_CACHE", Path.home() / ".cache" / "painpoint-radar")) / "extract"


def _schema(topics: list[str]) -> dict:
    return {
        "type": "object", "additionalProperties": False, "required": ["items"],
        "properties": {"items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "relevant", "topic", "problem", "stack", "tools_named", "tried", "wanted",
                         "answered", "answer_quality", "vendor_present", "vendor_accepted"],
            "properties": {
                "id": {"type": "string"},
                "relevant": {"type": "boolean"},
                "topic": {"type": "string", "enum": topics},
                "problem": {"type": "string"},
                "stack": {"type": "array", "items": {"type": "string"}},
                "tools_named": {"type": "array", "items": {"type": "string"}},
                "tried": {"type": "string"},
                "wanted": {"type": "string"},
                "answered": {"type": "boolean"},
                "answer_quality": {"type": "integer", "minimum": 0, "maximum": 3},
                "vendor_present": {"type": "array", "items": {"type": "string"}},
                "vendor_accepted": {"type": "boolean"},
            }}}},
    }


SCHEMA = _schema(TOPICS)

PROMPT_TEMPLATE = """You are a research analyst reading public developer forum threads for a company that sells
{category}. For EACH thread below return one item.

Definitions
- relevant: {relevance}
- topic: {topic_list} (other only if relevant is true but
  none fit; if relevant is false use other).
- problem: the poster's actual problem in one plain sentence, in your own words, no quotes, no names.
- stack: languages, frameworks, hosts, servers named (e.g. "Django", "Laravel", "Postfix", "Gmail").
- tools_named: {tool_label} named anywhere in the thread (e.g. {examples}).
- tried: what the poster already tried, one sentence, or "" if nothing.
- wanted: what a good answer would give them, one sentence.
- answered: true if a comment gives a specific, correct-looking answer the poster could act on.
- answer_quality: 0 none, 1 vague pointer, 2 usable answer, 3 complete answer with reasoning.
- vendor_present: {tool_label} whose representative or an obvious promoter comments in the thread ([] if none).
- vendor_accepted: true if a vendor comment is the accepted/top answer.

Return JSON matching the schema, one item per thread id, same ids as given, no extra ids.

THREADS
"""
DEFAULT_RELEVANCE = ("the thread describes a real problem in the company's domain; job posts, generic chatter and "
                     "passing asides are NOT relevant.")
PROMPT = ""


def configure(product: dict, providers: dict | None = None) -> None:
    """Build TOPICS, SCHEMA and PROMPT from a catalog's product block (see pulse.product_of)."""
    global TOPICS, SCHEMA, PROMPT
    TOPICS = [t for t in (product.get("topics") or []) if t != "other"] + ["other"]
    SCHEMA = _schema(TOPICS)
    examples = ", ".join(f'"{n}"' for n in list(providers or {})[:3]) or '"Vendor A", "Vendor B"'
    PROMPT = PROMPT_TEMPLATE.format(
        category=product.get("category") or "software tooling",
        relevance=" ".join((product.get("relevance") or DEFAULT_RELEVANCE).split()),
        topic_list=" | ".join(TOPICS), tool_label=product.get("tool_label") or "tools", examples=examples)


configure({})


def _clean(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def render_thread(rec: dict, op_chars: int = 1200, top: int = 6, comment_chars: int = 300) -> str:
    """Authorless view of one thread for the model: OP and the top comments by score, in time order."""
    op = rec.get("author")
    lines = [f"### id={rec['id']} sub=r/{rec['subreddit']} score={rec.get('score', 0)} comments={rec.get('num_comments_archived', 0)}",
             f"title: {_clean(rec.get('title', ''), 200)}",
             f"OP: {_clean(rec.get('selftext', ''), op_chars) or '(no body)'}"]
    comments = sorted(rec.get("comments", []), key=lambda c: (c.get("score") or 0), reverse=True)[:top]
    for c in sorted(comments, key=lambda c: c.get("created_utc", 0)):
        who = "OP" if c.get("author") and c.get("author") == op else "commenter"
        lines.append(f"- {who} (score {c.get('score', 0)}): {_clean(c.get('body', ''), comment_chars)}")
    return "\n".join(lines)


def _blank(tid: str, reason: str) -> dict:
    return {"id": tid, "relevant": False, "topic": "other", "problem": "", "stack": [], "tools_named": [],
            "tried": "", "wanted": "", "answered": False, "answer_quality": 0, "vendor_present": [],
            "vendor_accepted": False, "_error": reason}


def extract_batch(records: list[dict], backend: str | None, model: str | None, use_cache: bool) -> list[dict]:
    prompt = PROMPT + "\n\n".join(render_thread(r) for r in records)
    out = llm.complete(prompt, SCHEMA, backend=backend, model=model, use_cache=use_cache)
    by_id = {str(i.get("id")): i for i in out.get("items", []) if isinstance(i, dict)}
    result = []
    for r in records:
        item = by_id.get(r["id"])
        if item is None:
            result.append(_blank(r["id"], "missing from model output"))
            continue
        item["id"] = r["id"]
        if item.get("topic") not in TOPICS:
            item["topic"] = "other"
        result.append(item)
    return result


def cache_dir() -> Path:
    """Per-prompt subfolder: the prompt carries the product's relevance and topics, so products never share extractions."""
    return EXTRACT_CACHE / hashlib.sha256((PROMPT + json.dumps(SCHEMA, sort_keys=True)).encode()).hexdigest()[:12]


def run(run_dir: Path, batch: int, limit: int, backend: str | None, model: str | None, use_cache: bool) -> dict:
    records = [json.loads(l) for l in (run_dir / "threads.jsonl").read_text().splitlines() if l.strip()]
    if limit:
        records = records[:limit]
    cache = cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    done: dict[str, dict] = {}
    todo: list[dict] = []
    for r in records:
        f = cache / f"{r['id']}.json"
        if use_cache and f.exists():
            done[r["id"]] = json.loads(f.read_text())
        else:
            todo.append(r)
    calls = errors = 0
    t0 = time.time()
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        try:
            items = extract_batch(chunk, backend, model, use_cache)
            calls += 1
        except llm.LLMError as e:
            errors += 1
            print(f"batch {i // batch + 1}: {e}", file=sys.stderr)
            items = [_blank(r["id"], f"llm error: {e}") for r in chunk]
        for item in items:
            done[item["id"]] = item
            if "_error" not in item:
                (cache / f"{item['id']}.json").write_text(json.dumps(item, ensure_ascii=False))
        print(f"extract: {min(i + batch, len(todo))}/{len(todo)} new threads, {len(done)} total", file=sys.stderr)
    with (run_dir / "extract.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(done[r["id"]], ensure_ascii=False) + "\n")
    relevant = [d for d in done.values() if d.get("relevant")]
    topics = {t: sum(1 for d in relevant if d.get("topic") == t) for t in TOPICS}
    summary = {"threads": len(records), "relevant": len(relevant), "by_topic": topics,
               "answered": sum(1 for d in relevant if d.get("answered")),
               "vendor_present": sum(1 for d in relevant if d.get("vendor_present")),
               "llm_calls": calls, "llm_errors": errors, "from_cache": len(records) - len(todo),
               "seconds": round(time.time() - t0, 1), "backend": llm.pick_backend(backend),
               "model": model or os.environ.get("PAINPOINT_RADAR_LLM_MODEL") or ""}
    (run_dir / "extract.summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar extract", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="run folder containing threads.jsonl")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="only the first N threads (they are activity-sorted)")
    ap.add_argument("--backend", default=None, help="claude | codex (default: auto)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--catalog", default=str(pulse.DEFAULT_CATALOG), help="product catalog; sets the prompt and topics")
    a = ap.parse_args(argv)
    cat = pulse.load_catalog(a.catalog)
    configure(pulse.product_of(cat), cat.get("providers"))
    summary = run(Path(a.run), a.batch, a.limit, a.backend, a.model, not a.no_cache)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
