# Project Design Document: Codebase & Content Linkage Indexer

## 1. Summary

This project is a cross-platform, PyPI-installable Python tool that builds and
maintains a local SQLite index of a project's structure, symbols, and
cross-references — code, documentation, and other text alike. It is a spiritual
successor to `cscope`: an offline-built, instantly-queryable index, rather than
a live search. Unlike `cscope`, it is not limited to C or to source code; it is
designed to index a whole project directory, including prose documentation,
and to surface both certain (structural) and probable (fuzzy/heuristic)
connections between files.

The tool is built to be consumed primarily by AI coding agents/harnesses, not
directly by humans at a terminal. Its core design goal is to replace ad hoc
`grep`-and-stuff-into-context workflows with a single fast, structured query
that returns enough context (file, location, kind, evidence snippet) for an
agent to act on without a mandatory follow-up read of the full file.

It is also usable as a conventional CLI, and is intended to be triggered
automatically via git hooks and/or editor integrations, in addition to being
invoked directly by an AI harness as a tool call.

## 2. Goals and Non-Goals

**Goals**

- Deterministic, incremental, fully programmatic indexing (no AI required to
  build or update the index).
- Cover both structured content (code symbols, defs/refs/calls) and
  unstructured/semi-structured content (markdown headers, links, prose
  mentions of files or identifiers).
- Bias toward high recall ("generous") over precision for fuzzy/heuristic
  links — the consuming AI is expected to judge relevance of a candidate link
  cheaply; a missed connection is a worse failure than a spurious one.
- Support file / directory / project-wide query scoping, cscope-style.
- Provide an ergonomic, agent-friendly query interface — not just raw rows.
- Self-healing freshness: any query path checks staleness first and
  incrementally updates before answering, so callers never need to remember
  to run `update` themselves.
- Safe under concurrent access from multiple integrations (git hook, editor,
  multiple AI agents) hitting the same repo.
- Optional AI-assisted third layer for semantic/inferred linkage, invoked
  only interactively/on demand — never part of the automatic pipeline.
- Installable via `pip install <name>`, reasonable default behavior with zero
  configuration, sensible plugin points for extending language support.

**Non-Goals (v1)**

- Not a replacement for an LSP (no live diagnostics, no rename-refactor).
- Not a full-text search engine (no ranking/relevance scoring beyond simple
  match-kind weighting) — this is a linkage/reference index, not a search
  index competing with ripgrep for raw text search.
- Not attempting perfect precision on fuzzy links — that is explicitly
  deferred to the consuming agent.
- Not shipping a bundled editor plugin (Vim/VS Code/etc.) in v1 — the CLI's
  `--json` output is the integration contract; editor plugins are a later,
  separate deliverable.

## 3. Prior Art and What We're Borrowing

- **cscope**: build-once/query-many index; a small fixed set of query "kinds"
  (find symbol, find callers, find callees, find text, find file, find files
  including this file); instant response from a prebuilt database; strong
  editor integration culture (this is where the "interface is the value"
  lesson comes from).
- **ctags / Universal Ctags / GNU Global**: multi-language symbol tagging via
  pluggable parsers; scoping of queries; incremental update support.
- **tree-sitter**: the actual parsing backbone for v1's structural layer —
  real grammars, real parse trees, broad language coverage via the
  `tree-sitter-language-pack` package (300+ languages, downloaded/bundled as
  small prebuilt wheels, so the "is bundling everything too heavy" concern is
  largely moot — the plugin mechanism below is about clean extensibility and
  optionality, not primarily about avoiding install bloat).
- **Obsidian / Foam / Dendron**: the "generous graph of loosely-confident
  links, presented for a reader to judge" model — this is the direct
  inspiration for Layer 2/3's confidence-tagged, non-authoritative references
  over prose content.
- **aider's repo-map**: the "index exists to be fed to an LLM instead of raw
  files" framing — closest existing analog to this project's actual
  consumption pattern, though aider's repo-map is regenerated live per-request
  rather than persisted and incrementally maintained.

## 4. Architecture Overview

