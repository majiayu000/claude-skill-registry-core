#!/usr/bin/env python3
"""Validate the complete static-artifact-api-v1 publish tree."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

POINTER_REQUIRED = {
    "schema_version",
    "total_count",
    "deprecated_full_payload",
    "message",
    "manifest",
    "replacement",
    "compat_since",
    "compat_until",
}
COUNT_ALIASES = {"t", "count", "registry_skill_count_dedup"}
ENTRY_REQUIRED = {"path", "gzip_path", "count", "bytes", "gzip_bytes", "sha256"}
POINTER_EXTRA_FIELDS = {
    "registry": {
        "version",
        "updated_at",
        "plugin_count",
        "archive_skill_md_count_raw",
        "archive_metadata_count_raw",
        "registry_skill_count_dedup",
    },
    "search": {"v", "t"},
    "signal": {"updated_at", "count"},
    "category": {"category", "code", "updated_at", "count"},
}


@dataclass(frozen=True)
class ArtifactError:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    checked_files: int
    totals: dict[str, list[int]]
    errors: list[ArtifactError]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": "failed" if self.errors else "complete",
            "checked_files": self.checked_files,
            "totals": self.totals,
            "errors": [asdict(error) for error in self.errors],
        }


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


class ArtifactValidator:
    def __init__(self, root: Path, docs_dir: Path) -> None:
        self.root = root.resolve()
        self.docs = docs_dir.resolve()
        self.errors: list[ArtifactError] = []
        self.checked: set[Path] = set()
        self.totals: dict[str, list[int]] = {"registry": [], "scan": [], "stable": []}

    def error(self, code: str, path: str | Path, message: str) -> None:
        display = path.as_posix() if isinstance(path, Path) else path
        self.errors.append(ArtifactError(code=code, path=display, message=message))

    def require_fields(
        self,
        payload: dict,
        path: str,
        *,
        required: set[str],
        optional: set[str] | None = None,
        code: str = "invalid_shape",
    ) -> None:
        missing = sorted(required - payload.keys())
        unknown = sorted(payload.keys() - required - (optional or set()))
        if missing:
            self.error(code, path, f"missing fields: {','.join(missing)}")
        if unknown:
            self.error(code, path, f"unknown fields: {','.join(unknown)}")

    def resolve_file(self, base: Path, reference: object, owner: str) -> Path | None:
        if not isinstance(reference, str) or not reference or "\\" in reference:
            self.error("invalid_path", owner, "artifact path must be a non-empty POSIX string")
            return None
        pure = PurePosixPath(reference)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            self.error("path_escape", owner, "artifact path must remain inside publish root")
            return None
        candidate = base.joinpath(*pure.parts)
        current = base
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                self.error("non_regular_file", reference, "artifact path must not traverse symlinks")
                return None
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(base)
        except (FileNotFoundError, RuntimeError, ValueError):
            self.error("missing_or_escaped_path", reference, "referenced artifact is missing or escaped")
            return None
        if candidate.is_symlink() or not resolved.is_file():
            self.error("non_regular_file", reference, "artifact must be a regular non-symlink file")
            return None
        self.checked.add(resolved)
        return resolved

    def load_json(self, base: Path, reference: object, owner: str) -> tuple[Path, dict] | None:
        path = self.resolve_file(base, reference, owner)
        if path is None:
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.error("invalid_json", reference if isinstance(reference, str) else owner, "invalid UTF-8 JSON")
            return None
        if not isinstance(payload, dict):
            self.error("invalid_shape", str(reference), "top-level JSON must be an object")
            return None
        return path, payload

    def require_schema(self, payload: dict, path: str) -> bool:
        if payload.get("schema_version") != 1:
            self.error("unknown_schema", path, "schema_version must equal 1")
            return False
        return True

    def require_count(self, payload: dict, key: str, path: str) -> int | None:
        value = payload.get(key)
        if not _is_int(value):
            self.error("invalid_count", path, f"{key} must be a non-negative integer")
            return None
        return value

    def check_pointer(
        self,
        base: Path,
        pointer_path: str,
        *,
        kind: str,
        aliases: set[str],
    ) -> dict | None:
        loaded = self.load_json(base, pointer_path, pointer_path)
        if loaded is None:
            return None
        _, pointer = loaded
        self.require_schema(pointer, pointer_path)
        self.require_fields(
            pointer,
            pointer_path,
            required=POINTER_REQUIRED,
            optional=POINTER_EXTRA_FIELDS[kind],
            code="invalid_pointer_shape",
        )
        total = self.require_count(pointer, "total_count", pointer_path)
        if pointer.get("deprecated_full_payload") is not True:
            self.error("invalid_pointer", pointer_path, "deprecated_full_payload must be true")
        for key in ("message", "manifest", "replacement", "compat_since", "compat_until"):
            if not isinstance(pointer.get(key), str) or not pointer[key]:
                self.error("invalid_pointer", pointer_path, f"{key} must be a non-empty string")
        replacement = pointer.get("replacement")
        if isinstance(replacement, str):
            pure_replacement = PurePosixPath(replacement)
            if (
                pure_replacement.is_absolute()
                or "\\" in replacement
                or "://" in replacement
                or any(part in {"", ".", ".."} for part in pure_replacement.parts)
            ):
                self.error("invalid_replacement", pointer_path, "replacement must be a safe relative pattern")
        if pointer.get("compat_since") != "static-artifact-api-v1" or pointer.get(
            "compat_until"
        ) != "static-artifact-api-v2":
            self.error("invalid_compat_window", pointer_path, "unsupported compatibility window")
        if any(key in pointer for key in ("skills", "records", "s")):
            self.error("pointer_contains_payload", pointer_path, "pointer must not contain full payload")
        for alias in COUNT_ALIASES:
            if alias in pointer and alias not in aliases:
                self.error("unknown_count_alias", pointer_path, f"alias {alias} is not allowed")
            elif alias in pointer:
                alias_count = self.require_count(pointer, alias, pointer_path)
                if total is not None and alias_count != total:
                    self.error(
                        "count_alias_conflict",
                        pointer_path,
                        f"alias {alias} conflicts with total_count",
                    )
        return pointer

    def check_file_entry(
        self,
        base: Path,
        entry: object,
        owner: str,
        *,
        allowed_fields: set[str],
        required_fields: set[str] | None = None,
    ) -> tuple[dict, dict] | None:
        if not isinstance(entry, dict):
            self.error("invalid_entry", owner, "manifest entry must be an object")
            return None
        self.require_fields(
            entry,
            owner,
            required=required_fields or ENTRY_REQUIRED,
            optional=allowed_fields - (required_fields or ENTRY_REQUIRED),
            code="invalid_entry_shape",
        )
        unknown = sorted(set(entry) - allowed_fields)
        if unknown:
            self.error("unknown_entry_field", owner, f"unknown fields: {','.join(unknown)}")
        count = self.require_count(entry, "count", owner)
        plain = self.resolve_file(base, entry.get("path"), owner)
        compressed = self.resolve_file(base, entry.get("gzip_path"), owner)
        if plain is None or compressed is None or count is None:
            return None
        if entry.get("bytes") != plain.stat().st_size:
            self.error("bytes_mismatch", str(entry.get("path")), "bytes does not match file size")
        if entry.get("gzip_bytes") != compressed.stat().st_size:
            self.error("gzip_bytes_mismatch", str(entry.get("gzip_path")), "gzip_bytes does not match file size")
        digest = hashlib.sha256(plain.read_bytes()).hexdigest()
        if entry.get("sha256") != digest:
            self.error("sha256_mismatch", str(entry.get("path")), "sha256 does not match file")
        try:
            plain_payload = json.loads(plain.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            self.error("invalid_json", str(entry.get("path")), "plain artifact is invalid JSON")
            return None
        try:
            with gzip.open(compressed, "rt", encoding="utf-8") as handle:
                gzip_payload = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, gzip.BadGzipFile):
            self.error("invalid_gzip", str(entry.get("gzip_path")), "gzip artifact is invalid")
            return None
        if plain_payload != gzip_payload:
            self.error("gzip_payload_mismatch", str(entry.get("gzip_path")), "gzip JSON differs from plain JSON")
        if not isinstance(plain_payload, dict):
            self.error("invalid_shape", str(entry.get("path")), "payload must be an object")
            return None
        return entry, plain_payload

    def check_duplicate_entry_references(
        self,
        entry: dict,
        seen: set[str],
        owner: str,
    ) -> None:
        for key in ("path", "gzip_path"):
            value = entry.get(key)
            if not isinstance(value, str):
                continue
            if value in seen:
                self.error("duplicate_reference", owner, f"manifest repeats {key}")
            seen.add(value)

    def check_sharded(
        self,
        base: Path,
        pointer_path: str,
        *,
        kind: str,
        aliases: set[str],
    ) -> int | None:
        pointer = self.check_pointer(base, pointer_path, kind=kind, aliases=aliases)
        if pointer is None or not isinstance(pointer.get("manifest"), str):
            return None
        loaded = self.load_json(base, pointer["manifest"], pointer_path)
        if loaded is None:
            return None
        _, manifest = loaded
        self.require_schema(manifest, pointer["manifest"])
        if kind == "registry":
            self.require_fields(
                manifest,
                pointer["manifest"],
                required={
                    "schema_version",
                    "generated_at",
                    "total_count",
                    "plugin_count",
                    "shard_strategy",
                    "shard_count",
                    "record_key",
                    "provenance",
                    "summary",
                    "shards",
                    "plugins",
                },
                code="invalid_manifest_shape",
            )
        else:
            common_fields = {
                "schema_version",
                "updated_at",
                "total_count",
                "shard_strategy",
                "record_schema",
                "shard_count",
                "largest_shard_bytes",
                "largest_shard_gzip_bytes",
                "shards",
            }
            self.require_fields(
                manifest,
                pointer["manifest"],
                required=common_fields | ({"v"} if kind == "search" else set()),
                code="invalid_manifest_shape",
            )
        total = self.require_count(manifest, "total_count", pointer["manifest"])
        if total is not None and pointer.get("total_count") != total:
            self.error("pointer_manifest_count_mismatch", pointer_path, "pointer and manifest totals differ")
        entries_key = "shards"
        count_key = "shard_count"
        entries = manifest.get(entries_key)
        entry_count = self.require_count(manifest, count_key, pointer["manifest"])
        for size_key in ("largest_shard_bytes", "largest_shard_gzip_bytes"):
            if kind != "registry":
                self.require_count(manifest, size_key, pointer["manifest"])
        if kind == "registry":
            plugin_count = self.require_count(manifest, "plugin_count", pointer["manifest"])
            pointer_plugin_count = self.require_count(pointer, "plugin_count", pointer_path)
            if plugin_count is not None and pointer_plugin_count != plugin_count:
                self.error("plugin_count_mismatch", pointer_path, "pointer and manifest plugin counts differ")
            self.resolve_file(base, manifest.get("summary"), pointer["manifest"])
            plugins = manifest.get("plugins")
            if not isinstance(plugins, dict):
                self.error("invalid_manifest", pointer["manifest"], "plugins must be an object")
            else:
                self.require_fields(
                    plugins,
                    f"{pointer['manifest']}#plugins",
                    required={"path", "count"},
                    code="invalid_manifest_shape",
                )
                plugins_count = self.require_count(
                    plugins, "count", f"{pointer['manifest']}#plugins"
                )
                if plugin_count is not None and plugins_count != plugin_count:
                    self.error(
                        "plugin_count_mismatch",
                        pointer["manifest"],
                        "plugins entry count differs from manifest",
                    )
                self.resolve_file(base, plugins.get("path"), pointer["manifest"])
        else:
            for key in ("updated_at", "shard_strategy", "record_schema"):
                if not isinstance(manifest.get(key), str) or not manifest[key]:
                    self.error("invalid_manifest", pointer["manifest"], f"{key} must be non-empty")
        if not isinstance(entries, list):
            self.error("invalid_manifest", pointer["manifest"], "shards must be a list")
            return total
        if entry_count is not None and entry_count != len(entries):
            self.error("entry_count_mismatch", pointer["manifest"], "shard_count differs from entries")
        seen: set[str] = set()
        actual_total = 0
        payload_key = "skills" if kind == "registry" else "s" if kind == "search" else "records"
        for index, raw_entry in enumerate(entries):
            owner = f"{pointer['manifest']}#{index}"
            allowed = {"path", "gzip_path", "count", "bytes", "gzip_bytes", "sha256"}
            required = set(allowed)
            if kind == "registry":
                allowed.add("id")
                required.add("id")
            checked = self.check_file_entry(
                base,
                raw_entry,
                owner,
                allowed_fields=allowed,
                required_fields=required,
            )
            if not isinstance(raw_entry, dict):
                continue
            self.check_duplicate_entry_references(raw_entry, seen, owner)
            if _is_int(raw_entry.get("count")):
                actual_total += raw_entry["count"]
            if checked is None:
                continue
            entry, payload = checked
            self.require_schema(payload, str(entry.get("path")))
            expected_fields = {"schema_version", "count", payload_key}
            if kind == "registry":
                expected_fields |= {"shard", "generated_at"}
                identity_ok = payload.get("shard") == entry.get("id")
            else:
                expected_fields |= {"part", "part_count"}
                if kind == "search":
                    expected_fields.add("v")
                else:
                    expected_fields.add("updated_at")
                identity_ok = payload.get("part") == index and payload.get("part_count") == len(entries)
            unknown_payload = sorted(set(payload) - expected_fields)
            if unknown_payload:
                self.error("unknown_payload_field", str(entry.get("path")), f"unknown fields: {','.join(unknown_payload)}")
            records = payload.get(payload_key)
            payload_count = self.require_count(payload, "count", str(entry.get("path")))
            if kind != "registry":
                self.require_count(payload, "part_count", str(entry.get("path")))
            if not identity_ok:
                self.error("payload_identity_mismatch", str(entry.get("path")), "payload identity is invalid")
            if not isinstance(records, list):
                self.error("invalid_payload_key", str(entry.get("path")), f"{payload_key} must be a list")
            elif payload_count != entry.get("count") or len(records) != entry.get("count"):
                self.error("payload_count_mismatch", str(entry.get("path")), "payload count differs from entry")
        if total is not None and actual_total != total:
            self.error("manifest_total_mismatch", pointer["manifest"], "entry counts do not sum to total_count")
        return total

    def check_categories(self) -> int | None:
        loaded = self.load_json(self.docs, "categories/index.json", "categories/index.json")
        if loaded is None:
            return None
        _, index_payload = loaded
        self.require_schema(index_payload, "categories/index.json")
        self.require_fields(
            index_payload,
            "categories/index.json",
            required={
                "schema_version",
                "updated_at",
                "total_count",
                "category_count",
                "categories",
            },
            code="invalid_category_index_shape",
        )
        total = self.require_count(index_payload, "total_count", "categories/index.json")
        categories = index_payload.get("categories")
        category_count = self.require_count(index_payload, "category_count", "categories/index.json")
        if not isinstance(categories, list):
            self.error("invalid_category_index", "categories/index.json", "categories must be a list")
            return total
        if category_count is not None and category_count != len(categories):
            self.error("category_count_mismatch", "categories/index.json", "category_count differs from entries")
        manifest_total = 0
        seen_paths: set[str] = set()
        for index, category in enumerate(categories):
            owner = f"categories/index.json#{index}"
            if not isinstance(category, dict):
                self.error("invalid_category_entry", owner, "category entry must be an object")
                continue
            self.require_fields(
                category,
                owner,
                required={
                    "name",
                    "code",
                    "count",
                    "path",
                    "manifest",
                    "part_count",
                    "largest_part_bytes",
                    "largest_part_gzip_bytes",
                },
                code="invalid_category_entry_shape",
            )
            for key in ("name", "code", "path", "manifest"):
                if not isinstance(category.get(key), str) or not category[key]:
                    self.error("invalid_category_entry", owner, f"{key} must be a non-empty string")
            entry_total = self.require_count(category, "count", owner)
            self.require_count(category, "part_count", owner)
            self.require_count(category, "largest_part_bytes", owner)
            self.require_count(category, "largest_part_gzip_bytes", owner)
            pointer_path = category.get("path")
            if not isinstance(pointer_path, str):
                continue
            if pointer_path in seen_paths:
                self.error("duplicate_reference", owner, "category pointer path is duplicated")
            seen_paths.add(pointer_path)
            pointer = self.check_pointer(
                self.docs,
                pointer_path,
                kind="category",
                aliases={"count"},
            )
            if pointer is None or not isinstance(pointer.get("manifest"), str):
                continue
            if pointer.get("manifest") != category.get("manifest"):
                self.error("category_manifest_mismatch", pointer_path, "index and pointer manifests differ")
            manifest_loaded = self.load_json(self.docs, pointer["manifest"], pointer_path)
            if manifest_loaded is None:
                continue
            _, manifest = manifest_loaded
            self.require_schema(manifest, pointer["manifest"])
            self.require_fields(
                manifest,
                pointer["manifest"],
                required={
                    "schema_version",
                    "category",
                    "code",
                    "updated_at",
                    "total_count",
                    "count",
                    "part_count",
                    "part_strategy",
                    "largest_part_bytes",
                    "largest_part_gzip_bytes",
                    "parts",
                },
                code="invalid_manifest_shape",
            )
            manifest_count = self.require_count(manifest, "total_count", pointer["manifest"])
            alias_count = self.require_count(manifest, "count", pointer["manifest"])
            self.require_count(manifest, "largest_part_bytes", pointer["manifest"])
            self.require_count(manifest, "largest_part_gzip_bytes", pointer["manifest"])
            if manifest_count is not None:
                manifest_total += manifest_count
            if len({entry_total, pointer.get("total_count"), manifest_count, alias_count}) != 1:
                self.error("category_total_mismatch", pointer_path, "category totals differ")
            parts = manifest.get("parts")
            part_count = self.require_count(manifest, "part_count", pointer["manifest"])
            if not isinstance(parts, list):
                self.error("invalid_manifest", pointer["manifest"], "parts must be a list")
                continue
            if part_count != len(parts):
                self.error("entry_count_mismatch", pointer["manifest"], "part_count differs from entries")
            part_total = 0
            seen_parts: set[str] = set()
            for part_index, raw_entry in enumerate(parts):
                part_owner = f"{pointer['manifest']}#{part_index}"
                checked = self.check_file_entry(
                    self.docs,
                    raw_entry,
                    part_owner,
                    allowed_fields={"path", "gzip_path", "count", "bytes", "gzip_bytes", "sha256"},
                )
                if not isinstance(raw_entry, dict):
                    continue
                if _is_int(raw_entry.get("count")):
                    part_total += raw_entry["count"]
                self.check_duplicate_entry_references(raw_entry, seen_parts, part_owner)
                if checked is None:
                    continue
                entry, payload = checked
                self.require_schema(payload, str(entry.get("path")))
                expected = {"schema_version", "category", "code", "updated_at", "part", "part_count", "count", "skills"}
                self.require_fields(
                    payload,
                    str(entry.get("path")),
                    required=expected,
                    code="unknown_payload_field",
                )
                skills = payload.get("skills")
                payload_count = self.require_count(payload, "count", str(entry.get("path")))
                self.require_count(payload, "part_count", str(entry.get("path")))
                if payload.get("part") != part_index or payload.get("part_count") != len(parts):
                    self.error("payload_identity_mismatch", str(entry.get("path")), "category part identity is invalid")
                if not isinstance(skills, list):
                    self.error("invalid_payload_key", str(entry.get("path")), "skills must be a list")
                elif payload_count != entry.get("count") or len(skills) != entry.get("count"):
                    self.error("payload_count_mismatch", str(entry.get("path")), "category payload count differs")
            if manifest_count is not None and part_total != manifest_count:
                self.error("manifest_total_mismatch", pointer["manifest"], "part counts do not sum to total_count")
        if total is not None and manifest_total != total:
            self.error("category_index_total_mismatch", "categories/index.json", "category totals do not sum to total_count")
        return total

    def check_simple_documents(self) -> tuple[int | None, int | None, int | None]:
        lite_loaded = self.load_json(self.docs, "search-index-lite.json", "search-index-lite.json")
        stats_loaded = self.load_json(self.docs, "stats.json", "stats.json")
        summary_loaded = self.load_json(self.root, "registry_summary.json", "registry_summary.json")
        lite_total = stats_registry = summary_total = None
        if lite_loaded:
            _, lite = lite_loaded
            self.require_schema(lite, "search-index-lite.json")
            self.require_fields(
                lite,
                "search-index-lite.json",
                required={
                    "schema_version",
                    "version",
                    "updated_at",
                    "total_count",
                    "included_count",
                    "limit",
                    "raw_count",
                    "dedupe_key",
                    "skills",
                },
                code="invalid_lite_shape",
            )
            lite_total = self.require_count(lite, "total_count", "search-index-lite.json")
            included = self.require_count(lite, "included_count", "search-index-lite.json")
            self.require_count(lite, "limit", "search-index-lite.json")
            self.require_count(lite, "raw_count", "search-index-lite.json")
            skills = lite.get("skills")
            if not isinstance(skills, list) or included is None or len(skills) != included:
                self.error("lite_payload_count_mismatch", "search-index-lite.json", "skills length differs from included_count")
            if lite_total is not None and included is not None and included > lite_total:
                self.error("lite_count_mismatch", "search-index-lite.json", "included_count exceeds total_count")
        if stats_loaded:
            _, stats = stats_loaded
            self.require_schema(stats, "stats.json")
            required_stats = {
                "schema_version",
                "registry_skill_count_dedup",
                "indexed_skill_count_scan_shape",
                "lite_index_count",
            }
            missing_stats = sorted(required_stats - stats.keys())
            if missing_stats:
                self.error(
                    "invalid_stats_shape",
                    "stats.json",
                    f"missing fields: {','.join(missing_stats)}",
                )
            stats_registry = self.require_count(stats, "registry_skill_count_dedup", "stats.json")
            scan_count = self.require_count(stats, "indexed_skill_count_scan_shape", "stats.json")
            stable_count = self.require_count(stats, "lite_index_count", "stats.json")
            if scan_count is not None:
                self.totals["scan"].append(scan_count)
            if stable_count is not None:
                self.totals["stable"].append(stable_count)
        if summary_loaded:
            _, summary = summary_loaded
            self.require_schema(summary, "registry_summary.json")
            self.require_fields(
                summary,
                "registry_summary.json",
                required={"schema_version", "registry_updated_at", "total_count", "plugin_count"},
                code="invalid_summary_shape",
            )
            summary_total = self.require_count(summary, "total_count", "registry_summary.json")
            self.require_count(summary, "plugin_count", "registry_summary.json")
        return lite_total, stats_registry, summary_total

    def validate(self) -> ValidationReport:
        registry_total = self.check_sharded(
            self.root, "registry.json", kind="registry", aliases={"registry_skill_count_dedup"}
        )
        search_total = self.check_sharded(self.docs, "search-index.json", kind="search", aliases={"t"})
        signal_totals = [
            self.check_sharded(self.docs, f"{name}-index.json", kind="signal", aliases={"count"})
            for name in ("quality", "security", "ranking")
        ]
        category_total = self.check_categories()
        lite_total, stats_registry, summary_total = self.check_simple_documents()
        self.totals["registry"].extend(
            value for value in (registry_total, stats_registry, summary_total) if value is not None
        )
        self.totals["scan"].extend(value for value in (search_total, category_total) if value is not None)
        self.totals["stable"].extend(
            value for value in (lite_total, *signal_totals) if value is not None
        )
        for group, values in self.totals.items():
            if not values or len(set(values)) != 1:
                self.error("group_total_mismatch", group, f"same-set totals differ: {values}")
        return ValidationReport(
            checked_files=len(self.checked),
            totals=self.totals,
            errors=self.errors,
        )


def validate_artifact_api(root: Path, docs_dir: Path | None = None) -> ValidationReport:
    resolved_root = root.resolve()
    resolved_docs = (docs_dir or resolved_root / "docs").resolve()
    try:
        resolved_docs.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("docs-dir must be inside root") from exc
    return ArtifactValidator(resolved_root, resolved_docs).validate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--docs-dir", type=Path)
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()
    report = validate_artifact_api(args.root, args.docs_dir)
    payload = report.as_dict()
    if args.output_json:
        args.output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"artifact-api status={payload['status']} checked_files={report.checked_files} "
        f"errors={len(report.errors)}"
    )
    for error in report.errors:
        print(f"{error.code}: {error.path}: {error.message}")
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
