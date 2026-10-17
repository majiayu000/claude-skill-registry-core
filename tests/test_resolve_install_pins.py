import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from resolve_install_pins import (  # noqa: E402
    frontmatter_identity,
    git_blob_sha,
    load_archive_paths,
    match_upstream,
    resolve_pins,
)
from verify_upstream_assets import GitHubApiError  # noqa: E402

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
SKILL = b"---\nname: pdf-tools\ndescription: Extract text and tables from PDF files.\n---\n# PDF tools\n"
COMMIT = "a" * 40


def blob(path, data=SKILL):
    return {"path": path, "type": "blob", "sha": git_blob_sha(data)}


class FakeClient:
    def __init__(self, trees, fail=()):
        self.trees, self.fail, self.calls = trees, set(fail), []

    def branch_sha(self, repo, branch):
        self.calls.append(("branch", repo))
        if repo == "limited/repo":
            raise GitHubApiError(403, "GitHub API 403: API rate limit exceeded")
        if repo in self.fail:
            raise GitHubApiError(404, "GitHub API 404: Not Found")
        return COMMIT

    def get_json(self, path):
        self.calls.append(("tree", path))
        repo = path.split("/repos/", 1)[1].split("/git/", 1)[0]
        return self.trees[repo]


def archive(tmp_path, data=SKILL, rel="data/pdf-tools/SKILL.md"):
    path = tmp_path / "archive" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return tmp_path / "archive", {"id-1": rel}


def test_git_blob_sha_matches_git():
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


@pytest.mark.parametrize(
    "entries,expected",
    [
        ([blob("skills/pdf/SKILL.md")], {"status": "pinned", "path": "skills/pdf/SKILL.md"}),
        ([blob("skills/pdf/SKILL.md"), blob("skills/pdf/run.py", b"x")], {"status": "bundled_files"}),
        ([blob("SKILL.md")], {"status": "repo_root"}),
        ([blob("a/SKILL.md"), blob("b/SKILL.md")], {"status": "ambiguous"}),
        ([blob("skills/pdf/SKILL.md", b"changed")], {"status": "upstream_changed"}),
        ([blob("skills/pdf/SKILL.md"), blob("skills/pdf-extra/run.py", b"x")], {"status": "pinned", "path": "skills/pdf/SKILL.md"}),
    ],
)
def test_match_upstream_requires_identical_single_file_skill(entries, expected):
    assert match_upstream(entries, git_blob_sha(SKILL)) == expected


@pytest.mark.parametrize(
    "data,expected",
    [
        (SKILL, ("pdf-tools", "Extract text and tables from PDF files.")),
        (b"---\nname: PDF Tools\ndescription: x\n---\n", None),
        (b"---\nname: pdf\n---\n", None),
        (b"---\nname: pdf\ndescription: " + b"x" * 1025 + b"\n---\n", None),
        (b"# no frontmatter\n", None),
    ],
)
def test_frontmatter_identity_enforces_discovery_name_and_description(data, expected):
    assert frontmatter_identity(data) == expected


def test_resolve_pins_records_commit_path_and_archive_digest(tmp_path):
    archive_dir, paths = archive(tmp_path)
    client = FakeClient({"acme/skills": {"truncated": False, "tree": [blob("skills/pdf/SKILL.md")]}})
    records = [{"id": "id-1", "repo": "acme/skills", "branch": "main"}]

    pins, stats = resolve_pins(records, paths, archive_dir, {}, client, budget=10, now=NOW)

    assert pins["id-1"] == {
        "status": "pinned", "path": "skills/pdf/SKILL.md", "archive_path": "data/pdf-tools/SKILL.md",
        "repo": "acme/skills", "branch": "main", "digest": "sha256:" + hashlib.sha256(SKILL).hexdigest(),
        "checked_at": NOW.isoformat(), "commit": COMMIT, "name": "pdf-tools",
        "description": "Extract text and tables from PDF files.",
    }
    assert stats["api_calls"] == 2


def test_fresh_pins_are_reused_without_api_calls(tmp_path):
    archive_dir, paths = archive(tmp_path)
    digest = "sha256:" + hashlib.sha256(SKILL).hexdigest()
    existing = {"id-1": {"status": "pinned", "archive_path": "data/pdf-tools/SKILL.md", "digest": digest,
                         "checked_at": "2026-10-15T00:00:00+00:00"}}
    client = FakeClient({})

    pins, stats = resolve_pins([{"id": "id-1", "repo": "acme/skills"}], paths, archive_dir, existing,
                               client, budget=10, now=NOW)

    assert pins == existing and stats["reused"] == 1 and client.calls == []


def test_budget_defers_and_drops_pins_that_no_longer_match_the_archive(tmp_path):
    archive_dir, paths = archive(tmp_path)
    old = {"status": "pinned", "archive_path": "data/pdf-tools/SKILL.md", "checked_at": "2026-01-01T00:00:00+00:00"}
    same = dict(old, digest="sha256:" + hashlib.sha256(SKILL).hexdigest())
    changed = dict(old, digest="sha256:" + "0" * 64)
    records = [{"id": "id-1", "repo": "acme/skills"}]

    kept, stats = resolve_pins(records, paths, archive_dir, {"id-1": same}, FakeClient({}), budget=1, now=NOW)
    dropped, _ = resolve_pins(records, paths, archive_dir, {"id-1": changed}, FakeClient({}), budget=1, now=NOW)

    assert kept == {"id-1": same} and stats["deferred"] == 1
    assert dropped == {}


def test_unavailable_upstream_and_truncated_trees_are_not_pinned(tmp_path):
    archive_dir, paths = archive(tmp_path)
    paths["id-2"] = paths["id-1"]
    records = [{"id": "id-1", "repo": "gone/repo"}, {"id": "id-2", "repo": "huge/repo"}]
    client = FakeClient({"huge/repo": {"truncated": True, "tree": []}}, fail={"gone/repo"})

    pins, _ = resolve_pins(records, paths, archive_dir, {}, client, budget=10, now=NOW)

    assert pins["id-1"]["status"] == "upstream_unavailable"
    assert pins["id-2"]["status"] == "tree_truncated"


def test_missing_archive_file_is_counted_not_pinned(tmp_path):
    pins, stats = resolve_pins([{"id": "id-1", "repo": "acme/skills"}], {"id-1": "data/missing/SKILL.md"},
                               tmp_path, {}, FakeClient({}), budget=10, now=NOW)
    assert pins == {} and stats["unavailable"] == 1


def test_load_archive_paths_reads_detail_shards(tmp_path):
    shards = tmp_path / "skill-detail-shards"
    shards.mkdir()
    (shards / "a.json").write_text(json.dumps({"skills": [{"id": "a1", "archive_path": "x/a/SKILL.md"}, {"id": "a2"}]}))
    assert load_archive_paths(tmp_path) == {"a1": "x/a/SKILL.md"}


def test_rate_limit_defers_remaining_repositories_instead_of_marking_them_unavailable(tmp_path):
    archive_dir, paths = archive(tmp_path)
    paths["id-2"] = paths["id-1"]
    records = [{"id": "id-1", "repo": "limited/repo"}, {"id": "id-2", "repo": "zzz/repo"}]
    client = FakeClient({"zzz/repo": {"truncated": False, "tree": [blob("skills/pdf/SKILL.md")]}})

    pins, stats = resolve_pins(records, paths, archive_dir, {}, client, budget=10, now=NOW)

    assert pins == {} and stats["deferred"] == 2
    assert ("branch", "zzz/repo") not in client.calls
