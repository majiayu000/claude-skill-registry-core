"""Tests for build_search_index.py scoring of root-mounted SKILL.md entries.

Background: many community catalog entries (sources/community.json) describe
skills whose SKILL.md lives at the repo root, encoded as path="". Treating an
empty path as "no install location" under-scored these skills on install
status, quality, and trust, dropping them below the visibility threshold even
though their install URL (repo) was fully resolvable.

These tests pin the behavior that path="" and path="." are equivalent to a
real subdirectory path for scoring purposes whenever a repo is present.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_search_index import (  # noqa: E402
    build_search_index,
    has_install_location,
    infer_install_status,
    score_skill_quality,
    score_skill_trust,
)
from rebuild_registry import scan_skills as scan_registry_skills  # noqa: E402
from search_sources import (  # noqa: E402
    asset_ranking_penalty,
    is_root_mounted_path,
    load_from_registry,
    scan_skills_v2,
    verified_asset_fields,
)


def _skill(**overrides):
    base = {
        "name": "example",
        "description": "x" * 100,
        "repo": "acme/example",
        "path": "",
        "tags": ["a", "b", "c"],
        "stars": 0,
    }
    base.update(overrides)
    return base


def test_is_root_mounted_path_recognizes_empty_and_dot():
    assert is_root_mounted_path("") is True
    assert is_root_mounted_path(".") is True
    assert is_root_mounted_path(None) is True
    assert is_root_mounted_path("   ") is True


def test_is_root_mounted_path_rejects_real_subdirs():
    assert is_root_mounted_path("skills/foo") is False
    assert is_root_mounted_path("plugins/getterdone") is False
    assert is_root_mounted_path("./skills/foo") is False


def test_has_install_location_true_for_root_and_subdir():
    assert has_install_location("") is True
    assert has_install_location(".") is True
    assert has_install_location("skills/foo") is True


def test_infer_install_status_known_good_for_root_mounted_repo():
    # Root-mounted (path="") with a real repo is now known_good.
    assert infer_install_status("acme/example", "", "acme/example") == "known_good"
    assert infer_install_status("acme/example", ".", "acme/example") == "known_good"


def test_infer_install_status_known_good_for_subdir():
    # Existing behavior preserved: subdir path is still known_good.
    assert (
        infer_install_status("acme/example", "skills/foo", "acme/example/skills/foo")
        == "known_good"
    )


def test_infer_install_status_unchanged_for_broken_and_local_and_risky():
    # Empty install is still broken.
    assert infer_install_status("acme/example", "", "") == "broken"
    # local/ prefix is still unknown regardless of path.
    assert infer_install_status("acme/example", "", "local/foo") == "unknown"
    assert infer_install_status("acme/example", "skills/foo", "local/foo") == "unknown"
    # No repo, no install → risky.
    assert infer_install_status("", "", "something-else") == "risky"


def test_quality_score_root_mounted_matches_subdir():
    """Empty path with a repo must produce the same quality components as a real subdir."""
    root_skill = _skill(path="")
    subdir_skill = _skill(path="skills/foo")

    root_status = infer_install_status(root_skill["repo"], root_skill["path"], root_skill["repo"])
    subdir_status = infer_install_status(
        subdir_skill["repo"],
        subdir_skill["path"],
        f"{subdir_skill['repo']}/{subdir_skill['path']}",
    )

    root_quality = score_skill_quality(root_skill, root_status, "unknown")
    subdir_quality = score_skill_quality(subdir_skill, subdir_status, "unknown")

    assert root_quality["score_inputs"]["path"] == 15
    assert root_quality["score_inputs"]["install"] == 20
    assert root_quality["quality_score"] == subdir_quality["quality_score"]


def test_quality_score_dot_path_matches_empty_path():
    empty_skill = _skill(path="")
    dot_skill = _skill(path=".")

    empty_status = infer_install_status(empty_skill["repo"], "", empty_skill["repo"])
    dot_status = infer_install_status(dot_skill["repo"], ".", dot_skill["repo"])

    assert empty_status == dot_status == "known_good"
    assert (
        score_skill_quality(empty_skill, empty_status, "unknown")["quality_score"]
        == score_skill_quality(dot_skill, dot_status, "unknown")["quality_score"]
    )


def test_quality_score_clears_visibility_threshold_for_root_mounted_skill():
    """Realistic getterdone-shaped entry should score >= 70 (the A-grade gate)."""
    skill = _skill(
        description=(
            "AI agents hire human gig workers for real-world and specialized "
            "digital tasks via USD bounty with photo/text proof."
        ),
        repo="getterdoneinc/skill",
        path="",
        tags=["agents", "human-in-the-loop", "gig-economy", "real-world", "bounty", "mcp"],
        stars=0,
    )
    status = infer_install_status(skill["repo"], skill["path"], skill["repo"])
    quality = score_skill_quality(skill, status, "unknown")

    assert status == "known_good"
    assert quality["quality_score"] >= 70
    assert quality["quality_grade"] in {"A", "S"}


def test_quality_security_component_only_rewards_passed_security():
    skill = _skill()
    install_status = infer_install_status(skill["repo"], skill["path"], skill["repo"])

    passed = score_skill_quality(skill, install_status, "passed")
    unknown = score_skill_quality(skill, install_status, "unknown")
    failed = score_skill_quality(skill, install_status, "failed")

    assert passed["score_inputs"]["security"] == 10
    assert unknown["score_inputs"]["security"] == 0
    assert failed["score_inputs"]["security"] == 0


def test_trust_score_only_rewards_passed_security():
    skill = _skill(stars=5)
    install_status = infer_install_status(skill["repo"], skill["path"], skill["repo"])

    passed = score_skill_trust(
        skill["repo"], skill["path"], install_status, "passed", skill["stars"]
    )
    unknown = score_skill_trust(
        skill["repo"], skill["path"], install_status, "unknown", skill["stars"]
    )
    failed = score_skill_trust(
        skill["repo"], skill["path"], install_status, "failed", skill["stars"]
    )

    assert passed == unknown + 15
    assert unknown == failed


def test_build_search_index_consumes_security_decision_evidence(tmp_path):
    output_dir = tmp_path / "docs"
    output_dir.mkdir()
    output_dir.joinpath("security-report.json").write_text(
        json.dumps(
            {
                "scanner": {
                    "name": "claude-skill-registry-security-scanner",
                    "version": "1.1.0",
                    "ruleset_sha256": "abc123",
                },
                "generated_at": "2026-05-24T00:00:00Z",
                "total": 1,
                "passed": 1,
                "failed": 0,
                "skills": [
                    {
                        "path": "development/demo/SKILL.md",
                        "safe": True,
                        "security_decision": {
                            "id": "decision123",
                            "status": "passed",
                            "reason": "no_errors",
                            "scanner": {
                                "name": "claude-skill-registry-security-scanner",
                                "version": "1.1.0",
                                "ruleset_sha256": "abc123",
                            },
                            "provenance": {
                                "content_sha256": "def456",
                                "scanned_at": "2026-05-24T00:00:00Z",
                            },
                        },
                        "issues": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    build_search_index(
        [
            _skill(
                path="skills/demo",
                install="acme/example/skills/demo",
                archive_path="development/demo/SKILL.md",
            )
        ],
        output_dir,
        require_security_evidence=True,
    )

    manifest = json.loads((output_dir / "security-index-manifest.json").read_text())
    shard = json.loads((output_dir / manifest["shards"][0]["path"]).read_text())
    record = shard["records"][0]
    assert record["security_status"] == "passed"
    assert record["security_decision"]["id"] == "decision123"


def test_build_search_index_consumes_external_security_report_without_publishing_raw(tmp_path):
    output_dir = tmp_path / "docs"
    output_dir.mkdir()
    security_report_path = tmp_path / "security-report.json"
    security_report_path.write_text(
        json.dumps(
            {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "skills": [
                    {
                        "path": "development/demo/SKILL.md",
                        "security_decision": {
                            "status": "passed",
                            "scanner": {
                                "name": "claude-skill-registry-security-scanner",
                                "version": "1.1.0",
                                "ruleset_sha256": "abc123",
                            },
                            "provenance": {
                                "content_sha256": "def456",
                                "scanned_at": "2026-05-24T00:00:00Z",
                            },
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    build_search_index(
        [
            _skill(
                path="skills/demo",
                install="acme/example/skills/demo",
                archive_path="development/demo/SKILL.md",
            )
        ],
        output_dir,
        require_security_evidence=True,
        security_report_path=security_report_path,
    )

    stats = json.loads((output_dir / "stats.json").read_text())
    assert stats["security_scan"] == {"total": 1, "passed": 1, "failed": 0}
    assert not output_dir.joinpath("security-report.json").exists()


def test_build_search_index_fails_when_required_security_report_is_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="Required security evidence is missing"):
        build_search_index(
            [_skill(path="skills/demo", install="acme/example/skills/demo")],
            tmp_path / "docs",
            require_security_evidence=True,
        )


def test_build_search_index_skips_missing_security_decision_when_optional(tmp_path):
    output_dir = tmp_path / "docs"
    output_dir.mkdir()
    output_dir.joinpath("security-report.json").write_text(
        json.dumps(
            {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "skills": [
                    {
                        "path": "development/demo/SKILL.md",
                        "safe": True,
                        "issues": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    build_search_index(
        [
            _skill(
                path="skills/demo",
                install="acme/example/skills/demo",
                archive_path="development/demo/SKILL.md",
            )
        ],
        output_dir,
        require_security_evidence=False,
    )

    manifest = json.loads((output_dir / "security-index-manifest.json").read_text())
    shard = json.loads((output_dir / manifest["shards"][0]["path"]).read_text())
    record = shard["records"][0]
    assert record["security_status"] == "unknown"
    assert "security_decision" not in record


def test_build_search_index_requires_security_decision_when_required(tmp_path):
    output_dir = tmp_path / "docs"
    output_dir.mkdir()
    output_dir.joinpath("security-report.json").write_text(
        json.dumps(
            {
                "total": 1,
                "passed": 1,
                "failed": 0,
                "skills": [{"path": "development/demo/SKILL.md", "safe": True}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Missing security_decision"):
        build_search_index(
            [
                _skill(
                    path="skills/demo",
                    install="acme/example/skills/demo",
                    archive_path="development/demo/SKILL.md",
                )
            ],
            output_dir,
            require_security_evidence=True,
        )


def test_build_emits_complete_category_taxonomy_sidecar(tmp_path):
    output_dir = tmp_path / "docs"
    build_search_index([], output_dir)

    sidecar = json.loads((output_dir / "category-taxonomy.json").read_text())
    assert sidecar["schema_version"] == 1
    assert sidecar["taxonomy_schema_version"] == 2
    assert sidecar["category_count"] == 40
    assert sidecar["default_category"] == "other"
    assert sidecar["default_code"] == "oth"
    assert len({item["slug"] for item in sidecar["categories"]}) == 40
    assert len({item["code"] for item in sidecar["categories"]}) == 40
    assert len([item for item in sidecar["categories"] if not item["parent"]]) == 12


def test_registry_and_search_publish_only_locally_validated_asset_facets(tmp_path):
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / "development" / "asset-demo"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Asset demo", encoding="utf-8")
    support_file = skill_dir / "scripts" / "run.py"
    support_file.write_text("print('ok')", encoding="utf-8")
    metadata = {
        "name": "asset-demo",
        "repo": "acme/assets",
        "path": "skills/asset-demo/SKILL.md",
        "github_branch": "main",
        "category": "development",
        "archive_mode": "directory",
        "bundled_files": ["scripts/run.py"],
        "github_commit_sha": "a" * 40,
        "assets_verified_at": "2026-08-01T00:00:00Z",
        "asset_liveness": "live",
        "assets_liveness_checked_at": "2026-08-11T00:00:00Z",
        "assets_liveness_sha": "b" * 40,
    }
    (skill_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")

    [search_record] = scan_skills_v2(skills_dir)
    [registry_record] = scan_registry_skills(skills_dir)
    expected = {
        "asset_state": "verified",
        "asset_liveness": "live",
        "bundled_file_count": 1,
        "github_commit_sha": "a" * 40,
        "assets_verified_at": "2026-08-01T00:00:00Z",
        "assets_liveness_checked_at": "2026-08-11T00:00:00Z",
        "assets_liveness_sha": "b" * 40,
    }
    assert {key: search_record[key] for key in expected} == expected
    assert {key: registry_record[key] for key in expected} == expected

    support_file.unlink()
    [search_record] = scan_skills_v2(skills_dir)
    [registry_record] = scan_registry_skills(skills_dir)
    for record in (search_record, registry_record):
        assert "asset_state" not in record
        assert "asset_liveness" not in record


def test_live_asset_facets_win_equal_search_ranks_by_downranking_only(tmp_path):
    plain = _skill(
        name="plain",
        repo="acme/plain",
        path="skills/demo/SKILL.md",
        install="acme/plain/skills/demo/SKILL.md",
        branch="main",
        stars=10,
    )
    verified_live = _skill(
        name="verified-live",
        repo="acme/live",
        path="skills/demo/SKILL.md",
        install="acme/live/skills/demo/SKILL.md",
        branch="main",
        stars=10,
        asset_state="verified",
        asset_liveness="live",
        bundled_file_count=1,
        github_commit_sha="a" * 40,
        assets_verified_at="2026-08-01T00:00:00Z",
        assets_liveness_checked_at="2026-08-11T00:00:00Z",
        assets_liveness_sha="b" * 40,
    )
    output_dir = tmp_path / "docs"
    stats = build_search_index([plain, verified_live], output_dir)

    lite = json.loads((output_dir / "search-index-lite.json").read_text())
    assert [skill["name"] for skill in lite["skills"]] == ["verified-live", "plain"]
    assert lite["skills"][0]["asset_state"] == "verified"
    assert lite["skills"][0]["asset_liveness"] == "live"
    assert "asset_state" not in lite["skills"][1]

    search_manifest = json.loads((output_dir / "search-index-manifest.json").read_text())
    search_shard = json.loads((output_dir / search_manifest["shards"][0]["path"]).read_text())
    assert search_shard["s"][0]["a"] == "verified"
    assert search_shard["s"][0]["l"] == "live"
    assert "a" not in search_shard["s"][1]

    ranking_manifest = json.loads((output_dir / "ranking-index-manifest.json").read_text())
    ranking_shard = json.loads((output_dir / ranking_manifest["shards"][0]["path"]).read_text())
    by_install = {record["install"]: record for record in ranking_shard["records"]}
    live_rank = by_install[verified_live["install"]]
    plain_rank = by_install[plain["install"]]
    assert live_rank["asset_ranking_penalty"] == 0
    assert plain_rank["asset_ranking_penalty"] == 0.1
    assert live_rank["recommended_score"] > plain_rank["recommended_score"]
    assert stats["asset_state_counts"] == {"verified": 1}
    assert stats["asset_liveness_counts"] == {"live": 1}
    featured = json.loads((output_dir / "featured.json").read_text())
    assert [skill["name"] for skill in featured["skills"]] == ["verified-live", "plain"]


def test_asset_ranking_penalties_are_downrank_only():
    assert asset_ranking_penalty({"asset_state": "verified", "asset_liveness": "live"}) == 0
    assert asset_ranking_penalty({"asset_state": "verified"}) == 0.1
    assert asset_ranking_penalty({"asset_state": "verified", "asset_liveness": "partial"}) == 0.25
    assert asset_ranking_penalty({"asset_state": "verified", "asset_liveness": "moved"}) == 0.5
    assert asset_ranking_penalty({"asset_state": "verified", "asset_liveness": "gone"}) == 0.75
    assert asset_ranking_penalty({}) == 0.1


def test_verified_asset_fields_omit_malformed_claims_and_incomplete_liveness(tmp_path):
    skill_dir = tmp_path / "skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    (skill_dir / "scripts/run.py").write_text("asset", encoding="utf-8")
    base = {
        "archive_mode": "directory",
        "bundled_files": ["scripts/run.py"],
        "github_commit_sha": "a" * 40,
        "assets_verified_at": "2026-08-01T00:00:00Z",
    }
    invalid_changes = [
        {"archive_mode": "skill-md"},
        {"bundled_files": []},
        {"bundled_files": [" scripts/run.py"]},
        {"bundled_files": ["scripts\\run.py"]},
        {"bundled_files": ["../run.py"]},
        {"bundled_files": ["/run.py"]},
        {"bundled_files": ["SKILL.md"]},
        {"bundled_files": ["scripts/run.py", "scripts/run.py"]},
        {"github_commit_sha": "bad"},
        {"assets_verified_at": ""},
    ]
    for change in invalid_changes:
        assert verified_asset_fields({**base, **change}, skill_dir, tmp_path) == {}

    verified = verified_asset_fields({**base, "asset_liveness": "live"}, skill_dir, tmp_path)
    assert verified["asset_state"] == "verified"
    assert "asset_liveness" not in verified
    verified = verified_asset_fields(
        {
            **base,
            "asset_liveness": "gone",
            "assets_liveness_checked_at": "2026-08-11T00:00:00Z",
        },
        skill_dir,
        tmp_path,
    )
    assert verified["asset_liveness"] == "gone"
    assert "assets_liveness_sha" not in verified
    verified = verified_asset_fields(
        {
            **base,
            "asset_liveness": "live",
            "assets_liveness_checked_at": "2026-08-11T00:00:00Z",
            "assets_liveness_sha": "bad",
        },
        skill_dir,
        tmp_path,
    )
    assert "asset_liveness" not in verified


def test_verified_asset_fields_reject_symlinks(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("body", encoding="utf-8")
    external = tmp_path / "external.py"
    external.write_text("asset", encoding="utf-8")
    (skill_dir / "run.py").symlink_to(external)
    metadata = {
        "archive_mode": "directory",
        "bundled_files": ["run.py"],
        "github_commit_sha": "a" * 40,
        "assets_verified_at": "2026-08-01T00:00:00Z",
    }
    assert verified_asset_fields(metadata, skill_dir, tmp_path) == {}

    external_skill = tmp_path / "external-skill"
    external_skill.mkdir()
    (external_skill / "SKILL.md").write_text("body", encoding="utf-8")
    (external_skill / "run.py").write_text("asset", encoding="utf-8")
    linked_skill = tmp_path / "linked-skill"
    linked_skill.symlink_to(external_skill, target_is_directory=True)
    assert verified_asset_fields(metadata, linked_skill, tmp_path) == {}
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(tmp_path, target_is_directory=True)
    assert verified_asset_fields(metadata, linked_root / "skill", linked_root) == {}


def test_registry_fallback_preserves_validated_asset_fields(tmp_path):
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"skills": [{
        "name": "verified-live",
        "repo": "acme/live",
        "path": "skills/demo/SKILL.md",
        "branch": "main",
        "category": "development",
        "asset_state": "verified",
        "asset_liveness": "live",
        "bundled_file_count": 1,
        "github_commit_sha": "a" * 40,
        "assets_verified_at": "2026-08-01T00:00:00Z",
        "assets_liveness_checked_at": "2026-08-11T00:00:00Z",
        "assets_liveness_sha": "b" * 40,
    }]}), encoding="utf-8")
    [loaded] = load_from_registry(registry_path)
    assert loaded["asset_state"] == "verified"
    assert loaded["asset_liveness"] == "live"

    output_dir = tmp_path / "docs"
    build_search_index([loaded], output_dir)
    lite = json.loads((output_dir / "search-index-lite.json").read_text())
    assert lite["skills"][0]["asset_state"] == "verified"
    manifest = json.loads((output_dir / "search-index-manifest.json").read_text())
    shard = json.loads((output_dir / manifest["shards"][0]["path"]).read_text())
    assert shard["s"][0]["a"] == "verified"
