"""Rank local threads by joinability without exporting usernames.

CLI: painpoint-radar recommend --run runs/<stamp> [--catalog PATH]
     [--active-within 30d] [--op-window 14d] [--answer-threshold 10]
     [--density-cap 6] [--top 25]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from .pulse import DEFAULT_CATALOG, load_catalog, parse_window, trim


# Titles the archives return once Reddit has taken the post down. The body and comments survive in the archive
# (so themes still count them) but nobody can join the thread, so the recommender skips them.
REMOVED_TITLES = {"[ removed by moderator ]", "[removed]", "[deleted]", "[ removed by reddit ]"}


def is_removed(thread: dict) -> bool:
    return (thread.get("title") or "").strip().lower() in REMOVED_TITLES


def clamp(value: float) -> float:
    return max(0.0, min(value, 1.0))


def components(thread: dict, extract: dict | None = None, *, now: float,
               active_within: float = 30 * 86400, op_window: float = 14 * 86400,
               answer_threshold: float = 10, density_cap: float = 6) -> dict[str, float]:
    """Compute the five components; windows are seconds, endpoints inclusive."""
    for value in (active_within, op_window, answer_threshold, density_cap):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("windows, answer threshold and density cap must be positive and finite")
    extract = extract or {}
    comments = thread.get("comments", [])
    author = thread.get("author")
    op_comments = [c for c in comments if author and author not in ("[deleted]", "[removed]")
                   and c.get("author") == author]
    recent = any(c.get("created_utc") is not None
                 and 0 <= now - c["created_utc"] <= op_window for c in op_comments)
    max_score = max((c.get("score") or 0 for c in comments), default=0)
    return {
        "active": 1 - clamp((now - thread["last_activity_utc"]) / active_within),
        "op_engaged": 1.0 if recent else 0.5 if op_comments else 0.0,
        "unanswered": 0.0 if extract.get("answered", False) else 1 - clamp(max_score / answer_threshold),
        "density": clamp((thread.get("keyword_hits_post", 0) + thread.get("keyword_hits_comments", 0)) / density_cap),
        "no_vendor": 0.0 if extract.get("vendor_accepted", False) else 1.0,
    }


def recommend(threads: list[dict], extracts: list[dict] | None = None,
              catalog: dict | None = None, *, now: float | None = None,
              active_within: float = 30 * 86400, op_window: float = 14 * 86400,
              answer_threshold: float = 10, density_cap: float = 6, top: int = 25) -> list[dict]:
    """Return export-safe rows, sorting closed communities last, then descending J.

    Threads whose title is a removal placeholder are skipped (see REMOVED_TITLES). Equal scores retain corpus order. Missing extraction IDs use the same fallback
    as a missing extraction file. Comments means the archived count (what the corpus holds).
    """
    if top < 0:
        raise ValueError("top must be nonnegative")
    now = time.time() if now is None else now
    by_id = {row["id"]: row for row in extracts or []}
    subs = {key.lower(): value for key, value in (catalog or {}).get("subreddits", {}).items()}
    names = {str(c["author"]) for t in threads for c in [t, *t.get("comments", [])]
             if c.get("author") and c["author"] not in ("[deleted]", "[removed]")}
    names_re = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                          + r")(?!\w)", re.I) if names else None

    def redact(text: str) -> str:
        text = re.sub(r"(?i)(?:https?://(?:www\.)?reddit\.com/)?/?u/[\w-]+", "[redacted]", text)
        return names_re.sub("[redacted]", text) if names_re else text

    rows = []
    for thread in threads:
        extract = by_id.get(thread["id"], {})
        if extract.get("relevant") is False or is_removed(thread):
            continue
        values = components(thread, extract, now=now, active_within=active_within,
                            op_window=op_window, answer_threshold=answer_threshold, density_cap=density_cap)
        score = sum(values[key] * weight for key, weight in
                    (("active", .30), ("op_engaged", .25), ("unanswered", .20), ("density", .15), ("no_vendor", .10)))
        policy = subs.get(thread["subreddit"].lower(), {}).get("comment_policy", "unknown")
        why = " · ".join(f"{label} {values[key]:.3g}" for label, key in
                         (("act", "active"), ("op", "op_engaged"), ("unans", "unanswered"),
                          ("dens", "density"), ("vendor", "no_vendor")))
        if policy == "closed":
            why += " · DO NOT COMMENT (sub rules)"
        rows.append({"rank": 0, "id": thread["id"], "J": score, "components": values,
                     "subreddit": thread["subreddit"], "title": redact(thread["title"]),
                     "last_activity_utc": thread["last_activity_utc"],
                     "num_comments": thread.get("num_comments_archived", 0),
                     "comment_policy": policy, "why": why, "permalink": thread["permalink"]})
    rows.sort(key=lambda row: (row["comment_policy"] == "closed", -row["J"]))
    rows = rows[:top]
    for rank, row in enumerate(rows, 1):
        row.update(rank=rank, J=round(row["J"], 3))
    return rows


def read_jsonl(path: Path, *, optional: bool = False) -> list[dict]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        if optional:
            return []
        raise


def write_recommendations(run: Path, rows: list[dict]) -> None:
    def cell(value: object) -> str:
        return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\\", "&#92;").replace("|", "&#124;").replace("\n", " ").replace("\r", " ")

    lines = ["# Threads worth joining", "",
             "| rank | J | r/sub | title | last activity (UTC) | comments | policy | why | permalink |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        date = datetime.fromtimestamp(row["last_activity_utc"], timezone.utc).strftime("%Y-%m-%d")
        fields = [row["rank"], f'{row["J"]:.3f}', "r/" + row["subreddit"], trim(row["title"], 80),
                  date, row["num_comments"], row["comment_policy"], row["why"], row["permalink"]]
        lines.append("| " + " | ".join(cell(field) for field in fields) + " |")
    (run / "recommend.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    (run / "recommend.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None, *, now: float | None = None) -> int:
    parser = argparse.ArgumentParser(prog="painpoint-radar recommend", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG, type=Path)
    parser.add_argument("--active-within", default="30d", type=parse_window)
    parser.add_argument("--op-window", default="14d", type=parse_window)
    parser.add_argument("--answer-threshold", default=10, type=float)
    parser.add_argument("--density-cap", default=6, type=float)
    parser.add_argument("--top", default=25, type=int)
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v <= 0 for v in
           (args.active_within, args.op_window, args.answer_threshold, args.density_cap)) or args.top < 0:
        parser.error("windows, threshold and cap must be positive and finite; top must be nonnegative")
    rows = recommend(read_jsonl(args.run / "threads.jsonl"),
                     read_jsonl(args.run / "extract.jsonl", optional=True), load_catalog(args.catalog),
                     now=now, active_within=args.active_within, op_window=args.op_window,
                     answer_threshold=args.answer_threshold, density_cap=args.density_cap, top=args.top)
    write_recommendations(args.run, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
