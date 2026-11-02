"""Regression coverage for pointer path validation and sync mode selection."""

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from write_legacy_skill_pointers import write_pointers  # noqa: E402

SHA = "a21980a5b576646393ed574f158d7dd92fae4c3f"


class LegacyPointerPrefixTests(unittest.TestCase):
    def test_exact_and_full_path_case_duplicates_still_fail(self):
        for duplicate in ("skills/design/example/SKILL.md", "skills/DESIGN/EXAMPLE/SKILL.md"):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                paths_file = root / "paths.txt"
                paths_file.write_text("skills/design/example/SKILL.md\n" + duplicate + "\n", encoding="utf-8")
                output = root / "output"
                with self.assertRaisesRegex(ValueError, "Duplicate"):
                    write_pointers(paths_file, SHA, output, check=False)
                self.assertFalse(output.exists())

    def test_case_colliding_directory_prefixes_fail_before_writing(self):
        for paths in (
            ["skills/Design/a/SKILL.md", "skills/design/b/SKILL.md"],
            ["skills/design/Group/a/SKILL.md", "skills/design/group/b/SKILL.md"],
            ["skills/design/Straße/a/SKILL.md", "skills/design/STRASSE/b/SKILL.md"],
        ):
            for entries in (paths, list(reversed(paths))):
                with self.subTest(paths=entries), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    paths_file = root / "paths.txt"
                    paths_file.write_text("\n".join(entries) + "\n", encoding="utf-8")
                    output = root / "output"
                    with self.assertRaisesRegex(ValueError, "case-colliding"):
                        write_pointers(paths_file, SHA, output, check=False)
                    self.assertFalse(output.exists())

    def test_identical_prefixes_and_distinct_parents_are_allowed(self):
        paths = [
            "skills/design/Group/a/SKILL.md",
            "skills/design/Group/b/SKILL.md",
            "skills/development/group/c/SKILL.md",
        ]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths_file = root / "paths.txt"
            paths_file.write_text("\n".join(paths) + "\n", encoding="utf-8")
            output = root / "output"
            self.assertEqual(write_pointers(paths_file, SHA, output, check=False), len(paths))
            before = {path: (output / path).read_bytes() for path in paths}
            self.assertEqual(write_pointers(paths_file, SHA, output, check=True), len(paths))
            self.assertEqual(before, {path: (output / path).read_bytes() for path in paths})


class LegacyPointerSyncTests(unittest.TestCase):
    def run_sync(self, root, extra_args, *, real_rsync=False, pointers=True):
        core, data, main = (root / name for name in ("core source", "data source", "main target"))
        for directory in (core, data, main):
            directory.mkdir()
        pointer = main / "skills/design/example/SKILL.md"
        readme = main / "skills/README.md"
        if pointers:
            pointer.parent.mkdir(parents=True)
            pointer.write_text("# Frozen legacy pointer\n", encoding="utf-8")
            readme.write_text("# Frozen pointer set\n", encoding="utf-8")
        archived = data / "design/example/SKILL.md"
        archived.parent.mkdir(parents=True)
        archived.write_text("---\nname: example\n---\nArchived body\n", encoding="utf-8")
        wrapper = '''python() { printf 'TRACE_PYTHON_CWD %s\\n' "$PWD"; printf 'TRACE_PYTHON'; printf ' %q' "$@"; printf '\\n'; }
'''
        if not real_rsync:
            wrapper += '''rsync() { printf 'TRACE_RSYNC'; printf ' %q' "$@"; printf '\\n'; }
'''
        wrapper += 'source "$@"'
        result = subprocess.run(
            ["bash", "-c", wrapper, "sync-fixture", str(ROOT / "scripts/sync_main_repo.sh"),
             "--core", str(core), "--data", str(data), "--main", str(main),
             "--no-rebuild", *extra_args],
            capture_output=True, text=True, check=False, env=os.environ.copy(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        working_directories = [line.removeprefix("TRACE_PYTHON_CWD ")
                               for line in result.stdout.splitlines()
                               if line.startswith("TRACE_PYTHON_CWD ")]
        self.assertTrue(working_directories)
        self.assertEqual({Path(path).resolve() for path in working_directories}, {main.resolve()})
        return result, data, main, pointer, readme, archived

    def test_default_and_explicit_no_mirror_read_data_without_mirroring(self):
        for args in ([], ["--no-skills-mirror"], ["--skills-mirror", "--no-skills-mirror"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temporary:
                result, data, main, *_ = self.run_sync(Path(temporary), args)
                commands = [shlex.split(line) for line in result.stdout.splitlines()
                            if line.startswith("TRACE_RSYNC")]
                self.assertEqual(len(commands), 1)
                self.assertNotIn(str(main / "skills") + "/", commands[0])
                notices = next(shlex.split(line) for line in result.stdout.splitlines()
                               if line.startswith("TRACE_PYTHON") and "check_metadata_compliance.py" in line)
                self.assertEqual(notices[notices.index("--skills-dir") + 1], str(data))

    def test_no_mirror_does_not_create_absent_skills_tree(self):
        for args in ([], ["--no-skills-mirror"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temporary:
                _, _, main, *_ = self.run_sync(Path(temporary), args, pointers=False)
                self.assertFalse((main / "skills").exists())

    def test_archive_mirroring_requires_explicit_opt_in(self):
        for args in (["--skills-mirror"], ["--no-skills-mirror", "--skills-mirror"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temporary:
                result, data, main, *_ = self.run_sync(Path(temporary), args)
                commands = [shlex.split(line) for line in result.stdout.splitlines()
                            if line.startswith("TRACE_RSYNC")]
                self.assertEqual(len(commands), 2)
                self.assertIn("--delete", commands[-1])
                self.assertEqual(commands[-1][-2:], [str(data) + "/", str(main / "skills") + "/"])
                notices = next(shlex.split(line) for line in result.stdout.splitlines()
                               if line.startswith("TRACE_PYTHON") and "check_metadata_compliance.py" in line)
                self.assertEqual(notices[notices.index("--skills-dir") + 1], str(main / "skills"))

    @unittest.skipUnless(shutil.which("rsync"), "rsync is required for file-copy integration")
    def test_real_sync_preserves_pointers_unless_mirror_is_explicit(self):
        for args in ([], ["--no-skills-mirror"], ["--skills-mirror"]):
            with self.subTest(args=args), tempfile.TemporaryDirectory() as temporary:
                _, _, _, pointer, readme, archived = self.run_sync(Path(temporary), args, real_rsync=True)
                self.assertEqual(archived.read_text(), "---\nname: example\n---\nArchived body\n")
                if args == ["--skills-mirror"]:
                    self.assertEqual(pointer.read_bytes(), archived.read_bytes())
                    self.assertFalse(readme.exists())
                else:
                    self.assertEqual(pointer.read_text(), "# Frozen legacy pointer\n")
                    self.assertEqual(readme.read_text(), "# Frozen pointer set\n")


if __name__ == "__main__":
    unittest.main()
