#!/usr/bin/env python3
"""
Shared utilities for skill registry scripts.
"""

import re


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
