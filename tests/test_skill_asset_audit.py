import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import audit_skill_assets
from skill_asset_audit import (
    classify_files,
    classify_skill_text,
    iter_archived_skills,
    verdict_from_counts,
)
from verify_upstream_assets import resolve_skill_dir


class TestClassifySkillText:
    def test_exec_reference(self):
        assert classify_skill_text("Run scripts/setup.py before use.") == "EXEC"

    def test_relative_exec_reference(self):
        assert classify_skill_text("Execute ./tools/build.sh to compile.") == "EXEC"

    def test_doc_reference_only(self):
        assert classify_skill_text("See references/guide.md for details.") == "REF"

    def test_bare(self):
        assert classify_skill_text("Just follow these markdown steps.") == "BARE"

    def test_url_does_not_count_as_exec(self):
        text = "Docs at https://example.com/raw/setup.py explain more."
        assert classify_skill_text(text) == "BARE"


class TestClassifyFiles:
    def test_counts_and_ignores_skill_md(self):
        counts = classify_files([
            "s/SKILL.md", "s/metadata.json", "s/run.py", "s/notes.md", "s/logo.png",
        ])
        assert counts == {"exec": 1, "doc": 1, "asset": 1}

    def test_verdicts(self):
        assert verdict_from_counts({"exec": 1, "doc": 0, "asset": 0}) == "EXEC"
        assert verdict_from_counts({"exec": 0, "doc": 2, "asset": 0}) == "REF_ASSET"
        assert verdict_from_counts({"exec": 0, "doc": 0, "asset": 0}) == "BARE"


