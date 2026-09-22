"""Layer 1 structural extraction for code files via tree-sitter (DESIGN §6.1)."""

from __future__ import annotations

from typing import Any

from mycelia.indexer.enums import (
    CONFIDENCE_STRUCTURAL,
    REF_KIND_CALLS,
    REF_KIND_IMPORTS,
    SOURCE_STRUCTURAL,
    SYMBOL_KIND_CLASS,
    SYMBOL_KIND_FUNCTION,
    SYMBOL_KIND_METHOD,
)

# tree-sitter-language-pack uses "cpp"/"c_sharp"; classify.py uses "c++"/"c_sharp".
_LANGUAGE_ALIASES: dict[str, str] = {"c++": "cpp"}


def _node_text(node: Any, data: bytes) -> str:
    """Decode a node's source slice by *byte* offsets (safe under multi-byte UTF-8)."""
    if node is None:
        return ""
    try:
        return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    except (AttributeError, TypeError, UnicodeDecodeError):
        return ""


def extract_symbols(root_node: Any, data: bytes) -> list[dict[str, Any]]:
    """Walk the CST and return symbol dicts (name, kind, line_start, line_end, scope_path)."""
    symbols: list[dict[str, Any]] = []

    def walk(node: Any, parent_class: str | None) -> None:
        if node is None:
            return
        node_type = node.type

        if node_type == "class_definition":
            name = _node_text(node.child_by_field_name("name"), data)
            if name:
                symbols.append(
                    {
                        "name": name,
                        "kind": SYMBOL_KIND_CLASS,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "scope_path": None,
                    }
                )
            for child in node.children:
                walk(child, name)
            return

        if node_type == "function_definition":
            name = _node_text(node.child_by_field_name("name"), data)
            if name:
                symbols.append(
                    {
                        "name": name,
                        "kind": SYMBOL_KIND_METHOD if parent_class else SYMBOL_KIND_FUNCTION,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "scope_path": f"{parent_class}.{name}" if parent_class else None,
                    }
                )
            return

        # Generic descent reaches top-level defs (the module root is neither
        # class nor function).
        for child in node.children:
            walk(child, parent_class)

    walk(root_node, None)
    return symbols


def extract_structural_refs(
    root_node: Any, data: bytes, symbols: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Extract structural references (calls, imports) from the CST."""
    refs: list[dict[str, Any]] = []
    local_names = {sym["name"] for sym in symbols}

    def walk(node: Any) -> None:
        if node is None:
            return
        node_type = node.type

        if node_type == "call":
            func = node.child_by_field_name("function")
            if func is not None:
                name = _node_text(func, data)
                if name:
                    refs.append(
                        {
                            "from_symbol": None,
                            "to_symbol": name if name in local_names else None,
                            "line": node.start_point[0] + 1,
                            "ref_kind": REF_KIND_CALLS,
                            "source": SOURCE_STRUCTURAL,
                            "confidence": CONFIDENCE_STRUCTURAL,
                            "evidence": name,
                        }
                    )

        elif node_type in ("import_statement", "import_from_statement"):
            module = node.child_by_field_name("module") or node.child_by_field_name("name")
            module_text = _node_text(module, data)
            names_node = node.child_by_field_name("name")
            imported = _node_text(names_node, data) if node_type == "import_from_statement" else ""
            evidence = f"{module_text}.{imported}" if imported else module_text
            if module_text or imported:
                refs.append(
                    {
                        "from_symbol": None,
                        "to_symbol": imported or module_text,
                        "line": node.start_point[0] + 1,
                        "ref_kind": REF_KIND_IMPORTS,
                        "source": SOURCE_STRUCTURAL,
                        "confidence": CONFIDENCE_STRUCTURAL,
                        "evidence": evidence,
                    }
                )

        for child in node.children:
            walk(child)

    walk(root_node)
    return refs


def extract_code(language: str, text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse ``text`` with tree-sitter and return ``(symbols, structural_refs)``.

    Returns empty lists if the language has no available grammar or parsing fails.
    """
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return [], []

    lang = _LANGUAGE_ALIASES.get(language, language)
    try:
        parser = get_parser(lang)
        data = text.encode("utf-8")
        tree = parser.parse(data)
    except Exception:
        return [], []

    symbols = extract_symbols(tree.root_node, data)
    refs = extract_structural_refs(tree.root_node, data, symbols)
    return symbols, refs
