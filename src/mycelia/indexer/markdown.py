from __future__ import annotations

import re
from typing import Any

from mycelia.indexer.enums import (
    CONFIDENCE_STRUCTURAL,
    REF_KIND_LINKS_TO,
    SOURCE_STRUCTURAL,
    SYMBOL_KIND_HEADER,
)

# Regular expressions for markdown parsing
_ATX_HEADER_REGEX = re.compile(r"^(#{1,6})\s+(.+?)(?:\s*\{#.*?\})?$")
_SETEXT_UNDERLINE_REGEX = re.compile(r"^(?:=|\-)\s*$")
_MARKDOWN_LINK_REGEX = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_WIKILINK_REGEX = re.compile(r"\[\[([^\]]+)\]\]")


def extract_markdown(relpath: str, text: str) -> dict[str, Any]:
    """Extract structural information from markdown files.

    Returns a dict with keys:
    - "symbols": list of header symbols
    - "refs": list of link references
    - "issues": empty list (markdown has no parse errors in this layer)

    Args:
        relpath: POSIX-style relative path from project root
        text: File contents as string (UTF-8 decoded)

    Returns:
        Extraction result dict
    """
    symbols: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []

    lines = text.splitlines()

    for line_num, line in enumerate(lines, 1):
        # Check for ATX headers (e.g., "# Header", "## Subheader", etc.)
        atx_match = _ATX_HEADER_REGEX.match(line)
        if atx_match:
            header_text = atx_match.group(2).strip()
            symbols.append(
                {
                    "name": header_text,
                    "kind": SYMBOL_KIND_HEADER,
                    "line_start": line_num,
                    "line_end": line_num,
                    "scope_path": None,  # Headers don't have nesting in this model
                }
            )
            continue

        # Check for setext headers (headers underlined with === or ---)
        if _SETEXT_UNDERLINE_REGEX.match(line):
            # This is a setext underline, the previous line should be the header
            if line_num > 1:
                prev_line_num = line_num - 1
                prev_line = lines[prev_line_num - 1]

                # Check if previous line could be a header (contains text)
                if prev_line.strip():
                    # Look for ATX header in previous line
                    atx_match = _ATX_HEADER_REGEX.match(prev_line)
                    if atx_match:
                        header_text = atx_match.group(2).strip()
                        symbols.append(
                            {
                                "name": header_text,
                                "kind": SYMBOL_KIND_HEADER,
                                "line_start": prev_line_num,
                                "line_end": line_num,
                                "scope_path": None,
                            }
                        )

        # Check for markdown links [label](target)
        for match in _MARKDOWN_LINK_REGEX.finditer(line):
            target = match.group(2).strip()
            refs.append(
                {
                    "to_symbol": target,  # Could be a file, URL, or symbol
                    "line": line_num,
                    "ref_kind": REF_KIND_LINKS_TO,
                    "source": SOURCE_STRUCTURAL,
                    "confidence": CONFIDENCE_STRUCTURAL,
                    "evidence": match.group(0),  # Full match as evidence
                }
            )

        # Check for wikilinks [[target]]
        for match in _WIKILINK_REGEX.finditer(line):
            target = match.group(1).strip()
            refs.append(
                {
                    "to_symbol": target,
                    "line": line_num,
                    "ref_kind": REF_KIND_LINKS_TO,
                    "source": SOURCE_STRUCTURAL,
                    "confidence": CONFIDENCE_STRUCTURAL,
                    "evidence": match.group(0),
                }
            )

    return {
        "symbols": symbols,
        "refs": refs,
        "issues": [],  # Markdown layer doesn't produce parse errors
    }
