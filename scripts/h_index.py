#!/usr/bin/env python3
"""Hermes-Index helper.

Maintains a hidden ``h_index.md`` file with two visible sections:

    codes:
    /A = src/

    map:
    1: /A(Create folder)

The machine-readable state is stored in an HTML comment at the bottom of the
file so the visible sections stay small and human-readable.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Sequence, Tuple

VERSION = "1.0.0"
INDEX_FILENAME = "h_index.md"
LOCK_DIRNAME = ".h_index.lock"
STATE_MARKER = "<!-- h-index-state"
STATE_VERSION = 1
STATUS_ACTIVE = "active"
STATUS_DELETED = "deleted"
LOG_OPS = ("create", "adopt", "modify", "move", "delete")
ACTION_MAX_LENGTH = 80
FOLDER_ID_RE = re.compile(r"^/(?:[A-Z]+)(?:/[A-Z]+)*$")
FILE_ID_RE = re.compile(r"^/(?:[A-Z]+/)*[1-9][0-9]*$")
SEGMENT_RE = re.compile(r"^[A-Z]+$")
LETTERS = [chr(code) for code in range(ord("A"), ord("Z") + 1)]
MACOS_UF_HIDDEN = 0x8000


class HIndexError(Exception):
    """A user-facing Hermes-Index error."""


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def find_project_root(
    start: Optional[Path] = None, override: Optional[str] = None
) -> Path:
    if override:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            raise HIndexError(f"project root is not a directory: {root}")
        return root
    start = (start or Path.cwd()).resolve()
    git_root: Optional[Path] = None
    try:
        proc = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            git_root = Path(proc.stdout.strip()).resolve()
    except (OSError, ValueError):
        pass

    # Prefer the nearest directory that already owns an index. This makes
    # commands work from subdirectories even outside a Git repository.
    current = start
    while True:
        if (current / INDEX_FILENAME).is_file():
            return current
        if git_root is not None and current == git_root:
            break
        if current.parent == current:
            break
        current = current.parent
    if git_root is not None:
        return git_root
    return start


def normalize_rel_path(root: Path, raw_path: str) -> Tuple[Path, str]:
    """Return (absolute path, POSIX path relative to root)."""
    if raw_path is None or str(raw_path).strip() == "":
        raise HIndexError("path must not be empty")
    if any(char in str(raw_path) for char in ("\r", "\n", "\x00")):
        raise HIndexError("path must not contain newlines or NUL characters")
    root = root.resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=False)
    root_key = os.path.normcase(str(root))
    resolved_key = os.path.normcase(str(resolved))
    try:
        common = os.path.commonpath([root_key, resolved_key])
    except ValueError as exc:
        raise HIndexError(f"path is outside the project root: {raw_path}") from exc
    if common != root_key:
        raise HIndexError(f"path is outside the project root: {raw_path}")
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        rel = Path(os.path.relpath(str(resolved), str(root)))
    if rel == Path("."):
        return resolved, "."
    rel_posix = rel.as_posix()
    parts = PurePosixPath(rel_posix).parts
    lowered = [part.casefold() for part in parts]
    if lowered and lowered[0] in {".git", ".hg", ".svn"}:
        raise HIndexError(f"refusing to index VCS metadata: {raw_path}")
    if rel_posix.casefold() == INDEX_FILENAME.casefold():
        raise HIndexError(f"refusing to index the Hermes-Index file itself: {raw_path}")
    if lowered and lowered[0] == LOCK_DIRNAME.casefold():
        raise HIndexError(f"refusing to index the Hermes-Index lock: {raw_path}")
    return resolved, rel_posix


def _git(root: Path, *args: str) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None


def _folders_match(path_a: str, path_b: str) -> bool:
    return os.path.normcase(path_a.rstrip("/")) == os.path.normcase(
        path_b.rstrip("/")
    )


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


class ProjectLock:
    """A tiny cross-platform lock based on atomic directory creation."""

    def __init__(self, root: Path, timeout: float = 5.0, stale_after: float = 300.0):
        self.path = root / LOCK_DIRNAME
        self.timeout = timeout
        self.stale_after = stale_after
        self.acquired = False

    def __enter__(self) -> "ProjectLock":
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir(mode=0o700)
                self.acquired = True
                return self
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > self.stale_after:
                    try:
                        if self.path.is_dir():
                            self.path.rmdir()
                        else:
                            self.path.unlink()
                    except OSError:
                        pass
                    continue
                if time.monotonic() >= deadline:
                    raise HIndexError(
                        f"another Hermes-Index process holds {self.path}; "
                        "remove the lock if no process is running"
                    )
                time.sleep(0.05)
            except OSError as exc:
                raise HIndexError(f"cannot create lock {self.path}: {exc}") from exc

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.acquired:
            return
        try:
            if self.path.is_dir():
                self.path.rmdir()
            elif self.path.exists():
                self.path.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Hiding and Git exclusion
# ---------------------------------------------------------------------------


def apply_hidden(path: Path) -> bool:
    """Best-effort OS hidden attribute. Returns True when applied."""
    if not path.exists():
        return False
    if os.name == "nt":
        try:
            FILE_ATTRIBUTE_HIDDEN = 0x02
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs == -1:
                return False
            return bool(
                ctypes.windll.kernel32.SetFileAttributesW(
                    str(path), attrs | FILE_ATTRIBUTE_HIDDEN
                )
            )
        except Exception:
            return False
    if sys.platform == "darwin":
        try:
            if hasattr(os, "chflags"):
                flags = getattr(os.stat(path), "st_flags", 0)
                os.chflags(path, flags | MACOS_UF_HIDDEN)
                return True
            proc = subprocess.run(
                ["chflags", "hidden", str(path)],
                capture_output=True,
                check=False,
            )
            return proc.returncode == 0
        except (OSError, ValueError):
            return False
    return False


def clear_hidden(path: Path) -> None:
    if not path.exists():
        return
    if os.name == "nt":
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            if attrs != -1:
                ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs & ~0x02)
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            if hasattr(os, "chflags"):
                flags = getattr(os.stat(path), "st_flags", 0)
                os.chflags(path, flags & ~MACOS_UF_HIDDEN)
            else:
                subprocess.run(
                    ["chflags", "nohidden", str(path)],
                    capture_output=True,
                    check=False,
                )
        except (OSError, ValueError):
            pass


def is_hidden(path: Path) -> bool:
    if not path.exists():
        return False
    if os.name == "nt":
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            return attrs != -1 and bool(attrs & 0x02)
        except Exception:
            return False
    if sys.platform == "darwin":
        try:
            return bool(getattr(os.stat(path), "st_flags", 0) & MACOS_UF_HIDDEN)
        except OSError:
            return False
    return False


def apply_git_exclude(root: Path, index_path: Path) -> bool:
    """Add the index and lock to .git/info/exclude without touching .gitignore."""
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return False
    git_root = Path(proc.stdout.strip()).resolve()
    proc = _git(root, "rev-parse", "--absolute-git-dir")
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return False
    git_dir = Path(proc.stdout.strip()).resolve()
    exclude = git_dir / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    try:
        rel = index_path.resolve().relative_to(git_root)
    except ValueError:
        rel = Path(os.path.relpath(str(index_path.resolve()), str(git_root)))
    pattern = "/" + rel.as_posix().lstrip("/")
    lock_rel = (rel.parent / LOCK_DIRNAME).as_posix().lstrip("/")
    lock_pattern = "/" + lock_rel
    tmp_rel = (rel.parent / ".h_index.*.tmp").as_posix().lstrip("/")
    tmp_pattern = "/" + tmp_rel
    text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    existing = set(text.splitlines())
    to_add = [
        item
        for item in (pattern, lock_pattern, tmp_pattern)
        if item not in existing
    ]
    if to_add:
        with exclude.open("a", encoding="utf-8", newline="\n") as handle:
            if text and not text.endswith("\n"):
                handle.write("\n")
            for item in to_add:
                handle.write(item + "\n")
    return True


def is_git_ignored(root: Path, index_path: Path) -> Optional[bool]:
    proc = _git(root, "rev-parse", "--show-toplevel")
    if proc is None or proc.returncode != 0 or not proc.stdout.strip():
        return None
    git_root = Path(proc.stdout.strip()).resolve()
    try:
        rel = index_path.resolve().relative_to(git_root)
    except ValueError:
        rel = Path(os.path.relpath(str(index_path.resolve()), str(git_root)))
    proc = _git(root, "check-ignore", "-q", "--", rel.as_posix())
    if proc is None:
        return None
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    return None


# ---------------------------------------------------------------------------
# State and visible rendering
# ---------------------------------------------------------------------------


def new_state() -> Dict[str, Any]:
    now = utc_now()
    return {
        "version": STATE_VERSION,
        "created_at": now,
        "updated_at": now,
        "next_order": 1,
        "folders": {},
        "files": {},
        "counters": {"/": {"next_file": 1, "used_segments": []}},
        "log": [],
    }


def normalize_state(state: Any) -> None:
    if not isinstance(state, dict):
        raise HIndexError("state block is not a JSON object")
    for key in ("version", "next_order", "folders", "files", "counters", "log"):
        if key not in state:
            raise HIndexError(f"state block is missing '{key}'")
    if not isinstance(state.get("folders", {}), dict):
        raise HIndexError("state.folders must be an object")
    if not isinstance(state.get("files", {}), dict):
        raise HIndexError("state.files must be an object")
    if not isinstance(state.get("counters", {}), dict):
        raise HIndexError("state.counters must be an object")
    if not isinstance(state.get("log", []), list):
        raise HIndexError("state.log must be an array")
    state.setdefault("created_at", utc_now())
    state.setdefault("updated_at", utc_now())
    counters = state["counters"]
    if "/" not in counters:
        counters["/"] = {"next_file": 1, "used_segments": []}
    for counter in counters.values():
        if not isinstance(counter, dict):
            raise HIndexError("each counter must be an object")
        counter.setdefault("next_file", 1)
        counter.setdefault("used_segments", [])
        if not isinstance(counter["used_segments"], list):
            raise HIndexError("counter.used_segments must be an array")
    for folder_id, folder in state["folders"].items():
        if not isinstance(folder, dict):
            raise HIndexError(f"folder entry {folder_id} is not an object")
        folder.setdefault("path", "")
        folder.setdefault("parent", "/")
        folder.setdefault("id_parent", folder["parent"])
        folder.setdefault("segment", folder_id.rsplit("/", 1)[-1])
        folder.setdefault("created_order", 0)
        folder.setdefault("status", STATUS_ACTIVE)
    for file_id, file_entry in state["files"].items():
        if not isinstance(file_entry, dict):
            raise HIndexError(f"file entry {file_id} is not an object")
        file_entry.setdefault("path", "")
        file_entry.setdefault("folder", "/")
        file_entry.setdefault("id_folder", file_entry["folder"])
        try:
            default_number = int(file_id.rsplit("/", 1)[-1])
        except ValueError:
            default_number = 0
        file_entry.setdefault("number", default_number)
        file_entry.setdefault("created_order", 0)
        file_entry.setdefault("last_order", file_entry["created_order"])
        file_entry.setdefault("status", STATUS_ACTIVE)


def _is_int(value: Any) -> bool:
    """True for real integers, excluding bool (a subclass of int)."""
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_status(record: Dict[str, Any], label: str) -> None:
    status = record.get("status")
    if status not in (STATUS_ACTIVE, STATUS_DELETED):
        raise HIndexError(f"{label} has invalid status {status!r}")
    if "deleted_order" in record and not _is_int(record["deleted_order"]):
        raise HIndexError(f"{label} field 'deleted_order' must be an integer")


def _validate_folder_record(folder_id: str, folder: Any) -> None:
    if not FOLDER_ID_RE.fullmatch(folder_id):
        raise HIndexError(f"invalid folder ID: {folder_id}")
    label = f"folder {folder_id}"
    if not isinstance(folder, dict):
        raise HIndexError(f"{label} is not an object")
    for field in ("path", "parent", "id_parent", "segment"):
        if not isinstance(folder.get(field), str):
            raise HIndexError(f"{label} field '{field}' must be a string")
    for field in ("parent", "id_parent"):
        value = folder[field]
        if value != "/" and not FOLDER_ID_RE.fullmatch(value):
            raise HIndexError(f"{label} field '{field}' is not a valid folder ID")
    if not SEGMENT_RE.fullmatch(folder["segment"]) or not 1 <= len(folder["segment"]) <= 16:
        raise HIndexError(f"{label} field 'segment' must be 1-16 letters A-Z")
    for field in ("created_order", "last_order"):
        if field in folder and not _is_int(folder[field]):
            raise HIndexError(f"{label} field '{field}' must be an integer")
    _validate_status(folder, label)


def _validate_file_record(file_id: str, file_entry: Any) -> None:
    if not FILE_ID_RE.fullmatch(file_id):
        raise HIndexError(f"invalid file ID: {file_id}")
    label = f"file {file_id}"
    if not isinstance(file_entry, dict):
        raise HIndexError(f"{label} is not an object")
    for field in ("path", "folder", "id_folder"):
        if not isinstance(file_entry.get(field), str):
            raise HIndexError(f"{label} field '{field}' must be a string")
    for field in ("folder", "id_folder"):
        value = file_entry[field]
        if value != "/" and not FOLDER_ID_RE.fullmatch(value):
            raise HIndexError(f"{label} field '{field}' is not a valid folder ID")
    if not _is_int(file_entry.get("number")) or file_entry["number"] < 1:
        raise HIndexError(f"{label} field 'number' must be a positive integer")
    for field in ("created_order", "last_order"):
        if field in file_entry and not _is_int(file_entry[field]):
            raise HIndexError(f"{label} field '{field}' must be an integer")
    _validate_status(file_entry, label)


def _validate_counter(counter_id: str, counter: Any) -> None:
    if counter_id != "/" and not FOLDER_ID_RE.fullmatch(counter_id):
        raise HIndexError(f"invalid counter ID: {counter_id}")
    label = f"counter {counter_id}"
    if not isinstance(counter, dict):
        raise HIndexError(f"{label} must be an object")
    if not _is_int(counter.get("next_file")) or counter["next_file"] < 1:
        raise HIndexError(f"{label} field 'next_file' must be a positive integer")
    used = counter.get("used_segments")
    if not isinstance(used, list):
        raise HIndexError(f"{label} field 'used_segments' must be an array")
    for segment in used:
        if (
            not isinstance(segment, str)
            or not SEGMENT_RE.fullmatch(segment)
            or not 1 <= len(segment) <= 16
        ):
            raise HIndexError(f"{label} contains an invalid segment: {segment!r}")


def _validate_log_entry(entry: Any, position: int) -> None:
    label = f"map entry {position}"
    if not isinstance(entry, dict):
        raise HIndexError(f"{label} is not an object")
    for key in ("order", "id", "action", "kind", "op", "path"):
        if key not in entry:
            raise HIndexError(f"{label} is missing '{key}'")
    if not _is_int(entry["order"]) or entry["order"] < 1:
        raise HIndexError(f"{label} field 'order' must be a positive integer")
    item_id = entry["id"]
    if not isinstance(item_id, str) or not (
        FOLDER_ID_RE.fullmatch(item_id) or FILE_ID_RE.fullmatch(item_id)
    ):
        raise HIndexError(f"{label} field 'id' is not a valid item ID")
    if entry["kind"] not in ("folder", "file"):
        raise HIndexError(f"{label} field 'kind' must be 'folder' or 'file'")
    if entry["op"] not in LOG_OPS:
        raise HIndexError(f"{label} field 'op' is invalid")
    for key in ("path", "action"):
        if not isinstance(entry[key], str):
            raise HIndexError(f"{label} field '{key}' must be a string")
    validate_action(entry["action"])
    for key in ("timestamp", "old_path", "new_path"):
        if key in entry and not isinstance(entry[key], str):
            raise HIndexError(f"{label} field '{key}' must be a string")


def validate_state(state: Any) -> None:
    if not isinstance(state, dict):
        raise HIndexError("state block is not a JSON object")
    if not _is_int(state.get("version")) or state["version"] != STATE_VERSION:
        raise HIndexError(
            f"unsupported state version {state.get('version')!r}; expected {STATE_VERSION}"
        )
    for key in ("folders", "files", "counters", "log"):
        if key not in state:
            raise HIndexError(f"state block is missing '{key}'")
    for key in ("created_at", "updated_at"):
        if key in state and not isinstance(state[key], str):
            raise HIndexError(f"state.{key} must be a string")
    if not isinstance(state["folders"], dict):
        raise HIndexError("state.folders must be an object")
    if not isinstance(state["files"], dict):
        raise HIndexError("state.files must be an object")
    if not isinstance(state["counters"], dict):
        raise HIndexError("state.counters must be an object")
    if not isinstance(state["log"], list):
        raise HIndexError("state.log must be an array")
    if not _is_int(state.get("next_order")) or state["next_order"] < 1:
        raise HIndexError("state.next_order must be a positive integer")

    for folder_id, folder in state["folders"].items():
        _validate_folder_record(folder_id, folder)
    for file_id, file_entry in state["files"].items():
        _validate_file_record(file_id, file_entry)
    for counter_id, counter in state["counters"].items():
        _validate_counter(counter_id, counter)
    for position, entry in enumerate(state["log"], start=1):
        _validate_log_entry(entry, position)


def folder_display_path(path: str) -> str:
    stripped = path.rstrip("/")
    return stripped + "/" if stripped else "/"


def render_codes(state: Dict[str, Any]) -> str:
    items: List[Tuple[int, str, str]] = []
    for folder_id, folder in state["folders"].items():
        if folder.get("status") == STATUS_ACTIVE:
            line = f"{folder_id} = {folder_display_path(folder.get('path', ''))}"
            items.append((int(folder.get("created_order", 0)), folder_id, line))
    for file_id, file_entry in state["files"].items():
        if file_entry.get("status") == STATUS_ACTIVE:
            line = f"{file_id} = {file_entry.get('path', '')}"
            items.append((int(file_entry.get("created_order", 0)), file_id, line))
    items.sort(key=lambda item: (item[0], item[1]))
    return "\n".join(item[2] for item in items)


def render_map(state: Dict[str, Any]) -> str:
    entries = sorted(state["log"], key=lambda entry: int(entry.get("order", 0)))
    return "\n".join(
        f"{entry.get('order')}: {entry.get('id')}({entry.get('action')})"
        for entry in entries
    )


def render_visible(state: Dict[str, Any]) -> str:
    return f"codes:\n{render_codes(state)}\n\nmap:\n{render_map(state)}"


def state_comment(state: Dict[str, Any]) -> str:
    body = json.dumps(state, indent=2, ensure_ascii=False, sort_keys=False)
    # Escaping '>' keeps user-controlled strings from closing the HTML
    # comment early. JSON decodes the escape transparently.
    body = body.replace(">", "\\u003e")
    return f"{STATE_MARKER}\n{body}\n-->"


def render_index(state: Dict[str, Any]) -> str:
    return f"{render_visible(state)}\n\n{state_comment(state)}\n"


def write_index(root: Path, state: Dict[str, Any], *, hide: bool = True) -> Path:
    state["updated_at"] = utc_now()
    index_path = root / INDEX_FILENAME
    content = render_index(state)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".h_index.", suffix=".tmp", dir=str(root)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(tmp_name, index_path)
        except PermissionError:
            clear_hidden(index_path)
            os.replace(tmp_name, index_path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
    if hide:
        apply_hidden(index_path)
    return index_path


def read_index(root: Path) -> Tuple[Path, Dict[str, Any], str]:
    index_path = root / INDEX_FILENAME
    if not index_path.is_file():
        raise HIndexError(
            f"{INDEX_FILENAME} not found in {root}; run 'init' first"
        )
    text = index_path.read_text(encoding="utf-8")
    start = text.rfind(STATE_MARKER)
    if start == -1:
        raise HIndexError(
            f"{index_path} is not managed by Hermes-Index (missing state block)"
        )
    end = text.find("-->", start + len(STATE_MARKER))
    if end == -1:
        raise HIndexError(f"{index_path} has an incomplete state block")
    payload = text[start + len(STATE_MARKER) : end].strip()
    try:
        state = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise HIndexError(
            f"cannot parse the state block in {index_path}: {exc}"
        ) from exc
    normalize_state(state)
    validate_state(state)
    return index_path, state, text


# ---------------------------------------------------------------------------
# State lookups and allocation helpers
# ---------------------------------------------------------------------------


def get_counter(state: Dict[str, Any], folder_id: str) -> Dict[str, Any]:
    counters = state["counters"]
    if folder_id not in counters:
        counters[folder_id] = {"next_file": 1, "used_segments": []}
    counter = counters[folder_id]
    counter.setdefault("next_file", 1)
    counter.setdefault("used_segments", [])
    return counter


def find_active_by_path(
    state: Dict[str, Any], rel_path: str, kind: Optional[str] = None
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    rel_path = PurePosixPath(rel_path).as_posix()
    if kind in (None, "folder"):
        for folder_id, folder in state["folders"].items():
            if (
                folder.get("status") == STATUS_ACTIVE
                and _folders_match(folder.get("path", ""), rel_path)
            ):
                return "folder", folder_id, folder
    if kind in (None, "file"):
        for file_id, file_entry in state["files"].items():
            if (
                file_entry.get("status") == STATUS_ACTIVE
                and _folders_match(file_entry.get("path", ""), rel_path)
            ):
                return "file", file_id, file_entry
    return None


def find_active_folder_by_path(
    state: Dict[str, Any], rel_path: str
) -> Optional[str]:
    found = find_active_by_path(state, rel_path, kind="folder")
    return found[1] if found else None


def join_folder_id(parent_id: str, segment: str) -> str:
    if parent_id == "/":
        return "/" + segment
    return parent_id.rstrip("/") + "/" + segment


def join_file_id(folder_id: str, number: int) -> str:
    if folder_id == "/":
        return "/" + str(number)
    return folder_id.rstrip("/") + "/" + str(number)


def generate_multi_segment(
    parent_id: str, rel_path: str, used: Sequence[str]
) -> str:
    used_set = set(used)
    seed = f"{parent_id}|{rel_path}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    for length in range(2, 17):
        segment = "".join(LETTERS[digest[index] % 26] for index in range(length))
        if segment not in used_set:
            return segment
    counter = 0
    while True:
        segment = "".join(
            LETTERS[(digest[index] + counter) % 26] for index in range(16)
        )
        if segment not in used_set:
            return segment
        counter += 1


def normalize_segment(segment: str) -> str:
    value = segment.strip().upper()
    if not value or len(value) > 16 or not SEGMENT_RE.fullmatch(value):
        raise HIndexError("folder segment must be 1-16 letters A-Z")
    return value


def allocate_folder_segment(
    state: Dict[str, Any],
    parent_id: str,
    rel_path: str,
    override: Optional[str] = None,
) -> str:
    counter = get_counter(state, parent_id)
    used = list(counter["used_segments"])
    used_set = set(used)
    if override:
        segment = normalize_segment(override)
        if segment in used_set:
            raise HIndexError(
                f"folder segment '{segment}' is already used under {parent_id}"
            )
        return segment
    for letter in LETTERS:
        if letter not in used_set:
            return letter
    return generate_multi_segment(parent_id, rel_path, used)


def validate_action(action: Optional[str]) -> str:
    if action is None:
        raise HIndexError("an action is required")
    if not isinstance(action, str):
        raise HIndexError("action must be a string")
    if "\n" in action or "\r" in action:
        raise HIndexError("action must be a single line")
    normalized = " ".join(action.strip().split())
    if not normalized:
        raise HIndexError("action must not be empty")
    if any(ord(char) < 32 for char in normalized):
        raise HIndexError("action must not contain control characters")
    if "(" in normalized or ")" in normalized:
        raise HIndexError("action must not contain parentheses")
    if len(normalized) > ACTION_MAX_LENGTH:
        raise HIndexError(
            f"action is too long ({len(normalized)} characters); "
            f"keep it under {ACTION_MAX_LENGTH}"
        )
    return normalized


def next_order(state: Dict[str, Any]) -> int:
    order = int(state["next_order"])
    state["next_order"] = order + 1
    return order


def append_log(
    state: Dict[str, Any],
    item_id: str,
    action: str,
    kind: str,
    op: str,
    path: str,
    **extra: Any,
) -> Dict[str, Any]:
    entry = {
        "order": next_order(state),
        "id": item_id,
        "action": validate_action(action),
        "kind": kind,
        "op": op,
        "path": path,
        "timestamp": utc_now(),
    }
    entry.update(extra)
    state["log"].append(entry)
    return entry


# ---------------------------------------------------------------------------
# Create, adopt, log
# ---------------------------------------------------------------------------


def ensure_folder_chain(
    root: Path,
    state: Dict[str, Any],
    rel_path: str,
    *,
    create: bool = True,
    action_override: Optional[str] = None,
    segment_override: Optional[str] = None,
) -> Tuple[str, str]:
    """Ensure a folder is indexed, assigning an ID when needed.

    Returns (folder_id, operation), where operation is "create", "adopt" or
    "existing".
    """
    rel_path = PurePosixPath(rel_path).as_posix()
    if rel_path in (".", "", "/"):
        return "/", "existing"
    if action_override is not None:
        validate_action(action_override)
    if segment_override is not None:
        segment_override = normalize_segment(segment_override)
    existing = find_active_by_path(state, rel_path, kind="folder")
    if existing:
        folder_id, folder = existing[1], existing[2]
        if segment_override is not None:
            requested = normalize_segment(segment_override)
            actual = folder.get("segment", folder_id.rsplit("/", 1)[-1])
            if requested != actual:
                raise HIndexError(
                    f"{rel_path} is already indexed as {folder_id}; "
                    "IDs are stable and cannot be reassigned"
                )
        return folder_id, "existing"
    if find_active_by_path(state, rel_path, kind="file"):
        raise HIndexError(f"a file is already indexed at {rel_path}")

    parent_rel = PurePosixPath(rel_path).parent.as_posix()
    if parent_rel == ".":
        parent_id = "/"
    else:
        parent_id, _ = ensure_folder_chain(
            root, state, parent_rel, create=create, action_override=None
        )

    abs_path = root / rel_path
    if abs_path.exists():
        if not abs_path.is_dir():
            raise HIndexError(f"not a folder: {rel_path}")
        op = "adopt"
        default_action = "Adopt folder"
    else:
        if not create:
            raise HIndexError(f"folder does not exist: {rel_path}")
        try:
            abs_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HIndexError(f"cannot create folder {rel_path}: {exc}") from exc
        op = "create"
        default_action = "Create folder"

    counter = get_counter(state, parent_id)
    segment = allocate_folder_segment(
        state, parent_id, rel_path, override=segment_override
    )
    folder_id = join_folder_id(parent_id, segment)
    attempts = 0
    while folder_id in state["folders"] or folder_id in state["files"]:
        if segment not in counter["used_segments"]:
            counter["used_segments"].append(segment)
        segment = allocate_folder_segment(state, parent_id, rel_path)
        folder_id = join_folder_id(parent_id, segment)
        attempts += 1
        if attempts > 1000:
            raise HIndexError("could not allocate a unique folder ID")
    if segment not in counter["used_segments"]:
        counter["used_segments"].append(segment)

    action = action_override if action_override is not None else default_action
    entry = append_log(state, folder_id, action, "folder", op, rel_path)
    state["folders"][folder_id] = {
        "path": rel_path,
        "parent": parent_id,
        "id_parent": parent_id,
        "segment": segment,
        "created_order": entry["order"],
        "status": STATUS_ACTIVE,
    }
    get_counter(state, folder_id)
    return folder_id, op


def ensure_file(
    root: Path,
    state: Dict[str, Any],
    rel_path: str,
    *,
    create: bool = True,
    action_override: Optional[str] = None,
) -> Tuple[str, str]:
    """Ensure a file is indexed, assigning a per-folder number when needed.

    Returns (file_id, operation), where operation is "create", "adopt" or
    "existing".
    """
    rel_path = PurePosixPath(rel_path).as_posix()
    if action_override is not None:
        validate_action(action_override)
    existing = find_active_by_path(state, rel_path, kind="file")
    if existing:
        return existing[1], "existing"
    if find_active_by_path(state, rel_path, kind="folder"):
        raise HIndexError(f"a folder is already indexed at {rel_path}")

    parent_rel = PurePosixPath(rel_path).parent.as_posix()
    if parent_rel == ".":
        folder_id = "/"
    else:
        folder_id, _ = ensure_folder_chain(root, state, parent_rel, create=create)

    abs_path = root / rel_path
    if abs_path.exists():
        if abs_path.is_dir():
            raise HIndexError(f"not a file: {rel_path}")
        op = "adopt"
        default_action = "Adopt file"
    else:
        if not create:
            raise HIndexError(f"file does not exist: {rel_path}")
        try:
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.touch(exist_ok=False)
        except FileExistsError as exc:
            raise HIndexError(f"file appeared while indexing: {rel_path}") from exc
        except OSError as exc:
            raise HIndexError(f"cannot create file {rel_path}: {exc}") from exc
        op = "create"
        default_action = "Create file"

    counter = get_counter(state, folder_id)
    number = int(counter["next_file"])
    file_id = join_file_id(folder_id, number)
    while file_id in state["files"] or file_id in state["folders"]:
        number += 1
        file_id = join_file_id(folder_id, number)
    counter["next_file"] = number + 1

    action = action_override if action_override is not None else default_action
    entry = append_log(state, file_id, action, "file", op, rel_path)
    state["files"][file_id] = {
        "path": rel_path,
        "folder": folder_id,
        "id_folder": folder_id,
        "number": number,
        "created_order": entry["order"],
        "last_order": entry["order"],
        "status": STATUS_ACTIVE,
    }
    return file_id, op


def log_action_for_path(
    root: Path, state: Dict[str, Any], rel_path: str, action: str
) -> Dict[str, Any]:
    """Append a map entry for an existing or newly adopted item."""
    rel_path = PurePosixPath(rel_path).as_posix()
    action = validate_action(action)
    existing = find_active_by_path(state, rel_path)
    if existing is None:
        abs_path = root / rel_path
        if abs_path.is_dir():
            folder_id, _ = ensure_folder_chain(root, state, rel_path, create=False)
            kind = "folder"
            item_id = folder_id
        elif abs_path.is_file():
            file_id, _ = ensure_file(root, state, rel_path, create=False)
            kind = "file"
            item_id = file_id
        else:
            raise HIndexError(
                f"cannot log {rel_path}: it is not indexed and does not exist"
            )
    else:
        kind, item_id, _entry = existing

    entry = append_log(state, item_id, action, kind, "modify", rel_path)
    if kind == "file":
        state["files"][item_id]["last_order"] = entry["order"]
    return entry


# ---------------------------------------------------------------------------
# Move and delete
# ---------------------------------------------------------------------------


def _replace_path_prefix(path: str, old_prefix: str, new_prefix: str) -> str:
    if path == old_prefix:
        return new_prefix
    if path.startswith(old_prefix + "/"):
        return new_prefix + path[len(old_prefix) :]
    return path


def _update_descendant_paths(
    state: Dict[str, Any], old_prefix: str, new_prefix: str
) -> None:
    for folder in state["folders"].values():
        if folder.get("status") != STATUS_ACTIVE:
            continue
        folder["path"] = _replace_path_prefix(
            folder.get("path", ""), old_prefix, new_prefix
        )
    for file_entry in state["files"].values():
        if file_entry.get("status") != STATUS_ACTIVE:
            continue
        file_entry["path"] = _replace_path_prefix(
            file_entry.get("path", ""), old_prefix, new_prefix
        )


def move_item(
    root: Path,
    state: Dict[str, Any],
    old_rel: str,
    new_rel: str,
    *,
    action_override: Optional[str] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    old_rel = PurePosixPath(old_rel).as_posix()
    new_rel = PurePosixPath(new_rel).as_posix()
    if action_override is not None:
        validate_action(action_override)
    if old_rel == new_rel:
        raise HIndexError("source and destination are the same")
    found = find_active_by_path(state, old_rel)
    if not found:
        raise HIndexError(f"not indexed or not active: {old_rel}")
    kind, item_id, item = found
    conflict = find_active_by_path(state, new_rel)
    if conflict and conflict[1] != item_id:
        raise HIndexError(f"destination is already indexed: {new_rel}")
    if kind == "folder" and new_rel.startswith(old_rel.rstrip("/") + "/"):
        raise HIndexError("cannot move a folder into itself")

    old_abs = root / old_rel
    new_abs = root / new_rel
    if old_abs.exists() and new_abs.exists():
        raise HIndexError(f"destination already exists: {new_rel}")
    if not old_abs.exists() and not new_abs.exists():
        raise HIndexError(
            f"neither source nor destination exists: {old_rel} / {new_rel}"
        )

    new_parent_rel = PurePosixPath(new_rel).parent.as_posix()
    if new_parent_rel == ".":
        new_parent_id = "/"
    else:
        new_parent_id, _ = ensure_folder_chain(
            root, state, new_parent_rel, create=True
        )
    if kind == "folder" and new_parent_id == item_id:
        raise HIndexError("cannot move a folder into itself")

    if old_abs.exists():
        try:
            new_abs.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_abs), str(new_abs))
        except OSError as exc:
            raise HIndexError(f"cannot move {old_rel} to {new_rel}: {exc}") from exc

    if kind == "file":
        item["path"] = new_rel
        item["folder"] = new_parent_id
    else:
        _update_descendant_paths(state, old_rel, new_rel)
        item["parent"] = new_parent_id
        new_counter = get_counter(state, new_parent_id)
        segment = item.get("segment", item_id.rsplit("/", 1)[-1])
        if segment not in new_counter["used_segments"]:
            new_counter["used_segments"].append(segment)

    action = (
        action_override
        if action_override is not None
        else ("Move file" if kind == "file" else "Move folder")
    )
    entry = append_log(
        state,
        item_id,
        action,
        kind,
        "move",
        new_rel,
        old_path=old_rel,
        new_path=new_rel,
    )
    return item_id, kind, entry


def delete_item(
    root: Path,
    state: Dict[str, Any],
    rel_path: str,
    *,
    action_override: Optional[str] = None,
) -> Tuple[str, str]:
    """Mark an item and, for folders, all current descendants as deleted.

    The filesystem is intentionally left alone; the agent removes files with
    its normal tools and then records the operation here.
    """
    del root  # Kept in the signature for symmetry and future use.
    rel_path = PurePosixPath(rel_path).as_posix()
    if action_override is not None:
        validate_action(action_override)
    found = find_active_by_path(state, rel_path)
    if not found:
        raise HIndexError(f"not indexed or not active: {rel_path}")
    kind, item_id, _item = found

    if kind == "file":
        action = (
            action_override if action_override is not None else "Delete file"
        )
        entry = append_log(state, item_id, action, "file", "delete", rel_path)
        state["files"][item_id]["status"] = STATUS_DELETED
        state["files"][item_id]["deleted_order"] = entry["order"]
        return item_id, kind

    prefix = rel_path.rstrip("/")
    action = action_override if action_override is not None else "Delete folder"
    entry = append_log(state, item_id, action, "folder", "delete", prefix)
    for folder in state["folders"].values():
        path = folder.get("path", "")
        if folder.get("status") == STATUS_ACTIVE and (
            path == prefix or path.startswith(prefix + "/")
        ):
            folder["status"] = STATUS_DELETED
            folder["deleted_order"] = entry["order"]
    for file_entry in state["files"].values():
        path = file_entry.get("path", "")
        if file_entry.get("status") == STATUS_ACTIVE and (
            path == prefix or path.startswith(prefix + "/")
        ):
            file_entry["status"] = STATUS_DELETED
            file_entry["deleted_order"] = entry["order"]
    return item_id, kind


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _active_entries(
    state: Dict[str, Any],
) -> Tuple[List[Tuple[str, Dict[str, Any]]], List[Tuple[str, Dict[str, Any]]]]:
    folders = [
        (folder_id, folder)
        for folder_id, folder in state["folders"].items()
        if folder.get("status") == STATUS_ACTIVE
    ]
    files = [
        (file_id, file_entry)
        for file_id, file_entry in state["files"].items()
        if file_entry.get("status") == STATUS_ACTIVE
    ]
    return folders, files


def verify_project(root: Path, index_path: Path, state: Dict[str, Any], text: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    expected_visible = render_visible(state).strip()
    marker_start = text.rfind(STATE_MARKER)
    if marker_start == -1:
        errors.append("state block marker is missing")
        actual_visible = text.strip()
    else:
        actual_visible = text[:marker_start].replace("\r\n", "\n").strip()
    if actual_visible != expected_visible:
        errors.append(
            "visible codes/map sections do not match the state block; "
            "rewrite the index with a helper command"
        )

    log = state["log"]
    orders = [int(entry.get("order", 0)) for entry in log]
    if orders != list(range(1, len(log) + 1)):
        errors.append("map order numbers are not contiguous starting at 1")
    if int(state.get("next_order", 0)) != len(log) + 1:
        errors.append(
            f"next_order is {state.get('next_order')}; expected {len(log) + 1}"
        )

    for folder_id in state["folders"]:
        if not FOLDER_ID_RE.fullmatch(folder_id):
            errors.append(f"invalid folder ID: {folder_id}")
    for file_id in state["files"]:
        if not FILE_ID_RE.fullmatch(file_id):
            errors.append(f"invalid file ID: {file_id}")
    if set(state["folders"]) & set(state["files"]):
        errors.append("folder and file IDs overlap")

    active_folders, active_files = _active_entries(state)
    seen_paths: Dict[str, str] = {}
    for folder_id, folder in active_folders:
        path = folder.get("path", "")
        abs_path = root / path
        if not abs_path.exists():
            errors.append(f"active folder path is missing: {path} ({folder_id})")
        elif not abs_path.is_dir():
            errors.append(f"active folder path is not a directory: {path} ({folder_id})")
        key = os.path.normcase(path.rstrip("/"))
        if key in seen_paths:
            errors.append(
                f"duplicate active path: {path} ({seen_paths[key]} and {folder_id})"
            )
        seen_paths[key] = folder_id

    for file_id, file_entry in active_files:
        path = file_entry.get("path", "")
        abs_path = root / path
        if not abs_path.exists():
            errors.append(f"active file path is missing: {path} ({file_id})")
        elif not abs_path.is_file():
            errors.append(f"active file path is not a file: {path} ({file_id})")
        key = os.path.normcase(path.rstrip("/"))
        if key in seen_paths:
            errors.append(
                f"duplicate active path: {path} ({seen_paths[key]} and {file_id})"
            )
        seen_paths[key] = file_id

    for folder_id, folder in active_folders:
        path = folder.get("path", "")
        parent_id = folder.get("parent", "/")
        parent_path = PurePosixPath(path).parent.as_posix()
        if parent_id == "/":
            if parent_path != ".":
                errors.append(
                    f"folder {folder_id} has parent / but path {path} is nested"
                )
        else:
            parent = state["folders"].get(parent_id)
            if not parent or parent.get("status") != STATUS_ACTIVE:
                errors.append(
                    f"folder {folder_id} references missing active parent {parent_id}"
                )
            elif not _folders_match(parent_path, parent.get("path", "")):
                errors.append(
                    f"folder {folder_id} path {path} does not match parent {parent_id}"
                )

    for file_id, file_entry in active_files:
        path = file_entry.get("path", "")
        folder_id = file_entry.get("folder", "/")
        parent_path = PurePosixPath(path).parent.as_posix()
        if folder_id == "/":
            if parent_path != ".":
                errors.append(
                    f"file {file_id} has folder / but path {path} is nested"
                )
        else:
            folder = state["folders"].get(folder_id)
            if not folder or folder.get("status") != STATUS_ACTIVE:
                errors.append(
                    f"file {file_id} references missing active folder {folder_id}"
                )
            elif not _folders_match(parent_path, folder.get("path", "")):
                errors.append(
                    f"file {file_id} path {path} does not match folder {folder_id}"
                )

    for folder_id, folder in state["folders"].items():
        id_parent = folder.get("id_parent", folder.get("parent", "/"))
        expected_id_parent = folder_id.rpartition("/")[0] or "/"
        if id_parent != expected_id_parent:
            errors.append(
                f"folder {folder_id} id_parent is {id_parent}; "
                f"expected {expected_id_parent} from its ID"
            )
        if id_parent != "/" and id_parent not in state["folders"]:
            errors.append(
                f"folder {folder_id} references missing allocation parent {id_parent}"
            )
        if id_parent == "/":
            counter = get_counter(state, "/")
        else:
            counter = get_counter(state, id_parent)
        segment = folder.get("segment", folder_id.rsplit("/", 1)[-1])
        expected_segment = folder_id.rsplit("/", 1)[-1]
        if segment != expected_segment:
            errors.append(
                f"folder {folder_id} segment is {segment}; "
                f"expected {expected_segment} from its ID"
            )
        if segment not in counter.get("used_segments", []):
            errors.append(
                f"folder {folder_id} segment {segment} is not reserved under {id_parent}"
            )

    for file_id, file_entry in state["files"].items():
        id_folder = file_entry.get("id_folder", file_entry.get("folder", "/"))
        expected_id_folder = file_id.rpartition("/")[0] or "/"
        if id_folder != expected_id_folder:
            errors.append(
                f"file {file_id} id_folder is {id_folder}; "
                f"expected {expected_id_folder} from its ID"
            )
        if id_folder != "/" and id_folder not in state["folders"]:
            errors.append(
                f"file {file_id} references missing allocation folder {id_folder}"
            )
        try:
            expected_number = int(file_id.rsplit("/", 1)[-1])
        except ValueError:
            expected_number = None
        try:
            actual_number = int(file_entry.get("number", 0))
        except (TypeError, ValueError):
            actual_number = None
        if actual_number is None:
            errors.append(f"file {file_id} has an invalid number")
        elif expected_number is not None and actual_number != expected_number:
            errors.append(
                f"file {file_id} number is {file_entry.get('number')}; "
                f"expected {expected_number} from its ID"
            )
        holder = state["folders"].get(id_folder)
        if holder is not None and holder.get("status") != STATUS_ACTIVE:
            # A moved file may keep a historical allocation folder; this is
            # valid as long as the counter still accounts for its number.
            pass

    for folder_id, counter in state["counters"].items():
        used = counter.get("used_segments", [])
        if len(used) != len(set(used)):
            errors.append(f"counter {folder_id} has duplicate used segments")
        next_file = int(counter.get("next_file", 1))
        max_number = 0
        for file_entry in state["files"].values():
            if file_entry.get("id_folder", file_entry.get("folder", "/")) == folder_id:
                max_number = max(max_number, int(file_entry.get("number", 0)))
        if next_file <= max_number:
            errors.append(
                f"counter {folder_id} next_file {next_file} is not beyond used number {max_number}"
            )

    for entry in log:
        item_id = entry.get("id")
        kind = entry.get("kind")
        if kind == "folder" and item_id not in state["folders"]:
            errors.append(f"map entry {entry.get('order')} references unknown folder {item_id}")
        elif kind == "file" and item_id not in state["files"]:
            errors.append(f"map entry {entry.get('order')} references unknown file {item_id}")
        elif kind not in ("folder", "file"):
            errors.append(f"map entry {entry.get('order')} has invalid kind {kind}")
        if entry.get("op") not in ("create", "adopt", "modify", "move", "delete"):
            errors.append(f"map entry {entry.get('order')} has invalid op {entry.get('op')}")
        try:
            validate_action(entry.get("action"))
        except HIndexError as exc:
            errors.append(f"map entry {entry.get('order')} has invalid action: {exc}")

    if not is_hidden(index_path):
        if os.name == "nt" or sys.platform == "darwin":
            warnings.append(f"{INDEX_FILENAME} is not hidden at the OS level; run 'hide'")
        else:
            warnings.append(
                "this platform has no per-file hidden attribute; "
                f"{INDEX_FILENAME} is only excluded from Git"
            )
    ignored = is_git_ignored(root, index_path)
    if ignored is False:
        warnings.append(f"{INDEX_FILENAME} is not excluded from Git; run 'hide'")
    return errors, warnings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _is_git_repo(root: Path) -> bool:
    proc = _git(root, "rev-parse", "--is-inside-work-tree")
    return bool(
        proc is not None
        and proc.returncode == 0
        and proc.stdout.strip().lower() == "true"
    )


def _load(args: argparse.Namespace) -> Tuple[Path, Path, Dict[str, Any]]:
    root = find_project_root(override=args.root)
    index_path, state, _text = read_index(root)
    return root, index_path, state


def _save(root: Path, state: Dict[str, Any]) -> Path:
    index_path = write_index(root, state)
    try:
        apply_git_exclude(root, index_path)
    except OSError:
        pass
    return index_path


def _hidden_label(index_path: Path) -> str:
    if is_hidden(index_path):
        return "yes"
    if os.name == "nt" or sys.platform == "darwin":
        return "no (run 'hide')"
    return "unsupported on this platform (Linux has no per-file hidden attribute)"


def _git_label(root: Path, index_path: Path) -> str:
    if not _is_git_repo(root):
        return "not a Git repository"
    ignored = is_git_ignored(root, index_path)
    if ignored is True:
        return "excluded via .git/info/exclude"
    if ignored is False:
        return "not excluded (run 'hide')"
    return "unknown"


def _sorted_codes(state: Dict[str, Any]) -> List[Dict[str, str]]:
    items: List[Tuple[int, str, Dict[str, str]]] = []
    for folder_id, folder in state["folders"].items():
        if folder.get("status") == STATUS_ACTIVE:
            items.append(
                (
                    int(folder.get("created_order", 0)),
                    folder_id,
                    {
                        "id": folder_id,
                        "path": folder_display_path(folder.get("path", "")),
                        "type": "folder",
                    },
                )
            )
    for file_id, file_entry in state["files"].items():
        if file_entry.get("status") == STATUS_ACTIVE:
            items.append(
                (
                    int(file_entry.get("created_order", 0)),
                    file_id,
                    {
                        "id": file_id,
                        "path": file_entry.get("path", ""),
                        "type": "file",
                    },
                )
            )
    items.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in items]


def _counts(state: Dict[str, Any]) -> Tuple[int, int, int]:
    active_folders, active_files = _active_entries(state)
    return len(active_folders), len(active_files), len(state["log"])


def _print_refresh(root: Path, index_path: Path) -> None:
    print(f"Hidden: {_hidden_label(index_path)}")
    print(f"Git: {_git_label(root, index_path)}")


def cmd_init(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        index_path = root / INDEX_FILENAME
        if index_path.is_file():
            read_index(root)  # Validate before reporting success.
            apply_hidden(index_path)
            try:
                apply_git_exclude(root, index_path)
            except OSError as exc:
                print(f"warning: cannot update Git exclude: {exc}", file=sys.stderr)
            print(f"{INDEX_FILENAME} already exists: {index_path}")
            _print_refresh(root, index_path)
            return 0

        state = new_state()
        index_path = write_index(root, state)
        try:
            apply_git_exclude(root, index_path)
        except OSError as exc:
            print(f"warning: cannot update Git exclude: {exc}", file=sys.stderr)
        print(f"Initialized {index_path}")
        _print_refresh(root, index_path)
        return 0


def cmd_folder(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        _index_path, state, _text = read_index(root)
        _abs_path, rel_path = normalize_rel_path(root, args.path)
        if rel_path == ".":
            raise HIndexError("cannot assign an ID to the project root itself")
        folder_id, op = ensure_folder_chain(
            root,
            state,
            rel_path,
            create=True,
            action_override=args.action,
            segment_override=args.segment,
        )
        if op == "existing" and args.action:
            entry = append_log(
                state, folder_id, args.action, "folder", "modify", rel_path
            )
            op = "logged"
            del entry
        if op != "existing":
            _save(root, state)
        print(f"{folder_id} = {folder_display_path(rel_path)}")
        return 0


def cmd_file(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        _index_path, state, _text = read_index(root)
        _abs_path, rel_path = normalize_rel_path(root, args.path)
        if rel_path == ".":
            raise HIndexError("cannot assign a file ID to the project root")
        file_id, op = ensure_file(
            root,
            state,
            rel_path,
            create=True,
            action_override=args.action,
        )
        if op == "existing" and args.action:
            entry = append_log(state, file_id, args.action, "file", "modify", rel_path)
            state["files"][file_id]["last_order"] = entry["order"]
            op = "logged"
        if op != "existing":
            _save(root, state)
        print(f"{file_id} = {rel_path}")
        return 0


def cmd_log(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        _index_path, state, _text = read_index(root)
        _abs_path, rel_path = normalize_rel_path(root, args.path)
        if rel_path == ".":
            raise HIndexError("cannot log an action for the project root")
        entry = log_action_for_path(root, state, rel_path, args.action)
        _save(root, state)
        print(f"{entry['order']}: {entry['id']}({entry['action']})")
        return 0


def cmd_move(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        _index_path, state, _text = read_index(root)
        _old_abs, old_rel = normalize_rel_path(root, args.old)
        _new_abs, new_rel = normalize_rel_path(root, args.new)
        if old_rel == "." or new_rel == ".":
            raise HIndexError("cannot move the project root itself")
        item_id, _kind, _entry = move_item(
            root,
            state,
            old_rel,
            new_rel,
            action_override=args.action,
        )
        _save(root, state)
        print(f"{item_id} = {new_rel}")
        return 0


def cmd_delete(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        _index_path, state, _text = read_index(root)
        _abs_path, rel_path = normalize_rel_path(root, args.path)
        if rel_path == ".":
            raise HIndexError("cannot delete the project root")
        item_id, kind = delete_item(
            root,
            state,
            rel_path,
            action_override=args.action,
        )
        _save(root, state)
        print(f"Deleted {item_id} = {rel_path}")
        if (root / rel_path).exists():
            print(
                "Filesystem unchanged; remove the item with normal tools.",
                file=sys.stderr,
            )
        del kind
        return 0


def cmd_status(args: argparse.Namespace) -> int:
    root, index_path, state = _load(args)
    folders, files, entries = _counts(state)
    codes = _sorted_codes(state)
    if args.json:
        payload = {
            "root": str(root),
            "index": str(index_path),
            "folders": folders,
            "files": files,
            "entries": entries,
            "hidden": is_hidden(index_path),
            "git_ignored": is_git_ignored(root, index_path),
            "codes": codes,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    print(f"root: {root}")
    print(f"index: {index_path}")
    print(f"folders: {folders}")
    print(f"files: {files}")
    print(f"map entries: {entries}")
    print(f"hidden: {_hidden_label(index_path)}")
    print(f"git: {_git_label(root, index_path)}")
    print("")
    print("codes:")
    if codes:
        for item in codes:
            print(f"{item['id']} = {item['path']}")
    else:
        print("(empty)")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root, index_path, state = _load(args)
    _text = (root / INDEX_FILENAME).read_text(encoding="utf-8")
    errors, warnings = verify_project(root, index_path, state, _text)
    folders, files, entries = _counts(state)
    if args.json:
        payload = {
            "ok": not errors,
            "root": str(root),
            "index": str(index_path),
            "folders": folders,
            "files": files,
            "entries": entries,
            "errors": errors,
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        if errors:
            print(
                f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s)",
                file=sys.stderr,
            )
        else:
            print(
                f"OK: {folders} folder(s), {files} file(s), "
                f"{entries} map entry(ies)"
            )
    return 1 if errors else 0


def cmd_repair(args: argparse.Namespace) -> int:
    """Regenerate the visible sections from the trusted state block."""
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        index_path, state, _text = read_index(root)
        _save(root, state)
        text = index_path.read_text(encoding="utf-8")
        errors, warnings = verify_project(root, index_path, state, text)
        folders, files, entries = _counts(state)
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": not errors,
                        "index": str(index_path),
                        "folders": folders,
                        "files": files,
                        "entries": entries,
                        "errors": errors,
                        "warnings": warnings,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
        else:
            print(f"Repaired visible sections: {index_path}")
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            for error in errors:
                print(f"error: {error}", file=sys.stderr)
            if errors:
                print(
                    f"State still has {len(errors)} error(s); run 'verify' after fixing.",
                    file=sys.stderr,
                )
        return 1 if errors else 0


def cmd_hide(args: argparse.Namespace) -> int:
    root = find_project_root(override=args.root)
    with ProjectLock(root):
        index_path, _state, _text = read_index(root)
        apply_hidden(index_path)
        excluded: Optional[bool] = None
        if _is_git_repo(root):
            try:
                excluded = apply_git_exclude(root, index_path)
            except OSError as exc:
                raise HIndexError(f"cannot update Git exclude: {exc}") from exc
        hidden = is_hidden(index_path)
        if args.json:
            print(
                json.dumps(
                    {
                        "index": str(index_path),
                        "hidden": hidden,
                        "hidden_supported": os.name == "nt" or sys.platform == "darwin",
                        "git_ignored": excluded,
                    },
                    indent=2,
                )
            )
        else:
            print(f"Hidden: {_hidden_label(index_path)}")
            print(f"Git: {_git_label(root, index_path)}")
        if not hidden and (os.name == "nt" or sys.platform == "darwin"):
            return 1
        return 0


def build_parser() -> argparse.ArgumentParser:
    root_parent = argparse.ArgumentParser(add_help=False)
    root_parent.add_argument(
        "--root",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )

    parser = argparse.ArgumentParser(
        prog="h_index.py",
        description=(
            "Assign stable logical IDs to project files and maintain h_index.md."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help="project root (default: nearest index or Git root)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {VERSION}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        parents=[root_parent],
        help="create h_index.md explicitly",
    )
    init_parser.set_defaults(func=cmd_init)

    folder_parser = subparsers.add_parser(
        "folder",
        parents=[root_parent],
        help="create or adopt a folder and print its ID",
    )
    folder_parser.add_argument("path", help="folder path relative to the project root")
    folder_parser.add_argument(
        "-a", "--action", help="map action for a new or already indexed folder"
    )
    folder_parser.add_argument(
        "--segment",
        help="manual 1-16 letter segment for a new folder ID",
    )
    folder_parser.set_defaults(func=cmd_folder)

    file_parser = subparsers.add_parser(
        "file",
        parents=[root_parent],
        help="create or adopt a file and print its ID",
    )
    file_parser.add_argument("path", help="file path relative to the project root")
    file_parser.add_argument(
        "-a", "--action", help="map action for a new or already indexed file"
    )
    file_parser.set_defaults(func=cmd_file)

    log_parser = subparsers.add_parser(
        "log",
        parents=[root_parent],
        help="append an action to the map section",
    )
    log_parser.add_argument("path", help="indexed item path")
    log_parser.add_argument(
        "-a", "--action", required=True, help="short English action"
    )
    log_parser.set_defaults(func=cmd_log)

    move_parser = subparsers.add_parser(
        "move",
        parents=[root_parent],
        help="move or rename an item while keeping its ID",
    )
    move_parser.add_argument("old", help="current indexed path")
    move_parser.add_argument("new", help="new path")
    move_parser.add_argument(
        "-a", "--action", help="map action (default: Move file / Move folder)"
    )
    move_parser.set_defaults(func=cmd_move)

    delete_parser = subparsers.add_parser(
        "delete",
        parents=[root_parent],
        help="mark an item deleted in the index",
    )
    delete_parser.add_argument("path", help="indexed item path")
    delete_parser.add_argument(
        "-a", "--action", help="map action (default: Delete file / Delete folder)"
    )
    delete_parser.set_defaults(func=cmd_delete)

    status_parser = subparsers.add_parser(
        "status",
        parents=[root_parent],
        help="show active codes and protection status",
    )
    status_parser.add_argument("--json", action="store_true", help="print JSON")
    status_parser.set_defaults(func=cmd_status)

    verify_parser = subparsers.add_parser(
        "verify",
        parents=[root_parent],
        help="check the index against the filesystem",
    )
    verify_parser.add_argument("--json", action="store_true", help="print JSON")
    verify_parser.set_defaults(func=cmd_verify)

    repair_parser = subparsers.add_parser(
        "repair",
        parents=[root_parent],
        help="regenerate visible sections from the state block",
    )
    repair_parser.add_argument("--json", action="store_true", help="print JSON")
    repair_parser.set_defaults(func=cmd_repair)

    hide_parser = subparsers.add_parser(
        "hide",
        parents=[root_parent],
        help="reapply the hidden attribute and Git exclusion",
    )
    hide_parser.add_argument("--json", action="store_true", help="print JSON")
    hide_parser.set_defaults(func=cmd_hide)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except HIndexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
