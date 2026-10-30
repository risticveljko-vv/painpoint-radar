# Architecture

How painpoint-radar is put together: the rules every module follows, the files that pass between steps, and the
formulas behind the numbers in the digest.

## House rules

1. **Never touch reddit.com or old.reddit.com.** Archives only (Arctic Shift, PullPush). The one other site the tool
   fetches is the product's own sitemap, for `--site` seeding.
2. **Standard library only** in every module except `sentiment.py`, which imports `vaderSentiment` when the
   `sentiment` extra is installed and degrades without it.
3. **No usernames leave the machine.** Every Markdown output and `summary.json`, `recommend.*`, `overlay.*`,
   `digest.md` carry no usernames. `threads.jsonl` keeps them (needed to tell OP from commenters) and stays local.
   Prompts sent to a model replace authors with "OP" / "commenter".
4. **Count threads, never comments.** One thread is one vote, in themes and in sentiment.
5. **Offline tests for every module.** `python3 -m unittest discover -s tests`; no network in tests.
6. **No product name in code.** Everything product-specific comes from the catalog. A test checks the package for
   every product name and alias found in the bundled and test catalogs.
7. **A run records its product.** `delta` never pairs runs of different products.
8. **Anything that adds keywords prints the exact list first** and writes nothing with `--dry-run`.

## Package layout

`painpoint_radar.cli:main` is the `painpoint-radar` command. Pipeline commands (`collect`, `thread`, `ping`,
`omakase`, `keywords`) are parsed in `pulse.py`; the step commands (`extract`, `cluster`, `sentiment`, `recommend`,
`overlay`, `delta`, `digest`, `seed`, `llm`) are routed to each module's own `main(argv)`. `omakase` runs the steps
as `python -m painpoint_radar.<step>` subprocesses over one run folder and stops at the first failure.

Paths:

- Catalogs: `./catalogs/<product>.json` → `$PAINPOINT_RADAR_HOME/catalogs/` → bundled `painpoint_radar/catalogs/`.
- Runs: `./runs/<UTC stamp>/` (or `--out`).
- Caches: `~/.cache/painpoint-radar/` (`$PAINPOINT_RADAR_CACHE`): `http/` (archive responses, six-hour TTL),
  `llm/` (model outputs by backend, model, schema and prompt), `extract/<prompt hash>/<thread id>.json`.

## Catalog schema

One file per product.

```
{"product": {
   "name": "Queuely",                     label used in overlay.md, digest.md and log lines
   "aliases": ["queuely"],                own-brand match (lowercase, word-bounded)
   "site": "https://example.com",         default for --site seeding; the only site the tool ever fetches
   "contact": "",                         User-Agent contact ($PAINPOINT_RADAR_CONTACT wins)
   "domain": "background jobs",           short noun for the cluster prompt
   "category": "...",                     extractor prompt wording
   "tool_label": "job queue libraries/services",
   "relevance": "the thread is about ... X, Y are NOT relevant.",
   "topics": ["reliability", "scaling"],  extractor enum; "other" is always appended
   "keywords": {"core": [...],            matched on every subreddit
                "generic": [...],         matched only where allow_generic is true
                "competitors": "providers"}},
 "subreddits": {"<name>": {"allow_generic": false, "extra_keywords": [], "comment_policy": "open|cautious|closed|unknown",
                           "policy_note": "", "policy_url": "https://www.reddit.com/r/<name>/about/rules", "audience": ""}},
 "presets": {"<name>": {"subs": [...], "created_within": "90d", "active_within": "30d", "min_hits": 2}},
 "providers": {"Display Name": ["alias", "other alias"]},
 "serp_keywords": {"_note": "...", "<group>": ["keyword", ...]}}
```

The effective term set for a subreddit is `core + (generic if allow_generic) + provider aliases + extra_keywords +
--keywords`. A provider alias that is also listed under `generic` stays gated. Matching is word-bounded and
case-insensitive; phrases match across any whitespace. A catalog without a `product` block (the older shape, with
`keywords` per subreddit) still loads.

## Data contracts

**`threads.jsonl`**: one record per thread (`pulse.thread_record`): `id, subreddit, title, flair, created_utc,
last_activity_utc, score, num_comments_reported, num_comments_archived, num_comments_in_window, keyword_hits_post,
keyword_hits_comments, pain_terms, activity_score, permalink, author, selftext, comments[{id, parent_id, author,
created_utc, score, body}]`.

