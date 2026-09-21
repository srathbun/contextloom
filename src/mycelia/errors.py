"""User-facing error types."""

from __future__ import annotations


class MyceliaError(Exception):
    """Base error for user-facing failures (reported to stderr, exit code 1)."""
