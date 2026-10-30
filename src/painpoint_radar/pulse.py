"""painpoint-radar: collect recently active public Reddit threads on a topic, for trend and
pain-point analysis. Reads only third-party archives (Arctic Shift, PullPush); never touches
reddit.com. Standard library only.

Commands
  collect   pull threads for subreddits + keywords inside a recency window, write a corpus
  thread    dump one thread (post + every comment) as Markdown, for a read-before-you-cite check
  ping      check both backends from this machine
  omakase   collect (or reuse a run), extract pain points, cluster, score provider sentiment,
            rank threads worth joining, diff against the previous run, write digest.md
  keywords  list, import (Ahrefs, Semrush, CSV) or seed (sitemap) a catalog's keyword sets

Single steps over an existing run folder (each takes --run runs/<stamp>; --help for flags)
  extract  cluster  sentiment  recommend  overlay  delta  digest  seed  llm

Environment
  PAINPOINT_RADAR_CONTACT   contact address sent in the User-Agent to the archives (set it)
  PAINPOINT_RADAR_PRODUCT   product used without --product (default example-product)
  PAINPOINT_RADAR_HOME      extra folder searched for catalogs/<product>.json
  PAINPOINT_RADAR_CACHE     cache root (default ~/.cache/painpoint-radar)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import __version__ as VERSION

CATALOGS_DIR = Path(__file__).resolve().parent / "catalogs"   # bundled examples; searched last
CACHE_ROOT = Path(os.environ.get("PAINPOINT_RADAR_CACHE", Path.home() / ".cache" / "painpoint-radar"))


def catalog_dirs() -> list[Path]:
    """Where catalogs/<product>.json is looked up, first match wins: ./catalogs, $PAINPOINT_RADAR_HOME/catalogs, bundled."""
    dirs = [Path.cwd() / "catalogs"]
    home = os.environ.get("PAINPOINT_RADAR_HOME", "").strip()
    if home:
        dirs.append(Path(home).expanduser() / "catalogs")
    dirs.append(CATALOGS_DIR)
    return list(dict.fromkeys(dirs))


def find_catalog(product: str) -> Path | None:
    for folder in catalog_dirs():
        path = folder / f"{product}.json"
        if path.is_file():
            return path
    return None


def default_product() -> str:
    """Product used when neither --product nor --catalog is given: $PAINPOINT_RADAR_PRODUCT, else example-product."""
    return os.environ.get("PAINPOINT_RADAR_PRODUCT", "").strip() or "example-product"


DEFAULT_CATALOG = find_catalog(default_product()) or CATALOGS_DIR / f"{default_product()}.json"
PRODUCT_DEFAULTS = {"name": "", "aliases": [], "site": "", "domain": "", "category": "", "tool_label": "tools",
                    "relevance": "", "topics": [], "contact": ""}


def user_agent(name: str = "", contact: str = "") -> str:
    """The archives ask for identifiable, considerate use: a contact comes from $PAINPOINT_RADAR_CONTACT or product.contact."""
    contact = os.environ.get("PAINPOINT_RADAR_CONTACT", "").strip() or contact.strip()
    return f"painpoint-radar/{VERSION} ({name + ' ' if name else ''}content research{'; contact ' + contact if contact else ''})"


USER_AGENT = user_agent()
CACHE_DIR = CACHE_ROOT / "http"
ARCTIC = "https://arctic-shift.photon-reddit.com"
PULLPUSH = "https://api.pullpush.io"
PAIN_RE = re.compile(
    r"\b(can'?t|cannot|couldn'?t|doesn'?t work|not working|won'?t|fail(?:s|ed|ing)?|error|"
    r"stuck|blocked|bounc(?:e|es|ed|ing)|spam|junk|rejected|timeout|help|why|broken|issue|problem|"
    r"frustrat\w*|annoy\w*|confus\w*|no idea|any idea|how do i|is it possible)\b",
    re.I,
)
GONE = {"[removed]", "[deleted]", ""}

# ----------------------------------------------------------------------------- helpers

def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", file=sys.stderr)


def parse_window(text: str) -> int:
    """'90d', '12h', '2w', '3m' (30-day months), '1y' -> seconds."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhdwy])\s*", text or "")
    if not m:
        raise argparse.ArgumentTypeError(f"bad window {text!r}; use e.g. 30d, 12h, 2w, 3m, 1y")
    n, unit = int(m.group(1)), m.group(2)
    mult = {"s": 1, "m": 30 * 86400, "h": 3600, "d": 86400, "w": 7 * 86400, "y": 365 * 86400}
    return n * mult[unit]


