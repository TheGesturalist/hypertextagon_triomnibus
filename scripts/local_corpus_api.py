#!/usr/bin/env python3
"""
Hypertextagon Triomnibus — Local Corpus API Bridge (`127.0.0.1:18770`).

Zero-dependency Python `http.server` microservice enabling live local cross-resource
queries from the browser Graph Observatory (`https://triomnibus.thegesturalist.com`,
`http://localhost:8080`, or `file://`) against Johnny's private local Mac corpora:

  1. Read-Only Calibre `Scribrarium` SQLite Databases (`mode=ro` URI + `PRAGMA query_only=ON`):
     - `GET /api/health`
     - `GET /api/scribrarium/bridges?authors=A,B`
     - `GET /api/scribrarium/search?q=...`
     - `GET /api/scribrarium/annotations?book_id=...`
  2. Live Grist Lexicon (`jZT77mQfrBpwsy5Fkcn5AS`, `Terms` table):
     - `GET /api/lexicon?term=...`
  3. One-Click Park to Grist Parking Lot (`fjWnpuU31w9r2YnnGVMygW`, `Parking_Lot` table):
     - `GET  /api/parking-lot/sessions`
     - `POST /api/parking-lot` (auto-computing upcoming `Month_PLR1_YY` / `Month_PLR2_YY`
       target session — e.g. `Nov_PLR1_26` on Oct 29, 2026)
"""

from __future__ import annotations

import argparse
import datetime
import html
import http.server
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18770

GRIST_API_ROOT = "https://bstd.getgrist.com/api"
LEXICON_DOC_ID = "jZT77mQfrBpwsy5Fkcn5AS"
PARKING_LOT_DOC_ID = "fjWnpuU31w9r2YnnGVMygW"
PARKING_LOT_TABLE_ID = "Parking_Lot"

DEFAULT_CALIBRE_DIR = Path(
    "/Volumes/YOTUO/Locus Amoenus/Terra_Gesturalis_001/TG_Librarium_Veritatus/Scribrarium"
)
FALLBACK_METADATA_DBS = [
    Path.home() / ".gemini/antigravity/scratch/library-resurfacing/scribrarium_metadata_mirror.db",
    Path.home() / "mnt/Scribrarium/metadata.db",
]
FALLBACK_FTS_DBS = [
    Path.home() / ".gemini/antigravity/scratch/library-resurfacing/scribrarium_fts_mirror.db",
    Path.home() / "mnt/Scribrarium/full-text-search.db",
]

CONFIG_PATHS = [
    Path("/Users/themainframe/.gemini/antigravity/scratch/grist-lexicon-clipper/backend/config.json"),
    Path("/Users/themainframe/.gemini/antigravity/scratch/parking-lot-review/config.json"),
]

ALLOWED_CORS_ORIGINS: frozenset[str] = frozenset(
    {
        "https://triomnibus.thegesturalist.com",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "null",
    }
)

GENERIC_CALIBRE_TAGS_LOWER: frozenset[str] = frozenset(
    {
        "nonfiction",
        "fiction",
        "jlibrary",
        "tbr",
        "tier_one_tbr",
        "priority_tbr",
        "tops",
        "general",
        "books",
        "ebook",
        "epub",
        "pdf",
    }
)

MONTH_ABBR = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


# ---------------------------------------------------------------------------
# Parking Lot Review (PLR) Semi-Monthly Session Calculation
# ---------------------------------------------------------------------------


def format_plr_session_code(year: int, month: int, half: int) -> str:
    """Format `(year, month, half)` into `Month_PLR1_YY` or `Month_PLR2_YY`."""
    if not (1 <= month <= 12) or half not in (1, 2):
        raise ValueError(f"Invalid PLR session components: year={year}, month={month}, half={half}")
    return f"{MONTH_ABBR[month]}_PLR{half}_{year % 100:02d}"


def compute_plr_sessions(today: datetime.date | None = None, count: int = 6) -> dict[str, Any]:
    """
    Compute current and upcoming semi-monthly PLR session codes (1st and 15th of each month).

    Schedule rules (matching `plr_grist.py`):
      - Days  1..14 -> current session is `Month_PLR1_YY`; next upcoming is `Month_PLR2_YY`.
      - Days 15..31 -> current session is `Month_PLR2_YY`; next upcoming is `NextMonth_PLR1_YY`.
      - Example: Oct 29, 2026 -> current `Oct_PLR2_26`, recommended upcoming `Nov_PLR1_26`.
    """
    if today is None:
        today = datetime.date.today()

    year, month = today.year, today.month
    half = 1 if today.day <= 14 else 2

    sessions: list[str] = []
    y, m, h = year, month, half
    for _ in range(count + 1):
        sessions.append(format_plr_session_code(y, m, h))
        if h == 1:
            h = 2
        else:
            h = 1
            m += 1
            if m > 12:
                m = 1
                y += 1

    return {
        "today": today.isoformat(),
        "current_session": sessions[0],
        "recommended_target_session": sessions[1],
        "upcoming_sessions": sessions[1:],
    }


def compute_upcoming_plr_session(today: datetime.date | None = None) -> str:
    """Return the next upcoming `Month_PLR1_YY` / `Month_PLR2_YY` target session code."""
    return str(compute_plr_sessions(today=today, count=2)["recommended_target_session"])