```
                    ┌─────────────────────────────────────┐
                    │              CLI (entry point)        │
                    │  init | update | find | refs | refby   │
                    │  | near | status | gc                 │
                    └───────────────┬───────────────────────┘
                                    │
                    ┌───────────────▼───────────────────────┐
                    │         Staleness / Freshness Check     │
                    │  (hash/mtime diff vs. stored index)     │
                    └───────────────┬───────────────────────┘
                                    │ triggers partial reindex if stale
                    ┌───────────────▼───────────────────────┐
                    │            Indexing Pipeline            │
                    │                                          │
                    │  Layer 1: Structural extraction          │
                    │    - tree-sitter parsers (plugin-based)  │
                    │    - markdown/text structural parser     │
                    │    - "weird stuff" tagger (errors/binary)│
                    │                                          │
                    │  Layer 2: Heuristic linkage               │
                    │    - filename/path mention matching       │
                    │    - title/directory correlation           │
                    │    - import/include resolution              │
                    │                                          │
                    │  Layer 3: AI-assisted linkage (opt-in,    │
                    │    interactive only, never automatic)     │
                    └───────────────┬───────────────────────┘
                                    │ writes to temp db, atomic swap
                    ┌───────────────▼───────────────────────┐
                    │         SQLite index (project root)     │
                    │         <marker-file-name>.db            │
                    └───────────────┬───────────────────────┘
                                    │
                    ┌───────────────▼───────────────────────┐
                    │           Query Interface               │
                    │   Python API · CLI (--json) · (future:   │
                    │   MCP tool wrapper / editor plugins)     │
                    └─────────────────────────────────────────┘
```

Every integration point (git hook, editor, AI harness) is just a different
caller of the same CLI/library surface. There is exactly one indexing
implementation and one query implementation; nothing is reimplemented per
integration.

## 5. The Index File

### 5.1 Discovery

- On any invocation, the tool walks upward from the current working directory
  looking for the index file, the same way `git` walks upward looking for
  `.git`.
- If none is found and the command is `init`, a new index is created in the
  current working directory (treated as the project root).
- If none is found and the command is anything else (`find`, `update`, etc.),
  the tool errors clearly, telling the user to run `init`.
- The index is a single file at the project root: `<toolname>.db` (exact name
  finalized with the CLI/package name — see open items). It is a plain file,
  not a hidden directory, so it is trivially visible and trivially
  `.gitignore`-able.
- `init` prints a reminder to add the db file to `.gitignore` and offers to
  append it automatically (see §5.4 on config vs. db separation for why the
  db itself, not a config file, is what gets ignored).

### 5.2 Storage Format

SQLite. Single file. Schema (v1 draft):

```sql
-- Metadata / versioning
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- keys: schema_version, tool_version, created_at, last_full_index_at

-- Per-file tracking, for incremental update
CREATE TABLE files (
    id        INTEGER PRIMARY KEY,
    path      TEXT NOT NULL UNIQUE,   -- relative to project root, posix-style
    hash      TEXT NOT NULL,          -- content hash, e.g. blake3/sha1
    mtime     REAL NOT NULL,
    size      INTEGER NOT NULL,
    kind      TEXT NOT NULL,          -- 'code' | 'markdown' | 'text' | 'binary' | 'unknown'
    language  TEXT,                   -- e.g. 'python', 'markdown', NULL if n/a
    status    TEXT NOT NULL DEFAULT 'ok'  -- 'ok' | 'error' | 'unsupported' | 'binary'
);

-- "Weird stuff" detail — populated when files.status != 'ok'
CREATE TABLE file_issues (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    issue_kind  TEXT NOT NULL,   -- 'parse_error' | 'binary_blob' | 'unsupported_type' | 'too_large' | ...
    detail      TEXT             -- free-text explanation, e.g. exception message
);

-- Symbols: functions, classes, headers, etc.
CREATE TABLE symbols (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,    -- 'function' | 'class' | 'method' | 'header' | 'const' | ...
    line_start  INTEGER NOT NULL,
    line_end    INTEGER,
    scope_path  TEXT              -- e.g. 'ClassName.method_name' for nesting
);
CREATE INDEX idx_symbols_name ON symbols(name);

-- References/links between symbols and/or files
CREATE TABLE refs (
    id              INTEGER PRIMARY KEY,
    from_file_id    INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    from_symbol_id  INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
    to_file_id      INTEGER REFERENCES files(id) ON DELETE CASCADE,
    to_symbol_id    INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
    line            INTEGER,
    ref_kind        TEXT NOT NULL,  -- 'calls' | 'imports' | 'links_to' | 'mentions' |
                                     -- 'same_name_match' | 'path_mention' | 'inferred_related' | ...
    source          TEXT NOT NULL,  -- 'structural' | 'heuristic' | 'ai'
    confidence      TEXT NOT NULL,  -- enum, see §6.3 — legible, not a bare float
    evidence        TEXT            -- literal matched text / short snippet
);
CREATE INDEX idx_refs_from ON refs(from_file_id, from_symbol_id);
CREATE INDEX idx_refs_to   ON refs(to_file_id, to_symbol_id);

-- Project-level config, stored IN the db (see §5.4)
CREATE TABLE config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL  -- JSON-encoded where structured
);
```

