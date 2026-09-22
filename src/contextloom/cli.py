"""Command-line interface (DESIGN §9).

Commands: ``init``, ``update``, ``status``, ``find``, ``refs``, ``refby``,
``near``, ``config get/set``, ``gc``, ``infer``. Every query command self-heals
($.10): it triggers an incremental ``update`` if the on-disk tree is stale before
answering, so callers never need to remember to reindex.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from contextloom import __version__, query
from contextloom.config import KNOWN_KEYS, decode_config_value, normalize_config_value
from contextloom.db import create_index, discover_index, get_config, open_index, set_config, status
from contextloom.errors import ContextloomError
from contextloom.infer import infer
from contextloom.staleness import gc, is_stale, update

CommandHandler = Callable[[argparse.Namespace], int]


def _emit_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _require_index() -> Path:
    index = discover_index()
    if index is None:
        raise ContextloomError(
            "no 'contextloom.db' found in this directory or any parent; "
            "run 'contextloom init' first"
        )
    return index


def _fresh_connection(index: Path, *, readonly: bool) -> Any:
    """Self-healing open: update the index if the tree is stale, then open."""
    if is_stale(index.parent):
        update(index.parent)
    return open_index(index, readonly=readonly)


def _print_results(results: list[dict[str, Any]], *, as_json: bool) -> None:
    if as_json:
        _emit_json(results)
        return
    if not results:
        print("no matches")
        return
    for item in results:
        line = item.get("line")
        loc = f"{item['path']}:{line}" if line else str(item["path"])
        tag = item.get("ref_kind") or item.get("kind") or ""
        confidence = item.get("confidence") or ""
        print(f"{loc}  [{tag}] {confidence}")
        evidence = item.get("evidence")
        if evidence:
            print(f"      evidence: {evidence}")
        snippet = item.get("snippet")
        if snippet:
            print(f"      {snippet}")


def _cmd_init(args: argparse.Namespace) -> int:
    db_path = create_index(Path.cwd(), force=args.force)
    print(f"initialized index at {db_path}")
    print("recommend adding 'contextloom.db' and 'contextloom.db.tmp-*' to .gitignore")
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    if discover_index() is None:
        raise ContextloomError("no index found; run 'contextloom init' first")
    summary = update(Path.cwd(), full=args.full)
    if args.json:
        _emit_json(summary)
    else:
        print(
            f"indexed {summary['files']} files "
            f"(+{summary.get('added', 0)} added, "
            f"~{summary.get('changed', 0)} changed, "
            f"-{summary.get('deleted', 0)} deleted)"
        )
        print(
            f"symbols={summary['symbols']} refs={summary['refs']} issues={summary.get('issues', 0)}"
        )
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    info = status(_require_index())
    if args.json:
        _emit_json(info)
        return 0
    counts = info["counts"]
    print(f"Index:        {info['path']}")
    print(f"Schema:       v{info['schema_version']} (tool {info['tool_version']})")
    print(f"Created:      {info['created_at']}")
    print(f"Last full:    {info['last_full_index_at'] or 'never'}")
    print(f"Files:        {counts['files']}")
    print(f"Symbols:      {counts['symbols']}")
    print(f"References:   {counts['refs']}")
    print(f"File issues:  {counts['file_issues']}")
    return 0


def _cmd_find(args: argparse.Namespace) -> int:
    index = _require_index()
    conn = _fresh_connection(index, readonly=True)
    try:
        results = query.find(conn, args.symbol, root=index.parent, scope=args.scope, kind=args.kind)
    finally:
        conn.close()
    _print_results(results, as_json=args.json)
    return 0


def _cmd_refs(args: argparse.Namespace) -> int:
    index = _require_index()
    conn = _fresh_connection(index, readonly=True)
    try:
        results = query.refs_of(conn, args.path, root=index.parent)
    finally:
        conn.close()
    _print_results(results, as_json=args.json)
    return 0


def _cmd_refby(args: argparse.Namespace) -> int:
    index = _require_index()
    conn = _fresh_connection(index, readonly=True)
    try:
        results = query.refby(conn, args.path, root=index.parent)
    finally:
        conn.close()
    _print_results(results, as_json=args.json)
    return 0


def _cmd_near(args: argparse.Namespace) -> int:
    index = _require_index()
    conn = _fresh_connection(index, readonly=True)
    try:
        results = query.near(conn, args.path, root=index.parent)
    finally:
        conn.close()
    _print_results(results, as_json=args.json)
    return 0


def _cmd_config(args: argparse.Namespace) -> int:
    index = _require_index()
    if args.config_action == "get":
        raw = get_config(index, args.key)
        if raw is None:
            raise ContextloomError(f"no config key '{args.key}'")
        value = decode_config_value(raw)
        if args.json:
            _emit_json({args.key: value})
        else:
            print(value if isinstance(value, str) else json.dumps(value, indent=2))
        return 0
    normalized = normalize_config_value(args.key, args.value)
    set_config(index, args.key, normalized)
    print(f"{args.key} = {normalized}")
    return 0


def _cmd_gc(args: argparse.Namespace) -> int:
    if discover_index() is None:
        raise ContextloomError("no index found; run 'contextloom init' first")
    summary = gc(Path.cwd(), ai_refs=args.ai_refs)
    if args.json:
        _emit_json(summary)
    else:
        print(f"removed {summary['removed_files']} files, {summary['removed_refs']} references")
    return 0


def _cmd_infer(args: argparse.Namespace) -> int:
    index = _require_index()
    conn = open_index(index, readonly=False)
    try:
        results = infer(conn, scope=args.scope)
    finally:
        conn.close()
    _print_results(results, as_json=args.json)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextloom",
        description="Structural and fuzzy reference index for code, docs, and prose.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p_init = sub.add_parser("init", help="Create a new index in the current directory")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing index")
    p_init.set_defaults(func=_cmd_init)

    p_update = sub.add_parser("update", help="Reindex changed files (or full rebuild)")
    p_update.add_argument("--full", action="store_true", help="Reindex every file")
    p_update.add_argument("--json", action="store_true", help="Machine-readable output")
    p_update.set_defaults(func=_cmd_update)

    p_status = sub.add_parser("status", help="Show index health and schema/version info")
    p_status.add_argument("--json", action="store_true", help="Machine-readable output")
    p_status.set_defaults(func=_cmd_status)

    p_find = sub.add_parser("find", help="Find a symbol or reference")
    p_find.add_argument("symbol", help="Symbol or identifier to find")
    p_find.add_argument("--scope", choices=["file", "dir", "project"], help="Query scope")
    p_find.add_argument(
        "--kind",
        choices=["def", "call", "ref", "mention"],
        help="Match kind: def, call, ref, or mention",
    )
    p_find.add_argument("--json", action="store_true", help="Machine-readable output")
    p_find.set_defaults(func=_cmd_find)

    p_refs = sub.add_parser("refs", help="Everything a file or symbol references")
    p_refs.add_argument("path", help="File path, or path:line, or symbol name")
    p_refs.add_argument("--json", action="store_true", help="Machine-readable output")
    p_refs.set_defaults(func=_cmd_refs)

    p_refby = sub.add_parser("refby", help="Everything that references a file or symbol")
    p_refby.add_argument("path", help="File path, or path:line, or symbol name")
    p_refby.add_argument("--json", action="store_true", help="Machine-readable output")
    p_refby.set_defaults(func=_cmd_refby)

    p_near = sub.add_parser("near", help="Structural neighbors (same file/directory)")
    p_near.add_argument("path", help="File path, or path:line, or symbol name")
    p_near.add_argument("--json", action="store_true", help="Machine-readable output")
    p_near.set_defaults(func=_cmd_near)

    p_config = sub.add_parser("config", help="Read or write project configuration")
    cfg_sub = p_config.add_subparsers(dest="config_action", required=True, metavar="get|set")
    p_get = cfg_sub.add_parser("get", help="Read a config value")
    p_get.add_argument("key", help=f"One of: {', '.join(KNOWN_KEYS)}")
    p_get.add_argument("--json", action="store_true", help="Machine-readable output")
    p_get.set_defaults(func=_cmd_config)
    p_set = cfg_sub.add_parser("set", help="Write a config value")
    p_set.add_argument("key", help=f"One of: {', '.join(KNOWN_KEYS)}")
    p_set.add_argument("value", help="New value (JSON for structured keys)")
    p_set.set_defaults(func=_cmd_config)

    p_gc = sub.add_parser("gc", help="Prune stale or AI-inferred rows")
    p_gc.add_argument("--ai-refs", action="store_true", help="Also prune AI-inferred refs")
    p_gc.add_argument("--json", action="store_true", help="Machine-readable output")
    p_gc.set_defaults(func=_cmd_gc)

    p_infer = sub.add_parser("infer", help="Optional AI-assisted Layer 3 linkage (opt-in)")
    p_infer.add_argument("--scope", help="Limit inference to a path")
    p_infer.add_argument("--json", action="store_true", help="Machine-readable output")
    p_infer.set_defaults(func=_cmd_infer)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: CommandHandler = args.func
    try:
        return handler(args)
    except ContextloomError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
