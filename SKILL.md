---
name: painpoint-radar
description: >-
  Run a pain-point pass on Reddit for content and product research: collect threads still active on a topic
  from a product catalog's subreddits, then get a digest with threads worth joining, clustered pain points,
  provider sentiment with confidence, and the change since the last run. Use when asked what developers are
  struggling with right now, which Reddit threads a team could help in, what Reddit says about a provider, or
  to refresh a voice-of-customer section. Uses the painpoint-radar CLI (Arctic Shift and PullPush archives),
  never reddit.com. Trends only, no quotes.
---

# painpoint-radar

Installed CLI: `painpoint-radar` (see README "Install"). Work from a folder that holds `catalogs/` and where
`runs/` may be written. Everything product-specific lives in `catalogs/<product>.json`; add `--product NAME` to any
command. The bundled `example-product` is a template, not a real product.

## Steps

1. **Pick (or make) a catalog, then a preset.** The product block (name, aliases, domain, relevance, topics,
   `keywords.core` / `generic`), the subreddits (`allow_generic`, `extra_keywords`, `comment_policy`), presets and
   provider aliases live in the catalog. Extra terms for one run: `--keywords "a,b"`; bare `--site` seeds terms from
   the product's sitemap. A new subreddit goes into the catalog first. Set `allow_generic` only on narrow
   language or framework communities: a generic word on a broad subreddit is how a run becomes mostly noise.
   **New product, about twenty minutes:** copy the bundled `example-product.json` to `catalogs/<name>.json`, edit
   the product block and subreddits, then fill the keyword sets from data and review every printed addition:
   ```
   painpoint-radar keywords import <ahrefs-export.csv> --product <name> --into core --min-volume 100 --dry-run
   painpoint-radar keywords import <ahrefs-export.csv> --product <name> --into core --min-volume 100
   painpoint-radar keywords seed --product <name> --dry-run
   painpoint-radar keywords list --product <name>
   ```
   `comment_policy` stays `unknown` until a human reads each subreddit's rules for this product's purpose.
2. **Run it.** From a normal terminal (model calls use `claude -p`):
   ```
   painpoint-radar omakase --product <name> --preset <preset>
   ```
   Inside a Claude Code session `claude -p` refuses to nest; add `--backend codex`. Steps: collect → extract →
   cluster → sentiment → recommend → overlay → delta → digest, each also runnable alone with `--run runs/<stamp>`
   (e.g. `painpoint-radar digest --run runs/<stamp>`). Re-runs are cheap: archive responses cache for six hours,
   model outputs cache per prompt and thread. Budget: ~25 min collection for 8 subreddits, ~50 model calls per 400
   threads, USD 0 on the archives.
3. **Overlay (optional): add what Google and AI answers already trust.** Needs a SERP source (e.g. the Ahrefs
   MCP) and an AI-citation tracker (e.g. the Peec MCP). Write four files into `runs/<stamp>/overlay/`:
   - `serp.json`: `painpoint-radar overlay keywords` prints the keyword list from the catalog's `serp_keywords`.
     For each keyword fetch the top 10 (e.g. Ahrefs `serp-overview`, `select=position,url,type`, `country=us`,
     `top_positions=10`) and keep only reddit.com positions:
     `{"country":"us","fetched_at":"YYYY-MM-DD","keywords":{"<kw>":{"reddit":[{"position":3,"type":["organic"],"url":"..."}]}}}`.
     A keyword with an empty SERP gets `"no_serp_data": true`; a SERP with no Reddit result gets `"reddit": []`.
   - `citations.json`: AI-answer citations of reddit.com over the last 30 days by prompt, as `{"columns": [...],
     "rows": [...]}` (the shape of Peec `get_url_report` with `filters` domain in `["reddit.com","www.reddit.com"]`,
     `dimensions ["prompt_id"]`).
   - `prompts.json`: `{"prompts": {prompt_id: text}}`; `brands.json` (optional): `{"own": [ids], "brands": {id: name}}`.
   Then `painpoint-radar overlay --run runs/<stamp>` (2 to 4 min: it looks up threads outside the run on Arctic
   Shift so the table can say whether they are still active) and `painpoint-radar digest --run runs/<stamp>`.
   Status per thread lives in `backlog_status.csv` (ids only, hand-edited) and is merged in on every run.
4. **Read `runs/<stamp>/digest.md`.**
   - **Run facts**: counts, drops, kept per subreddit, relevant after extraction. If relevant is under 20, widen a
     window or fix the catalog terms before trusting the rest.
   - **Threads worth joining**: ranked by the joinability index J. The `policy` column is the subreddit's comment
     policy: `unknown` means nobody has read the rules yet; `closed` means do not comment. A human reads the
     thread, checks the rules and decides. With an overlay, two more columns: **Google** (best top-10 position) and
     **AI answers** (citation count, prompts, whether the product is already named).
   - **Backlog** (with an overlay): tier 1 = open in this run and already ranking or cited (start here); tier 2 =
     ranking or cited but not collected, often old or locked (open it first); tier 3 = open, not yet trusted;
     tier 4 = trusted but removed.
   - **Pain-point themes**: threads per theme (never comments), subreddits, tools named, unanswered count,
     `rising` when the theme skews to the latest third of the window. Each theme lists thread ids as evidence.
   - **Provider sentiment**: a number appears only when n ≥ 10 threads and κ ≥ 0.4; otherwise the reason.
   - **Since the previous run**: same product and preset; thread churn, themes and provider net then vs now.
   - **Evidence index**: every cited thread id with its permalink.
5. **Write the angle, not the quote.** Themes become article, documentation or product notes, paraphrased. The
   corpus is never quoted. `painpoint-radar thread <id>` is for reading a thread in full before joining it.
6. **File it.** Copy the digest (and `overlay.md`) to wherever the project keeps research. `runs/` stays local: it
   holds usernames and is git-ignored.

## When not to use

- **You want to quote a specific thread.** Paraphrase, or link the permalink.
- **You need historical volume.** Use Arctic Shift's `/api/time_series` or the monthly dumps.
- **You are checking whether a claim is true.** Reddit is a source of pains, not facts.
- **You want to post a comment.** Nothing here posts. Read the subreddit's rules, disclose who you are, help first.

## Common mistakes

| Mistake | Fix |
|---|---|
| Trusting a digest where one subreddit supplies most threads | Tighten that subreddit's terms or raise `--min-hits`; re-run |
| Reading a provider's sentiment when the section says "not reported" | It failed n or κ; collect more, never lower the bar |
| Reporting "N developers say" from N comments | Counts are threads; one loud thread is one data point |
| Commenting on a `policy: unknown` thread | Read the rules first; fill `comment_policy` in the catalog |
| Treating `rising` as a trend on n < 5 | It is a skew flag; check the dates in the evidence index |
| Comparing deltas across presets or products | delta pairs runs of the same product and preset only |
| Importing keywords without `--dry-run` first | The printed list is the review |
| Editing the bundled example for a real product | Copy it to `catalogs/<name>.json`; one catalog per product |
| Committing `runs/` | It holds full bodies and usernames; it is git-ignored |
| Running `omakase` in Claude Code without `--backend codex` | `claude -p` cannot nest; the chain stops at extract |
| Editing `overlay.md` to record progress | It is regenerated every run; use `backlog_status.csv` |

## Sources

- Arctic Shift API: https://github.com/ArthurHeitmann/arctic_shift/blob/master/api/README.md
- PullPush: https://pullpush.io
- VADER: Hutto & Gilbert 2014, `vaderSentiment` (MIT); Cohen's kappa; Wilson score interval
