#!/usr/bin/env python3
"""
Build Search Index v2.0 - Generate lightweight search index

Supports directory structure:
- skills/{category}/{skill-name}/SKILL.md

Output files:
- search-index.json - Minimal index (~1-2MB gzip)
- categories/*.json - Category-based indexes
- featured.json - Top 100 skills by stars
"""

import json
import gzip
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List
import argparse
import logging
import re
import yaml

from utils import get_repo_suffix

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Category short codes
CATEGORY_CODES = {
    "development": "dev",
    "devops": "ops",
    "security": "sec",
    "documents": "doc",
    "design": "des",
    "testing": "tst",
    "product": "prd",
    "marketing": "mkt",
    "productivity": "pro",
    "data": "dat",
    "official": "off",
    "other": "oth",
}

# Known category directories (for scanning)
KNOWN_CATEGORIES = set(CATEGORY_CODES.keys()) | {"data", "other"}


def truncate_text(text: Any, max_length: int) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    if isinstance(text, list):
        text = " ".join(str(t) for t in text if t)
    text = str(text).strip().replace("\n", " ").replace("\r", "")
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def get_category_code(category: str) -> str:
    """Get short code for category."""
    if not category:
        return "oth"
    return CATEGORY_CODES.get(category.lower(), "oth")


def extract_description(skill_content: str) -> str:
    """Extract description from SKILL.md content."""
    # Try YAML frontmatter
    if skill_content.startswith("---"):
        try:
            end_idx = skill_content.find("---", 3)
            if end_idx > 0:
                frontmatter = skill_content[3:end_idx].strip()
                data = yaml.safe_load(frontmatter)
                if data and data.get("description"):
                    return data["description"]
        except Exception:
            pass

    # Try first paragraph
    lines = skill_content.split("\n")
    in_frontmatter = False

    for line in lines:
        line = line.strip()
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter:
            continue
        if line.startswith("#"):
            continue
        if line and not line.startswith("```") and len(line) > 20:
            line = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', line)
            line = re.sub(r'[*_`]', '', line)
            return line

    return ""


def scan_skills_v2(skills_dir: Path) -> List[Dict]:
    """Scan skills directory with structure: skills/{category}/{skill-name}/"""
    skills = []

    if not skills_dir.exists():
        logger.warning(f"Skills directory not found: {skills_dir}")
        return skills

    for category_dir in skills_dir.iterdir():
        if not category_dir.is_dir():
            continue
        if category_dir.name.startswith('.'):
            continue

        category_name = category_dir.name

        for skill_dir in category_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            metadata_file = skill_dir / "metadata.json"

            if not skill_md.exists():
                continue

            dir_name = skill_dir.name

            # Load metadata
            metadata = {}
            if metadata_file.exists():
                try:
                    metadata = json.loads(metadata_file.read_text(encoding='utf-8'))
                except Exception:
                    pass

            # Get skill name (from metadata or directory)
            name = metadata.get("name") or dir_name

            # Remove repo suffix from dir_name if metadata repo is available
            if name == dir_name:
                repo = metadata.get("repo", "")
                suffix = get_repo_suffix(repo)
                if suffix and dir_name.endswith(f"-{suffix}"):
                    name = dir_name[: -(len(suffix) + 1)]

            # Get description
            description = metadata.get("description", "")
            if not description:
                try:
                    content = skill_md.read_text(encoding='utf-8')
                    description = extract_description(content)
                except Exception:
                    pass
            if not description:
                description = f"Skill: {name}"

            # Get category
            category = metadata.get("category", category_name)

            # Build install path
            repo = metadata.get("repo", "")
            github_path = metadata.get("github_path", "")
            github_branch = metadata.get("github_branch", "main")  # Default to main

            if github_path and repo:
                install = f"{repo}/{github_path}"
            elif repo:
                install = repo
            else:
                install = f"unknown/{name}"

            skill_entry = {
                "name": name,
                "dir_name": dir_name,
                "description": description,
                "repo": repo,
                "path": github_path,
                "branch": github_branch,
                "category": category,
                "tags": metadata.get("tags", []),
                "stars": metadata.get("stars", 0),
                "source": metadata.get("source", "downloaded"),
                "install": install,
            }

            skills.append(skill_entry)

    return skills


