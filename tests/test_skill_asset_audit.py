import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import verify_upstream_assets as liveness
from skill_asset_audit import (
    classify_files,
    classify_skill_text,
    iter_archived_skills,
    verdict_from_counts,
)


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


def make_verified_asset(
    root: Path,
    name: str,
    *,
    repo: str = "acme/tools",
    branch: str = "main",
    bundled_files: list[str] | None = None,
) -> Path:
    files = bundled_files or ["scripts/run.py"]
    skill_dir = root / "dev" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("Run scripts/run.py", encoding="utf-8")
    for filename in files:
        path = skill_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("asset", encoding="utf-8")
    metadata = {
        "name": name,
        "repo": repo,
        "path": f"skills/{name}/SKILL.md",
        "github_branch": branch,
        "github_commit_sha": "a" * 40,
        "assets_verified_at": "2026-08-01T00:00:00Z",
        "archive_mode": "directory",
        "bundled_files": files,
    }
    metadata_path = skill_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    return metadata_path


class FakeLivenessClient:
    def __init__(self, paths=None, *, repo_error=None, branch_error=None, tree_error=None):
        self.paths = set(paths or [])
        self.repo_error = repo_error
        self.branch_error = branch_error
        self.tree_error = tree_error
        self.calls = []

    def repository(self, repo):
        self.calls.append(("repo", repo))
        if self.repo_error:
            raise self.repo_error
        return {"full_name": repo}

    def branch_sha(self, repo, branch):
        self.calls.append(("branch", repo, branch))
        if self.branch_error:
            raise self.branch_error
        return "b" * 40

    def tree(self, repo, sha):
        self.calls.append(("tree", repo, sha))
        if self.tree_error:
            raise self.tree_error
        return self.paths