def iso(ts: int | float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%d %H:%M")


def clean_sub(name: str) -> str:
    return re.sub(r"^/?r/", "", name.strip())


def bare_id(thing: str) -> str:
    return re.sub(r"^t[13]_", "", thing)


def build_keyword_re(keywords: list[str]) -> re.Pattern | None:
    kws = [k.strip() for k in keywords if k.strip()]
    if not kws:
        return None
    parts = [r"\b" + re.escape(k).replace(r"\ ", r"\s+") + r"\b" for k in kws]
    return re.compile("|".join(parts), re.I)


def load_catalog(path: str | Path) -> dict:
    """Load local catalog data; a missing file permits keyword-only collection."""
    global USER_AGENT
    try:
        catalog = json.loads(Path(path).read_text())
    except FileNotFoundError:
        catalog = {}
    if "product" in catalog:
        product = catalog["product"]
        if not isinstance(product, dict):
            raise ValueError("product must be an object")
        if not isinstance(product.get("name"), str) or not product["name"].strip():
            raise ValueError("product.name is required and must be a nonempty string")
        keywords = product.get("keywords")
        if not isinstance(keywords, dict):
            raise ValueError("product.keywords.core is required")
        for label, owner, key in (("aliases", product, "aliases"), ("keywords.core", keywords, "core")):
            if not isinstance(owner.get(key), list) or not all(isinstance(v, str) for v in owner[key]):
                raise ValueError(f"product.{label} is required and must be a list of strings")
        for key, default in (("site", ""), ("domain", ""), ("category", ""), ("tool_label", "tools"),
                             ("relevance", ""), ("topics", []), ("contact", "")):
            product.setdefault(key, default)
            if not isinstance(product[key], type(default)):
                raise ValueError(f"product.{key} must be a {type(default).__name__}")
        keywords.setdefault("generic", [])
        keywords.setdefault("competitors", "providers")
        for label, values in (("topics", product["topics"]), ("keywords.generic", keywords["generic"])):
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError(f"product.{label} must be a list of strings")
        if keywords["competitors"] != "providers":
            raise ValueError('product.keywords.competitors must be "providers"')
        for entry in catalog.get("subreddits", {}).values():
            entry.setdefault("allow_generic", False)
            entry.setdefault("extra_keywords", [])
            entry.setdefault("comment_policy", "unknown")
    product = catalog.get("product", {})
    USER_AGENT = user_agent(product.get("name", ""), product.get("contact", ""))
    return catalog


def product_of(catalog: dict | None) -> dict:
    """The product block with every field present, for modules that may be handed an old catalog or none."""
    p = dict(PRODUCT_DEFAULTS)
    p.update((catalog or {}).get("product") or {})
    return p


def resolve_catalog_path(product: str | None, catalog: str | None) -> Path:
    """Resolve CLI catalog selection over catalog_dirs(), with explicit paths taking precedence."""
    if catalog is not None:
        return Path(catalog)
    if product is None:
        return find_catalog(default_product()) or DEFAULT_CATALOG
    path = find_catalog(product) if Path(product).name == product else None
    if path is None:
        dirs = catalog_dirs()
        present = ", ".join(sorted({p.stem for d in dirs for p in d.glob("*.json")})) or "(none)"
        raise ValueError(f"unknown product {product!r}: expected a catalog in {', '.join(map(str, dirs))}; "
                         f"available products: {present}")
    return path


def resolve_collect_args(args: argparse.Namespace, catalog: dict) -> None:
    """Apply preset values only where the caller omitted an explicit flag."""
    preset = {}
    if args.preset:
        if args.preset not in catalog.get("presets", {}):
            raise ValueError(f"unknown preset: {args.preset}")
        preset = catalog["presets"][args.preset]
    for key, default in (("subs", None), ("created_within", "90d"),
                         ("active_within", "30d"), ("min_hits", 1)):
        if getattr(args, key) is None:
            setattr(args, key, preset.get(key, default))
    if isinstance(args.subs, list):
        args.subs = ",".join(args.subs)
    if not args.subs:
        raise ValueError("collect requires --subs or a preset with subs")
    if args.min_hits < 1:
        raise ValueError("--min-hits must be at least 1")


def subreddit_terms(catalog: dict, sub: str, extra: str) -> list[str]:
    """Compose community keywords, gating broad provider aliases like other generic terms."""
    communities = {name.lower(): entry for name, entry in catalog.get("subreddits", {}).items()}
    entry = communities.get(clean_sub(sub).lower(), {})
    if "product" not in catalog:
        terms = list(entry.get("keywords", []))
    else:
        keywords = catalog["product"]["keywords"]
        generic = keywords.get("generic", [])
        terms = list(keywords["core"])
        if entry.get("allow_generic", False):
            terms += generic
        if keywords.get("competitors", "providers") == "providers":
            generic_set = {term.casefold() for term in generic}
            terms += [alias for aliases in catalog.get("providers", {}).values() for alias in aliases
                      if alias.casefold() not in generic_set]
        terms += entry.get("extra_keywords", [])
    terms += extra.split(",")
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))


