"""Convert the daily resurfacing PDFs (pandoc + xelatex, DejaVu Serif) back into Markdown.

The PDFs share one layout, so structure is recovered from typography:
  size >= 16  -> document title (dropped; the triad page has its own)
  size 11-16  -> section / book heading (##)
  x0 ~ 72     -> body text; vertical gaps separate paragraphs
  x0 >= 90    -> block quotation (unless continuing a list item)
  "•" / "1."  -> list items
  "From | To | What passes between them" -> the constellation table
  image blocks -> PNG files, referenced in place
Line-break hyphens are removed only when the joined word occurs unbroken elsewhere in the corpus.
Usage: python3 pdf2md.py PDF_DIR OUT_MD_DIR OUT_IMG_DIR
"""
import sys, re, pathlib, collections
import pymupdf
from wordfreq import zipf_frequency

BODY_X = 72
QUOTE_X = 90
BOLD, ITALIC = 16, 2

def spans_to_md(spans):
    """Render spans, wrapping runs that sit on a link annotation as [text](uri)."""
    runs, cur_u, buf = [], object(), []
    for sp in spans:
        u = sp.get("uri")
        if u != cur_u and buf:
            runs.append((cur_u, buf)); buf = []
        cur_u = u; buf.append(sp)
    if buf: runs.append((cur_u, buf))
    out = []
    for u, group in runs:
        t = _style(group)
        txt = "".join(s["text"] for s in group).strip()
        if u and txt.rstrip(".,;)").replace(" ", "") not in u and not u.startswith("mailto") and t.strip():
            lead = t[: len(t) - len(t.lstrip())]; trail = t[len(t.rstrip()):]
            t = f"{lead}[{t.strip()}]({u}){trail}"
        out.append(t)
    return "".join(out)

def _style(spans):
    out, cur, style = [], [], None
    def flush():
        if not cur: return
        t = "".join(cur)
        core = t.strip()
        if not core:
            out.append(t); return
        lead = t[: len(t) - len(t.lstrip())]
        trail = t[len(t.rstrip()):]
        if style == "bi": core = f"***{core}***"
        elif style == "b": core = f"**{core}**"
        elif style == "i": core = f"*{core}*"
        out.append(lead + core + trail)
    for sp in spans:
        f = sp["flags"]; fn = sp["font"]
        b = bool(f & BOLD) or "Bold" in fn
        i = bool(f & ITALIC) or "Italic" in fn or "Oblique" in fn
        st = "bi" if (b and i) else "b" if b else "i" if i else None
        if st != style:
            flush(); cur = []; style = st
        cur.append(sp["text"])
    flush()
    s = "".join(out)
    # merge adjacent identical markers split across spans: "*a* *b*" -> "*a b*"
    for m in ("***", "**", "*"):
        s = re.sub(re.escape(m) + r"(\s+)" + re.escape(m), r"\1", s)
    return s

def plain(spans):
    return "".join(sp["text"] for sp in spans)

def collect_lines(doc):
    lines = []
    for pno, page in enumerate(doc):
        links = [(pymupdf.Rect(lk["from"]), lk["uri"]) for lk in page.get_links() if lk.get("uri")]
        d = page.get_text("dict")
        for b in d["blocks"]:
            if b["type"] == 1:
                lines.append({"kind": "img", "page": pno, "y0": b["bbox"][1], "bbox": b["bbox"],
                              "image": b.get("image"), "ext": b.get("ext", "png")})
                continue
            for l in b["lines"]:
                spans = [s for s in l["spans"] if s["text"]]
                if not spans: continue
                for sp in spans:
                    r = pymupdf.Rect(sp["bbox"])
                    for lr, u in links:
                        inter = r & lr
                        if not inter.is_empty and inter.get_area() > 0.5 * r.get_area():
                            sp["uri"] = u; break
                txt = plain(spans)
                if not txt.strip(): continue
                lines.append({"kind": "text", "page": pno, "x0": l["bbox"][0], "y0": l["bbox"][1],
                              "y1": l["bbox"][3], "size": max(s["size"] for s in spans),
                              "spans": spans, "text": txt})
    lines.sort(key=lambda l: (l["page"], l["y0"], l.get("x0", 0)))
    texts = [l for l in lines if l["kind"] == "text"]
    for l in texts:
        l["row"] = [m for m in texts if m["page"] == l["page"] and abs(m["y0"] - l["y0"]) < 1.5]
    # drop page numbers (a lone number with nothing else on its line)
    return [l for l in lines if not (l["kind"] == "text" and re.fullmatch(r"\d{1,3}", l["text"].strip()) and l["x0"] > 250 and len(l["row"]) == 1)]

