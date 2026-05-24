#!/usr/bin/env python3
"""Fail closed when publish inputs or artifacts contain noncanonical categories."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from category_taxonomy import CategoryTaxonomy, category_slug, get_taxonomy


@dataclass(frozen=True)
class CategoryIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


class CategoryGate:
    def __init__(self, taxonomy: CategoryTaxonomy | None = None) -> None:
        self.taxonomy = taxonomy or get_taxonomy()
        self.publishable = self.taxonomy.publishable_categories()
        self.publishable_codes = frozenset(
            definition.code
            for definition in self.taxonomy.categories.values()
            if definition.status == "active"
        )

    def check_category(
        self,
        value: Any,
        *,
        path: str,
        field: str,
        issues: list[CategoryIssue],
    ) -> str | None:
        if not isinstance(value, str) or not value.strip():
            issues.append(
                CategoryIssue(
                    "category-missing",
                    path,
                    f"{field} must be a non-empty canonical category slug",
                )
            )
            return None

        raw = value.strip()
        slug = category_slug(raw)
        if raw != slug:
            issues.append(
                CategoryIssue(
                    "category-format",
                    path,
                    f"{field} {raw!r} must be written as canonical slug {slug!r}",
                )
            )
            return None

        if slug in self.publishable:
            return slug

        status = self.taxonomy.category_status(slug)
        target = self.taxonomy.migration_target(slug)
        hint = f"; reviewed target would be {target!r}" if target else ""
        issues.append(
            CategoryIssue(
                f"category-{status}",
                path,
                f"{field} {raw!r} is {status}, not publishable{hint}",
            )
        )
        return None

    def check_code(
        self,
        value: Any,
        *,
        path: str,
        field: str,
        issues: list[CategoryIssue],
    ) -> None:
        if not isinstance(value, str) or not value.strip():
            issues.append(
                CategoryIssue("category-code-missing", path, f"{field} must be non-empty")
            )
            return
        if value not in self.publishable_codes:
            issues.append(
                CategoryIssue(
                    "category-code-noncanonical",
                    path,
                    f"{field} {value!r} is not an active canonical category code",
                )
            )


def _load_json(path: Path, issues: list[CategoryIssue]) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append(CategoryIssue("file-missing", str(path), "file does not exist"))
    except json.JSONDecodeError as exc:
        issues.append(CategoryIssue("json-invalid", str(path), f"invalid JSON: {exc}"))
    return None


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _record_path(path: Path, root: Path, location: str = "") -> str:
    label = _relative(path, root)
    return f"{label} {location}".strip()


def _check_skill_record(
    record: Any,
    *,
    path: str,
    gate: CategoryGate,
    issues: list[CategoryIssue],
) -> None:
    if not isinstance(record, dict):
        issues.append(CategoryIssue("skill-record-shape", path, "skill record must be an object"))
        return
    gate.check_category(record.get("category"), path=path, field="skill.category", issues=issues)


def check_skills_dir(skills_dir: Path, gate: CategoryGate) -> list[CategoryIssue]:
    issues: list[CategoryIssue] = []
    if not skills_dir.exists():
        return [CategoryIssue("skills-dir-missing", str(skills_dir), "skills directory missing")]

    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        skill_dir = skill_md.parent
        rel_parts = skill_md.relative_to(skills_dir).parts
        label = _record_path(skill_md, skills_dir)
        if len(rel_parts) < 2:
            issues.append(
                CategoryIssue(
                    "archive-category-missing",
                    label,
                    "SKILL.md must live under <category>/<skill>/SKILL.md",
                )
            )
            continue

        directory_category = rel_parts[0]
        directory_slug = gate.check_category(
            directory_category,
            path=label,
            field="archive directory category",
            issues=issues,
        )

        metadata_path = skill_dir / "metadata.json"
        metadata = _load_json(metadata_path, issues)
        if metadata is None:
            continue
        metadata_label = _record_path(metadata_path, skills_dir)
        if not isinstance(metadata, dict):
            issues.append(
                CategoryIssue("metadata-shape", metadata_label, "metadata.json must be an object")
            )
            continue

        metadata_slug = gate.check_category(
            metadata.get("category"),
            path=metadata_label,
            field="metadata.category",
            issues=issues,
        )
        if directory_slug and metadata_slug and directory_slug != metadata_slug:
            issues.append(
                CategoryIssue(
                    "category-mismatch",
                    metadata_label,
                    "metadata.category must match the archive directory category "
                    f"({metadata_slug!r} != {directory_slug!r})",
                )
            )

    return issues


def _iter_skill_records_from_json(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        skills = payload.get("skills")
        if isinstance(skills, list):
            return skills
    if isinstance(payload, list):
        return payload
    return []


def _has_skill_records_shape(payload: Any) -> bool:
    return (isinstance(payload, dict) and isinstance(payload.get("skills"), list)) or isinstance(
        payload, list
    )


def check_registry_shards(shards_dir: Path, gate: CategoryGate) -> list[CategoryIssue]:
    issues: list[CategoryIssue] = []
    if not shards_dir.exists():
        return [
            CategoryIssue("registry-shards-missing", str(shards_dir), "registry shards missing")
        ]

    for shard_path in sorted(shards_dir.glob("*.json")):
        payload = _load_json(shard_path, issues)
        if payload is None:
            continue
        if not _has_skill_records_shape(payload):
            issues.append(
                CategoryIssue(
                    "registry-shard-shape",
                    _record_path(shard_path, shards_dir.parent),
                    "registry shard must contain a skills array",
                )
            )
            continue
        records = _iter_skill_records_from_json(payload)
        for index, record in enumerate(records):
            _check_skill_record(
                record,
                path=_record_path(shard_path, shards_dir.parent, f"skills[{index}]"),
                gate=gate,
                issues=issues,
            )

    return issues


def _check_category_entry(
    entry: Any,
    *,
    path: str,
    gate: CategoryGate,
    issues: list[CategoryIssue],
) -> None:
    if not isinstance(entry, dict):
        issues.append(CategoryIssue("category-entry-shape", path, "category entry must be an object"))
        return
    gate.check_category(entry.get("name"), path=path, field="category.name", issues=issues)
    if "code" in entry:
        gate.check_code(entry.get("code"), path=path, field="category.code", issues=issues)


def _check_category_payload(
    payload: Any,
    *,
    path: str,
    gate: CategoryGate,
    issues: list[CategoryIssue],
) -> None:
    if not isinstance(payload, dict):
        issues.append(CategoryIssue("artifact-shape", path, "category artifact must be an object"))
        return
    gate.check_category(payload.get("category"), path=path, field="category", issues=issues)
    if "code" in payload:
        gate.check_code(payload.get("code"), path=path, field="code", issues=issues)
    for index, record in enumerate(_iter_skill_records_from_json(payload)):
        _check_skill_record(
            record,
            path=f"{path} skills[{index}]",
            gate=gate,
            issues=issues,
        )


def _check_search_records(
    payload: Any,
    *,
    path: str,
    gate: CategoryGate,
    issues: list[CategoryIssue],
) -> None:
    if not isinstance(payload, dict):
        issues.append(CategoryIssue("artifact-shape", path, "search artifact must be an object"))
        return
    for index, record in enumerate(payload.get("skills") or []):
        _check_skill_record(
            record,
            path=f"{path} skills[{index}]",
            gate=gate,
            issues=issues,
        )
    for index, record in enumerate(payload.get("s") or []):
        if not isinstance(record, dict):
            issues.append(
                CategoryIssue(
                    "search-record-shape",
                    f"{path} s[{index}]",
                    "search mini record must be an object",
                )
            )
            continue
        gate.check_code(record.get("c"), path=f"{path} s[{index}]", field="s[].c", issues=issues)


def check_docs_dir(docs_dir: Path, gate: CategoryGate) -> list[CategoryIssue]:
    issues: list[CategoryIssue] = []
    if not docs_dir.exists():
        return [CategoryIssue("docs-dir-missing", str(docs_dir), "docs directory missing")]

    categories_dir = docs_dir / "categories"
    index_path = categories_dir / "index.json"
    if index_path.exists():
        index_payload = _load_json(index_path, issues)
        if isinstance(index_payload, dict):
            for index, entry in enumerate(index_payload.get("categories") or []):
                _check_category_entry(
                    entry,
                    path=_record_path(index_path, docs_dir, f"categories[{index}]"),
                    gate=gate,
                    issues=issues,
                )

    if categories_dir.exists():
        for pointer_path in sorted(categories_dir.glob("*.json")):
            if pointer_path.name == "index.json":
                continue
            gate.check_category(
                pointer_path.stem,
                path=_record_path(pointer_path, docs_dir),
                field="category pointer filename",
                issues=issues,
            )
            payload = _load_json(pointer_path, issues)
            if payload is not None:
                _check_category_payload(
                    payload,
                    path=_record_path(pointer_path, docs_dir),
                    gate=gate,
                    issues=issues,
                )

        for manifest_path in sorted(categories_dir.glob("*/manifest.json")):
            gate.check_category(
                manifest_path.parent.name,
                path=_record_path(manifest_path, docs_dir),
                field="category manifest directory",
                issues=issues,
            )
            payload = _load_json(manifest_path, issues)
            if payload is not None:
                _check_category_payload(
                    payload,
                    path=_record_path(manifest_path, docs_dir),
                    gate=gate,
                    issues=issues,
                )

        for part_path in sorted(categories_dir.glob("*/part-*.json")):
            payload = _load_json(part_path, issues)
            if payload is not None:
                _check_category_payload(
                    payload,
                    path=_record_path(part_path, docs_dir),
                    gate=gate,
                    issues=issues,
                )

    for path in (docs_dir / "search-index-lite.json", docs_dir / "featured.json"):
        if path.exists():
            payload = _load_json(path, issues)
            if payload is not None:
                _check_search_records(
                    payload,
                    path=_record_path(path, docs_dir),
                    gate=gate,
                    issues=issues,
                )

    for shard_path in sorted((docs_dir / "search-shards").glob("part-*.json")):
        payload = _load_json(shard_path, issues)
        if payload is not None:
            _check_search_records(
                payload,
                path=_record_path(shard_path, docs_dir),
                gate=gate,
                issues=issues,
            )

    stats_path = docs_dir / "stats.json"
    if stats_path.exists():
        stats_payload = _load_json(stats_path, issues)
        if isinstance(stats_payload, dict):
            for key in ("category_counts", "categories"):
                values = stats_payload.get(key)
                if isinstance(values, list):
                    for index, entry in enumerate(values):
                        _check_category_entry(
                            entry,
                            path=_record_path(stats_path, docs_dir, f"{key}[{index}]"),
                            gate=gate,
                            issues=issues,
                        )

    return issues


def build_report(
    *,
    skills_dirs: list[Path] | None = None,
    registry_shards_dirs: list[Path] | None = None,
    docs_dirs: list[Path] | None = None,
    taxonomy: CategoryTaxonomy | None = None,
) -> dict[str, Any]:
    gate = CategoryGate(taxonomy)
    issues: list[CategoryIssue] = []

    for path in skills_dirs or []:
        issues.extend(check_skills_dir(path, gate))
    for path in registry_shards_dirs or []:
        issues.extend(check_registry_shards(path, gate))
    for path in docs_dirs or []:
        issues.extend(check_docs_dir(path, gate))

    return {
        "canonical_category_count": len(gate.publishable),
        "canonical_category_code_count": len(gate.publishable_codes),
        "error_count": len(issues),
        "errors": [issue.as_dict() for issue in issues],
    }


def print_report(report: dict[str, Any], *, limit: int) -> None:
    print("Canonical category publish gate")
    print(f"Canonical categories: {report['canonical_category_count']}")
    print(f"Errors: {report['error_count']}")
    for issue in report["errors"][:limit]:
        print(f"- {issue['code']} {issue['path']}: {issue['message']}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", action="append", type=Path, default=[])
    parser.add_argument("--registry-shards", action="append", type=Path, default=[])
    parser.add_argument("--docs-dir", action="append", type=Path, default=[])
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--limit", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.skills_dir and not args.registry_shards and not args.docs_dir:
        print("ERROR: provide at least one --skills-dir, --registry-shards, or --docs-dir")
        return 2

    report = build_report(
        skills_dirs=args.skills_dir,
        registry_shards_dirs=args.registry_shards,
        docs_dirs=args.docs_dir,
    )
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print_report(report, limit=args.limit)
    return 1 if report["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
