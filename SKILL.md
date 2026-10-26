---
name: hermes-index
description: >-
  Maintain Hermes-Index, a hidden h_index.md that gives every project file and
  folder a stable logical ID (such as /A/1) while keeping real names unchanged.
  Use this skill whenever you create, edit, move, rename, or delete files or
  folders in a project that already has h_index.md, and whenever the user
  mentions Hermes-Index, h_index.md, project file IDs, folder codes, stable file
  references, or asks to index a project. Also use it at the start of a
  multi-file coding task to check for h_index.md and offer initialization once
  when it is missing.
license: MIT
metadata:
  author: Articipate
  version: "1.0.0"
---

# Hermes-Index

Hermes-Index is a local memory map for a project. It gives each folder and file
a short logical ID such as `/A`, `/A/1`, or `/A/B/2`, while the real files keep
their meaningful names. The map lives in a hidden `h_index.md` at the project
root. Use it to refer to work consistently across a long task, a refactor, or a
new session.

The index is split into two parts:

- `codes:` maps active IDs to real paths.
- `map:` is an append-only, chronological list of short actions.

A machine-readable state block sits at the bottom in an HTML comment. Treat it
as private implementation detail and never edit it by hand.

## Trigger behavior

At the start of any task that may create or modify project files:

1. Look for `h_index.md` at the project root. It is OS-hidden on Windows and
   macOS, so use the helper rather than assuming a visible directory listing is
   complete.
2. If `h_index.md` exists, use it automatically. Do not ask for permission to
   maintain it and do not create a second index in a subfolder.
3. If `h_index.md` is absent, offer initialization **once**:

   > This project has no Hermes-Index. Should I initialize it? It creates a
   > hidden local `h_index.md` and excludes it from Git. Real file names stay
   > unchanged.

   If the user says no, continue normally and do not ask again in that session.
   If the user explicitly asks to initialize or set up Hermes-Index, that is
   consent; run `init` without asking again.
4. Never create `h_index.md` silently, and never overwrite an existing
   `h_index.md` that Hermes-Index does not recognize. Ask the user first.

## Helper

All mutations go through the Python helper bundled with this skill:

```text
python "<skill-dir>/scripts/h_index.py" <command> [arguments]
```

`<skill-dir>` is the directory containing this `SKILL.md`. The helper requires
Python 3.9+ and uses only the standard library. It finds the nearest directory
with `h_index.md`, then the Git root, then the current directory. Pass `--root
<dir>` before or after the subcommand when working somewhere else.

## Workflow

| Situation | Action |
| --- | --- |
| User explicitly approves initialization | `init` |
| Create a folder | `folder <path>` |
| Create a file | `file <path>`, write the content, then `log` the work |
| Edit an existing indexed file | `log <path> --action "<short action>"` |
| Rename or move an item | `move <old> <new>` instead of a shell move |
| Delete an item | Remove it with normal tools, then `delete <path>` |
| Resolve an ID or inspect active paths | `status` or `status --json` |
| Check consistency | `verify` |
| Visible sections were edited manually | `repair` |
| Reapply hiding and Git exclusion | `hide` |

`folder` and `file` create missing parents, are safe on existing paths, and
print the assigned ID. They never overwrite existing content:

```text
$ python "<skill-dir>/scripts/h_index.py" file src/login.py
/A/1 = src/login.py
```

After writing the file, record the meaningful work:

```text
$ python "<skill-dir>/scripts/h_index.py" log src/login.py --action "Implement login flow"
3: /A/1(Implement login flow)
```

For a small task, `file <path> --action "<action>"` can create the file and
record the semantic action in one call. Without `--action`, the helper records
`Create file` or `Adopt file`. If the item is already indexed, passing
`--action` appends that action as a new `map:` entry.

### New file sequence

1. Run `file` first so the map records the creation correctly.
2. Write the content with the normal editing tools.
3. Run `log` once for the logical unit of work.
4. Run `verify` after structural changes such as moves or deletions.

Do not create a file with another tool and then rename it to the path you
already indexed. Use `move`; it keeps the ID stable.

### Folders

`folder src/components` creates the folder if needed and adopts it if it already
exists. It also indexes missing ancestors. Use it before creating a folder so
the map says `Create folder` rather than `Adopt folder`.

Manual folder segments are available for new IDs when the user wants a
particular code:

```text
python "<skill-dir>/scripts/h_index.py" folder src --segment SRC
```

Existing IDs never change. A manual segment cannot be used to reassign an ID
that was already allocated.

### Moves and renames

Always use the helper for moves:

```text
python "<skill-dir>/scripts/h_index.py" move src/auth/login.py src/auth/session.py
```

The helper moves the filesystem item and updates every affected path in
`codes:`. The ID stays the same. For a folder, all descendant paths are updated
in one operation.

If a file was already renamed outside the helper, run `move <old> <new>` with
the old indexed path. When the old path is gone and the new path exists, the
helper treats it as a relink and does not move anything on disk. This is the
recovery path for external renames.

### Deletes

`delete` only updates the index; it never removes files as a safety measure.
Remove the item with your normal tools and then record it:

```text
python "<skill-dir>/scripts/h_index.py" delete src/auth/session.py
```

A deleted ID disappears from `codes:` but stays in the state and in `map:`
history. IDs and per-folder numbers are never reused.

## Index format

The visible format is exact and intentionally minimal:

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

Rules:

- Folder IDs use letters: `/A`, `/A/B`. Folder paths end with `/` in `codes:`.
- File IDs use per-folder numbers: `/A/1`, `/A/B/1`. Root files use `/1`, `/2`.
- IDs have a leading slash everywhere.
- `codes:` is ordered by ID allocation and shows only active items.
- `map:` is append-only and ordered by the operation number. Deleted entries
  remain there forever.
- Each `map:` line is exactly `<order>: <ID>(<action>)`. There is no space before
  the parenthesis.
- Do not add headings, bullets, timestamps, comments, or explanations to the
  visible sections. Do not edit the state block.

### ID allocation

- Folder segments start at `A` and advance through `Z` independently under each
  parent folder. Once `A`-`Z` are used under a parent, the helper generates a
  deterministic 2+ letter segment from the allocation parent and path. This
  keeps IDs short in normal projects and still avoids collisions at scale.
- File numbers are per folder. Each folder, including the project root, has its
  own counter. Deleting a file does not rewind the counter.
- Moving or renaming never changes an ID. Moving a folder updates descendant
  paths but not their IDs.
- IDs are stable references, not a description of the current tree. A folder ID
  may keep its original allocation parent after a move. That is expected.
- Do not invent, shorten, renumber, or recycle IDs. If you need an ID, read it
  from `status` or `codes:`.

## Action style

Actions are written by you, not by the helper. They should read like a compact
engineering log in English.

- Use the imperative or base form: `Create`, `Fix`, `Refactor`, `Update`.
- Keep it to roughly 2-8 words. The helper rejects actions over 80 characters.
- Do not include paths, filenames, IDs, parentheses, or line breaks. The path is
  already in `codes:` and the ID is at the start of the line.
- Log one entry per meaningful unit of work, not every keystroke. For a
  multi-file refactor, log each touched ID with the same concise action.
- Do not write vague entries such as `Update stuff` or `Work on file`.

Good:

```text
Create login form
Fix session expiry
Refactor auth middleware
Update README examples
Remove unused imports
Add pagination tests
```

Bad:

```text
Created login form          (past tense)
Write src/login.py          (path)
Fix bug (session)           (parentheses)
login form stuff            (vague)
Update everything           (too broad)
```

## Verify and repair

Run `verify` after moves, deletes, merges, or any external filesystem change:

```text
python "<skill-dir>/scripts/h_index.py" verify
```

Run `status` at any time to read the active `codes:` entries without changing
the index. Use `status --json` when you need to parse the mapping.
`verify --json` and `repair --json` are also available for automation.

It checks that:

- active paths exist and have the right kind,
- parent folder relationships match the filesystem,
- IDs follow the grammar and do not overlap,
- per-folder counters are beyond every allocated number,
- `map:` order is contiguous,
- the visible sections match the state,
- the index is hidden and excluded from Git where the platform allows it.

If only the visible sections were edited manually, `repair` regenerates them
from the state block. If the state itself is inconsistent, `repair` still
reports the remaining errors; fix those through the normal commands rather than
editing JSON.

If a command fails, do not hand-edit the state to force it through. Common
recoveries:

- **External rename:** `move <old> <new>` to relink the existing ID.
- **Stale lock:** if no helper is running, remove the `.h_index.lock` directory
  and retry. The helper also clears locks older than five minutes.
- **Unrecognized index:** stop and ask the user. Do not overwrite it.
- **Missing active path:** restore the item, or record its deletion with
  `delete` if it is gone.

## Protection

The helper hides `h_index.md` with the OS hidden attribute on Windows and macOS
and excludes it, the lock, and temporary files through `.git/info/exclude`. It
does not modify `.gitignore`.

Linux has no per-file hidden attribute. On Linux the file remains visible in
directory listings but is still excluded from Git. `verify` reports this as a
warning, not an error. Do not add `h_index.md` to `.gitignore` just to hide it;
the local exclude is intentional so the project's tracked configuration stays
clean.

Because the index is excluded from Git, it is local to a working copy. Each
clone that wants an index initializes one. Never commit `h_index.md`.

## Example session

```text
User: Add a settings page to this project.

Agent: checks for h_index.md; it exists, so maintenance is automatic.
Agent: python "<skill-dir>/scripts/h_index.py" file src/settings.py
       /A/1 = src/settings.py
Agent: writes the page and its styles.
Agent: python "<skill-dir>/scripts/h_index.py" log src/settings.py --action "Build settings page"
       3: /A/1(Build settings page)
Agent: python "<skill-dir>/scripts/h_index.py" verify
       OK: 1 folder(s), 1 file(s), 3 map entry(ies)
```

If the project had no index, the agent would first offer initialization once and
wait for consent.

## Further reading

Read [`references/rules.md`](references/rules.md) when you need the full ID
algorithm, state schema, recovery procedures, or platform details. The
[`examples/`](examples/) directory contains a rendered index and a longer agent
walkthrough.