def join_lines(parts, wordset):
    """Join wrapped lines, removing LaTeX line-break hyphens only when safe."""
    out = ""
    for p in parts:
        p = p.strip()
        if not out:
            out = p; continue
        mm = re.search(r"-(\*{1,3})$", out)
        if mm and p.startswith(mm.group(1)):
            out = out[: -len(mm.group(1))]; p = p[len(mm.group(1)):]
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

def convert(pdf, wordset, img_dir, img_prefix):
    doc = pymupdf.open(pdf)
    lines = collect_lines(doc)
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
                while len(cells) < n: cells.append("")
                md.append("| " + " | ".join(cells[:n]) + " |")
            md.append("")
        table, in_table = [], False

    for i, l in enumerate(lines):
        if l["kind"] == "img":
            flush_para()
            if in_table: flush_table()
            if l["image"]:
                img_n += 1
                name = f"{img_prefix}-{img_n}.{l['ext']}"
                (img_dir / name).write_bytes(l["image"])
                md.append(f"![]({name})"); md.append("")
            prev = None
            continue
        t, x, size = l["text"].strip(), l["x0"], l["size"]
        if size >= 16:  # document title
            prev = l; continue
        # tables: a row of three or more cells, indented from the body text
        if not in_table and len(l["row"]) >= 3 and l is l["row"][0] and all(m["size"] <= 11 for m in l["row"]) and min(m["x0"] for m in l["row"]) > BODY_X + 4:
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
                prev = l; continue
            # repeated header after a page break
            rowtexts = [m["text"].strip() for m in sorted(l["row"], key=lambda m: m["x0"])]
            if rowtexts == header:
                skip_ids |= {id(m) for m in l["row"]}
                prev = l; continue
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
                    table.append([[] for _ in col_x]); table[-1][col].append(cell)
                prev = l
                continue
        content = spans_to_md(l["spans"]).strip()
        if size > 11:
            content = t
        same_line = prev is not None and prev["kind"] == "text" and prev["page"] == l["page"] and abs(l["y0"] - prev["y0"]) < 2
        if same_line and para:
            para[-1] = para[-1] + " " + content
            prev = l; continue
        if re.match(r"^Figure \d+:", t):
            flush_para(); md.append("*" + t + "*"); md.append(""); prev = l; continue
        gap = (l["y0"] - prev["y1"]) if (prev and prev["kind"] == "text" and prev["page"] == l["page"]) else 99
        if size > 11:
            kind = "h"
        elif re.match(r"^[•◦‣–]\s", t):
            kind = "li"; content = "- " + re.sub(r"^\**[•◦‣–]\s*", "", content)
        elif re.match(r"^\d+\.\s", t) and x > BODY_X + 5:
            kind = "li"
        elif x >= QUOTE_X:
            kind = "cont" if para_kind == "li" else "quote"
        else:
            kind = "body"
        new = (kind in ("h", "li") or
               (kind == "body" and para_kind != "body") or
               (kind == "quote" and para_kind != "quote") or
               (kind in ("body", "quote") and gap > 3.5) or
               (kind == "h" and para_kind != "h"))
        if kind == "h" and para_kind == "h" and gap < 4:
            new = False
        if kind == "cont":
            para.append(content); prev = l; continue
        if new and kind == "body" and gap == 99:
            flush_para()
            # continue the last body paragraph, which a page break (and maybe a float) interrupted
            idx = max((k for k, v in enumerate(md) if v and not v.startswith(("!", "*Figure", ">", "#", "|", "-")) and not re.match(r"^\d+\.", v)), default=None)
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
    if in_table: flush_table()
    text = "\n".join(md)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
    # merge links that a line break split into two
    pat = re.compile(r"\[([^\]]*)\]\((\S+?)\)(\s+)\[([^\]]*)\]\(\2\)")
    while pat.search(text):
        text = pat.sub(lambda m: "[" + join_lines([m.group(1), m.group(4)], wordset) + "](" + m.group(2) + ")", text)
    # repair URLs broken across lines, using the PDF's own link annotations
    uris = sorted({lk["uri"] for pg in doc for lk in pg.get_links() if lk.get("uri")}, key=len, reverse=True)
    for u in uris:
        if u in text: continue
        pat = "".join(re.escape(ch) + r"(?:-?\s+)?" for ch in u[:-1]) + re.escape(u[-1])
        text, n = re.subn(pat, lambda m: u, text)
    return text

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
    out_md.mkdir(parents=True, exist_ok=True); out_img.mkdir(parents=True, exist_ok=True)
    for p in pdfs:
        date = re.search(r"(\d{4}-\d{2}-\d{2})", p.name).group(1)
        md = convert(p, ws, out_img, date)
        (out_md / f"{date}.md").write_text(md, encoding="utf-8")
        print(date, len(md))

if __name__ == "__main__":
    main(*sys.argv[1:4])
