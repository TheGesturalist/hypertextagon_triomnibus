#!/usr/bin/env python3
"""
Hypertextagon Triomnibus — End-to-End Daily Update Publisher.

Bridges:
  1. Grist Resurfacing Document (`b88AFYd2n31Xk5UDaFyTmd` on `https://bstd.getgrist.com`)
     - Sets          -> data/sets.json
     - Books         -> data/books.json
     - Themes        -> data/themes.json       ({str(id): theme_label})
     - Methods       -> data/methods.json
     - Author_Weeks  -> data/author_weeks.json
     - Shadow_Edges  -> data/shadow_edges.json
     - Mentions      -> data/mentions.json
  2. Optional Grist Lexicon Document (`jZT77mQfrBpwsy5Fkcn5AS`, `Terms` table)
     - Excludes `Status == "Inbox"` and strips private notes/attestations
     - Terms         -> data/lexicon_terms.json
  3. Incremental Google Drive Resurfacing PDFs (`resurfacing-YYYY-MM-DD.pdf`)
     - Copied into `<repo_root>/raw/pdfs/` (strictly gitignored; NEVER staged)
     - Invokes `scripts/pdf2md.py` ONLY when new PDFs arrive (or `--rebuild-pdfs`)
  4. Site & Graph Observatory Generation + Safe Git Publish
     - Runs `python3 scripts/generate.py` and verifies `content/index.md` +
       `quartz/static/triomnibus-graph.json`
     - Stages ONLY `data/*.json`, `content/`, and `quartz/static/triomnibus-graph.json`
     - Explicitly verifies via `git diff --cached --name-only` and `git status --porcelain`
       that `raw/` and private fields NEVER enter the Git index
     - Commits `chore(resurfacing): publish through YYYY-MM-DD (Set #N — Title)` and
       pushes to `origin main` (unless `--no-push` or `--dry-run`).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants & Configuration
# ---------------------------------------------------------------------------

GRIST_API_ROOT = "https://bstd.getgrist.com/api"
RESURFACING_DOC_ID = "b88AFYd2n31Xk5UDaFyTmd"
LEXICON_DOC_ID = "jZT77mQfrBpwsy5Fkcn5AS"

DEFAULT_REPO_URL = "https://github.com/TheGesturalist/hypertextagon_triomnibus.git"
DEFAULT_REPO_ROOT = Path("/Volumes/YOTUO/001_antigravity_externaldrive/hypertextagon_triomnibus")
DEFAULT_DRIVE_PDF_DIR = Path(
    "/Users/themainframe/Library/CloudStorage/GoogleDrive-johnnytrotta@gmail.com/"
    "My Drive/002_inbox_and_triage/002.44_claude_inbox/library_resurfacing"
)

CONFIG_PATHS = [
    Path("/Users/themainframe/.gemini/antigravity/scratch/grist-lexicon-clipper/backend/config.json"),
    Path("/Users/themainframe/.gemini/antigravity/scratch/parking-lot-review/config.json"),
]

# Ordered table -> target JSON filename inside `<repo_root>/data/`
RESURFACING_TABLE_OUTPUTS: dict[str, str] = {
    "Sets": "sets.json",
    "Books": "books.json",
    "Themes": "themes.json",
    "Methods": "methods.json",
    "Author_Weeks": "author_weeks.json",
    "Shadow_Edges": "shadow_edges.json",
    "Mentions": "mentions.json",
}
LEXICON_OUTPUT_FILENAME = "lexicon_terms.json"

RESURFACING_PDF_RE = re.compile(r"^resurfacing-\d{4}-\d{2}-\d{2}\.pdf$", re.IGNORECASE)
PRIVATE_DRIVE_URL_RE = re.compile(
    r"https?://(?:docs\.google\.com/document/d/|drive\.google\.com/(?:file/d/|open\?id=))",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Privacy Invariants (Allowlists & Denylists)
# ---------------------------------------------------------------------------

GLOBAL_PRIVATE_FIELDS: frozenset[str] = frozenset(
    {
        "Doc_Link",
        "PDF_Link",
        "Johnny_Notes",
        "Calibre_Tags",
        "Draft_UUID",
        "Draft_Created",
        "WP_Post_IDs",
        "WP_Quotes_Synced",
        "Attestations",
        "Private_Notes",
    }
)
GLOBAL_PRIVATE_FIELDS_LOWER: frozenset[str] = frozenset(f.lower() for f in GLOBAL_PRIVATE_FIELDS)

TABLE_DENYLISTS: dict[str, frozenset[str]] = {
    "Sets": GLOBAL_PRIVATE_FIELDS | frozenset({"Doc_Link", "PDF_Link", "Johnny_Notes"}),
    "Books": GLOBAL_PRIVATE_FIELDS
    | frozenset(
        {
            "Calibre_Tags",
            "Johnny_Notes",
            "Draft_UUID",
            "WP_Post_IDs",
            "WP_Quotes_Synced",
            "Draft_Created",
        }
    ),
    "Author_Weeks": GLOBAL_PRIVATE_FIELDS | frozenset({"Notes"}),
    "Methods": GLOBAL_PRIVATE_FIELDS,
    "Shadow_Edges": GLOBAL_PRIVATE_FIELDS,
    "Mentions": GLOBAL_PRIVATE_FIELDS,
    "Terms": GLOBAL_PRIVATE_FIELDS | frozenset({"Johnny_Notes", "Attestations", "Notes", "Sources"}),
}

TABLE_ALLOWLISTS: dict[str, frozenset[str]] = {
    "Author_Weeks": frozenset(
        {
            "Author",
            "Status",
            "Start",
            "Closing_Summary",
        }
    ),
    "Terms": frozenset(
        {
            "Term",
            "Status",
            "Bastardization_Op",
            "My_Definition",
            "Received_Definition",
            "Etymology",
            "Original_Script",
            "Pronunciation",
            "Language",
            "Portable_Axiom",
            "Boundary_Tensions",
            "WN_Status",
            "WN_Synset",
            "WN_Lexname",
            "WN_Gloss",
            "WN_Hypernyms",
            "Category",
            "Gravity",
            "Projects",
            "Lineages",
        }
    ),
}


class PrivacyViolationError(RuntimeError):
    """Raised whenever a private field or gitignored `raw/` path threatens to leak."""


# ---------------------------------------------------------------------------
# API Authentication & Grist Client
# ---------------------------------------------------------------------------


def get_api_key(config_paths: list[Path] | None = None) -> str:
    """Resolve `GRIST_API_KEY` from environment or local Clipper/PLR config.json."""
    env_key = os.environ.get("GRIST_API_KEY", "").strip()
    if env_key:
        return env_key
    for path in config_paths or CONFIG_PATHS:
        if path.exists():
            try:
                cfg = json.loads(path.read_text(encoding="utf-8"))
                key = str(cfg.get("api_key") or "").strip()
                if key:
                    return key
            except Exception:
                continue
    raise RuntimeError(
        "Could not find GRIST_API_KEY in environment or "
        "/Users/themainframe/.gemini/antigravity/scratch/grist-lexicon-clipper/backend/config.json"
    )


def grist_request(
    doc_id: str,
    method: str,
    path: str,
    payload: Any = None,
    *,
    api_key: str | None = None,
    api_root: str = GRIST_API_ROOT,
) -> dict[str, Any]:
    """
    Execute an authenticated Grist REST request against `docs/{doc_id}/...`.
    Uses Python `urllib.request` with `/usr/bin/curl` fallback (matching `resurfacing_sync.py`).
    """
    resolved_key = (api_key or get_api_key()).strip()
    url = f"{api_root.rstrip('/')}/docs/{doc_id}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {resolved_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body_str = json.dumps(payload) if payload is not None else None
    data = body_str.encode("utf-8") if body_str is not None else None

    curl_bin = "/usr/bin/curl" if Path("/usr/bin/curl").exists() else shutil.which("curl")
    last_err: Exception | None = None

    for _attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=25) as resp:
                resp_body = resp.read().decode("utf-8")
                return json.loads(resp_body) if resp_body else {}
        except urllib.error.HTTPError as http_exc:
            # 4xx errors (401/403/404) are definitive; do not retry blindly via curl unless 5xx
            if 400 <= http_exc.code < 500:
                err_text = http_exc.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Grist HTTP {http_exc.code} for {url}: {err_text[:300]}") from http_exc
            last_err = http_exc
        except Exception as exc:
            last_err = exc

        if curl_bin:
            try:
                cmd = [
                    curl_bin,
                    "-sS",
                    "--fail-with-body",
                    "--max-time",
                    "30",
                    "-X",
                    method,
                    "-H",
                    f"Authorization: Bearer {resolved_key}",
                    "-H",
                    "Content-Type: application/json",
                    "-H",
                    "Accept: application/json",
                ]
                if body_str is not None:
                    cmd.extend(["--data-binary", "@-"])
                cmd.append(url)
                proc = subprocess.run(
                    cmd,
                    input=body_str,
                    text=True,
                    capture_output=True,
                    timeout=35,
                )
                if proc.returncode == 0:
                    out = proc.stdout.strip()
                    return json.loads(out) if out else {}
                last_err = RuntimeError(
                    f"curl error ({proc.returncode}) on {url}: {proc.stderr.strip()} {proc.stdout[:200]}"
                )
            except Exception as curl_exc:
                last_err = curl_exc

    assert last_err is not None
    raise last_err


# ---------------------------------------------------------------------------
# Privacy Sanitization & Audit
# ---------------------------------------------------------------------------


def _scrub_value(val: Any) -> Any:
    """Recursively ensure nested structures do not carry private Google Doc/Drive URLs."""
    if isinstance(val, str):
        if PRIVATE_DRIVE_URL_RE.search(val):
            # Strip lines or tokens containing private Google Docs / Drive file links
            cleaned_lines = [
                line for line in val.splitlines() if not PRIVATE_DRIVE_URL_RE.search(line)
            ]
            return "\n".join(cleaned_lines).strip()
        return val
    if isinstance(val, list):
        return [_scrub_value(item) for item in val]
    if isinstance(val, dict):
        return {
            k: _scrub_value(v)
            for k, v in val.items()
            if str(k).lower() not in GLOBAL_PRIVATE_FIELDS_LOWER
        }
    return val


def sanitize_record(
    table_name: str,
    record: dict[str, Any],
    *,
    allowlist: set[str] | frozenset[str] | None = None,
    denylist: set[str] | frozenset[str] | None = None,
) -> dict[str, Any]:
    """
    Sanitize a single Grist record (`{"id": ..., "fields": {...}}`) or raw fields dictionary
    using per-table allowlists and global + per-table denylists.

    Guaranteed to strip:
      - Sets: `Doc_Link`, `PDF_Link`, `Johnny_Notes`
      - Books: `Calibre_Tags`, `Johnny_Notes`, `Draft_UUID`, `WP_Post_IDs`,
               `WP_Quotes_Synced`, `Draft_Created`
      - Author_Weeks: keeps ONLY public allowlist (`Author`, `Status`, `Start`,
               `Own_Works`, `Name_Variants`, `Closing_Summary`, `Day_Plan`), stripping `Notes`
      - Terms: keeps ONLY public Lexicon allowlist, stripping `Johnny_Notes`, `Attestations`, etc.
    """
    if not isinstance(record, dict):
        raise TypeError(f"Expected dict record for {table_name}, got {type(record).__name__}")

    has_envelope = "fields" in record and isinstance(record.get("fields"), dict)
    raw_fields: dict[str, Any] = record["fields"] if has_envelope else record

    effective_allowlist = allowlist if allowlist is not None else TABLE_ALLOWLISTS.get(table_name)
    effective_denylist = (
        GLOBAL_PRIVATE_FIELDS
        | TABLE_DENYLISTS.get(table_name, frozenset())
        | (frozenset(denylist) if denylist else frozenset())
    )
    effective_denylist_lower = frozenset(k.lower() for k in effective_denylist)

    cleaned_fields: dict[str, Any] = {}
    for key, val in raw_fields.items():
        key_str = str(key)
        if effective_allowlist is not None and key_str not in effective_allowlist:
            continue
        if key_str in effective_denylist or key_str.lower() in effective_denylist_lower:
            continue
        # Guard against future private columns named Johnny_*, Private_*, WP_*
        low = key_str.lower()
        if low.startswith(("johnny_", "private_", "wp_")):
            continue
        cleaned_fields[key_str] = _scrub_value(val)

    if has_envelope:
        out: dict[str, Any] = {"id": record.get("id"), "fields": cleaned_fields}
        return out
    return cleaned_fields


def sanitize_table_records(table_name: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize a list of Grist records, filtering `Status == 'Inbox'` for Lexicon `Terms`."""
    sanitized: list[dict[str, Any]] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else rec
        if table_name == "Terms":
            status = str(fields.get("Status") or "").strip()
            term_label = str(fields.get("Term") or "").strip()
            if not term_label or status.lower() == "inbox":
                continue
        elif table_name == "Author_Weeks":
            status = str(fields.get("Status") or "").strip()
            if status.lower() in ("planned", "queued", "inbox"):
                continue
        cleaned = sanitize_record(table_name, rec)
        sanitized.append(cleaned)
    return sanitized


