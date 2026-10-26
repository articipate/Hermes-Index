"""Tests for the Hermes-Index helper and CLI."""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import h_index  # noqa: E402


class HermesIndexTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hermes-index-test-")
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        index_path = self.root / h_index.INDEX_FILENAME
        h_index.clear_hidden(index_path)
        self._tmp.cleanup()

    def cli(self, *args: str) -> tuple[int, str, str]:
        return self.run_cli("--root", str(self.root), *args)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = h_index.main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_ok(self, result: tuple[int, str, str]) -> tuple[str, str]:
        code, stdout, stderr = result
        self.assertEqual(code, 0, f"command failed:\n{stderr}")
        return stdout, stderr

    def assert_fails(self, result: tuple[int, str, str], text: str) -> None:
        code, _stdout, stderr = result
        self.assertEqual(code, 1, f"command unexpectedly succeeded:\n{stderr}")
        self.assertIn(text, stderr)

    def index_text(self) -> str:
        return (self.root / h_index.INDEX_FILENAME).read_text(encoding="utf-8")

    def visible_text(self) -> str:
        text = self.index_text()
        self.assertIn(h_index.STATE_MARKER, text)
        return text.split(h_index.STATE_MARKER, 1)[0].strip()

    def state(self) -> dict:
        text = self.index_text()
        payload = text.split(h_index.STATE_MARKER, 1)[1].split("-->", 1)[0].strip()
        return json.loads(payload)

    def map_lines(self) -> list[str]:
        visible = self.visible_text()
        return visible.split("map:\n", 1)[1].splitlines()

    def code_lines(self) -> list[str]:
        visible = self.visible_text()
        block = visible.split("map:", 1)[0]
        lines = [line for line in block.splitlines()[1:] if line.strip()]
        return lines


