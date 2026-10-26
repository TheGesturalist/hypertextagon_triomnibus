"""Generate Quartz Markdown pages for the Hypertext Triomnibus from data/*.json.

Source of truth: data/sets.json, data/books.json, data/themes.json (exported from the
Grist doc "Library Resurfacing"). Pages in content/ are generated; do not hand-edit them
except under content/method/, which is written by hand and never overwritten.
"""
import json, re, sys, unicodedata, datetime, collections, pathlib, shutil

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from credits import parse as parse_credits

DATA = ROOT / "data"
OUT = ROOT / "content"
GENERATED = ["triads", "books", "people", "themes", "apparatus"]

def slug(s, maxlen=60):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen].rstrip("-") or "untitled"

def reflist(v):
    return [x for x in (v or []) if x != "L"]

def ymd(ts):
    return datetime.datetime.fromtimestamp(int(ts), datetime.UTC).strftime("%Y-%m-%d")

def esc(s):
    return (s or "").replace('"', '\\"')

def link(path, label):
    label = label.replace("|", "/").replace("[", "(").replace("]", ")")
    return f"[[{path}|{label}]]"

def write(rel, text):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip() + "\n", encoding="utf-8")

def bullets_from_block(text):
    """Turn a newline- or number-separated block into list items."""
    lines = [l.strip() for l in (text or "").split("\n") if l.strip()]
    return lines

RAW_MD = ROOT / "raw" / "md"
RAW_IMG = ROOT / "raw" / "img"
EXCERPT_MAX_WORDS = 80   # public edition: longer block quotations are shortened
EXCERPT_KEEP_WORDS = 60

def shorten_quote(text):
    """Cut a long quotation at the best boundary within the first EXCERPT_KEEP_WORDS words:
    a full stop / ? / ! (with any closing quote) first, then ; or :, then a comma. Words are never altered."""
    words = text.split()
    if len(words) <= EXCERPT_MAX_WORDS:
        return text, False
    window = " ".join(words[:EXCERPT_KEEP_WORDS + 10])
    for pat in (r"[.?!][”’\")]*(?=\s)", r"[;:](?=\s)", r",(?=\s)"):
        ends = [m.end() for m in re.finditer(pat, window + " ")]
        ends = [e for e in ends if 20 <= len(window[:e].split()) <= EXCERPT_KEEP_WORDS + 10]
        if ends:
            return window[:ends[-1]] + " […]", True
    return " ".join(words[:EXCERPT_KEEP_WORDS]) + " […]", True

def repair_urls(md, rabbit_text):
    """Restore URLs whose tails were clipped or split in the PDF, using the URLs recorded in Grist."""
    gurls = re.findall(r"https?://[^\s)\]]+", rabbit_text or "")
    gurls = [u.rstrip(".,;") for u in gurls]
    for u in sorted(set(gurls), key=len, reverse=True):
        if u in md:
            continue
        # split form: prefix + whitespace + suffix
        done = False
        for k in range(len(u) - 4, 14, -1):
            pre = u[:k]
            i = md.find(pre)
            if i < 0:
                continue
            j = i + k
            m = re.match(r"\S*\s+(\S+)", md[j:])
            if m and u.endswith(m.group(1).rstrip(".,;")) and len(m.group(1)) > 4:
                end = j + m.end(1) - (len(m.group(1)) - len(m.group(1).rstrip(".,;")))
                md = md[:i] + u + md[end:]
                done = True
                break
            m2 = re.match(r"(\S*)", md[j:])
            frag = pre + m2.group(1)
            if [g for g in gurls if g.startswith(frag.rstrip(".,;")) and g not in md] == [u] and frag.rstrip(".,;") != u:
                md = md[:i] + u + md[j + len(m2.group(1).rstrip(".,;")):]
                done = True
                break
        if not done:
            pass
    return md