# ---------------------------------------------------------------------------
# Strictly Read-Only SQLite Helpers
# ---------------------------------------------------------------------------


def build_ro_sqlite_uri(db_path: Path) -> str:
    """Construct an RFC-3986 SQLite `file:...` URI enforcing `mode=ro`."""
    resolved = str(db_path.resolve())
    quoted = urllib.parse.quote(resolved, safe="/:")
    return f"file:{quoted}?mode=ro"


def open_sqlite_ro(db_path: Path) -> sqlite3.Connection:
    """
    Open a SQLite database strictly in read-only URI mode (`?mode=ro`) and set
    `PRAGMA query_only = ON` so zero writes can ever occur.
    """
    if not db_path.exists():
        raise FileNotFoundError(f"SQLite DB not found: {db_path}")
    uri = build_ro_sqlite_uri(db_path)
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON;")
    return conn


def _clean_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", raw_html)
    s = re.sub(r"(?i)</p\s*>", "\n\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


# ---------------------------------------------------------------------------
# Grist Client Helper
# ---------------------------------------------------------------------------


def get_api_key(config_paths: list[Path] | None = None) -> str:
    """Resolve `GRIST_API_KEY` from environment or local config.json files."""
    env_key = os.environ.get("GRIST_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in config_paths or CONFIG_PATHS:
        if path.exists():
            try:
                cfg = json.loads(path.read_text(encoding="utf-8"))
                k = str(cfg.get("api_key") or "").strip()
                if k:
                    return k
            except Exception:
                continue
    return ""


def default_grist_requester(method: str, path: str, payload: Any = None) -> dict[str, Any]:
    """Authenticated Grist HTTP client (`urllib.request` with `/usr/bin/curl` fallback)."""
    api_key = get_api_key()
    if not api_key:
        raise RuntimeError("GRIST_API_KEY is not configured in environment or config.json.")

    url = f"{GRIST_API_ROOT.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body_str = json.dumps(payload) if payload is not None else None
    data = body_str.encode("utf-8") if body_str is not None else None

    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as http_exc:
        err_body = http_exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Grist HTTP {http_exc.code} on {method} {url}: {err_body[:300]}") from http_exc
    except Exception as exc:
        curl_bin = "/usr/bin/curl" if Path("/usr/bin/curl").exists() else shutil.which("curl")
        if not curl_bin:
            raise
        cmd = [
            curl_bin,
            "-sS",
            "--fail-with-body",
            "--max-time",
            "25",
            "-X",
            method,
            "-H",
            f"Authorization: Bearer {api_key}",
            "-H",
            "Content-Type: application/json",
            "-H",
            "Accept: application/json",
        ]
        if body_str is not None:
            cmd.extend(["--data-binary", "@-"])
        cmd.append(url)
        proc = subprocess.run(cmd, input=body_str, text=True, capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Grist curl fallback failed ({proc.returncode}): {proc.stderr.strip()}"
            ) from exc
        out = proc.stdout.strip()
        return json.loads(out) if out else {}


# ---------------------------------------------------------------------------
# Core Corpus Query Service
# ---------------------------------------------------------------------------


class LocalCorpusService:
    """Read-only Scribrarium + live Grist Lexicon & Parking Lot query service."""

    def __init__(
        self,
        *,
        calibre_dir: Path = DEFAULT_CALIBRE_DIR,
        metadata_db_path: Path | None = None,
        fts_db_path: Path | None = None,
        offline_snapshot: Path | None = None,
        grist_requester: Callable[[str, str, Any], dict[str, Any]] | None = None,
        today_provider: Callable[[], datetime.date] | None = None,
    ) -> None:
        self.calibre_dir = calibre_dir
        self._explicit_metadata_db = metadata_db_path
        self._explicit_fts_db = fts_db_path
        self.offline_snapshot = offline_snapshot
        self.grist_requester = grist_requester or default_grist_requester
        self.today_provider = today_provider or datetime.date.today

    def resolve_metadata_db(self) -> Path | None:
        candidates: list[Path] = []
        if self._explicit_metadata_db is not None:
            candidates.append(self._explicit_metadata_db)
        candidates.append(self.calibre_dir / "metadata.db")
        candidates.extend(FALLBACK_METADATA_DBS)
        for cand in candidates:
            try:
                if cand.exists():
                    conn = open_sqlite_ro(cand)
                    try:
                        conn.execute("SELECT 1 FROM books LIMIT 1").fetchall()
                    finally:
                        conn.close()
                    return cand
            except Exception:
                continue
        return None

    def resolve_fts_db(self) -> Path | None:
        candidates: list[Path] = []
        if self._explicit_fts_db is not None:
            candidates.append(self._explicit_fts_db)
        candidates.append(self.calibre_dir / "full-text-search.db")
        candidates.extend(FALLBACK_FTS_DBS)
        for cand in candidates:
            try:
                if cand.exists():
                    conn = open_sqlite_ro(cand)
                    try:
                        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
                    finally:
                        conn.close()
                    return cand
            except Exception:
                continue
        return None

    def _enrich_books_map(self, mconn: sqlite3.Connection, book_ids: list[int]) -> dict[int, dict[str, Any]]:
        unique_ids = sorted({int(b) for b in book_ids if b is not None})
        if not unique_ids:
            return {}
        placeholders = ",".join("?" for _ in unique_ids)
        rows = mconn.execute(
            f"SELECT id, title, pubdate, series_index FROM books WHERE id IN ({placeholders})",
            unique_ids,
        ).fetchall()

        books_map: dict[int, dict[str, Any]] = {}
        for r in rows:
            bid = int(r["id"])
            pub = str(r["pubdate"] or "")[:4]
            year = int(pub) if pub.isdigit() and pub != "0101" else None
            books_map[bid] = {
                "book_id": bid,
                "title": r["title"],
                "year": year,
                "authors": [],
                "tags": [],
                "series": None,
            }

        if not books_map:
            return {}

        for r in mconn.execute(
            f"""
            SELECT l.book AS book_id, a.name AS author_name
            FROM books_authors_link l
            JOIN authors a ON a.id = l.author
            WHERE l.book IN ({placeholders})
            ORDER BY l.id
            """,
            unique_ids,
        ).fetchall():
            bid = int(r["book_id"])
            if bid in books_map:
                books_map[bid]["authors"].append(str(r["author_name"]).replace("|", ", "))

        # Optional tables (`books_tags_link`, `books_series_link`) gracefully queried if present
        existing_tables = {
            row["name"]
            for row in mconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if {"books_tags_link", "tags"} <= existing_tables:
            for r in mconn.execute(
                f"""
                SELECT l.book AS book_id, t.name AS tag_name
                FROM books_tags_link l
                JOIN tags t ON t.id = l.tag
                WHERE l.book IN ({placeholders})
                """,
                unique_ids,
            ).fetchall():
                bid = int(r["book_id"])
                if bid in books_map:
                    books_map[bid]["tags"].append(str(r["tag_name"]))

        if {"books_series_link", "series"} <= existing_tables:
            for r in mconn.execute(
                f"""
                SELECT l.book AS book_id, s.name AS series_name
                FROM books_series_link l
                JOIN series s ON s.id = l.series
                WHERE l.book IN ({placeholders})
                """,
                unique_ids,
            ).fetchall():
                bid = int(r["book_id"])
                if bid in books_map:
                    books_map[bid]["series"] = str(r["series_name"])

        return books_map

    # -----------------------------------------------------------------------
    # Endpoint Implementations
    # -----------------------------------------------------------------------

    def get_health(self) -> dict[str, Any]:
        meta_db = self.resolve_metadata_db()
        fts_db = self.resolve_fts_db()
        book_count = 0
        if meta_db is not None:
            conn = open_sqlite_ro(meta_db)
            try:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM books").fetchone()
                book_count = int(row["cnt"]) if row else 0
            finally:
                conn.close()

        plr = compute_plr_sessions(today=self.today_provider(), count=4)
        return {
            "status": "ok",
            "service": "triomnibus-local-corpus-api",
            "scribrarium": {
                "metadata_db_available": meta_db is not None,
                "fts_db_available": fts_db is not None,
                "metadata_db_path": str(meta_db) if meta_db else None,
                "fts_db_path": str(fts_db) if fts_db else None,
                "read_only": True,
                "book_count": book_count,
            },
            "grist": {
                "api_key_configured": bool(get_api_key()),
                "lexicon_doc_id": LEXICON_DOC_ID,
                "parking_lot_doc_id": PARKING_LOT_DOC_ID,
                "offline_snapshot": str(self.offline_snapshot) if self.offline_snapshot else None,
            },
            "plr": plr,
        }

    def get_annotations(
        self,
        *,
        book_id: int | None = None,
        query: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        meta_db = self.resolve_metadata_db()
        if meta_db is None:
            return {"available": False, "total": 0, "annotations": [], "error": "Scribrarium metadata.db unavailable"}

        limit = max(1, min(int(limit), 500))
        conn = open_sqlite_ro(meta_db)
        try:
            tables = {
                r["name"]
                for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "annotations" not in tables:
                return {"available": True, "total": 0, "annotations": []}

            clauses: list[str] = []
            params: list[Any] = []
            if book_id is not None:
                clauses.append("a.book = ?")
                params.append(int(book_id))
            if query.strip():
                clauses.append("(a.searchable_text LIKE ? OR a.annot_data LIKE ?)")
                like_q = f"%{query.strip()}%"
                params.extend([like_q, like_q])

            where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            sql = f"""
                SELECT a.id, a.book, b.title AS book_title, a.format, a.annot_type, a.annot_data, a.timestamp
                FROM annotations a
                LEFT JOIN books b ON b.id = a.book
                {where_sql}
                ORDER BY a.timestamp DESC
                LIMIT ?
            """
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()

            results: list[dict[str, Any]] = []
            for row in rows:
                try:
                    data = json.loads(row["annot_data"] or "{}")
                except Exception:
                    data = {}
                if data.get("removed") is True:
                    continue
                toc_titles = data.get("toc_family_titles")
                if isinstance(toc_titles, list) and toc_titles:
                    chapter = " / ".join(str(t) for t in toc_titles if t)
                else:
                    chapter = str(data.get("title") or "")
                note_val = data.get("notes") if data.get("notes") is not None else data.get("note")
                results.append(
                    {
                        "annotation_id": int(row["id"]),
                        "book_id": int(row["book"]),
                        "title": row["book_title"] or "",
                        "format": row["format"],
                        "type": row["annot_type"],
                        "highlighted_text": data.get("highlighted_text") or "",
                        "note": note_val if note_val not in ("", None) else None,
                        "chapter": chapter,
                        "timestamp": data.get("timestamp") or row["timestamp"],
                    }
                )
            return {"available": True, "total": len(results), "annotations": results}
        finally:
            conn.close()

    def search_scribrarium(self, query: str, *, limit: int = 25) -> dict[str, Any]:
        q = (query or "").strip()
        if not q:
            raise ValueError("Query parameter 'q' is required.")
        limit = max(1, min(int(limit), 100))

        meta_db = self.resolve_metadata_db()
        if meta_db is None:
            return {"available": False, "query": q, "total": 0, "results": [], "error": "Scribrarium metadata.db unavailable"}

        fts_db = self.resolve_fts_db()
        mconn = open_sqlite_ro(meta_db)
        try:
            like_q = f"%{q}%"
            existing_tables = {
                r["name"]
                for r in mconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }

            hit_Order: list[int] = []
            hit_sources: dict[int, list[dict[str, str]]] = {}

            def _record_hit(bid: int, source: str, snippet: str) -> None:
                if bid not in hit_sources:
                    hit_Order.append(bid)
                    hit_sources[bid] = []
                hit_sources[bid].append({"source": source, "snippet": snippet})

            # 1. Metadata matches (title, author, tag, comments/synopsis)
            for r in mconn.execute(
                "SELECT id, title FROM books WHERE title LIKE ? LIMIT ?",
                (like_q, limit),
            ).fetchall():
                _record_hit(int(r["id"]), "title", str(r["title"]))

            for r in mconn.execute(
                """
                SELECT l.book AS book_id, a.name AS author_name
                FROM books_authors_link l
                JOIN authors a ON a.id = l.author
                WHERE a.name LIKE ?
                LIMIT ?
                """,
                (like_q, limit),
            ).fetchall():
                _record_hit(int(r["book_id"]), "author", str(r["author_name"]).replace("|", ", "))

            if {"books_tags_link", "tags"} <= existing_tables:
                for r in mconn.execute(
                    """
                    SELECT l.book AS book_id, t.name AS tag_name
                    FROM books_tags_link l
                    JOIN tags t ON t.id = l.tag
                    WHERE t.name LIKE ?
                    LIMIT ?
                    """,
                    (like_q, limit),
                ).fetchall():
                    _record_hit(int(r["book_id"]), "tag", str(r["tag_name"]))

            if "comments" in existing_tables:
                for r in mconn.execute(
                    "SELECT book, text FROM comments WHERE text LIKE ? LIMIT ?",
                    (like_q, limit),
                ).fetchall():
                    cleaned = _clean_html(str(r["text"]))
                    pos = cleaned.lower().find(q.lower())
                    start = max(0, pos - 90) if pos >= 0 else 0
                    _record_hit(int(r["book"]), "synopsis", cleaned[start : start + 220])

            # 2. Annotation matches
            if "annotations" in existing_tables:
                for r in mconn.execute(
                    """
                    SELECT book, annot_data, searchable_text
                    FROM annotations
                    WHERE searchable_text LIKE ? OR annot_data LIKE ?
                    LIMIT ?
                    """,
                    (like_q, like_q, limit),
                ).fetchall():
                    try:
                        ad = json.loads(r["annot_data"] or "{}")
                    except Exception:
                        ad = {}
                    if ad.get("removed") is True:
                        continue
                    txt = str(ad.get("highlighted_text") or r["searchable_text"] or "").strip()
                    _record_hit(int(r["book"]), "annotation", txt[:240])

            # 3. Full-text search (`full-text-search.db`)
            fts_engine = "unavailable"
            if fts_db is not None:
                fconn = open_sqlite_ro(fts_db)
                try:
                    fts_engine = self._query_fts_hits(fconn, q, limit, _record_hit)
                finally:
                    fconn.close()

            selected_ids = hit_Order[:limit]
            enriched = self._enrich_books_map(mconn, selected_ids)

            results: list[dict[str, Any]] = []
            for bid in selected_ids:
                info = dict(enriched.get(bid, {"book_id": bid, "title": f"Book #{bid}", "authors": [], "tags": []}))
                info["matches"] = hit_sources.get(bid, [])
                results.append(info)

            return {
                "available": True,
                "query": q,
                "fts_engine": fts_engine,
                "total": len(results),
                "results": results,
            }
        finally:
            mconn.close()

    @staticmethod
    def _query_fts_hits(
        fconn: sqlite3.Connection,
        query: str,
        limit: int,
        record_cb: Callable[[int, str, str], None],
    ) -> str:
        """
        Attempt FTS5 `books_fts MATCH` first; if SQLite lacks Calibre's custom C tokenizer
        (`no such tokenizer: calibre`), fall back seamlessly to `books_text` `LIKE` + `INSTR`.
        """
        tables = {
            r["name"]
            for r in fconn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'view')").fetchall()
        }
        if "books_fts" in tables and "books_text" in tables:
            try:
                safe_fts_q = '"' + query.replace('"', '""') + '"'
                rows = fconn.execute(
                    """
                    SELECT bt.book, bt.format,
                           snippet(books_fts, 0, '[', ']', ' … ', 24) AS snip
                    FROM books_fts
                    JOIN books_text bt ON bt.id = books_fts.rowid
                    WHERE books_fts MATCH ?
                    LIMIT ?
                    """,
                    (safe_fts_q, limit),
                ).fetchall()
                for r in rows:
                    record_cb(int(r["book"]), f"fulltext:{r['format']}", str(r["snip"] or "").strip())
                return "fts5"
            except sqlite3.OperationalError:
                pass

        if "books_text" in tables:
            q_low = query.lower()
            rows = fconn.execute(
                """
                SELECT book, format,
                       substr(searchable_text, max(1, instr(lower(searchable_text), ?) - 100), 240) AS snip
                FROM books_text
                WHERE searchable_text LIKE ?
                LIMIT ?
                """,
                (q_low, f"%{query}%", limit),
            ).fetchall()
            for r in rows:
                snip = re.sub(r"\s+", " ", str(r["snip"] or "")).strip()
                record_cb(int(r["book"]), f"fulltext:{r['format']}", snip)
            return "books_text_scan"

        return "none"

    def find_bridges(self, authors: list[str], *, limit: int = 20) -> dict[str, Any]:
        cleaned_authors = [a.strip() for a in authors if a and a.strip()]
        if len(cleaned_authors) < 2:
            raise ValueError("Provide at least two comma-separated authors via ?authors=A,B")
        limit = max(1, min(int(limit), 100))

        meta_db = self.resolve_metadata_db()
        if meta_db is None:
            return {
                "available": False,
                "authors": cleaned_authors,
                "error": "Scribrarium metadata.db unavailable",
            }

        fts_db = self.resolve_fts_db()
        mconn = open_sqlite_ro(meta_db)
        try:
            existing_tables = {
                r["name"]
                for r in mconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }

            # 1. Resolve each queried author's books in Scribrarium
            author_book_ids: dict[str, set[int]] = {}
            all_own_book_ids: set[int] = set()
            for auth in cleaned_authors:
                rows = mconn.execute(
                    """
                    SELECT DISTINCT l.book AS book_id
                    FROM books_authors_link l
                    JOIN authors a ON a.id = l.author
                    WHERE a.name LIKE ?
                    """,
                    (f"%{auth}%",),
                ).fetchall()
                bids = {int(r["book_id"]) for r in rows}
                author_book_ids[auth] = bids
                all_own_book_ids |= bids

            # 2. Direct co-authored / anthology volumes shared by >= 2 queried authors
            coauthored_ids: list[int] = []
            for bid in sorted(all_own_book_ids):
                matched = [a for a, bids in author_book_ids.items() if bid in bids]
                if len(matched) >= 2:
                    coauthored_ids.append(bid)

            # 3. Shared conceptual tags across >= 2 queried authors (ranked by specificity)
            shared_tags: list[dict[str, Any]] = []
            if {"books_tags_link", "tags"} <= existing_tables:
                tags_by_author: dict[str, set[str]] = {}
                for auth, bids in author_book_ids.items():
                    if not bids:
                        tags_by_author[auth] = set()
                        continue
                    ph = ",".join("?" for _ in bids)
                    trows = mconn.execute(
                        f"""
                        SELECT DISTINCT t.name
                        FROM books_tags_link l
                        JOIN tags t ON t.id = l.tag
                        WHERE l.book IN ({ph})
                        """,
                        sorted(bids),
                    ).fetchall()
                    tags_by_author[auth] = {
                        str(r["name"])
                        for r in trows
                        if str(r["name"]).strip().lower() not in GENERIC_CALIBRE_TAGS_LOWER
                    }

                candidate_tags: dict[str, list[str]] = {}
                for auth, tset in tags_by_author.items():
                    for tname in tset:
                        candidate_tags.setdefault(tname, []).append(auth)

                for tname, sharing_authors in candidate_tags.items():
                    if len(sharing_authors) >= 2:
                        cnt_row = mconn.execute(
                            """
                            SELECT COUNT(*) AS cnt
                            FROM books_tags_link l
                            JOIN tags t ON t.id = l.tag
                            WHERE t.name = ?
                            """,
                            (tname,),
                        ).fetchone()
                        global_count = int(cnt_row["cnt"]) if cnt_row else 0
                        shared_tags.append(
                            {
                                "tag": tname,
                                "authors": sorted(sharing_authors),
                                "library_book_count": global_count,
                            }
                        )
                shared_tags.sort(key=lambda x: ( -len(x["authors"]), x["library_book_count"], x["tag"].lower()))
                shared_tags = shared_tags[:limit]

            # Extract surname / distinctive token for each author for cross-mention discovery
            author_tokens = [self._distinctive_author_token(a) for a in cleaned_authors]

            # 4. Annotation bridges (highlights/notes mentioning multiple queried authors,
            #    or one author's book annotated with a mention of the other author)
            annotation_bridges: list[dict[str, Any]] = []
            if "annotations" in existing_tables:
                clauses = " OR ".join("(searchable_text LIKE ? OR annot_data LIKE ?)" for _ in author_tokens)
                params: list[Any] = []
                for tok in author_tokens:
                    lk = f"%{tok}%"
                    params.extend([lk, lk])
                arows = mconn.execute(
                    f"SELECT id, book, annot_type, annot_data, searchable_text FROM annotations WHERE {clauses} LIMIT 300",
                    params,
                ).fetchall()
                annot_book_ids: list[int] = []
                raw_annots: list[tuple[int, dict[str, Any], list[str]]] = []
                for r in arows:
                    try:
                        ad = json.loads(r["annot_data"] or "{}")
                    except Exception:
                        ad = {}
                    if ad.get("removed") is True:
                        continue
                    bid = int(r["book"])
                    blob = f"{ad.get('highlighted_text', '')} {ad.get('notes', '')} {r['searchable_text'] or ''}".lower()
                    mentioned = [
                        auth
                        for auth, tok in zip(cleaned_authors, author_tokens)
                        if tok.lower() in blob or bid in author_book_ids.get(auth, set())
                    ]
                    if len(set(mentioned)) >= 2:
                        annot_book_ids.append(bid)
                        raw_annots.append((bid, ad, sorted(set(mentioned))))

                annot_books_map = self._enrich_books_map(mconn, annot_book_ids[:limit])
                for bid, ad, mentioned in raw_annots[:limit]:
                    binfo = annot_books_map.get(bid, {"book_id": bid, "title": f"Book #{bid}", "authors": []})
                    annotation_bridges.append(
                        {
                            "book_id": bid,
                            "title": binfo.get("title"),
                            "book_authors": binfo.get("authors", []),
                            "bridged_authors": mentioned,
                            "highlighted_text": (ad.get("highlighted_text") or "")[:300],
                            "note": ad.get("notes") or ad.get("note"),
                        }
                    )

            # 5. Full-text co-mention bridges in `full-text-search.db`
            fulltext_bridges: list[dict[str, Any]] = []
            if fts_db is not None:
                fconn = open_sqlite_ro(fts_db)
                try:
                    ft_tables = {
                        r["name"]
                        for r in fconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                    }
                    if "books_text" in ft_tables:
                        where_ands = " AND ".join("searchable_text LIKE ?" for _ in author_tokens)
                        ft_params: list[Any] = [author_tokens[0].lower()]
                        ft_params.extend(f"%{tok}%" for tok in author_tokens)
                        ft_params.append(limit)
                        ft_rows = fconn.execute(
                            f"""
                            SELECT book, format,
                                   substr(searchable_text, max(1, instr(lower(searchable_text), ?) - 100), 260) AS snip
                            FROM books_text
                            WHERE {where_ands}
                            LIMIT ?
                            """,
                            ft_params,
                        ).fetchall()
                        ft_bids = [int(r["book"]) for r in ft_rows]
                        ft_bmap = self._enrich_books_map(mconn, ft_bids)
                        for r in ft_rows:
                            bid = int(r["book"])
                            binfo = ft_bmap.get(bid, {"book_id": bid, "title": f"Book #{bid}", "authors": []})
                            fulltext_bridges.append(
                                {
                                    "book_id": bid,
                                    "title": binfo.get("title"),
                                    "authors": binfo.get("authors", []),
                                    "year": binfo.get("year"),
                                    "format": r["format"],
                                    "is_third_party_bridge": bid not in all_own_book_ids,
                                    "snippet": re.sub(r"\s+", " ", str(r["snip"] or "")).strip(),
                                }
                            )
                finally:
                    fconn.close()

            coauthored_map = self._enrich_books_map(mconn, coauthored_ids[:limit])
            author_books_summary: dict[str, list[dict[str, Any]]] = {}
            for auth, bids in author_book_ids.items():
                sample_ids = sorted(bids)[:15]
                bmap = self._enrich_books_map(mconn, sample_ids)
                author_books_summary[auth] = [bmap[i] for i in sample_ids if i in bmap]

            return {
                "available": True,
                "authors": cleaned_authors,
                "author_book_counts": {a: len(bids) for a, bids in author_book_ids.items()},
                "author_books": author_books_summary,
                "coauthored_books": [coauthored_map[i] for i in coauthored_ids[:limit] if i in coauthored_map],
                "shared_tags": shared_tags,
                "annotation_bridges": annotation_bridges,
                "fulltext_bridges": fulltext_bridges,
            }
        finally:
            mconn.close()

    @staticmethod
    def _distinctive_author_token(author_name: str) -> str:
        clean = re.sub(r"\([^)]*\)", "", author_name).strip()
        if "," in clean:
            # "Canetti, Elias" -> "Canetti"
            return clean.split(",", 1)[0].strip()
        parts = [p for p in clean.split() if len(p) > 1]
        return parts[-1] if parts else clean

    def lookup_lexicon(self, term: str = "", *, include_inbox: bool = False) -> dict[str, Any]:
        q = (term or "").strip()
        q_low = q.lower()

        if self.offline_snapshot is not None:
            records = self._load_offline_lexicon_records()
        else:
            resp = self.grist_requester("GET", f"docs/{LEXICON_DOC_ID}/tables/Terms/records", None)
            records = resp.get("records", [])

        matches: list[dict[str, Any]] = []
        for rec in records:
            fields = rec.get("fields", {}) if isinstance(rec, dict) else {}
            status = str(fields.get("Status") or "").strip()
            if not include_inbox and status.lower() == "inbox":
                continue
            rec_term = str(fields.get("Term") or "").strip()
            if not rec_term:
                continue
            if q_low:
                haystack = " ".join(
                    str(fields.get(col) or "")
                    for col in ("Term", "My_Definition", "Received_Definition", "Portable_Axiom")
                ).lower()
                if q_low not in rec_term.lower() and q_low not in haystack:
                    continue
            matches.append(
                {
                    "id": rec.get("id"),
                    "exact_match": bool(q_low and rec_term.lower() == q_low),
                    "fields": fields,
                }
            )

        matches.sort(key=lambda r: (not r["exact_match"], str(r["fields"].get("Term") or "").lower()))
        return {
            "doc_id": LEXICON_DOC_ID,
            "query": q,
            "total": len(matches),
            "terms": matches,
        }

    def _load_offline_lexicon_records(self) -> list[dict[str, Any]]:
        if self.offline_snapshot is None or not self.offline_snapshot.exists():
            return []
        if self.offline_snapshot.is_file():
            bundle = json.loads(self.offline_snapshot.read_text(encoding="utf-8"))
            raw = bundle.get("Terms") or bundle.get("lexicon_terms") or []
            return raw.get("records", raw) if isinstance(raw, dict) else raw
        for fname in ("lexicon_terms.json", "Terms.json", "terms.json"):
            cand = self.offline_snapshot / fname
            if cand.exists():
                parsed = json.loads(cand.read_text(encoding="utf-8"))
                return parsed.get("records", parsed) if isinstance(parsed, dict) else parsed
        return []

    def park_to_parking_lot(self, body: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
        """
        Create a `Parking_Lot` item in Digital Workflow (`fjWnpuU31w9r2YnnGVMygW`),
        auto-computing the upcoming `Month_PLR1_YY` / `Month_PLR2_YY` target session if omitted.
        """
        item = str(body.get("item") if body.get("item") is not None else (body.get("Item") or "")).strip()
        if not item:
            raise ValueError("Field 'item' (or 'Item') is required.")

        today = self.today_provider()
        today_iso = today.isoformat()
        explicit_session = str(
            body.get("session") if body.get("session") is not None else (body.get("Target_Session") or "")
        ).strip()
        target_session = explicit_session or compute_upcoming_plr_session(today=today)

        notes = str(body.get("notes") if body.get("notes") is not None else (body.get("Notes") or "")).strip()
        links = str(body.get("links") if body.get("links") is not None else (body.get("Links") or "")).strip()
        status = str(
            body.get("status") if body.get("status") is not None else (body.get("Status") or "Open")
        ).strip() or "Open"

        grist_payload = {
            "records": [
                {
                    "fields": {
                        "Item": item,
                        "Target_Session": target_session,
                        "Status": status,
                        "Notes": notes,
                        "Links": links,
                        "Resurface_Count": 1,
                        "Created_At": today_iso,
                        "Last_Updated": today_iso,
                    }
                }
            ]
        }

        if dry_run:
            return {
                "status": "dry_run",
                "doc_id": PARKING_LOT_DOC_ID,
                "table_id": PARKING_LOT_TABLE_ID,
                "target_session": target_session,
                "fields": grist_payload["records"][0]["fields"],
            }

        resp = self.grist_requester(
            "POST",
            f"docs/{PARKING_LOT_DOC_ID}/tables/{PARKING_LOT_TABLE_ID}/records",
            grist_payload,
        )
        created_records = resp.get("records", [])
        created_id = created_records[0].get("id") if created_records else None
        return {
            "status": "parked",
            "id": created_id,
            "doc_id": PARKING_LOT_DOC_ID,
            "table_id": PARKING_LOT_TABLE_ID,
            "target_session": target_session,
            "fields": grist_payload["records"][0]["fields"],
        }


# ---------------------------------------------------------------------------
# HTTP Request Dispatcher & `http.server` Handler
# ---------------------------------------------------------------------------


def resolve_cors_headers(origin: str | None) -> dict[str, str]:
    """
    Build CORS response headers strictly allowing `https://triomnibus.thegesturalist.com`,
    `http://localhost:8080`, `http://127.0.0.1:8080`, and `null` (`file://`), plus
    Private Network Access headers for HTTPS -> `127.0.0.1` browser fetches.
    """
    headers: dict[str, str] = {
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With",
        "Access-Control-Allow-Private-Network": "true",
        "Access-Control-Max-Age": "86400",
    }
    if origin and origin in ALLOWED_CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
    return headers


def dispatch_http_request(
    service: LocalCorpusService,
    method: str,
    raw_url: str,
    *,
    body_bytes: bytes = b"",
    origin: str | None = None,
    dry_run: bool = False,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    """
    Pure request router decoupled from raw sockets so both `ThreadingHTTPServer`
    and offline unit tests execute identical routing, CORS, and error-handling logic.
    """
    cors = resolve_cors_headers(origin)
    headers = {"Content-Type": "application/json; charset=utf-8", **cors}

    if method == "OPTIONS":
        return 204, headers, {}

    parsed = urllib.parse.urlsplit(raw_url)
    path = parsed.path.rstrip("/") or "/"
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)

    def _first(key: str, default: str = "") -> str:
        vals = qs.get(key)
        return vals[0] if vals else default

    try:
        if method == "GET" and path == "/api/health":
            return 200, headers, service.get_health()

        if method == "GET" and path == "/api/scribrarium/bridges":
            raw_authors = _first("authors")
            authors = [a.strip() for a in raw_authors.split(",") if a.strip()]
            limit = int(_first("limit", "20"))
            return 200, headers, service.find_bridges(authors, limit=limit)

        if method == "GET" and path == "/api/scribrarium/search":
            q = _first("q")
            limit = int(_first("limit", "25"))
            return 200, headers, service.search_scribrarium(q, limit=limit)

        if method == "GET" and path == "/api/scribrarium/annotations":
            raw_bid = _first("book_id")
            book_id = int(raw_bid) if raw_bid != "" else None
            q = _first("q")
            limit = int(_first("limit", "100"))
            return 200, headers, service.get_annotations(book_id=book_id, query=q, limit=limit)

        if method == "GET" and path == "/api/lexicon":
            term = _first("term")
            include_inbox = _first("include_inbox", "0") in ("1", "true", "yes")
            return 200, headers, service.lookup_lexicon(term, include_inbox=include_inbox)

        if method == "GET" and path == "/api/parking-lot/sessions":
            return 200, headers, compute_plr_sessions(today=service.today_provider(), count=6)

        if method == "POST" and path == "/api/parking-lot":
            try:
                payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else {}
            except json.JSONDecodeError as exc:
                return 400, headers, {"error": f"Invalid JSON body: {exc}"}
            if not isinstance(payload, dict):
                return 400, headers, {"error": "Expected JSON object body."}
            result = service.park_to_parking_lot(payload, dry_run=dry_run)
            return 201 if result.get("status") == "parked" else 200, headers, result

        return 404, headers, {"error": f"Unknown route: {method} {path}"}
    except ValueError as val_exc:
        return 400, headers, {"error": str(val_exc)}
    except Exception as exc:
        return 500, headers, {"error": str(exc)}


def make_handler_class(service: LocalCorpusService, *, dry_run: bool = False) -> type[http.server.BaseHTTPRequestHandler]:
    class CorpusAPIRequestHandler(http.server.BaseHTTPRequestHandler):
        server_version = "TriomnibusLocalCorpusAPI/1.0"

        def _handle(self, method: str) -> None:
            origin = self.headers.get("Origin")
            length = int(self.headers.get("Content-Length") or "0")
            body = self.rfile.read(length) if length > 0 else b""
            status, resp_headers, payload = dispatch_http_request(
                service,
                method,
                self.path,
                body_bytes=body,
                origin=origin,
                dry_run=dry_run,
            )
            encoded = b"" if status == 204 else (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self.send_response(status)
            for hk, hv in resp_headers.items():
                self.send_header(hk, hv)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if encoded:
                self.wfile.write(encoded)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._handle("OPTIONS")

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"[local_corpus_api] {self.address_string()} - {fmt % args}\n")

    return CorpusAPIRequestHandler


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local read-only Scribrarium + Grist Lexicon/Parking Lot HTTP bridge."
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bind address (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bind port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--calibre-dir",
        type=Path,
        default=DEFAULT_CALIBRE_DIR,
        help=f"Path to Calibre Scribrarium directory (default: {DEFAULT_CALIBRE_DIR})",
    )
    parser.add_argument(
        "--offline-snapshot",
        type=Path,
        default=None,
        help="Optional local snapshot file/directory for offline Lexicon lookups",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate read-only SQLite access + PLR session state, print health JSON, and exit",
    )
    # Pipeline CLI flag compatibility
    parser.add_argument("--repo-root", type=Path, default=None, help="Optional Triomnibus repo root")
    parser.add_argument("--drive-pdf-dir", type=Path, default=None, help="Unused (CLI flag compatibility)")
    parser.add_argument("--no-push", action="store_true", help="Unused (CLI flag compatibility)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    offline_snap = args.offline_snapshot.expanduser() if args.offline_snapshot else None
    if offline_snap is None and args.repo_root and (args.repo_root.expanduser() / "data").exists():
        # Allow optional fallback to repo_root/data when offline_snapshot isn't explicitly passed
        pass

    service = LocalCorpusService(
        calibre_dir=args.calibre_dir.expanduser(),
        offline_snapshot=offline_snap,
    )

    if args.dry_run:
        health = service.get_health()
        health["bind"] = f"{args.host}:{args.port}"
        print(json.dumps(health, indent=2))
        return 0

    server = http.server.ThreadingHTTPServer(
        (args.host, args.port),
        make_handler_class(service, dry_run=False),
    )
    print(
        f"[local_corpus_api] Listening on http://{args.host}:{args.port} "
        f"(CORS origins: {sorted(ALLOWED_CORS_ORIGINS)})",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[local_corpus_api] Shutting down cleanly.", file=sys.stderr)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
