import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_metadata_compliance  # noqa: E402


def test_validate_metadata_preserves_copyright_for_notices(tmp_path):
    schema = json.loads((ROOT / "schema" / "metadata.schema.json").read_text(encoding="utf-8"))
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(
        json.dumps(
            {
                "name": "aksel-spacing",
                "repo": "navikt/copilot",
                "category": "design",
                "dir_name": "aksel-spacing",
                "author": "Nav",
                "source_url": "https://github.com/navikt/copilot/blob/main/.github/skills/aksel-spacing/SKILL.md",
                "license": "MIT",
                "copyright": "Copyright (c) 2025 Nav",
                "permission_note": "MIT terms require preserving copyright and permission notices.",
                "distribution": "compatible",
            }
        ),
        encoding="utf-8",
    )

    errors, warnings, row = check_metadata_compliance.validate_single_metadata(
        metadata_path, schema
    )

    assert errors == []
    assert warnings == []
    assert row["copyright"] == "Copyright (c) 2025 Nav"
    assert row["local_path"] == "skills/design/aksel-spacing"


def test_write_notices_includes_attribution_and_mit_text(tmp_path):
    notices_path = tmp_path / "THIRD_PARTY_NOTICES.md"

    check_metadata_compliance.write_notices(
        notices_path,
        [
            {
                "name": "aksel-spacing",
                "local_path": "skills/design/aksel-spacing",
                "repo": "navikt/copilot",
                "author": "Nav",
                "license": "MIT",
                "copyright": "Copyright (c) 2025 Nav",
                "distribution": "compatible",
                "source_url": "https://github.com/navikt/copilot/blob/main/.github/skills/aksel-spacing/SKILL.md",
                "permission_note": "MIT terms require preserving copyright and permission notices.",
            }
        ],
        scanned_count=1,
    )

    notices = notices_path.read_text(encoding="utf-8") + "".join(
        part.read_text(encoding="utf-8")
        for part in notices_path.with_suffix(".d").glob("part-*.md")
    )

    assert "Copyright (c) 2025 Nav" in notices
    assert "skills/design/aksel-spacing" in notices
    assert "MIT terms require preserving copyright and permission notices." in notices
    assert "The above copyright notice and this permission notice shall be included" in notices


def test_notice_parts_preserve_every_row_and_utf8_byte_bound(tmp_path, monkeypatch):
    monkeypatch.setattr(check_metadata_compliance, "NOTICES_PART_MAX_BYTES", 1024)
    path = tmp_path / "THIRD_PARTY_NOTICES.md"
    rows = [
        {
            "name": f"技能-{number}",
            "local_path": f"skills/design/unique-{number}",
            "repo": f"owner/repo-{number}",
            "author": "作者|team",
            "license": "MIT" if number % 2 else "Apache-2.0",
            "copyright": f"Copyright {number} 作者",
            "distribution": "compatible" if number % 2 else "restricted",
            "source_url": f"https://example.test/source/{number}",
            "permission_note": "保留许可|版权" * 8,
        }
        for number in range(17)
    ]
    check_metadata_compliance.write_notices(path, rows, scanned_count=len(rows))
    parts = sorted(path.with_suffix(".d").glob("part-*.md"))
    assert len(parts) > 1
    actual_rows = []
    for part in parts:
        assert part.stat().st_size <= 1024
        actual_rows.extend(
            line for line in part.read_text(encoding="utf-8").splitlines()
            if line.startswith("| 技能-")
        )
        assert f"({part.parent.name}/{part.name})" in path.read_text(encoding="utf-8")
    expected_rows = []
    for row in sorted(rows, key=lambda item: (item["repo"], item["name"])):
        fields = [row[k].replace("|", "\\|") for k in (
            "name", "local_path", "repo", "author", "license", "copyright", "distribution",
        )]
        fields.extend([row["source_url"], row["permission_note"].replace("|", "\\|")])
        expected_rows.append("| " + " | ".join(fields) + " |")
    assert actual_rows == expected_rows
    index = path.read_text(encoding="utf-8")
    assert "Notice rows generated: **17**" in index
    assert "### MIT" in index
    for text in check_metadata_compliance.LICENSE_NOTICE_TEXTS["MIT"]:
        assert text in index


