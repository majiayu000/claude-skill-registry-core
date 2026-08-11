#!/usr/bin/env python3
"""Pure helpers for exact and bundled sync downloads."""

import hashlib
import re
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

from security_blocklist import blocked_metadata_source
from sync_pipeline_support import (
    GITHUB_API_BASE,
    MAX_BUNDLED_FILES_PER_SKILL,
    MAX_BUNDLED_TOTAL_BYTES,
    BundledListingError,
    bundled_relative_path,
    is_safe_bundled_file,
    should_recurse_bundled_dir,
    skill_source_dir,
)
from utils import build_legal_metadata

SHA_PATTERN = re.compile(r"[0-9a-fA-F]{40}")


def select_bundled_file_entries(candidates: list[dict]) -> tuple[list[dict], bool]:
    """Apply bundle limits and report whether eligible files were omitted."""
    selected = []
    total_size = 0
    truncated = False
    for entry in sorted(candidates, key=lambda item: item["relative_path"]):
        if len(selected) >= MAX_BUNDLED_FILES_PER_SKILL:
            truncated = True
            continue
        if total_size + entry["size"] > MAX_BUNDLED_TOTAL_BYTES:
            truncated = True
            continue
        selected.append(entry)
        total_size += entry["size"]
    return selected, truncated


def exact_source_branch(skill: dict) -> str:
    """Return a safe recorded source branch for immutable exact downloads."""
    branches = []
    for field in ("github_branch", "branch"):
        if field not in skill:
            continue
        raw_branch = skill[field]
        if not isinstance(raw_branch, str):
            return ""
        branch = raw_branch.strip()
        if not branch or len(branch) > 255:
            return ""
        if any(ord(character) < 33 or ord(character) == 127 for character in branch):
            return ""
        if re.fullmatch(r"[0-9a-fA-F]{40}", branch):
            return ""
        branches.append(branch)
    if not branches or any(branch != branches[0] for branch in branches[1:]):
        return ""
    return branches[0]