class TestAssetLiveness:
    def test_groups_repository_and_branch_then_applies_live_and_partial(self, tmp_path):
        skills = tmp_path / "skills"
        first = make_verified_asset(skills, "alpha")
        second = make_verified_asset(skills, "beta")
        client = FakeLivenessClient({
            "skills/alpha/SKILL.md",
            "skills/alpha/scripts/run.py",
            "skills/beta/SKILL.md",
        })
        report_path = tmp_path / "report.json"

        result = liveness.main([
            "--skills-dir", str(skills),
            "--report", str(report_path),
            "--apply",
            "--max-decayed-percent", "100",
        ], client=client)

        assert result == 0
        assert [call[0] for call in client.calls] == ["repo", "branch", "tree"]
        report = json.loads(report_path.read_text())
        assert report["summary"] == {"live": 1, "partial": 1}
        assert report["repo_count"] == 1
        assert report["applied"] is True
        alpha = json.loads(first.read_text())
        beta = json.loads(second.read_text())
        assert alpha["asset_liveness"] == "live"
        assert beta["asset_liveness"] == "partial"
        assert alpha["github_commit_sha"] == "a" * 40
        assert alpha["assets_liveness_sha"] == "b" * 40
        assert alpha["assets_verified_at"] == "2026-08-01T00:00:00Z"

    def test_api_failure_preserves_previous_verified_state(self, tmp_path):
        skills = tmp_path / "skills"
        metadata_path = make_verified_asset(skills, "alpha")
        metadata = json.loads(metadata_path.read_text())
        metadata.update({
            "asset_liveness": "live",
            "assets_liveness_checked_at": "2026-08-02T00:00:00Z",
            "assets_liveness_sha": "c" * 40,
        })
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        before = metadata_path.read_bytes()
        client = FakeLivenessClient(
            repo_error=liveness.GitHubApiError(503, "service unavailable")
        )

        result = liveness.main([
            "--skills-dir", str(skills),
            "--report", str(tmp_path / "report.json"),
            "--apply",
            "--max-error-percent", "100",
        ], client=client)

        assert result == 0
        assert metadata_path.read_bytes() == before
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["summary"] == {"verification_error": 1}

    def test_missing_repo_records_gone_without_deleting_archive(self, tmp_path):
        skills = tmp_path / "skills"
        metadata_path = make_verified_asset(skills, "alpha")
        metadata = json.loads(metadata_path.read_text())
        metadata["assets_liveness_sha"] = "c" * 40
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        client = FakeLivenessClient(repo_error=liveness.GitHubApiError(404, "not found"))

        result = liveness.main([
            "--skills-dir", str(skills),
            "--report", str(tmp_path / "report.json"),
            "--apply",
            "--max-decayed-percent", "100",
        ], client=client)

        assert result == 0
        updated = json.loads(metadata_path.read_text())
        assert updated["asset_liveness"] == "gone"
        assert "assets_liveness_sha" not in updated
        assert (metadata_path.parent / "scripts/run.py").is_file()

    def test_missing_branch_is_moved_and_missing_skill_path_is_moved(self, tmp_path):
        skills = tmp_path / "skills"
        make_verified_asset(skills, "alpha")
        targets, errors = liveness.load_targets(skills)
        assert not errors
        branch_client = FakeLivenessClient(
            branch_error=liveness.GitHubApiError(404, "branch missing")
        )
        [branch_row] = liveness.verify_targets(targets, branch_client, "now")
        assert branch_row["status"] == "moved"
        path_client = FakeLivenessClient({"skills/alpha/scripts/run.py"})
        [path_row] = liveness.verify_targets(targets, path_client, "now")
        assert path_row["status"] == "moved"
        assert path_row["current_source_sha"] == "b" * 40

    def test_tree_failure_preserves_state_as_verification_error(self, tmp_path):
        skills = tmp_path / "skills"
        make_verified_asset(skills, "alpha")
        targets, errors = liveness.load_targets(skills)
        assert not errors
        client = FakeLivenessClient(
            tree_error=liveness.GitHubApiError(404, "tree unavailable")
        )
        [row] = liveness.verify_targets(targets, client, "now")
        assert row["status"] == "verification_error"
        assert [call[0] for call in client.calls] == ["repo", "branch", "tree"]

    def test_repository_identity_redirect_is_moved(self, tmp_path):
        skills = tmp_path / "skills"
        make_verified_asset(skills, "alpha")
        targets, _ = liveness.load_targets(skills)
        client = FakeLivenessClient()
        client.repository = lambda _repo: {"full_name": "other/tools"}
        [row] = liveness.verify_targets(targets, client, "now")
        assert row["status"] == "moved"

    def test_local_mismatch_fails_closed_without_api_call(self, tmp_path):
        skills = tmp_path / "skills"
        metadata_path = make_verified_asset(skills, "alpha")
        (metadata_path.parent / "scripts/run.py").unlink()
        client = FakeLivenessClient()
        before = metadata_path.read_bytes()

        result = liveness.main([
            "--skills-dir", str(skills),
            "--report", str(tmp_path / "report.json"),
            "--apply",
            "--max-error-percent", "100",
        ], client=client)

        assert result == 1
        assert client.calls == []
        assert metadata_path.read_bytes() == before
        report = json.loads((tmp_path / "report.json").read_text())
        assert report["summary"] == {"local_error": 1}

    @pytest.mark.parametrize(
        "change,error",
        [
            ({"bundled_files": []}, "non-empty"),
            ({"bundled_files": ["../run.py"]}, "invalid or duplicate"),
            ({"github_commit_sha": "not-a-sha"}, "immutable"),
            ({"github_branch": ""}, "github_branch must be a non-empty string"),
            ({"github_branch": "d" * 40}, "raw commit SHA"),
            ({"github_path": "other/SKILL.md"}, "conflicting path"),
            ({"branch": "develop"}, "conflicting github_branch"),
            (
                {"path": "", "github_path": "skills/alpha/SKILL.md"},
                "path must be a non-empty string",
            ),
            ({"github_branch": [], "branch": "main"}, "github_branch must be"),
        ],
    )
    def test_invalid_canonical_metadata_is_a_local_error(self, tmp_path, change, error):
        skills = tmp_path / "skills"
        metadata_path = make_verified_asset(skills, "alpha")
        metadata = json.loads(metadata_path.read_text())
        metadata.update(change)
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        targets, errors = liveness.load_targets(skills)
        assert targets == []
        assert error in errors[0]["error"]

    @pytest.mark.parametrize("level", ["category", "skill"])
    def test_symlinked_archive_parent_is_rejected(self, tmp_path, level):
        external = tmp_path / "external"
        metadata_path = make_verified_asset(external, "alpha")
        skills = tmp_path / "skills"
        skills.mkdir()
        if level == "category":
            (skills / "dev").symlink_to(external / "dev", target_is_directory=True)
        else:
            (skills / "dev").mkdir()
            (skills / "dev" / "alpha").symlink_to(
                metadata_path.parent, target_is_directory=True
            )
        targets, errors = liveness.load_targets(skills)
        assert targets == []
        assert "cannot be a symlink" in errors[0]["error"]

    def test_apply_rejects_changed_metadata(self, tmp_path):
        skills = tmp_path / "skills"
        metadata_path = make_verified_asset(skills, "alpha")
        targets, errors = liveness.load_targets(skills)
        assert not errors
        metadata_path.write_text(metadata_path.read_text() + "\n", encoding="utf-8")
        rows = [{"stable_key": targets[0].stable_key, "status": "live", "current_source_sha": "b" * 40}]
        apply_errors = liveness.apply_updates(targets, rows, "now")
        assert "changed after verification" in apply_errors[0]

    def test_gate_recomputes_summary_and_enforces_thresholds(self):
        report = {
            "rows": [
                {"status": "live"},
                {"status": "partial"},
                {"status": "verification_error"},
            ],
            "summary": {"live": 1, "partial": 1, "verification_error": 1},
            "target_count": 3,
        }
        errors = liveness.gate_errors(
            report, max_decayed_percent=20, max_error_percent=20, min_targets=4
        )
        assert len(errors) == 3
        report["summary"] = {"live": 3}
        assert "summary mismatch" in liveness.gate_errors(
            report, max_decayed_percent=100, max_error_percent=100, min_targets=1
        )[0]
        malformed = liveness.gate_errors(
            {"rows": None, "summary": {}, "target_count": 0},
            max_decayed_percent=100,
            max_error_percent=100,
            min_targets=1,
        )
        assert malformed == ["report rows or summary is malformed"]

    def test_apply_error_always_fails_even_below_error_threshold(self):
        rows = [{"status": "live"} for _ in range(20)] + [{"status": "apply_error"}]
        report = {
            "rows": rows,
            "summary": liveness.summarize(rows),
            "target_count": 20,
        }
        assert liveness.gate_errors(
            report, max_decayed_percent=100, max_error_percent=10, min_targets=1
        ) == ["metadata apply or rollback failed"]

    def test_main_apply_failure_rolls_back_and_fails_gate(self, tmp_path, monkeypatch):
        skills = tmp_path / "skills"
        first = make_verified_asset(skills, "alpha")
        second = make_verified_asset(skills, "beta")
        originals = {path: path.read_bytes() for path in (first, second)}
        real_write = liveness._write_atomic
        failed = False

        def fail_second_update(path, content):
            nonlocal failed
            if path == second and not failed and b'"asset_liveness"' in content:
                failed = True
                raise OSError("replace failed")
            real_write(path, content)

        monkeypatch.setattr(liveness, "_write_atomic", fail_second_update)
        client = FakeLivenessClient({
            "skills/alpha/SKILL.md", "skills/alpha/scripts/run.py",
            "skills/beta/SKILL.md", "skills/beta/scripts/run.py",
        })
        report_path = tmp_path / "report.json"
        result = liveness.main([
            "--skills-dir", str(skills), "--report", str(report_path), "--apply",
            "--max-error-percent", "100",
        ], client=client)
        assert result == 1
        assert {path: path.read_bytes() for path in (first, second)} == originals
        report = json.loads(report_path.read_text())
        assert report["summary"]["apply_error"] == 1
        assert report["gate"]["passed"] is False

    def test_main_metadata_drift_fails_gate_without_overwrite(self, tmp_path):
        skills = tmp_path / "skills"
        metadata_path = make_verified_asset(skills, "alpha")
        original = metadata_path.read_bytes()
        client = FakeLivenessClient({
            "skills/alpha/SKILL.md", "skills/alpha/scripts/run.py",
        })
        real_tree = client.tree

        def mutate_then_list(repo, sha):
            metadata_path.write_bytes(original + b"\n")
            return real_tree(repo, sha)

        client.tree = mutate_then_list
        report_path = tmp_path / "report.json"
        result = liveness.main([
            "--skills-dir", str(skills), "--report", str(report_path), "--apply",
            "--max-error-percent", "100",
        ], client=client)
        assert result == 1
        assert metadata_path.read_bytes() == original + b"\n"
        report = json.loads(report_path.read_text())
        assert report["summary"]["apply_error"] == 1

    def test_apply_reports_incomplete_rollback(self, tmp_path, monkeypatch):
        skills = tmp_path / "skills"
        first = make_verified_asset(skills, "alpha")
        second = make_verified_asset(skills, "beta")
        targets, errors = liveness.load_targets(skills)
        assert not errors
        originals = {path: path.read_bytes() for path in (first, second)}
        real_write = liveness._write_atomic
        apply_failed = False

        def fail_apply_and_restore(path, content):
            nonlocal apply_failed
            if path == second and not apply_failed and b'"asset_liveness"' in content:
                apply_failed = True
                raise OSError("apply failed")
            if path == first and apply_failed and content == originals[first]:
                raise OSError("restore failed")
            real_write(path, content)

        monkeypatch.setattr(liveness, "_write_atomic", fail_apply_and_restore)
        rows = [
            {"stable_key": target.stable_key, "status": "live", "current_source_sha": "b" * 40}
            for target in targets
        ]
        apply_errors = liveness.apply_updates(targets, rows, "now")
        assert "recovery failed" in apply_errors[0]
        assert json.loads(first.read_text())["asset_liveness"] == "live"
        assert second.read_bytes() == originals[second]

    def test_workflow_runs_full_profile_gate_before_data_commit(self):
        workflow = Path(".github/workflows/sync-data.yml").read_text(encoding="utf-8")
        verify_at = workflow.index("Verify bundled asset liveness")
        commit_at = workflow.index("Commit & push data repo changes")
        assert verify_at < commit_at
        assert "steps.discovery.outputs.profile == 'full'" in workflow[verify_at:commit_at]
        assert "--apply" in workflow[verify_at:commit_at]
        assert "--max-decayed-percent 35" in workflow[verify_at:commit_at]
        assert "Upload bundled asset liveness report" in workflow


