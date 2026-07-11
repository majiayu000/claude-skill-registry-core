from __future__ import annotations

import gzip
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_registry_summary  # noqa: E402
import build_search_index  # noqa: E402
import check_artifact_api  # noqa: E402
import rebuild_registry  # noqa: E402


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rewrite(path: Path, mutate) -> None:
    payload = _read(path)
    mutate(payload)
    rebuild_registry.safe_write_json(path, payload)


def build_generated_fixture(root: Path) -> Path:
    generated_at = "2026-07-11T00:00:00Z"
    plugins = [{"name": "demo-plugin", "repo": "owner/plugins"}]
    skills = [
        {
            "name": "alpha",
            "description": "Alpha skill",
            "repo": "owner/alpha",
            "path": "skills/alpha/SKILL.md",
            "branch": "main",
            "category": "development",
            "tags": ["demo"],
            "stars": 2,
            "install": "owner/alpha/skills/alpha/SKILL.md",
            "source": "test",
        },
        {
            "name": "beta",
            "description": "Beta skill",
            "repo": "owner/beta",
            "path": "skills/beta/SKILL.md",
            "branch": "main",
            "category": "testing",
            "tags": ["demo"],
            "stars": 1,
            "install": "owner/beta/skills/beta/SKILL.md",
            "source": "test",
        },
    ]
    sources = root / "sources"
    sources.mkdir(parents=True)
    rebuild_registry.safe_write_json(sources / "plugins.json", {"plugins": plugins})

    entries = rebuild_registry.write_registry_shards(
        skills,
        root / "registry-shards",
        generated_at,
        reference_base=root,
    )
    manifest = rebuild_registry.build_registry_manifest(
        generated_at=generated_at,
        total_count=len(skills),
        plugin_count=len(plugins),
        shards=entries,
        summary_path="registry_summary.json",
        plugins_path="sources/plugins.json",
    )
    rebuild_registry.safe_write_json(root / "registry-manifest.json", manifest)
    pointer = rebuild_registry.build_compatibility_registry(
        generated_at=generated_at,
        total_count=len(skills),
        plugin_count=len(plugins),
        archive_skill_md_count_raw=len(skills),
        archive_metadata_count_raw=len(skills),
        manifest_path="registry-manifest.json",
    )
    rebuild_registry.safe_write_json(root / "registry.json", pointer)
    summary = build_registry_summary.build_registry_summary(
        root / "registry.json", sources / "plugins.json"
    )
    build_registry_summary.write_summary(root / "registry_summary.json", summary)

    docs = root / "docs"
    build_search_index.build_plugins_index(plugins, docs, updated_at=generated_at)
    build_search_index.build_search_index(
        skills,
        docs,
        source_name="generated fixture",
        archive_skill_md_count_raw=len(skills),
        archive_metadata_count_raw=len(skills),
        registry_skill_count_dedup=len(skills),
    )
    return docs


def _codes(report: check_artifact_api.ValidationReport) -> set[str]:
    return {error.code for error in report.errors}


def test_production_writers_generate_valid_v1_fixture(tmp_path):
    docs = build_generated_fixture(tmp_path)

    report = check_artifact_api.validate_artifact_api(tmp_path, docs)

    assert report.errors == []
    assert report.checked_files >= 520
    assert report.totals == {
        "registry": [2, 2, 2],
        "scan": [2, 2, 2],
        "stable": [2, 2, 2, 2, 2],
    }


