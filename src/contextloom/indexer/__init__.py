"""Indexing pipeline: classification, Layer 1 extraction, Layer 2 linkage."""

from __future__ import annotations

from contextloom.indexer.classify import classify_file
from contextloom.indexer.code import extract_code, extract_structural_refs, extract_symbols
from contextloom.indexer.heuristic import link_heuristic
from contextloom.indexer.markdown import extract_markdown

__all__ = [
    "classify_file",
    "extract_code",
    "extract_structural_refs",
    "extract_symbols",
    "extract_markdown",
    "link_heuristic",
]
