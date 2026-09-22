"""Tests for index storage, schema, discovery, config, and locking."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from contextloom.config import DEFAULT_FUZZY_LEVEL, DEFAULT_IGNORES, DEFAULT_MAX_FILE_SIZE
from contextloom.db import (
    SCHEMA_VERSION,
    ContextloomError,
    IndexLock,
    create_index,
    discover_index,
    get_config,
    open_index,
    set_config,
    status,
)

EXPECTED_TABLES = {"meta", "files", "file_issues", "symbols", "refs", "config"}


def test_init_seeds_schema_meta_and_config(tmp_path: Path) -> None:
    db = create_index(tmp_path)
    assert db == tmp_path / "contextloom.db"
    assert db.is_file()

    conn = open_index(db, readonly=True)
    try:
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert EXPECTED_TABLES <= tables
    finally:
        conn.close()

    info = status(db)
    assert info["schema_version"] == SCHEMA_VERSION
    assert info["counts"] == {"files": 0, "symbols": 0, "refs": 0, "file_issues": 0}
    assert info["config"]["fuzzy_level"] == DEFAULT_FUZZY_LEVEL
    assert info["config"]["ignores"] == DEFAULT_IGNORES
    assert info["config"]["max_file_size"] == DEFAULT_MAX_FILE_SIZE
    assert info["config"]["ai_enabled"] is False


def test_init_refuses_existing_without_force(tmp_path: Path) -> None:
    create_index(tmp_path)
    with pytest.raises(ContextloomError, match="already exists"):
        create_index(tmp_path)
    assert create_index(tmp_path, force=True).is_file()


def test_init_is_atomic_no_temp_leftover(tmp_path: Path) -> None:
    create_index(tmp_path)
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_discover_index_walks_up(tmp_path: Path) -> None:
    db = create_index(tmp_path)
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert discover_index(deep) == db
    assert discover_index(tmp_path) == db


def test_discover_index_not_found(tmp_path: Path) -> None:
    assert discover_index(tmp_path) is None


def test_open_enforces_schema_version(tmp_path: Path) -> None:
    db = create_index(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(ContextloomError, match="v999"):
        open_index(db, readonly=True)


def test_config_get_set_roundtrip(tmp_path: Path) -> None:
    db = create_index(tmp_path)
    assert get_config(db, "fuzzy_level") == '"generous"'
    set_config(db, "fuzzy_level", '"balanced"')
    assert get_config(db, "fuzzy_level") == '"balanced"'
    set_config(db, "custom", "hello world")
    assert get_config(db, "custom") == "hello world"


def test_lock_is_exclusive(tmp_path: Path) -> None:
    db = create_index(tmp_path)
    with IndexLock(db):
        with pytest.raises(ContextloomError, match="already in progress"):
            with IndexLock(db):
                pass
    with IndexLock(db):
        pass
