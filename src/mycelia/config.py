"""Project configuration defaults and validation (DESIGN §5.4, §7).

Configuration lives inside the index's ``config`` table, not in a separate repo
file, so there is nothing to drift out of sync. ``init`` seeds these defaults;
``mycelia config get/set`` reads and writes them. Values are JSON-encoded where
structured (see §5.4).
"""

from __future__ import annotations

import json
from typing import Any

from mycelia.errors import MyceliaError

# Built-in ignore patterns, applied in addition to any project ``.gitignore`` (§7).
DEFAULT_IGNORES: list[str] = [
    ".git/",
    ".hg/",
    ".svn/",
    "node_modules/",
    ".venv/",
    "venv/",
    "env/",
    "__pycache__/",
    "dist/",
    "build/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    "*.pyc",
    "*.pyo",
    "mycelia.db",
    "mycelia.db.tmp-*",
    "mycelia.db.lock",
]

# Files larger than this (bytes) are tagged ``status='unsupported'`` / ``too_large``
# rather than parsed (§7).
DEFAULT_MAX_FILE_SIZE: int = 2_000_000

# Layer 2 fuzzy-matching aggressiveness (§7): generous | balanced | strict.
DEFAULT_FUZZY_LEVEL: str = "generous"

# Layer 3 (AI) linkage opt-in; enabling only makes ``infer`` available (§6.3).
DEFAULT_AI_ENABLED: bool = False

# Ollama model used by ``infer`` (Layer 3) when no custom backend is injected.
DEFAULT_AI_MODEL: str = "gpt-oss:20b"

KNOWN_KEYS: tuple[str, ...] = (
    "ignores",
    "max_file_size",
    "fuzzy_level",
    "ai_enabled",
    "ai_model",
)
FUZZY_LEVELS: tuple[str, ...] = ("generous", "balanced", "strict")


def default_config() -> dict[str, str]:
    """Return the seeded ``config`` table as ``{key: json-encoded value}``."""
    return {
        "ignores": json.dumps(DEFAULT_IGNORES),
        "max_file_size": json.dumps(DEFAULT_MAX_FILE_SIZE),
        "fuzzy_level": json.dumps(DEFAULT_FUZZY_LEVEL),
        "ai_enabled": json.dumps(DEFAULT_AI_ENABLED),
        "ai_model": json.dumps(DEFAULT_AI_MODEL),
    }


def normalize_config_value(key: str, raw: str) -> str:
    """Validate a user-supplied value and return its stored (JSON) form.

    Unknown keys are stored verbatim.
    """
    if key == "ignores":
        try:
            parsed: Any = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MyceliaError("'ignores' must be a JSON array of strings") from exc
        if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
            raise MyceliaError("'ignores' must be a JSON array of strings")
        return json.dumps(parsed)
    if key == "max_file_size":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MyceliaError("'max_file_size' must be a non-negative integer") from exc
        if not isinstance(parsed, int) or isinstance(parsed, bool) or parsed < 0:
            raise MyceliaError("'max_file_size' must be a non-negative integer")
        return json.dumps(parsed)
    if key == "fuzzy_level":
        if raw not in FUZZY_LEVELS:
            raise MyceliaError(f"'fuzzy_level' must be one of: {', '.join(FUZZY_LEVELS)}")
        return json.dumps(raw)
    if key == "ai_enabled":
        if raw not in ("true", "false"):
            raise MyceliaError("'ai_enabled' must be 'true' or 'false'")
        return raw
    if key == "ai_model":
        if not raw.strip():
            raise MyceliaError("'ai_model' must be a non-empty model name")
        return json.dumps(raw)
    return raw


def decode_config_value(value: str) -> Any:
    """Decode a stored config value; fall back to the raw string if not JSON."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