`schema_version` in `meta` is checked on every open. If the running tool's
supported schema version doesn't match, the tool refuses to operate on the
db and tells the user to run a rebuild (`update --full` or `init --force`)
rather than attempting any kind of silent migration in v1. Migrations may be
added later once the schema has settled.

### 5.3 Atomicity and Concurrency

- Readers (queries) open the db read-only and are safe to run concurrently
  with each other at any time.
- `update` never writes to the live db in place. It builds a temp db
  (`<name>.db.tmp-<pid/uuid>`), performs the full write there (copying
  forward unchanged rows, re-indexing changed files), and atomically renames
  it over the live db on completion (`os.replace`, which is atomic on both
  POSIX and Windows for same-volume renames).
- If `update` is interrupted or fails, the live db is untouched; the temp
  file is cleaned up (or left for inspection, flagged in `status`).
- This means multiple agents/editors querying mid-update always see either
  the fully-old or fully-new index, never a half-written one, and a running
  `update` never blocks readers.
- A simple lock file (`<name>.db.lock`) prevents two concurrent `update`
  processes from racing each other; a second `update` invocation while one is
  in progress waits briefly or exits with a clear "update already in
  progress" message, configurable.

### 5.4 Config

- Project-level configuration (ignore patterns, enabled language plugins,
  fuzzy-matching aggressiveness, Layer 3 opt-in) is stored in the `config`
  table inside the db itself, **not** in a separate repo file — this was a
  deliberate simplification: one file to reason about, one file to
  `.gitignore`, no risk of config and db drifting out of sync.
- `init` seeds `config` with sensible defaults (see §7) and prints what it
  chose.
- A `<toolname> config get/set <key> [value]` subcommand reads/writes it
  without needing to touch SQLite directly.
- Tradeoff accepted: because config lives inside the gitignored db, it is
  *not* naturally shared across a team via git. If that turns out to matter
  in practice, a follow-up option is an optional `<toolname>.toml` that,
  when present, overrides/seeds the in-db config on `init`/`update` — flagged
  as a v1.1 candidate, not blocking for v1.

## 6. Indexing Pipeline

### 6.1 Layer 1 — Structural Extraction (deterministic, no AI)

- **Code files**: parsed via `tree-sitter`, using per-language grammars.
  Extracts real symbol definitions (functions, classes, methods, etc.) and
  structural references (calls, imports) from the actual parse tree — not
  regex guessing.
- **Markdown / prose files**: a lightweight structural parser (no tree-sitter
  needed) extracts headers and heading hierarchy, explicit links (Markdown
  links, wikilinks if present, `#anchor` references), front-matter, and code
  fence languages. This has no required external dependency — it should work
  out of the box on any install.
- **Everything else (config files, plain text, etc.)**: minimal structural
  extraction (e.g. just file-level metadata) unless a plugin exists for that
  type.
- Every extracted symbol/ref from this layer is `source = 'structural'` and
  gets the highest implicit confidence — it's derived from an actual parse,
  not a guess.

### 6.2 Layer 2 — Heuristic Linkage (deterministic, no AI, fuzzy by design)

- Filename/path literal mentions: a string in a comment, doc, or code that
  matches an existing file's name or path.
- Title/directory correlation: a markdown file's title or filename
  (`README.md`, `overview.md`) associated with the directory it lives in or
  siblings it plausibly documents.
- Identifier mentions in prose: an identifier that exists as a `symbols` row
  appearing in markdown/comment text outside of code fences.
- Import/include/require resolution beyond what a language grammar surfaces
  directly (e.g. resolving a relative import path to an actual file row).
- All of these are still 100% programmatic pattern matching — "generous" here
  describes how wide the matching rules cast their net, not any use of an
  LLM. This is what keeps the git-hook/`update` path fast and API-free.
- **Scope-aware matching to control noise on common short identifiers**:
  matching strictness scales with identifier specificity rather than being
  uniform. A short, generic identifier (e.g. `init`, `get`, `run`) still
  matches, but ranks low and is scoped down by default (same-file/same-module
  bias — see §6.4); a long or distinctive identifier gets the full generous
  fuzzy net project-wide. This is a ranking/default-scope behavior, not an
  exclusion rule — nothing is dropped, common names are just deprioritized
  by default so they don't drown out relevant results.

### 6.3 Layer 3 — AI-Assisted Semantic Linkage (opt-in, interactive only)

- Never runs as part of `update`, a git hook, or any automatic trigger.
- Invoked explicitly, e.g. `<toolname> infer [--scope path]`, intended to be
  run by a human or by an AI harness that has deliberately decided the
  moment calls for deeper linkage.