**`summary.json`**: `product, version, collected_at, subreddits, keywords, created_within, active_within,
exclude_flair, min_comments, min_score, backend, threads_kept, threads_dropped, requests, cache_hits, kept_by_sub,
min_hits, preset`.

**`extract.jsonl`**: one record per thread:
`{id, relevant: bool, topic: <catalog topic>|"other", problem: str, stack: [str], tools_named: [str], tried: str,
wanted: str, answered: bool, answer_quality: 0-3, vendor_present: [str], vendor_accepted: bool}`.

**`clusters.json`**: themes with `label`, description, thread ids, `n` (threads), subreddits, tools named,
unanswered count, `recent_share` and `rising` (`n ≥ 3` and more than 45 % of the theme's threads in the latest third
of the creation window).

**`sentiment.json`**: per provider: votes from each scorer, κ, the aggregate below, and the report/refuse decision.

**`recommend.jsonl`**: per relevant thread: J, its five components, comment policy, permalink. No usernames.

**`delta.json`**: the previous run (latest earlier run with the same product and preset, or the same subreddit set
without a preset), thread churn, theme counts matched by label then by thread overlap (Jaccard ≥ 0.3), provider net
then vs now where both runs reported a number.

## Selection

1. Pull every post created inside `--created-within`, newest first, paginated. No server-side keyword search: it
   times out on busy subreddits.
2. Keyword gate: at least one hit in title or body, and `keyword_hits_post + keyword_hits_comments ≥ min_hits`;
   otherwise dropped as `few-hits`.
3. Fetch archived comments; drop `[removed]` and `[deleted]` bodies.
4. Keep if the last comment (or the post) is inside `--active-within`, the flair is not in `--exclude-flair`
   (default solved, resolved), and there are at least `--min-comments` comments.
5. `activity_score = comments in window + 0.5·ln(1 + score) + 0.25·pain terms in the post`.

## Joinability index

```
J = 0.30·active + 0.25·op_engaged + 0.20·unanswered + 0.15·density + 0.10·no_vendor
active     = 1 − clamp((now − last_activity_utc) / active_within, 0, 1)
op_engaged = 1 if the post author commented within op_window of now; 0.5 if they commented at all; else 0
unanswered = 0 if extract.answered, else 1 − clamp(max_comment_score / answer_threshold, 0, 1)
density    = clamp((keyword_hits_post + keyword_hits_comments) / density_cap, 0, 1)
no_vendor  = 0 if extract.vendor_accepted, else 1
```

Defaults: `active_within 30d`, `op_window 14d`, `answer_threshold 10`, `density_cap 6`. Threads with
`relevant == false` are excluded. Without `extract.jsonl`, every thread counts as relevant, unanswered, no vendor.
`closed` comment policy rows are kept, sorted last and marked DO NOT COMMENT. Threads removed since the archive
captured them are skipped here but still count in themes.

## Statistics (`stats.py`)

- `vader_vote(compounds)`: mean compound, pos above 0.05, neg below −0.05, else neu; empty → neu.
- `rubric_vote(score)`: 1–2 neg, 3 neu, 4–5 pos.
- `cohen_kappa(a, b)`: nominal κ; identical constant raters → 1.0; expected agreement 1 with disagreement → 0.0.
- `wilson(k, n)`: Wilson score interval at 95 %; n = 0 → (0, 1).
- `bootstrap_net(votes)`: 2.5 and 97.5 percentiles of net = (pos − neg)/n over 2,000 seeded resamples.
- `aggregate(votes)`: n, pos, neu, neg, net, shares, Wilson intervals, bootstrap interval on net.
- `report_allowed(n, kappa)`: a number is reported only when n ≥ 10 and κ ≥ 0.4; otherwise the reason.

## Overlay tiers

| tier | meaning | what to do |
|---|---|---|
| 1 | open in this run and already ranking in Google or cited by AI answers | comment here first |
| 2 | ranking or cited but not collected (often 6 months+ or locked) | open it, check it accepts comments |
| 3 | open in this run, not yet trusted | regular candidates |
| 4 | ranking or cited but removed | nothing to do |

Within a tier: AI citations, then Google position, then J. Threads outside the run are looked up on Arctic Shift
(one request per 400 ids for metadata, one per thread for comments; `--no-activity` skips the second).

## HTTP behaviour

One request per `--pace` seconds (0.5), a budget of `--max-requests` (2,000) per run, six attempts per request:
429 waits for `X-RateLimit-Reset` (max 120 s), 422 "slow down" and 5xx back off exponentially (2 s doubling to
60 s). `ping` makes a single attempt per archive.
