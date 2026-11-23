"""Convert the daily resurfacing PDFs (pandoc + xelatex, DejaVu Serif / Latin Modern) back into Markdown.

The PDFs share one layout, so structure is recovered from typography and booktabs rules:
  size >= 16  -> document title (dropped; the triad/shadow page has its own)
  size 11-16  -> section / book heading (##)
  x0 ~ 72     -> body text; vertical gaps separate paragraphs
  x0 >= 90    -> block quotation (unless continuing a list item)
  "•" / "1."  -> list items
  booktabs horizontal rules (width > 350pt) -> deterministic table boundaries (supports 2, 3, 4+ cols & multi-page longtables)
  "From | To | What passes between them" -> fallback constellation table detector
  image blocks -> PNG files, referenced in place
Line-break hyphens are removed only when the joined word occurs unbroken elsewhere in the corpus.
Usage: python3 pdf2md.py PDF_DIR OUT_MD_DIR OUT_IMG_DIR
"""
import collections
import pathlib
import re
import sys
import pymupdf

try:
    from wordfreq import zipf_frequency  # type: ignore[import-not-found]
except ImportError:
    _DICT_PATH = pathlib.Path("/usr/share/dict/words")
    _DICT_WORDS = (
        {w.strip().lower() for w in _DICT_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()}
        if _DICT_PATH.exists()
        else set()
    )

    def zipf_frequency(word: str, _lang: str = "en") -> float:
        return 3.0 if word.lower() in _DICT_WORDS else 0.0

BODY_X = 72
QUOTE_X = 90
BOLD, ITALIC = 16, 2


def spans_to_md(spans):
    """Render spans, wrapping runs that sit on a link annotation as [text](uri)."""
    runs, cur_u, buf = [], object(), []
    for sp in spans:
        u = sp.get("uri")
        if u != cur_u and buf:
            runs.append((cur_u, buf))
            buf = []
        cur_u = u
        buf.append(sp)
    if buf:
        runs.append((cur_u, buf))
    out = []
    for u, group in runs:
        t = _style(group)
        txt = "".join(s["text"] for s in group).strip()
        if u and txt.rstrip(".,;)").replace(" ", "") not in u and not u.startswith("mailto") and t.strip():
            lead = t[: len(t) - len(t.lstrip())]
            trail = t[len(t.rstrip()) :]
            t = f"{lead}[{t.strip()}]({u}){trail}"
        out.append(t)
    return "".join(out)


def _style(spans):
    out, cur, style = [], [], None

    def flush():
        if not cur:
            return
        t = "".join(cur)
        core = t.strip()
        if not core:
            out.append(t)
            return
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()) :]
        if style == "bi":
            core = f"***{core}***"
        elif style == "b":
            core = f"**{core}**"
        elif style == "i":
            core = f"*{core}*"
        out.append(lead + core + trail)

    for sp in spans:
        f = sp["flags"]
        fn = sp["font"]
        b = bool(f & BOLD) or "Bold" in fn
        i = bool(f & ITALIC) or "Italic" in fn or "Oblique" in fn
        st = "bi" if (b and i) else "b" if b else "i" if i else None
        if st != style:
            flush()
            cur = []
            style = st
        cur.append(sp["text"])
    flush()
    s = "".join(out)
    for m in ("***", "**", "*"):
        s = re.sub(re.escape(m) + r"(\s+)" + re.escape(m), r"\1", s)
    return s


def plain(spans):
    return "".join(sp["text"] for sp in spans)


def get_booktabs_zones(page):
    """Return list of (y_top, y_mid, y_bot) for booktabs tables on this page (rule width > 350pt)."""
    horiz = sorted(
        set(
            round(d["rect"].y0, 1)
            for d in page.get_drawings()
            if abs(d["rect"].y1 - d["rect"].y0) < 2 and (d["rect"].x1 - d["rect"].x0) > 350
        )
    )
    zones = []
    i = 0
    while i + 1 < len(horiz):
        if 12 <= (horiz[i + 1] - horiz[i]) <= 42:
            y_top, y_mid = horiz[i], horiz[i + 1]
            if i + 2 < len(horiz):
                y_bot = horiz[i + 2]
                zones.append((y_top, y_mid, y_bot))
                i += 3
            else:
                zones.append((y_top, y_mid, 735.0))
                i += 2
        else:
            i += 1
    return zones