- Given a chunk of content, an AI call may propose relations that have no
  literal textual evidence (conceptual relatedness between a doc section and
  a module). These are written with `source = 'ai'`, `ref_kind =
  'inferred_related'`, and `confidence = 'ai_inferred'` (the lowest rung —
  see below), always with a caller/timestamp recorded so they can be bulk
  identified/pruned later if desired (`<toolname> gc --ai-refs`).
- Because this path costs API credits and is non-idempotent (a re-run may
  produce different results), it is explicitly excluded from anything that
  runs "on every edit" — this is the boundary the whole three-layer split
  exists to protect.

### 6.4 Confidence Model

Rather than a bare numeric score, confidence is a small legible enum tied to
*why* the match exists, so a consuming agent can reason about it the way it
reasons about any other categorical fact:

| confidence value      | meaning                                             | typical source |
|------------------------|------------------------------------------------------|-----------------|
| `structural`            | derived directly from a parse tree (def, call, import) | Layer 1 |
| `exact_scoped_match`     | exact identifier match within same file/module        | Layer 2 |
| `exact_project_match`    | exact identifier match, project-wide                    | Layer 2 |
| `path_mention`           | literal filename/path string found in content           | Layer 2 |
| `name_correlation`       | title/directory/naming heuristic, no literal reference   | Layer 2 |
| `ai_inferred`            | proposed by Layer 3, no textual evidence                 | Layer 3 |

Every `refs` row carries this plus `evidence` (the literal matched
snippet/text) so a query response is self-explanatory without a second
lookup.

### 6.5 "Weird Stuff" Tagging (error handling)

- Every file the indexer encounters gets a row in `files` regardless of
  whether it could be meaningfully parsed. `status` defaults to `'ok'`.
- If a file can't be parsed (unsupported type, no matching plugin, parse
  exception, detected binary content, exceeds a size cutoff), the indexer
  does **not** abort the run. It sets `status` to the appropriate value
  (`'error'`, `'unsupported'`, `'binary'`), adds a row to `file_issues` with
  the specifics, and moves on to the next file.
- This means even opaque or broken files remain *identifiable* to a querying
  agent (`<toolname> find --status error` or similar) even though their
  contents aren't searchable — a agent asking "what's in this repo" still
  learns a binary blob or unparseable file exists at that path, rather than
  it silently vanishing from the index.
- This single mechanism unifies three previously-separate concerns (parse
  errors, unsupported filetypes, intentionally-opaque binaries) into one
  queryable status field.

## 7. Configuration Defaults

Seeded by `init`, editable via `config get/set`:

- **Ignore rules**: respects `.gitignore` by default; additionally excludes a
  small built-in default list (`.git/`, `node_modules/`, `.venv/`,
  `venv/`, `__pycache__/`, `dist/`, `build/`, common lockfiles). Overridable/
  extendable via config.
- **Size cutoff**: files above a default threshold (e.g. 2 MB) are tagged
  `status = 'unsupported'` / `file_issues.issue_kind = 'too_large'` rather
  than parsed, to keep indexing fast and the db small. Configurable.
- **Language plugins enabled**: default set is whatever ships in the base
  install (see §8); additional plugins can be enabled once installed.
