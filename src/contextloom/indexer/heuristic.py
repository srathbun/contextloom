"""Layer 2 heuristic linkage over prose (DESIGN §6.2) — deterministic, no AI."""

from __future__ import annotations

import re
from typing import Any

from contextloom.indexer.enums import (
    CONFIDENCE_EXACT_PROJECT_MATCH,
    CONFIDENCE_PATH_MENTION,
    REF_KIND_MENTIONS,
    REF_KIND_PATH_MENTION,
    SOURCE_HEURISTIC,
)

_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _fenced(lines: list[str]) -> list[bool]:
    """Return a per-line mask: True when the line is inside a code fence."""
    in_fence = False
    mask: list[bool] = []
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            mask.append(False)  # the fence marker itself
            continue
        mask.append(in_fence)
    return mask


def link_heuristic(
    relpath: str, text: str, project_files: set[str], symbol_names: set[str]
) -> list[dict[str, Any]]:
    """Produce heuristic refs: filename mentions and identifier mentions."""
    refs: list[dict[str, Any]] = []
    lines = text.splitlines()
    fenced = _fenced(lines)

    basename_to_path: dict[str, str] = {}
    for f in project_files:
        basename_to_path.setdefault(f.lower().rsplit("/", 1)[-1], f)

    seen: set[tuple[str, str | None, int]] = set()

    for line_num, line in enumerate(lines, 1):
        if fenced[line_num - 1]:
            continue

        tokens = _TOKEN_RE.findall(line)
        for token in tokens:
            path = basename_to_path.get(token.lower())
            if path is not None:
                key = (REF_KIND_PATH_MENTION, path, line_num)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        {
                            "to_file": path,
                            "to_symbol": None,
                            "line": line_num,
                            "ref_kind": REF_KIND_PATH_MENTION,
                            "source": SOURCE_HEURISTIC,
                            "confidence": CONFIDENCE_PATH_MENTION,
                            "evidence": token,
                        }
                    )
            if token in symbol_names:
                key = (REF_KIND_MENTIONS, token, line_num)
                if key not in seen:
                    seen.add(key)
                    refs.append(
                        {
                            "to_file": None,
                            "to_symbol": token,
                            "line": line_num,
                            "ref_kind": REF_KIND_MENTIONS,
                            "source": SOURCE_HEURISTIC,
                            "confidence": CONFIDENCE_EXACT_PROJECT_MATCH,
                            "evidence": token,
                        }
                    )

    return refs