def extract_booktabs_table(page_lines, y_top, y_mid, y_bot, wordset):
    """Extract (headers, formatted_rows, consumed_line_ids) for a single booktabs table zone."""
    hdr_lines = [l for l in page_lines if l["kind"] == "text" and (y_top - 2.0) <= l["y0"] <= (y_mid + 2.0)]
    body_lines = [l for l in page_lines if l["kind"] == "text" and (y_mid + 1.5) < l["y0"] <= (y_bot + 2.0)]
    consumed = {id(l) for l in hdr_lines} | {id(l) for l in body_lines}
    if not hdr_lines:
        return None, [], consumed

    hdr_sorted_x = sorted(hdr_lines, key=lambda l: l["x0"])
    cols = []
    for l in hdr_sorted_x:
        txt = spans_to_md(l["spans"]).strip()
        if not cols or abs(l["x0"] - cols[-1][0]) > 12:
            cols.append([l["x0"], [(l["y0"], txt)]])
        else:
            cols[-1][1].append((l["y0"], txt))

    col_x = [c[0] for c in cols]
    col_widths = [
        (col_x[k + 1] - col_x[k] - 10.0) if k + 1 < len(col_x) else (535.0 - col_x[k])
        for k in range(len(col_x))
    ]
    headers = [" ".join(t for _, t in sorted(c[1], key=lambda item: item[0])) for c in cols]
    headers = [re.sub(r"\*+", "", h).strip() for h in headers]

    y_groups = []
    for l in sorted(body_lines, key=lambda item: (item["y0"], item["x0"])):
        if not y_groups or abs(l["y0"] - y_groups[-1][0]) > 2.0:
            y_groups.append([l["y0"], [l]])
        else:
            y_groups[-1][1].append(l)

    rows = []
    prev_col_widths = {}
    prev_col_map = {}
    prev_y0 = None
    for y0, grp in y_groups:
        col_map = {}
        cur_col_widths = {}
        for l in grp:
            x = l["x0"]
            x1 = max(s["bbox"][2] for s in l["spans"])
            c_idx = max((k for k in range(len(col_x)) if col_x[k] - 8 <= x), default=0)
            cell_txt = spans_to_md(l["spans"]).strip()
            col_map[c_idx] = (col_map.get(c_idx, "") + " " + cell_txt).strip()
            cur_col_widths[c_idx] = max(cur_col_widths.get(c_idx, 0.0), x1 - col_x[c_idx])

        is_new_row = False
        if not rows:
            is_new_row = True
        elif prev_y0 is not None and (y0 - prev_y0) > 13.5:
            is_new_row = True
        elif len(col_map) < len(col_x):
            if 0 not in col_map and len(col_x) == 3 and 1 in col_map and 2 in col_map:
                if 1 not in prev_col_widths or prev_col_widths.get(1, 0.0) < 0.65 * col_widths[1]:
                    is_new_row = True
        else:
            if any(c_idx not in prev_col_widths for c_idx in range(len(col_x))):
                is_new_row = True
            else:
                wide_cols = [k for k in range(len(col_x)) if col_widths[k] >= 105.0] or list(range(len(col_x)))
                if any(re.sub(r"^[*_`]+\s*", "", col_map[k]).startswith("(") for k in col_map):
                    is_new_row = False
                elif any(prev_col_widths.get(k, 0.0) < 0.65 * col_widths[k] for k in wide_cols):
                    is_new_row = True
                else:
                    c0_clean = re.sub(r"^[*_`“\"'‘]+", "", col_map.get(0, "")).strip()
                    c1_clean = re.sub(r"^[*_`“\"'‘]+", "", col_map.get(1, "")).strip() if len(col_x) > 1 else "X"
                    p0_clean = re.sub(r"[*_`]+", "", prev_col_map.get(0, "")).strip()
                    if (
                        c0_clean
                        and not c0_clean[0].islower()
                        and c1_clean
                        and not c1_clean[0].islower()
                        and not any(
                            re.sub(r"^[*_`“\"'‘]+", "", col_map.get(k, ""))[0].islower()
                            for k in wide_cols
                            if re.sub(r"^[*_`“\"'‘]+", "", col_map.get(k, ""))
                        )
                        and not re.search(r"\b(the|a|an|of|in|to|and|or|de|for|with|his)$", p0_clean, re.I)
                    ):
                        is_new_row = True

        if is_new_row:
            new_r = [[] for _ in col_x]
            for c_idx, val in col_map.items():
                new_r[c_idx].append(val)
            rows.append(new_r)
        else:
            for c_idx, val in col_map.items():
                rows[-1][c_idx].append(val)
        prev_col_widths = cur_col_widths
        prev_col_map = col_map
        prev_y0 = y0

    formatted_rows = []
    for r in rows:
        formatted_rows.append([join_lines(cell_lines, wordset).replace("|", "/") for cell_lines in r])
    return headers, formatted_rows, consumed