class FormatTests(HermesIndexTestCase):
    def test_init_creates_expected_sections_and_is_idempotent(self) -> None:
        stdout, _stderr = self.assert_ok(self.cli("init"))
        self.assertIn("Initialized", stdout)
        index_path = self.root / h_index.INDEX_FILENAME
        self.assertTrue(index_path.is_file())
        if os.name == "nt" or sys.platform == "darwin":
            self.assertTrue(h_index.is_hidden(index_path))

        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/main.py"))
        before = self.index_text()

        stdout, _stderr = self.assert_ok(self.cli("init"))
        self.assertIn("already exists", stdout)
        self.assertEqual(before, self.index_text())
        self.assertTrue(self.state()["folders"]["/A"]["status"] == "active")

        self.assertEqual(
            self.visible_text(),
            "\n".join(
                [
                    "codes:",
                    "/A = src/",
                    "/A/1 = src/main.py",
                    "",
                    "map:",
                    "1: /A(Create folder)",
                    "2: /A/1(Create file)",
                ]
            ),
        )
        for line in self.map_lines():
            self.assertRegex(line, r"^\d+: /[A-Z0-9/]+\([^()\n]+\)$")

    def test_state_block_is_hidden_from_visible_sections(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        visible = self.visible_text()
        self.assertNotIn("{", visible)
        self.assertNotIn("version", visible)
        self.assertIn(h_index.STATE_MARKER, self.index_text())
        self.assertEqual(self.state()["version"], 1)

    def test_status_json_reports_codes(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/main.py"))
        stdout, _stderr = self.assert_ok(self.cli("status", "--json"))
        payload = json.loads(stdout)
        self.assertEqual(payload["folders"], 1)
        self.assertEqual(payload["files"], 1)
        self.assertEqual(payload["entries"], 2)
        self.assertEqual(
            payload["codes"],
            [
                {"id": "/A", "path": "src/", "type": "folder"},
                {"id": "/A/1", "path": "src/main.py", "type": "file"},
            ],
        )
        if os.name == "nt" or sys.platform == "darwin":
            self.assertTrue(payload["hidden"])
        else:
            self.assertFalse(payload["hidden"])

    def test_root_option_after_subcommand_is_accepted(self) -> None:
        self.assert_ok(self.cli("init"))
        code, stdout, stderr = self.run_cli(
            "status", "--root", str(self.root), "--json"
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["folders"], 0)

    def test_invalid_paths_are_rejected(self) -> None:
        self.assert_ok(self.cli("init"))
        for bad_path in ("../outside.txt", ".git/config", "h_index.md", "bad\nname.txt"):
            with self.assertRaises(h_index.HIndexError):
                h_index.normalize_rel_path(self.root, bad_path)
        self.assert_fails(self.cli("folder", "."), "project root")
        self.assert_fails(self.cli("folder", "src", "--segment", "A1"), "letters")
        self.assertFalse((self.root / "src").exists())


class AllocationTests(HermesIndexTestCase):
    def test_root_and_per_folder_file_counters(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assertEqual(self.assert_ok(self.cli("file", "README.md"))[0].strip(), "/1 = README.md")
        self.assertEqual(self.assert_ok(self.cli("file", "LICENSE"))[0].strip(), "/2 = LICENSE")

        self.assertEqual(self.assert_ok(self.cli("folder", "docs"))[0].strip(), "/A = docs/")
        self.assertEqual(self.assert_ok(self.cli("file", "docs/a.md"))[0].strip(), "/A/1 = docs/a.md")
        self.assertEqual(self.assert_ok(self.cli("file", "docs/b.md"))[0].strip(), "/A/2 = docs/b.md")

        self.assertEqual(self.assert_ok(self.cli("folder", "src"))[0].strip(), "/B = src/")
        self.assertEqual(self.assert_ok(self.cli("file", "src/app.py"))[0].strip(), "/B/1 = src/app.py")

        (self.root / "docs" / "a.md").unlink()
        self.assert_ok(self.cli("delete", "docs/a.md"))
        self.assertEqual(
            self.assert_ok(self.cli("file", "docs/c.md"))[0].strip(),
            "/A/3 = docs/c.md",
        )

        (self.root / "README.md").unlink()
        self.assert_ok(self.cli("delete", "README.md"))
        self.assertEqual(
            self.assert_ok(self.cli("file", "NOTES.md"))[0].strip(),
            "/3 = NOTES.md",
        )
        self.assert_ok(self.cli("verify"))

    def test_az_exhaustion_generates_multi_letter_ids(self) -> None:
        self.assert_ok(self.cli("init"))
        ids: list[str] = []
        for number in range(26):
            stdout, _stderr = self.assert_ok(self.cli("folder", f"dir{number:02d}"))
            ids.append(stdout.split(" = ", 1)[0])
        self.assertEqual(ids, [f"/{chr(ord('A') + i)}" for i in range(26)])

        stdout, _stderr = self.assert_ok(self.cli("folder", "dir26"))
        extra_id = stdout.split(" = ", 1)[0]
        self.assertRegex(extra_id, r"^/[A-Z]{2,}$")
        self.assertNotIn(extra_id, ids)

        stdout, _stderr = self.assert_ok(
            self.cli("folder", "manual", "--segment", "bxmmm")
        )
        self.assertEqual(stdout.strip(), "/BXMMM = manual/")
        self.assert_fails(
            self.cli("folder", "duplicate", "--segment", "BXMMM"),
            "already used",
        )
        self.assert_ok(self.cli("verify"))

    def test_manual_segment_cannot_change_an_existing_id(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assertEqual(self.assert_ok(self.cli("folder", "src"))[0].strip(), "/A = src/")
        self.assertEqual(
            self.assert_ok(self.cli("folder", "src", "--segment", "A"))[0].strip(),
            "/A = src/",
        )
        self.assert_fails(
            self.cli("folder", "src", "--segment", "B"),
            "IDs are stable",
        )
        self.assert_ok(self.cli("verify"))

    def test_deleted_folder_ids_are_not_reused(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/a.py"))
        self.assert_ok(self.cli("folder", "src/sub"))
        self.assert_ok(self.cli("file", "src/sub/b.py"))
        shutil.rmtree(self.root / "src")
        self.assert_ok(self.cli("delete", "src"))

        codes = self.code_lines()
        self.assertEqual(codes, [])
        state = self.state()
        self.assertEqual(state["folders"]["/A"]["status"], "deleted")
        self.assertEqual(state["folders"]["/A/A"]["status"], "deleted")
        self.assertEqual(state["files"]["/A/1"]["status"], "deleted")
        self.assertEqual(state["files"]["/A/A/1"]["status"], "deleted")

        stdout, _stderr = self.assert_ok(self.cli("folder", "src"))
        self.assertEqual(stdout.strip(), "/B = src/")
        stdout, _stderr = self.assert_ok(self.cli("file", "src/new.py"))
        self.assertEqual(stdout.strip(), "/B/1 = src/new.py")
        self.assert_ok(self.cli("verify"))

        map_text = "\n".join(self.map_lines())
        self.assertIn("/A(Delete folder)", map_text)
        self.assertIn("/A(Create folder)", map_text)


class AdoptionAndMoveTests(HermesIndexTestCase):
    def test_lazy_adoption_preserves_existing_content(self) -> None:
        (self.root / "app").mkdir()
        source = self.root / "app" / "main.py"
        source.write_text("print('hello')\n", encoding="utf-8")
        (self.root / "assets").mkdir()

        self.assert_ok(self.cli("init"))
        self.assertEqual(
            self.assert_ok(self.cli("file", "app/main.py"))[0].strip(),
            "/A/1 = app/main.py",
        )
        self.assertEqual(source.read_text(encoding="utf-8"), "print('hello')\n")
        self.assertEqual(
            self.assert_ok(self.cli("folder", "assets"))[0].strip(),
            "/B = assets/",
        )
        self.assertEqual(
            self.map_lines()[:3],
            [
                "1: /A(Adopt folder)",
                "2: /A/1(Adopt file)",
                "3: /B(Adopt folder)",
            ],
        )
        self.assert_ok(self.cli("verify"))

    def test_move_keeps_ids_and_relinks_external_renames(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/a.py"))
        self.assert_ok(self.cli("file", "src/b.py"))
        (self.root / "src" / "b.py").write_text("payload\n", encoding="utf-8")

        stdout, _stderr = self.assert_ok(self.cli("move", "src/a.py", "src/renamed.py"))
        self.assertEqual(stdout.strip(), "/A/1 = src/renamed.py")
        self.assertFalse((self.root / "src" / "a.py").exists())
        self.assertTrue((self.root / "src" / "renamed.py").exists())

        stdout, _stderr = self.assert_ok(self.cli("move", "src", "lib"))
        self.assertEqual(stdout.strip(), "/A = lib")
        self.assertEqual(
            self.code_lines(),
            ["/A = lib/", "/A/1 = lib/renamed.py", "/A/2 = lib/b.py"],
        )

        (self.root / "lib" / "b.py").rename(self.root / "lib" / "c.py")
        stdout, _stderr = self.assert_ok(self.cli("move", "lib/b.py", "lib/c.py"))
        self.assertEqual(stdout.strip(), "/A/2 = lib/c.py")
        self.assertEqual(
            (self.root / "lib" / "c.py").read_text(encoding="utf-8"), "payload\n"
        )

        stdout, _stderr = self.assert_ok(self.cli("move", "lib/c.py", "docs/c.py"))
        self.assertEqual(stdout.strip(), "/A/2 = docs/c.py")
        self.assertEqual(
            self.assert_ok(self.cli("file", "docs/new.md"))[0].strip(),
            "/B/1 = docs/new.md",
        )
        self.assert_ok(self.cli("verify"))

        self.assert_fails(
            self.cli("move", "docs/new.md", "docs/c.py"),
            "destination",
        )

    def test_move_destination_must_not_exist(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("file", "one.txt"))
        self.assert_ok(self.cli("file", "two.txt"))
        self.assert_fails(self.cli("move", "one.txt", "two.txt"), "destination")

    def test_logging_actions_and_validation(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/main.py"))
        stdout, _stderr = self.assert_ok(
            self.cli("log", "src/main.py", "--action", "Write the parser")
        )
        self.assertEqual(stdout.strip(), "3: /A/1(Write the parser)")

        self.assert_fails(
            self.cli("log", "src/main.py", "--action", "Fix (parser)"),
            "parentheses",
        )
        self.assert_fails(
            self.cli("log", "src/main.py", "--action", "First line\nSecond line"),
            "single line",
        )
        self.assert_fails(
            self.cli("log", "src/main.py", "--action", "x" * 81),
            "too long",
        )
        self.assertNotIn("Fix (parser)", "\n".join(self.map_lines()))

        self.assert_fails(
            self.cli("file", "src/bad.py", "--action", "Fix (parser)"),
            "parentheses",
        )
        self.assertFalse((self.root / "src" / "bad.py").exists())

        (self.root / "src" / "extra.py").write_text("", encoding="utf-8")
        stdout, _stderr = self.assert_ok(
            self.cli("log", "src/extra.py", "--action", "Adopt extra module")
        )
        self.assertEqual(stdout.strip(), "5: /A/2(Adopt extra module)")
        self.assertIn("4: /A/2(Adopt file)", "\n".join(self.map_lines()))

        stdout, _stderr = self.assert_ok(
            self.cli("log", "src/main.py", "--action", "Handle --> marker")
        )
        self.assertEqual(stdout.strip(), "6: /A/1(Handle --> marker)")
        self.assertIn("\\u003e", self.index_text())
        self.assert_ok(self.cli("verify"))


class ProtectionTests(HermesIndexTestCase):
    def test_git_exclusion_uses_local_exclude_only(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git is not installed")
        subprocess.run(
            ["git", "init", "-q", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("hide"))

        index_path = self.root / h_index.INDEX_FILENAME
        ignored = subprocess.run(
            ["git", "-C", str(self.root), "check-ignore", "-q", "--", h_index.INDEX_FILENAME],
            check=False,
        )
        self.assertEqual(ignored.returncode, 0)
        self.assertFalse((self.root / ".gitignore").exists())

        exclude_path = self.root / ".git" / "info" / "exclude"
        exclude_text = exclude_path.read_text(encoding="utf-8")
        self.assertEqual(exclude_text.count("/h_index.md"), 1)
        self.assertIn("/.h_index.lock", exclude_text)

        self.assert_ok(self.cli("hide"))
        exclude_text = exclude_path.read_text(encoding="utf-8")
        self.assertEqual(exclude_text.count("/h_index.md"), 1)

        status = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn(h_index.INDEX_FILENAME, status.stdout)

        if os.name == "nt" or sys.platform == "darwin":
            self.assertTrue(h_index.is_hidden(index_path))
            verify_code, _stdout, _stderr = self.cli("verify")
            self.assertEqual(verify_code, 0)
        else:
            verify_code, _stdout, stderr = self.cli("verify")
            self.assertEqual(verify_code, 0)
            self.assertIn("no per-file hidden attribute", stderr)

        nested = self.root / "nested"
        nested.mkdir()
        self.assertEqual(h_index.find_project_root(start=nested), self.root.resolve())


class VerificationTests(HermesIndexTestCase):
    def test_verify_detects_visible_tampering_and_missing_files(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/main.py"))

        text = self.index_text()
        tampered = text.replace("/A = src/", "/A = other/", 1)
        self.assertNotEqual(text, tampered)
        index_path = self.root / h_index.INDEX_FILENAME
        h_index.clear_hidden(index_path)
        index_path.write_text(tampered, encoding="utf-8")
        self.assert_fails(self.cli("verify"), "visible codes/map")

        stdout, _stderr = self.assert_ok(self.cli("repair"))
        self.assertIn("Repaired visible sections", stdout)
        self.assertIn("/A = src/", self.index_text())
        self.assert_ok(self.cli("verify"))

        (self.root / "src" / "main.py").unlink()
        self.assert_fails(self.cli("verify"), "active file path is missing")
        (self.root / "src" / "main.py").touch()
        self.assert_ok(self.cli("verify"))

    def test_missing_state_block_is_rejected(self) -> None:
        (self.root / h_index.INDEX_FILENAME).write_text(
            "codes:\n\nmap:\n", encoding="utf-8"
        )
        self.assert_fails(self.cli("status"), "missing state block")
        self.assert_fails(self.cli("verify"), "missing state block")

    def test_log_action_must_reference_an_item(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_fails(
            self.cli("log", "missing.txt", "--action", "Write the file"),
            "does not exist",
        )

    def test_malformed_state_is_rejected_cleanly(self) -> None:
        self.assert_ok(self.cli("init"))
        self.assert_ok(self.cli("folder", "src"))
        self.assert_ok(self.cli("file", "src/main.py"))
        base = self.state()

        def changed(mutator) -> dict:
            state = copy.deepcopy(base)
            mutator(state)
            return state

        cases = [
            ("version", changed(lambda s: s.update(version=True))),
            ("next_order", changed(lambda s: s.update(next_order="2"))),
            (
                "status",
                changed(lambda s: s["folders"]["/A"].update(status="gone")),
            ),
            (
                "segment",
                changed(lambda s: s["folders"]["/A"].update(segment=7)),
            ),
            (
                "number",
                changed(lambda s: s["files"]["/A/1"].update(number=0)),
            ),
            (
                "next_file",
                changed(lambda s: s["counters"]["/A"].update(next_file=0)),
            ),
            (
                "action",
                changed(lambda s: s["log"][0].update(action=7)),
            ),
            (
                "op",
                changed(lambda s: s["log"][0].update(op="rename")),
            ),
        ]
        for label, state in cases:
            with self.assertRaises(h_index.HIndexError, msg=label):
                h_index.validate_state(state)

        index_path = self.root / h_index.INDEX_FILENAME
        h_index.clear_hidden(index_path)
        h_index.write_index(self.root, changed(lambda s: s.update(next_order="2")))
        self.assert_fails(self.cli("status"), "next_order")


if __name__ == "__main__":
    unittest.main()
