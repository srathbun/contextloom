# Contextloom CLI Reference

Full command reference and exact `--json` output shapes. Every query command
passes `--json` for agent-facing structured output. All commands discover the
index by walking upward from the current directory; all query commands
self-heal (incrementally reindex if the tree is stale) before answering.

## Commands

### `init`

```
contextloom init [--force]
```

Creates `contextloom.db` in the current working directory (the project root),
seeding `meta` and `config` tables with defaults. `--force` overwrites an
existing index. Prints a reminder to add `contextloom.db` and
`contextloom.db.tmp-*` to `.gitignore`.

### `update`

```
contextloom update [--full] [--json]
```

Reindexes changed files. `--full` forces a full rebuild (deterministic
re-extract + atomic swap). Without `--json`, prints a human summary.

`--json` output shape:

```json
{
  "files": 1234,
  "added": 12,
  "changed": 34,
  "deleted": 5,
  "symbols": 4567,
  "refs": 8910,
  "issues": 3
}
```

### `status`

```
contextloom status [--json]
```

Index health summary. `--json` output shape:

```json
{
  "path": "/abs/path/contextloom.db",
  "schema_version": 1,
  "tool_version": "1.1.0",
  "created_at": "2026-09-23T00:00:00",
  "last_full_index_at": "2026-09-23T10:00:00",
  "counts": {
    "files": 1234,
    "symbols": 4567,
    "refs": 8910,
    "file_issues": 3
  },
  "config": {
    "ai_enabled": false,
    "ai_model": "gpt-oss:20b",
    "fuzzy_level": "generous",
    "ignores": ["node_modules/", ".git/", "..."],
    "max_file_size": 2000000
  }
}
```

The `config` object is the fully-decoded project configuration (see the config
key table below).

### `find`

```
contextloom find <symbol> [--scope file|dir|project] [--kind def|call|ref|mention] [--json]
```

Find symbol definitions and/or references matching `<symbol>` (exact name
match). Without `--kind`, returns both definitions and references.

- `--kind def` → only symbol-definition rows.
- `--kind call` → only reference rows with `ref_kind = 'calls'`.
- `--kind ref` → all reference rows (no kind filter).
- `--kind mention` → reference rows with `ref_kind IN ('mentions','path_mention')`.
- `--scope file|dir|project` — applied as a path-prefix filter
  (`f.path LIKE '<token>/%'`). Since the CLI only accepts the literal tokens
  `file|dir|project`, it does not provide meaningful cscope-style scoping;
  scope reliably with the path-scoped `refs`/`refby`/`near` commands instead.

### `refs`

```
contextloom refs <path> [--json]
```

Outgoing references: everything the target file or symbol references.
`<path>` resolves as a project-relative path, `path:line`, or symbol name
(`files` table first, then `symbols`).

### `refby`

```
contextloom refby <path> [--json]
```

Incoming references: everything that references the target file or symbol. A
file path also includes "symbols defined in that file" as targets, and
duplicate rows are deduplicated by `(from_path, line, ref_kind, evidence)`.

### `near`

```
contextloom near <path> [--json]
```

Structural neighbors: symbols defined in the target file, symbols defined in
the same directory, and reference rows originating in that directory. `<path>`
resolves identically to `refs`/`refby` (file path, `path:line`, or symbol).

### `config`

```
contextloom config get <key> [--json]
contextloom config set <key> <value>
```

Read/write project configuration stored inside the index. `get --json` emits
`{"<key>": <decoded-value>}`. `set` validates/normalizes the value (structured
keys are JSON-encoded; see the key table below).

### `gc`

```
contextloom gc [--ai-refs] [--json]
```

Prunes `files`/`symbols`/`refs` rows whose backing file no longer exists on
disk. `--ai-refs` additionally prunes AI-inferred references (unrelated to
missing-file pruning). `--json` output shape:

```json
{
  "removed_files": 3,
  "removed_refs": 7
}
```

### `infer`