async def resolve_exact_commit_sha(
    session: Any,
    repo: str,
    branch: str,
    *,
    timeout: Any,
    security_blocklist: dict,
    repo_cache: dict[str, str],
    commit_cache: dict[tuple[str, str], str],
) -> str:
    """Resolve an exact branch after proving the canonical repository identity."""
    cache_key = (repo, branch)
    if cache_key in commit_cache:
        return commit_cache[cache_key]

    canonical_repo = repo_cache.get(repo)
    if canonical_repo is None:
        async with session.get(f"{GITHUB_API_BASE}/repos/{repo}", timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(
                    f"repository resolution failed with status {response.status}"
                )
            payload = await response.json()
        canonical_repo = payload.get("full_name") if isinstance(payload, dict) else ""
        if (
            not isinstance(canonical_repo, str)
            or canonical_repo.casefold() != repo.casefold()
        ):
            raise RuntimeError("repository resolution returned a different canonical identity")
        if blocked_metadata_source({"repo": canonical_repo}, security_blocklist):
            raise RuntimeError("repository resolution returned a blocked canonical identity")
        repo_cache[repo] = canonical_repo

    branch_url = (
        f"{GITHUB_API_BASE}/repos/{canonical_repo}/branches/{quote(branch, safe='')}"
    )
    async with session.get(branch_url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"branch resolution failed with status {response.status}")
        payload = await response.json()
    resolved_branch = payload.get("name") if isinstance(payload, dict) else ""
    commit = payload.get("commit") if isinstance(payload, dict) else None
    commit_sha = commit.get("sha") if isinstance(commit, dict) else ""
    if resolved_branch != branch:
        raise RuntimeError("branch resolution returned a different branch identity")
    if not isinstance(commit_sha, str) or not SHA_PATTERN.fullmatch(commit_sha):
        raise RuntimeError("branch resolution returned an invalid commit SHA")
    commit_cache[cache_key] = commit_sha.lower()
    return commit_sha.lower()


async def collect_pinned_tree_entries(
    session: Any,
    repo: str,
    commit_sha: str,
    resolved_skill_path: str,
    *,
    timeout: Any,
    tree_cache: dict[tuple[str, str], list[dict]],
) -> tuple[list[dict], bool]:
    """Collect a complete, regular-file-only bundle from one immutable Git tree."""
    cache_key = (repo, commit_sha)
    entries = tree_cache.get(cache_key)
    if entries is None:
        url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{commit_sha}?recursive=1"
        try:
            async with session.get(url, timeout=timeout) as response:
                if response.status != 200:
                    raise BundledListingError(".", f"tree status {response.status}")
                payload = await response.json()
        except BundledListingError:
            raise
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            raise BundledListingError(".", reason) from exc
        if not isinstance(payload, dict) or payload.get("truncated") is True:
            raise BundledListingError(".", "truncated or malformed Git tree")
        entries = payload.get("tree")
        if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
            raise BundledListingError(".", "malformed Git tree entries")
        tree_cache[cache_key] = entries

    source_entry = next(
        (entry for entry in entries if entry.get("path") == resolved_skill_path),
        None,
    )
    if (
        not isinstance(source_entry, dict)
        or source_entry.get("type") != "blob"
        or source_entry.get("mode") not in {"100644", "100755"}
    ):
        raise BundledListingError(resolved_skill_path, "source skill is not a regular blob")

    source_dir = skill_source_dir(resolved_skill_path)
    candidates = []
    for entry in entries:
        repo_path = entry.get("path")
        if not isinstance(repo_path, str) or repo_path == resolved_skill_path:
            continue
        rel_path = bundled_relative_path(source_dir, repo_path)
        if not rel_path:
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = -1
        parts = PurePosixPath(rel_path).parts
        in_support_scope = is_safe_bundled_file(rel_path, size) or (
            len(parts) > 1 and should_recurse_bundled_dir(parts[0])
        )
        entry_type = entry.get("type")
        mode = entry.get("mode")
        if entry_type == "tree" and mode == "040000":
            continue
        if entry_type != "blob" or mode not in {"100644", "100755"}:
            if in_support_scope:
                raise BundledListingError(repo_path, f"unsupported Git object mode {mode}")
            continue
        if not is_safe_bundled_file(rel_path, size):
            continue
        blob_sha = entry.get("sha")
        if not isinstance(blob_sha, str) or not SHA_PATTERN.fullmatch(blob_sha):
            raise BundledListingError(repo_path, "regular blob lacks a valid object ID")
        candidates.append({
            "repo_path": repo_path,
            "relative_path": rel_path,
            "download_url": "",
            "size": size,
            "sha": blob_sha.lower(),
        })
    return select_bundled_file_entries(candidates)


def content_matches_git_blob(entry: dict, content: bytes) -> bool:
    """Match both the advertised byte length and immutable Git blob object ID."""
    expected_size = entry.get("size")
    expected_sha = entry.get("sha")
    if len(content) != expected_size or not isinstance(expected_sha, str):
        return False
    blob_header = f"blob {len(content)}\0".encode("ascii")
    actual_sha = hashlib.sha1(blob_header + content, usedforsecurity=False).hexdigest()
    return actual_sha == expected_sha.casefold()


def classify_download_result(skill: dict, result: object) -> tuple[bool, str]:
    """Classify an async task result and retain unexpected exception details."""
    if isinstance(result, BaseException) and not isinstance(result, Exception):
        raise result
    if result is True:
        return True, ""
    if isinstance(result, Exception):
        name = (skill.get("name") or "unknown").strip() or "unknown"
        return False, f"{name}: {type(result).__name__}: {result}"
    return False, ""


def build_archived_skill_metadata(
    skill: dict,
    *,
    name: str,
    repo: str,
    resolved_path: str,
    branch: str,
    dir_name: str,
    bundled_files: list[str],
    commit_sha: str = "",
    assets_verified_at: str = "",
) -> dict:
    """Build downloader metadata without coupling it to network control flow."""
    legal_meta = build_legal_metadata(
        repo=repo,
        path=resolved_path,
        branch=branch,
        source_url=skill.get("source_url", ""),
        author=skill.get("author", ""),
        license_name=skill.get("license", ""),
        copyright_text=skill.get("copyright", ""),
        permission_note=skill.get("permission_note", ""),
        distribution=skill.get("distribution", ""),
    )
    return {
        "name": name,
        "description": skill.get("description", ""),
        "repo": repo,
        "path": resolved_path,
        "github_branch": branch,
        **(
            {"github_commit_sha": commit_sha, "assets_verified_at": assets_verified_at}
            if commit_sha
            else {}
        ),
        "category": skill.get("category", ""),
        "tags": skill.get("tags", []),
        "stars": skill.get("stars", 0),
        "source": skill.get("source", ""),
        "dir_name": dir_name,
        "archive_mode": "directory" if bundled_files else "skill-md",
        "bundled_files": bundled_files,
        **legal_meta,
    }
