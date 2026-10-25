# The Hypertext Triomnibus

Scribrarium, Volume 1: Triads. A Quartz site built from the daily resurfacing data.

## Layout

- `data/` — JSON exported from the Grist document *Library Resurfacing* (sets, books, themes). The source of truth for generated pages.
- `scripts/generate.py` — builds `content/` (triads, books, people, themes, apparatus, home page) from `data/`.
- `scripts/credits.py` — parses credit strings into people and roles; hand overrides live here.
- `content/method/` — written by hand; never overwritten by the generator.
- Everything else is Quartz 5 (https://quartz.jzhao.xyz).

## Rebuild

    python3 scripts/generate.py        # regenerate content/ from data/
    npm ci && npx quartz plugin install
    npx quartz build --serve           # preview at http://localhost:8080

Note: the generator writes to `content/` relative to the repository root.
