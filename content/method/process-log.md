---
title: "Process log"
---

Dated decisions, changes and failures. Newest last.

- **2026-09-24** — First triad: *Cento, Copy, Dictionary*. Daily routine begins.
- **2026-10-25** — Triad 32, *The Pattern Determines the Parts*. The Scribrarium drive could not be mounted in the Mac shell; the catalogue and the three book files were copied to the cloud workspace, read unaltered, and the PDF built there. Every other step ran as specified.
- **2026-10-25** — Decision: compile Volume 1 (32 triads, 96 books) as the *Hypertext Triomnibus*, combining a hypertext edition with a scholarly apparatus (bibliography, index of quotations, graded climb by difficulty, errata).
- **2026-10-25** — Decision: five candidate daily methods recorded (see [[method/methods|Reading methods]]); *An author and their shadow* chosen to start, with methods to be kept as a table in Grist and every reading tracked against a method.
- **2026-10-25** — Decision: document the whole process as it happens; this log begins.
- **2026-10-25** — Hosting: considered DokuWiki (needs a PHP server), PHP hosts, Railway and Fly.io, Wiki.js and Semantic MediaWiki. Chosen: **Quartz** on GitHub Pages at triomnibus.thegesturalist.com, repository `TheGesturalist/hypertextagon_triomnibus`. Reason: no server, any number of readers, and Markdown that can later move to Wiki.js or Semantic MediaWiki. Principle adopted: keep the data separate from the wiki engine.
- **2026-10-25** — First build. Data exported from Grist (32 sets, 96 books, 1,106 themes). Credits parsed into people with roles (author, editor, translator, contributor); 14 credit strings needed hand overrides, recorded in `scripts/credits.py`. Private fields (Drive links, personal notes, Calibre tags) are excluded from public pages.
- **2026-10-25** — Published. Code pushed to GitHub by hand (this session has no repository access). GitHub Pages deploys from `main` via the Quartz workflow. DNS: CNAME `triomnibus` → `thegesturalist.github.io` added in the WordPress.com DNS for thegesturalist.com. The site is live at triomnibus.thegesturalist.com; HTTPS waits on GitHub issuing a certificate for the subdomain.
- **2026-10-25** — Removed the Quartz project's own maintenance files that came with the template (Dependabot, auto-merge, CI, Docker and preview workflows, issue templates, funding link). They had opened dependency-update pull requests in this repository and could merge them automatically. Footer links now point to theGesturalist and this repository.

## Known issues

- The theme list contains near-duplicates (e.g. *Sabbatai Ṣevi* / *Sabbatai Zevi*, *Ulcinj* / *Dulcigno*, *time turned into space* / *time into space*). Merging them is a curation task, not yet done.
- Errata from earlier runs were not systematically recorded; only those that can be recovered will be listed.
