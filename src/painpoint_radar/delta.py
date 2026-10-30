"""delta: diff this run against the previous run of the same product and preset.

Reads  runs/<stamp>/{summary,clusters,sentiment}.json and threads.jsonl, and the same in the previous run
Writes runs/<stamp>/delta.json

The previous run is the latest other run folder whose summary.json has the same preset (or, without a
preset, the same subreddit set) and an earlier collected_at. Clusters are matched by label, then by
thread overlap (Jaccard >= 0.3). Provider deltas are only computed where both runs reported a number.

CLI
  painpoint-radar delta --run runs/<stamp> [--previous runs/<other>] [--runs-dir runs]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def _ids(run: Path) -> set[str]:
    f = run / "threads.jsonl"
    return {json.loads(l)["id"] for l in f.read_text().splitlines() if l.strip()} if f.exists() else set()


def legacy_product() -> str:
    """Runs from before summary.json carried `product` belong to the default product."""
    try:
        from . import pulse
        return pulse.product_of(pulse.load_catalog(pulse.DEFAULT_CATALOG))["name"]
    except Exception:  # noqa: BLE001
        return ""


def _key(s: dict, legacy: str) -> tuple:
    return (s.get("product") or legacy, s.get("preset") or tuple(sorted(s.get("subreddits", []))))


def find_previous(run: Path, runs_dir: Path, legacy: str | None = None) -> Path | None:
    """Latest earlier run of the same product and preset (or subreddit set)."""
    legacy = legacy_product() if legacy is None else legacy
    cur = _load(run / "summary.json") or {}
    key = _key(cur, legacy)
    best = None
    for other in runs_dir.iterdir():
        if not other.is_dir() or other.resolve() == run.resolve():
            continue
        s = _load(other / "summary.json")
        if not s or not (other / "threads.jsonl").exists():
            continue
        okey = _key(s, legacy)
        if okey != key or s.get("collected_at", "") >= cur.get("collected_at", ""):
            continue
        if best is None or s["collected_at"] > best[0]:
            best = (s["collected_at"], other)
    return best[1] if best else None


def match_clusters(cur: list[dict], prev: list[dict]) -> list[dict]:
    used, out = set(), []
    by_label = {c["label"].lower(): i for i, c in enumerate(prev)}
    for c in cur:
        j = by_label.get(c["label"].lower())
        how = "label"
        if j is None or j in used:
            j, how, best = None, "overlap", 0.0
            a = set(c.get("thread_ids", []))
            for i, p in enumerate(prev):
                if i in used:
                    continue
                b = set(p.get("thread_ids", []))
                jac = len(a & b) / len(a | b) if a | b else 0.0
                if jac >= 0.3 and jac > best:
                    best, j = jac, i
        row = {"label": c["label"], "n": c["n"], "previous_n": None, "delta": None, "matched_by": None, "previous_label": None}
        if j is not None:
            used.add(j)
            row.update(previous_n=prev[j]["n"], delta=c["n"] - prev[j]["n"], matched_by=how, previous_label=prev[j]["label"])
        out.append(row)
    gone = [{"label": p["label"], "previous_n": p["n"]} for i, p in enumerate(prev) if i not in used]
    return out, gone


def compare(run: Path, prev: Path) -> dict:
    cur_ids, prev_ids = _ids(run), _ids(prev)
    result = {"previous_run": str(prev), "previous_collected_at": (_load(prev / "summary.json") or {}).get("collected_at"),
              "threads": {"current": len(cur_ids), "previous": len(prev_ids), "new": len(cur_ids - prev_ids),
                          "gone": len(prev_ids - cur_ids), "shared": len(cur_ids & prev_ids)}}
    cc, pc = _load(run / "clusters.json"), _load(prev / "clusters.json")
    if cc and pc:
        matched, gone = match_clusters(cc.get("clusters", []), pc.get("clusters", []))
        result["clusters"] = {"matched": matched, "gone": gone}
    cs, ps = _load(run / "sentiment.json"), _load(prev / "sentiment.json")
    if cs and ps:
        rows = []
        for name, e in cs.get("providers", {}).items():
            p = ps.get("providers", {}).get(name)
            if not e.get("reported"):
                continue
            if p and p.get("reported"):
                rows.append({"provider": name, "net": e["llm"]["net"], "previous_net": p["llm"]["net"],
                             "delta": round(e["llm"]["net"] - p["llm"]["net"], 3), "n": e["n_scored"], "previous_n": p["n_scored"]})
            else:
                rows.append({"provider": name, "net": e["llm"]["net"], "previous_net": None, "delta": None,
                             "n": e["n_scored"], "previous_n": p["n_scored"] if p else 0, "note": "newly reported"})
        result["providers"] = rows
    return result


def run_delta(run: Path, previous: Path | None, runs_dir: Path) -> dict:
    prev = previous or find_previous(run, runs_dir)
    result = compare(run, prev) if prev else {"previous_run": None, "note": "no earlier run of this preset"}
    (run / "delta.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar delta", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True)
    ap.add_argument("--previous", default=None)
    ap.add_argument("--runs-dir", default="runs")
    a = ap.parse_args(argv)
    r = run_delta(Path(a.run), Path(a.previous) if a.previous else None, Path(a.runs_dir))
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
