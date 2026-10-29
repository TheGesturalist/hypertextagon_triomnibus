"""Generate Quartz Markdown pages AND the rich Graph Observatory dataset for the Hypertext Triomnibus.

Upgrades over v1 generate.py:
1. Multi-Method Architecture:
   - Preserves Volume 1 (Triads, Method 1) under `triads/` with zero URL changes.
   - Renders Method 2 ("An author and their shadow") under `shadows/` + `shadows/weeks/<author>.md` hubs,
     incorporating `Author_Weeks`, `Shadow_Edges` citation/dispute tables, and Scribrarium `Mentions` totals.
   - Renders Methods 3–6 ("Lens and specimen", "News oracle", "Margin and Lexicon", "Mnemosyne panel")
     under `sets/<method-slug>/` so future method rotations publish automatically without code changes.
   - Handles recurring books across multiple sets/methods cleanly (one canonical book page listing all appearances).
2. Canonical Theme Normalization:
   - Applies `data/theme_aliases.json` + case/diacritic folding to merge near-duplicate themes
     (e.g. Sabbatai Ṣevi / Sabbatai Zevi, Acéphale / acephale, spectator / spectators).
3. Formal Structural Graph Payload (`quartz/static/triomnibus-graph.json`):
   - Emits typed vertices (`set`, `author_week`, `book`, `person`, `theme`, `ghost_book`) and typed edges
     (`in_set`, `credit:*`, `shadow:*`, `has_theme`, `shared_theme`, `concept_stem`, `wordnet_kin`, `ghost_bridge`, `sequence`).
   - Precomputes exact Hage & Harary / Moretti graph metrics on the core structural backbone:
     Brandes betweenness centrality, Tarjan articulation points (cutpoints / "Horatios"), bridge edges,
     local clustering coefficient, and connected components.
"""
import collections
import datetime
import json
import pathlib
import re
import shutil
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from credits import parse as parse_credits

DATA = ROOT / "data"
OUT = ROOT / "content"
STATIC_OUT = ROOT / "quartz" / "static"
GENERATED = ["triads", "shadows", "sets", "books", "people", "themes", "apparatus"]

RAW_MD = ROOT / "raw" / "md"
RAW_IMG = ROOT / "raw" / "img"
EXCERPT_MAX_WORDS = 80
EXCERPT_KEEP_WORDS = 60

STOP_STEMS = {
    "the", "and", "of", "in", "to", "as", "a", "an", "for", "on", "by", "or",
    "vs", "with", "without", "from", "into", "their", "what", "who", "over",
}


def slug(s, maxlen=60):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:maxlen].rstrip("-") or "untitled"


def reflist(v):
    return [x for x in (v or []) if x != "L"]


def ymd(ts):
    if isinstance(ts, str) and re.match(r"^\d{4}-\d{2}-\d{2}", ts):
        return ts[:10]
    return datetime.datetime.fromtimestamp(int(ts), datetime.timezone.utc).strftime("%Y-%m-%d")


