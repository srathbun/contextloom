"""Command-line interface (DESIGN §9), implemented commands only.

Milestone 1 ships the index-foundation commands: ``init``, ``status``, and
``config get/set``. The query and indexing commands (``update``, ``find``,
``refs``, ``refby``, ``near``, ``infer``, ``gc``) are added in later milestones.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mycelia import __version__
from mycelia.config import KNOWN_KEYS, decode_config_value, normalize_config_value
from mycelia.db import create_index, discover_index, get_config, set_config, status
from mycelia.errors import MyceliaError

CommandHandler = Callable[[argparse.Namespace], int]


def _emit_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def _require_index() -> Path:
    index = discover_index()
    if index is None:
        raise MyceliaError(
            "no 'mycelia.db' found in this directory or any parent; run 'mycelia init' first"
        )
    return index


def _cmd_init(args: argparse.Namespace) -> int:
    db_path = create_index(Path.cwd(), force=args.force)
    print(f"initialized index at {db_path}")
    print("recommend adding 'mycelia.db' and 'mycelia.db.tmp-*' to .gitignore")
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


def _cmd_config(args: argparse.Namespace) -> int:
    index = _require_index()
    if args.config_action == "get":
        raw = get_config(index, args.key)
        if raw is None:
            raise MyceliaError(f"no config key '{args.key}'")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mycelia",
        description="Structural and fuzzy reference index for code, docs, and prose.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    p_init = sub.add_parser("init", help="Create a new index in the current directory")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing index")
    p_init.set_defaults(func=_cmd_init)

    p_status = sub.add_parser("status", help="Show index health and schema/version info")
    p_status.add_argument("--json", action="store_true", help="Machine-readable output")
    p_status.set_defaults(func=_cmd_status)

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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: CommandHandler = args.func
    try:
        return handler(args)
    except MyceliaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