def sanitize_themes_table(raw_themes: list[dict[str, Any]] | dict[str, Any]) -> dict[str, str]:
    """
    Convert Grist `Themes` table rows into `{str(id): theme_str}` matching `data/themes.json`,
    gracefully skipping missing, deleted, or empty rows and supporting either `Theme` or `Name`.
    """
    if isinstance(raw_themes, dict) and "records" not in raw_themes:
        items: list[tuple[str, str]] = []
        for k, v in raw_themes.items():
            if k is None or v is None:
                continue
            label = str(v).strip()
            key_str = str(k).strip()
            if key_str and label:
                items.append((key_str, label))
    else:
        rows = raw_themes.get("records", []) if isinstance(raw_themes, dict) else (raw_themes or [])
        items = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rec_id = row.get("id")
            if rec_id is None:
                continue
            fields = row.get("fields")
            if not isinstance(fields, dict):
                continue
            label = str(
                fields.get("Theme")
                if fields.get("Theme") not in (None, "")
                else (fields.get("Name") or "")
            ).strip()
            if not label:
                continue
            items.append((str(rec_id), label))

    def _sort_key(pair: tuple[str, str]) -> tuple[int, int | str]:
        k = pair[0]
        return (0, int(k)) if k.isdigit() else (1, k)

    return dict(sorted(items, key=_sort_key))


