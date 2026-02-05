#!/usr/bin/env python3
"""
Shared utilities for skill registry scripts.
"""

import re
import hashlib
import json
from pathlib import Path


def normalize_name(name: str) -> str:
    """
    Normalize skill/category name: lowercase, hyphens, max 64 chars.

    This prevents case-sensitivity issues on macOS/Windows filesystems.
    All scripts MUST use this function when creating skill directories.

    Examples:
        "Architect" -> "architect"
        "LangChain" -> "langchain"
        "Go-to-Market-Planner" -> "go-to-market-planner"
        "My Skill Name" -> "my-skill-name"
    """
    if not name:
        return "unknown"
    # Convert to lowercase, replace non-alphanumeric with hyphens
    name = re.sub(r'[^a-z0-9]+', '-', name.lower())
    # Strip leading/trailing hyphens, collapse consecutive hyphens
    name = re.sub(r'-+', '-', name).strip('-')
    # Max 64 chars
    return name[:64] if name else "unknown"


def normalize_category(category: str) -> str:
    """
    Normalize category name for directory creation.
    Same as normalize_name but with 32 char limit.
    """
    if not category:
        return "other"
    name = re.sub(r'[^a-z0-9]+', '-', category.lower())
    name = re.sub(r'-+', '-', name).strip('-')
    return name[:32] if name else "other"


def build_skill_key(repo: str = "", path: str = "", name: str = "", category: str = "") -> str:
    """Build a stable key for a skill to detect duplicates."""
    repo = (repo or "").strip()
    path = (path or "").strip().lstrip("/")
    if repo and path:
        return f"{repo}:{path}"
    if repo:
        return repo
    if category or name:
        return f"{category}:{name}"
    return ""


def _short_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


def short_hash(value: str) -> str:
    """Public short hash helper."""
    return _short_hash(value)


def normalize_repo(repo: str) -> str:
    """Normalize GitHub repo to owner/repo format."""
    repo = (repo or "").strip()
    if repo.startswith("https://github.com/"):
        repo = repo[len("https://github.com/"):]
    return repo.strip("/")


def get_repo_suffix(repo: str) -> str:
    """Get a short suffix from repo: owner-repo."""
    repo = normalize_repo(repo)
    if not repo or "/" not in repo:
        return ""
    owner, repo_name = repo.split("/", 1)
    owner = normalize_name(owner)[:20]
    repo_name = normalize_name(repo_name)[:20]
    if not owner and not repo_name:
        return ""
    if not repo_name:
        return owner
    return f"{owner}-{repo_name}"


def build_dir_name(base_name: str, repo: str = "") -> str:
    """Build directory name using repo suffix when provided."""
    base = normalize_name(base_name)
    suffix = get_repo_suffix(repo)
    return f"{base}-{suffix}" if suffix else base


def _metadata_key(metadata_path: Path) -> str:
    if not metadata_path.exists():
        return ""
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return build_skill_key(
        meta.get("repo", ""),
        meta.get("path") or meta.get("github_path") or "",
        meta.get("name", ""),
        meta.get("category", ""),
    )


def ensure_unique_dir(parent: Path, base_name: str, key: str = "", repo: str = "") -> Path:
    """
    Ensure directory name is unique on case-insensitive filesystems.
    If a conflict exists, prefer repo suffix (name-owner-repo).
    """
    parent = Path(parent)
    base = normalize_name(base_name)
    parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    matched_by_key = None
    for d in parent.iterdir():
        if d.is_dir():
            existing.setdefault(d.name.lower(), []).append(d)
            if key and not matched_by_key:
                meta_key = _metadata_key(d / "metadata.json")
                if meta_key == key:
                    matched_by_key = d

    # No conflict
    if base.lower() not in existing:
        return parent / base

    # Reuse existing dir if it matches the same skill key
    if matched_by_key:
        return matched_by_key

    # Create a unique suffixed directory name
    suffix = get_repo_suffix(repo)
    if suffix and not base.endswith(f"-{suffix}"):
        candidate_base = f"{base}-{suffix}"
    elif suffix:
        candidate_base = base
    else:
        candidate_base = f"{base}-{_short_hash(key or base)}"
    candidate = candidate_base
    counter = 2
    while candidate.lower() in existing:
        candidate = f"{candidate_base}-{counter}"
        counter += 1

    return parent / candidate
