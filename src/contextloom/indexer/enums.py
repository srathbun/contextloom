from __future__ import annotations

# Source constants
SOURCE_STRUCTURAL = "structural"
SOURCE_HEURISTIC = "heuristic"
SOURCE_AI = "ai"

# Confidence constants
CONFIDENCE_STRUCTURAL = "structural"
CONFIDENCE_EXACT_SCOPED_MATCH = "exact_scoped_match"
CONFIDENCE_EXACT_PROJECT_MATCH = "exact_project_match"
CONFIDENCE_PATH_MENTION = "path_mention"
CONFIDENCE_NAME_CORRELATION = "name_correlation"
CONFIDENCE_AI_INFERRED = "ai_inferred"

# Ref kind constants
REF_KIND_CALLS = "calls"
REF_KIND_IMPORTS = "imports"
REF_KIND_LINKS_TO = "links_to"
REF_KIND_MENTIONS = "mentions"
REF_KIND_SAME_NAME_MATCH = "same_name_match"
REF_KIND_PATH_MENTION = "path_mention"
REF_KIND_INFERRED_RELATED = "inferred_related"

# Symbol kind constants (from tree-sitter nodes and built-ins)
SYMBOL_KIND_FUNCTION = "function"
SYMBOL_KIND_CLASS = "class"
SYMBOL_KIND_METHOD = "method"
SYMBOL_KIND_HEADER = "header"
SYMBOL_KIND_CONST = "const"
SYMBOL_KIND_MODULE = "module"

# Files kind constants
FILES_KIND_CODE = "code"
FILES_KIND_MARKDOWN = "markdown"
FILES_KIND_TEXT = "text"
FILES_KIND_BINARY = "binary"
FILES_KIND_UNKNOWN = "unknown"

# Files status constants
FILES_STATUS_OK = "ok"
FILES_STATUS_ERROR = "error"
FILES_STATUS_UNSUPPORTED = "unsupported"
FILES_STATUS_BINARY = "binary"

# Issue kind constants
ISSUE_KIND_PARSE_ERROR = "parse_error"
ISSUE_KIND_BINARY_BLOB = "binary_blob"
ISSUE_KIND_UNSUPPORTED_TYPE = "unsupported_type"
ISSUE_KIND_TOO_LARGE = "too_large"

# Re-export for convenience
__all__ = [
    # Source
    "SOURCE_STRUCTURAL",
    "SOURCE_HEURISTIC",
    "SOURCE_AI",
    # Confidence
    "CONFIDENCE_STRUCTURAL",
    "CONFIDENCE_EXACT_SCOPED_MATCH",
    "CONFIDENCE_EXACT_PROJECT_MATCH",
    "CONFIDENCE_PATH_MENTION",
    "CONFIDENCE_NAME_CORRELATION",
    "CONFIDENCE_AI_INFERRED",
    # Ref kind
    "REF_KIND_CALLS",
    "REF_KIND_IMPORTS",
    "REF_KIND_LINKS_TO",
    "REF_KIND_MENTIONS",
    "REF_KIND_SAME_NAME_MATCH",
    "REF_KIND_PATH_MENTION",
    "REF_KIND_INFERRED_RELATED",
    # Symbol kind
    "SYMBOL_KIND_FUNCTION",
    "SYMBOL_KIND_CLASS",
    "SYMBOL_KIND_METHOD",
    "SYMBOL_KIND_HEADER",
    "SYMBOL_KIND_CONST",
    "SYMBOL_KIND_MODULE",
    # Files kind
    "FILES_KIND_CODE",
    "FILES_KIND_MARKDOWN",
    "FILES_KIND_TEXT",
    "FILES_KIND_BINARY",
    "FILES_KIND_UNKNOWN",
    # Files status
    "FILES_STATUS_OK",
    "FILES_STATUS_ERROR",
    "FILES_STATUS_UNSUPPORTED",
    "FILES_STATUS_BINARY",
    # Issue kind
    "ISSUE_KIND_PARSE_ERROR",
    "ISSUE_KIND_BINARY_BLOB",
    "ISSUE_KIND_UNSUPPORTED_TYPE",
    "ISSUE_KIND_TOO_LARGE",
]