def load_writeup(date, setf, cid_to_path):
    p = RAW_MD / f"{date}.md"
    if not p.exists():
        return None
    md = p.read_text(encoding="utf-8")
    md = repair_urls(md, setf.get("Rabbit_Holes"))
    for old, new in json.load(open(DATA / "url_fixes.json")).get(date, []):
        md = md.replace(old, new)
    # private field: Calibre tags in the edition line
    md = re.sub(r"\s*·\s*tags:[^*\n]*(?=\*)", "", md)
    out, shortened, linked = [], 0, set()
    for line in md.split("\n"):
        m = re.search(r"Calibre ID (\d+)", line)
        if m and not line.startswith(("|", "-", ">")) and int(m.group(1)) in cid_to_path and int(m.group(1)) not in linked:
            linked.add(int(m.group(1)))
            line = line + " → " + link(cid_to_path[int(m.group(1))], "book page")
        if line.startswith("> ") and not line.startswith("> —"):
            q, cut = shorten_quote(line[2:])
            if cut:
                shortened += 1
                line = "> " + q + "\n>\n> *(Shortened for the public edition.)*"
        line = re.sub(r"!\[\]\(([^)]+)\)", lambda m: f"![](images/{m.group(1)})", line)
        out.append(line)
    # copy images
    for img in RAW_IMG.glob(f"{date}-*"):
        dst = OUT / "triads" / "images" / img.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(img, dst)
    return "\n".join(out).strip()