def collect_lines(doc, page_indices=None):
    lines = []
    target_pages = page_indices if page_indices is not None else range(len(doc))
    for pno in target_pages:
        page = doc[pno]
        links = [(pymupdf.Rect(lk["from"]), lk["uri"]) for lk in page.get_links() if lk.get("uri")]
        d = page.get_text("dict")
        for b in d["blocks"]:
            if b["type"] == 1:
                lines.append(
                    {
                        "kind": "img",
                        "page": pno,
                        "y0": b["bbox"][1],
                        "bbox": b["bbox"],
                        "image": b.get("image"),
                        "ext": b.get("ext", "png"),
                    }
                )
                continue
            for l in b["lines"]:
                spans = [s for s in l["spans"] if s["text"]]
                if not spans:
                    continue
                for sp in spans:
                    r = pymupdf.Rect(sp["bbox"])
                    for lr, u in links:
                        inter = r & lr
                        if not inter.is_empty and inter.get_area() > 0.5 * r.get_area():
                            sp["uri"] = u
                            break
                txt = plain(spans)
                if not txt.strip():
                    continue
                lines.append(
                    {
                        "kind": "text",
                        "page": pno,
                        "x0": l["bbox"][0],
                        "y0": l["bbox"][1],
                        "y1": l["bbox"][3],
                        "size": max(s["size"] for s in spans),
                        "spans": spans,
                        "text": txt,
                    }
                )
    lines.sort(key=lambda l: (l["page"], l["y0"], l.get("x0", 0)))
    texts = [l for l in lines if l["kind"] == "text"]
    for l in texts:
        l["row"] = [m for m in texts if m["page"] == l["page"] and abs(m["y0"] - l["y0"]) < 1.5]
    # drop page numbers (a lone number with nothing else on its line)
    return [
        l
        for l in lines
        if not (
            l["kind"] == "text"
            and re.fullmatch(r"\d{1,3}", l["text"].strip())
            and l["x0"] > 250
            and len(l["row"]) == 1
        )
    ]


def join_lines(parts, wordset):
    """Join wrapped lines, removing LaTeX line-break hyphens only when safe."""
    out = ""
    for p in parts:
        p = p.strip()
        if not out:
            out = p
            continue
        mm = re.search(r"-(\*{1,3})$", out)
        if mm and p.startswith(mm.group(1)):
            out = out[: -len(mm.group(1))]
            p = p[len(mm.group(1)) :]
        m = re.search(r"([A-Za-zÀ-ÿ]+)-$", out)
        n = re.match(r"[*]*([a-zà-ÿ]+)", p)
        if out.endswith("-") and m and n:
            joined = (m.group(1) + n.group(1)).lower()
            hyph = (m.group(1) + "-" + n.group(1)).lower()
            if hyph not in wordset and (joined in wordset or zipf_frequency(joined, "en") >= 1.5):
                out = out[:-1] + p
                continue
            out = out + p  # keep the hyphen, no space
            continue
        out = out + " " + p
    out = re.sub(r"\*\* \*\*", " ", out)
    out = re.sub(r"(?<!\*)\* \*(?!\*)", " ", out)
    return out


