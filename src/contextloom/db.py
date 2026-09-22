"""SQLite index storage, schema, and concurrency primitives (DESIGN §5).

Owns everything about the ``contextloom.db`` file on disk:

* upward discovery (like git finding ``.git``)
* creation and schema DDL, seeded with metadata and config
* opening with schema-version enforcement
* atomic build-and-swap (writes go to a temp file, ``os.replace`` promotes it),
  so concurrent readers never observe a half-written index
* an advisory lock that serializes writers

The indexing pipeline and query layer are separate modules landed in later
milestones (see DESIGN §12).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, BinaryIO
from urllib.parse import quote
from uuid import uuid4

from contextloom import __version__
from contextloom.config import decode_config_value, default_config
from contextloom.errors import ContextloomError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

DB_FILENAME = "contextloom.db"
LOCK_SUFFIX = ".lock"
SCHEMA_VERSION = 1
TOOL_VERSION = __version__

SCHEMA: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS meta ( key   TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS files ("
    " id        INTEGER PRIMARY KEY,"
    " path      TEXT NOT NULL UNIQUE,"
    " hash      TEXT NOT NULL,"
    " mtime     REAL NOT NULL,"
    " size      INTEGER NOT NULL,"
    " kind      TEXT NOT NULL,"
    " language  TEXT,"
    " status    TEXT NOT NULL DEFAULT 'ok'"
    ")",
    "CREATE TABLE IF NOT EXISTS file_issues ("
    " id          INTEGER PRIMARY KEY,"
    " file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,"
    " issue_kind  TEXT NOT NULL,"
    " detail      TEXT"
    ")",
    "CREATE TABLE IF NOT EXISTS symbols ("
    " id          INTEGER PRIMARY KEY,"
    " file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,"
    " name        TEXT NOT NULL,"
    " kind        TEXT NOT NULL,"
    " line_start  INTEGER NOT NULL,"
    " line_end    INTEGER,"
    " scope_path  TEXT"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name)",
    "CREATE TABLE IF NOT EXISTS refs ("
    " id              INTEGER PRIMARY KEY,"
    " from_file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,"
    " from_symbol_id  INTEGER REFERENCES symbols(id) ON DELETE CASCADE,"
    " to_file_id      INTEGER REFERENCES files(id) ON DELETE CASCADE,"
    " to_symbol_id    INTEGER REFERENCES symbols(id) ON DELETE CASCADE,"
    " line            INTEGER,"
    " ref_kind        TEXT NOT NULL,"
    " source          TEXT NOT NULL,"
    " confidence      TEXT NOT NULL,"
    " evidence        TEXT"
    ")",
    "CREATE INDEX IF NOT EXISTS idx_refs_from ON refs(from_file_id, from_symbol_id)",
    "CREATE INDEX IF NOT EXISTS idx_refs_to ON refs(to_file_id, to_symbol_id)",
    "CREATE TABLE IF NOT EXISTS config ( key   TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def discover_index(start: Path | None = None) -> Path | None:
    """Walk upward from ``start`` (default cwd) looking for ``contextloom.db``."""
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / DB_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    else:
        conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _check_schema(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        raise ContextloomError(
            "index has no 'schema_version'; run 'contextloom init --force' to rebuild"
        )
    version = int(row["value"])
    if version != SCHEMA_VERSION:
        raise ContextloomError(
            f"index schema v{version} is not supported (this build supports "
            f"v{SCHEMA_VERSION}); run 'contextloom init --force' to rebuild"
        )


def open_index(path: Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Open an index, enforcing the supported schema version."""
    if not path.is_file():
        raise ContextloomError(f"no index at '{path}'; run 'contextloom init' first")
    conn = _connect(path, readonly=readonly)
    try:
        _check_schema(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _seed_meta(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        {
            "schema_version": str(SCHEMA_VERSION),
            "tool_version": TOOL_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_full_index_at": "",
        }.items(),
    )


def _seed_config(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO config (key, value) VALUES (?, ?)",
        default_config().items(),
    )


def create_index(root: Path, *, force: bool = False) -> Path:
    """Create a fresh index at ``root`` (atomic: build in a temp file, then swap)."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / DB_FILENAME
    if db_path.exists() and not force:
        raise ContextloomError(f"index already exists at '{db_path}'; use --force to overwrite")

    tmp = root / f"{DB_FILENAME}.tmp-{uuid4().hex}"
    try:
        conn = sqlite3.connect(str(tmp))
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(";\n".join(SCHEMA))
            _seed_meta(conn)
            _seed_config(conn)
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, db_path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return db_path


def status(db_path: Path) -> dict[str, Any]:
    """Return an index health summary (schema version, counts, config)."""
    conn = open_index(db_path, readonly=True)
    try:
        meta = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("files", "symbols", "refs", "file_issues")
        }
        raw_config = {
            row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM config")
        }
    finally:
        conn.close()
    return {
        "path": str(db_path),
        "schema_version": int(meta["schema_version"]),
        "tool_version": meta["tool_version"],
        "created_at": meta["created_at"],
        "last_full_index_at": meta.get("last_full_index_at", ""),
        "counts": counts,
        "config": {key: decode_config_value(value) for key, value in raw_config.items()},
    }


def get_config(db_path: Path, key: str) -> str | None:
    """Return the raw stored value for ``key`` (JSON-encoded for known keys)."""
    conn = open_index(db_path, readonly=True)
    try:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return None if row is None else str(row["value"])


def set_config(db_path: Path, key: str, value: str) -> None:
    """Write a config value (upsert)."""
    conn = open_index(db_path, readonly=False)
    try:
        conn.execute(
            "INSERT INTO config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _lock_file(fh: BinaryIO) -> None:
    if sys.platform == "win32":
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(fh: BinaryIO) -> None:
    if sys.platform == "win32":
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


class IndexLock:
    """Advisory lock serializing index writers (DESIGN §5.3).

    Readers do not take this lock: they open the database read-only and are
    always safe because writers never modify the live file in place.
    """

    def __init__(self, db_path: Path) -> None:
        self._lock_path = db_path.with_name(db_path.name + LOCK_SUFFIX)
        self._fh: BinaryIO | None = None

    def __enter__(self) -> IndexLock:
        fh = self._lock_path.open("a+b")
        try:
            _lock_file(fh)
        except OSError as exc:
            fh.close()
            raise ContextloomError("another update is already in progress") from exc
        self._fh = fh
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._fh is not None:
            _unlock_file(self._fh)
            self._fh.close()
            self._fh = None
