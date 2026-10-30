# painpoint-radar

Find out what developers are struggling with right now. painpoint-radar collects public Reddit threads that are
still active on a topic, extracts the problem in each one, groups the problems into themes, scores how people talk
about the tools in your category, and ranks the threads worth joining. The output is one Markdown digest per run.

It reads two public Reddit archives, [Arctic Shift](https://arctic-shift.photon-reddit.com) and
[PullPush](https://pullpush.io), and never contacts reddit.com. The collector uses only the Python standard
library; the archives cost nothing.

```
collect → extract → cluster → sentiment → recommend → overlay → delta → digest.md
```

## Why archives, not Reddit

Reddit blocks scripted reads (HTTP 403 for curl and automation browsers; its User Agreement forbids scraping;
`robots.txt` is `Disallow: /`). The two archives expose the same public content over a plain HTTP API. The tool
paces itself (one request every 0.5 s), caches every response for six hours, and identifies itself with a contact
address so the archive operators can reach you.

**Archive status (2026-09-30):** Arctic Shift is live and current to the day. PullPush answers 429 to paced
requests and its archive is reported frozen at May 2025; it remains as an automatic fallback only.

## Install

Requires Python 3.13 or newer and [uv](https://docs.astral.sh/uv/).

```
git clone https://github.com/risticveljko-vv/painpoint-radar.git
cd painpoint-radar
uv tool install --editable ".[sentiment]"
painpoint-radar --version
```

The `sentiment` extra installs VADER (one of the two sentiment scorers). Without it everything else runs, and the
sentiment section of the digest says why it reports no numbers.

Set a contact address for the archives' User-Agent once, in your shell profile:

```
export PAINPOINT_RADAR_CONTACT="you@example.com"
```

## Quick start (five minutes, bundled example)

The bundled catalog `example-product` describes a fictional background-job product called Queuely, listening on
r/rails, r/node and r/django. It is the default product, so this works straight after install:

```
mkdir ~/radar && cd ~/radar              # runs/ is written here
painpoint-radar ping                     # are the archives reachable?
painpoint-radar keywords list            # what the example listens for
painpoint-radar collect --preset queues  # threads only, no model calls
painpoint-radar omakase --preset queues  # the whole chain, prints the path of digest.md
```

`collect` writes a run folder under `runs/<stamp>/`: `threads.md` (the readable corpus, no usernames),
`threads.jsonl` (every archived comment), and `summary.json` (parameters, counts, requests).

## Your own product (about twenty minutes)

A **catalog** separates *where to listen* (subreddits, time windows, comment policy) from *what to listen for*
(the product, its competitors, its keywords). One JSON file per product, no code changes.

```
mkdir -p catalogs
cp "$(python3 -c 'import painpoint_radar, pathlib; print(pathlib.Path(painpoint_radar.__file__).parent)')/catalogs/example-product.json" catalogs/myproduct.json
# edit catalogs/myproduct.json: product block, subreddits, presets, providers
painpoint-radar keywords import ~/Downloads/ahrefs-export.csv --product myproduct --into core --min-volume 100 --dry-run
painpoint-radar keywords import ~/Downloads/ahrefs-export.csv --product myproduct --into core --min-volume 100
painpoint-radar keywords seed --product myproduct --dry-run      # seeds from the product's own sitemap
painpoint-radar keywords list --product myproduct
painpoint-radar omakase --product myproduct --preset <preset>
```

Catalogs are looked up in `./catalogs/`, then `$PAINPOINT_RADAR_HOME/catalogs/`, then the bundled examples.
`--catalog PATH` overrides all of them; `$PAINPOINT_RADAR_PRODUCT` changes the default product.

`keywords import` accepts Ahrefs Keywords Explorer exports as downloaded (UTF-16 with tabs, or UTF-8 with commas),
Semrush exports, or a one-column list. Terms are casefolded, deduplicated against the whole catalog and dropped
under three characters. Every addition is printed first; `--dry-run` writes nothing.

**Noise is this tool's failure mode.** A generic word like "email" on a broad subreddit floods the run. Put broad
terms under `keywords.generic` (matched only on subreddits with `allow_generic: true`) and keep `min_hits` at 2 on
broad communities. Review the printed list before every import.

### Catalog fields

```
product        name, aliases (own-brand match), site (for --site seeding), contact (User-Agent),
               domain + category (prompt wording), tool_label (what the extractor calls named tools),
               relevance (what counts as relevant), topics (extractor enum; "other" is added),
               keywords.core (every sub), keywords.generic (subs with allow_generic), keywords.competitors = "providers"
subreddits     allow_generic, extra_keywords, comment_policy, policy_note, policy_url, audience
presets        subs, created_within, active_within, min_hits
providers      name -> aliases; also the competitor keywords and the sentiment targets
serp_keywords  groups of Google keywords the overlay checks
```

The full schema and data contracts are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## The pipeline

`painpoint-radar omakase` chains every step over one run folder. Each step is also its own command and can be
re-run alone with `--run runs/<stamp>`.

| step | command | writes | model call |
|---|---|---|---|
| collect | `painpoint-radar collect` | `threads.{md,jsonl}`, `summary.json` | no |
| extract | `painpoint-radar extract` | `extract.jsonl`: relevant, topic, problem, stack, tools, tried, wanted, answered, vendor | yes, batches of 8 |
| cluster | `painpoint-radar cluster` | `clusters.json`: themes, threads per theme, `rising` flag | yes, 1 + n/40 |
| sentiment | `painpoint-radar sentiment` | `sentiment.json`: per provider, two scorers, κ, net with intervals | yes, n/25 |
| recommend | `painpoint-radar recommend` | `recommend.{md,jsonl}`: joinability index J, comment policy | no |
| overlay | `painpoint-radar overlay` | `overlay.{md,json}`: threads that rank in Google or are cited by AI answers | no |
| delta | `painpoint-radar delta` | `delta.json`: change since the previous run of the same product and preset | no |
| digest | `painpoint-radar digest` | `digest.md`: the report a human reads | no |

**How threads are selected.** Every post created inside `--created-within` (default 90 days) is pulled per
subreddit, newest first. A thread needs at least one keyword hit in its title or body and at least `--min-hits`
hits overall. Its comments are fetched, `[removed]` and `[deleted]` bodies dropped. It is kept only if its last
comment is inside `--active-within` (default 30 days), it is not flaired solved or resolved, and it has at least
`--min-comments` comments: still open, still discussed. Threads are ranked by recent comments, with a little
weight for score and for pain vocabulary in the post ("can't", "stuck", "why", ...).

**Joinability index.** `J = 0.30·active + 0.25·OP engaged + 0.20·unanswered + 0.15·keyword density + 0.10·no
vendor accepted`, each component in [0, 1]. Every row carries the subreddit's `comment_policy` from the catalog;
`closed` rows sort last and are marked DO NOT COMMENT. The policy is filled by hand from each subreddit's rules
page. The tool recommends; a human decides whether to comment.

**Sentiment.** Aspect-level: only sentences that name a provider count, one vote per thread. Two independent
scorers (VADER and an LLM on a fixed 1 to 5 rubric), Cohen's κ between them, net = (pos − neg)/n with Wilson and
bootstrap intervals. A number is printed only when n ≥ 10 threads and κ ≥ 0.4; otherwise the digest prints why.

**Overlay (optional).** A thread that already ranks in Google's top 10 for one of your keywords, or that AI answers
already cite, is worth more than a fresh one. `overlay` joins that evidence onto the run, adds trusted threads the
run did not collect, and writes a tiered backlog. Its inputs are four JSON files in `runs/<stamp>/overlay/`,
written by an agent from a SERP source and an AI-citation tracker (formats in [SKILL.md](SKILL.md) step 3).
`omakase` skips the step when the inputs are absent. Per-thread status (`new | needs-comment | commented | growing
| skip`, owner, note) is kept by hand in `backlog_status.csv` in the working folder and merged in on every run.

## Model backends

Model calls go through `llm.py`, which shells out to a CLI and validates the JSON against a schema:

- `claude`: `claude -p --json-schema` (default in a normal terminal; uses a Claude subscription)
- `codex`: `codex exec --output-schema` (`--backend codex`; required inside a Claude Code session, where
  `claude -p` refuses to nest)

Outputs are cached under `~/.cache/painpoint-radar/` (LLM calls by prompt, extractions per prompt and thread), so a
re-run only pays for new threads. Prompts sent to the model carry no usernames: authors become "OP" and
"commenter". `PAINPOINT_RADAR_LLM` and `PAINPOINT_RADAR_LLM_MODEL` pick the backend and model.

## Compliance position

- The tool reads third-party archives, not Reddit. Both archives are free services that ask users to be
  considerate; the pacing, the cache and the contact address are there for that.
- **The corpus is never quoted.** It is for trends and pain points, paraphrased in your own words.
  `painpoint-radar thread <id>` exists to read a thread in full before deciding to join it, not to lift text.
- Usernames are kept in `threads.jsonl` (needed to tell OP from commenters) and omitted from every Markdown output.
  **Do not commit `runs/`. Do not publish the corpus.** `runs/` and `backlog_status.csv` are git-ignored.
- Nothing here posts to Reddit.

## Behaviour under load

- One request every 0.5 s (`--pace`), a hard budget of 2,000 requests per run (`--max-requests`).
- 429: sleeps for the archive's `X-RateLimit-Reset`, then retries; 422 "slow down" and 5xx: exponential backoff.
- Archive responses are cached for six hours (`--no-cache` to skip).
- If Arctic Shift fails for a subreddit, PullPush is tried automatically (`--no-fallback` to disable).
- The two archives can differ by a comment or two on the same thread. For a count-grade claim, say which archive.

## Environment

| variable | effect |
|---|---|
| `PAINPOINT_RADAR_CONTACT` | contact address in the User-Agent (overrides `product.contact`) |
| `PAINPOINT_RADAR_PRODUCT` | default product (default `example-product`) |
| `PAINPOINT_RADAR_HOME` | extra folder searched for `catalogs/<product>.json` |
| `PAINPOINT_RADAR_CACHE` | cache root (default `~/.cache/painpoint-radar`) |
| `PAINPOINT_RADAR_LLM` | `claude`, `codex` or `auto` |
| `PAINPOINT_RADAR_LLM_MODEL` | model name for the chosen backend |

## Tests

```
python3 -m unittest discover -s tests
```

107 offline tests: window parsing, keyword matching and gating, activity logic, the authorless corpus, catalog
resolution, keyword import, statistics (κ, Wilson, bootstrap), the recommender, the overlay and every pipeline
module. No test touches the network; `painpoint-radar ping` is the live smoke test.

## Layout

```
src/painpoint_radar/
  cli.py          entry point; routes step commands to their modules
  pulse.py        collector, thread reader, ping, catalog resolution, the omakase chain
  keywords.py     list / import (Ahrefs, Semrush, CSV) / seed (sitemap)
  llm.py          schema-checked model call with a disk cache
  extract.py      pain-point extraction per thread
  cluster.py      themes and threads per theme
  sentiment.py    aspect-level provider sentiment, two scorers
  stats.py        κ, Wilson, bootstrap, vote aggregation, report/refuse rule
  recommend.py    joinability index and comment policy
  overlay.py      SERP + AI-citation overlay and the tiered backlog
  delta.py        diff against the previous run
  digest.py       digest.md writer
  seed.py         keyword seeds from a sitemap
  catalogs/       bundled example catalog
docs/ARCHITECTURE.md   house rules, data contracts, formulas
SKILL.md               how an AI agent runs a pass and reads the digest
tests/                 offline unit tests and fixtures
```
