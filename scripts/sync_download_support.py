#!/usr/bin/env python3
"""Pure helpers for exact and bundled sync downloads."""

from sync_pipeline_support import MAX_BUNDLED_FILES_PER_SKILL, MAX_BUNDLED_TOTAL_BYTES
from utils import build_legal_metadata


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
    raw_branch = skill.get("github_branch") or skill.get("branch")
    if not isinstance(raw_branch, str):
        return ""
    branch = raw_branch.strip()
    if not branch or len(branch) > 255:
        return ""
    if any(ord(character) < 33 or ord(character) == 127 for character in branch):
        return ""
    return branch


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
