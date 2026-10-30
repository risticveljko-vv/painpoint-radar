"""digest: assemble runs/<stamp>/digest.md from the run's artefacts.

Sections: run facts · threads worth joining · pain-point themes · provider sentiment · delta ·
evidence index. Every number is threads, never comments. No usernames. Providers without a
cleared sentiment show the refusal reason instead of a number.

CLI
  painpoint-radar digest --run runs/<stamp> [--top 15]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .recommend import is_removed
from .overlay import google_cell, ai_cell


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "-"


def _trim(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def build(run: Path, top: int = 15) -> str:
    summary = _load(run / "summary.json") or {}
    ex = _load(run / "extract.summary.json") or {}
    clusters = (_load(run / "clusters.json") or {}).get("clusters", [])
    sentiment = _load(run / "sentiment.json") or {}
    delta = _load(run / "delta.json") or {}
    recs = _jsonl(run / "recommend.jsonl")
    threads = {t["id"]: t for t in _jsonl(run / "threads.jsonl")}
    overlay = _load(run / "overlay.json") or {}
    ov = {r["id"]: r for r in overlay.get("rows", [])}
    brand = summary.get("product") or overlay.get("product") or "own brand"
    cited: set[str] = set()
    L: list[str] = []

    L += [f"# Pain-point digest — {summary.get('preset') or 'custom'} — {summary.get('collected_at', '')[:10]}", "",
          "Trends and pain points only. Nothing here is a quote; run `painpoint-radar thread <id>` and open the permalink in a "
          "browser before quoting anyone. The tool recommends threads; a human decides whether to comment, under each "
          "subreddit's rules.", ""]

    # 1 run facts
    L += ["## 1. Run facts", ""]
    L += [f"- Subreddits: {', '.join('r/' + s for s in summary.get('subreddits', []))}",
          f"- Windows: created within {summary.get('created_within')}, active within {summary.get('active_within')}, "
          f"min keyword hits {summary.get('min_hits', 1)}",
          f"- Threads kept: {summary.get('threads_kept', 0)} (dropped: "
          + ", ".join(f"{k} {v}" for k, v in (summary.get('threads_dropped') or {}).items()) + ")",
          f"- Kept by subreddit: " + ", ".join(f"r/{k} {v}" for k, v in (summary.get('kept_by_sub') or {}).items()),
          f"- Requests to the archives: {summary.get('requests', 0)} · cost USD 0"]
    removed = [t for t in threads.values() if is_removed(t)]
    if removed:
        by_sub = Counter(t.get("subreddit") for t in removed)
        L.append(f"- Taken down on Reddit since archiving: {len(removed)} ("
                 + ", ".join(f"r/{k} {v}" for k, v in by_sub.most_common())
                 + ") · counted in themes, excluded from threads worth joining")
    if ex:
        L += [f"- Relevant after extraction: {ex.get('relevant', 0)} of {ex.get('threads', 0)} · by topic: "
              + ", ".join(f"{k} {v}" for k, v in (ex.get('by_topic') or {}).items() if v)
              + f" · answered {ex.get('answered', 0)} · vendor present {ex.get('vendor_present', 0)}"
              + f" · model {ex.get('backend')}/{ex.get('model') or 'default'}"]
    L.append("")

    # 2 threads worth joining
    L += [f"## 2. Threads worth joining (top {min(top, len(recs)) if recs else 0} by joinability index)", ""]
    if recs:
        extra = " Google | AI answers |" if ov else ""
        L += ["| # | J | sub | title | last activity | comments | policy | why |" + extra,
              "|---|---|---|---|---|---|---|---|" + ("---|---|" if ov else "")]
        for r in recs[:top]:
            cited.add(r["id"])
            o = ov.get(r["id"], {})
            tail = f" {google_cell(o)} | {ai_cell(o, brand)} |" if ov else ""
            L.append(f"| {r.get('rank', '')} | {r.get('J', 0):.2f} | r/{r.get('subreddit')} | [{_trim(r.get('title', ''), 70)}]({r.get('permalink')}) "
                     f"| {_day(r.get('last_activity_utc'))} | {r.get('num_comments', '')} | {r.get('comment_policy', 'unknown')} | {r.get('why', '')} |" + tail)
        L += ["", "J = 0.30·active + 0.25·OP engaged + 0.20·unanswered + 0.15·keyword density + 0.10·no vendor accepted. "
              "Policy `unknown` means nobody has read that subreddit's rules yet; `closed` means do not comment."]
    else:
        L.append("_recommend.jsonl missing; run recommend.py._")
    L.append("")

    # 2b overlay backlog
    if ov:
        c = overlay.get("counts", {})
        L += ["## 2b. Backlog: threads Google and AI answers already trust", "",
              f"Google: {overlay.get('serp', {}).get('keywords_with_reddit', 0)} of {overlay.get('serp', {}).get('keywords_total', 0)} "
              f"tracked keywords show a Reddit thread in the top 10. AI answers: {overlay.get('citations', {}).get('threads', 0)} Reddit "
              f"threads cited for the tracked prompts. Tier 1 (open here and trusted): {c.get('tier1', 0)} · tier 2 (trusted, not collected): "
              f"{c.get('tier2', 0)} · trusted but removed: {c.get('removed_but_trusted', 0)} · {brand} already named in {c.get('mentions_own', 0)}. "
              f"Tier 2 still active (a comment in the last 60 days): {c.get('tier2_active', 0)}. "
              "Full table with status and keyword/prompt views: `overlay.md`.", "",
              "| # | tier | state | sub | title | last activity | age | policy | Google | AI answers | status |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        shown = [r for r in overlay.get("rows", []) if r.get("tier") in (1, 2)][:top]
        for r in shown:
            cited.add(r["id"])
            L.append(f"| {r['rank']} | {r['tier']} | {r['state']} | r/{r['subreddit']} | [{_trim(r['title'], 60)}]({r['permalink']}) | {_day(r.get('last_activity_utc'))} "
                     f"| {r['age_days'] if r.get('age_days') is not None else '-'} d | {r.get('comment_policy', 'unknown')} | {google_cell(r)} | {ai_cell(r, brand)} | {r.get('status', 'new')} |")
        if not shown:
            L.append("_No thread in the run or the overlay inputs is ranking or cited yet._")
        L.append("")

    # 3 themes
    L += ["## 3. Pain-point themes (threads per theme)", ""]
    if clusters:
        for i, c in enumerate(clusters, 1):
            subs = ", ".join(f"r/{s} {n}" for s, n in list(c.get("subs", {}).items())[:4])
            tools = ", ".join(f"{t} {n}" for t, n in list(c.get("tools", {}).items())[:5]) or "none named"
            L += [f"### 3.{i} {c['label']}{'  ↑ rising' if c.get('rising') else ''}", "",
                  f"{c.get('description', '')}", "",
                  f"- Threads: {c['n']} · unanswered {c.get('unanswered', 0)} · created {_day(c.get('first_created'))} → {_day(c.get('last_created'))}"
                  f" · recent share {c.get('recent_share', 0):.0%}",
                  f"- Where: {subs}", f"- Tools named: {tools}",
                  f"- Evidence: " + ", ".join(f"`{t}`" for t in c.get("thread_ids", [])[:12]), ""]
            cited.update(c.get("thread_ids", []))
    else:
        L.append("_clusters.json missing or too few relevant threads._")
    L.append("")

    # 4 sentiment
    L += ["## 4. Provider sentiment (one vote per thread, two scorers)", ""]
    provs = sentiment.get("providers", {})
    if provs:
        rep = [(n, e) for n, e in provs.items() if e.get("reported")]
        if rep:
            L += ["| provider | threads | κ | net | 95% CI (bootstrap) | pos / neu / neg | VADER net |", "|---|---|---|---|---|---|---|"]
            for n, e in sorted(rep, key=lambda kv: -kv[1]["n_scored"]):
                a, v = e["llm"], e["vader"]
                L.append(f"| {n} | {e['n_scored']} | {e['kappa']:.2f} | {a['net']:+.2f} | [{a['net_ci'][0]:+.2f}, {a['net_ci'][1]:+.2f}] "
                         f"| {a['pos']} / {a['neu']} / {a['neg']} | {v['net']:+.2f} |")
                cited.update(e.get("thread_ids", []))
            L.append("")
        held = [(n, e) for n, e in provs.items() if not e.get("reported") and e.get("mentioning_threads")]
        if held:
            L += ["Not reported (below the bar): " + "; ".join(f"{n} ({e['reason']})" for n, e in sorted(held, key=lambda kv: -kv[1]['mentioning_threads'])), ""]
        L += [f"Method: sentences naming the provider → scorer A VADER compound mean (±0.05) and scorer B a 1–5 rubric → "
              f"pos/neu/neg per thread. Reported only when n ≥ {sentiment.get('min_n', 10)} and Cohen's κ ≥ {sentiment.get('min_kappa', 0.4)}. "
              "net = (pos − neg)/n on the rubric votes; CI from 2,000 bootstrap resamples."]
    else:
        L.append("_No providers in the catalog; sentiment not scored._" if sentiment else "_sentiment.json missing._")
    L.append("")

    # 5 delta
    L += ["## 5. Since the previous run", ""]
    if delta.get("previous_run"):
        t = delta.get("threads", {})
        L += [f"Previous run: `{delta['previous_run']}` ({(delta.get('previous_collected_at') or '')[:10]}). "
              f"Threads: {t.get('current')} now vs {t.get('previous')} then · new {t.get('new')} · gone {t.get('gone')} · shared {t.get('shared')}.", ""]
        cl = delta.get("clusters", {})
        if cl:
            L += ["| theme | now | then | Δ | matched by |", "|---|---|---|---|---|"]
            for r in cl.get("matched", []):
                L.append(f"| {r['label']} | {r['n']} | {r['previous_n'] if r['previous_n'] is not None else '–'} "
                         f"| {('%+d' % r['delta']) if r['delta'] is not None else 'new'} | {r.get('matched_by') or '–'} |")
            if cl.get("gone"):
                L.append("| " + "; ".join(f"{g['label']} ({g['previous_n']})" for g in cl["gone"]) + " | – | | gone | |")
            L.append("")
        pr = delta.get("providers", [])
        if pr:
            L += ["| provider | net now | net then | Δ |", "|---|---|---|---|"]
            for r in pr:
                L.append(f"| {r['provider']} | {r['net']:+.2f} | {('%+.2f' % r['previous_net']) if r['previous_net'] is not None else '–'} "
                         f"| {('%+.2f' % r['delta']) if r['delta'] is not None else r.get('note', '')} |")
            L.append("")
    else:
        L += [delta.get("note", "_delta.json missing._"), ""]

    # 6 evidence index
    L += ["## 6. Evidence index", ""]
    for tid in sorted(cited, key=lambda t: -threads.get(t, {}).get("activity_score", 0)):
        t = threads.get(tid)
        if t:
            L.append(f"- `{tid}` r/{t['subreddit']} — {_trim(t['title'], 90)} — {t['num_comments_archived']} comments, last {_day(t['last_activity_utc'])} — <{t['permalink']}>")
        elif tid in ov:
            o = ov[tid]
            L.append(f"- `{tid}` r/{o['subreddit']} — {_trim(o['title'], 90)} — not in this run ({o['state']}), last {_day(o.get('last_activity_utc'))} — <{o['permalink']}>")
    L.append("")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar digest", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--top", type=int, default=15)
    a = ap.parse_args(argv)
    run = Path(a.run)
    md = build(run, a.top)
    (run / "digest.md").write_text(md)
    print(str(run / "digest.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