def assert_no_private_fields(payload: Any, *, table_name: str = "") -> None:
    """
    Deep post-sanitization security audit. Raises `PrivacyViolationError` if ANY
    forbidden private field key or private Google Doc/Drive URL survives anywhere in `payload`.
    """
    extra_forbidden = set(GLOBAL_PRIVATE_FIELDS_LOWER)
    if table_name == "Author_Weeks":
        extra_forbidden.add("notes")
    elif table_name == "Terms":
        extra_forbidden.update({"notes", "attestations", "sources"})

    def _walk(node: Any, trail: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                k_str = str(k)
                k_low = k_str.lower()
                # Check fields (ignore top-level envelope keys 'id' and 'fields')
                if k_low in extra_forbidden or k_low.startswith(("johnny_", "private_", "wp_")):
                    raise PrivacyViolationError(
                        f"Private field '{k_str}' detected at {trail}.{k_str} (table={table_name})"
                    )
                _walk(v, f"{trail}.{k_str}")
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                _walk(item, f"{trail}[{idx}]")
        elif isinstance(node, str):
            if PRIVATE_DRIVE_URL_RE.search(node):
                raise PrivacyViolationError(
                    f"Private Google Docs/Drive URL detected at {trail} (table={table_name})"
                )

    _walk(payload, table_name or "root")


# ---------------------------------------------------------------------------
# Fetching Data (Live Grist vs. Offline Snapshot)
# ---------------------------------------------------------------------------


def _load_offline_snapshot(snapshot_path: Path) -> dict[str, Any]:
    """
    Load Grist table payloads from an `--offline-snapshot` file or directory.

    Supports:
      1. A single JSON bundle file (`{"Sets": [...], "Books": [...], "Themes": [...], ...}`)
      2. A directory containing `<table_lower>.json` or `<table_lower>_v1_snapshot.json`
    """
    if not snapshot_path.exists():
        raise FileNotFoundError(f"Offline snapshot path does not exist: {snapshot_path}")

    raw_tables: dict[str, Any] = {}
    if snapshot_path.is_file():
        bundle = json.loads(snapshot_path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict):
            raise ValueError(f"Expected JSON object in snapshot file {snapshot_path}")
        lower_map = {str(k).lower(): v for k, v in bundle.items()}
        for table_name, out_file in RESURFACING_TABLE_OUTPUTS.items():
            stem = Path(out_file).stem.lower()
            val = lower_map.get(table_name.lower(), lower_map.get(stem))
            if val is not None:
                raw_tables[table_name] = val.get("records", val) if isinstance(val, dict) and "records" in val else val
            else:
                raw_tables[table_name] = {} if table_name == "Themes" else []
        terms_val = lower_map.get("terms", lower_map.get("lexicon_terms"))
        if terms_val is not None:
            raw_tables["Terms"] = (
                terms_val.get("records", terms_val)
                if isinstance(terms_val, dict) and "records" in terms_val
                else terms_val
            )
        return raw_tables

    # Directory mode
    for table_name, out_file in RESURFACING_TABLE_OUTPUTS.items():
        stem = Path(out_file).stem  # e.g. 'sets', 'author_weeks'
        candidates = [
            snapshot_path / out_file,
            snapshot_path / f"{table_name}.json",
            snapshot_path / f"{stem}_v1_snapshot.json",
        ]
        loaded: Any = None
        for cand in candidates:
            if cand.exists() and not cand.name.startswith("._"):
                parsed = json.loads(cand.read_text(encoding="utf-8"))
                loaded = (
                    parsed.get("records", parsed)
                    if isinstance(parsed, dict) and "records" in parsed
                    else parsed
                )
                break
        if loaded is None:
            raw_tables[table_name] = {} if table_name == "Themes" else []
        else:
            raw_tables[table_name] = loaded

    for lex_cand in (
        snapshot_path / LEXICON_OUTPUT_FILENAME,
        snapshot_path / "Terms.json",
        snapshot_path / "terms.json",
    ):
        if lex_cand.exists() and not lex_cand.name.startswith("._"):
            parsed = json.loads(lex_cand.read_text(encoding="utf-8"))
            raw_tables["Terms"] = (
                parsed.get("records", parsed)
                if isinstance(parsed, dict) and "records" in parsed
                else parsed
            )
            break

    return raw_tables


def fetch_all_tables(
    *,
    offline_snapshot: Path | None = None,
    skip_lexicon: bool = False,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Fetch all 7 Resurfacing tables + optional Lexicon Terms, returning sanitized payloads."""
    if offline_snapshot is not None:
        raw_tables = _load_offline_snapshot(offline_snapshot)
    else:
        resolved_key = api_key or get_api_key()
        raw_tables = {}
        required_tables = {"Sets", "Books"}
        for table_name in RESURFACING_TABLE_OUTPUTS:
            try:
                resp = grist_request(
                    RESURFACING_DOC_ID,
                    "GET",
                    f"tables/{table_name}/records",
                    api_key=resolved_key,
                )
                raw_tables[table_name] = resp.get("records", [])
            except Exception as exc:
                if table_name in required_tables:
                    raise
                print(
                    f"[warn] Table '{table_name}' unavailable in Grist ({exc}); using empty default.",
                    file=sys.stderr,
                )
                raw_tables[table_name] = []

        if not skip_lexicon:
            try:
                lex_resp = grist_request(
                    LEXICON_DOC_ID,
                    "GET",
                    "tables/Terms/records",
                    api_key=resolved_key,
                )
                raw_tables["Terms"] = lex_resp.get("records", [])
            except Exception as exc:
                print(
                    f"[info] Optional Lexicon doc {LEXICON_DOC_ID} unavailable ({exc}); skipping lexicon_terms.json.",
                    file=sys.stderr,
                )

    sanitized_outputs: dict[str, Any] = {}
    for table_name, out_filename in RESURFACING_TABLE_OUTPUTS.items():
        if table_name == "Themes":
            cleaned: Any = sanitize_themes_table(raw_tables.get("Themes", []))
        else:
            cleaned = sanitize_table_records(table_name, raw_tables.get(table_name, []))
        assert_no_private_fields(cleaned, table_name=table_name)
        sanitized_outputs[out_filename] = cleaned

    if not skip_lexicon and "Terms" in raw_tables:
        cleaned_terms = sanitize_table_records("Terms", raw_tables["Terms"])
        assert_no_private_fields(cleaned_terms, table_name="Terms")
        sanitized_outputs[LEXICON_OUTPUT_FILENAME] = cleaned_terms

    return sanitized_outputs


# ---------------------------------------------------------------------------
# Repository Initialization, `.gitignore` Guard & Drive PDF Ingestion
# ---------------------------------------------------------------------------


def ensure_repo_cloned(
    repo_root: Path,
    *,
    remote_url: str = DEFAULT_REPO_URL,
    dry_run: bool = False,
    offline: bool = False,
) -> bool:
    """
    Auto-clone `hypertextagon_triomnibus` on first live run if `repo_root` does not exist yet.
    Returns True if a fresh clone was performed.
    """
    if (repo_root / ".git").exists():
        return False
    if repo_root.exists() and any(repo_root.iterdir()):
        # Directory exists and is non-empty (e.g. local staging checkout)
        return False
    if dry_run or offline:
        print(
            f"[info] Skipping auto-clone of {remote_url} into {repo_root} (dry_run={dry_run}, offline={offline}).",
            file=sys.stderr,
        )
        return False

    repo_root.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", remote_url, str(repo_root)],
        text=True,
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed ({proc.returncode}): {proc.stderr.strip()}")
    return True


def ensure_raw_gitignored(repo_root: Path, *, dry_run: bool = False) -> bool:
    """
    Guarantee `<repo_root>/.gitignore` ignores `raw/` (and AppleDouble `._*` files).
    Also strips macOS exFAT `._*` AppleDouble files (including `.git/objects/pack/._*`)
    and verifies `raw/` is not already tracked in Git. Returns True if `.gitignore` was updated.
    """
    if not dry_run and repo_root.exists():
        for ad in repo_root.rglob("._*"):
            try:
                ad.unlink()
            except OSError:
                pass
    gitignore_path = repo_root / ".gitignore"
    existing_text = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    lines = {line.strip() for line in existing_text.splitlines()}

    has_raw_rule = any(rule in lines for rule in ("raw/", "/raw/", "raw", "/raw"))
    has_appledouble_rule = "._*" in lines

    updated = False
    additions: list[str] = []
    if not has_raw_rule:
        additions.append("raw/")
    if not has_appledouble_rule:
        additions.append("._*")

    if additions and not dry_run:
        repo_root.mkdir(parents=True, exist_ok=True)
        prefix = "" if (not existing_text or existing_text.endswith("\n")) else "\n"
        block = (
            f"{prefix}# Private raw resurfacing PDFs, extracted markdown, and macOS AppleDouble files\n"
            + "\n".join(additions)
            + "\n"
        )
        gitignore_path.write_text(existing_text + block, encoding="utf-8")
        updated = True

    if (repo_root / ".git").exists():
        proc = subprocess.run(
            ["git", "ls-files", "raw/", "raw"],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=15,
        )
        tracked_raw = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if tracked_raw:
            raise PrivacyViolationError(
                f"CRITICAL: Files under raw/ are tracked by Git and must be removed first: {tracked_raw}"
            )

    return updated


def discover_drive_pdfs(drive_pdf_dir: Path) -> list[Path]:
    """
    Scan `drive_pdf_dir` (and any `PDFs - Resurfacing` archive subdirectories) for
    `resurfacing-YYYY-MM-DD.pdf` files, ignoring hidden/temp/trash folders and `._*` files.
    """
    if not drive_pdf_dir.exists() or not drive_pdf_dir.is_dir():
        return []

    ignored_dirs = {"_to_delete", ".git", "__pycache__"}
    by_filename: dict[str, Path] = {}

    for root, dirs, files in os.walk(drive_pdf_dir):
        dirs[:] = [
            d
            for d in sorted(dirs)
            if not d.startswith(".") and d not in ignored_dirs
        ]
        for fname in sorted(files):
            if fname.startswith("._"):
                continue
            if RESURFACING_PDF_RE.match(fname):
                candidate = Path(root) / fname
                key = fname.lower()
                # Prefer top-level or newer mtime if duplicate exists across archive folder
                if key not in by_filename or candidate.stat().st_mtime > by_filename[key].stat().st_mtime:
                    by_filename[key] = candidate

    return [by_filename[k] for k in sorted(by_filename)]


def ingest_drive_pdfs(
    repo_root: Path,
    drive_pdf_dir: Path | None,
    *,
    rebuild_pdfs: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Incrementally copy new `resurfacing-YYYY-MM-DD.pdf` files from `drive_pdf_dir` into
    `<repo_root>/raw/pdfs/` and run `scripts/pdf2md.py` ONLY when new PDFs arrived or
    `rebuild_pdfs=True`. Gracefully handles missing Drive directory (e.g. GitHub Actions).
    """
    ensure_raw_gitignored(repo_root, dry_run=dry_run)

    if drive_pdf_dir is None or not drive_pdf_dir.exists():
        msg = f"Drive PDF folder unavailable ({drive_pdf_dir}); proceeding with Grist structured fields."
        print(f"[info] {msg}", file=sys.stderr)
        pdf2md_ran = False
        if rebuild_pdfs and not dry_run and (repo_root / "scripts" / "pdf2md.py").exists():
            _run_checked_python_script(repo_root, "scripts/pdf2md.py", "raw/pdfs", "raw/md", "raw/img")
            pdf2md_ran = True
        return {
            "drive_available": False,
            "discovered_count": 0,
            "copied_pdfs": [],
            "pdf2md_executed": pdf2md_ran,
        }

    discovered = discover_drive_pdfs(drive_pdf_dir)
    target_dir = repo_root / "raw" / "pdfs"
    copied_names: list[str] = []

    for src_pdf in discovered:
        target_name = src_pdf.name.lower()
        dst_pdf = target_dir / target_name
        is_new = not dst_pdf.exists() or dst_pdf.stat().st_size != src_pdf.stat().st_size
        if is_new:
            copied_names.append(target_name)
            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src_pdf, dst_pdf)

    should_run_pdf2md = (len(copied_names) > 0 or rebuild_pdfs) and not dry_run
    pdf2md_executed = False
    pdf2md_script = repo_root / "scripts" / "pdf2md.py"
    if should_run_pdf2md:
        if pdf2md_script.exists():
            _run_checked_python_script(repo_root, "scripts/pdf2md.py", "raw/pdfs", "raw/md", "raw/img")
            pdf2md_executed = True
        else:
            print(
                f"[warn] {pdf2md_script} not found; skipping PDF-to-Markdown extraction.",
                file=sys.stderr,
            )

    return {
        "drive_available": True,
        "discovered_count": len(discovered),
        "copied_pdfs": copied_names,
        "pdf2md_executed": pdf2md_executed,
    }


# ---------------------------------------------------------------------------
# JSON Writing, Site Generation & Output Verification
# ---------------------------------------------------------------------------


def write_sanitized_data_files(repo_root: Path, sanitized_outputs: dict[str, Any]) -> list[str]:
    """Write all sanitized `data/*.json` payloads atomically after re-verifying privacy invariants."""
    data_dir = repo_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for filename, payload in sorted(sanitized_outputs.items()):
        table_hint = {v: k for k, v in RESURFACING_TABLE_OUTPUTS.items()}.get(
            filename, "Terms" if filename == LEXICON_OUTPUT_FILENAME else filename
        )
        assert_no_private_fields(payload, table_name=table_hint)
        serialized = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        dst = data_dir / filename
        tmp = data_dir / f".{filename}.tmp"
        tmp.write_text(serialized, encoding="utf-8")
        os.replace(tmp, dst)
        written.append(f"data/{filename}")

    return written


def _run_checked_python_script(
    repo_root: Path, rel_script: str, *extra_args: str
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        [sys.executable, rel_script, *extra_args],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{rel_script} exited with code {proc.returncode}:\n"
            f"STDOUT:\n{proc.stdout[-1000:]}\nSTDERR:\n{proc.stderr[-1000:]}"
        )
    return proc


def run_generator_and_verify(repo_root: Path) -> dict[str, Any]:
    """
    Execute `python3 scripts/generate.py` inside `repo_root` and verify exit code 0 plus
    non-empty `content/index.md` and valid `quartz/static/triomnibus-graph.json`.
    """
    gen_script = repo_root / "scripts" / "generate.py"
    if not gen_script.exists():
        raise FileNotFoundError(f"Generator script missing at {gen_script}")

    proc = _run_checked_python_script(repo_root, "scripts/generate.py")

    index_md = repo_root / "content" / "index.md"
    graph_json = repo_root / "quartz" / "static" / "triomnibus-graph.json"

    if not index_md.exists() or index_md.stat().st_size == 0:
        raise RuntimeError(f"Generator verification failed: missing or empty {index_md}")
    if not graph_json.exists() or graph_json.stat().st_size == 0:
        raise RuntimeError(f"Generator verification failed: missing or empty {graph_json}")

    # Validate graph JSON is well-formed
    parsed_graph = json.loads(graph_json.read_text(encoding="utf-8"))

    return {
        "index_md_bytes": index_md.stat().st_size,
        "graph_json_bytes": graph_json.stat().st_size,
        "graph_keys": sorted(parsed_graph.keys()) if isinstance(parsed_graph, dict) else "list",
        "generator_stdout_tail": proc.stdout.strip()[-400:],
    }


# ---------------------------------------------------------------------------
# Commit Message, Git Staging Privacy Guard & Push
# ---------------------------------------------------------------------------


def format_date_utc(ts: Any) -> str:
    """Convert a Grist Unix timestamp (seconds) or ISO date string into `YYYY-MM-DD`."""
    if ts in (None, ""):
        return ""
    if isinstance(ts, (int, float)):
        return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    s = str(ts).strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return datetime.datetime.fromtimestamp(float(s), tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    return s[:10]


def build_commit_message(sets_records: list[dict[str, Any]]) -> str:
    """
    Compute `chore(resurfacing): publish through YYYY-MM-DD (Set #N — Title)`
    from the latest Set record.
    """
    if not sets_records:
        today_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return f"chore(resurfacing): publish through {today_iso} (Set #0 — Sync)"

    def _set_sort_key(rec: dict[str, Any]) -> tuple[str, int]:
        fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else rec
        date_str = format_date_utc(fields.get("Date")) or "0000-00-00"
        raw_id = rec.get("id")
        num_id = int(raw_id) if isinstance(raw_id, int) or str(raw_id or "").isdigit() else 0
        return (date_str, num_id)

    latest = max(sets_records, key=_set_sort_key)
    fields = latest.get("fields") if isinstance(latest.get("fields"), dict) else latest
    date_str = format_date_utc(fields.get("Date")) or datetime.datetime.now(
        datetime.timezone.utc
    ).strftime("%Y-%m-%d")
    set_num = fields.get("Set_Number") or latest.get("id") or 0
    title = str(fields.get("Set_Title") or "Untitled").strip()
    return f"chore(resurfacing): publish through {date_str} (Set #{set_num} — {title})"


def _is_allowed_staged_path(rel_path: str) -> bool:
    """Allow ONLY `data/*.json`, `content/**`, and `quartz/static/triomnibus-graph.json`."""
    norm = rel_path.strip().strip('"').lstrip("./")
    if not norm:
        return False
    # Never allow raw/, PDFs, or macOS AppleDouble `._*` files
    parts = Path(norm).parts
    if not parts:
        return False
    if "raw" in parts and parts[0] == "raw":
        return False
    if any(p.startswith("._") for p in parts):
        return False
    if norm.lower().endswith(".pdf"):
        return False

    if norm == "quartz/static/triomnibus-graph.json":
        return True
    if norm.startswith("content/"):
        return True
    if len(parts) == 2 and parts[0] == "data" and parts[1].endswith(".json"):
        return True
    return False


def verify_staged_paths(repo_root: Path) -> list[str]:
    """
    Inspect BOTH `git diff --cached --name-only` and `git status --porcelain` to enforce
    that ONLY `data/*.json`, `content/`, and `quartz/static/triomnibus-graph.json` are staged,
    that `raw/` is NEVER staged, and that staged `data/*.json` files contain zero private keys.
    Unstages the index immediately (`git reset`) if any violation is detected.
    """
    diff_proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=20,
    )
    if diff_proc.returncode != 0:
        raise RuntimeError(f"git diff --cached failed: {diff_proc.stderr.strip()}")

    status_proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=20,
    )
    if status_proc.returncode != 0:
        raise RuntimeError(f"git status --porcelain failed: {status_proc.stderr.strip()}")

    staged_paths: set[str] = {
        ln.strip() for ln in diff_proc.stdout.splitlines() if ln.strip()
    }
    for line in status_proc.stdout.splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        # Index status is xy[0]; '?' or ' ' or '!' means not staged in index
        if xy[0] not in (" ", "?", "!"):
            staged_paths.add(raw_path.strip('"'))

    forbidden = [p for p in sorted(staged_paths) if not _is_allowed_staged_path(p)]
    if forbidden:
        subprocess.run(["git", "reset"], cwd=str(repo_root), capture_output=True, timeout=15)
        raise PrivacyViolationError(
            f"Git staging guard tripped! Unstaged index due to forbidden paths: {forbidden}"
        )

    # Re-audit every staged data/*.json file on disk right before commit
    for rel in sorted(staged_paths):
        if rel.startswith("data/") and rel.endswith(".json"):
            full_path = repo_root / rel
            if full_path.exists():
                payload = json.loads(full_path.read_text(encoding="utf-8"))
                table_hint = {f"data/{v}": k for k, v in RESURFACING_TABLE_OUTPUTS.items()}.get(
                    rel, "Terms" if rel.endswith(LEXICON_OUTPUT_FILENAME) else rel
                )
                try:
                    assert_no_private_fields(payload, table_name=table_hint)
                except PrivacyViolationError:
                    subprocess.run(["git", "reset"], cwd=str(repo_root), capture_output=True, timeout=15)
                    raise

    return sorted(staged_paths)


