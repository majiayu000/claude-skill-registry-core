#!/usr/bin/env python3
"""
Build the unified registry.json from all sources.
Also creates category-based indexes.
"""

import json
import os
from datetime import datetime
from pathlib import Path

REGISTRY_DIR = Path(__file__).parent.parent
SOURCES_DIR = REGISTRY_DIR / "sources"
CATEGORIES_DIR = REGISTRY_DIR / "categories"


def load_sources():
    """Load all source files."""
    sources = []
    for source_file in SOURCES_DIR.glob("*.json"):
        with open(source_file) as f:
            sources.append(json.load(f))
    return sources


def build_registry(sources):
    """Build the unified registry from all sources."""
    all_skills = []

    def derive_name(skill):
        """Best-effort name derivation when sources omit it."""
        name = skill.get("name")
        if name:
            return name
        path = skill.get("path", "")
        if path:
            base = os.path.basename(path.rstrip("/"))
            if base.lower().endswith(".md"):
                base = base[:-3]
            if base:
                return base
        repo = skill.get("repo", "")
        if repo:
            return repo.split("/")[-1]
        return "unknown-skill"

    for source in sources:
        source_repo = source.get("repo", "")
        source_name = source.get("name", "Unknown")

        for skill in source.get("skills", []):
            # Build full install command
            if source_repo:
                # Monorepo like anthropics/skills
                path = skill.get("path", "")
                if path:
                    install_cmd = f"{source_repo}/{path.split('/')[-1]}"
                else:
                    install_cmd = source_repo
            else:
                # Individual repo
                repo = skill.get("repo", "")
                path = skill.get("path", "")
                if path:
                    install_cmd = f"{repo}/{path}"
                else:
                    install_cmd = repo

            all_skills.append({
                "name": derive_name(skill),
                "description": skill.get("description", ""),
                "install": install_cmd,
                "repo": skill.get("repo", source_repo),
                "path": skill.get("path", ""),
                "category": skill.get("category", "other"),
                "tags": skill.get("tags", []),
                "source": source_name,
                "stars": skill.get("stars", 0),
                "featured": skill.get("featured", False),
            })

    # Sort by featured first, then by name
    all_skills.sort(key=lambda x: (not x["featured"], x["name"].lower()))

    return {
        "version": "1.0.0",
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "total_count": len(all_skills),
        "skills": all_skills,
    }


def sanitize_category(category: str) -> str:
    """Sanitize category name for use as filename."""
    # Replace / and other problematic characters with -
    return category.replace("/", "-").replace("\\", "-").replace(":", "-")


def build_category_indexes(registry):
    """Build category-based indexes."""
    categories = {}

    for skill in registry["skills"]:
        category = skill["category"]
        # Sanitize category for grouping
        safe_category = sanitize_category(category)
        if safe_category not in categories:
            categories[safe_category] = {
                "category": safe_category,
                "updated_at": registry["updated_at"],
                "skills": [],
            }
        categories[safe_category]["skills"].append(skill)

    # Ensure categories directory exists
    CATEGORIES_DIR.mkdir(exist_ok=True)

    # Write each category file
    for category, data in categories.items():
        data["count"] = len(data["skills"])
        output_file = CATEGORIES_DIR / f"{category}.json"
        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  Created {output_file.name} ({data['count']} skills)")

    # Write categories index
    categories_index = {
        "updated_at": registry["updated_at"],
        "categories": [
            {"name": cat, "count": len(data["skills"])}
            for cat, data in sorted(categories.items())
        ],
    }
    with open(CATEGORIES_DIR / "index.json", "w") as f:
        json.dump(categories_index, f, indent=2)


def main():
    print("Building Claude Skills Registry...")
    print()

    # Load sources
    print("Loading sources...")
    sources = load_sources()
    print(f"  Found {len(sources)} source(s)")

    # Build registry
    print("Building registry...")
    registry = build_registry(sources)
    print(f"  Total skills: {registry['total_count']}")

    # Write registry.json
    output_file = REGISTRY_DIR / "registry.json"
    with open(output_file, "w") as f:
        json.dump(registry, f, indent=2)
    print(f"  Written to {output_file}")

    # Build category indexes
    print("Building category indexes...")
    build_category_indexes(registry)

    print()
    print("Done!")


if __name__ == "__main__":
    main()
