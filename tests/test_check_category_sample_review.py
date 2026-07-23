from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_modules():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    return (
        importlib.import_module("audit_category_quality"),
        importlib.import_module("check_category_sample_review"),
    )


def _evidence(tmp_path):
    audit, _ = _load_modules()
    skills_dir = tmp_path / "skills"
    rows = []
    for index in range(2):
        skill_dir = skills_dir / "integration" / f"skill-{index}"
        skill_dir.mkdir(parents=True)
        skill_path = skill_dir / "SKILL.md"
        metadata_path = skill_dir / "metadata.json"
        skill_path.write_text(f"---\nname: skill-{index}\n---\n", encoding="utf-8")
        metadata_path.write_text(
            f'{{"name":"skill-{index}","category":"integration"}}\n',
            encoding="utf-8",
        )
        rows.append(
            {
                "path": f"integration/skill-{index}/SKILL.md",
                "name": f"skill-{index}",
                "current_category": "integration",
                "description": "integration helper",
                "content_excerpt": "bounded excerpt",
                "semantic_sources": {"name": "frontmatter"},
                "source_sha256": audit.file_sha256(skill_path),
                "metadata_sha256": audit.file_sha256(metadata_path),
                "sample_key": str(index + 5) * 64,
            }
        )
    stratum_digest = audit.canonical_digest(rows)
    sample_digest = audit.canonical_digest(
        [{"category": "integration", "digest": stratum_digest}]
    )
    sample = {
        "schema_version": 1,
        "status": "complete",
        "skills_dir": str(skills_dir),
        "policy": {
            "seed": "test",
            "per_category": 2,
            "categories": ["integration"],
            "content_chars": 128,
        },
        "sample_count": 2,
        "digest": sample_digest,
        "strata": [
            {
                "category": "integration",
                "population_count": 3,
                "sample_count": 2,
                "quota": 2,
                "digest": stratum_digest,
                "samples": rows,
            }
        ],
        "errors": [],
    }
    review = {
        "schema_version": 1,
        "sample_digest": sample_digest,
        "reviews": [
            {
                "path": row["path"],
                "source_sha256": row["source_sha256"],
                "metadata_sha256": row["metadata_sha256"],
                "expected_category": "integration",
            }
            for row in rows
        ],
    }
    return sample, review


def _use_test_policy(checker, monkeypatch):
    policy = SimpleNamespace(
        schema_version=1,
        seed="test",
        per_category=2,
        categories=("integration",),
    )
    taxonomy = SimpleNamespace(
        audit_sampling=policy,
        publishable_categories=lambda: {"integration", "data"},
    )
    monkeypatch.setattr(checker, "get_taxonomy", lambda: taxonomy)


def test_review_gate_accepts_complete_fresh_review(tmp_path, monkeypatch):
    _, checker = _load_modules()
    _use_test_policy(checker, monkeypatch)
    sample, review = _evidence(tmp_path)
    result = checker.check_review(sample, review, min_accuracy=0.8)
    assert result["status"] == "passed"
    assert result["accuracy"] == 1
    assert result["categories"]["integration"]["total"] == 2


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda sample, review: review["reviews"].pop(), "missing review paths"),
        (
            lambda sample, review: review["reviews"].append(
                copy.deepcopy(review["reviews"][0])
            ),
            "duplicate review path",
        ),
        (
            lambda sample, review: review["reviews"][0].update(
                source_sha256="f" * 64
            ),
            "stale source hashes",
        ),
        (
            lambda sample, review: review["reviews"][0].update(
                expected_category="not-canonical"
            ),
            "non-canonical expected category",
        ),
        (
            lambda sample, review: sample["strata"][0]["samples"][0].update(
                description="tampered"
            ),
            "sample stratum digest mismatch",
        ),
        (
            lambda sample, review: review.update(sample_digest="0" * 64),
            "review digest does not match sample",
        ),
    ],
)
def test_review_gate_rejects_incomplete_or_stale_evidence(
    mutation,
    message,
    tmp_path,
    monkeypatch,
):
    _, checker = _load_modules()
    _use_test_policy(checker, monkeypatch)
    sample, review = _evidence(tmp_path)
    mutation(sample, review)
    with pytest.raises(checker.ReviewEvidenceError, match=message):
        checker.check_review(sample, review, min_accuracy=0.8)


def test_review_gate_rejects_low_per_category_accuracy(tmp_path, monkeypatch):
    _, checker = _load_modules()
    _use_test_policy(checker, monkeypatch)
    sample, review = _evidence(tmp_path)
    review["reviews"][0]["expected_category"] = "data"
    with pytest.raises(checker.ReviewEvidenceError, match="accuracy.*below"):
        checker.check_review(sample, review, min_accuracy=0.8)


def test_review_gate_rejects_sources_changed_after_sampling(tmp_path, monkeypatch):
    _, checker = _load_modules()
    _use_test_policy(checker, monkeypatch)
    sample, review = _evidence(tmp_path)
    source_path = Path(sample["skills_dir"]) / sample["strata"][0]["samples"][0]["path"]
    source_path.write_text("changed after review\n", encoding="utf-8")

    with pytest.raises(checker.ReviewEvidenceError, match="sample source changed"):
        checker.check_review(sample, review, min_accuracy=0.8)


def test_review_gate_rejects_noncanonical_sampling_policy(tmp_path):
    _, checker = _load_modules()
    sample, review = _evidence(tmp_path)
    with pytest.raises(
        checker.ReviewEvidenceError,
        match="sample policy does not match canonical taxonomy",
    ):
        checker.check_review(sample, review, min_accuracy=0.8)


def test_review_gate_cli_reports_pass_and_failure(
    tmp_path,
    monkeypatch,
    capsys,
):
    _, checker = _load_modules()
    _use_test_policy(checker, monkeypatch)
    sample, review = _evidence(tmp_path)
    sample_path = tmp_path / "sample.json"
    review_path = tmp_path / "review.json"
    sample_path.write_text(json.dumps(sample), encoding="utf-8")
    review_path.write_text(json.dumps(review), encoding="utf-8")

    assert checker.main(
        ["--sample", str(sample_path), "--review", str(review_path)]
    ) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"

    review["reviews"].pop()
    review_path.write_text(json.dumps(review), encoding="utf-8")
    assert checker.main(
        ["--sample", str(sample_path), "--review", str(review_path)]
    ) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