def stage_commit_and_push(
    repo_root: Path,
    commit_message: str,
    *,
    no_push: bool = False,
) -> dict[str, Any]:
    """
    Stage ONLY `data/*.json`, `content/`, and `quartz/static/triomnibus-graph.json`,
    run `verify_staged_paths()`, commit if changes exist, and push to `origin main` unless `no_push`.
    """
    if not (repo_root / ".git").exists():
        raise RuntimeError(f"Cannot stage/commit: {repo_root} is not a Git repository.")

    ensure_raw_gitignored(repo_root, dry_run=False)

    # Clear any pre-existing staged paths so external working-tree noise never leaks in
    subprocess.run(["git", "reset"], cwd=str(repo_root), capture_output=True, timeout=15)

    paths_to_add: list[str] = []
    data_dir = repo_root / "data"
    if data_dir.exists():
        for jf in sorted(data_dir.glob("*.json")):
            if not jf.name.startswith("._"):
                paths_to_add.append(f"data/{jf.name}")
    if (repo_root / "content").exists():
        paths_to_add.append("content")
    if (repo_root / "quartz" / "static" / "triomnibus-graph.json").exists():
        paths_to_add.append("quartz/static/triomnibus-graph.json")

    if paths_to_add:
        add_proc = subprocess.run(
            ["git", "add", "-A", "--", *paths_to_add],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=30,
        )
        if add_proc.returncode != 0:
            raise RuntimeError(f"git add failed ({add_proc.returncode}): {add_proc.stderr.strip()}")

        # Unstage any macOS exFAT AppleDouble `._*` files that could have appeared under `content/`
        status_check = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=15,
        )
        apple_doubles = [
            ln.strip()
            for ln in status_check.stdout.splitlines()
            if ln.strip() and any(part.startswith("._") for part in Path(ln.strip()).parts)
        ]
        if apple_doubles:
            subprocess.run(
                ["git", "reset", "HEAD", "--", *apple_doubles],
                cwd=str(repo_root),
                capture_output=True,
                timeout=15,
            )

    staged = verify_staged_paths(repo_root)
    if not staged:
        return {
            "committed": False,
            "pushed": False,
            "staged_files": [],
            "commit_message": commit_message,
            "commit_sha": None,
        }

    # Ensure git author is configured (important for fresh CI containers / automated environments)
    name_chk = subprocess.run(
        ["git", "config", "user.name"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=10,
    )
    if not name_chk.stdout.strip():
        subprocess.run(
            ["git", "config", "user.name", "Triomnibus Sync Bot"],
            cwd=str(repo_root),
            check=True,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            cwd=str(repo_root),
            check=True,
            timeout=10,
        )

    commit_proc = subprocess.run(
        ["git", "commit", "-m", commit_message],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=30,
    )
    if commit_proc.returncode != 0:
        raise RuntimeError(f"git commit failed ({commit_proc.returncode}): {commit_proc.stderr.strip()}")

    sha_proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=10,
    )
    commit_sha = sha_proc.stdout.strip()

    pushed = False
    if not no_push:
        push_proc = subprocess.run(
            ["git", "push", "origin", "main"],
            cwd=str(repo_root),
            text=True,
            capture_output=True,
            timeout=120,
        )
        if push_proc.returncode != 0:
            raise RuntimeError(
                f"git push origin main failed ({push_proc.returncode}): {push_proc.stderr.strip()}"
            )
        pushed = True

    return {
        "committed": True,
        "pushed": pushed,
        "staged_files": staged,
        "commit_message": commit_message,
        "commit_sha": commit_sha,
    }