def build_search_index(skills: List[Dict], output_dir: Path, source_name: str = "skills") -> Dict[str, Any]:
    """Build the lightweight search index."""
    logger.info(f"Building index from {len(skills)} {source_name}...")

    # Build minimal search index
    search_index = {
        "v": datetime.utcnow().strftime("%Y-%m-%d"),
        "t": len(skills),
        "s": []
    }

    # Category indexes
    categories: Dict[str, List[Dict]] = {}

    # Featured skills
    featured_skills = []

    for skill in skills:
        name = skill.get('name', '')
        description = skill.get('description', '')
        category = skill.get('category', 'other')
        tags = skill.get('tags', [])
        stars = skill.get('stars', 0)
        repo = skill.get('repo', '')
        install = skill.get('install', repo)
        branch = skill.get('branch', 'main')

        # Minimal record
        mini_record = {
            "n": name,
            "d": truncate_text(description, 80),
            "c": get_category_code(category),
            "g": tags[:5] if tags else [],
            "r": stars,
            "i": install,
            "b": branch  # branch for GitHub URL
        }
        search_index["s"].append(mini_record)

        # Full record
        full_record = {
            "name": name,
            "description": truncate_text(description, 200),
            "repo": repo,
            "path": skill.get('path', ''),
            "branch": branch,
            "category": category,
            "tags": tags[:10] if tags else [],
            "stars": stars,
            "install": install,
            "source": skill.get('source', '')
        }

        # Add to category
        if category not in categories:
            categories[category] = []
        categories[category].append(full_record)

        # Track for featured
        if stars > 0:
            featured_skills.append(full_record)

    # Sort by stars
    search_index["s"].sort(key=lambda x: x.get("r", 0), reverse=True)
    featured_skills.sort(key=lambda x: x.get("stars", 0), reverse=True)
    featured_skills = featured_skills[:100]

    # Create output directories
    output_dir.mkdir(parents=True, exist_ok=True)
    categories_dir = output_dir / "categories"
    categories_dir.mkdir(exist_ok=True)

    # Write search index
    search_index_path = output_dir / "search-index.json"
    with open(search_index_path, 'w', encoding='utf-8') as f:
        json.dump(search_index, f, ensure_ascii=False, separators=(',', ':'))

    # Write gzipped version
    search_index_gz_path = output_dir / "search-index.json.gz"
    with gzip.open(search_index_gz_path, 'wt', encoding='utf-8') as f:
        json.dump(search_index, f, ensure_ascii=False, separators=(',', ':'))

    logger.info(f"  search-index.json: {search_index_path.stat().st_size / 1024 / 1024:.2f} MB")
    logger.info(f"  search-index.json.gz: {search_index_gz_path.stat().st_size / 1024 / 1024:.2f} MB")

    # Write category indexes
    category_index = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "categories": []
    }

    for category, cat_skills in sorted(categories.items()):
        cat_skills.sort(key=lambda x: x.get("stars", 0), reverse=True)

        cat_data = {
            "category": category,
            "code": get_category_code(category),
            "count": len(cat_skills),
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "skills": cat_skills
        }

        cat_path = categories_dir / f"{category}.json"
        with open(cat_path, 'w', encoding='utf-8') as f:
            json.dump(cat_data, f, ensure_ascii=False, indent=2)

        category_index["categories"].append({
            "name": category,
            "code": get_category_code(category),
            "count": len(cat_skills)
        })

        logger.info(f"  categories/{category}.json: {len(cat_skills)} skills")

    # Write category index
    with open(categories_dir / "index.json", 'w', encoding='utf-8') as f:
        json.dump(category_index, f, ensure_ascii=False, indent=2)

    # Write featured
    featured_data = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "count": len(featured_skills),
        "skills": featured_skills
    }
    with open(output_dir / "featured.json", 'w', encoding='utf-8') as f:
        json.dump(featured_data, f, ensure_ascii=False, indent=2)

    logger.info(f"  featured.json: {len(featured_skills)} skills")

    # Write stats
    stats = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "total_skills": len(skills),
        "categories": len(categories),
        "featured_count": len(featured_skills),
        "index_size_bytes": search_index_path.stat().st_size,
        "index_size_gzip_bytes": search_index_gz_path.stat().st_size,
    }
    # Attach latest security scan summary if available
    security_report_path = output_dir / "security-report.json"
    if security_report_path.exists():
        try:
            with open(security_report_path, 'r', encoding='utf-8') as f:
                security_report = json.load(f)
            stats["security_scan"] = {
                "total": security_report.get("total"),
                "passed": security_report.get("passed"),
                "failed": security_report.get("failed"),
            }
        except Exception:
            stats["security_scan"] = {
                "total": None,
                "passed": None,
                "failed": None,
            }
    with open(output_dir / "stats.json", 'w', encoding='utf-8') as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)

    logger.info(f"\nIndex build complete!")
    logger.info(f"  Total skills: {len(skills)}")
    logger.info(f"  Categories: {len(categories)}")

    return stats


def load_from_registry(registry_path: Path) -> List[Dict]:
    """Load skills from registry.json (fallback mode)."""
    with open(registry_path, 'r', encoding='utf-8') as f:
        registry = json.load(f)

    skills = registry.get('skills', [])

    for skill in skills:
        repo = skill.get('repo', '')
        path = skill.get('path', '')
        skill['install'] = f"{repo}/{path}" if path else repo

    return skills


def main():
    parser = argparse.ArgumentParser(description='Build search index for skill registry')
    parser.add_argument('--skills-dir', '-s', default='skills', help='Skills directory')
    parser.add_argument('--registry', '-r', default='registry.json', help='Registry.json (fallback)')
    parser.add_argument('--output', '-o', default='docs', help='Output directory')
    parser.add_argument('--use-registry', action='store_true', help='Force use registry.json')

    args = parser.parse_args()

    skills_dir = Path(args.skills_dir)
    registry_path = Path(args.registry)
    output_dir = Path(args.output)

    # Prefer scanning skills directory
    if not args.use_registry and skills_dir.exists():
        logger.info(f"Scanning skills from {skills_dir}")
        skills = scan_skills_v2(skills_dir)
        source_name = "verified downloaded skills"
    elif registry_path.exists():
        logger.info(f"Loading from registry: {registry_path}")
        skills = load_from_registry(registry_path)
        source_name = "registry entries"
    else:
        logger.error("No skills source found!")
        exit(1)

    if not skills:
        logger.error("No skills found!")
        exit(1)

    build_search_index(skills, output_dir, source_name)


if __name__ == '__main__':
    main()
