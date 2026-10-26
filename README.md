# Hermes-Index

[![Tests](https://github.com/Articipate/Hermes-Index/actions/workflows/test.yml/badge.svg)](https://github.com/Articipate/Hermes-Index/actions/workflows/test.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

**Stable logical IDs for project files, without renaming a single file.**

Hermes-Index is an Agent Skill and a small Python helper that maintains a hidden
`h_index.md` at the project root. Every file and folder can be referenced by a
short ID such as `/A`, `/A/1`, or `/A/B/2`, while the real paths stay meaningful:

```text
codes:
/A = src/
/A/1 = src/main.py
/A/A = src/utils/
/A/B = src/components/
/A/B/1 = src/components/button.py
/1 = README.md

map:
1: /A(Create folder)
2: /A/1(Create file)
3: /A/1(Write the parser)
4: /A/A(Create folder)
5: /A/B(Create folder)
6: /A/B/1(Create button component)
7: /1(Create file)
```

The visible file has only `codes:` and `map:`. Machine-readable counters,
reserved IDs, and statuses live in an HTML comment at the bottom, invisible in a
rendered Markdown preview.

## Why

Agents work across long sessions, refactors, and multiple files. Paths change;
names collide; context gets lost. Hermes-Index gives the agent a compact,
authoritative map:

- **Stable references.** A moved or renamed item keeps its ID.
- **No filename pollution.** Real files and folders keep human names. IDs exist
  only in the index.
- **Chronological memory.** `map:` records what happened, in order, without
  rewriting history.
- **Cheap lazy indexing.** Nothing is scanned up front. Items are adopted when
  they are first touched.
- **Local and quiet.** The index is hidden, excluded from Git, and never
  committed.
- **Deterministic at scale.** After `A`-`Z` are exhausted under a folder, the
  helper generates deterministic 2+ letter codes instead of failing.

## Install

Clone the repository into your agent's skills directory. For Claude Code:

```bash
git clone https://github.com/Articipate/Hermes-Index.git ~/.claude/skills/hermes-index
```

For another Agent Skills client, use that client's skills directory and keep the
folder name `hermes-index`. The skill entry point is `SKILL.md`.

The helper is `scripts/h_index.py`. It requires **Python 3.9 or newer** and uses
only the standard library. No installation, virtual environment, or third-party
package is needed.

## Quick start

```bash
SKILL_DIR="$HOME/.claude/skills/hermes-index"

# Explicitly initialize the current project
python "$SKILL_DIR/scripts/h_index.py" init

# Create and index a folder
python "$SKILL_DIR/scripts/h_index.py" folder src

# Create and index an empty file
python "$SKILL_DIR/scripts/h_index.py" file src/main.py

# Write the file with your normal tools, then record the work
python "$SKILL_DIR/scripts/h_index.py" log src/main.py --action "Write the entry point"

# Move without changing the ID
python "$SKILL_DIR/scripts/h_index.py" move src/main.py src/app.py

# Remove the file with your normal tools, then retire its ID
python "$SKILL_DIR/scripts/h_index.py" delete src/app.py

# Check everything
python "$SKILL_DIR/scripts/h_index.py" verify
```

Pass `--root <dir>` before or after the subcommand when working outside the
project. Without it, the helper finds the nearest directory containing
`h_index.md`, then the Git root, then the current directory.

## How the skill behaves

At the start of a task that may modify project files, the agent checks for
`h_index.md`:

- **If it exists**, the agent maintains it automatically. No permission prompt.
- **If it is absent**, the agent offers initialization once. If the user declines,
  the agent continues normally and does not ask again in that session.
- **If the user explicitly asks to initialize**, the agent runs `init` directly.
- **If an unrecognized `h_index.md` exists**, the agent does not overwrite it. It
  asks first.

Existing items are adopted lazily. On first touch, an unindexed folder or file
gets an ID and an `Adopt folder` / `Adopt file` entry. File content is never
rewritten by adoption.

## ID rules

| Item | ID | Example |
| --- | --- | --- |
| Root folder | not indexed | `/` is internal only |
| Top-level folder | `/A`, `/B`, ... | `src/` → `/A` |
| Nested folder | `/A/A`, `/A/B` | `src/components/` → `/A/B` as a second child |
| Root file | `/1`, `/2`, ... | `README.md` → `/1` |
| Nested file | `/A/1`, `/A/B/1` | `src/components/button.py` → `/A/B/1` |

The first child folder under `/A` receives `/A/A`; its siblings advance to
`/A/B`, `/A/C`, and so on. The `/A/B` example assumes a sibling already uses
`/A/A`.

- Folder segments are allocated per parent: `A` through `Z`, then deterministic
  2+ letter segments derived from a SHA-256 hash of allocation parent and path.
- File numbers are per folder. Every folder, including the root, has its own
  counter.
- IDs are stable across moves and renames. Moving a folder updates descendant
  paths but not their IDs.
- Deleted IDs and numbers are never reused. They stay reserved in state, and the
  deletion remains in `map:`.
- A file ID's number belongs to the folder that allocated it. Moving the file
  does not renumber it.
- `--segment` can request a manual segment for a new folder, for example
  `folder src --segment SRC`. Existing IDs cannot be reassigned.

The full allocation algorithm is in
[`references/rules.md`](references/rules.md#4-allocation-algorithm).

## `map:` actions

Actions are written by the agent, not generated from file paths. Keep them
short, English, and in base/imperative form:

```text
Create login form
Fix session expiry
Refactor auth middleware
Update README examples
Remove unused imports
```

Avoid paths, IDs, parentheses, past tense, and vague entries:

```text
Created login form        # past tense
Write src/login.py        # path belongs in codes:
Fix bug (session)         # parentheses are rejected
Update stuff              # vague
```

The helper normalizes whitespace, rejects parentheses, control characters, and
actions over 80 characters, and appends one ordered line per call.

## CLI

| Command | Purpose |
| --- | --- |
| `init` | Create `h_index.md`, hide it, and exclude it from Git |
| `folder <path>` | Create or adopt a folder, printing its ID |
| `file <path>` | Create or adopt a file, printing its ID |
| `log <path> --action TEXT` | Append an action for an active item |
| `move <old> <new>` | Move or relink an item without changing its ID |
| `delete <path>` | Mark an item and its descendants deleted |
| `status [--json]` | Show active codes, counts, and protection status |
| `verify [--json]` | Check state, paths, ordering, and protection |
| `repair [--json]` | Rebuild visible sections from the state block |
| `hide [--json]` | Reapply the hidden attribute and Git exclusion |

Useful options:

- `--root <dir>` on the main command or any subcommand.
- `--action TEXT` on `folder`, `file`, `move`, and `delete` to override the
  default create/adopt/move/delete wording.
- `--segment LETTERS` on `folder` for a new manual folder segment.
- `--json` on read-only and maintenance commands for automation.
- `--version`.

`folder` and `file` create missing parents. `delete` never removes files from
disk; it only updates the index. Remove items with normal tools, then record the
deletion.

## Protection

`init` and `hide` apply the best available local protection:

- **Windows:** sets the `FILE_ATTRIBUTE_HIDDEN` attribute.
- **macOS:** sets the `hidden` filesystem flag through `os.chflags` (with a
  `chflags` command fallback).
- **Linux:** has no per-file hidden attribute; the file remains visible in
  directory listings. This is reported as a warning by `verify`.
- **Git:** adds `/h_index.md`, `/.h_index.lock`, and `/.h_index.*.tmp` to
  `.git/info/exclude`. It never modifies `.gitignore`.

The index is local to each working copy. Because it is excluded from Git, clones
do not share IDs or history. Each clone that wants an index initializes its own.
Hidden and excluded are hygiene, not security; do not store secrets in actions.

## Requirements

- Python 3.9+
- Windows, macOS, or Linux
- Git optional (exclusion is skipped outside a repository)

No network access or third-party Python packages are used. Git is used only when
available to maintain the local exclude file.

## Development

```bash
python -m unittest discover -s tests -v
```

The suite covers the format, allocation, lazy adoption, moves, deletes,
A-Z exhaustion, hidden attributes, Git exclusion, verification, and repair. CI
runs on Linux, Windows, and macOS.

## Repository layout

```text
.
├── SKILL.md                 # Agent Skill instructions
├── README.md
├── LICENSE
├── scripts/
│   └── h_index.py           # Python 3.9+ standard-library helper
├── references/
│   └── rules.md             # Full rules, schema, recovery, and design notes
├── examples/
│   ├── h_index.example.md   # Rendered index example
│   └── agent-workflow.md    # Longer agent session walkthrough
├── evals/
│   └── evals.json           # Agent behavior scenarios
├── tests/
│   └── test_h_index.py
└── .github/workflows/test.yml
```

## License

MIT. Copyright (c) 2026 Articipate. See [LICENSE](LICENSE).
