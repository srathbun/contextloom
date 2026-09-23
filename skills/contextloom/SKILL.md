---
name: contextloom
description: This skill should be used when the user asks to "use contextloom", "index the codebase", "find symbol references", "find callers", "who calls <symbol>", "what references this file", "what does this import", "query the reference index", or wants cscope-style structural and fuzzy cross-reference lookup instead of grepping a project. Covers the index lifecycle (init/update/status/gc) and the agent-facing find/refs/refby/near queries with their --json contract.
version: 0.1.0
---

# Contextloom

Contextloom is a local, offline-built structural + fuzzy reference index for a
whole project (code, docs, and prose) stored in a single SQLite file
(`contextloom.db`). It is a cscope-descended tool built specifically for AI
agents: instead of an ad hoc `grep`-and-stuff-into-context workflow, run one
structured query that returns file, location, kind, confidence, and an evidence
snippet — usually enough to act without reopening the file.

Use contextloom when a question is about *relationships and structure*:

- "Where is `greet` defined?" → `find greet`
- "Who calls/references `util.parse`?" → `refby util.py` or `refby parse`
- "What does `pkg/main.py` import or reference?" → `refs pkg/main.py`
- "What symbols and refs live near `pkg/main.py`?" → `near pkg/main.py`

Use `grep` instead when the target is a raw text search with no index-worthy
structure (this is a linkage index, not a full-text search engine).

## Lifecycle

Run these at the project root (or any subdirectory; the index is discovered by
walking upward like `git` finds `.git`).

| Command | Purpose |
|---|---|
| `contextloom init [--force]` | Create `contextloom.db` in the current directory. Prints a reminder to add `contextloom.db` and `contextloom.db.tmp-*` to `.gitignore`. (`--force` overwrites.) |
| `contextloom update [--full] [--json]` | Reindex changed files (or full rebuild with `--full`). Primes/warms the index. |
| `contextloom status [--json]` | Index health: schema version, tool version, file/symbol/ref/file-issue counts, last full index. |
| `contextloom gc [--ai-refs] [--json]` | Prune rows whose files no longer exist on disk; `--ai-refs` also prunes Layer-3 AI-inferred refs. |
| `contextloom --version` | Print the version. |

**Self-healing freshness:** every query command (`find` / `refs` / `refby` /
`near`) checks staleness first by walking the tree and comparing hashes/mtimes
against the index, and transparently incrementally re-indexes changed files
before answering. Do not run `update` before querying for correctness — it is
only needed to pre-warm (e.g. a post-commit hook) or to force `--full`.

The index is safe under concurrent readers/writers: updates build in a temp db
and atomically swap it in, so a mid-update query always sees a fully-old or
fully-new index, never a half-written one.

## Query commands

Always pass `--json` for machine-readable, agent-facing output. Default
(human) output is for terminal use only.

- **`find <symbol> [--kind def|call|ref|mention] [--scope file|dir|project] [--json]`**
  Find definitions and references to a symbol/identifier. Without `--kind`,
  returns both definitions and references. `--kind def` restricts to symbol
  definitions; `--kind call` to call sites; `--kind mention` to prose mentions;
  `--kind ref` to all reference rows. `--scope` limits matches to a path prefix
  (but the CLI currently only accepts the literal tokens `file|dir|project`, so
  for reliable scoping use `refs`/`refby`/`near` on a path).
- **`refs <path> [--json]`** — everything this file or symbol references
  (outgoing edges).
- **`refby <path> [--json]`** — everything that references this file or symbol
  (incoming edges; a file path implies "symbols defined in this file" too).
- **`near <path> [--json]`** — structural neighbors: symbols and refs in the
  same file and same directory, regardless of explicit references.

For `refs` / `refby` / `near`, `<path>` is a **project-relative path**, a
**`path:line`**, or a **symbol name** (resolved against the `files` table first,
then `symbols`).

## JSON result contract

Every result row carries the same field contract:

- `path` — project-relative, posix-style file path.
- `line`, `line_end` — 1-based line (and end line for definitions; `line_end`
  is `null` for references).
- `kind` — definition = `function|class|method|header|const|module`; reference
  = repeats `ref_kind`.
- `ref_kind` — edge type: `calls|imports|links_to|mentions|same_name_match|
  path_mention|inferred_related` (`null` for definitions).
- `source` — `structural` (tree-sitter parse), `heuristic` (Layer 2 fuzzy), or
  `ai` (opt-in Layer 3).
- `confidence` — why the match exists; a legible enum, not a score (see below).
- `evidence` — literal matched text/snippet (may be `null`).
- `snippet` — a few surrounding lines re-read from disk at query time (`null`
  if the file is missing/changed).

Logic of the enum: `source` is *who* produced the link; `confidence` is *how
certain* it is.

### Confidence (change weight on results with this)

| value | meaning |
|---|---|
| `structural` | derived directly from a parse tree (def, call, import). Trust. |
| `exact_scoped_match` | exact identifier match within the same file/module. |
| `exact_project_match` | exact identifier match, project-wide. |
| `path_mention` | a literal filename/path string appeared in content. |
| `name_correlation` | title/directory/naming heuristic, no literal reference. |
| `ai_inferred` | proposed by Layer 3; no textual evidence. Treat as a hint. |

The design favors generous recall over precision: shorter/generic identifiers
(`init`, `get`, `run`) still match but should be treated cautiously — weigh
`confidence`/`evidence` before trusting a candidate link.

## Config knobs

Config lives inside `contextloom.db` (table `config`), not in a repo file — one
file to keep in sync. Read/write with `config get/set`; `init` seeds defaults.

```bash
contextloom config get fuzzy_level
contextloom config set fuzzy_level balanced
```

| key | default | notes |
|---|---|---|
| `ignores` | built-in list + `.gitignore` | JSON array; extends default ignores |
| `max_file_size` | `2000000` | bytes; larger files tagged `too_large`, not parsed |
| `fuzzy_level` | `generous` | `generous \| balanced \| strict` (Layer 2 aggressiveness) |
| `ai_enabled` | `false` | opt-in gate for `infer` (Layer 3) |
| `ai_model` | `gpt-oss:20b` | Ollama model used by `infer` when no backend injected |

## Layer 3 (infer) is opt-in and never automatic

`contextloom infer [--scope path] [--json]` proposes semantic links with no
literal evidence (`source=ai`, `ref_kind=inferred_related`,
`confidence=ai_inferred`). It requires `ai_enabled=true` plus an injected AI
backend, and none ships by default. It is non-idempotent and may cost API
credits. Never run it automatically or as part of a normal workflow; invoke it
only when explicitly asked to find deeper conceptual relatedness. Prune its rows
later with `gc --ai-refs`.

## Working guidance

- When asked "what is this file/function and how is it used here", start with
  `find`/`refby`/`near` rather than `grep` — results self-heal and come back
  structured with evidence snippets.
- Read `evidence` and `snippet` first; open the source file only if the snippet
  is insufficient.
- To list definitions only, use `find <symbol> --kind def`; filter references
  with `--kind call`/`--kind mention`, or restrict by path with
  `refs`/`refby`/`near`.
- contextloom indexes prose too — query it for docs/markdown that mention a
  file or symbol, which grep over code alone would miss.
- A missing index (`no 'contextloom.db' found`) means `init` was never run:
  run `contextloom init` once per project, then query freely.

## Additional resources

- **`references/cli-reference.md`** — full command reference, exact `--json`
  output shapes for `update`/`status`/`gc`, path-resolution rules, and the
  complete enum tables (`kind`, `ref_kind`, `source`, `confidence`, file
  kinds/statuses, issue kinds).