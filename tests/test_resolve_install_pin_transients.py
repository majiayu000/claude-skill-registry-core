"""Regression coverage for cached install pins during temporary API failures."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from resolve_install_pins import git_blob_sha, resolve_pins, sha256_digest  # noqa: E402
from verify_upstream_assets import GitHubApiError  # noqa: E402

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
SKILL = b"---\nname: pdf-tools\ndescription: Extract text from PDF files.\n---\n# PDF tools\n"
COMMIT = "a" * 40
ARCHIVE_PATH = "data/pdf-tools/SKILL.md"
UPSTREAM_PATH = "skills/pdf/SKILL.md"
TRANSIENT_STATUSES = (0, 500, 502, 503, 504, 599)


class FailingClient:
    def __init__(self, stage, status, message="temporary failure"):
        self.stage = stage
        self.status = status
        self.message = message
        self.calls = []

    def _request(self, stage, repo):
        self.calls.append((stage, repo))
        if repo == "acme/skills" and stage == self.stage:
            raise GitHubApiError(self.status, self.message)

    def branch_sha(self, repo, branch):
        self._request("branch", repo)
        return COMMIT

    def get_json(self, path):
        repo = path.split("/repos/", 1)[1].split("/git/", 1)[0]
        self._request("tree", repo)
        return {"truncated": False, "tree": [
            {"path": UPSTREAM_PATH, "type": "blob", "sha": git_blob_sha(SKILL)},
        ]}


class TransientInstallPinTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.archive_dir = Path(temporary.name)
        skill_file = self.archive_dir / ARCHIVE_PATH
        skill_file.parent.mkdir(parents=True)
        skill_file.write_bytes(SKILL)
        self.paths = {"id-1": ARCHIVE_PATH}
        self.records = [{"id": "id-1", "repo": "acme/skills", "branch": "main"}]
        self.previous = {
            "status": "pinned", "archive_path": ARCHIVE_PATH,
            "repo": "acme/skills", "branch": "main", "path": UPSTREAM_PATH,
            "digest": sha256_digest(SKILL), "commit": "b" * 40,
            "name": "pdf-tools", "description": "Extract text from PDF files.",
            "checked_at": (NOW - timedelta(days=8)).isoformat(),
        }

    def resolve(self, existing, client, now=NOW):
        return resolve_pins(
            self.records, self.paths, self.archive_dir, existing,
            client, budget=10, now=now,
        )

    def test_transient_branch_and_tree_errors_preserve_valid_pin_and_continue(self):
        self.paths["id-2"] = ARCHIVE_PATH
        self.records.append({"id": "id-2", "repo": "zzz/skills", "branch": "main"})
        for stage in ("branch", "tree"):
            for status in TRANSIENT_STATUSES:
                with self.subTest(stage=stage, status=status):
                    previous = dict(self.previous)
                    client = FailingClient(stage, status)
                    pins, stats = self.resolve({"id-1": previous}, client)

                    self.assertEqual(pins["id-1"], self.previous)
                    self.assertEqual(previous, self.previous)
                    self.assertEqual(pins["id-2"]["status"], "pinned")
                    self.assertEqual(stats, {
                        "api_calls": 3 if stage == "branch" else 4,
                        "reused": 0, "deferred": 1, "unavailable": 0,
                    })
                    self.assertIn(("tree", "zzz/skills"), client.calls)

    def test_transient_errors_do_not_restore_mismatched_or_absent_pins(self):
        candidates = {
            "no_pin": {},
            "changed_digest": {"id-1": dict(self.previous, digest=sha256_digest(b"old"))},
            "changed_archive_path": {"id-1": dict(self.previous, archive_path="old/SKILL.md")},
            "missing_digest": {"id-1": {key: value for key, value in self.previous.items()
                                        if key != "digest"}},
        }
        for stage in ("branch", "tree"):
            for status in TRANSIENT_STATUSES:
                for name, existing in candidates.items():
                    with self.subTest(stage=stage, status=status, cached=name):
                        pins, stats = self.resolve(existing, FailingClient(stage, status))

                        self.assertEqual(pins, {})
                        self.assertEqual(stats["deferred"], 1)
                        self.assertEqual(stats["reused"], 0)

    def test_preserved_pin_is_retried_on_the_next_run(self):
        for stage in ("branch", "tree"):
            for status in TRANSIENT_STATUSES:
                with self.subTest(stage=stage, status=status):
                    pins, _ = self.resolve({"id-1": dict(self.previous)}, FailingClient(stage, status))
                    retry = FailingClient(None, 0)
                    retry_time = NOW + timedelta(minutes=1)
                    refreshed, stats = self.resolve(pins, retry, now=retry_time)

                    self.assertEqual(refreshed["id-1"]["commit"], COMMIT)
                    self.assertEqual(refreshed["id-1"]["checked_at"], retry_time.isoformat())
                    self.assertEqual(stats["api_calls"], 2)
                    self.assertEqual(stats["reused"], 0)

    def test_terminal_404_still_replaces_valid_cached_pin(self):
        for stage in ("branch", "tree"):
            with self.subTest(stage=stage):
                pins, stats = self.resolve(
                    {"id-1": dict(self.previous)}, FailingClient(stage, 404, "Not Found"),
                )

                self.assertEqual(pins["id-1"], {
                    "status": "upstream_unavailable", "error": "Not Found",
                    "archive_path": ARCHIVE_PATH, "digest": sha256_digest(SKILL),
                    "checked_at": NOW.isoformat(),
                })
                self.assertEqual(stats["deferred"], 0)

    def test_rate_limits_still_defer_remaining_repositories(self):
        self.paths["id-2"] = ARCHIVE_PATH
        self.records.append({"id": "id-2", "repo": "zzz/skills", "branch": "main"})
        for stage in ("branch", "tree"):
            for status in (403, 429):
                with self.subTest(stage=stage, status=status):
                    client = FailingClient(stage, status, "API rate limit exceeded")
                    pins, stats = self.resolve({"id-1": dict(self.previous)}, client)

                    self.assertEqual(pins, {"id-1": self.previous})
                    self.assertEqual(stats["deferred"], 2)
                    self.assertNotIn(("branch", "zzz/skills"), client.calls)