def main():
    sets = json.load(open(DATA / "sets.json"))
    books = json.load(open(DATA / "books.json"))
    themes = {int(k): v for k, v in json.load(open(DATA / "themes.json")).items()}

    for d in GENERATED:
        shutil.rmtree(OUT / d, ignore_errors=True)

    # ---- index structures
    set_by_id = {s["id"]: s["fields"] for s in sets}
    set_path, theme_slug, theme_sets, theme_books = {}, {}, collections.defaultdict(set), collections.defaultdict(set)
    for sid, f in set_by_id.items():
        set_path[sid] = f"triads/{ymd(f['Date'])}-{slug(f['Set_Title'], 50)}"
    # theme slugs (merge collisions)
    seen = {}
    for tid, name in themes.items():
        s = slug(name)
        theme_slug[tid] = "themes/" + s
        seen.setdefault(s, []).append(name)
    book_path, books_by_set, people = {}, collections.defaultdict(list), collections.defaultdict(lambda: collections.defaultdict(set))
    for b in books:
        f = b["fields"]
        cid = int(f["Calibre_ID"])
        book_path[b["id"]] = f"books/{cid}-{slug(f['Title'], 50)}"
        books_by_set[int(f["Set"])].append(b)
        for name, role in parse_credits(cid, f["Author"]):
            people[name][role].add(b["id"])
        for t in reflist(f.get("Theme_Links")):
            theme_books[t].add(b["id"])
    for sid, f in set_by_id.items():
        for t in reflist(f.get("Theme_Links")):
            theme_sets[t].add(sid)
    person_path = {n: "people/" + slug(n) for n in people}

    def people_links(cid, credit):
        parts = []
        for name, role in parse_credits(cid, credit):
            lab = link(person_path[name], name)
            parts.append(lab if role == "author" else f"{lab} ({role})")
        return ", ".join(parts)

    # ---- triad pages
    ordered = sorted(set_by_id.items(), key=lambda kv: int(kv[1]["Date"]))
    for i, (sid, f) in enumerate(ordered):
        date = ymd(f["Date"])
        kinds = reflist(f.get("Connection_Kind"))
        tags = ["triad", f"difficulty/{f.get('Difficulty')}"] + ["kind/" + slug(k) for k in kinds]
        prev_l = link(set_path[ordered[i-1][0]], ordered[i-1][1]["Set_Title"]) if i > 0 else ""
        next_l = link(set_path[ordered[i+1][0]], ordered[i+1][1]["Set_Title"]) if i + 1 < len(ordered) else ""
        L = ["---", f'title: "{esc(f["Set_Title"])}"', f"date: {date}",
             "tags:", *[f"  - {t}" for t in tags], f"difficulty: {f.get('Difficulty')}", "---", ""]
        L.append(f"*Triad {i+1} of {len(ordered)} · {date} · Difficulty {f.get('Difficulty')} · {', '.join(kinds)}*")
        body = load_writeup(date, f, {int(b["fields"]["Calibre_ID"]): book_path[b["id"]] for b in books_by_set[sid]})
        if body is None:
            L += ["", "## The thread", "", f.get("Thread", "").strip()]
        L += ["", "## The three books", ""]
        for b in books_by_set[sid]:
            bf = b["fields"]
            L.append(f"- {link(book_path[b['id']], bf['Title'])}, {people_links(int(bf['Calibre_ID']), bf['Author'])}; {bf.get('Year','')}. *{bf.get('Role_in_Set','').strip()}*")
        if body is not None:
            L += ["", "---", "", body]
        if body is None and f.get("Notebook_Prompts"):
            L += ["", "## Notebook", ""] + [f"{n}. " + re.sub(r"^\d+[.)]\s*", "", x) for n, x in enumerate(bullets_from_block(f["Notebook_Prompts"]), 1)]
        if body is None and f.get("Rabbit_Holes"):
            L += ["", "## Rabbit holes", "", "*Links as checked on the day of the reading.*", ""]
            L += [f"- {x}" for x in bullets_from_block(f["Rabbit_Holes"])]
        if body is None and f.get("Carry_Quote"):
            L += ["", "## To carry", "", "> " + f["Carry_Quote"].strip()]
        tl = sorted(reflist(f.get("Theme_Links")), key=lambda t: themes.get(t, "").lower())
        if tl:
            L += ["", "## Themes", "", " · ".join(link(theme_slug[t], themes[t]) for t in tl if t in themes)]
        L += ["", "---", "", f"Previous: {prev_l or '—'}  ", f"Next: {next_l or '—'}"]
        write(set_path[sid] + ".md", "\n".join(L))

    # ---- book pages
    for b in books:
        f = b["fields"]; cid = int(f["Calibre_ID"]); sid = int(f["Set"])
        L = ["---", f'title: "{esc(f["Title"])}"', "tags:", "  - book", f"calibre_id: {cid}", "---", ""]
        L.append(f"**{people_links(cid, f['Author'])}** · {f.get('Year','')} · {f.get('File_Format','')} · Calibre ID {cid}")
        L += ["", f"Resurfaced in {link(set_path[sid], set_by_id[sid]['Set_Title'])} ({ymd(set_by_id[sid]['Date'])}).", ""]
        if f.get("Role_in_Set"):
            L += ["## Role in the triad", "", f["Role_in_Set"].strip(), ""]
        if f.get("Selection_Logic"):
            L += ["## Why this book", "", f["Selection_Logic"].strip(), ""]
        if f.get("Quotes"):
            L += ["## Quotations", "", "*Verified against the book's own text on the day of the reading.*", ""]
            L += [f"- {q}" for q in bullets_from_block(f["Quotes"])]
            L.append("")
        tl = sorted(reflist(f.get("Theme_Links")), key=lambda t: themes.get(t, "").lower())
        if tl:
            L += ["## Themes", "", " · ".join(link(theme_slug[t], themes[t]) for t in tl if t in themes)]
        write(book_path[b["id"]] + ".md", "\n".join(L))

    # ---- people pages
    role_order = ["author", "editor", "translator", "contributor"]
    for name, roles in people.items():
        L = ["---", f'title: "{esc(name)}"', "tags:", "  - person", *[f"  - role/{r}" for r in roles], "---", ""]
        for r in role_order:
            if r in roles:
                L += [f"## As {r}", ""]
                for bid in sorted(roles[r]):
                    bf = next(x["fields"] for x in books if x["id"] == bid)
                    sid = int(bf["Set"])
                    L.append(f"- {link(book_path[bid], bf['Title'])} — in {link(set_path[sid], set_by_id[sid]['Set_Title'])}")
                L.append("")
        write(person_path[name] + ".md", "\n".join(L))

    # ---- theme pages (only themes in use)
    used = set(theme_sets) | set(theme_books)
    by_slug = collections.defaultdict(list)
    for t in used:
        if t in themes:
            by_slug[theme_slug[t]].append(t)
    for path, tids in by_slug.items():
        names = sorted({themes[t] for t in tids})
        L = ["---", f'title: "{esc(names[0])}"', "tags:", "  - theme"]
        L += ["---", ""]
        if len(names) > 1:
            L += ["*Also recorded as: " + "; ".join(names[1:]) + ".*", ""]
        sids = sorted({s for t in tids for s in theme_sets.get(t, ())}, key=lambda s: int(set_by_id[s]["Date"]))
        bids = sorted({b for t in tids for b in theme_books.get(t, ())})
        if sids:
            L += ["## Triads", ""] + [f"- {link(set_path[s], set_by_id[s]['Set_Title'])} ({ymd(set_by_id[s]['Date'])})" for s in sids] + [""]
        if bids:
            L += ["## Books", ""]
            for bid in bids:
                bf = next(x["fields"] for x in books if x["id"] == bid)
                L.append(f"- {link(book_path[bid], bf['Title'])}")
        write(path + ".md", "\n".join(L))

    # ---- apparatus
    # bibliography
    L = ["---", 'title: "Bibliography"', "tags:", "  - apparatus", "---", "",
         f"All {len(books)} books resurfaced in Volume 1, alphabetical by first credited name. Calibre IDs refer to the Scribrarium catalogue.", ""]
    def sortkey(b):
        first = parse_credits(int(b['fields']['Calibre_ID']), b['fields']['Author'])[0][0]
        return (first.split()[-1].lower(), b['fields']['Title'].lower())
    for b in sorted(books, key=sortkey):
        f = b["fields"]; cid = int(f["Calibre_ID"])
        L.append(f"- {people_links(cid, f['Author'])}. *{link(book_path[b['id']], f['Title'])}*. {f.get('Year','')}. Calibre ID {cid}.")
    write("apparatus/bibliography.md", "\n".join(L))
    # index of quotations
    L = ["---", 'title: "Index of Quotations"', "tags:", "  - apparatus", "---", "",
         "Every quotation recorded for a book, grouped by book. Each was checked against the book's own text on the day it was used.", ""]
    for b in sorted(books, key=sortkey):
        f = b["fields"]
        qs = bullets_from_block(f.get("Quotes"))
        if qs:
            L += [f"### {link(book_path[b['id']], f['Title'])}", ""] + [f"- {q}" for q in qs] + [""]
    write("apparatus/quotations.md", "\n".join(L))
    # graded climb
    L = ["---", 'title: "The Graded Climb"', "tags:", "  - apparatus", "---", "",
         "The triads ordered by difficulty, from the most visible connections to the most oblique.", ""]
    for d in sorted({int(f.get("Difficulty") or 0) for f in set_by_id.values()}):
        L += [f"## Difficulty {d}", ""]
        for sid, f in ordered:
            if int(f.get("Difficulty") or 0) == d:
                L.append(f"- {link(set_path[sid], f['Set_Title'])} ({ymd(f['Date'])})")
        L.append("")
    write("apparatus/graded-climb.md", "\n".join(L))
    # people index
    L = ["---", 'title: "People"', "tags:", "  - apparatus", "---", "", "Authors, editors, translators and contributors credited in Volume 1.", ""]
    for name in sorted(people, key=lambda n: n.split()[-1].lower()):
        roles = ", ".join(r for r in role_order if r in people[name])
        L.append(f"- {link(person_path[name], name)} — {roles}")
    write("apparatus/people.md", "\n".join(L))

    # ---- home (generated)
    L = ["---", 'title: "The Hypertext Triomnibus"', "---", "",
         "*Scribrarium, Volume 1: Triads.*", "",
         f"{len(ordered)} days, {len(books)} books, three at a time, resurfaced from one private library of about thirteen thousand volumes and read for what passes between them. Each triad links to its books, each book to its people and themes, so the volume can be read in order or sideways.", "",
         "## Ways in", "",
         "- In order: start with " + link(set_path[ordered[0][0]], ordered[0][1]["Set_Title"]) + ".",
         "- By difficulty: " + link("apparatus/graded-climb", "The Graded Climb") + ".",
         "- By person: " + link("apparatus/people", "People") + ".",
         "- The apparatus: " + link("apparatus/bibliography", "Bibliography") + " · " + link("apparatus/quotations", "Index of Quotations") + ".",
         "- How this was made: " + link("method/index", "Method") + ".", "",
         "## The triads", ""]
    for i, (sid, f) in enumerate(ordered):
        L.append(f"{i+1}. {ymd(f['Date'])} — {link(set_path[sid], f['Set_Title'])}")
    write("index.md", "\n".join(L))

    print(f"triads={len(ordered)} books={len(books)} people={len(people)} themes={len(by_slug)}")

if __name__ == "__main__":
    main()
