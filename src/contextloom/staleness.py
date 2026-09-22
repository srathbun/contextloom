"""Index freshness and the update/GC pipeline (DESIGN §6, §10).

``update`` is a deterministic full rebuild into a temp database followed by an
atomic ``os.replace``, so concurrent readers always see either the fully-old or
fully-new index (DESIGN §5.3). The self-healing query path (§10) calls
``is_stale`` and then ``update`` before answering.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from contextloom.config import DEFAULT_IGNORES, DEFAULT_MAX_FILE_SIZE
from contextloom.db import (
    DB_FILENAME,
    SCHEMA,
    IndexLock,
    create_index,
    discover_index,
    open_index,
)
from contextloom.indexer import (
    classify_file,
    extract_code,
    extract_markdown,
    link_heuristic,
)
from contextloom.indexer.enums import (
    FILES_KIND_CODE,
    FILES_KIND_MARKDOWN,
    FILES_KIND_TEXT,
    FILES_STATUS_OK,
    FILES_STATUS_UNSUPPORTED,
    ISSUE_KIND_TOO_LARGE,
)

# Ref kinds that markdown/prose production writes (kept for read clarity).
_TEXT_KINDS = (FILES_KIND_MARKDOWN, FILES_KIND_TEXT)


def sha1_file(path: Path) -> str:
    """SHA-1 content hash (DESIGN §11.2; change detection, not security)."""
    hasher = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _load_gitignore(root: Path) -> list[str]:
    patterns: list[str] = []
    gi = root / ".gitignore"
    if gi.is_file():
        for raw in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                patterns.append(line.rstrip("/"))
    return patterns


def _ignored(rel_posix: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if fnmatch.fnmatch(rel_posix, pat) or fnmatch.fnmatch(rel_posix, f"*/{pat}"):
            return True
        if pat.endswith("/") and (
            rel_posix.startswith(pat) or f"/{rel_posix}".startswith(f"/{pat}")
        ):
            return True
    return False


def walk_files(root: Path, ignores: list[str]) -> list[Path]:
    """Return absolute paths of all non-ignored files under ``root``."""
    patterns = ignores + _load_gitignore(root)
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if not _ignored(f"{Path(dirpath, d).relative_to(root).as_posix()}/", patterns)
        ]
        for name in filenames:
            if name == DB_FILENAME or name.startswith(DB_FILENAME + "."):
                continue
            abs_path = Path(dirpath, name)
            rel = abs_path.relative_to(root).as_posix()
            if _ignored(rel, patterns):
                continue
            out.append(abs_path)
    return out


def _read_tables(index: Path) -> tuple[dict[str, str], dict[str, str]]:
    conn = open_index(index, readonly=True)
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        config = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM config")}
    finally:
        conn.close()
    return meta, config


def is_stale(root: Path) -> bool:
    """True if the on-disk tree differs from the index (DESIGN §10)."""
    index = discover_index(root)
    if index is None:
        return True
    try:
        conn = open_index(index, readonly=True)
        indexed: dict[str, tuple[str, float, int]] = {}
        for r in conn.execute("SELECT path, hash, mtime, size FROM files"):
            indexed[r["path"]] = (r["hash"], r["mtime"], r["size"])
        conn.close()
    except Exception:
        return True

    config = _read_tables(index)[1]
    ignores = _json_list(config.get("ignores"), DEFAULT_IGNORES)

    seen: set[str] = set()
    for path in walk_files(root, ignores):
        rel = path.relative_to(root).as_posix()
        seen.add(rel)
        stat = path.stat()
        stored = indexed.get(rel)
        if stored is None:
            return True
        _hash, mtime, size = stored
        if stat.st_mtime != mtime or stat.st_size != size:
            if sha1_file(path) != _hash:
                return True
    for rel in indexed:
        if rel not in seen:
            return True
    return False


def _json_list(raw: str | None, default: list[str]) -> list[str]:
    if not raw:
        return list(default)
    import json

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return list(default)
    return value if isinstance(value, list) else list(default)


def _collect(root: Path, ignores: list[str], max_size: int) -> list[dict[str, Any]]:
    """Walk + hash + classify. Too-large files are tagged, not dropped (§6.5)."""
    entries: list[dict[str, Any]] = []
    for abs_path in walk_files(root, ignores):
        rel = abs_path.relative_to(root).as_posix()
        try:
            stat = abs_path.stat()
        except OSError:
            continue
        if stat.st_size > max_size:
            cls = classify_file(rel, b"", max_size)
            cls["status"] = FILES_STATUS_UNSUPPORTED
            cls["issue_kind"] = ISSUE_KIND_TOO_LARGE
            cls["issue_detail"] = f"size {stat.st_size} exceeds max {max_size}"
            data = b""
        else:
            try:
                data = abs_path.read_bytes()
            except OSError:
                continue
            cls = classify_file(rel, data, max_size)
        entries.append(
            {
                "rel": rel,
                "data": data,
                "hash": sha1_file(abs_path),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "cls": cls,
            }
        )
    return entries


def _extract(
    entries: list[dict[str, Any]],
) -> tuple[
    list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]
]:
    files_rows: list[tuple[Any, ...]] = []
    symbols_rows: list[tuple[Any, ...]] = []
    refs_rows: list[tuple[Any, ...]] = []
    issues_rows: list[tuple[Any, ...]] = []

    project_files = {e["rel"] for e in entries}
    symbol_names: set[str] = set()

    # Layer 1: structural extraction.
    for e in entries:
        rel = e["rel"]
        cls = e["cls"]
        files_rows.append(
            (rel, e["hash"], e["mtime"], e["size"], cls["kind"], cls["language"], cls["status"])
        )
        if cls["status"] != FILES_STATUS_OK:
            issues_rows.append((rel, cls["issue_kind"] or "parse_error", cls["issue_detail"]))
            continue
        try:
            text = e["data"].decode("utf-8", errors="replace")
        except Exception:
            continue

        syms: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        if cls["kind"] == FILES_KIND_CODE and cls["language"]:
            syms, refs = extract_code(cls["language"], text)
        elif cls["kind"] == FILES_KIND_MARKDOWN:
            result = extract_markdown(rel, text)
            syms, refs = result["symbols"], result["refs"]

        for s in syms:
            symbols_rows.append(
                (rel, s["name"], s["kind"], s["line_start"], s.get("line_end"), s.get("scope_path"))
            )
            symbol_names.add(s["name"])
        for r in refs:
            refs_rows.append(
                (
                    rel,
                    r.get("to_file"),
                    r.get("to_symbol"),
                    r["line"],
                    r["ref_kind"],
                    r["source"],
                    r["confidence"],
                    r["evidence"],
                )
            )

    # Layer 2: heuristic linkage over prose (deterministic, no AI).
    for e in entries:
        if e["cls"]["status"] != FILES_STATUS_OK or e["cls"]["kind"] not in _TEXT_KINDS:
            continue
        try:
            text = e["data"].decode("utf-8", errors="replace")
        except Exception:
            continue
        for r in link_heuristic(e["rel"], text, project_files, symbol_names):
            refs_rows.append(
                (
                    e["rel"],
                    r.get("to_file"),
                    r.get("to_symbol"),
                    r["line"],
                    r["ref_kind"],
                    r["source"],
                    r["confidence"],
                    r["evidence"],
                )
            )

    return files_rows, symbols_rows, refs_rows, issues_rows


def _build(
    tmp: Path,
    meta: dict[str, str],
    config: dict[str, str],
    files_rows: list[tuple[Any, ...]],
    symbols_rows: list[tuple[Any, ...]],
    refs_rows: list[tuple[Any, ...]],
    issues_rows: list[tuple[Any, ...]],
) -> None:
    conn = sqlite3.connect(str(tmp))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(";\n".join(SCHEMA))
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", meta.items())
        conn.executemany("INSERT INTO config (key, value) VALUES (?, ?)", config.items())

        file_ids: dict[str, int] = {}
        for rel, hash_, mtime, size, kind, language, status in files_rows:
            cur = conn.execute(
                "INSERT INTO files (path, hash, mtime, size, kind, language, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rel, hash_, mtime, size, kind, language, status),
            )
            assert cur.lastrowid is not None
            file_ids[rel] = cur.lastrowid

        name_to_ids: dict[str, list[int]] = {}
        for rel, name, kind, line_start, line_end, scope in symbols_rows:
            fid = file_ids.get(rel)
            if fid is None:
                continue
            cur = conn.execute(
                "INSERT INTO symbols (file_id, name, kind, line_start, line_end, scope_path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fid, name, kind, line_start, line_end, scope),
            )
            assert cur.lastrowid is not None
            name_to_ids.setdefault(name, []).append(cur.lastrowid)

        for rel, to_file, to_symbol, line, ref_kind, source, confidence, evidence in refs_rows:
            fid = file_ids.get(rel)
            if fid is None:
                continue
            to_fid = file_ids.get(to_file) if to_file else None
            to_sid = name_to_ids.get(to_symbol, [None])[0] if to_symbol else None
            conn.execute(
                "INSERT INTO refs (from_file_id, from_symbol_id, to_file_id, to_symbol_id, "
                "line, ref_kind, source, confidence, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fid, None, to_fid, to_sid, line, ref_kind, source, confidence, evidence),
            )

        for rel, issue_kind, detail in issues_rows:
            fid = file_ids.get(rel)
            if fid is None:
                continue
            conn.execute(
                "INSERT INTO file_issues (file_id, issue_kind, detail) VALUES (?, ?, ?)",
                (fid, issue_kind, detail),
            )
        conn.commit()
    finally:
        conn.close()


def update(root: Path, *, full: bool = False) -> dict[str, Any]:
    """(Re)build the index. Deterministic full rebuild + atomic swap; ``full`` is
    accepted for API compatibility (the rebuild is always complete)."""
    root = root.resolve()
    index = discover_index(root)
    if index is None:
        index = create_index(root)

    with IndexLock(index):
        meta, config = _read_tables(index)
        ignores = _json_list(config.get("ignores"), DEFAULT_IGNORES)
        max_size = _json_int(config.get("max_file_size"), DEFAULT_MAX_FILE_SIZE)

        entries = _collect(root, ignores, max_size)
        files_rows, symbols_rows, refs_rows, issues_rows = _extract(entries)
        # AI-inferred refs (Layer 3) are not regenerated; carry them forward so a
        # rebuild does not silently drop them (they are pruned via `gc --ai-refs`).
        refs_rows.extend(_existing_ai_refs(index))

        # Diff against previous index for reporting.
        old_paths: set[str] = set()
        try:
            conn = open_index(index, readonly=True)
            old_paths = {r["path"] for r in conn.execute("SELECT path FROM files")}
            conn.close()
        except Exception:
            old_paths = set()
        new_paths = {e["rel"] for e in entries}

        meta = dict(meta)
        meta["schema_version"] = str(_current_schema_version())
        meta["tool_version"] = meta.get("tool_version", "0.1.0")
        meta["last_full_index_at"] = datetime.now(timezone.utc).isoformat()

        tmp = index.with_name(f"{DB_FILENAME}.tmp-{uuid4().hex}")
        try:
            _build(tmp, meta, config, files_rows, symbols_rows, refs_rows, issues_rows)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, index)

        return {
            "files": len(files_rows),
            "added": len(new_paths - old_paths),
            "changed": len(new_paths & old_paths),
            "deleted": len(old_paths - new_paths),
            "symbols": len(symbols_rows),
            "refs": len(refs_rows),
            "issues": len(issues_rows),
        }


def _current_schema_version() -> int:
    # SCHEMA is fixed; the supported version lives in db.
    from contextloom.db import SCHEMA_VERSION

    return SCHEMA_VERSION


def _existing_ai_refs(index: Path) -> list[tuple[Any, ...]]:
    """AI-inferred refs currently stored, as build tuples (carried across rebuilds)."""
    try:
        conn = open_index(index, readonly=True)
        rows: list[tuple[Any, ...]] = []
        for r in conn.execute(
            "SELECT f.path AS from_path, t.path AS to_path, r.line, r.ref_kind, "
            "r.source, r.confidence, r.evidence "
            "FROM refs r "
            "JOIN files f ON r.from_file_id = f.id "
            "LEFT JOIN files t ON r.to_file_id = t.id "
            "WHERE r.source = 'ai'"
        ):
            rows.append(
                (
                    r["from_path"],
                    r["to_path"],
                    None,
                    r["line"],
                    r["ref_kind"],
                    r["source"],
                    r["confidence"],
                    r["evidence"],
                )
            )
        conn.close()
        return rows
    except Exception:
        return []


def _json_int(raw: str | None, default: int) -> int:
    if not raw:
        return default
    import json

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return default
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def gc(root: Path, *, ai_refs: bool = False) -> dict[str, Any]:
    """Prune rows whose file no longer exists on disk; optionally AI refs (DESIGN §9)."""
    root = root.resolve()
    index = discover_index(root)
    if index is None:
        return {"removed_files": 0, "removed_refs": 0}

    conn = open_index(index, readonly=False)
    try:
        missing_ids = [
            int(r["id"])
            for r in conn.execute("SELECT id, path FROM files")
            if not (root / r["path"]).is_file()
        ]
        removed_refs = 0
        for mid in missing_ids:
            cur = conn.execute(
                "SELECT COUNT(*) AS c FROM refs WHERE from_file_id = ? OR to_file_id = ?",
                (mid, mid),
            )
            removed_refs += cur.fetchone()["c"]
        removed_files = len(missing_ids)
        for mid in missing_ids:
            conn.execute("DELETE FROM files WHERE id = ?", (mid,))

        if ai_refs:
            cur = conn.execute("SELECT COUNT(*) AS c FROM refs WHERE source = 'ai'")
            removed_refs += cur.fetchone()["c"]
            conn.execute("DELETE FROM refs WHERE source = 'ai'")
        conn.commit()
    finally:
        conn.close()
    return {"removed_files": removed_files, "removed_refs": removed_refs}