def convert_pages(doc, page_indices, wordset, img_dir, img_prefix):
    target_pages = list(page_indices) if page_indices is not None else list(range(len(doc)))
    lines = collect_lines(doc, target_pages)

    # Pre-extract booktabs tables on each page and replace their lines with synthetic "booktabs_table" items
    bt_consumed = set()
    bt_tables_by_first_line = {}
    for pno in target_pages:
        zones = get_booktabs_zones(doc[pno])
        if not zones:
            continue
        p_lines = [l for l in lines if l["page"] == pno]
        for z in zones:
            hdrs, t_rows, c_ids = extract_booktabs_table(p_lines, z[0], z[1], z[2], wordset)
            if hdrs and c_ids:
                bt_consumed |= c_ids
                first_l = next((l for l in p_lines if id(l) in c_ids), None)
                if first_l is not None:
                    bt_tables_by_first_line[id(first_l)] = (hdrs, t_rows)

    md, para, para_kind, prev = [], [], None, None
    table, in_table, col_x, skip_ids = [], False, None, set()
    img_n = 0

    def flush_para():
        nonlocal para, para_kind
        if para:
            text = join_lines(para, wordset)
            if para_kind == "quote":
                md.append("> " + text)
            elif para_kind == "li":
                md.append(text)
            elif para_kind == "h":
                md.append("## " + re.sub(r"^\*+|\*+$", "", text).strip())
            else:
                md.append(text)
            md.append("")
        para, para_kind = [], None

    header = []

    def flush_table():
        nonlocal table, in_table
        if table:
            n = len(col_x)
            md.append("| " + " | ".join(h.replace("|", "/") for h in header) + " |")
            md.append("|" + "---|" * n)
            for row in table:
                cells = [join_lines(c, wordset).replace("|", "/") for c in row]
                while len(cells) < n:
                    cells.append("")
                md.append("| " + " | ".join(cells[:n]) + " |")
            md.append("")
        table, in_table = [], False

    pending_bt_hdr = None
    pending_bt_rows = []

    def flush_bt_table():
        nonlocal pending_bt_hdr, pending_bt_rows
        if pending_bt_hdr:
            n = len(pending_bt_hdr)
            md.append("| " + " | ".join(h.replace("|", "/") for h in pending_bt_hdr) + " |")
            md.append("|" + "---|" * n)
            for r_cells in pending_bt_rows:
                row_padded = list(r_cells) + [""] * max(0, n - len(r_cells))
                md.append("| " + " | ".join(row_padded[:n]) + " |")
            md.append("")
        pending_bt_hdr = None
        pending_bt_rows = []

    for i, l in enumerate(lines):
        if id(l) in bt_tables_by_first_line:
            flush_para()
            if in_table:
                flush_table()
            hdrs, t_rows = bt_tables_by_first_line[id(l)]
            if pending_bt_hdr == hdrs:
                pending_bt_rows.extend(t_rows)
            else:
                flush_bt_table()
                pending_bt_hdr = hdrs
                pending_bt_rows = list(t_rows)
            prev = None
            continue
        if id(l) in bt_consumed:
            continue

        if pending_bt_hdr is not None:
            flush_bt_table()

        if l["kind"] == "img":
            flush_para()
            if in_table:
                flush_table()
            if l["image"] and img_dir is not None:
                img_n += 1
                name = f"{img_prefix}-{img_n}.{l['ext']}"
                (img_dir / name).write_bytes(l["image"])
                md.append(f"![]({name})")
                md.append("")
            prev = None
            continue
        t, x, size = l["text"].strip(), l["x0"], l["size"]
        if size >= 16:  # document title
            prev = l
            continue
        # Fallback table detector for tables without booktabs rules
        if (
            not in_table
            and len(l["row"]) >= 3
            and l is l["row"][0]
            and all(m["size"] <= 11 for m in l["row"])
            and min(m["x0"] for m in l["row"]) > BODY_X + 4
        ):
            flush_para()
            in_table = True
            hdr = sorted(l["row"], key=lambda m: m["x0"])
            col_x = [h["x0"] for h in hdr]
            header = [h["text"].strip() for h in hdr]
            skip_ids = {id(m) for m in hdr}
            prev = l
            continue
        if in_table:
            if id(l) in skip_ids:
                prev = l
                continue
            rowtexts = [m["text"].strip() for m in sorted(l["row"], key=lambda m: m["x0"])]
            if rowtexts == header:
                skip_ids |= {id(m) for m in l["row"]}
                prev = l
                continue
            if x < col_x[0] - 6 or size > 11 or re.match(r"^Figure \d+:", t):
                flush_table()
            else:
                col = max(k for k in range(len(col_x)) if col_x[k] - 5 <= x) if x >= col_x[0] - 5 else 0
                cell = spans_to_md(l["spans"]).strip()
                if col == 0:
                    table.append([[cell]] + [[] for _ in col_x[1:]])
                elif table:
                    table[-1][col].append(cell)
                else:
                    table.append([[] for _ in col_x])
                    table[-1][col].append(cell)
                prev = l
                continue
        content = spans_to_md(l["spans"]).strip()
        if size > 11:
            content = t
        same_line = (
            prev is not None
            and prev["kind"] == "text"
            and prev["page"] == l["page"]
            and abs(l["y0"] - prev["y0"]) < 2
        )
        if same_line and para:
            para[-1] = para[-1] + " " + content
            prev = l
            continue
        if re.match(r"^Figure \d+:", t):
            flush_para()
            md.append("*" + t + "*")
            md.append("")
            prev = l
            continue
        gap = (l["y0"] - prev["y1"]) if (prev and prev["kind"] == "text" and prev["page"] == l["page"]) else 99
        if size > 11:
            kind = "h"
        elif re.match(r"^[•◦‣–]\s", t):
            kind = "li"
            content = "- " + re.sub(r"^\**[•◦‣–]\s*", "", content)
        elif re.match(r"^\d+\.\s", t) and x > BODY_X + 5:
            kind = "li"
        elif x >= QUOTE_X:
            kind = "cont" if para_kind == "li" else "quote"
        else:
            kind = "body"
        new = (
            kind in ("h", "li")
            or (kind == "body" and para_kind != "body")
            or (kind == "quote" and para_kind != "quote")
            or (kind in ("body", "quote") and gap > 3.5)
            or (kind == "h" and para_kind != "h")
        )
        if kind == "h" and para_kind == "h" and gap < 4:
            new = False
        if kind == "cont":
            para.append(content)
            prev = l
            continue
        if new and kind == "body" and gap == 99:
            flush_para()
            idx = max(
                (
                    k
                    for k, v in enumerate(md)
                    if v
                    and not v.startswith(("!", "*Figure", ">", "#", "|", "-"))
                    and not re.match(r"^\d+\.", v)
                ),
                default=None,
            )
            if idx is not None and not re.search(r"[.?!:”\")]$", md[idx]):
                md[idx] = join_lines([md[idx], content], wordset)
                prev = l
                continue
        if new:
            flush_para()
            para_kind = kind
        para.append(content)
        prev = l
    flush_para()
    if in_table:
        flush_table()
    if pending_bt_hdr is not None:
        flush_bt_table()
    text = "\n".join(md)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    pat = re.compile(r"\[([^\]]*)\]\((\S+?)\)(\s+)\[([^\]]*)\]\(\2\)")
    while pat.search(text):
        text = pat.sub(lambda m: "[" + join_lines([m.group(1), m.group(4)], wordset) + "](" + m.group(2) + ")", text)
    uris = sorted(
        {lk["uri"] for pno in target_pages for lk in doc[pno].get_links() if lk.get("uri")},
        key=len,
        reverse=True,
    )
    for u in uris:
        if u in text:
            continue
        pat_u = "".join(re.escape(ch) + r"(?:-?\s+)?" for ch in u[:-1]) + re.escape(u[-1])
        text, _ = re.subn(pat_u, lambda m: u, text)
    return text


def convert(pdf, wordset, img_dir, img_prefix):
    doc = pymupdf.open(pdf)
    return convert_pages(doc, None, wordset, img_dir, img_prefix)


def build_wordset(pdfs):
    ws = set()
    for p in pdfs:
        for page in pymupdf.open(p):
            for w in re.findall(r"[A-Za-zÀ-ÿ]+(?:-[A-Za-zÀ-ÿ]+)*", page.get_text()):
                ws.add(w.lower())
    return ws


def main(pdf_dir, out_md, out_img):
    pdfs = sorted(pathlib.Path(pdf_dir).glob("resurfacing-*.pdf"))
    ws = build_wordset(pdfs)
    out_md, out_img = pathlib.Path(out_md), pathlib.Path(out_img)
    out_md.mkdir(parents=True, exist_ok=True)
    out_img.mkdir(parents=True, exist_ok=True)
    for p in pdfs:
        date = re.search(r"(\d{4}-\d{2}-\d{2})", p.name).group(1)
        md = convert(p, ws, out_img, date)
        (out_md / f"{date}.md").write_text(md, encoding="utf-8")
        print(date, len(md))


if __name__ == "__main__":
    main(*sys.argv[1:4])
