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
- **2026-10-26** — Full daily write-ups added to the triad pages. Source: the 32 daily PDFs, converted back to Markdown by `scripts/pdf2md.py`, which reads structure from the typography (headings by size, block quotations by indent, tables by aligned cells, links from the PDFs' link annotations). Checked against each PDF's own text layer: only titles and page numbers are missing. URLs clipped at the page margin in the PDFs were restored from the URLs recorded in Grist (one by hand, listed in `data/url_fixes.json`). The 103 diagrams were extracted as images.
- **2026-10-26** — Public-edition rule: block quotations longer than 80 words are cut to about 60 words and marked as shortened (31 cases). The full converted write-ups live in `raw/`, which is kept out of the public repository; regenerating the site needs that folder.
- **2026-10-26** — Long public quotations now cut at the end of a sentence where possible (then at ; or :, then at a comma), never mid-word and never reworded.
- **2026-10-26** — Methods became data. Grist now has a `Methods` table (six methods: Triads, now on hand; An author and their shadow, active; Lens and specimen; News oracle; Margin and Lexicon; Mnemosyne panel), and every `Sets` row records its method (the 32 triads backfilled). New tables `Author_Weeks` (one row per author week) and `Shadow_Edges` (one row per relation found, with verified quote and location). The Triads instructions are kept verbatim in `method/triads_prompt_v1.md`; the draft instructions for the new method in `method/shadow_prompt_v0.1.md`.
- **2026-10-26** — First author week planned: Kathy Acker (four of her books in the library, three flagged Gestext; the library's full-text index finds "Kathy Acker" in 229 books).

## Known issues

- The theme list contains near-duplicates (e.g. *Sabbatai Ṣevi* / *Sabbatai Zevi*, *Ulcinj* / *Dulcigno*, *time turned into space* / *time into space*). Merging them is a curation task, not yet done.
- Conversion artefacts: a few line-break hyphens in proper names survive (e.g. "Plas-saert"), and figures sit where LaTeX floated them, which is sometimes a section later than the paragraph that describes them.
- Errata from earlier runs were not systematically recorded; only those that can be recovered will be listed.