@pytest.mark.parametrize(
    ("path", "mutate", "expected"),
    [
        ("registry.json", lambda value: value.__setitem__("schema_version", 2), "unknown_schema"),
        ("registry.json", lambda value: value.__setitem__("unexpected", 1), "invalid_pointer_shape"),
        ("registry.json", lambda value: value.__setitem__("total_count", False), "invalid_count"),
        ("docs/search-index.json", lambda value: value.__setitem__("s", []), "pointer_contains_payload"),
        ("docs/search-index.json", lambda value: value.__setitem__("t", 3), "count_alias_conflict"),
        (
            "docs/search-index-manifest.json",
            lambda value: value.__setitem__("total_count", 3),
            "manifest_total_mismatch",
        ),
        (
            "docs/search-index-manifest.json",
            lambda value: value.__setitem__("shard_count", 7),
            "entry_count_mismatch",
        ),
        (
            "docs/search-index-manifest.json",
            lambda value: value.__setitem__("unexpected", []),
            "invalid_manifest_shape",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].pop("sha256"),
            "invalid_entry_shape",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("unexpected", 1),
            "invalid_entry_shape",
        ),
        (
            "docs/search-shards/part-000.json",
            lambda value: value.__setitem__("skills", value.pop("s")),
            "invalid_payload_key",
        ),
        (
            "docs/search-shards/part-000.json",
            lambda value: value.__setitem__("count", 7),
            "payload_count_mismatch",
        ),
        (
            "docs/search-shards/part-000.json",
            lambda value: value.__setitem__("part", 9),
            "payload_identity_mismatch",
        ),
        (
            "docs/search-shards/part-000.json",
            lambda value: value.__setitem__("unexpected", []),
            "unknown_payload_field",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("path", "../outside.json"),
            "path_escape",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("path", "/outside.json"),
            "path_escape",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("path", "registry-shards/missing.json"),
            "missing_or_escaped_path",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("bytes", 1),
            "bytes_mismatch",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("gzip_bytes", 1),
            "gzip_bytes_mismatch",
        ),
        (
            "registry-manifest.json",
            lambda value: value["shards"][0].__setitem__("sha256", "0" * 64),
            "sha256_mismatch",
        ),
        (
            "docs/stats.json",
            lambda value: value.__setitem__("registry_skill_count_dedup", 3),
            "group_total_mismatch",
        ),
        (
            "docs/stats.json",
            lambda value: value.__setitem__("lite_index_count", 3),
            "group_total_mismatch",
        ),
    ],
    ids=[
        "schema",
        "pointer-unknown-field",
        "boolean-count",
        "pointer-payload",
        "alias",
        "manifest-count",
        "manifest-entry-count",
        "manifest-unknown-field",
        "entry-missing-field",
        "entry-unknown-field",
        "payload-key",
        "payload-count",
        "payload-identity",
        "payload-unknown-field",
        "path-escape",
        "absolute-path",
        "missing-path",
        "bytes",
        "gzip-bytes",
        "hash",
        "registry-cross-total",
        "stable-cross-total",
    ],
)
def test_validator_rejects_single_fact_mutations(tmp_path, path, mutate, expected):
    docs = build_generated_fixture(tmp_path)
    _rewrite(tmp_path / path, mutate)

    report = check_artifact_api.validate_artifact_api(tmp_path, docs)

    assert expected in _codes(report)


def test_validator_rejects_duplicate_reference_and_bad_gzip(tmp_path):
    docs = build_generated_fixture(tmp_path)
    manifest_path = tmp_path / "registry-manifest.json"
    manifest = _read(manifest_path)
    manifest["shards"][1]["path"] = manifest["shards"][0]["path"]
    rebuild_registry.safe_write_json(manifest_path, manifest)
    gzip_path = docs / "search-shards" / "part-000.json.gz"
    gzip_path.write_bytes(b"not gzip")

    report = check_artifact_api.validate_artifact_api(tmp_path, docs)

    assert {"duplicate_reference", "invalid_gzip"} <= _codes(report)


def test_validator_rejects_duplicate_gzip_reference_and_symlink(tmp_path):
    docs = build_generated_fixture(tmp_path)
    manifest_path = tmp_path / "registry-manifest.json"
    manifest = _read(manifest_path)
    manifest["shards"][1]["gzip_path"] = manifest["shards"][0]["gzip_path"]
    target = tmp_path / manifest["shards"][0]["path"]
    symlink = tmp_path / "registry-shards" / "linked.json"
    symlink.symlink_to(target)
    manifest["shards"][0]["path"] = "registry-shards/linked.json"
    rebuild_registry.safe_write_json(manifest_path, manifest)

    report = check_artifact_api.validate_artifact_api(tmp_path, docs)

    assert {"duplicate_reference", "non_regular_file"} <= _codes(report)


def test_validator_rejects_gzip_payload_mismatch(tmp_path):
    docs = build_generated_fixture(tmp_path)
    gzip_path = docs / "quality-shards" / "part-000.json.gz"
    with gzip.open(gzip_path, "wt", encoding="utf-8") as handle:
        json.dump({"schema_version": 1, "records": []}, handle)

    report = check_artifact_api.validate_artifact_api(tmp_path, docs)

    assert "gzip_payload_mismatch" in _codes(report)


def test_validator_cli_collects_errors_and_writes_report(tmp_path):
    docs = build_generated_fixture(tmp_path)
    (docs / "search-index.json").write_text("[]", encoding="utf-8")
    (tmp_path / "registry_summary.json").write_text("{", encoding="utf-8")
    output = tmp_path / "validation.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "check_artifact_api.py"),
            "--root",
            str(tmp_path),
            "--docs-dir",
            str(docs),
            "--output-json",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    payload = _read(output)
    assert completed.returncode == 1
    assert payload["status"] == "failed"
    assert len(payload["errors"]) >= 2
    assert "artifact-api status=failed" in completed.stdout


def test_validator_cli_never_echoes_invalid_artifact_contents(tmp_path):
    docs = build_generated_fixture(tmp_path)
    sentinel = "SENTINEL_PRIVATE_ARTIFACT_CONTENT_12345"
    (docs / "search-index.json").write_text(sentinel, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "check_artifact_api.py"),
            "--root",
            str(tmp_path),
            "--docs-dir",
            str(docs),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert sentinel not in completed.stdout
    assert sentinel not in completed.stderr


def test_validator_rejects_docs_dir_outside_root(tmp_path):
    outside = tmp_path.parent / "outside-docs"
    outside.mkdir(exist_ok=True)
    with pytest.raises(ValueError, match="inside root"):
        check_artifact_api.validate_artifact_api(tmp_path, outside)