class TestGitHubClient:
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

        def close(self):
            return None

    def test_encodes_branch_and_parses_blob_tree(self, monkeypatch):
        requests = []
        payloads = iter([
            {"name": "feature/assets", "commit": {"sha": "a" * 40}},
            {"tree": [{"path": "SKILL.md", "type": "blob"}, {"path": "dir", "type": "tree"}]},
        ])

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            return self.Response(next(payloads))

        monkeypatch.setattr(liveness.urllib.request, "urlopen", fake_urlopen)
        client = liveness.GitHubClient("token")
        assert client.branch_sha("acme/tools", "feature/assets") == "a" * 40
        assert client.tree("acme/tools", "a" * 40) == {"SKILL.md"}
        assert "feature%2Fassets" in requests[0][0].full_url
        assert requests[0][0].get_header("Authorization") == "Bearer token"

    def test_http_and_malformed_tree_fail_explicitly(self, monkeypatch):
        def http_error(_request, timeout):
            assert timeout == 30
            raise urllib.error.HTTPError("url", 404, "missing", {}, self.Response({"message": "missing"}))

        monkeypatch.setattr(liveness.urllib.request, "urlopen", http_error)
        with pytest.raises(liveness.GitHubApiError, match="404") as caught:
            liveness.GitHubClient().repository("acme/missing")
        assert caught.value.status == 404

        monkeypatch.setattr(
            liveness.urllib.request,
            "urlopen",
            lambda _request, timeout: self.Response({"truncated": True}),
        )
        with pytest.raises(liveness.GitHubApiError, match="truncated"):
            liveness.GitHubClient().tree("acme/tools", "a" * 40)
