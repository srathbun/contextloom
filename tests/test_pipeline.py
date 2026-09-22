"""End-to-end tests for the indexing pipeline and query layer."""

from __future__ import annotations

from pathlib import Path

from mycelia import query
from mycelia.db import create_index, discover_index, open_index
from mycelia.staleness import gc, is_stale, update


def _write_sample(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / "util.py").write_text(
        "def greet(name):\n"
        '    return "hello " + name\n'
        "\n\n"
        "class Widget:\n"
        "    def render(self):\n"
        '        return greet("world")\n'
    )
    (root / "pkg" / "main.py").write_text(
        'from pkg.util import greet, Widget\n\n\ndef run():\n    greet("agent")\n'
    )
    (root / "README.md").write_text("# Demo\n\nSee pkg/util.py for greet.\n")


def _open(tmp_path: Path):
    index = discover_index(tmp_path)
    assert index is not None
    return open_index(index, readonly=True)


def test_update_builds_index_and_serves_queries(tmp_path: Path) -> None:
    _write_sample(tmp_path)
    create_index(tmp_path)
    summary = update(tmp_path)

    assert summary["files"] == 3
    assert summary["symbols"] >= 3
    assert summary["refs"] >= 1
    assert summary["issues"] == 0

    conn = _open(tmp_path)
    try:
        defs = query.find(conn, "greet", root=tmp_path, kind="def")
        assert any(d["kind"] == "function" and d["path"] == "pkg/util.py" for d in defs)

        incoming = query.refby(conn, "pkg/util.py", root=tmp_path)
        assert any(r["path"] == "pkg/main.py" and r["ref_kind"] == "imports" for r in incoming)

        outgoing = query.refs_of(conn, "pkg/main.py", root=tmp_path)
        assert any(r["ref_kind"] == "imports" for r in outgoing)

        mentions = query.find(conn, "greet", root=tmp_path, kind="mention")
        assert any(r["ref_kind"] == "mentions" for r in mentions)
    finally:
        conn.close()


def test_update_excludes_index_file(tmp_path: Path) -> None:
    _write_sample(tmp_path)
    create_index(tmp_path)
    update(tmp_path)
    conn = _open(tmp_path)
    try:
        paths = [r["path"] for r in conn.execute("SELECT path FROM files")]
        assert "mycelia.db" not in paths
    finally:
        conn.close()


def test_is_stale_detects_change_and_self_heal(tmp_path: Path) -> None:
    _write_sample(tmp_path)
    create_index(tmp_path)
    update(tmp_path)
    assert is_stale(tmp_path) is False

    (tmp_path / "pkg" / "util.py").write_text('def greet(name):\n    return "hi " + name\n')
    assert is_stale(tmp_path) is True
    update(tmp_path)
    assert is_stale(tmp_path) is False


def test_gc_removes_missing_file(tmp_path: Path) -> None:
    _write_sample(tmp_path)
    create_index(tmp_path)
    update(tmp_path)
    (tmp_path / "README.md").unlink()

    result = gc(tmp_path)
    assert result["removed_files"] == 1
    conn = _open(tmp_path)
    try:
        paths = [r["path"] for r in conn.execute("SELECT path FROM files")]
        assert "README.md" not in paths
    finally:
        conn.close()