def test_notice_regeneration_cleans_only_own_parts_even_when_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(check_metadata_compliance, "NOTICES_PART_MAX_BYTES", 512)
    path = tmp_path / "THIRD_PARTY_NOTICES.generated.md"
    rows = [{"name": f"entry-{i}", "permission_note": "a" * 150} for i in range(12)]
    check_metadata_compliance.write_notices(path, rows, scanned_count=len(rows))
    parts_dir = path.with_suffix(".d")
    assert len(list(parts_dir.glob("part-*.md"))) > 1
    unrelated = parts_dir / "owned-by-user.txt"
    unrelated.write_text("preserve", encoding="utf-8")
    check_metadata_compliance.write_notices(path, rows[:1], scanned_count=1)
    assert [p.name for p in parts_dir.glob("part-*.md")] == ["part-00001.md"]
    check_metadata_compliance.write_notices(path, [], scanned_count=0)
    assert not list(parts_dir.glob("part-*.md"))
    assert unrelated.read_text(encoding="utf-8") == "preserve"
    assert "No metadata entries were selected" in path.read_text(encoding="utf-8")


def test_notice_oversized_single_row_fails_without_replacing_previous_output(tmp_path, monkeypatch):
    monkeypatch.setattr(check_metadata_compliance, "NOTICES_PART_MAX_BYTES", 512)
    path = tmp_path / "THIRD_PARTY_NOTICES.md"
    check_metadata_compliance.write_notices(path, [{"name": "valid"}], scanned_count=1)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="Notice row exceeds"):
        check_metadata_compliance.write_notices(
            path, [{"name": "oversized", "permission_note": "界" * 200}], scanned_count=1
        )
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_notice_artifact_and_publish_boundaries_are_complete():
    for filename, upload_name in (
        ("metadata-compliance.yml", "Upload compliance artifacts"),
        ("sync-data.yml", "Upload metadata compliance report"),
    ):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        upload = next(
            step for job in workflow["jobs"].values() for step in job["steps"]
            if step.get("name") == upload_name
        )
        assert "THIRD_PARTY_NOTICES.generated.d/**" in upload["with"]["path"].splitlines()
        assert upload["if"] == "always()"
    sync_script = (ROOT / "scripts/sync_main_repo.sh").read_text()
    assert '"$root/THIRD_PARTY_NOTICES.generated.d"' in sync_script
    assert "--exclude 'THIRD_PARTY_NOTICES.generated.d'" in sync_script
    generation = sync_script.index('run_step "Generate third-party notices')
    size_check = sync_script.index('run_step "Check published notice sizes')
    assert size_check > generation
    assert "--include THIRD_PARTY_NOTICES.md" in sync_script[size_check:]
    assert "--include THIRD_PARTY_NOTICES.d" in sync_script[size_check:]
    assert "--include docs" not in sync_script[size_check:]
    subprocess.run(["bash", "-n", str(ROOT / "scripts/sync_main_repo.sh")], check=True)


def test_actual_publish_sync_removes_transient_parts_and_publishes_complete_notices(tmp_path):
    core, data, main = (tmp_path / name for name in ("core", "data", "main"))
    for path in (core, data, main):
        path.mkdir()
    for directory in ("scripts", "schema", "taxonomy"):
        shutil.copytree(ROOT / directory, core / directory)
    for path in (core, data, main):
        transient = path / "THIRD_PARTY_NOTICES.generated.d"
        transient.mkdir()
        (transient / "part-00001.md").write_text("transient advisory evidence")
    skill = data / "design/legal-example"
    skill.mkdir(parents=True)
    (skill / "metadata.json").write_text(json.dumps({
        "name": "legal-example", "repo": "owner/repo", "category": "design",
        "dir_name": "legal-example", "author": "Owner", "license": "MIT",
        "copyright": "Copyright 2026 Owner", "distribution": "compatible",
        "source_url": "https://github.com/owner/repo/blob/main/SKILL.md",
        "permission_note": "Preserve the copyright and permission notice.",
    }))
    # Existing advisory metadata errors must still report without blocking publish.
    (data / "invalid").mkdir()
    (data / "invalid/metadata.json").write_text("{}")
    result = subprocess.run(
        ["bash", str(core / "scripts/sync_main_repo.sh"), "--core", str(core),
         "--data", str(data), "--main", str(main), "--no-rebuild"],
        cwd=main,
        env={**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (main / "THIRD_PARTY_NOTICES.generated.d").exists()
    assert not (main / "skills/THIRD_PARTY_NOTICES.generated.d").exists()
    assert "Copyright 2026 Owner" in (main / "THIRD_PARTY_NOTICES.d/part-00001.md").read_text()
    assert "Check published notice sizes status=0" in result.stdout
    assert "Errors: 0" not in result.stdout
