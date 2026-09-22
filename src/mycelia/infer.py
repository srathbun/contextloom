"""Layer 3: AI-assisted semantic linkage via a local Ollama model (DESIGN §6.3).

Opt-in and never automatic: ``infer`` reads ``ai_enabled``, prompts the
configured Ollama model to propose conceptually-related file pairs, and persists
them with ``source='ai'``, ``ref_kind='inferred_related'``,
``confidence='ai_inferred'``. ``gc --ai-refs`` prunes them.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from mycelia.errors import MyceliaError

OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gpt-oss:20b"

# Programmatic override hook. When None, the built-in Ollama backend is used.
# If set, it must be a callable ``(conn, *, scope) -> list[dict]`` returning
# link dicts of the shape ``{"from_file", "to_file", "reason"}``.
_backend: Callable[..., list[dict[str, Any]]] | None = None

_SYSTEM = "You are a code-indexing assistant. Respond with valid JSON only, no commentary."


def _config(conn: sqlite3.Connection, key: str, default: Any) -> Any:
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def _file_id(conn: sqlite3.Connection, path: str | None) -> int | None:
    if not path:
        return None
    row = conn.execute("SELECT id FROM files WHERE path = ?", (path,)).fetchone()
    return None if row is None else int(row["id"])


def _ollama(model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise MyceliaError(f"could not reach Ollama at {OLLAMA_URL}: {exc.reason}") from exc
    if "error" in body:
        raise MyceliaError(f"Ollama error: {body['error']}")
    return str(body.get("message", {}).get("content", ""))


def _listing(conn: sqlite3.Connection) -> str:
    lines: list[str] = []
    for r in conn.execute("SELECT id, path, kind FROM files WHERE status = 'ok' ORDER BY path"):
        syms = [
            str(s["name"])
            for s in conn.execute(
                "SELECT name FROM symbols WHERE file_id = ? ORDER BY line_start LIMIT 12",
                (r["id"],),
            )
        ]
        suffix = f": {', '.join(syms)}" if syms else ""
        lines.append(f"- {r['path']} ({r['kind']}){suffix}")
    return "\n".join(lines)


def infer(conn: sqlite3.Connection, *, scope: str | None = None) -> list[dict[str, Any]]:
    """Propose and persist conceptual links (Layer 3)."""
    if _config(conn, "ai_enabled", False) is not True:
        raise MyceliaError("Layer 3 is opt-in; enable with 'mycelia config set ai_enabled true'")

    model = _config(conn, "ai_model", DEFAULT_MODEL)

    links: Any
    if _backend is not None:
        links = _backend(conn, scope=scope)
    else:
        prompt = (
            "Given a repository with these files (path (kind): defined symbols/headers):\n\n"
            f"{_listing(conn)}\n\n"
            "Propose up to 20 pairs of files that are conceptually related but NOT already "
            "connected by an explicit import, call, or markdown link. Prefer documentation "
            "\u2194 code and module \u2194 module relationships. Respond with JSON only:\n"
            '{"links": [{"from_file": "<path>", "to_file": "<path>", "reason": "<one sentence>"}]}'
        )
        raw = _ollama(model, prompt)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MyceliaError(f"model returned invalid JSON: {exc}") from exc
        links = parsed.get("links") if isinstance(parsed, dict) else None
        if not isinstance(links, list):
            raise MyceliaError("model returned a malformed response (no 'links' array)")

    written: list[dict[str, Any]] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        from_file = link.get("from_file")
        to_file = link.get("to_file")
        reason = str(link.get("reason") or "").strip()
        if scope:
            prefix = scope.rstrip("/")
            if str(from_file) != prefix and not str(from_file).startswith(prefix + "/"):
                continue
        from_id = _file_id(conn, from_file)
        to_id = _file_id(conn, to_file)
        if from_id is None or to_id is None or from_id == to_id:
            continue
        dup = conn.execute(
            "SELECT 1 FROM refs WHERE from_file_id = ? AND to_file_id = ? AND source = 'ai'",
            (from_id, to_id),
        ).fetchone()
        if dup is not None:
            continue
        conn.execute(
            "INSERT INTO refs (from_file_id, to_file_id, line, ref_kind, source, "
            "confidence, evidence) "
            "VALUES (?, ?, NULL, 'inferred_related', 'ai', 'ai_inferred', ?)",
            (from_id, to_id, reason),
        )
        written.append(
            {
                "path": from_file,
                "line": None,
                "kind": "inferred_related",
                "ref_kind": "inferred_related",
                "source": "ai",
                "confidence": "ai_inferred",
                "evidence": f"{from_file} -> {to_file}: {reason}"
                if reason
                else f"{from_file} -> {to_file}",
                "snippet": None,
            }
        )
    conn.commit()
    return written
