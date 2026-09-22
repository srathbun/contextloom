"""Query interface over the read-only contextloom index (DESIGN §9).

Every result dict carries the full field contract: ``path``, ``line``,
``line_end``, ``kind``, ``ref_kind``, ``source``, ``confidence``, ``evidence``,
``snippet``. ``snippet`` re-reads a few lines from disk at query time (DESIGN
§11.5); a missing or changed file yields ``snippet=None`` but still returns the
row.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_SNIPPET_WINDOW = 3


def _snippet(root: Path, rel_path: str, line: int | None) -> str | None:
    if not line:
        return None
    try:
        target = root / rel_path
        if not target.is_file():
            return None
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        idx = line - 1
        if idx < 0 or idx >= len(lines):
            return None
        start = max(0, idx - 1)
        end = min(len(lines), idx + _SNIPPET_WINDOW - 1)
        return "\n".join(lines[start:end])
    except OSError:
        return None


def _file_id(conn: sqlite3.Connection, path: str) -> int | None:
    row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    return None if row is None else int(row["id"])


def _path(conn: sqlite3.Connection, file_id: int) -> str | None:
    row = conn.execute("SELECT path FROM files WHERE id = ?", (file_id,)).fetchone()
    return None if row is None else str(row["path"])


def _symbol_ids_by_name(conn: sqlite3.Connection, name: str) -> list[int]:
    return [int(r["id"]) for r in conn.execute("SELECT id FROM symbols WHERE name = ?", (name,))]


def _symbol_ids_for_file(conn: sqlite3.Connection, file_id: int) -> list[int]:
    return [
        int(r["id"]) for r in conn.execute("SELECT id FROM symbols WHERE file_id = ?", (file_id,))
    ]


def _resolve_file_or_symbol(conn: sqlite3.Connection, path: str) -> tuple[int | None, list[int]]:
    """Return (file_id, [symbol_ids]) for a path query: relpath, relpath:line, or symbol name."""
    file_part = path
    if ":" in path and path.rsplit(":", 1)[1].isdigit():
        file_part = path.rsplit(":", 1)[0]
    fid = _file_id(conn, file_part)
    if fid is not None:
        return fid, []
    return None, _symbol_ids_by_name(conn, path)


def _def_dict(row: sqlite3.Row, root: Path) -> dict[str, Any]:
    rel = str(row["path"])
    line = int(row["line_start"])
    return {
        "path": rel,
        "line": line,
        "line_end": row["line_end"],
        "kind": str(row["kind"]),
        "ref_kind": None,
        "source": "structural",
        "confidence": "structural",
        "evidence": f"symbol '{row['name']}'",
        "snippet": _snippet(root, rel, line),
    }


def _ref_dict(
    row: sqlite3.Row, source_path: str | None, root: Path, extra: str = ""
) -> dict[str, Any]:
    rel = source_path or ""
    line = row["line"]
    evidence = (row["evidence"] or "") + (f" {extra}" if extra else "")
    return {
        "path": rel,
        "line": line,
        "line_end": None,
        "kind": str(row["ref_kind"]),
        "ref_kind": str(row["ref_kind"]),
        "source": str(row["source"]),
        "confidence": str(row["confidence"]),
        "evidence": evidence.strip() or None,
        "snippet": _snippet(root, rel, line),
    }


def find(
    conn: sqlite3.Connection,
    name: str,
    *,
    root: Path,
    scope: str | None = None,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    """Find symbol definitions and/or references matching ``name`` (DESIGN §9)."""
    results: list[dict[str, Any]] = []

    if kind is None or kind == "def":
        sql = (
            "SELECT s.name, s.kind, s.line_start, s.line_end, f.path "
            "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.name = ?"
        )
        params: list[Any] = [name]
        if scope:
            sql += " AND f.path LIKE ?"
            params.append(scope.rstrip("/") + "/%")
        for row in conn.execute(sql, params):
            results.append(_def_dict(row, root))

    if kind is None or kind in ("call", "ref", "mention"):
        sql = (
            "SELECT r.line, r.ref_kind, r.source, r.confidence, r.evidence, "
            "r.to_file_id, f.path AS from_path, t.path AS to_path "
            "FROM refs r "
            "JOIN files f ON r.from_file_id = f.id "
            "LEFT JOIN files t ON r.to_file_id = t.id "
            "WHERE r.to_symbol_id IN (SELECT id FROM symbols WHERE name = ?) "
            "OR r.evidence = ? OR r.evidence LIKE ?"
        )
        params = [name, name, name + ".%"]
        if kind == "call":
            sql += " AND r.ref_kind = 'calls'"
        elif kind == "mention":
            sql += " AND r.ref_kind IN ('mentions', 'path_mention')"
        if scope:
            sql += " AND f.path LIKE ?"
            params.append(scope.rstrip("/") + "/%")
        for row in conn.execute(sql, params):
            target = row["to_path"]
            extra = f"-> {target}" if target else ""
            results.append(_ref_dict(row, str(row["from_path"]), root, extra))
    return results


def refs_of(conn: sqlite3.Connection, path: str, *, root: Path) -> list[dict[str, Any]]:
    """Everything a file or symbol references (outgoing)."""
    fid, sym_ids = _resolve_file_or_symbol(conn, path)
    results: list[dict[str, Any]] = []
    sql = (
        "SELECT r.line, r.ref_kind, r.source, r.confidence, r.evidence, "
        "r.to_file_id, f.path AS from_path, t.path AS to_path "
        "FROM refs r "
        "JOIN files f ON r.from_file_id = f.id "
        "LEFT JOIN files t ON r.to_file_id = t.id "
        "WHERE r.from_file_id = ?"
    )
    if fid is not None:
        for row in conn.execute(sql, (fid,)):
            target = row["to_path"]
            results.append(
                _ref_dict(row, str(row["from_path"]), root, f"-> {target}" if target else "")
            )
    return results


def refby(conn: sqlite3.Connection, path: str, *, root: Path) -> list[dict[str, Any]]:
    """Everything that references a file or symbol (incoming)."""
    fid, sym_ids = _resolve_file_or_symbol(conn, path)
    if fid is not None:
        # A file path means "symbols defined in this file" too.
        sym_ids = list(dict.fromkeys(sym_ids + _symbol_ids_for_file(conn, fid)))

    results: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    def emit(row: sqlite3.Row) -> None:
        key = (str(row["from_path"]), row["line"], str(row["ref_kind"]), row["evidence"])
        if key in seen:
            return
        seen.add(key)
        results.append(_ref_dict(row, str(row["from_path"]), root, ""))

    if fid is not None:
        for row in conn.execute(
            "SELECT r.line, r.ref_kind, r.source, r.confidence, r.evidence, "
            "r.to_file_id, f.path AS from_path, t.path AS to_path "
            "FROM refs r JOIN files f ON r.from_file_id = f.id "
            "LEFT JOIN files t ON r.to_file_id = t.id "
            "WHERE r.to_file_id = ?",
            (fid,),
        ):
            emit(row)

    for sym_id in sym_ids:
        for row in conn.execute(
            "SELECT r.line, r.ref_kind, r.source, r.confidence, r.evidence, "
            "r.to_file_id, f.path AS from_path, t.path AS to_path "
            "FROM refs r JOIN files f ON r.from_file_id = f.id "
            "LEFT JOIN files t ON r.to_file_id = t.id "
            "WHERE r.to_symbol_id = ?",
            (sym_id,),
        ):
            emit(row)
    return results


def near(conn: sqlite3.Connection, path: str, *, root: Path) -> list[dict[str, Any]]:
    """Structural neighbors: symbols/refs in the same file and same directory."""
    results: list[dict[str, Any]] = []
    fid, _ = _resolve_file_or_symbol(conn, path)
    if fid is None:
        return results

    rel = _path(conn, fid) or path
    directory = str(Path(rel).parent) if "/" in rel else "."

    for row in conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.line_end, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.id WHERE s.file_id = ?",
        (fid,),
    ):
        results.append(_def_dict(row, root))

    for row in conn.execute(
        "SELECT s.name, s.kind, s.line_start, s.line_end, f.path "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE f.path LIKE ? AND s.file_id != ?",
        (directory.rstrip("/") + "/%", fid),
    ):
        results.append(_def_dict(row, root))

    for row in conn.execute(
        "SELECT r.line, r.ref_kind, r.source, r.confidence, r.evidence, "
        "r.to_file_id, f.path AS from_path, t.path AS to_path "
        "FROM refs r JOIN files f ON r.from_file_id = f.id "
        "LEFT JOIN files t ON r.to_file_id = t.id "
        "WHERE f.path LIKE ?",
        (directory.rstrip("/") + "/%",),
    ):
        target = row["to_path"]
        results.append(
            _ref_dict(row, str(row["from_path"]), root, f"-> {target}" if target else "")
        )
    return results
