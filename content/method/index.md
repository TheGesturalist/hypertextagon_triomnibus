---
title: "Method"
---

How the Triomnibus was made, kept as a record alongside the work itself.

- [[method/methods|Reading methods]]: the daily methods, active and on hand.
- [[method/process-log|Process log]]: dated decisions, changes and failures.

## The practice in brief

Each day a scheduled routine draws three books from the Scribrarium, a private Calibre library of roughly 13,000 volumes. It picks books that share something that can be named precisely, reads them from their own files, and writes up the connection. Every quotation is checked word for word against the book's text and given a location. Each day's set and books are logged in a Grist database, which is the source for the pages of this site.

## How the site is built

The facts (sets, books, themes, quotations) live in the Grist document *Library Resurfacing* and are exported to `data/` in this repository. A script, `scripts/generate.py`, turns them into one page per triad, book, person and theme, and builds the apparatus. Pages under `method/` are written by hand. The site is rendered with Quartz, so the same Markdown can later move to another wiki engine.
