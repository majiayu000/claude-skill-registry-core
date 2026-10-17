"""Keep single-file install pins from omitting submodule content."""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from resolve_install_pins import match_upstream  # noqa: E402

BLOB_SHA = "a" * 40
SKILL_PATH = "skills/pdf/SKILL.md"


def entry(path, kind="blob", sha=BLOB_SHA):
    return {"path": path, "type": kind, "sha": sha}


class MatchUpstreamGitlinkTests(unittest.TestCase):
    def test_direct_and_nested_submodules_are_bundled_content(self):
        for path in ("skills/pdf/resources", "skills/pdf/vendor/resources"):
            with self.subTest(path=path):
                self.assertEqual(
                    match_upstream([entry(SKILL_PATH), entry(path, "commit")], BLOB_SHA),
                    {"status": "bundled_files"},
                )

    def test_submodules_outside_skill_directory_do_not_block_pin(self):
        for path in ("resources", "skills/resources", "skills/pdf-extra/resources"):
            with self.subTest(path=path):
                self.assertEqual(
                    match_upstream([entry(SKILL_PATH), entry(path, "commit")], BLOB_SHA),
                    {"status": "pinned", "path": SKILL_PATH},
                )

    def test_submodule_named_skill_md_is_not_a_matching_blob(self):
        self.assertEqual(
            match_upstream([entry(SKILL_PATH, "commit")], BLOB_SHA),
            {"status": "upstream_changed"},
        )

    def test_submodule_named_skill_md_does_not_make_blob_match_ambiguous(self):
        self.assertEqual(
            match_upstream([entry(SKILL_PATH), entry("other/SKILL.md", "commit")], BLOB_SHA),
            {"status": "pinned", "path": SKILL_PATH},
        )

    def test_directory_and_non_string_paths_do_not_count_as_bundled_files(self):
        entries = [entry(SKILL_PATH), entry("skills/pdf/resources", "tree")]
        entries.extend(entry(path, "commit") for path in (None, 42))
        self.assertEqual(
            match_upstream(entries, BLOB_SHA),
            {"status": "pinned", "path": SKILL_PATH},
        )

    def test_companion_blobs_still_count_as_bundled_files(self):
        self.assertEqual(
            match_upstream([entry(SKILL_PATH), entry("skills/pdf/run.py")], BLOB_SHA),
            {"status": "bundled_files"},
        )


if __name__ == "__main__":
    unittest.main()
