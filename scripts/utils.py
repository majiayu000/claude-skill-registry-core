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


def ensure_unique_dir(parent: Path, base_name: str, key: str = "") -> Path:
    """
    Ensure directory name is unique on case-insensitive filesystems.
    If a conflict exists, append a stable suffix based on the skill key.
    """
    parent = Path(parent)
    base = normalize_name(base_name)
    parent.mkdir(parents=True, exist_ok=True)

    existing = {}
    for d in parent.iterdir():
        if d.is_dir():
            existing.setdefault(d.name.lower(), []).append(d)

    # No conflict
    if base.lower() not in existing:
        return parent / base

    # Reuse existing dir if it matches the same skill key
    for d in existing.get(base.lower(), []):
        meta_key = _metadata_key(d / "metadata.json")
        if key and meta_key == key:
            return d

    # Create a unique suffixed directory name
    suffix = f"__dup-{_short_hash(key or base)}"
    candidate = f"{base}{suffix}"
    counter = 2
    while candidate.lower() in existing:
        candidate = f"{base}{suffix}-{counter}"
        counter += 1

    return parent / candidate
