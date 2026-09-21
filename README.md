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

The foundation milestone is implemented and tested:

- index discovery, schema, and creation (`mycelia init`)
- schema-version enforcement and atomic build-and-swap machinery
- health summary (`mycelia status`)
- project configuration stored in the index (`mycelia config get/set`)

The indexing pipeline (Layer 1/2/3), query commands (`find`, `refs`, `refby`,
`near`), staleness check, and `update`/`gc`/`infer` land in subsequent milestones
per the build order in `DESIGN.md` §12.

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
mycelia status                  # schema version, counts, last full index
mycelia status --json           # machine-readable output
mycelia config get fuzzy_level          # -> "generous"
mycelia config set fuzzy_level balanced
```

Configuration lives inside the index database (see `DESIGN.md` §5.4), so there
is no separate config file to keep in sync. `mycelia init` prints a reminder to
add `mycelia.db` (and `mycelia.db.tmp-*`) to `.gitignore`.

## CLI

| Command | Description |
|---|---|
| `mycelia init [--force]` | Create a new index in the current directory |
| `mycelia status [--json]` | Index health: schema/version, counts, last full index |
| `mycelia config get <key> [--json]` | Read a config value |
| `mycelia config set <key> <value>` | Write a config value |
| `mycelia --version` | Print the version |

Planned per `DESIGN.md` §9: `update`, `find`, `refs`, `refby`, `near`, `infer`,
`gc`.

## Development

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy src        # type check
```

A `Makefile` wraps these targets (Posix shells; on plain Windows run the `uv`
commands directly or via `python -m uv`).