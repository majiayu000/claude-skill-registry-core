#!/usr/bin/env python3
"""Verify canonical bundled assets against their recorded GitHub sources."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from audit_skill_assets import canonical_source_identity
from sync_download_support import exact_source_branch

SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
LIVENESS_STATUSES = {"live", "partial", "moved", "gone"}
ERROR_STATUSES = {"verification_error", "local_error", "apply_error"}


class GitHubApiError(RuntimeError):
    """A GitHub response that can be classified without hiding its status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GitHubClient:
    def __init__(self, token: str = ""):
        self.token = token

    def get_json(self, path: str) -> dict:
        request = urllib.request.Request(
            f"https://api.github.com{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "claude-skill-registry-asset-verifier",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise GitHubApiError(exc.code, f"GitHub API {exc.code}: {detail}") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubApiError(0, f"GitHub API transport failure: {exc}") from exc
        if not isinstance(payload, dict):
            raise GitHubApiError(0, "GitHub API returned a non-object response")
        return payload

    def repository(self, repo: str) -> dict:
        return self.get_json(f"/repos/{repo}")

    def branch_sha(self, repo: str, branch: str) -> str:
        encoded = urllib.parse.quote(branch, safe="")
        payload = self.get_json(f"/repos/{repo}/branches/{encoded}")
        if payload.get("name") != branch:
            raise GitHubApiError(0, "GitHub branch response identity mismatch")
        commit = payload.get("commit")
        sha = commit.get("sha") if isinstance(commit, dict) else None
        if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
            raise GitHubApiError(0, "GitHub commit response lacks a valid SHA")
        return sha.lower()

    def tree(self, repo: str, sha: str) -> set[str]:
        payload = self.get_json(f"/repos/{repo}/git/trees/{sha}?recursive=1")
        if payload.get("truncated") is True:
            raise GitHubApiError(0, "GitHub tree response is truncated")
        entries = payload.get("tree")
        if not isinstance(entries, list):
            raise GitHubApiError(0, "GitHub tree response lacks entries")
        paths = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise GitHubApiError(0, "GitHub tree contains a malformed entry")
            path = entry.get("path")
            entry_type = entry.get("type")
            if not isinstance(path, str) or not isinstance(entry_type, str):
                raise GitHubApiError(0, "GitHub tree entry lacks path or type")
            if entry_type == "blob":
                paths.add(path)
        return paths


@dataclass(frozen=True)
class Target:
    stable_key: str
    repo: str
    source_path: str
    branch: str
    pinned_sha: str
    verified_at: str
    bundled_files: tuple[str, ...]
    metadata_path: Path
    metadata_hash: str
    metadata: dict


def _metadata_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_bundle_path(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        return ""
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return ""
    if normalized in {"SKILL.md", "metadata.json"}:
        return ""
    return normalized


def _actual_bundled_files(skill_dir: Path) -> list[str]:
    files = []
    def raise_walk_error(error: OSError) -> None:
        raise error

    for dirpath, dirnames, filenames in os.walk(skill_dir, onerror=raise_walk_error):
        directory = Path(dirpath)
        for name in dirnames + filenames:
            path = directory / name
            relative = path.relative_to(skill_dir).as_posix()
            if path.is_symlink():
                raise ValueError(f"archive contains a symbolic link: {relative}")
            if path.is_file() and relative not in {"SKILL.md", "metadata.json"}:
                files.append(relative)
    return sorted(files)


def _looks_like_target(metadata: object, skill_dir: Path) -> bool:
    if isinstance(metadata, dict) and (
        metadata.get("archive_mode") == "directory"
        or bool(metadata.get("bundled_files"))
        or bool(metadata.get("github_commit_sha"))
        or bool(metadata.get("assets_verified_at"))
    ):
        return True
    return any(
        path.is_file() and path.name not in {"SKILL.md", "metadata.json"}
        for path in skill_dir.rglob("*")
    )


def _target_from_metadata(metadata_path: Path, skills_dir: Path) -> Target:
    skill_dir = metadata_path.parent
    category_dir = skill_dir.parent
    if category_dir.is_symlink() or skill_dir.is_symlink():
        raise ValueError("canonical archive category and skill directories cannot be symlinks")
    try:
        skill_dir.resolve().relative_to(skills_dir.resolve())
    except ValueError as exc:
        raise ValueError("canonical archive path escapes skills root") from exc
    relative_dir = skill_dir.relative_to(skills_dir).as_posix()
    if len(PurePosixPath(relative_dir).parts) != 2:
        raise ValueError("canonical archive path must be <category>/<skill>")
    if metadata_path.is_symlink() or not metadata_path.is_file():
        raise ValueError("metadata.json must be a regular file")
    skill_path = skill_dir / "SKILL.md"
    if skill_path.is_symlink() or not skill_path.is_file():
        raise ValueError("SKILL.md must be a regular file")
    raw = metadata_path.read_bytes()
    try:
        metadata = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid metadata JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    canonical_path = metadata.get("path")
    legacy_path = metadata.get("github_path")
    for field, value in (("path", canonical_path), ("github_path", legacy_path)):
        if field in metadata and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field} must be a non-empty string")
    if "path" in metadata and "github_path" in metadata and canonical_path != legacy_path:
        raise ValueError("conflicting path and github_path identities")
    repo, source_path, source_error = canonical_source_identity(
        metadata.get("repo"), canonical_path if "path" in metadata else legacy_path
    )
    if source_error:
        raise ValueError(source_error)
    canonical_branch = metadata.get("github_branch")
    legacy_branch = metadata.get("branch")
    for field, value in (("github_branch", canonical_branch), ("branch", legacy_branch)):
        if field in metadata and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{field} must be a non-empty string")
    if (
        "github_branch" in metadata
        and "branch" in metadata
        and canonical_branch != legacy_branch
    ):
        raise ValueError("conflicting github_branch and branch identities")
    branch = exact_source_branch(metadata)
    if not branch:
        raise ValueError("missing exact source branch")
    if SHA_PATTERN.fullmatch(branch):
        raise ValueError("source branch cannot be a raw commit SHA")
    pinned_sha = metadata.get("github_commit_sha")
    if not isinstance(pinned_sha, str) or not SHA_PATTERN.fullmatch(pinned_sha):
        raise ValueError("missing immutable github_commit_sha")
    verified_at = metadata.get("assets_verified_at")
    if not isinstance(verified_at, str) or not verified_at.strip():
        raise ValueError("missing assets_verified_at")
    declared = metadata.get("bundled_files")
    if not isinstance(declared, list) or not declared:
        raise ValueError("bundled_files must be a non-empty list")
    normalized = [_safe_bundle_path(value) for value in declared]
    if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
        raise ValueError("bundled_files contains an invalid or duplicate path")
    actual = _actual_bundled_files(skill_dir)
    if sorted(normalized) != actual:
        raise ValueError(f"bundled_files mismatch: declared={sorted(normalized)}, actual={actual}")
    return Target(
        stable_key=f"{repo}:{source_path}",
        repo=repo,
        source_path=source_path,
        branch=branch,
        pinned_sha=pinned_sha.lower(),
        verified_at=verified_at,
        bundled_files=tuple(sorted(normalized)),
        metadata_path=metadata_path,
        metadata_hash=_metadata_hash(raw),
        metadata=metadata,
    )


def load_targets(skills_dir: Path) -> tuple[list[Target], list[dict]]:
    root = skills_dir.resolve()
    targets = []
    errors = []
    seen = set()
    metadata_paths = []
    try:
        category_paths = sorted(root.iterdir())
    except OSError as exc:
        return [], [{"stable_key": str(skills_dir), "status": "local_error", "error": str(exc)}]
    for category_path in category_paths:
        if category_path.is_symlink():
            errors.append({
                "stable_key": relative_path(category_path, root),
                "status": "local_error",
                "error": "canonical archive category directory cannot be a symlink",
            })
            continue
        if not category_path.is_dir():
            continue
        try:
            skill_paths = sorted(category_path.iterdir())
        except OSError as exc:
            errors.append({
                "stable_key": relative_path(category_path, root),
                "status": "local_error",
                "error": str(exc)[:500],
            })
            continue
        for skill_path in skill_paths:
            if skill_path.is_symlink():
                errors.append({
                    "stable_key": relative_path(skill_path, root),
                    "status": "local_error",
                    "error": "canonical archive skill directory cannot be a symlink",
                })
            elif skill_path.is_dir() and (skill_path / "metadata.json").exists():
                metadata_paths.append(skill_path / "metadata.json")
    for metadata_path in metadata_paths:
        skill_dir = metadata_path.parent
        try:
            raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raw_metadata = None
        if not _looks_like_target(raw_metadata, skill_dir):
            continue
        try:
            target = _target_from_metadata(metadata_path, root)
            if target.stable_key in seen:
                raise ValueError(f"duplicate stable key: {target.stable_key}")
            seen.add(target.stable_key)
            targets.append(target)
        except (OSError, ValueError) as exc:
            errors.append({
                "stable_key": relative_path(metadata_path, root),
                "status": "local_error",
                "error": str(exc)[:500],
            })
    return targets, errors


def relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _error_rows(targets: list[Target], status: str, error: Exception) -> list[dict]:
    return [
        {
            "stable_key": target.stable_key,
            "repo": target.repo,
            "source_path": target.source_path,
            "status": status,
            "pinned_source_sha": target.pinned_sha,
            "error": str(error)[:500],
        }
        for target in targets
    ]


def verify_targets(targets: list[Target], client: GitHubClient, checked_at: str) -> list[dict]:
    rows = []
    by_repo: dict[str, list[Target]] = collections.defaultdict(list)
    for target in targets:
        by_repo[target.repo].append(target)
    for repo, repo_targets in sorted(by_repo.items()):
        try:
            repository = client.repository(repo)
            full_name = repository.get("full_name")
            if not isinstance(full_name, str):
                raise GitHubApiError(0, "GitHub repository response lacks full_name")
            if full_name.casefold() != repo.casefold():
                raise GitHubApiError(301, "GitHub repository identity moved or mismatched")
        except GitHubApiError as exc:
            status = "gone" if exc.status == 404 else "moved" if exc.status == 301 else "verification_error"
            rows.extend(_error_rows(repo_targets, status, exc))
            continue
        by_branch: dict[str, list[Target]] = collections.defaultdict(list)
        for target in repo_targets:
            by_branch[target.branch].append(target)
        for branch, branch_targets in sorted(by_branch.items()):
            try:
                current_sha = client.branch_sha(repo, branch)
            except GitHubApiError as exc:
                status = "moved" if exc.status == 404 else "verification_error"
                rows.extend(_error_rows(branch_targets, status, exc))
                continue
            try:
                upstream_paths = client.tree(repo, current_sha)
            except GitHubApiError as exc:
                rows.extend(_error_rows(branch_targets, "verification_error", exc))
                continue
            for target in branch_targets:
                source_dir = PurePosixPath(target.source_path).parent
                expected_assets = {
                    (source_dir / bundled_file).as_posix()
                    for bundled_file in target.bundled_files
                }
                missing_assets = sorted(expected_assets - upstream_paths)
                if target.source_path not in upstream_paths:
                    status = "moved"
                elif missing_assets:
                    status = "partial"
                else:
                    status = "live"
                rows.append({
                    "stable_key": target.stable_key,
                    "repo": repo,
                    "source_path": target.source_path,
                    "branch": branch,
                    "status": status,
                    "pinned_source_sha": target.pinned_sha,
                    "current_source_sha": current_sha,
                    "checked_at": checked_at,
                    "missing_assets": missing_assets,
                    "metadata_path": str(target.metadata_path),
                    "metadata_hash": target.metadata_hash,
                })
    return rows


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def apply_updates(targets: list[Target], rows: list[dict], checked_at: str) -> list[str]:
    target_by_key = {target.stable_key: target for target in targets}
    updates = []
    for row in rows:
        if row["status"] not in LIVENESS_STATUSES:
            continue
        target = target_by_key[row["stable_key"]]
        try:
            current = target.metadata_path.read_bytes()
        except OSError as exc:
            return [f"{target.stable_key}: unable to read metadata before apply: {exc}"]
        if _metadata_hash(current) != target.metadata_hash:
            return [f"{target.stable_key}: metadata changed after verification"]
        metadata = dict(target.metadata)
        metadata["asset_liveness"] = row["status"]
        metadata["assets_liveness_checked_at"] = checked_at
        current_sha = row.get("current_source_sha")
        if current_sha:
            metadata["assets_liveness_sha"] = current_sha
        else:
            metadata.pop("assets_liveness_sha", None)
        content = (json.dumps(metadata, indent=2, ensure_ascii=False) + "\n").encode()
        updates.append((target, current, content))
    applied = []
    try:
        for target, original, content in updates:
            _write_atomic(target.metadata_path, content)
            applied.append((target, original))
    except Exception as exc:  # noqa: BLE001 — restore the entire applied batch
        recovery_errors = []
        for target, original in reversed(applied):
            try:
                _write_atomic(target.metadata_path, original)
            except Exception as recovery_exc:  # noqa: BLE001 — report every failed restore
                recovery_errors.append(f"{target.stable_key}: {recovery_exc}")
        detail = f"metadata apply failed: {type(exc).__name__}: {exc}"
        if recovery_errors:
            detail += f"; recovery failed: {recovery_errors}"
        return [detail]
    return []


def summarize(rows: list[dict]) -> dict[str, int]:
    counts = collections.Counter(row.get("status", "invalid") for row in rows)
    return dict(sorted(counts.items()))


def gate_errors(
    report: dict,
    *,
    max_decayed_percent: float,
    max_error_percent: float,
    min_targets: int,
) -> list[str]:
    rows = report.get("rows")
    summary = report.get("summary")
    if not isinstance(rows, list) or not isinstance(summary, dict):
        return ["report rows or summary is malformed"]
    actual_summary = summarize(rows)
    if summary != actual_summary:
        return [f"report summary mismatch: expected {actual_summary}, got {summary}"]
    unknown = sorted(set(actual_summary) - LIVENESS_STATUSES - ERROR_STATUSES)
    if unknown:
        return [f"report contains unknown statuses: {unknown}"]
    total = report.get("target_count")
    if not isinstance(total, int) or total < 0:
        return ["report target_count is malformed"]
    errors = []
    if total < min_targets:
        errors.append(f"verified target count {total} is below minimum {min_targets}")
    denominator = max(total, 1)
    decayed = sum(actual_summary.get(status, 0) for status in {"partial", "moved", "gone"})
    failed = actual_summary.get("verification_error", 0)
    decay_percent = decayed * 100 / denominator
    error_percent = failed * 100 / denominator
    if decay_percent > max_decayed_percent:
        errors.append(
            f"asset decay {decay_percent:.2f}% exceeds {max_decayed_percent:.2f}% "
            f"({decayed}/{total})"
        )
    if actual_summary.get("local_error", 0):
        errors.append("canonical archive validation failed")
    if actual_summary.get("apply_error", 0):
        errors.append("metadata apply or rollback failed")
    if error_percent > max_error_percent:
        errors.append(
            f"verification errors {error_percent:.2f}% exceed {max_error_percent:.2f}% "
            f"({failed}/{total})"
        )
    return errors


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, default=Path("skills"))
    parser.add_argument("--report", type=Path, default=Path("asset-liveness-report.json"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-decayed-percent", type=float, default=35.0)
    parser.add_argument("--max-error-percent", type=float, default=10.0)
    parser.add_argument("--min-targets", type=int, default=1)
    args = parser.parse_args(argv)
    if not 0 <= args.max_decayed_percent <= 100:
        parser.error("--max-decayed-percent must be between 0 and 100")
    if not 0 <= args.max_error_percent <= 100:
        parser.error("--max-error-percent must be between 0 and 100")
    if args.min_targets < 1:
        parser.error("--min-targets must be at least 1")
    return args


def main(argv: list[str] | None = None, *, client: GitHubClient | None = None) -> int:
    args = parse_args(argv)
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    targets, local_errors = load_targets(args.skills_dir)
    api_client = client or GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
    rows = local_errors + verify_targets(targets, api_client, checked_at)
    apply_errors = apply_updates(targets, rows, checked_at) if args.apply else []
    rows.extend({"stable_key": "apply", "status": "apply_error", "error": error} for error in apply_errors)
    report = {
        "schema_version": 1,
        "checked_at": checked_at,
        "target_count": len(targets) + len(local_errors),
        "repo_count": len({target.repo for target in targets}),
        "applied": args.apply and not apply_errors,
        "summary": summarize(rows),
        "rows": rows,
    }
    gate_failures = gate_errors(
        report,
        max_decayed_percent=args.max_decayed_percent,
        max_error_percent=args.max_error_percent,
        min_targets=args.min_targets,
    )
    report["gate"] = {
        "passed": not gate_failures,
        "errors": gate_failures,
        "max_decayed_percent": args.max_decayed_percent,
        "max_error_percent": args.max_error_percent,
        "min_targets": args.min_targets,
    }
    _write_atomic(args.report, (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode())
    print(json.dumps({"summary": report["summary"], "gate": report["gate"]}, indent=2))
    return 0 if not gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