def subreddit_keyword_re(catalog: dict, sub: str, keywords: str) -> re.Pattern | None:
    """Build the community pattern from its effective terms and CLI keywords."""
    return build_keyword_re(subreddit_terms(catalog, sub, keywords))


# ----------------------------------------------------------------------------- HTTP

class Http:
    """Polite GET with disk cache, pacing, and retry on 429 / 422-timeout / 5xx."""

    def __init__(self, pace: float = 0.5, cache_ttl: int = 6 * 3600, max_requests: int = 2000,
                 use_cache: bool = True, attempts: int = 6):
        self.pace, self.cache_ttl, self.max_requests, self.use_cache = pace, cache_ttl, max_requests, use_cache
        self.attempts = attempts
        self.requests = 0
        self.cache_hits = 0
        self._last = 0.0
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def get_json(self, url: str, params: dict) -> dict | list:
        qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        full = f"{url}?{qs}"
        key = CACHE_DIR / (hashlib.sha256(full.encode()).hexdigest() + ".json")
        if self.use_cache and key.exists() and time.time() - key.stat().st_mtime < self.cache_ttl:
            self.cache_hits += 1
            return json.loads(key.read_text())
        body = self._fetch(full)
        if self.use_cache:
            key.write_text(body)
        return json.loads(body)

    def _fetch(self, full: str) -> str:
        delay = 2.0
        for attempt in range(self.attempts):
            if self.requests >= self.max_requests:
                raise RuntimeError(f"request budget of {self.max_requests} exhausted")
            wait = self.pace - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            if self.requests == 0 and "contact" not in USER_AGENT:
                log("no contact in the User-Agent: set PAINPOINT_RADAR_CONTACT (or product.contact) so the archives can reach you")
            self.requests += 1
            req = urllib.request.Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")[:200]
                if e.code == 429:
                    reset = e.headers.get("X-RateLimit-Reset")
                    sleep_for = min(120, int(reset) + 1) if reset and reset.isdigit() else delay
                    log(f"429 from {urllib.parse.urlsplit(full).netloc}; sleeping {sleep_for}s")
                    time.sleep(sleep_for)
                elif e.code == 422 and "slow down" in text.lower():
                    log(f"422 server timeout; retry in {delay:.0f}s")
                    time.sleep(delay)
                elif 500 <= e.code < 600:
                    log(f"{e.code} from server; retry in {delay:.0f}s")
                    time.sleep(delay)
                else:
                    raise RuntimeError(f"HTTP {e.code} for {full}: {text}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                log(f"network error {e}; retry in {delay:.0f}s")
                time.sleep(delay)
            delay = min(delay * 2, 60)
        raise RuntimeError(f"gave up after {self.attempts} attempts: {full}")


# ----------------------------------------------------------------------------- backends

POST_FIELDS = "id,subreddit,title,selftext,author,created_utc,score,num_comments,link_flair_text,url"
COMMENT_FIELDS = "id,link_id,parent_id,author,created_utc,score,body"


class Arctic:
    name = "arctic"

    def __init__(self, http: Http):
        self.http = http

    def posts_window(self, sub: str, after: int, before: int | None = None):
        """Every post in r/sub created in [after, before], newest first, paginated."""
        cursor = before
        seen: set[str] = set()
        while True:
            d = self.http.get_json(f"{ARCTIC}/api/posts/search", {
                "subreddit": sub, "after": after, "before": cursor, "limit": "auto",
                "sort": "desc", "fields": POST_FIELDS})
            rows = d.get("data") or []
            if d.get("error"):
                raise RuntimeError(f"arctic error: {d['error']}")
            fresh = [r for r in rows if r["id"] not in seen]
            for r in fresh:
                seen.add(r["id"])
                yield r
            if not fresh:
                return
            oldest = min(r["created_utc"] for r in fresh)
            if oldest <= after:
                return
            cursor = oldest  # 'before' is exclusive enough; dedupe handles same-second overlap

    def comments_for(self, post_id: str) -> list[dict]:
        out: dict[str, dict] = {}
        cursor = None
        while True:
            d = self.http.get_json(f"{ARCTIC}/api/comments/search", {
                "link_id": post_id, "limit": 100, "sort": "asc", "after": cursor, "fields": COMMENT_FIELDS})
            rows = d.get("data") or []
            fresh = [r for r in rows if r["id"] not in out]
            for r in fresh:
                out[r["id"]] = r
            if len(rows) < 100 or not fresh:
                break
            cursor = max(r["created_utc"] for r in fresh)
        return sorted(out.values(), key=lambda r: r["created_utc"])

    def posts_by_ids(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 400):
            d = self.http.get_json(f"{ARCTIC}/api/posts/ids", {"ids": ",".join(ids[i:i + 400]), "fields": POST_FIELDS})
            out.extend(d.get("data") or [])
        return out


class PullPush:
    name = "pullpush"

    def __init__(self, http: Http):
        self.http = http

    def posts_window(self, sub: str, after: int, before: int | None = None):
        cursor = before
        seen: set[str] = set()
        while True:
            d = self.http.get_json(f"{PULLPUSH}/reddit/search/submission/", {
                "subreddit": sub, "after": after, "before": cursor, "size": 100, "sort": "desc"})
            rows = d.get("data") or []
            fresh = [r for r in rows if r["id"] not in seen]
            for r in fresh:
                seen.add(r["id"])
                yield r
            if not fresh:
                return
            oldest = min(r["created_utc"] for r in fresh)
            if oldest <= after:
                return
            cursor = oldest

    def comments_for(self, post_id: str) -> list[dict]:
        d = self.http.get_json(f"{PULLPUSH}/reddit/search/comment/", {"link_id": post_id, "size": 100})
        rows = d.get("data") or []
        return sorted(rows, key=lambda r: r["created_utc"])

    def posts_by_ids(self, ids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(ids), 100):
            d = self.http.get_json(f"{PULLPUSH}/reddit/search/submission/", {"ids": ",".join(ids[i:i + 100])})
            out.extend(d.get("data") or [])
        return out


def make_backend(name: str, http: Http):
    return {"arctic": Arctic, "pullpush": PullPush}[name](http)


# ----------------------------------------------------------------------------- selection

def thread_record(post: dict, comments: list[dict], now: int, active_after: int, kw: re.Pattern | None) -> dict:
    live = [c for c in comments if (c.get("body") or "").strip() not in GONE]
    last = max([post["created_utc"]] + [c["created_utc"] for c in live])
    recent = [c for c in live if c["created_utc"] >= active_after]
    text = f"{post.get('title', '')}\n{post.get('selftext') or ''}"
    kw_hits = len(kw.findall(text)) if kw else 0
    kw_hits_comments = sum(len(kw.findall(c.get("body") or "")) for c in live) if kw else 0
    pain = len(set(m.lower() for m in PAIN_RE.findall(text)))
    return {
        "id": post["id"],
        "subreddit": post.get("subreddit"),
        "title": post.get("title", ""),
        "flair": post.get("link_flair_text"),
        "created_utc": post["created_utc"],
        "last_activity_utc": last,
        "score": post.get("score", 0),
        "num_comments_reported": post.get("num_comments", 0),
        "num_comments_archived": len(live),
        "num_comments_in_window": len(recent),
        "keyword_hits_post": kw_hits,
        "keyword_hits_comments": kw_hits_comments,
        "pain_terms": pain,
        "activity_score": round(len(recent) + 0.5 * math.log1p(max(post.get("score", 0), 0)) + 0.25 * pain, 2),
        "permalink": f"https://www.reddit.com/r/{post.get('subreddit')}/comments/{post['id']}/",
        "author": post.get("author"),
        "selftext": post.get("selftext") or "",
        "comments": [{k: c.get(k) for k in ("id", "parent_id", "author", "created_utc", "score", "body")} for c in live],
    }


def passes(rec: dict, args, active_after: int, gate: bool = True) -> tuple[bool, str]:
    """`gate=False` skips the keyword-hit gate: a run with no keywords at all keeps every post."""
    if (rec["selftext"].strip() in GONE and rec["title"].strip() == ""):
        return False, "empty"
    if args.exclude_flair and (rec["flair"] or "").strip().lower() in args.exclude_flair:
        return False, f"flair:{rec['flair']}"
    if rec["last_activity_utc"] < active_after:
        return False, "inactive"
    if rec["num_comments_archived"] < args.min_comments:
        return False, "few-comments"
    if rec["score"] < args.min_score:
        return False, "low-score"
    if gate and getattr(args, "min_hits", None) and (rec["keyword_hits_post"] < 1 or
            rec["keyword_hits_post"] + rec["keyword_hits_comments"] < args.min_hits):
        return False, "few-hits"
    return True, "ok"


# ----------------------------------------------------------------------------- output

def trim(text: str, n: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def write_corpus(records: list[dict], out: Path, args, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "threads.jsonl").open("w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    lines = [f"# painpoint-radar corpus — {meta['collected_at']}", "",
             f"Subreddits: {', '.join('r/' + s for s in meta['subreddits'])} · keywords: {meta['keywords'] or '(none)'} · "
             f"created within {args.created_within}, active within {args.active_within} · {len(records)} threads · "
             f"backend {meta['backend']} · {meta['requests']} requests ({meta['cache_hits']} from cache)", "",
             "Authors are omitted here on purpose: this corpus is for trend and pain-point synthesis, not quotation. "
             "Use `painpoint-radar thread <id>` to read a thread in full, attributed, before joining it.", ""]
    for r in records:
        lines += [f"## [{r['id']}] r/{r['subreddit']} — {r['title']}",
                  f"created {iso(r['created_utc'])} · last activity {iso(r['last_activity_utc'])} · score {r['score']} · "
                  f"comments {r['num_comments_archived']} ({r['num_comments_in_window']} in window) · flair {r['flair'] or '-'} · "
                  f"activity {r['activity_score']} · pain terms {r['pain_terms']}",
                  f"<{r['permalink']}>", ""]
        if r["selftext"].strip() not in GONE:
            lines += ["**OP:** " + trim(r["selftext"], args.op_chars), ""]
        top = sorted(r["comments"], key=lambda c: (c.get("score") or 0), reverse=True)[: args.top_comments]
        for c in sorted(top, key=lambda c: c["created_utc"]):
            who = f" ({c['author']})" if args.with_authors else ""
            lines.append(f"- [{c['id']}{who} · {iso(c['created_utc'])} · {c.get('score', 0)}] {trim(c.get('body'), args.comment_chars)}")
        lines.append("")
    (out / "threads.md").write_text("\n".join(lines))
    (out / "summary.json").write_text(json.dumps(meta, indent=2))


def write_thread_md(rec: dict, path: Path) -> None:
    lines = [f"# r/{rec['subreddit']} `{rec['id']}` — {rec['title']}", "",
             f"Posted {iso(rec['created_utc'])} UTC by u/{rec['author']} · score {rec['score']} · "
             f"{rec['num_comments_archived']} archived comments · last activity {iso(rec['last_activity_utc'])} UTC",
             f"Permalink: <{rec['permalink']}>  (open it in your own browser before publishing anything: existence and deletion check)", "",
             "## OP", "", "> " + (rec["selftext"].replace("\n", "\n> ") if rec["selftext"] else "(no body / removed)"), "",
             "## Comments (chronological)", ""]
    for c in rec["comments"]:
        parent = "top-level" if str(c.get("parent_id", "")).startswith("t3_") else f"reply to {bare_id(c.get('parent_id') or '')}"
        lines += [f"**u/{c['author']}** — `{c['id']}` — {iso(c['created_utc'])} UTC — score {c.get('score', 0)} — {parent}",
                  "> " + (c.get("body") or "").replace("\n", "\n> "), ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


# ----------------------------------------------------------------------------- commands

def cmd_collect(args) -> int:
    catalog = load_catalog(args.catalog)
    resolve_collect_args(args, catalog)
    now = int(time.time())
    created_after = now - parse_window(args.created_within)
    active_after = now - parse_window(args.active_within)
    args.exclude_flair = {f.strip().lower() for f in args.exclude_flair.split(",") if f.strip()}
    http = Http(pace=args.pace, max_requests=args.max_requests, use_cache=not args.no_cache)
    backends = [args.backend] + (["pullpush"] if args.backend == "arctic" and not args.no_fallback else [])
    subs = [clean_sub(s) for s in args.subs.split(",") if s.strip()]
    out = Path(args.out) if args.out else Path("runs") / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")
    dropped: dict[str, int] = {}
    records: list[dict] = []
    used = None
    kept_by_sub = {sub: 0 for sub in subs}
    for sub in subs:
        kw = subreddit_keyword_re(catalog, sub, args.keywords)
        candidates = []
        for name in backends:
            be = make_backend(name, http)
            try:
                scanned = 0
                for p in be.posts_window(sub, created_after):
                    scanned += 1
                    text = f"{p.get('title', '')}\n{p.get('selftext') or ''}"
                    if kw is None or kw.search(text):
                        candidates.append(p)
                used = used or name
                log(f"r/{sub}: {scanned} posts scanned, {len(candidates)} match keywords ({name})")
                break
            except Exception as e:  # noqa: BLE001
                log(f"r/{sub}: backend {name} failed: {e}")
                candidates = []
        if args.limit_per_sub:
            candidates = sorted(candidates, key=lambda p: (p.get("num_comments", 0), p.get("score", 0)), reverse=True)[: args.limit_per_sub]
        be = make_backend(used or backends[0], http)
        for p in candidates:
            try:
                comments = be.comments_for(p["id"])
            except Exception as e:  # noqa: BLE001
                log(f"{p['id']}: comments failed ({e}); keeping post without comments")
                comments = []
            rec = thread_record(p, comments, now, active_after, kw)
            ok, why = passes(rec, args, active_after, gate=kw is not None)
            if ok:
                records.append(rec)
                kept_by_sub[sub] += 1
            else:
                dropped[why] = dropped.get(why, 0) + 1
    records.sort(key=lambda r: (r["activity_score"], r["last_activity_utc"]), reverse=True)
    meta = {"product": catalog.get("product", {}).get("name", ""), "version": VERSION, "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "subreddits": subs, "keywords": args.keywords, "created_within": args.created_within,
            "active_within": args.active_within, "exclude_flair": sorted(args.exclude_flair),
            "min_comments": args.min_comments, "min_score": args.min_score, "backend": used or "none",
            "threads_kept": len(records), "threads_dropped": dropped, "requests": http.requests,
            "cache_hits": http.cache_hits, "kept_by_sub": kept_by_sub,
            "min_hits": args.min_hits, "preset": args.preset}
    write_corpus(records, out, args, meta)
    log(f"kept {len(records)} threads, dropped {dropped}; {http.requests} requests, {http.cache_hits} cache hits -> {out}/")
    print(str(out))
    return 0



def cmd_omakase(args) -> int:
    """Chain the step modules over one run folder. Each step is its own CLI (python -m painpoint_radar.<step>); failures stop the chain."""
    import subprocess
    llm_flags = (["--backend", args.backend] if args.backend else []) + (["--model", args.model] if args.model else [])
    if args.run:
        out = Path(args.run)
    else:
        out = Path(args.out) if args.out else Path("runs") / datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M")
        keywords = [k for k in (args.keywords or "").split(",") if k.strip()]
        if args.site:
            from . import seed
            seeded = seed.site_keywords(args.site)
            log(f"site seeds from {args.site}: {', '.join(seeded)}")
            keywords += seeded
        argv = ["collect", "--out", str(out), "--catalog", args.catalog]
        if args.preset:
            argv += ["--preset", args.preset]
        if args.subs:
            argv += ["--subs", args.subs]
        if keywords:
            argv += ["--keywords", ",".join(keywords)]
        for flag, val in (("--created-within", args.created_within), ("--active-within", args.active_within),
                          ("--min-hits", args.min_hits)):
            if val is not None:
                argv += [flag, str(val)]
        if not args.preset and not args.subs:
            log("omakase needs --preset, --subs or --run")
            return 2
        rc = main(argv)
        if rc:
            return rc
    steps = [
        ("extract", ["--run", str(out), "--catalog", args.catalog, *llm_flags]),
        ("cluster", ["--run", str(out), "--max-clusters", str(args.max_clusters), "--catalog", args.catalog, *llm_flags]),
        ("sentiment", ["--run", str(out), "--catalog", args.catalog, *llm_flags]),
        ("recommend", ["--run", str(out), "--catalog", args.catalog]),
        ("overlay", ["--run", str(out), "--catalog", args.catalog]),
        ("delta", ["--run", str(out)]),
        ("digest", ["--run", str(out), "--top", str(args.top)]),
    ]
    for step, step_args in steps:
        if args.skip and step in args.skip:
            log(f"skip {step}")
            continue
        if step == "overlay" and not (out / "overlay").exists():
            log("overlay: no inputs in runs/<stamp>/overlay/ (SERP positions + AI citations); skipped, see SKILL.md step 3")
            continue
        log(f"run {step} " + " ".join(step_args))
        r = subprocess.run([sys.executable, "-m", f"{__package__}.{step}", *step_args])
        if r.returncode:
            log(f"{step} failed with exit {r.returncode}; stopping")
            return r.returncode
    print(str(out / "digest.md"))
    return 0

def cmd_thread(args) -> int:
    http = Http(pace=args.pace, use_cache=not args.no_cache)
    pid = bare_id(args.id)
    for name in [args.backend] + (["pullpush"] if args.backend == "arctic" else []):
        be = make_backend(name, http)
        try:
            posts = be.posts_by_ids([pid])
            if not posts:
                raise RuntimeError("post not in archive")
            comments = be.comments_for(pid)
            break
        except Exception as e:  # noqa: BLE001
            log(f"backend {name} failed: {e}")
            posts, comments = [], []
    if not posts:
        log("thread not found in any backend")
        return 2
    rec = thread_record(posts[0], comments, int(time.time()), 0, None)
    path = Path(args.out) if args.out else Path("runs") / "threads" / f"{pid}-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    write_thread_md(rec, path)
    (path.with_suffix(".json")).write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    log(f"{len(rec['comments'])} comments -> {path}")
    print(str(path))
    return 0


def cmd_ping(args) -> int:
    http = Http(pace=0.2, use_cache=False, attempts=1)  # a health check reports, it does not wait out rate limits
    rc = 0
    for name, url, params in [
        ("arctic", f"{ARCTIC}/api/posts/ids", {"ids": "atv15i", "fields": "id,num_comments"}),
        ("pullpush", f"{PULLPUSH}/reddit/search/submission/", {"subreddit": "webdev", "size": 1, "sort": "desc"}),
    ]:
        t = time.time()
        try:
            d = http.get_json(url, params)
            n = len(d.get("data") or [])
            print(f"{name:9s} OK   {time.time() - t:5.2f}s  rows={n}")
        except Exception as e:  # noqa: BLE001
            print(f"{name:9s} FAIL {e}")
            rc = 1
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=VERSION)
    sp = ap.add_subparsers(dest="cmd", required=True)
    from . import keywords
    keywords.add_parser(sp)

    c = sp.add_parser("collect", help="collect recently active threads into a corpus")
    c.add_argument("--preset", help="catalog preset name")
    c.add_argument("--min-hits", type=int, help="minimum post plus comment keyword hits (default 1)")
    c.add_argument("--subs", help="comma list, e.g. learnpython,PHPhelp,webdev")
    c.add_argument("--keywords", default="", help="comma list matched on title+body, word-bounded, case-insensitive; added to subreddit catalog terms")
    c.add_argument("--created-within", help="post age window (default 90d)")
    c.add_argument("--active-within", help="last comment must be inside this window (default 30d)")
    c.add_argument("--exclude-flair", default="solved,resolved", help="comma list of flairs to drop (default solved,resolved)")
    c.add_argument("--min-comments", type=int, default=1)
    c.add_argument("--min-score", type=int, default=0)
    c.add_argument("--limit-per-sub", type=int, default=0, help="only fetch comments for the N busiest candidates per sub (0 = all)")
    c.add_argument("--top-comments", type=int, default=6, help="comments per thread in threads.md")
    c.add_argument("--op-chars", type=int, default=1200)
    c.add_argument("--comment-chars", type=int, default=500)
    c.add_argument("--with-authors", action="store_true", help="include comment authors in threads.md (off by default)")
    c.add_argument("--backend", choices=["arctic", "pullpush"], default="arctic")
    c.add_argument("--no-fallback", action="store_true")
    c.add_argument("--pace", type=float, default=0.5, help="min seconds between requests (default 0.5)")
    c.add_argument("--max-requests", type=int, default=2000)
    c.add_argument("--no-cache", action="store_true")
    c.add_argument("--out", default="")
    c.set_defaults(fn=cmd_collect)

    o = sp.add_parser("omakase", help="the whole chain: collect -> extract -> cluster -> sentiment -> recommend -> overlay -> delta -> digest")
    o.add_argument("--preset", help="catalog preset name")
    o.add_argument("--site", nargs="?", const="", help="seed keywords from this site's sitemap (our own site only)")
    o.add_argument("--keywords", default="", help="extra comma-separated keywords")
    o.add_argument("--subs", help="comma list; overrides the preset's subs")
    o.add_argument("--created-within", default=None)
    o.add_argument("--active-within", default=None)
    o.add_argument("--min-hits", type=int, default=None)
    o.add_argument("--run", default="", help="reuse this run folder instead of collecting")
    o.add_argument("--out", default="")
    o.add_argument("--backend", default=None, help="LLM backend for extract/cluster/sentiment: claude | codex")
    o.add_argument("--model", default=None)
    o.add_argument("--max-clusters", type=int, default=12)
    o.add_argument("--top", type=int, default=15)
    o.add_argument("--skip", nargs="*", default=[], help="module stems to skip, e.g. --skip sentiment delta")
    o.set_defaults(fn=cmd_omakase)

    t = sp.add_parser("thread", help="dump one thread with every comment, attributed")
    t.add_argument("id", help="post id, e.g. atv15i or t3_atv15i")
    t.add_argument("--backend", choices=["arctic", "pullpush"], default="arctic")
    t.add_argument("--pace", type=float, default=0.5)
    t.add_argument("--no-cache", action="store_true")
    t.add_argument("--out", default="")
    t.set_defaults(fn=cmd_thread)

    p = sp.add_parser("ping", help="check both archives from this machine")
    p.set_defaults(fn=cmd_ping)

    for parser in (c, o, t, p):
        parser.add_argument("--product", help="product name: catalogs/<name>.json in ./catalogs, $PAINPOINT_RADAR_HOME or the bundled examples")
        parser.add_argument("--catalog", help=f"local catalog path (default {DEFAULT_CATALOG}); overrides --product")
    args = ap.parse_args(argv)
    try:
        args.catalog = str(resolve_catalog_path(args.product, args.catalog))
        catalog = load_catalog(args.catalog)
        if getattr(args, "site", None) == "":
            args.site = catalog.get("product", {}).get("site", "")
            if not args.site:
                raise ValueError("--site requires a URL or product.site in the catalog")
    except ValueError as e:
        ap.error(str(e))
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
