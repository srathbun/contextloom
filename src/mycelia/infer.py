"""Layer 3: AI-assisted linkage inference (DESIGN §6.3, implementation contract).

Optional, opt-in semantic/inferred linkage. Base install has no AI backend:
reads project config, validates AI opt-in, and calls an injectable backend
module-level hook ``mycelia.infer._backend``.

The backend must be a callable accepting ``(conn, *, scope=None)`` and
returning ``list[dict]`` where each dict will be written with
``source='ai'``, ``ref_kind='inferred_related'``, ``confidence='ai_inferred'``.
This is the honest v1 boundary — no fabricated LLM output is ever emitted.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from mycelia.errors import MyceliaError

# Module-level backend hook. Default is None, meaning no AI backend is
# configured. Consumers can set ``mycelia.infer._backend`` to a callable.
_backend: Callable[..., list[dict[str, Any]]] | None = None


def infer(conn: Any, *, scope: str | None = None) -> list[dict[str, Any]]:
    """Return AI-generated linkage rows for the given scope.

    Reads ``config`` table ``ai_enabled``; if false, raises ``MyceliaError``
    guiding the user to enable Layer 3 opt-in via CLI. If enabled, calls the
    injectable ``mycelia.infer._backend`` hook (if present) and returns its
    results, tagging each row with Layer 3 source attributes.

    The caller is expected to write returned rows using the standard refs
    schema with ``source='ai'``, ``ref_kind='inferred_related'``,
    ``confidence='ai_inferred'`` (see DESIGN §6.1–§6.4).
    """
    # 1. Read ai_enabled config. The ``config`` table stores values as JSON
    #    strings in the ``value`` column.
    row = conn.execute("SELECT value FROM config WHERE key = 'ai_enabled'").fetchone()
    if row is None:
        # Not set yet — default is False per DESIGN §5.4.
        raise MyceliaError(
            "Layer 3 is opt-in; enable with 'mycelia config set ai_enabled true' "
            "and provide an AI backend"
        )
    ai_enabled_raw: str = row["value"]
    try:
        ai_enabled: bool = json.loads(ai_enabled_raw)
    except json.JSONDecodeError as exc:
        raise MyceliaError(f"Invalid ai_enabled config value: {ai_enabled_raw!r}") from exc
    if not ai_enabled:
        raise MyceliaError(
            "Layer 3 is opt-in; enable with 'mycelia config set ai_enabled true' "
            "and provide an AI backend"
        )

    # 2. Backend hook is the injectable AI implementation.
    backend = _backend
    if backend is None:
        raise MyceliaError("no AI backend configured")

    # 3. Backend must be callable; call it with conn and optional scope.
    if not callable(backend):
        raise MyceliaError(f"mycelia.infer._backend is set to non-callable: {backend!r}")

    results: list[dict[str, Any]] = backend(conn, scope=scope)
    # Validate shape: ensure we got a list and each element is a dict.
    if not isinstance(results, list):
        raise MyceliaError(f"AI backend must return a list; got {type(results).__name__}")
    for i, row in enumerate(results):
        if not isinstance(row, dict):
            raise MyceliaError(f"AI backend row[{i}] is not a dict: {row!r}")

    # The caller is responsible for writing each dict to refs table with:
    #   source='ai'
    #   ref_kind='inferred_related'
    #   confidence='ai_inferred'
    # This function only validates and passes through backend output.

    return results
