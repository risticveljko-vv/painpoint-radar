# Changelog

## Unreleased

- Public release under the MIT license.
- README rewritten: what the digest contains, archive status checked 2026-11-13, responsible use, FAQ.
- Dev and QA environment with mise (`mise run setup | check | test | qa`); CI runs the same tasks.

## 0.5.0 (2026-10-30)

First standalone release.

- Installable CLI: `painpoint-radar`, with every pipeline step as a subcommand (`uv tool install`).
- Package layout under `src/painpoint_radar/`; `omakase` runs steps as `python -m painpoint_radar.<step>`, so it
  works from any folder.
- Catalogs are looked up in `./catalogs`, `$PAINPOINT_RADAR_HOME/catalogs`, then the bundled examples. The bundled
  `example-product` (fictional) is the default product.
- The User-Agent contact comes from `$PAINPOINT_RADAR_CONTACT` or `product.contact`; a warning is printed when neither
  is set.
- Caches moved to `~/.cache/painpoint-radar/`; extraction cache keyed by prompt, so products never share results.
- VADER is the optional `sentiment` extra.
- `ping` makes one attempt per archive instead of waiting out rate limits.
- Docs: README, SKILL.md for agents, docs/ARCHITECTURE.md.
