# Agent workflow example

This walkthrough shows a realistic Hermes-Index session. It assumes an existing
Python project with `src/main.py` and no index yet. Commands use
`<skill-dir>` for the folder containing `SKILL.md`.

## 1. First contact

The user asks for a new authentication flow. Before touching files, the agent
checks for `h_index.md`; it is absent, so the agent offers initialization once:

> This project has no Hermes-Index. Should I initialize it? It creates a hidden
> local `h_index.md` and excludes it from Git. Real file names stay unchanged.

The user agrees.

```text
$ python "<skill-dir>/scripts/h_index.py" init
Initialized /project/h_index.md
Hidden: yes
Git: excluded via .git/info/exclude
```

No IDs exist yet. The empty index has `codes:` and `map:` headers and a private
state block.

## 2. Adopt the entry point

`src/` and `src/main.py` already exist. Touching the file adopts them lazily:

```text
$ python "<skill-dir>/scripts/h_index.py" file src/main.py
/A/1 = src/main.py
```

```text
$ python "<skill-dir>/scripts/h_index.py" log src/main.py --action "Review the entry point"
3: /A/1(Review the entry point)
```

At this point:

```text
codes:
/A = src/
/A/1 = src/main.py

map:
1: /A(Adopt folder)
2: /A/1(Adopt file)
3: /A/1(Review the entry point)
```

## 3. Add the feature

Create the new folder and file through the helper, then write the content:

```text
$ python "<skill-dir>/scripts/h_index.py" folder src/auth
/A/A = src/auth/

$ python "<skill-dir>/scripts/h_index.py" file src/auth/login.py
/A/A/1 = src/auth/login.py
```

`src/auth` is `/A/A`, not `/A/B`: folder letters are allocated per parent, and
this is the first child folder under `src/`.

After implementing the flow:

```text
$ python "<skill-dir>/scripts/h_index.py" log src/auth/login.py --action "Implement login flow"
6: /A/A/1(Implement login flow)
```

## 4. Rename without losing the ID

The user decides `login.py` should be `session.py`. Use the helper, not a shell
move:

```text
$ python "<skill-dir>/scripts/h_index.py" move src/auth/login.py src/auth/session.py --action "Rename login module"
/A/A/1 = src/auth/session.py
```

The ID remains `/A/A/1`; only the path in `codes:` changes. If the file had
already been renamed outside the helper, the same command with the old path
would relink it without touching the filesystem.

## 5. Add a test

```text
$ python "<skill-dir>/scripts/h_index.py" file tests/test_session.py
/B/1 = tests/test_session.py

$ python "<skill-dir>/scripts/h_index.py" log tests/test_session.py --action "Add session regression test"
10: /B/1(Add session regression test)
```

## 6. Delete an item

The old session module is superseded. Remove it with normal tools, then retire
its ID:

```text
$ rm src/auth/session.py
$ python "<skill-dir>/scripts/h_index.py" delete src/auth/session.py
Deleted /A/A/1 = src/auth/session.py
```

The ID and its file number are now reserved forever. They disappear from
`codes:` but remain in `map:` and state. The empty `src/auth/` folder stays
active and indexed.

## 7. Verify

```text
$ python "<skill-dir>/scripts/h_index.py" verify
OK: 3 folder(s), 2 file(s), 11 map entry(ies)
```

Final visible index:

```text
codes:
/A = src/
/A/1 = src/main.py
/A/A = src/auth/
/B = tests/
/B/1 = tests/test_session.py

map:
1: /A(Adopt folder)
2: /A/1(Adopt file)
3: /A/1(Review the entry point)
4: /A/A(Create folder)
5: /A/A/1(Create file)
6: /A/A/1(Implement login flow)
7: /A/A/1(Rename login module)
8: /B(Create folder)
9: /B/1(Create file)
10: /B/1(Add session regression test)
11: /A/A/1(Delete file)
```

The project's actual files are still `src/main.py`, `src/auth/`, and
`tests/test_session.py`. No file names contain IDs.

## What the agent did well

- Checked for the index before the first edit.
- Asked for initialization exactly once.
- Created through the helper so creation was recorded accurately.
- Logged meaningful work in short base-form English.
- Used `move` so the ID survived a rename.
- Removed the file with normal tools and recorded the deletion separately.
- Ran `verify` after the structural change.

This is the intended rhythm: the helper owns the map, the agent owns the work,
and the project tree keeps its natural names.
