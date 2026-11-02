"""Count archived content changes across current and legacy history entries."""

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import skill_content_history as sch  # noqa: E402
from resolve_install_pins import sha256_digest  # noqa: E402

NOW = "2026-10-21T00:00:00+00:00"


def entry(**values):
    return {"status": "bundled_files", "first_seen": NOW, "last_seen": NOW, **values}


def count(*entries):
    return sch.summarize({"guide": list(entries)}, {"guide": {}}, NOW)["content_rescanned"]


class ContentRescannedCountTests(unittest.TestCase):
    def test_bundled_file_only_change_is_counted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            skill = root / "data" / "pdf-tools"
            skill.mkdir(parents=True)
            data = b"---\nname: pdf-tools\ndescription: PDF tools\n---\n"
            (skill / "SKILL.md").write_bytes(data)
            script = skill / "run.sh"
            script.write_bytes(b"echo before\n")
            pins = {"guide": {"status": "bundled_files", "repo": "acme/skills",
                              "archive_path": "data/pdf-tools/SKILL.md",
                              "digest": sha256_digest(data)}}
            history = {}
            sch.update_history(history, pins, root, "2026-10-20T00:00:00+00:00")
            self.assertEqual(sch.summarize(history, pins, NOW)["content_rescanned"], 0)

            script.write_bytes(b"echo after\n")
            sch.update_history(history, pins, root, NOW)

            self.assertEqual(len(history["guide"]), 2)
            first, second = history["guide"]
            self.assertEqual(first["digest"], second["digest"])
            self.assertNotEqual(first["tree_digest"], second["tree_digest"])
            self.assertEqual(sch.summarize(history, pins, NOW)["content_rescanned"], 1)

    def test_legacy_skill_digest_changes_are_counted(self):
        self.assertEqual(count(entry(digest="a"), entry(digest="b")), 1)

    def test_one_tree_observation_does_not_hide_legacy_digest_changes(self):
        self.assertEqual(count(entry(digest="a"), entry(digest="b"),
                               entry(digest="b", tree_digest="tree-b")), 1)

    def test_tree_hash_backfill_and_status_changes_are_not_content_changes(self):
        self.assertEqual(count(entry(digest="a"),
                               entry(digest="a", tree_digest="tree-a", status="pinned")), 0)
        self.assertEqual(count(entry(digest="a", tree_digest="tree-a"),
                               entry(digest="a", tree_digest="tree-a", status="pinned")), 0)

    def test_missing_hashes_are_not_content_versions(self):
        for key in ("digest", "tree_digest"):
            for missing in ({}, {key: None}, {key: ""}):
                with self.subTest(key=key, missing=missing):
                    self.assertEqual(count(entry(**{key: "a"}), entry(**missing)), 0)
                    self.assertEqual(count(entry(**{key: "a"}), entry(**missing),
                                           entry(**{key: "b"})), 1)

    def test_multiple_changes_and_reversions_count_each_guide_once(self):
        self.assertEqual(count(entry(digest="a", tree_digest="tree-a"),
                               entry(digest="b", tree_digest="tree-b"),
                               entry(digest="a", tree_digest="tree-a")), 1)

    def test_dropped_guides_are_not_counted(self):
        history = {"current": [entry(digest="a")],
                   "dropped": [entry(digest="a", tree_digest="tree-a"),
                               entry(digest="b", tree_digest="tree-b")]}
        self.assertEqual(sch.summarize(history, {"current": {}}, NOW)["content_rescanned"], 0)

    def test_empty_history_has_no_changed_guides(self):
        self.assertEqual(sch.summarize({}, {}, NOW)["content_rescanned"], 0)


if __name__ == "__main__":
    unittest.main()