```
contextloom infer [--scope path] [--json]
```

Opt-in Layer 3 semantic linkage. Requires `ai_enabled=true` and an injected AI
backend (none ships by default). Writes `source='ai'`,
`ref_kind='inferred_related'`, `confidence='ai_inferred'` rows. Non-idempotent,
may cost credits; never run automatically.

## `--json` result row contract

All `find`/`refs`/`refby`/`near`/`infer` queries emit an array of rows. Field
set is the union of the definition and reference shapes:

| field | definition row | reference row |
|---|---|---|
| `path` | file path (rel, posix) | source file path (rel, posix) |
| `line` | `line_start` (1-based) | reference line (1-based) |
| `line_end` | symbol end line (or `null`) | `null` |
| `kind` | symbol kind | repeats `ref_kind` |
| `ref_kind` | `null` | edge type |
| `source` | `"structural"` | `structural\|heuristic\|ai` |
| `confidence` | `"structural"` | confidence enum |
| `evidence` | `"symbol '<name>'"` | literal matched text (may be `null`) |
| `snippet` | surrounding lines or `null` | surrounding lines or `null` |

`snippet` re-reads a few lines from disk at query time; a missing/changed file
yields `snippet: null` while still returning the row.

## Full enum tables

### Symbol `kind`

`function` · `class` · `method` · `header` · `const` · `module`

### `ref_kind`

| value | meaning |
|---|---|
| `calls` | function/method call (structural) |
| `imports` | import/include/require (structural) |
| `links_to` | explicit markdown/wikilink/`#anchor` link |
| `mentions` | identifier mention in prose/comment |
| `same_name_match` | same-name heuristic match |
| `path_mention` | literal filename/path string in content |
| `inferred_related` | Layer 3 semantic link (no textual evidence) |

### `source`

`structural` · `heuristic` · `ai`

### `confidence`

| value | typical source | meaning |
|---|---|---|
| `structural` | Layer 1 | derived from a parse tree |
| `exact_scoped_match` | Layer 2 | exact identifier match, same file/module |
| `exact_project_match` | Layer 2 | exact identifier match, project-wide |
| `path_mention` | Layer 2 | literal filename/path string found |
| `name_correlation` | Layer 2 | title/directory/naming heuristic |
| `ai_inferred` | Layer 3 | proposed by AI, no textual evidence |

### File `kind` (stored in `files`, surfaced via `status`/issues)

`code` · `markdown` · `text` · `binary` · `unknown`

### File `status`

`ok` · `error` · `unsupported` · `binary`

### `file_issues.issue_kind`

`parse_error` · `binary_blob` · `unsupported_type` · `too_large`

## Config keys (`config get/set`)

| key | type | default | validation |
|---|---|---|---|
| `ignores` | JSON array of strings | built-in list | must parse to a list of strings |
| `max_file_size` | JSON int | `2000000` | non-negative int |
| `fuzzy_level` | string | `generous` | one of `generous\|balanced\|strict` |
| `ai_enabled` | `true`/`false` | `false` | literal `true` or `false` |
| `ai_model` | JSON string | `gpt-oss:20b` | non-empty model name |

Built-in `ignores` default: `.git/`, `.hg/`, `.svn/`, `node_modules/`, `.venv/`,
`venv/`, `env/`, `__pycache__/`, `dist/`, `build/`, `.pytest_cache/`,
`.ruff_cache/`, `.mypy_cache/`, `*.pyc`, `*.pyo`, plus the index files
themselves (`contextloom.db`, `contextloom.db.tmp-*`, `contextloom.db.lock`).
Project `.gitignore` rules are also honored.

## Path resolution (`refs`/`refby`/`near`)

1. If the argument ends in `:N` (trailing digits after the last `:`), the `:N`
   is a line hint; the leading part is the path.
2. Look up the path in `files.path` — if found, the target is that file.
3. Otherwise treat the whole argument as a symbol name and look it up in
   `symbols.name`.