def ts_num(ts):
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str) and ts.isdigit():
        return int(ts)
    if isinstance(ts, str) and re.match(r"^\d{4}-\d{2}-\d{2}", ts):
        dt = datetime.datetime.strptime(ts[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    return 0


def esc(s):
    return (s or "").replace('"', '\\"')


def link(path, label):
    label = (label or "").replace("|", "/").replace("[", "(").replace("]", ")")
    return f"[[{path}|{label}]]"


def write(rel, text):
    p = OUT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text.rstrip() + "\n", encoding="utf-8")


def bullets_from_block(text):
    return [l.strip() for l in (text or "").split("\n") if l.strip()]


def shorten_quote(text):
    words = text.split()
    if len(words) <= EXCERPT_MAX_WORDS:
        return text, False
    window = " ".join(words[: EXCERPT_KEEP_WORDS + 10])
    for pat in (r'[.?!][”’")]*(?=\s)', r"[;:](?=\s)", r",(?=\s)"):
        ends = [m.end() for m in re.finditer(pat, window + " ")]
        ends = [e for e in ends if 20 <= len(window[:e].split()) <= EXCERPT_KEEP_WORDS + 10]
        if ends:
            return window[: ends[-1]] + " […]", True
    return " ".join(words[:EXCERPT_KEEP_WORDS]) + " […]", True


def repair_urls(md, rabbit_text):
    gurls = re.findall(r"https?://[^\s)\]]+", rabbit_text or "")
    gurls = [u.rstrip(".,;") for u in gurls]
    for u in sorted(set(gurls), key=len, reverse=True):
        if u in md:
            continue
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
                break
            m2 = re.match(r"(\S*)", md[j:])
            frag = pre + m2.group(1)
            if (
                [g for g in gurls if g.startswith(frag.rstrip(".,;")) and g not in md] == [u]
                and frag.rstrip(".,;") != u
            ):
                md = md[:i] + u + md[j + len(m2.group(1).rstrip(".,;")) :]
                break
    return md


def load_writeup(date, setf, cid_to_path, section_folder):
    p = RAW_MD / f"{date}.md"
    if not p.exists():
        return None
    md = p.read_text(encoding="utf-8")
    md = repair_urls(md, setf.get("Rabbit_Holes"))
    url_fixes_path = DATA / "url_fixes.json"
    if url_fixes_path.exists():
        for old, new in json.loads(url_fixes_path.read_text(encoding="utf-8")).get(date, []):
            md = md.replace(old, new)
    md = re.sub(r"\s*·\s*tags:[^*\n]*(?=\*)", "", md)
    out, linked = [], set()
    for line in md.split("\n"):
        m = re.search(r"Calibre ID (\d+)", line)
        if (
            m
            and not line.startswith(("|", "-", ">"))
            and int(m.group(1)) in cid_to_path
            and int(m.group(1)) not in linked
        ):
            linked.add(int(m.group(1)))
            line = line + " → " + link(cid_to_path[int(m.group(1))], "book page")
        if line.startswith("> ") and not line.startswith("> —"):
            q, cut = shorten_quote(line[2:])
            if cut:
                line = "> " + q + "\n>\n> *(Shortened for the public edition.)*"
        line = re.sub(r"!\[\]\(([^)]+)\)", lambda m: f"![](images/{m.group(1)})", line)
        out.append(line)
    if RAW_IMG.exists():
        for img in RAW_IMG.glob(f"{date}-*"):
            dst = OUT / section_folder / "images" / img.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(img, dst)
    return "\n".join(out).strip()


def load_json_opt(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def compute_graph_metrics(node_ids, undirected_pairs):
    """Exact Hage & Harary / Moretti structural metrics on an undirected graph:
    - Degree
    - Connected component ID
    - Local clustering coefficient
    - Brandes exact normalized betweenness centrality
    - Tarjan articulation points (cutpoints / 'Horatios') and bridges
    """
    adj = {u: set() for u in node_ids}
    for u, v in undirected_pairs:
        if u in adj and v in adj and u != v:
            adj[u].add(v)
            adj[v].add(u)

    # Connected components
    comp = {}
    cid = 0
    for u in node_ids:
        if u in comp:
            continue
        cid += 1
        q = collections.deque([u])
        comp[u] = cid
        while q:
            cur = q.popleft()
            for nb in adj[cur]:
                if nb not in comp:
                    comp[nb] = cid
                    q.append(nb)

    # Local clustering coefficient
    clustering = {}
    for u in node_ids:
        nbs = list(adj[u])
        k = len(nbs)
        if k < 2:
            clustering[u] = 0.0
            continue
        links = 0
        nb_set = adj[u]
        for i in range(k):
            for j in range(i + 1, k):
                if nbs[j] in adj[nbs[i]]:
                    links += 1
        clustering[u] = round((2.0 * links) / (k * (k - 1)), 4)

    # Brandes betweenness centrality
    cb = {u: 0.0 for u in node_ids}
    for s in node_ids:
        stack = []
        pred = {w: [] for w in node_ids}
        sigma = {w: 0.0 for w in node_ids}
        sigma[s] = 1.0
        dist = {w: -1 for w in node_ids}
        dist[s] = 0
        q = collections.deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    q.append(w)
                    dist[w] = dist[v] + 1
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {w: 0.0 for w in node_ids}
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                cb[w] += delta[w]

    n = len(node_ids)
    norm = ((n - 1) * (n - 2)) if n > 2 else 1.0
    for u in cb:
        cb[u] = round(cb[u] / norm, 5)

    # Tarjan articulation points (cutpoints) & bridges
    disc = {}
    low = {}
    parent = {}
    cutpoints = set()
    bridges = set()
    timer = [0]

    sys.setrecursionlimit(max(4000, n * 4))

    def dfs(u):
        disc[u] = low[u] = timer[0]
        timer[0] += 1
        children = 0
        for v in adj[u]:
            if v not in disc:
                children += 1
                parent[v] = u
                dfs(v)
                low[u] = min(low[u], low[v])
                if parent.get(u) is None and children > 1:
                    cutpoints.add(u)
                if parent.get(u) is not None and low[v] >= disc[u]:
                    cutpoints.add(u)
                if low[v] > disc[u]:
                    bridges.add(tuple(sorted((u, v))))
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for u in node_ids:
        if u not in disc:
            parent[u] = None
            dfs(u)

    # 2-core peeling to isolate non-trivial macro cutpoints ("Horatios" that split multi-set clusters)
    alive_2core = set(node_ids)
    peeled = True
    while peeled:
        peeled = False
        for u in list(alive_2core):
            if sum(1 for nb in adj[u] if nb in alive_2core) <= 1:
                alive_2core.remove(u)
                peeled = True

    disc2, low2, parent2, macro_cutpoints = {}, {}, {}, set()
    timer2 = [0]

    def dfs2(u):
        disc2[u] = low2[u] = timer2[0]
        timer2[0] += 1
        ch = 0
        for v in adj[u]:
            if v not in alive_2core:
                continue
            if v not in disc2:
                ch += 1
                parent2[v] = u
                dfs2(v)
                low2[u] = min(low2[u], low2[v])
                if parent2.get(u) is None and ch > 1:
                    macro_cutpoints.add(u)
                if parent2.get(u) is not None and low2[v] >= disc2[u]:
                    macro_cutpoints.add(u)
            elif v != parent2.get(u):
                low2[u] = min(low2[u], disc2[v])

    for u in alive_2core:
        if u not in disc2:
            parent2[u] = None
            dfs2(u)

    return {
        "degree": {u: len(adj[u]) for u in node_ids},
        "component": comp,
        "clustering": clustering,
        "betweenness": cb,
        "cutpoints": cutpoints,
        "macro_cutpoints": macro_cutpoints,
        "in_2core": alive_2core,
        "bridges": bridges,
    }


def main():
    sets = json.loads((DATA / "sets.json").read_text(encoding="utf-8"))
    books = json.loads((DATA / "books.json").read_text(encoding="utf-8"))
    themes = {int(k): v for k, v in json.loads((DATA / "themes.json").read_text(encoding="utf-8")).items()}
    methods_raw = load_json_opt(DATA / "methods.json", [])
    weeks_raw = load_json_opt(DATA / "author_weeks.json", [])
    shadow_edges_raw = load_json_opt(DATA / "shadow_edges.json", [])
    mentions_raw = load_json_opt(DATA / "mentions.json", [])
    aliases_cfg = load_json_opt(DATA / "theme_aliases.json", {"canonical_map": {}})
    overlay = load_json_opt(DATA / "enrichment_overlay.json", {})

    canonical_map = aliases_cfg.get("canonical_map", {})

    for d in GENERATED:
        shutil.rmtree(OUT / d, ignore_errors=True)

    # ---- Methods & Author_Weeks lookup
    methods_by_id = {
        m["id"]: m["fields"]
        for m in methods_raw
    }
    if 1 not in methods_by_id:
        methods_by_id[1] = {"Name": "Triads", "Slug": "triads"}
    if 2 not in methods_by_id:
        methods_by_id[2] = {"Name": "An author and their shadow", "Slug": "shadows"}

    active_week_ids_from_sets = {
        int(s["fields"].get("Author_Week") or 0)
        for s in sets
        if s["fields"].get("Author_Week")
    }
    weeks_filtered = [
        w
        for w in weeks_raw
        if w["id"] in active_week_ids_from_sets
        or str(w["fields"].get("Status") or "").strip().lower() not in ("planned", "queued", "inbox")
    ]
    weeks_filtered.sort(key=lambda w: (ts_num(w["fields"].get("Start")) or 0, w["id"]))
    weeks_by_id = {w["id"]: w["fields"] for w in weeks_filtered}
    week_display_num = {w["id"]: idx + 1 for idx, w in enumerate(weeks_filtered)}
    week_path = {}
    for wid, wf in weeks_by_id.items():
        author_name = wf.get("Author") or f"week-{wid}"
        week_path[wid] = f"shadows/weeks/{slug(author_name, 50)}"

    # Mentions count per week
    mentions_count_by_week = collections.Counter()
    for mr in mentions_raw:
        wk = mr.get("fields", {}).get("Week")
        if wk:
            mentions_count_by_week[int(wk)] += 1
    for wid, wf in weeks_by_id.items():
        if wf.get("Mentions_Total") and not mentions_count_by_week[wid]:
            mentions_count_by_week[wid] = int(wf["Mentions_Total"])

    # ---- Sets & method routing
    set_by_id = {s["id"]: s["fields"] for s in sets}
    set_path, set_method_name, set_folder = {}, {}, {}
    for sid, f in set_by_id.items():
        mid = f.get("Method") or 1
        if isinstance(mid, str):
            # Support string or integer reference
            mname = mid
        else:
            mname = methods_by_id.get(int(mid), {}).get("Name", "Triads")
        set_method_name[sid] = mname
        if mname == "Triads" or (not f.get("Method") and not str(f.get("Set_Title", "")).startswith("Shadow")):
            folder = "triads"
            set_method_name[sid] = "Triads"
        elif mname == "An author and their shadow" or str(f.get("Set_Title", "")).startswith("Shadow"):
            folder = "shadows"
            set_method_name[sid] = "An author and their shadow"
        else:
            folder = f"sets/{slug(mname, 30)}"
        set_folder[sid] = folder
        set_path[sid] = f"{folder}/{ymd(f['Date'])}-{slug(f['Set_Title'], 50)}"

    # ---- Canonical Theme Normalization
    theme_slug, theme_canonical_name = {}, {}
    theme_sets = collections.defaultdict(set)
    theme_books = collections.defaultdict(set)
    for tid, name in themes.items():
        cname = canonical_map.get(name, name)
        s = slug(cname)
        theme_slug[tid] = "themes/" + s
        theme_canonical_name[tid] = cname

    # ---- Books (grouped by Calibre_ID so recurring books merge cleanly onto one canonical page)
    books_by_set = collections.defaultdict(list)
    books_by_cid = collections.defaultdict(list)
    book_path_by_id = {}
    book_path_by_cid = {}
    people = collections.defaultdict(lambda: collections.defaultdict(set))  # name -> role -> set of cids

    for b in books:
        f = b["fields"]
        cid = int(f["Calibre_ID"])
        sid = int(f["Set"])
        books_by_set[sid].append(b)
        books_by_cid[cid].append(b)
        if cid not in book_path_by_cid:
            book_path_by_cid[cid] = f"books/{cid}-{slug(f['Title'], 50)}"
        book_path_by_id[b["id"]] = book_path_by_cid[cid]
        for name, role in parse_credits(cid, f.get("Author", "")):
            if not name.startswith("?"):
                people[name][role].add(cid)
        for t in reflist(f.get("Theme_Links")):
            theme_books[t].add(cid)

    for sid, f in set_by_id.items():
        for t in reflist(f.get("Theme_Links")):
            theme_sets[t].add(sid)

    person_path = {n: "people/" + slug(n) for n in people}

    def people_links(cid, credit):
        parts = []
        for name, role in parse_credits(cid, credit):
            if name.startswith("?"):
                continue
            lab = link(person_path[name], name)
            parts.append(lab if role == "author" else f"{lab} ({role})")
        return ", ".join(parts)

    # Shadow edges indexed by week and set
    edges_by_week = collections.defaultdict(list)
    edges_by_set = collections.defaultdict(list)
    for se in shadow_edges_raw:
        sef = se["fields"]
        if sef.get("Week"):
            edges_by_week[int(sef["Week"])].append(sef)
        if sef.get("Set"):
            edges_by_set[int(sef["Set"])].append(sef)

    # ---- Ordered sets (chronological across all methods)
    ordered = sorted(set_by_id.items(), key=lambda kv: (ts_num(kv[1]["Date"]), kv[0]))
    triad_ordered = [(sid, f) for sid, f in ordered if set_method_name[sid] == "Triads"]
    shadow_ordered = [(sid, f) for sid, f in ordered if set_method_name[sid] == "An author and their shadow"]
    other_ordered = [
        (sid, f)
        for sid, f in ordered
        if set_method_name[sid] not in ("Triads", "An author and their shadow")
    ]

    triad_rank = {sid: idx + 1 for idx, (sid, _) in enumerate(triad_ordered)}

    # ---- Render Set Pages (Triads, Shadow Days, and Other Methods)
    for i, (sid, f) in enumerate(ordered):
        date = ymd(f["Date"])
        mname = set_method_name[sid]
        kinds = reflist(f.get("Connection_Kind"))
        is_triad = mname == "Triads"
        is_shadow = mname == "An author and their shadow"

        base_tag = "triad" if is_triad else ("shadow" if is_shadow else f"method/{slug(mname)}")
        tags = [base_tag, f"difficulty/{f.get('Difficulty')}"] + ["kind/" + slug(k) for k in kinds]
        prev_l = link(set_path[ordered[i - 1][0]], ordered[i - 1][1]["Set_Title"]) if i > 0 else ""
        next_l = link(set_path[ordered[i + 1][0]], ordered[i + 1][1]["Set_Title"]) if i + 1 < len(ordered) else ""

        L = [
            "---",
            f'title: "{esc(f["Set_Title"])}"',
            f"date: {date}",
            f'method: "{esc(mname)}"',
            "tags:",
            *[f"  - {t}" for t in tags],
            f"difficulty: {f.get('Difficulty')}",
            "---",
            "",
        ]

        if is_triad:
            L.append(
                f"*Triad {triad_rank[sid]} of {len(triad_ordered)} · {date} · "
                f"Difficulty {f.get('Difficulty')} · {', '.join(kinds)}*"
            )
        elif is_shadow:
            wid = int(f.get("Author_Week") or 0)
            wday = f.get("Week_Day") or "?"
            wf = weeks_by_id.get(wid, {})
            wauthor = wf.get("Author", "Subject")
            wk_link = link(week_path[wid], f"{wauthor} (Author & Shadow)") if wid in week_path else wauthor
            L.append(
                f"*{wk_link} · Day {wday} of 7 · {date} · "
                f"Difficulty {f.get('Difficulty')} · {', '.join(kinds)}*"
            )
        else:
            L.append(f"*{mname} · {date} · Difficulty {f.get('Difficulty')} · {', '.join(kinds)}*")

        body = load_writeup(
            date,
            f,
            {int(b["fields"]["Calibre_ID"]): book_path_by_id[b["id"]] for b in books_by_set[sid]},
            set_folder[sid].split("/")[0],
        )
        if body is None and f.get("Thread"):
            heading = "## The thread" if is_triad else "## The day's finding"
            L += ["", heading, "", f.get("Thread", "").strip()]

        b_heading = "## The three books" if (is_triad and len(books_by_set[sid]) == 3) else "## Books in this reading"
        L += ["", b_heading, ""]
        for b in books_by_set[sid]:
            bf = b["fields"]
            cid = int(bf["Calibre_ID"])
            role_txt = f" *{bf.get('Role_in_Set', '').strip()}*" if bf.get("Role_in_Set") else ""
            L.append(
                f"- {link(book_path_by_cid[cid], bf['Title'])}, "
                f"{people_links(cid, bf.get('Author', ''))}; {bf.get('Year', '')}.{role_txt}"
            )

        if is_shadow:
            wid = int(f.get("Author_Week") or 0)
            wday_num = int(f.get("Week_Day") or 99)
            week_edges_so_far = [
                e for e in edges_by_week.get(wid, [])
                if int(set_by_id.get(int(e.get("Set", 0)), {}).get("Week_Day") or 0) <= wday_num
            ]
            if week_edges_so_far:
                L += [
                    "",
                    "## The shadow so far",
                    "",
                    "| Citing Book | Relation | Target | Location |",
                    "|---|---|---|---|",
                ]
                for e in week_edges_so_far:
                    ccid = int(e.get("Citing_Calibre_ID") or 0)
                    clink = (
                        link(book_path_by_cid[ccid], e.get("Citing_Title", str(ccid)))
                        if ccid in book_path_by_cid
                        else e.get("Citing_Title", "")
                    )
                    tcid = int(e.get("Target_Calibre_ID") or 0)
                    tlink = (
                        link(book_path_by_cid[tcid], e.get("Target_Title", "Subject"))
                        if tcid in book_path_by_cid
                        else (e.get("Target_Title") or weeks_by_id.get(wid, {}).get("Author", ""))
                    )
                    L.append(
                        f"| {clink} ({e.get('Citing_Author', '')}) | **{e.get('Relation', '')}** | "
                        f"{tlink} | {e.get('Location', '')} |"
                    )

        if body is not None:
            L += ["", "---", "", body]
        if body is None and f.get("Notebook_Prompts"):
            L += ["", "## Notebook", ""] + [
                f"{n}. " + re.sub(r"^\d+[.)]\s*", "", x)
                for n, x in enumerate(bullets_from_block(f["Notebook_Prompts"]), 1)
            ]
        if body is None and f.get("Rabbit_Holes"):
            L += ["", "## Rabbit holes", "", "*Links as checked on the day of the reading.*", ""]
            L += [f"- {x}" for x in bullets_from_block(f["Rabbit_Holes"])]
        if body is None and f.get("Carry_Quote"):
            L += ["", "## To carry", "", "> " + f["Carry_Quote"].strip()]

        tl = sorted(
            {theme_slug[t]: theme_canonical_name[t] for t in reflist(f.get("Theme_Links")) if t in themes}.items(),
            key=lambda kv: kv[1].lower(),
        )
        if tl:
            L += ["", "## Themes", "", " · ".join(link(sl, nm) for sl, nm in tl)]
        L += ["", "---", "", f"Previous: {prev_l or '—'}  ", f"Next: {next_l or '—'}"]
        write(set_path[sid] + ".md", "\n".join(L))

    # ---- Render Author_Week Hub Pages
    for wid, wf in weeks_by_id.items():
        author_name = wf.get("Author", f"Week {wid}")
        wsets = [
            (sid, sf)
            for sid, sf in shadow_ordered
            if int(sf.get("Author_Week") or 0) == wid
        ]
        m_total = mentions_count_by_week.get(wid, 0)
        start_str = f" · Started {ymd(wf['Start'])}" if wf.get("Start") else ""
        L = [
            "---",
            f'title: "Shadow — {esc(author_name)}"',
            "tags:",
            "  - author-week",
            "  - shadow",
            "---",
            "",
            f"*Volume 2: An Author and Their Shadow · Week {week_display_num.get(wid, wid)} · Status: {wf.get('Status', 'In progress')}"
            f"{start_str} · {m_total} Scribrarium volumes mention {author_name}.*",
            "",
        ]
        if author_name in person_path:
            L += [f"Subject page: {link(person_path[author_name], author_name)}.", ""]
        if wf.get("Day_Plan"):
            L += ["## Plan of the week", "", wf["Day_Plan"].strip(), ""]
        if wsets:
            L += ["## Days published", ""]
            for sid, sf in wsets:
                L.append(
                    f"- **Day {sf.get('Week_Day', '?')}** ({ymd(sf['Date'])}) — "
                    f"{link(set_path[sid], sf['Set_Title'])}"
                )
            L.append("")
        wedges = edges_by_week.get(wid, [])
        if wedges:
            L += [
                "## Citation & argument map (`Shadow_Edges`)",
                "",
                "| Citing Book | Author | Relation | Target Work | Location |",
                "|---|---|---|---|---|",
            ]
            for e in wedges:
                ccid = int(e.get("Citing_Calibre_ID") or 0)
                clink = (
                    link(book_path_by_cid[ccid], e.get("Citing_Title", str(ccid)))
                    if ccid in book_path_by_cid
                    else e.get("Citing_Title", "")
                )
                tcid = int(e.get("Target_Calibre_ID") or 0)
                tlink = (
                    link(book_path_by_cid[tcid], e.get("Target_Title", author_name))
                    if tcid in book_path_by_cid
                    else (e.get("Target_Title") or author_name)
                )
                L.append(
                    f"| {clink} | {e.get('Citing_Author', '')} | **{e.get('Relation', '')}** | "
                    f"{tlink} | {e.get('Location', '')} |"
                )
            L.append("")
        if wf.get("Closing_Summary"):
            L += ["## Closing essay", "", wf["Closing_Summary"].strip(), ""]
        write(week_path[wid] + ".md", "\n".join(L))

    # ---- Render Canonical Book Pages (supporting multiple appearances across sets)
    for cid, entries in books_by_cid.items():
        entries_sorted = sorted(entries, key=lambda x: ts_num(set_by_id[int(x["fields"]["Set"])]["Date"]))
        f0 = entries_sorted[0]["fields"]
        L = [
            "---",
            f'title: "{esc(f0["Title"])}"',
            "tags:",
            "  - book",
            f"calibre_id: {cid}",
            "---",
            "",
            f"**{people_links(cid, f0.get('Author', ''))}** · {f0.get('Year', '')} · "
            f"{f0.get('File_Format', '')} · Calibre ID {cid}",
            "",
        ]
        if len(entries_sorted) == 1:
            sid = int(f0["Set"])
            role_hdr = "## Role in the triad" if set_method_name[sid] == "Triads" else "## Role in the reading"
            L += [f"Resurfaced in {link(set_path[sid], set_by_id[sid]['Set_Title'])} ({ymd(set_by_id[sid]['Date'])}).", ""]
            if f0.get("Role_in_Set"):
                L += [role_hdr, "", f0["Role_in_Set"].strip(), ""]
            if f0.get("Selection_Logic"):
                L += ["## Why this book", "", f0["Selection_Logic"].strip(), ""]
        else:
            L += ["## Resurfaced across multiple readings", ""]
            for ent in entries_sorted:
                ef = ent["fields"]
                sid = int(ef["Set"])
                L.append(
                    f"- {link(set_path[sid], set_by_id[sid]['Set_Title'])} ({ymd(set_by_id[sid]['Date'])}): "
                    f"*{ef.get('Role_in_Set', '').strip()}*"
                )
            L.append("")

        all_quotes = []
        for ent in entries_sorted:
            for q in bullets_from_block(ent["fields"].get("Quotes")):
                if q not in all_quotes:
                    all_quotes.append(q)
        if all_quotes:
            L += ["## Quotations", "", "*Verified against the book's own text on the day of the reading.*", ""]
            L += [f"- {q}" for q in all_quotes]
            L.append("")

        all_tids = [t for ent in entries_sorted for t in reflist(ent["fields"].get("Theme_Links")) if t in themes]
        tl = sorted(
            {theme_slug[t]: theme_canonical_name[t] for t in all_tids}.items(),
            key=lambda kv: kv[1].lower(),
        )
        if tl:
            L += ["## Themes", "", " · ".join(link(sl, nm) for sl, nm in tl)]
        write(book_path_by_cid[cid] + ".md", "\n".join(L))

    # ---- Render People Pages
    role_order = ["author", "editor", "translator", "contributor"]
    for name, roles in people.items():
        L = [
            "---",
            f'title: "{esc(name)}"',
            "tags:",
            "  - person",
            *[f"  - role/{r}" for r in roles],
            "---",
            "",
        ]
        # Link to Author_Week hub if this person was an Author & Shadow subject
        for wid, wf in weeks_by_id.items():
            if wf.get("Author") == name:
                L += [f"**Featured Subject:** {link(week_path[wid], 'Shadow — ' + name)}", ""]
        for r in role_order:
            if r in roles:
                L += [f"## As {r}", ""]
                for cid in sorted(roles[r]):
                    entries = books_by_cid[cid]
                    btitle = entries[0]["fields"]["Title"]
                    set_links = ", ".join(
                        link(set_path[int(e["fields"]["Set"])], set_by_id[int(e["fields"]["Set"])]["Set_Title"])
                        for e in entries
                    )
                    L.append(f"- {link(book_path_by_cid[cid], btitle)} — in {set_links}")
                L.append("")
        write(person_path[name] + ".md", "\n".join(L))

    # ---- Render Canonical Theme Pages
    used = set(theme_sets) | set(theme_books)
    by_slug = collections.defaultdict(list)
    for t in used:
        if t in themes:
            by_slug[theme_slug[t]].append(t)
    for path, tids in by_slug.items():
        cname = theme_canonical_name[tids[0]]
        raw_names = sorted({themes[t] for t in tids if themes[t] != cname})
        L = ["---", f'title: "{esc(cname)}"', "tags:", "  - theme", "---", ""]
        if raw_names:
            L += ["*Also recorded as: " + "; ".join(raw_names) + ".*", ""]
        sids = sorted({s for t in tids for s in theme_sets.get(t, ())}, key=lambda s: ts_num(set_by_id[s]["Date"]))
        cids = sorted({c for t in tids for c in theme_books.get(t, ())})
        if sids:
            L += ["## Readings", ""] + [
                f"- {link(set_path[s], set_by_id[s]['Set_Title'])} ({ymd(set_by_id[s]['Date'])})"
                for s in sids
            ] + [""]
        if cids:
            L += ["## Books", ""]
            for cid in cids:
                L.append(f"- {link(book_path_by_cid[cid], books_by_cid[cid][0]['fields']['Title'])}")
        write(path + ".md", "\n".join(L))

    # ---- Render Apparatus
    def sortkey_cid(cid):
        f0 = books_by_cid[cid][0]["fields"]
        parsed = parse_credits(cid, f0.get("Author", ""))
        first = parsed[0][0] if parsed else ""
        return ((first.split()[-1].lower() if first else ""), f0["Title"].lower())

    L = [
        "---",
        'title: "Bibliography"',
        "tags:",
        "  - apparatus",
        "---",
        "",
        f"All {len(books_by_cid)} distinct books resurfaced across {len(ordered)} daily readings, "
        f"alphabetical by first credited name. Calibre IDs refer to the Scribrarium catalogue.",
        "",
    ]
    for cid in sorted(books_by_cid, key=sortkey_cid):
        f0 = books_by_cid[cid][0]["fields"]
        L.append(
            f"- {people_links(cid, f0.get('Author', ''))}. *{link(book_path_by_cid[cid], f0['Title'])}*. "
            f"{f0.get('Year', '')}. Calibre ID {cid}."
        )
    write("apparatus/bibliography.md", "\n".join(L))

    L = [
        "---",
        'title: "Index of Quotations"',
        "tags:",
        "  - apparatus",
        "---",
        "",
        "Every quotation recorded for a book, grouped by book. Each was checked against the book's own text on the day it was used.",
        "",
    ]
    for cid in sorted(books_by_cid, key=sortkey_cid):
        f0 = books_by_cid[cid][0]["fields"]
        qs = []
        for ent in books_by_cid[cid]:
            for q in bullets_from_block(ent["fields"].get("Quotes")):
                if q not in qs:
                    qs.append(q)
        if qs:
            L += [f"### {link(book_path_by_cid[cid], f0['Title'])}", ""] + [f"- {q}" for q in qs] + [""]
    write("apparatus/quotations.md", "\n".join(L))

    L = [
        "---",
        'title: "The Graded Climb"',
        "tags:",
        "  - apparatus",
        "---",
        "",
        "Readings ordered by difficulty, from the most visible connections to the most oblique.",
        "",
    ]
    for d in sorted({int(f.get("Difficulty") or 0) for f in set_by_id.values()}):
        L += [f"## Difficulty {d}", ""]
        for sid, f in ordered:
            if int(f.get("Difficulty") or 0) == d:
                mtag = "" if set_method_name[sid] == "Triads" else f" · *{set_method_name[sid]}*"
                L.append(f"- {link(set_path[sid], f['Set_Title'])} ({ymd(f['Date'])}){mtag}")
        L.append("")
    write("apparatus/graded-climb.md", "\n".join(L))

    L = [
        "---",
        'title: "People"',
        "tags:",
        "  - apparatus",
        "---",
        "",
        "Authors, editors, translators and contributors credited across the Triomnibus.",
        "",
    ]
    for name in sorted(people, key=lambda n: n.split()[-1].lower()):
        roles = ", ".join(r for r in role_order if r in people[name])
        L.append(f"- {link(person_path[name], name)} — {roles}")
    write("apparatus/people.md", "\n".join(L))

    # ---- Home page (multi-volume aware + Graph Observatory link)
    L = [
        "---",
        'title: "The Hypertext Triomnibus"',
        "---",
        "",
        "*Scribrarium — Daily Resurfacings from a Thirteen-Thousand-Volume Library.*",
        "",
        f"{len(ordered)} days and {len(books_by_cid)} distinct books resurfaced from one private library "
        f"and read from their own files for what passes between them. Volume 1 (*Triads*) reads three books "
        f"at a time across shared forms, motifs, genealogies, and conflicts; Volume 2 (*An Author and Their Shadow*) "
        f"tracks a single writer per week across the library's full-text index.",
        "",
        "## Ways in",
        "",
        "- **Interactive Constellation:** [Open the Graph Observatory](/static/observatory/index.html) (Moretti ablation lab, *Chavruta* pathfinder, Scribrarium ghost shelf, & corpus Q&A).",
        "- In order: start with " + link(set_path[ordered[0][0]], ordered[0][1]["Set_Title"]) + ".",
        "- By difficulty: " + link("apparatus/graded-climb", "The Graded Climb") + ".",
        "- By person: " + link("apparatus/people", "People") + ".",
        "- The apparatus: " + link("apparatus/bibliography", "Bibliography") + " · " + link("apparatus/quotations", "Index of Quotations") + ".",
        "- How this was made: " + link("method/index", "Method") + ".",
        "",
    ]
    if shadow_ordered or weeks_by_id:
        L += ["## Volume 2: An Author and Their Shadow", ""]
        for wid, wf in weeks_by_id.items():
            wnum = week_display_num.get(wid, wid)
            L.append(
                f"- **Week {wnum}:** {link(week_path[wid], 'Shadow — ' + wf.get('Author', ''))} "
                f"(*{wf.get('Status', 'In progress')}* · {mentions_count_by_week.get(wid, 0)} library mentions)"
            )
        L.append("")
        for j, (sid, f) in enumerate(shadow_ordered, 1):
            L.append(f"{j}. {ymd(f['Date'])} — {link(set_path[sid], f['Set_Title'])}")
        L.append("")

    if other_ordered:
        L += ["## Additional Reading Series", ""]
        for j, (sid, f) in enumerate(other_ordered, 1):
            L.append(f"{j}. {ymd(f['Date'])} — {link(set_path[sid], f['Set_Title'])} (*{set_method_name[sid]}*)")
        L.append("")

    L += ["## Volume 1: The Triads", ""]
    for i, (sid, f) in enumerate(triad_ordered, 1):
        L.append(f"{i}. {ymd(f['Date'])} — {link(set_path[sid], f['Set_Title'])}")
    write("index.md", "\n".join(L))

    # =========================================================================
    # Build Rich Graph Payload (`quartz/static/triomnibus-graph.json`)
    # =========================================================================
    annotations_by_cid = {
        int(a["calibre_id"]): a for a in overlay.get("calibre_reader_annotations", [])
    }

    nodes = []
    edges = []
    node_by_id = {}

    def add_node(nd):
        node_by_id[nd["id"]] = nd
        nodes.append(nd)

    day_idx_by_sid = {sid: idx + 1 for idx, (sid, _) in enumerate(ordered)}

    # 1. Set nodes
    for idx, (sid, f) in enumerate(ordered):
        mname = set_method_name[sid]
        subtype = "triad" if mname == "Triads" else ("shadow" if mname == "An author and their shadow" else "method")
        wid = int(f.get("Author_Week") or 0)
        add_node({
            "id": f"set:{sid}",
            "type": "set",
            "subtype": subtype,
            "set_id": sid,
            "slug": set_path[sid],
            "title": f["Set_Title"],
            "date": ymd(f["Date"]),
            "day_index": idx + 1,
            "difficulty": int(f.get("Difficulty") or 3),
            "connection_kinds": reflist(f.get("Connection_Kind")),
            "method": mname,
            "author_week": weeks_by_id.get(wid, {}).get("Author") if wid else None,
            "week_day": int(f.get("Week_Day") or 0) if wid else None,
            "thread": (f.get("Thread") or "").strip(),
            "carry_quote": (f.get("Carry_Quote") or "").strip(),
            "prompts": bullets_from_block(f.get("Notebook_Prompts")),
            "preview": bool(f.get("_preview", False)),
        })
        if idx > 0:
            prev_sid = ordered[idx - 1][0]
            edges.append({
                "source": f"set:{prev_sid}",
                "target": f"set:{sid}",
                "kind": "sequence",
                "label": f"Day {idx} → Day {idx + 1}",
                "day_index": idx + 1,
            })

    # 2. Author_Week nodes
    for wid, wf in weeks_by_id.items():
        wauthor = wf.get("Author", f"Week {wid}")
        wnode_id = f"week:{wid}"
        w_sids = [sid for sid, sf in shadow_ordered if int(sf.get("Author_Week") or 0) == wid]
        min_day = min((day_idx_by_sid[s] for s in w_sids), default=1)
        wnum = week_display_num.get(wid, wid)
        add_node({
            "id": wnode_id,
            "type": "author_week",
            "subtype": "author_week",
            "slug": week_path[wid],
            "title": f"Shadow Week {wnum}: {wauthor}",
            "author": wauthor,
            "status": wf.get("Status", "In progress"),
            "mentions_total": mentions_count_by_week.get(wid, 0),
            "day_plan": wf.get("Day_Plan", ""),
            "own_works": wf.get("Own_Works", ""),
            "day_index": min_day,
        })
        for s in w_sids:
            edges.append({
                "source": f"set:{s}",
                "target": wnode_id,
                "kind": "week_member",
                "label": f"Day {set_by_id[s].get('Week_Day', '?')}",
                "day_index": day_idx_by_sid[s],
            })

    # 3. Book nodes (merged by Calibre_ID)
    for cid, entries in books_by_cid.items():
        entries_sorted = sorted(entries, key=lambda x: ts_num(set_by_id[int(x["fields"]["Set"])]["Date"]))
        f0 = entries_sorted[0]["fields"]
        sids = [int(e["fields"]["Set"]) for e in entries_sorted]
        min_day = min(day_idx_by_sid[s] for s in sids)
        yr_m = re.search(r"(-?\d{3,4})", str(f0.get("Year", "")))
        yr_num = int(yr_m.group(1)) if yr_m else None
        qs = []
        for ent in entries_sorted:
            for q in bullets_from_block(ent["fields"].get("Quotes")):
                if q not in qs:
                    qs.append(q)
        ann = annotations_by_cid.get(cid)
        add_node({
            "id": f"book:{cid}",
            "type": "book",
            "subtype": "recurring_book" if len(sids) > 1 else "book",
            "calibre_id": cid,
            "slug": book_path_by_cid[cid],
            "title": f0["Title"],
            "author_credit": f0.get("Author", ""),
            "year": str(f0.get("Year", "")),
            "year_num": yr_num,
            "format": f0.get("File_Format", ""),
            "sets": sids,
            "day_index": min_day,
            "difficulty": max(int(set_by_id[s].get("Difficulty") or 3) for s in sids),
            "method": set_method_name[sids[0]],
            "role_in_set": f0.get("Role_in_Set", ""),
            "selection_logic": f0.get("Selection_Logic", ""),
            "quotes": qs[:8],
            "quote_count": len(qs),
            "reader_highlights": ann["highlight_count"] if ann else 0,
            "sample_highlight": ann["sample_highlight"] if ann else None,
        })
        for ent in entries_sorted:
            sid = int(ent["fields"]["Set"])
            edges.append({
                "source": f"book:{cid}",
                "target": f"set:{sid}",
                "kind": "in_set",
                "label": ent["fields"].get("Role_in_Set", "")[:70],
                "day_index": day_idx_by_sid[sid],
            })

    # 4. Person nodes & role-typed credit edges
    for name, roles in people.items():
        pid = f"person:{slug(name)}"
        all_cids = sorted({c for cset in roles.values() for c in cset})
        person_sids = sorted({int(e["fields"]["Set"]) for c in all_cids for e in books_by_cid[c]})
        min_day = min((day_idx_by_sid[s] for s in person_sids), default=1)
        role_list = [r for r in role_order if r in roles]
        primary_role = role_list[0] if role_list else "author"
        add_node({
            "id": pid,
            "type": "person",
            "subtype": primary_role,
            "slug": person_path[name],
            "title": name,
            "roles": role_list,
            "book_cids": all_cids,
            "sets": person_sids,
            "cross_set_count": len(person_sids),
            "day_index": min_day,
        })
        for r in role_list:
            for cid in sorted(roles[r]):
                c_sids = [int(e["fields"]["Set"]) for e in books_by_cid[cid]]
                edges.append({
                    "source": pid,
                    "target": f"book:{cid}",
                    "kind": f"credit:{r}",
                    "role": r,
                    "label": r,
                    "day_index": min(day_idx_by_sid[s] for s in c_sids),
                })
        for wid, wf in weeks_by_id.items():
            if wf.get("Author") == name:
                edges.append({
                    "source": pid,
                    "target": f"week:{wid}",
                    "kind": "week_member",
                    "label": "Shadow Subject",
                    "day_index": node_by_id[f"week:{wid}"]["day_index"],
                })

    # 5. Shadow_Edges (citation / argument / translation / uncredited-use graph!)
    for se in shadow_edges_raw:
        sef = se["fields"]
        ccid = int(sef.get("Citing_Calibre_ID") or 0)
        tcid = int(sef.get("Target_Calibre_ID") or 0)
        sid = int(sef.get("Set") or 0)
        wid = int(sef.get("Week") or 1)
        rel = (sef.get("Relation") or "quotes").lower()
        if "argue" in rel or "dispute" in rel or "critic" in rel:
            ekind = "shadow:argues"
        elif "translat" in rel or "rewrit" in rel:
            ekind = "shadow:translates"
        elif "uncredit" in rel or "suspect" in rel:
            ekind = "shadow:uncredited"
        else:
            ekind = "shadow:quotes"
        src = f"book:{ccid}" if f"book:{ccid}" in node_by_id else None
        tgt = f"book:{tcid}" if f"book:{tcid}" in node_by_id else f"week:{wid}"
        if src and tgt in node_by_id:
            edges.append({
                "source": src,
                "target": tgt,
                "kind": ekind,
                "relation": sef.get("Relation", "quotes"),
                "quote": sef.get("Quote", ""),
                "location": sef.get("Location", ""),
                "verified": bool(sef.get("Verified", True)),
                "label": sef.get("Relation", "quotes"),
                "day_index": day_idx_by_sid.get(sid, 34),
            })

    # 6. Canonical Theme nodes (all themes recorded, with cross_set_count so UI filters >=2 by default!)
    theme_node_sets = {}
    stem_to_theme_ids = collections.defaultdict(list)
    cname_to_tid_node = {}

    for path, tids in by_slug.items():
        cname = theme_canonical_name[tids[0]]
        tslug = path.split("/", 1)[1]
        tid_node = f"theme:{tslug}"
        sids_direct = {s for t in tids for s in theme_sets.get(t, ())}
        cids_direct = {c for t in tids for c in theme_books.get(t, ())}
        sids_all = sorted(
            sids_direct | {int(e["fields"]["Set"]) for c in cids_direct for e in books_by_cid[c]}
        )
        if not sids_all:
            continue
        min_day = min(day_idx_by_sid[s] for s in sids_all)
        raw_aliases = sorted({themes[t] for t in tids if themes[t] != cname})
        toks = [
            re.sub(r"(ies)$", "y", re.sub(r"s$", "", w))
            for w in re.findall(r"[A-Za-zÀ-ÿ]{4,}", cname.lower())
            if w not in STOP_STEMS
        ]
        primary_stem = toks[0] if toks else tslug
        add_node({
            "id": tid_node,
            "type": "theme",
            "subtype": "bridge_theme" if len(sids_all) >= 2 else "leaf_theme",
            "slug": path,
            "title": cname,
            "aliases": raw_aliases,
            "sets": sids_all,
            "book_cids": sorted(cids_direct),
            "cross_set_count": len(sids_all),
            "stem": primary_stem,
            "day_index": min_day,
        })
        theme_node_sets[tid_node] = sids_all
        cname_to_tid_node[cname.lower()] = tid_node
        for st in set(toks):
            stem_to_theme_ids[st].append(tid_node)

        for s in sorted(sids_direct):
            edges.append({
                "source": f"set:{s}",
                "target": tid_node,
                "kind": "has_theme",
                "day_index": day_idx_by_sid[s],
            })
        for c in sorted(cids_direct):
            c_min_day = min(day_idx_by_sid[int(e["fields"]["Set"])] for e in books_by_cid[c])
            edges.append({
                "source": f"book:{c}",
                "target": tid_node,
                "kind": "has_theme",
                "day_index": c_min_day,
            })

    # Derived Set <-> Set shared_theme edges (for pure Set-level / Moretti projection!)
    set_themes_map = collections.defaultdict(set)
    for tid_node, sids_all in theme_node_sets.items():
        if len(sids_all) >= 2:
            tname = node_by_id[tid_node]["title"]
            for s in sids_all:
                set_themes_map[s].add(tname)

    all_sids = [sid for sid, _ in ordered]
    for i in range(len(all_sids)):
        for j in range(i + 1, len(all_sids)):
            s1, s2 = all_sids[i], all_sids[j]
            shared = sorted(set_themes_map[s1] & set_themes_map[s2])
            if shared:
                edges.append({
                    "source": f"set:{s1}",
                    "target": f"set:{s2}",
                    "kind": "shared_theme",
                    "weight": len(shared),
                    "themes": shared,
                    "label": ", ".join(shared[:3]),
                    "day_index": max(day_idx_by_sid[s1], day_idx_by_sid[s2]),
                })

    # Concept-stem kinship edges between cross-set hub themes and compound themes
    for st, tnodes in stem_to_theme_ids.items():
        if 2 <= len(tnodes) <= 10:
            # Find anchor theme (highest cross_set_count, shortest title)
            anchor = sorted(tnodes, key=lambda tid: (-node_by_id[tid]["cross_set_count"], len(node_by_id[tid]["title"])))[0]
            if node_by_id[anchor]["cross_set_count"] >= 2:
                for other in tnodes:
                    if other != anchor:
                        edges.append({
                            "source": anchor,
                            "target": other,
                            "kind": "concept_stem",
                            "stem": st,
                            "label": f"stem: {st}",
                            "day_index": max(node_by_id[anchor]["day_index"], node_by_id[other]["day_index"]),
                        })

    # 7. WordNet OEWN Synset Kinship edges
    for wk in overlay.get("wordnet_kinships", []):
        ta = cname_to_tid_node.get(wk["theme_a"].lower()) or f"theme:{slug(wk['theme_a'])}"
        tb = cname_to_tid_node.get(wk["theme_b"].lower()) or f"theme:{slug(wk['theme_b'])}"
        if ta in node_by_id and tb in node_by_id:
            edges.append({
                "source": ta,
                "target": tb,
                "kind": "wordnet_kin",
                "meet_at": wk["meet_at"],
                "wu_palmer": wk["wu_palmer"],
                "synset_path": wk["path"],
                "label": f"WordNet LCA: {wk['meet_at'].split(' (')[0]} ({wk['wu_palmer']:.2f})",
                "day_index": max(node_by_id[ta]["day_index"], node_by_id[tb]["day_index"]),
            })

    # 8. Scribrarium Ghost-Shelf Bridge nodes (live FTS5 discoveries across the 13,268-volume library!)
    for gb in overlay.get("scribrarium_ghost_bridges", []):
        if int(gb["calibre_id"]) in books_by_cid:
            # Graduated from Ghost Shelf to an officially resurfaced Triomnibus volume!
            continue
        gid = f"ghost:{gb['calibre_id']}"
        linked_pids = [f"person:{slug(p)}" for p in gb.get("bridges_people", []) if f"person:{slug(p)}" in node_by_id]
        min_day = max((node_by_id[pid]["day_index"] for pid in linked_pids), default=33)
        add_node({
            "id": gid,
            "type": "ghost_book",
            "subtype": "ghost_book",
            "calibre_id": gb["calibre_id"],
            "slug": None,
            "title": gb["title"],
            "author_credit": gb["author"],
            "format": gb.get("format", "EPUB"),
            "bridges_people": gb.get("bridges_people", []),
            "fts_snippet": gb.get("fts_snippet", ""),
            "why_bridge": gb.get("why_bridge", ""),
            "day_index": min_day,
        })
        for pid in linked_pids:
            edges.append({
                "source": gid,
                "target": pid,
                "kind": "ghost_bridge",
                "label": "Scribrarium FTS mention",
                "fts_snippet": gb.get("fts_snippet", ""),
                "day_index": min_day,
            })

    # ---- Compute Exact Hage & Harary / Moretti Metrics on the Core Structural Backbone
    # Core backbone = Sets + Author_Weeks + Books + People + Bridge Themes (cross_set_count >= 2)
    core_ids = [
        nd["id"]
        for nd in nodes
        if nd["type"] in ("set", "author_week", "book", "person")
        or (nd["type"] == "theme" and nd.get("cross_set_count", 0) >= 2)
    ]
    core_set = set(core_ids)
    CORE_EDGE_KINDS = {
        "in_set",
        "week_member",
        "credit:author",
        "credit:translator",
        "credit:editor",
        "credit:contributor",
        "shadow:quotes",
        "shadow:argues",
        "shadow:translates",
        "shadow:uncredited",
        "has_theme",
    }
    core_pairs = [
        (e["source"], e["target"])
        for e in edges
        if e["kind"] in CORE_EDGE_KINDS and e["source"] in core_set and e["target"] in core_set
    ]
    metrics = compute_graph_metrics(core_ids, core_pairs)

    for nd in nodes:
        nid = nd["id"]
        nd["degree"] = metrics["degree"].get(nid, 1)
        nd["betweenness"] = metrics["betweenness"].get(nid, 0.0)
        nd["clustering"] = metrics["clustering"].get(nid, 0.0)
        nd["is_cutpoint"] = nid in metrics["cutpoints"]
        nd["is_macro_cutpoint"] = nid in metrics["macro_cutpoints"]
        nd["in_2core"] = nid in metrics["in_2core"]
        nd["component"] = metrics["component"].get(nid, 0)

    for e in edges:
        pair = tuple(sorted((e["source"], e["target"])))
        e["is_bridge"] = pair in metrics["bridges"]

    payload = {
        "meta": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_sets": len(ordered),
            "triad_count": len(triad_ordered),
            "shadow_set_count": len(shadow_ordered),
            "book_count": len(books_by_cid),
            "person_count": len(people),
            "canonical_theme_count": len(by_slug),
            "bridge_theme_count": sum(1 for nd in nodes if nd["type"] == "theme" and nd.get("cross_set_count", 0) >= 2),
            "ghost_bridge_count": sum(1 for nd in nodes if nd["type"] == "ghost_book"),
            "cutpoint_count": len(metrics["cutpoints"]),
            "macro_cutpoint_count": len(metrics["macro_cutpoints"]),
            "two_core_count": len(metrics["in_2core"]),
            "bridge_edge_count": len(metrics["bridges"]),
            "scribrarium_total_volumes": 13268,
            "scribrarium_fts_indexed": 12849,
            "scribrarium_annotations_total": 469,
            "canetti_mentions_total": max(mentions_count_by_week.values(), default=234),
            "wordnet_kinship_count": sum(1 for e in edges if e["kind"] == "wordnet_kin"),
        },
        "wordnet_kinships": overlay.get("wordnet_kinships", []),
        "nodes": nodes,
        "edges": edges,
    }

    STATIC_OUT.mkdir(parents=True, exist_ok=True)
    (STATIC_OUT / "triomnibus-graph.json").write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )

    print(
        f"sets={len(ordered)} (triads={len(triad_ordered)}, shadows={len(shadow_ordered)}) "
        f"books={len(books_by_cid)} people={len(people)} themes={len(by_slug)} "
        f"bridge_themes={payload['meta']['bridge_theme_count']} "
        f"cutpoints={payload['meta']['cutpoint_count']} edges={len(edges)}"
    )


if __name__ == "__main__":
    main()