class TestStrictBackfillInventory:
    @pytest.mark.parametrize("repo", ["../tools", "owner/..", "./repo", "owner/."])
    def test_rejects_dot_segment_repository_components(self, repo):
        assert audit_skill_assets.canonical_source_identity(
            repo, "skills/demo/SKILL.md"
        )[2] == "invalid_repo"

    def test_requires_one_exact_source_branch(self):
        assert audit_skill_assets.canonical_source_branch_from_metadata({}) == (
            "",
            "missing_source_branch",
        )
        assert audit_skill_assets.canonical_source_branch_from_metadata({
            "github_branch": "main",
            "branch": "release",
        }) == ("", "conflicting_source_branch_aliases")
        assert audit_skill_assets.canonical_source_branch_from_metadata({
            "github_branch": " release/v1 ",
            "branch": "release/v1",
        }) == ("release/v1", "")
        pinned_ref = "a" * 40
        assert audit_skill_assets.canonical_source_branch_from_metadata({
            "github_branch": pinned_ref,
        }) == (pinned_ref, "")
        assert audit_skill_assets.canonical_source_branch_from_metadata({
            "github_branch": "@",
        }) == ("@", "")

    @pytest.mark.parametrize(
        "source_ref",
        ["main..evil", "main@{x}", "main.lock", ".hidden", "main/", "-main", "main?"],
    )
    def test_rejects_invalid_git_source_refs(self, source_ref):
        assert audit_skill_assets.canonical_source_branch_from_metadata({
            "github_branch": source_ref,
        }) == ("", "invalid_source_branch")

    def test_rejects_case_colliding_archive_roots(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        monkeypatch.setattr(
            audit_skill_assets,
            "_iter_canonical_archive_paths",
            lambda _root: iter(["dev/Demo", "dev/demo"]),
        )

        with pytest.raises(ValueError, match="case-conflicting skill roots"):
            list(audit_skill_assets._canonical_archive_rows(root))

    def test_archive_path_preflight_fails_closed_on_walk_error(self, tmp_path, monkeypatch):
        root = tmp_path / "data"

        def failed_walk(_root, *, onerror):
            onerror(PermissionError("denied"))
            yield from ()

        monkeypatch.setattr(audit_skill_assets.os, "walk", failed_walk)

        with pytest.raises(ValueError, match="unable to inspect archive tree.*denied"):
            list(audit_skill_assets._canonical_archive_rows(root))

    def test_canonical_archive_rows_stream_metadata_after_lightweight_preflight(
        self, tmp_path, monkeypatch
    ):
        root = tmp_path / "data"
        first = root / "dev" / "one"
        second = root / "dev" / "two"
        first.mkdir(parents=True)
        second.mkdir(parents=True)
        calls = []

        monkeypatch.setattr(
            audit_skill_assets,
            "_iter_canonical_archive_paths",
            lambda _root: iter(["dev/one", "dev/two"]),
        )

        def metadata_rows(_root):
            calls.append("metadata")
            yield str(first), {"name": "one"}
            yield str(second), {"name": "two"}

        monkeypatch.setattr(audit_skill_assets, "iter_archived_skills", metadata_rows)

        rows = audit_skill_assets._canonical_archive_rows(root)

        assert calls == []
        assert next(rows) == (str(first), {"name": "one"})
        assert calls == ["metadata"]
        assert next(rows) == (str(second), {"name": "two"})
        with pytest.raises(StopIteration):
            next(rows)

    @pytest.mark.parametrize(
        ("metadata", "expected_path"),
        [
            ({"repo": "acme/tools", "path": "skills/demo"}, "skills/demo/SKILL.md"),
            ({"repo": "acme/tools", "github_path": "skills/demo"}, "skills/demo/SKILL.md"),
            ({"repo": "acme/tools", "github_path": ""}, "SKILL.md"),
            (
                {
                    "repo": "acme/tools",
                    "path": "skills/demo/SKILL.md",
                    "github_path": "skills/demo",
                },
                "skills/demo/SKILL.md",
            ),
        ],
    )
    def test_normalizes_repository_directory_form_metadata_paths(
        self, metadata, expected_path
    ):
        assert audit_skill_assets.canonical_source_identity_from_metadata(metadata) == (
            "acme/tools",
            expected_path,
            "",
        )

    def test_direct_canonical_source_still_requires_exact_skill_path(self):
        assert audit_skill_assets.canonical_source_identity(
            "acme/tools", "skills/demo"
        ) == ("acme/tools", "skills/demo", "source_path_not_skill_md")

    def test_conflicting_aliases_contribute_every_normalized_identity_key(self):
        keys = audit_skill_assets._identity_keys(
            {
                "repo": "Acme/Tools",
                "path": "skills/one/SKILL.md",
                "github_path": "skills/two/SKILL.md",
            },
            name="demo",
            category="dev",
        )
        assert keys == {
            "acme/tools:skills/one/SKILL.md",
            "acme/tools:skills/two/SKILL.md",
        }

    @pytest.mark.parametrize(
        "paths",
        [
            ["references/Guide.md", "references/guide.md"],
            ["References/one.md", "references/two.md"],
        ],
    )
    def test_detects_case_conflicts_in_files_and_directory_prefixes(self, paths):
        assert audit_skill_assets._has_case_conflict(paths) is True

    @pytest.mark.parametrize(
        "declared",
        [
            ["references//guide.md"],
            ["C:/scripts/run.py"],
            ["C:scripts/run.py"],
            ["references/Guide.md", "references/guide.md"],
            ["References/one.md", "references/two.md"],
        ],
    )
    def test_rejects_non_portable_bundled_file_declarations(self, declared):
        assert audit_skill_assets._declared_bundled_files({
            "bundled_files": declared,
        }) == ([], False)

    def test_nested_metadata_is_a_bundled_asset(self):
        assert audit_skill_assets._local_verdict(["references/metadata.json"]) == "REF_ASSET"

    def test_nested_metadata_keeps_current_state_internally_consistent(self, tmp_path):
        skill = tmp_path / "data" / "dev" / "demo"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text("See references/metadata.json.", encoding="utf-8")
        (skill / "references" / "metadata.json").write_text("{}", encoding="utf-8")

        report = audit_skill_assets.run_current_state(str(tmp_path / "data"), min_stars=0)

        assert report["actual_bundled_file_count"] == 1
        assert report["local_verdict_counts"] == {"REF_ASSET": 1}

    def test_conflicting_alias_record_makes_valid_candidate_ambiguous(self, tmp_path):
        root = tmp_path / "data"
        for name, metadata in (
            (
                "valid",
                {
                    "repo": "acme/tools",
                    "path": "skills/one/SKILL.md",
                    "github_branch": "main",
                    "stars": 100,
                },
            ),
            (
                "conflict",
                {
                    "repo": "Acme/Tools",
                    "path": "skills/two/SKILL.md",
                    "github_path": "skills/one/SKILL.md",
                    "github_branch": "main",
                    "stars": 100,
                },
            ),
        ):
            skill = root / "dev" / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("Run scripts/setup.py.", encoding="utf-8")
            (skill / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

        report = audit_skill_assets.run_current_state(str(root), min_stars=100)

        assert report["ambiguous_stable_key_count"] == 1
        assert report["backfill_candidate_count"] == 0

    def test_backfill_target_preserves_source_branch(self, tmp_path):
        skill = tmp_path / "data" / "dev" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("Run scripts/setup.py.", encoding="utf-8")
        (skill / "metadata.json").write_text(
            json.dumps({
                "repo": "acme/tools",
                "path": "skills/demo/SKILL.md",
                "github_branch": "release/v1",
                "stars": 100,
            }),
            encoding="utf-8",
        )

        [target] = audit_skill_assets.build_backfill_targets(
            str(tmp_path / "data"), min_stars=100
        )

        assert target["github_branch"] == "release/v1"


class TestResolveSkillDir:
    DIRS = ["skills/alpha", "skills/beta", ""]

    def test_declared_dir_wins(self):
        assert resolve_skill_dir({"dir": "skills/beta", "name": "alpha"}, self.DIRS) == "skills/beta"

    def test_falls_back_to_name_match(self):
        assert resolve_skill_dir({"dir": "", "name": "alpha"}, self.DIRS) == "skills/alpha"

    def test_unmatched_returns_none(self):
        assert resolve_skill_dir({"dir": "", "name": "missing"}, ["skills/alpha"]) is None


class TestIterArchivedSkills:
    def test_yields_skill_dirs_with_metadata(self, tmp_path):
        skill = tmp_path / "cat" / "demo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("body")
        (skill / "metadata.json").write_text(json.dumps({"stars": 5}))
        (tmp_path / "cat" / "not-a-skill").mkdir()

        results = list(iter_archived_skills(str(tmp_path)))
        assert len(results) == 1
        dirpath, meta = results[0]
        assert dirpath.endswith("demo")
        assert meta == {"stars": 5}

    def test_bad_metadata_yields_none(self, tmp_path):
        skill = tmp_path / "demo"
        skill.mkdir()
        (skill / "SKILL.md").write_text("body")
        (skill / "metadata.json").write_text("{broken")
        [(_, meta)] = list(iter_archived_skills(str(tmp_path)))
        assert meta is None
