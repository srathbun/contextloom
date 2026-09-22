from __future__ import annotations

from typing import Any

from mycelia.indexer.enums import (
    FILES_KIND_BINARY,
    FILES_KIND_CODE,
    FILES_KIND_MARKDOWN,
    FILES_KIND_TEXT,
    FILES_KIND_UNKNOWN,
    FILES_STATUS_BINARY,
    FILES_STATUS_OK,
    FILES_STATUS_UNSUPPORTED,
    ISSUE_KIND_BINARY_BLOB,
    ISSUE_KIND_TOO_LARGE,
    ISSUE_KIND_UNSUPPORTED_TYPE,
)

# Extension-to-language mapping for known code languages
_EXTENSION_TO_LANGUAGE: dict[str, str] = {
    # Python
    ".py": "python",
    # JavaScript/TypeScript
    ".js": "javascript",
    ".ts": "typescript",
    # Rust
    ".rs": "rust",
    # Go
    ".go": "go",
    # Java
    ".java": "java",
    # C/C++
    ".c": "c",
    ".cpp": "c++",
    ".cc": "c++",
    ".cxx": "c++",
    ".hpp": "c++",
    ".h": "c++",
    # C#
    ".cs": "c_sharp",
    # Ruby
    ".rb": "ruby",
    # Shell
    ".sh": "bash",
    ".bash": "bash",
    # JSON
    ".json": "json",
    # YAML
    ".yaml": "yaml",
    ".yml": "yaml",
    # TOML
    ".toml": "toml",
    # HTML
    ".html": "html",
    ".htm": "html",
    # CSS
    ".css": "css",
    # SQL
    ".sql": "sql",
}

# Text-like extensions that are not code
_TEXT_EXTENSIONS: set[str] = {
    ".txt",
    ".md",  # Markdown is handled specially
    ".markdown",
}

# Configuration files that are text but not code
_CONFIG_EXTENSIONS: set[str] = {
    ".yml",
    ".yaml",
    ".json",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".config",
}


def classify_file(relpath: str, data: bytes, max_size: int) -> dict[str, Any]:
    """Classify a file for indexing purposes.

    Returns a dict with keys:
    - "kind": str (code|markdown|text|binary|unknown)
    - "language": str|None (language for code files, None otherwise)
    - "status": str (ok|error|unsupported|binary)
    - "issue_kind": str|None (issue type if any)
    - "issue_detail": str|None (human-readable issue description)

    Args:
        relpath: POSIX-style relative path from project root
        data: File contents as bytes
        max_size: Maximum file size in bytes for processing

    Returns:
        Classification result dict
    """
    # Initialize result with defaults
    result: dict[str, Any] = {
        "kind": FILES_KIND_UNKNOWN,
        "language": None,
        "status": FILES_STATUS_OK,
        "issue_kind": None,
        "issue_detail": None,
    }

    # Check for binary content (NUL byte in the first 8192 bytes)
    if b"\x00" in data[:8192]:
        result.update(
            {
                "kind": FILES_KIND_BINARY,
                "status": FILES_STATUS_BINARY,
                "issue_kind": ISSUE_KIND_BINARY_BLOB,
                "issue_detail": "File contains binary content (NUL byte detected)",
            }
        )
        return result

    # Check file size too large
    if len(data) > max_size:
        result.update(
            {
                "status": FILES_STATUS_UNSUPPORTED,
                "issue_kind": ISSUE_KIND_TOO_LARGE,
                "issue_detail": f"size exceeds maximum {max_size} bytes",
            }
        )
        return result

    # Extract file extension properly
    ext = ""
    if "." in relpath:
        ext = "." + relpath.split(".")[-1].lower()

    # Handle markdown files (check extension before extracting)
    relpath_lower = relpath.lower()
    if relpath_lower.endswith(".md") or relpath_lower.endswith(".markdown"):
        result.update(
            {
                "kind": FILES_KIND_MARKDOWN,
                "language": "markdown",
                "status": FILES_STATUS_OK,
            }
        )
        return result

    # Check if it's a known code extension
    if ext in _EXTENSION_TO_LANGUAGE:
        result.update(
            {
                "kind": FILES_KIND_CODE,
                "language": _EXTENSION_TO_LANGUAGE[ext],
                "status": FILES_STATUS_OK,
            }
        )
        return result

    # Check if it's a text-like file
    if ext in _TEXT_EXTENSIONS or ext in _CONFIG_EXTENSIONS:
        result.update(
            {
                "kind": FILES_KIND_TEXT,
                "status": FILES_STATUS_OK,
            }
        )
        return result

    # For unknown extensions, check if it appears to be text
    try:
        # Try to decode as UTF-8 to see if it's text
        data.decode("utf-8")
        result.update(
            {
                "kind": FILES_KIND_TEXT,
                "status": FILES_STATUS_OK,
            }
        )
    except UnicodeDecodeError:
        # If it's not valid UTF-8, treat as unknown with error status
        result.update(
            {
                "kind": FILES_KIND_UNKNOWN,
                "status": FILES_STATUS_UNSUPPORTED,
                "issue_kind": ISSUE_KIND_UNSUPPORTED_TYPE,
                "issue_detail": "File type not recognized and cannot be decoded as UTF-8 text",
            }
        )

    return result