- **Fuzzy matching aggressiveness**: a single top-level knob
  (`fuzzy_level: generous | balanced | strict`, defaulting to `generous`
  per the project's stated design goal) that adjusts Layer 2 thresholds
  globally, without requiring per-rule configuration for the common case.
- **Layer 3 (AI) opt-in**: off by default; enabling it doesn't make it
  automatic, it only makes the `infer` subcommand available/configured with
  an API key or harness hook.

## 8. Packaging & Distribution

- **Language plugin mechanism**: language support is provided via a plugin
  interface (entry-points based, so third parties can `pip install` extra
  plugins that register themselves) even though the primary backend,
  `tree-sitter-language-pack`, already bundles 300+ grammars at low install
  cost (compiled wheels in the low single-digit MB range). The plugin
  boundary is kept for cleanliness and future custom/DSL grammar support,
  not because the full language pack is too heavy to depend on directly.
  Recommended default: depend on `tree-sitter-language-pack` directly in the
  base install; treat truly custom/uncommon grammars as the plugin
  extension point.
- **Base install** (`pip install <name>`) is fully functional out of the box
  for common languages plus markdown/text — no extras required for the
  common case.
- **Python version**: target a currently-supported CPython range (e.g. 3.10+)
  to keep dependency wheels (tree-sitter bindings) available prebuilt across
  platforms including Windows, which matters given this needs to install
  cleanly on the author's Windows machine without a local build toolchain.
- **Distribution**: standard `pyproject.toml` + build backend (e.g.
  `hatchling` or `setuptools`), published to PyPI, console-script entry point
  registered so the CLI is available as a bare command after install.

## 9. CLI Surface (draft)

```
<toolname> init                       Create a new index in cwd, seed default config
<toolname> update [--full]            Incrementally reindex changed files (or full rebuild)
<toolname> status                     Show index health: file counts, last update, schema version, issues
<toolname> find <symbol> [--scope file|dir|project] [--kind def|call|ref|mention] [--json]
<toolname> refs <path>                Everything this file/symbol references
<toolname> refby <path>               Everything that references this file/symbol
<toolname> near <path>                Structural neighbors (same dir/module) regardless of explicit refs
<toolname> infer [--scope path]       Run optional AI-assisted Layer 3 linkage (interactive, opt-in)
<toolname> config get/set <key> [val] Read/write project config
<toolname> gc [--ai-refs]             Prune stale or (optionally) AI-inferred rows
```

- Every query command supports `--json` for structured, agent-facing output;
  default output is human-readable for terminal use.
- Every query command runs the staleness check first (§10) and transparently
  performs a partial `update` if needed before answering — callers never
  need to invoke `update` themselves for correctness, only for pre-warming
  performance (e.g. a pre-commit hook calling it proactively).
- Query responses include, per match: file path, line range, `ref_kind`,
  `source`, `confidence`, `evidence` snippet, and (by default) a short
  surrounding code/text snippet — enough context that many queries won't
  require the caller to open the file at all.

## 10. Staleness & Freshness

- Staleness is determined by comparing current on-disk file hashes/mtimes
  against the `files` table, **not** by git commit state — this matters
  because uncommitted working-directory edits must be reflected, not just
  changes at commit boundaries.
- Every query path runs this check first (cheap — a directory walk plus hash
  comparison against stored rows) and triggers an incremental `update` for
  just the changed files if anything is stale, before executing the actual
  query. This is what makes the "AI never has to remember to reindex"
  property hold in practice.
- Git hooks (pre-commit and/or post-commit, both viable) call `update`
  directly as a proactive/eager refresh — this is a performance optimization
  (keeps the index warm at commit boundaries) layered on top of, not a
  substitute for, the self-healing check every query already does.

## 11. Open Items / Decisions Needed Before Implementation Starts

1. **Package/CLI name.** Needs a PyPI availability check and should be
   distinct enough from `cscope`/`ctags`/`gtags` to avoid confusion. This
   determines the index filename convention (`<name>.db`) referenced
   throughout this doc. **Answer** I have chosen Mycelia for the name.
2. **Content hash algorithm** for `files.hash` — recommend a fast
   non-cryptographic or lightweight hash (e.g. `blake3` if the dependency is
   acceptable, else stdlib `hashlib.sha1`) since this is purely a
   change-detection mechanism, not a security boundary. **Answer** I think sha1 should be fine.
3. **Exact `tree-sitter-language-pack` version pin** and confirmation of
   Windows wheel availability for the target Python version range (should be
   verified against current PyPI files at implementation time, not assumed
   from this doc).
4. **Pre-commit vs. post-commit** (or both, configurable) as the default hook
   recommendation shipped in docs/examples. **Answer** Post commit hook default.
5. **Whether `refs`/`symbols` store a denormalized `snippet` column directly**
   (larger db, zero-cost reads) **vs. re-reading a few lines from disk at
   query time** (smaller db, small read cost, but breaks if the file has
   changed since indexing and staleness check hasn't caught it yet). Leaning
   toward denormalized storage given the explicit goal of minimizing agent
   follow-up reads, but worth confirming once real db sizes are visible. **Answer** reread at query time since we can use hash to verify changes for staleness.

## 12. Suggested Build Order

1. Schema + db open/create/atomic-swap machinery (§5), with `init` and a
   `status` command that can prove the db round-trips correctly.
2. Layer 1 for one language (e.g. Python) + markdown, end to end, with
   `update --full` producing a populated db.
3. `find` / `refs` / `refby` / `near` against that data, `--json` output
   finalized as the agent-facing contract.
4. Staleness check + incremental `update` (hash diff, partial reindex).
5. "Weird stuff" tagging and graceful-failure behavior, tested against
   deliberately broken/binary input.
6. Layer 2 heuristic linkage + confidence enum.
7. Plugin mechanism generalized beyond the one hardcoded language.
8. Git hook example script + packaging/PyPI polish.
9. Layer 3 (`infer`) as the final, optional piece.
