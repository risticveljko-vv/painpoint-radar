"""seed: derive keyword seeds from a website's sitemap, for `painpoint-radar omakase --site`.

Fetches <site>/sitemap.xml (following a sitemap index, up to 10 child maps), takes the last path
segment of every URL, splits it on hyphens, and returns the most frequent tokens and adjacent
bigrams. Crude by design: it is a starting list to review, not a topic model. Only the product's own site
is fetched; never reddit.com.

CLI
  painpoint-radar seed https://example.com [--max-terms 25] [--min-count 3]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from urllib.parse import urlparse

STOP = set("""the and for with from your you our this that into how what why when best free top guide
tutorial vs versus using use new blog page pages home about index html php news update updates review
reviews list lists tips tricks ways step steps make made create get set up start ultimate complete full
2020 2021 2022 2023 2024 2025 2026 part one two three""".split())
LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def fetch(url: str, timeout: int = 20) -> str:
    from .pulse import USER_AGENT
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def collect_urls(site: str, max_children: int = 10) -> list[str]:
    base = site.rstrip("/")
    xml = fetch(base + "/sitemap.xml")
    locs = LOC.findall(xml)
    if "<sitemapindex" in xml.lower():
        urls: list[str] = []
        for child in locs[:max_children]:
            try:
                urls += LOC.findall(fetch(child))
            except Exception:  # noqa: BLE001
                continue
        return urls
    return locs


def tokens_from_urls(urls: list[str]) -> tuple[Counter, Counter]:
    uni, bi = Counter(), Counter()
    for u in urls:
        slug = urlparse(u).path.rstrip("/").rsplit("/", 1)[-1].lower()
        parts = [p for p in slug.split("-") if len(p) >= 3 and not p.isdigit() and p not in STOP]
        uni.update(set(parts))
        bi.update({f"{a} {b}" for a, b in zip(parts, parts[1:])})
    return uni, bi


def site_keywords(site: str, max_terms: int = 25, min_count: int = 3) -> list[str]:
    uni, bi = tokens_from_urls(collect_urls(site))
    terms = [t for t, n in bi.most_common() if n >= min_count][: max_terms // 2]
    terms += [t for t, n in uni.most_common() if n >= min_count and t not in " ".join(terms)][: max_terms - len(terms)]
    return terms


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="painpoint-radar seed", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("site")
    ap.add_argument("--max-terms", type=int, default=25)
    ap.add_argument("--min-count", type=int, default=3)
    a = ap.parse_args(argv)
    print(json.dumps(site_keywords(a.site, a.max_terms, a.min_count)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
