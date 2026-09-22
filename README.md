# mycelia

Cscope-style structural + fuzzy reference indexing for any project — code, docs,
and prose — stored in a local SQLite index, built for AI coding agents to query
instead of grepping.

`mycelia` builds a self-healing, incrementally-maintained index of a project's
structure, symbols, and cross-references. It is an offline-built, instantly
queryable index (a spiritual successor to `cscope`) that covers both code
(structural) and prose (fuzzy/heuristic) content. The full design lives in
[`DESIGN.md`](DESIGN.md).

## Status

All of `DESIGN.md` §9 is implemented and tested:

- `init` / `update` / `status` / `gc` — index lifecycle, atomic swap, freshness
- Layer 1 structural extraction: tree-sitter (code) + a markdown/prose parser
- Layer 2 heuristic linkage (filename/path and identifier mentions) + confidence enum
- `find` / `refs` / `refby` / `near` with `--json` (agent-facing contract)
- self-healing queries: stale indexes are transparently re-indexed (§10)
- `infer` — Layer 3 boundary (opt-in; requires an injected AI backend, none shipped)

The post-commit hook example lives in `hooks/post-commit` (§11.4).

## Install

Requires Python 3.10+.

```bash
uv sync --extra dev   # development install (pulls pytest, ruff, mypy)
```

or, from a release:

```bash
pip install mycelia
```

## Quickstart

```bash
mycelia init                    # create mycelia.db in the current directory
mycelia update                  # index the tree (also runs automatically when stale)
mycelia find greet --json       # definitions + references to `greet`
mycelia refs pkg/main.py        # outgoing references from a file
mycelia refby pkg/util.py       # everything that references a file/symbol
mycelia near pkg/main.py        # structural neighbors
mycelia status                  # schema version, counts, last full index
mycelia config set fuzzy_level balanced
```

Configuration lives inside the index database (see `DESIGN.md` §5.4), so there
is no separate config file to keep in sync. `mycelia init` prints a reminder to
add `mycelia.db` (and `mycelia.db.tmp-*`) to `.gitignore`.

## CLI

| Command | Description |
|---|---|
| `mycelia init [--force]` | Create a new index in the current directory |
| `mycelia update [--full] [--json]` | Reindex (full rebuild + atomic swap) |
| `mycelia status [--json]` | Index health: schema/version, counts, last full index |
| `mycelia find <symbol> [--scope …] [--kind …] [--json]` | Find definitions and references |
| `mycelia refs <path> [--json]` | Outgoing references from a file/symbol |
| `mycelia refby <path> [--json]` | Incoming references to a file/symbol |
| `mycelia near <path> [--json]` | Structural neighbors (same file/directory) |
| `mycelia config get/set <key> [value]` | Read/write a config value |
| `mycelia gc [--ai-refs] [--json]` | Prune rows for missing files (optionally AI refs) |
| `mycelia infer [--scope …] [--json]` | Optional Layer 3 linkage (opt-in, needs a backend) |
| `mycelia --version` | Print the version |

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # type check
```

A `Makefile` wraps these targets (Posix shells; on plain Windows run the `uv`
commands directly or via `python -m uv`).