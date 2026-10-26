# Hermes-Index Rules

This document is the complete reference for Hermes-Index behavior. Read it when
you need exact ID allocation rules, the state schema, recovery steps, or platform
details. The short operational guide is in [`../SKILL.md`](../SKILL.md).

## Contents

1. [Terms](#1-terms)
2. [Visible format](#2-visible-format)
3. [ID grammar](#3-id-grammar)
4. [Allocation algorithm](#4-allocation-algorithm)
5. [Lifecycle and stability](#5-lifecycle-and-stability)
6. [State schema](#6-state-schema)
7. [Command reference](#7-command-reference)
8. [Protection and concurrency](#8-protection-and-concurrency)
9. [Verification invariants](#9-verification-invariants)
10. [Recovery](#10-recovery)
11. [Design notes and limitations](#11-design-notes-and-limitations)
12. [Testing](#12-testing)

## 1. Terms

| Term | Meaning |
| --- | --- |
| Project root | Directory containing `h_index.md`, normally the Git root |
| Index | The hidden `h_index.md` file |
| ID | Logical reference such as `/A`, `/A/B`, `/A/1`, or `/1` |
| Folder ID | Letter-only ID, for example `/A/B` |
| File ID | Folder path plus a per-folder number, for example `/A/B/1` |
| Allocation parent | Folder that supplied a folder's letter segment |
| Allocation folder | Folder that supplied a file's number |
| Current parent | Folder where an item lives after moves |
| Active | Item present in `codes:` |
| Deleted | Item removed from `codes:` but retained in state and history |

Real files and folders never contain Hermes-Index IDs in their names. IDs exist
only in `h_index.md`.

## 2. Visible format

`h_index.md` has exactly two visible sections:

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

Formatting rules:

- `codes:` and `map:` appear in that order.
- There is one blank line between the sections.
- `codes:` lines are `<ID> = <path>`. Folder paths end with `/`.
- `map:` lines are `<order>: <ID>(<action>)`.
- There is no space between the ID and the opening parenthesis.
- Paths are relative to the project root and use `/` separators.
- `codes:` is ordered by allocation time, then ID as a tie-breaker.
- `map:` is ordered by operation number, which starts at 1.
- The visible sections contain no bullets, headings, timestamps, or prose.
- The state block at the bottom is an HTML comment and is not visible in a
  rendered Markdown preview.

Do not edit the visible sections directly. Use helper commands so the state and
the visible sections stay synchronized. If only the visible sections were
changed, `repair` can regenerate them.

## 3. ID grammar

Folder ID:

```text
/SEGMENT
/SEGMENT/SEGMENT
...
```

File ID:

```text
/NUMBER
/SEGMENT/NUMBER
/SEGMENT/SEGMENT/NUMBER
...
```

`SEGMENT` is one or more ASCII letters `A-Z`. `NUMBER` is a positive decimal
integer without leading zeros.

Examples:

| Item | ID |
| --- | --- |
| Root folder | (not indexed; `/` is used internally) |
| `src/` | `/A` |
| `src/utils/` | `/A/A` |
| `src/components/` | `/A/B` (the second child folder under `src/`) |
| `src/main.py` | `/A/1` |
| `src/components/button.py` | `/A/B/1` |
| `README.md` at root | `/1` |
| `LICENSE` at root | `/2` |

Folders always have letter segments. Files always end in a number. This makes
the type obvious from the ID alone.

IDs are not file names, are not stored in file contents, and are not derived from
the current path after allocation. Treat them as opaque stable references.

## 4. Allocation algorithm

### 4.1 Folder segments

Each folder has a counter entry. Folder segments are allocated independently
under each allocation parent:

1. Try `A`, `B`, `C`, ... `Z` in order, skipping any segment already used under
   that parent.
2. If all 26 single letters are used, generate a deterministic multi-letter
   segment.
3. Record the chosen segment in the parent's `used_segments` list immediately.

The `used_segments` list is never pruned, including when a folder is deleted or
moved. This is what prevents ID reuse.

Single-letter IDs are used first because they are short and predictable. A
project with more than 26 subfolders under one parent is unusual, but large
projects can still allocate without human intervention.

### 4.2 Segment exhaustion

When `A`-`Z` are exhausted under a parent, the helper generates a deterministic
2+ letter segment from:

```text
seed   = f"{allocation_parent_id}|{relative_path}"
digest = sha256(seed.encode("utf-8")).digest()
```

For each candidate length from 2 through 16, it builds a segment by mapping the
first bytes of the digest modulo 26 to letters:

```text
segment = "".join(LETTERS[digest[i] % 26] for i in range(length))
```

If that segment is unused under the parent, it is selected. If it collides, the
helper tries the next length. In the extremely unlikely case that every length
through 16 collides, it searches additional digest-derived candidates with a
counter until it finds a free one.

Consequences:

- The result is deterministic for the same allocation parent and path.
- It is not sequential `AA`, `AB`, `AC`; it is a path-derived short code.
- It can be two or more letters. Examples: `QU`, `AZ`, `ATM`, `BXMMM`.
- Different parents may use the same segment safely because the full ID differs.
- Generated IDs are stable once assigned. Moving the folder does not regenerate
  them.
- Deleted or moved segments remain reserved under their original allocation
  parent.

The helper never uses a hash segment when a free single letter exists. Manual
overrides are the exception below.

### 4.3 Manual segment override

For a new folder, `folder <path> --segment <letters>` lets the user request a
segment. The value is uppercased, must be 1-16 ASCII letters, and must be unused
under the allocation parent. Example:

```text
folder src --segment SRC
```

This assigns `/SRC` for `src/` if `SRC` was not already used. Manual overrides do
not renumber or rename an existing folder. If the path is already indexed, the
requested segment must match the existing one; otherwise the command fails.

Manual overrides are useful for stable external references or a preferred short
code. They are not required for normal use.

### 4.4 File numbers

Each folder, including the project root, has its own `next_file` counter.

- `next_file` starts at 1.
- Allocation uses the current value and advances it by one.
- The counter is never rewound, including after deletes.
- The helper skips any number whose full ID already exists in state as an extra
  safety check.
- Different folders can both have a file numbered `1`; full IDs are still unique
  because the folder ID is part of the file ID.

A file's number belongs to its allocation folder, not its current folder. When a
file moves, the number and the full ID stay unchanged. New files in the
destination folder use that destination's own counter, so they are not
renumbered around the moved file.

### 4.5 The project root

The project root itself is not an indexed folder and does not appear in `codes:`.
Its files use IDs `/1`, `/2`, and so on. Internally, its counter is stored under
the key `/`.

## 5. Lifecycle and stability

### 5.1 Create

- `folder <path>` creates missing folders (including ancestors), assigns IDs, and
  records `Create folder` entries.
- `file <path>` creates missing parent folders and an empty file if the file does
  not exist, assigns IDs, and records `Create folder` / `Create file` entries.
- Neither command overwrites existing content.

### 5.2 Adopt

Hermes-Index does not scan the whole project at initialization. Existing items
are adopted lazily on first touch:

- If a folder or file already exists but is unindexed, `folder` or `file` assigns
  a new ID and records `Adopt folder` or `Adopt file`.
- If the parent chain is also unindexed, ancestors are adopted first, in order.
- Existing file content is preserved.
- If the target is already active, the command is a no-op unless `--action` is
  supplied, in which case it appends that action to `map:`.

Lazy adoption keeps initialization cheap and means the index grows around the
work that actually matters.

### 5.3 Move and rename

A move never changes an ID.

For a file:

- `path` becomes the new relative path.
- `folder` becomes the new current parent ID.
- `id_folder` and `number` remain unchanged.
- `codes:` shows the new path.

For a folder:

- `path` becomes the new relative path.
- `parent` becomes the new current parent ID.
- Every active descendant path is rewritten with the new prefix.
- `id_parent`, `segment`, and the full ID remain unchanged.
- The segment is also reserved under the new parent's counter to prevent future
  collisions.

Filesystem behavior:

- If the old path exists and the new path does not, the helper moves the item
  with `shutil.move`.
- If the old path is missing and the new path exists, the helper assumes an
  external move and only relinks the state. No filesystem change is made.
- If both exist, neither exists, the destination is already indexed, or a folder
  would move inside itself, the command fails.

Map history is never rewritten. A move adds one map entry, `Move file` or
`Move folder` by default; previous entries keep their historical paths.

### 5.4 Delete

`delete` updates the index only. It intentionally does not remove files from
disk; use the normal tools for that.

For a file:

- Status becomes `deleted`.
- The ID disappears from `codes:`.
- A `Delete file` entry is appended.
- The ID and number stay reserved forever.

For a folder:

- The folder and all currently active descendants become `deleted`.
- One `Delete folder` entry is appended for the folder ID.
- All removed IDs disappear from `codes:`.
- Their counters and `used_segments` remain reserved.

If the physical item is left on disk and is touched again later, it is treated as
a new item and receives a new ID. This is why external deletion should be
recorded promptly.

### 5.5 External changes

The helper does not watch the filesystem. After external changes:

- Rename or move: `move <old> <new>` with the old indexed path. If the filesystem
  move already happened, it relinks without moving anything.
- Delete: `delete <path>`.
- Create outside the helper: `folder` or `file` adopts it.
- Edited outside the helper: `log <path> --action ...`.
- Any structural change: `verify`.

If a new path is indexed while an old active path is merely missing, the helper
cannot know they are the same item. It creates a new ID. Relink first when
stability matters.

## 6. State schema

The state block is the last thing in `h_index.md`:

```html
<!-- h-index-state
{
  "version": 1,
  ...
}
-->
```

The JSON inside is authoritative for counters, IDs, statuses, and history. The
visible sections are a rendering of it. `read_index` parses this block; helper
commands rewrite both parts atomically.

### 6.1 Top-level fields

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | integer | Schema version, currently `1` |
| `created_at` | string | UTC timestamp of initialization |
| `updated_at` | string | UTC timestamp of last write |
| `next_order` | integer | Next `map:` order number; equals `len(log) + 1` |
| `folders` | object | Folder ID to folder record |
| `files` | object | File ID to file record |
| `counters` | object | Folder ID (or `/`) to allocation counter |
| `log` | array | Chronological operation entries |

### 6.2 Folder record

```json
{
  "/A/B": {
    "path": "src/components",
    "parent": "/A",
    "id_parent": "/A",
    "segment": "B",
    "created_order": 4,
    "status": "active"
  }
}
```

| Field | Meaning |
| --- | --- |
| `path` | Current project-relative POSIX path, no trailing slash |
| `parent` | Current parent folder ID, or `/` for root |
| `id_parent` | Folder that allocated the segment; fixed forever |
| `segment` | Allocated letter segment; fixed forever |
| `created_order` | `map:` order of the create/adopt entry; used for `codes:` order |
| `status` | `active` or `deleted` |
| `deleted_order` | Present when deleted; order of the delete entry |

After a move, `parent` and `path` change but `id_parent` and `segment` do not.

### 6.3 File record

```json
{
  "/A/1": {
    "path": "src/main.py",
    "folder": "/A",
    "id_folder": "/A",
    "number": 1,
    "created_order": 2,
    "last_order": 3,
    "status": "active"
  }
}
```

| Field | Meaning |
| --- | --- |
| `path` | Current project-relative POSIX path |
| `folder` | Current parent folder ID, or `/` for root |
| `id_folder` | Folder that allocated the number; fixed forever |
| `number` | Allocated file number; fixed forever |
| `created_order` | Order of the create/adopt entry |
| `last_order` | Order of the most recent logged action for this file |
| `status` | `active` or `deleted` |
| `deleted_order` | Present when deleted |

### 6.4 Counter

```json
{
  "/A": {
    "next_file": 2,
    "used_segments": ["B"]
  }
}
```

| Field | Meaning |
| --- | --- |
| `next_file` | Next number to try for a file allocated in this folder |
| `used_segments` | Folder segments ever used under this folder |

`next_file` only moves forward. `used_segments` only grows. Both are retained for
deleted and moved items.

### 6.5 Log entry

```json
{
  "order": 3,
  "id": "/A/1",
  "action": "Write the parser",
  "kind": "file",
  "op": "modify",
  "path": "src/main.py",
  "timestamp": "2026-10-26T19:28:02Z"
}
```

Move entries also include `old_path` and `new_path`. Delete entries are followed
by status changes in the item records.

`kind` is `folder` or `file`. `op` is one of:

| Op | Meaning | Default action |
| --- | --- | --- |
| `create` | Folder or file was created | `Create folder` / `Create file` |
| `adopt` | Existing item was indexed | `Adopt folder` / `Adopt file` |
| `modify` | Logged work on an item | Agent-supplied |
| `move` | Item moved or relinked | `Move folder` / `Move file` |
| `delete` | Item marked deleted | `Delete folder` / `Delete file` |

Actions are single-line English, at most 80 characters, with no parentheses.
Whitespace is normalized to single spaces. The helper rejects control
characters.

### 6.6 State block integrity

- The JSON is formatted with two-space indentation for human inspection.
- `>` characters are serialized as `\u003e` so user-controlled strings cannot
  close the HTML comment early. JSON readers decode them transparently.
- Do not add or remove fields by hand. Unknown top-level fields are preserved by
  reads, but the helper does not use them.
- Required fields must have the documented types and statuses. Invalid values
  are rejected before a command can mutate the index.
- Missing required top-level fields make the state unreadable.
- The state is local and hidden from Git; it is not a security boundary.

## 7. Command reference

General form:

```text
python scripts/h_index.py [--root DIR] <command> [options]
```

`--root` is accepted before or after the subcommand. Without it, the helper
chooses the nearest directory containing `h_index.md`, then the Git root, then
the current directory.

Exit codes:

- `0`: success (warnings are allowed).
- `1`: Hermes-Index error, verification failure, or protection failure.
- `2`: argparse usage error.

### `init`

Creates `h_index.md`, applies the hidden attribute, and adds Git exclusions.
If the index already exists and is readable, validates it and refreshes
protections without resetting state. Never overwrites an unrecognized file.

### `folder <path> [--action TEXT] [--segment LETTERS]`

Creates or adopts a folder and missing ancestors. Prints `<ID> = <path>/`.
`--segment` applies only to a new folder. `--action` replaces the default
create/adopt action for a new folder; for an existing folder it appends a
`modify` entry.

### `file <path> [--action TEXT]`

Creates or adopts a file and missing ancestors. If absent, creates an empty file
without overwriting. Prints `<ID> = <path>`. `--action` behaves like the folder
option.

### `log <path> --action TEXT`

Appends a `modify` entry for an active item. If the path exists but is not
indexed, it is adopted first. The action is required and validated. Prints the
new map line.

### `move <old> <new> [--action TEXT]`

Moves or relinks an item and updates `codes:` without changing IDs. Creates
missing destination parents. Fails on conflicts. Prints `<ID> = <new path>`.

### `delete <path> [--action TEXT]`

Marks an active item and, for folders, all active descendants as deleted. Does
not touch the filesystem. Prints the deleted ID. If the path still exists, prints
a safety reminder to stderr.

### `status [--json]`

Read-only. Shows root, index path, active counts, map count, protection status,
and the active `codes:` block. JSON output includes a `codes` array with
`id`, `path`, and `type`.

### `verify [--json]`

Read-only structural check. Prints errors and warnings. Exits 1 if any error is
found. Warnings include Linux hidden-attribute limitations and a missing Git
exclusion.

### `repair [--json]`

Regenerates the visible `codes:` and `map:` sections from the state block. Useful
when the visible sections were edited manually. It does not fix inconsistent
state; remaining errors are reported.

### `hide [--json]`

Reapplies the OS hidden attribute and Git exclusion. On Linux, where a per-file
hidden attribute does not exist, it still applies Git exclusion and returns 0 with
a note. On Windows and macOS it returns 1 if hiding fails.

### `--version`

Prints the helper version.

## 8. Protection and concurrency

### 8.1 Hidden attribute

- **Windows:** sets `FILE_ATTRIBUTE_HIDDEN` with `SetFileAttributesW`.
- **macOS:** sets the `hidden` flag with `os.chflags` (falling back to the
  `chflags` command); reads `UF_HIDDEN` from `st_flags`.
- **Linux:** no per-file hidden attribute exists. The file stays visible in
  directory listings. This is documented and reported by `verify`.

Hiding is a convenience, not security. Anyone who knows the file exists can read
it. Do not put secrets in the index or its actions.

### 8.2 Git exclusion

The helper edits the repository-local `.git/info/exclude` and adds:

```text
/h_index.md
/.h_index.lock
/.h_index.*.tmp
```

It does not touch `.gitignore`, so the project's tracked configuration stays
clean. If the project is not a Git repository, exclusion is skipped; after
`git init`, run `init` or `hide` to add it.

The exclude file is local to each clone. Since `h_index.md` itself is excluded,
it is never cloned. Each working copy that wants an index creates its own.

### 8.3 Atomic writes

State updates are written to a temporary `.h_index.*.tmp` file in the project
root, flushed, then replaced over `h_index.md`. The temporary file is excluded
from Git and cleaned up on failure. On Windows, a hidden destination may need its
attribute temporarily cleared before replacement; the helper does this
automatically.

### 8.4 Locking

A process lock is implemented as the directory `.h_index.lock` in the project
root. Commands wait up to five seconds, then fail with a clear message. Locks
older than five minutes are treated as stale and removed.

If a crash leaves the lock behind and no helper is running, remove the directory
manually. Do not remove a lock while another helper is actively writing.

## 9. Verification invariants

`verify` checks the following and reports each failure:

1. The visible `codes:` / `map:` text matches a fresh rendering of state.
2. `map:` orders are contiguous from 1.
3. `next_order` equals `len(log) + 1`.
4. Every folder ID matches the folder grammar; every file ID matches the file
   grammar; folder and file ID sets do not overlap.
5. Every active folder path exists and is a directory.
6. Every active file path exists and is a file.
7. No two active items share a path.
8. Every active folder's `parent` is active and its path's parent matches that
   folder's path, or `parent` is `/` and the folder is at the root.
9. Every active file's `folder` is active and its path's parent matches that
   folder's path, or `folder` is `/` and the file is at the root.
10. Every folder's `id_parent` exists in state and its `segment` is reserved in
    that parent's `used_segments`.
11. Every file's `id_folder` exists in state.
12. Every counter has unique segments and `next_file` beyond every file number
    ever allocated from it.
13. Every log entry references a known ID and has a valid `kind`, `op`, and
    action.

`verify` does **not** require every file on disk to be indexed. Lazy adoption is
intentional, so unindexed files are allowed. It also does not validate deleted
item paths against the filesystem.

Warnings are not failures. Treat them as maintenance suggestions, especially on
Linux where hidden attributes are unavailable.

## 10. Recovery

### 10.1 External rename

If a file was renamed outside the helper, run:

```text
move <old indexed path> <new path>
```

When the old path is missing and the new path exists, the helper relinks the
existing ID without moving anything. Check `status` before and after.

### 10.2 External delete

Remove the filesystem item, then run `delete <path>` so the ID is retired. If the
delete is recorded first, remove the item afterward. Either order works; recording
promptly prevents accidental reuse.

### 10.3 Visible sections tampered with

Run `repair`. It rewrites `codes:` and `map:` from state. If repair reports state
errors, use normal commands or restore files; do not hand-edit JSON.

### 10.4 Stale lock

If a command reports that another process holds `.h_index.lock` and no helper is
running, delete the lock directory:

```text
rm -rf .h_index.lock
```

On Windows, remove it with Explorer or `Remove-Item -Recurse -Force`. The helper
also removes locks older than five minutes automatically.

### 10.5 Missing or corrupt index

If `h_index.md` is absent, initialization is a user decision. If it exists but is
unrecognized (no state block, unreadable JSON, or unsupported version), stop and
ask. Do not overwrite it.

Rebuilding an index from scratch resets IDs and history. There is no automatic
backup because the file is local and excluded from Git. If IDs matter, keep a
manual backup before risky filesystem work.

### 10.6 Git was initialized later

Run `init` (if the index exists, it refreshes protections) or `hide`. This adds
the local exclude entries. No commit is needed.

### 10.7 Linux visibility

This is expected. There is no per-file hidden attribute on Linux. The index is
still excluded from Git. Do not add it to `.gitignore` just to reduce visibility.

## 11. Design notes and limitations

- **Local memory, not shared state.** The index is per working copy and excluded
  from Git. Clones do not share IDs.
- **Stable, not semantic.** A moved item keeps its original ID. An ID does not
  describe the current tree and should not be decoded as a path.
- **No bulk scan.** The index grows lazily as items are touched. There is no
  command to index an entire repository automatically.
- **Numbers can repeat across folders.** `/A/1` and `/B/1` are different files.
  The full ID is always used.
- **Folder letters can repeat across parents.** `/A/B` and `/C/B` are different
  folders.
- **Deletes are logical.** `delete` is an index operation. Filesystem removal is
  separate by design to avoid destructive surprises.
- **Hidden is not private.** Hidden attributes and Git excludes are convenience
  and hygiene, not access control.
- **Cooperative concurrency.** The lock protects helper processes in one working
  copy. It cannot coordinate independent clones or manual edits.
- **Python 3.9+ standard library only.** No third-party Python packages or
  network access. Git is optional, and macOS can fall back to the system
  `chflags` command for the hidden flag.
- **Cross-platform paths.** Paths are normalized to resolved absolute paths for
  containment checks and stored as project-relative POSIX paths. Windows path
  comparisons are case-insensitive through the OS.
- **State comment escaping.** `>` is escaped in JSON so no user string can
  terminate the HTML comment early.
- **Index file name is reserved.** The helper refuses to index `h_index.md`, the
  lock, and VCS metadata.

## 12. Testing

Run the test suite from the repository root:

```text
python -m unittest discover -s tests -v
```

The tests cover:

- initialization and idempotence,
- exact `codes:` / `map:` format,
- root and per-folder counters,
- A-Z exhaustion, deterministic multi-letter IDs, and manual segments,
- lazy adoption with content preservation,
- moves, external relinks, descendant updates, and conflicts,
- deletion without ID reuse,
- hidden attributes where supported,
- Git exclusion through `.git/info/exclude`,
- visible tampering and `repair`,
- action validation and state corruption errors.

The GitHub Actions workflow runs the same suite on Linux, Windows, and macOS with
supported Python versions.
