"""overlay: join Google SERP and AI-answer citation evidence onto a run and build the Reddit backlog.

Why: a thread that already ranks in Google's top 10 for one of the product's keywords, or that AI answers already
cite, is worth more than a fresh one: one good comment there keeps working for months. This step marks those
threads in the run, adds the ones the run did not collect, and writes one backlog table to work from.

Inputs, all under runs/<stamp>/overlay/, written by an agent from a SERP source (e.g. Ahrefs) and an AI-citation
tracker (e.g. Peec) (see SKILL.md step 3) or by hand:
  serp.json       {"country", "fetched_at", "keywords": {kw: {"reddit": [{"position", "type", "url"}], "no_serp_data"?}}}
  citations.json  citation rows as columns/rows (Peec get_url_report shape), filtered to reddit.com, dimension prompt_id
  prompts.json    {"prompts": {prompt_id: text}}
  brands.json     {"own": [brand ids], "brands": {brand id: name}}   optional
Working-folder level: backlog_status.csv (id,status,owner,note,updated), hand-maintained, thread ids only.

Outputs: runs/<stamp>/overlay.json and overlay.md. Threads not in the run are looked up on Arctic Shift (one
request per 400 ids for title/subreddit/date, then one request per thread for comments so last activity is known;
--no-activity skips the second part). Never reddit.com. No usernames are written.

CLI
  painpoint-radar overlay --run runs/<stamp> [--catalog PATH] [--status backlog_status.csv] [--top 15]
                     [--no-activity] [--max-fetch 300] [--pace 0.5]
  painpoint-radar overlay keywords [--catalog PATH]      print the SERP keyword list for the agent to fetch
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .pulse import DEFAULT_CATALOG, Arctic, Http, load_catalog, product_of, thread_record, trim
from .recommend import REMOVED_TITLES, is_removed

THREAD_RE = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)/comments/([a-z0-9]+)", re.I)
STATUSES = ("new", "needs-comment", "commented", "growing", "skip")
OLD_DAYS = 180   # Reddit lets subreddits archive posts after six months (optional since 2023); age alone is not a lock signal
ACTIVE_DAYS = 60  # a comment inside this window proves the thread still accepts comments


def thread_ref(url: str) -> tuple[str, str] | None:
    """(id, subreddit) from any reddit.com thread or comment URL, else None."""
    m = THREAD_RE.search(url or "")
    return (m.group(2).lower(), m.group(1)) if m else None


def redact(text: str) -> str:
    return re.sub(r"(?i)(?:https?://(?:www\.)?reddit\.com/)?/?u/[\w-]+", "[redacted]", text or "")


def load_serp(path: Path) -> tuple[dict[str, dict], dict]:
    """Per thread: best Google position and the keywords it appears for. Returns (by_id, meta)."""
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text())
    by_id: dict[str, dict] = {}
    for keyword, entry in (data.get("keywords") or {}).items():
        seen: dict[str, dict] = {}
        for pos in entry.get("reddit") or []:
            ref = thread_ref(pos.get("url", ""))
            if not ref or not isinstance(pos.get("position"), int):
                continue
            tid, sub = ref
            kinds = pos.get("type") or []
            kinds = [kinds] if isinstance(kinds, str) else list(kinds)
            row = seen.setdefault(tid, {"keyword": keyword, "position": pos["position"], "kinds": set(), "subreddit": sub})
            row["position"] = min(row["position"], pos["position"])
            row["kinds"].update(kinds)
        for tid, row in seen.items():
            t = by_id.setdefault(tid, {"subreddit": row["subreddit"], "best_position": 99, "keywords": []})
            t["best_position"] = min(t["best_position"], row["position"])
            t["keywords"].append({"keyword": row["keyword"], "position": row["position"], "kinds": sorted(row["kinds"])})
    for t in by_id.values():
        t["keywords"].sort(key=lambda k: (k["position"], k["keyword"]))
    meta = {"country": data.get("country"), "fetched_at": data.get("fetched_at"),
            "keywords_total": len(data.get("keywords") or {}),
            "keywords_with_reddit": sum(1 for e in (data.get("keywords") or {}).values() if e.get("reddit")),
            "keywords_no_serp_data": sum(1 for e in (data.get("keywords") or {}).values() if e.get("no_serp_data"))}
    return by_id, meta


def load_citations(path: Path, prompts_path: Path | None = None, brands_path: Path | None = None,
                   own_aliases: list[str] | None = None) -> tuple[dict[str, dict], dict]:
    """Per thread: citations summed over prompts, prompt texts, whether our own brand is named on the page.

    Own brand = Peec brand ids in brands.json["own"]; without those, the product aliases matched on the page title."""
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text())
    prompts = (json.loads(prompts_path.read_text()).get("prompts") or {}) if prompts_path and prompts_path.exists() else {}
    brands, own = {}, set()
    if brands_path and brands_path.exists():
        b = json.loads(brands_path.read_text())
        brands, own = b.get("brands") or {}, set(b.get("own") or [])
    cols = data.get("columns") or []
    by_id: dict[str, dict] = {}
    for raw in data.get("rows") or []:
        row = dict(zip(cols, raw))
        ref = thread_ref(row.get("url", ""))
        if not ref:
            continue
        tid, sub = ref
        t = by_id.setdefault(tid, {"subreddit": sub, "title": row.get("title") or "", "citations": 0, "retrievals": 0,
                                   "prompts": [], "prompt_ids": [], "brands": set(), "mentions_own": False})
        t["citations"] += int(row.get("citation_count") or 0)
        t["retrievals"] += int(row.get("retrieval_count") or 0)
        pid = row.get("prompt_id")
        if isinstance(pid, dict):
            pid = pid.get("id")
        if pid and pid not in t["prompt_ids"]:
            t["prompt_ids"].append(pid)
            t["prompts"].append(prompts.get(pid, pid))
        for bid in row.get("mentioned_brand_ids") or []:
            t["brands"].add(brands.get(bid, bid))
            if bid in own:
                t["mentions_own"] = True
    alias_rx = None
    if not own and own_aliases:
        alias_rx = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(a) for a in own_aliases) + r")(?![\w-])", re.I)
    for t in by_id.values():
        t["brands"] = sorted(t["brands"])
        if alias_rx is not None and alias_rx.search(t.get("title") or ""):
            t["mentions_own"] = True
    meta = {"rows": len(data.get("rows") or []), "threads": len(by_id), "prompts_known": len(prompts), "own_brand_known": bool(own),
            "own_match": "peec" if own else ("alias" if alias_rx is not None else "none")}
    return by_id, meta


def load_status(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        rows = {r["id"].strip(): r for r in csv.DictReader(f) if r.get("id", "").strip()}
    for tid, r in rows.items():
        if r.get("status") and r["status"] not in STATUSES:
            print(f"overlay: backlog_status.csv: {tid} has status {r['status']!r}, expected one of {', '.join(STATUSES)}", file=sys.stderr)
    return rows


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def build(run: Path, catalog: dict | None = None, *, status: dict[str, dict] | None = None, now: float | None = None,
          lookup=None, activity=None, max_fetch: int = 300, top_run_only: int = 15) -> dict:
    """Assemble the backlog. lookup(ids) -> archive posts; activity(post) -> comments; either may be None (offline)."""
    now = time.time() if now is None else now
    serp, serp_meta = load_serp(run / "overlay" / "serp.json")
    product = product_of(catalog)
    cites, cite_meta = load_citations(run / "overlay" / "citations.json", run / "overlay" / "prompts.json", run / "overlay" / "brands.json",
                                      own_aliases=product["aliases"] or ([product["name"]] if product["name"] else None))
    status = status or {}
    subs = {k.lower(): v for k, v in (catalog or {}).get("subreddits", {}).items()}
    threads = {t["id"]: t for t in _jsonl(run / "threads.jsonl")}
    recs = {r["id"]: r for r in _jsonl(run / "recommend.jsonl")}
    extracts = {e["id"]: e for e in _jsonl(run / "extract.jsonl")}

    ids = set(threads) | set(serp) | set(cites)
    missing = sorted(i for i in ids if i not in threads and (i in serp or i in cites))
    fetched: dict[str, dict] = {}
    fetch_note = "offline"
    if lookup and missing:
        posts = lookup(missing[:max_fetch])
        for post in posts:
            comments = activity(post) if activity else []
            rec = thread_record(post, comments, int(now), 0, None)
            if not activity:
                rec["last_activity_utc"] = None
                rec["num_comments_archived"] = post.get("num_comments")
            fetched[rec["id"]] = rec
        fetch_note = f"{len(fetched)} of {len(missing)} looked up on Arctic Shift" + ("" if activity else " (metadata only)")

    rows = []
    for tid in ids:
        t = threads.get(tid) or fetched.get(tid) or {}
        in_run = tid in threads
        g, a = serp.get(tid), cites.get(tid)
        if not in_run and not g and not a:
            continue
        rec = recs.get(tid) or {}
        ex = extracts.get(tid) or {}
        if in_run and ex.get("relevant") is False:
            in_run = False  # collected but judged off-topic: keep only if Google or AI trust it
            if not g and not a:
                continue
        sub = t.get("subreddit") or (g or a or {}).get("subreddit") or ""
        title = t.get("title") or (a or {}).get("title") or ""
        removed = bool(t) and is_removed(t)
        created = t.get("created_utc")
        age_days = int((now - created) / 86400) if created else None
        last = t.get("last_activity_utc")
        if removed:
            state = "removed"
        elif in_run:
            state = "open"
        elif last and now - last <= ACTIVE_DAYS * 86400:
            state = "active"
        elif not t:
            state = "unknown"
        elif age_days is not None and age_days > OLD_DAYS:
            state = "old"
        else:
            state = "quiet"
        if in_run and not removed and (g or a):
            tier = 1
        elif (g or a) and not removed:
            tier = 2
        elif in_run and not removed:
            tier = 3
        else:
            tier = 4
        rows.append({
            "id": tid, "subreddit": sub, "title": redact(title), "permalink": f"https://www.reddit.com/r/{sub}/comments/{tid}/",
            "tier": tier, "state": state, "in_run": tid in threads, "removed": removed, "created_utc": created, "age_days": age_days,
            "last_activity_utc": last, "num_comments": t.get("num_comments_archived"),
            "J": rec.get("J"), "joinability_rank": rec.get("rank") or None,
            "comment_policy": subs.get(sub.lower(), {}).get("comment_policy", "unknown"),
            "google": g and {"best_position": g["best_position"], "keywords": g["keywords"]},
            "ai": a and {"citations": a["citations"], "retrievals": a["retrievals"], "prompts": a["prompts"],
                         "brands": a["brands"], "mentions_own": a["mentions_own"]},
            "status": (status.get(tid) or {}).get("status") or "new",
            "owner": (status.get(tid) or {}).get("owner") or "", "note": (status.get(tid) or {}).get("note") or "",
        })
    rows.sort(key=lambda r: (r["tier"], r["state"] not in ("open", "active"), -((r["ai"] or {}).get("citations") or 0),
                             (r["google"] or {}).get("best_position", 99), -(r["J"] or 0), r["id"]))
    tier3 = [r for r in rows if r["tier"] == 3][:top_run_only]
    rows = [r for r in rows if r["tier"] in (1, 2)] + tier3 + [r for r in rows if r["tier"] == 4 and (r["google"] or r["ai"])]
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"generated_at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "product": product["name"], "serp": serp_meta, "citations": cite_meta, "lookup": fetch_note,
            "counts": {"tier1": sum(r["tier"] == 1 for r in rows), "tier2": sum(r["tier"] == 2 for r in rows),
                       "tier2_active": sum(r["tier"] == 2 and r["state"] == "active" for r in rows),
                       "tier3": len(tier3), "removed_but_trusted": sum(r["tier"] == 4 for r in rows),
                       "mentions_own": sum(1 for r in rows if (r["ai"] or {}).get("mentions_own"))},
            "rows": rows}


def _day(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "-"


def cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def google_cell(r: dict) -> str:
    g = r.get("google")
    if not g:
        return ""
    kws = "; ".join(f"{k['keyword']} #{k['position']}" for k in g["keywords"][:3])
    more = f" +{len(g['keywords']) - 3}" if len(g["keywords"]) > 3 else ""
    return f"#{g['best_position']}: {kws}{more}"


def ai_cell(r: dict, brand: str = "own brand") -> str:
    a = r.get("ai")
    if not a:
        return ""
    own = f" · **{brand} named**" if a.get("mentions_own") else ""
    ps = "; ".join(a["prompts"][:2]) + (f" +{len(a['prompts']) - 2}" if len(a["prompts"]) > 2 else "")
    return f"{a['citations']} citations ({ps}){own}"


def render(result: dict, top: int = 40) -> str:
    rows = result["rows"]
    brand = result.get("product") or "own brand"
    L = ["# Reddit backlog — threads Google and AI answers already trust", "",
         f"Generated {result['generated_at']}. Google: {result['serp'].get('keywords_with_reddit', 0)} of "
         f"{result['serp'].get('keywords_total', 0)} keywords put a Reddit thread in the top 10 "
         f"({result['serp'].get('keywords_no_serp_data', 0)} had no SERP data). AI answers: "
         f"{result['citations'].get('threads', 0)} Reddit threads cited across the tracked prompts. "
         f"Lookup of threads outside the run: {result['lookup']}.", "",
         "Tiers: **1** open in this run and already ranking or cited · **2** ranking or cited but not collected · "
         "**3** open in this run, not yet trusted (top by joinability) · **4** trusted but taken down on Reddit. "
         f"State: `open` in this run · `active` a comment in the last {ACTIVE_DAYS} days · `quiet` under {OLD_DAYS} days old, no "
         f"recent comment · `old` over {OLD_DAYS} days and no recent comment (may be archived: open it first) · `unknown` not looked up. "
         "Active threads sort first inside a tier, then AI citations, then Google position. Status comes from "
         "`backlog_status.csv`; edit that file, not this one. The tool never posts.", "",
         "| # | tier | state | sub | title | last activity | age | J | policy | Google | AI answers | status | owner |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows[:top]:
        L.append(f"| {r['rank']} | {r['tier']} | {r['state']} | r/{cell(r['subreddit'])} | [{cell(trim(r['title'], 70))}]({r['permalink']}) "
                 f"| {_day(r['last_activity_utc'])} | {r['age_days'] if r['age_days'] is not None else '-'} d "
                 f"| {r['J'] if r['J'] is not None else '-'} | {r['comment_policy']} | {cell(google_cell(r))} | {cell(ai_cell(r, brand))} "
                 f"| {r['status']} | {cell(r['owner'])} |")
    if len(rows) > top:
        L.append(f"\n_{len(rows) - top} more rows in overlay.json._")
    L += ["", "## Keyword view (Google top 10, Reddit threads only)", ""]
    by_kw: dict[str, list] = defaultdict(list)
    for r in rows:
        for k in (r.get("google") or {}).get("keywords", []):
            by_kw[k["keyword"]].append((k["position"], r))
    for kw in sorted(by_kw):
        items = sorted(by_kw[kw], key=lambda x: x[0])
        L.append(f"- **{kw}**: " + "; ".join(f"#{p} `{r['id']}` r/{r['subreddit']} ({r['state']})" for p, r in items))
    L += ["", "## Prompt view (AI answers citing Reddit)", ""]
    by_p: dict[str, list] = defaultdict(list)
    for r in rows:
        for p in (r.get("ai") or {}).get("prompts", []):
            by_p[p].append(r)
    for p in sorted(by_p, key=lambda k: -len(by_p[k]))[:40]:
        L.append(f"- **{p}**: " + "; ".join(f"`{r['id']}` r/{r['subreddit']} ({r['ai']['citations']}{'*' if r['ai']['mentions_own'] else ''})"
                                           for r in sorted(by_p[p], key=lambda r: -r['ai']['citations'])[:6]))
    L += ["", f"`*` = {brand} already named on that page (Peec brand match, or the product aliases in the page title).", ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar overlay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keywords", nargs="?", help="print the SERP keyword list from the catalog and exit")
    ap.add_argument("--run")
    ap.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    ap.add_argument("--status", default="backlog_status.csv")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--no-activity", action="store_true", help="skip per-thread comment fetch for threads outside the run")
    ap.add_argument("--offline", action="store_true", help="no archive requests at all")
    ap.add_argument("--max-fetch", type=int, default=300)
    ap.add_argument("--pace", type=float, default=0.5)
    a = ap.parse_args(argv)
    catalog = load_catalog(a.catalog)
    if a.keywords == "keywords":
        for group, kws in (catalog.get("serp_keywords") or {}).items():
            if group.startswith("_") or not isinstance(kws, list):
                continue
            for kw in kws:
                print(f"{group}\t{kw}")
        return 0
    if not a.run:
        ap.error("--run is required")
    run = Path(a.run)
    if not (run / "overlay").exists():
        print(f"overlay: no inputs at {run / 'overlay'}; see SKILL.md step 3", file=sys.stderr)
        return 2
    lookup = activity = None
    if not a.offline:
        http = Http(pace=a.pace)
        arctic = Arctic(http)
        lookup = arctic.posts_by_ids
        if not a.no_activity:
            activity = lambda post: arctic.comments_for(post["id"])  # noqa: E731
    result = build(run, catalog, status=load_status(Path(a.status)), lookup=lookup, activity=activity, max_fetch=a.max_fetch)
    (run / "overlay.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    (run / "overlay.md").write_text(render(result, a.top))
    c = result["counts"]
    print(f"overlay: tier1 {c['tier1']} · tier2 {c['tier2']} ({c['tier2_active']} active) · tier3 {c['tier3']} · removed-but-trusted {c['removed_but_trusted']} "
          f"· {result.get('product') or 'own brand'} named in {c['mentions_own']} cited threads -> {run / 'overlay.md'}", file=sys.stderr)
    print(str(run / "overlay.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