# ---------------------------------------------------------------------------
# End-to-End Orchestrator & CLI
# ---------------------------------------------------------------------------


def run_pipeline(
    *,
    repo_root: Path = DEFAULT_REPO_ROOT,
    drive_pdf_dir: Path | None = DEFAULT_DRIVE_PDF_DIR,
    offline_snapshot: Path | None = None,
    dry_run: bool = False,
    no_push: bool = False,
    rebuild_pdfs: bool = False,
    skip_lexicon: bool = False,
    remote_url: str = DEFAULT_REPO_URL,
) -> dict[str, Any]:
    """Execute the complete Triomnibus daily synchronization pipeline."""
    cloned = ensure_repo_cloned(
        repo_root,
        remote_url=remote_url,
        dry_run=dry_run,
        offline=(offline_snapshot is not None),
    )

    sanitized_outputs = fetch_all_tables(
        offline_snapshot=offline_snapshot,
        skip_lexicon=skip_lexicon,
    )
    sets_records = sanitized_outputs.get("sets.json", [])
    commit_message = build_commit_message(sets_records)

    pdf_report = ingest_drive_pdfs(
        repo_root,
        drive_pdf_dir,
        rebuild_pdfs=rebuild_pdfs,
        dry_run=dry_run,
    )

    counts = {
        fname: len(payload) if isinstance(payload, (list, dict)) else 0
        for fname, payload in sanitized_outputs.items()
    }

    if dry_run:
        return {
            "status": "dry_run",
            "repo_root": str(repo_root),
            "cloned": cloned,
            "table_counts": counts,
            "pdf_ingestion": pdf_report,
            "commit_message": commit_message,
            "privacy_audit": "passed",
        }

    written_files = write_sanitized_data_files(repo_root, sanitized_outputs)
    generator_report = run_generator_and_verify(repo_root)
    git_report = stage_commit_and_push(
        repo_root,
        commit_message,
        no_push=no_push,
    )

    return {
        "status": "ok",
        "repo_root": str(repo_root),
        "cloned": cloned,
        "table_counts": counts,
        "written_data_files": written_files,
        "pdf_ingestion": pdf_report,
        "generator": generator_report,
        "git": git_report,
        "privacy_audit": "passed",
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync Grist Resurfacing + Drive PDFs into hypertextagon_triomnibus and publish."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=DEFAULT_REPO_ROOT,
        help=f"Target hypertextagon_triomnibus Git repo path (default: {DEFAULT_REPO_ROOT})",
    )
    parser.add_argument(
        "--drive-pdf-dir",
        type=str,
        default=str(DEFAULT_DRIVE_PDF_DIR),
        help="Local Google Drive folder containing resurfacing-YYYY-MM-DD.pdf files (pass '' to disable)",
    )
    parser.add_argument(
        "--offline-snapshot",
        type=Path,
        default=None,
        help="Read Grist tables from a local JSON snapshot file or directory instead of live API",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate fetch, sanitization, Drive diff, and commit message without mutating repo or pushing",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="Write sanitized files, run generator, and commit locally without pushing to origin main",
    )
    parser.add_argument(
        "--rebuild-pdfs",
        action="store_true",
        help="Force running scripts/pdf2md.py even when zero new PDFs were copied from Drive",
    )
    parser.add_argument(
        "--skip-lexicon",
        action="store_true",
        help="Skip optional Lexicon (jZT77mQfrBpwsy5Fkcn5AS) Terms sync",
    )
    parser.add_argument(
        "--remote-url",
        type=str,
        default=DEFAULT_REPO_URL,
        help=f"Git remote URL for first-run auto-clone (default: {DEFAULT_REPO_URL})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    drive_dir = Path(args.drive_pdf_dir).expanduser() if args.drive_pdf_dir.strip() else None
    report = run_pipeline(
        repo_root=args.repo_root.expanduser(),
        drive_pdf_dir=drive_dir,
        offline_snapshot=args.offline_snapshot.expanduser() if args.offline_snapshot else None,
        dry_run=args.dry_run,
        no_push=args.no_push,
        rebuild_pdfs=args.rebuild_pdfs,
        skip_lexicon=args.skip_lexicon,
        remote_url=args.remote_url,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
