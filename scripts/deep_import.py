#!/usr/bin/env python3
"""
Deep Import - Parse awesome lists and clone ALL referenced repos.
No GitHub API needed!
"""

import os
import json
import re
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging

from utils import normalize_name, ensure_unique_dir, build_skill_key

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Category mapping
CATEGORY_KEYWORDS = {
    "development": ["dev", "code", "programming", "api", "sdk", "framework", "build", "typescript", "python", "rust", "go"],
    "testing": ["test", "qa", "quality", "spec", "jest", "pytest", "unittest", "e2e"],
    "devops": ["devops", "ci", "cd", "docker", "kubernetes", "deploy", "infra", "aws", "terraform"],
    "security": ["security", "auth", "crypto", "vulnerability", "audit", "pentest"],
    "documents": ["doc", "pdf", "word", "excel", "pptx", "xlsx", "docx", "markdown", "readme"],
    "data": ["data", "analytics", "sql", "database", "etl", "pipeline", "ml", "ai"],
    "design": ["design", "ui", "ux", "css", "frontend", "component", "figma"],
    "productivity": ["productivity", "automation", "workflow", "task", "todo"],
    "product": ["product", "prd", "roadmap", "feature", "backlog"],
    "marketing": ["marketing", "seo", "content", "social", "campaign"],
}


def guess_category(text: str) -> str:
    text = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return category
    return "other"


def extract_github_repos(clone_cache: Path) -> set:
    """Extract all GitHub repo URLs from README files."""
    repos = set()
    pattern = re.compile(r'https://github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)')

    for readme in clone_cache.rglob("README.md"):
        try:
            content = readme.read_text(encoding="utf-8", errors="ignore")
            matches = pattern.findall(content)
            for match in matches:
                # Clean up (remove .git suffix, trailing slashes)
                repo = match.rstrip("/").replace(".git", "")
                if "/" in repo and len(repo.split("/")) == 2:
                    repos.add(repo)
        except Exception:
            pass

    return repos


def clone_repo(repo: str, clone_dir: Path) -> tuple:
    """Clone a single repo. Returns (repo, success, skill_count)."""
    repo_name = repo.replace("/", "_")
    clone_path = clone_dir / repo_name

    if clone_path.exists():
        # Count existing skills
        skill_count = len(list(clone_path.rglob("SKILL.md")))
        return (repo, True, skill_count, "cached")

    try:
        result = subprocess.run(
            ["git", "clone", "--depth", "1", f"https://github.com/{repo}.git", str(clone_path)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode == 0:
            skill_count = len(list(clone_path.rglob("SKILL.md")))
            return (repo, True, skill_count, "cloned")
        else:
            return (repo, False, 0, "failed")
    except subprocess.TimeoutExpired:
        return (repo, False, 0, "timeout")
    except Exception as e:
        return (repo, False, 0, str(e))


def parse_skill_frontmatter(content: str) -> dict:
    metadata = {}
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip().lower()
                    value = value.strip().strip('"').strip("'")
                    if key in ["name", "description", "category"]:
                        metadata[key] = value
    return metadata


def import_skills(clone_dir: Path, skills_dir: Path) -> dict:
    """Import all SKILL.md files from cloned repos."""
    stats = {"imported": 0, "skipped": 0, "errors": 0}

    for skill_file in clone_dir.rglob("SKILL.md"):
        if ".git" in str(skill_file) or "node_modules" in str(skill_file):
            continue

        try:
            content = skill_file.read_text(encoding="utf-8", errors="ignore")
            if len(content) < 50:
                stats["skipped"] += 1
                continue

            # Get repo name from path
            rel_path = skill_file.relative_to(clone_dir)
            repo_name = str(rel_path.parts[0]) if rel_path.parts else "unknown"
            repo_slug = repo_name.replace("_", "/")

            # Parse metadata
            metadata = parse_skill_frontmatter(content)

            # Skill name
            skill_name = metadata.get("name", "")
            if not skill_name:
                skill_name = skill_file.parent.name
            skill_name = normalize_name(skill_name)

            if not skill_name or skill_name in ["unknown", "skills", "skill"]:
                skill_name = f"{normalize_name(repo_name)}-skill"

            # Category
            category = normalize_name(metadata.get("category", ""))
            if not category or category == "unknown":
                category = guess_category(str(skill_file) + content[:500])

            # Target path (case-safe)
            key = build_skill_key(repo_slug, str(skill_file), name=skill_name, category=category)
            target_dir = ensure_unique_dir(skills_dir / category, skill_name, key, repo=repo_slug)
            target_file = target_dir / "SKILL.md"

            if target_file.exists():
                stats["skipped"] += 1
                continue

            # Save
            target_dir.mkdir(parents=True, exist_ok=True)
            target_file.write_text(content, encoding="utf-8")

            # Metadata
            meta = {
                "name": skill_name,
                "description": metadata.get("description", "")[:200],
                "category": category,
                "repo": repo_slug,
                "source": f"github.com/{repo_slug}",
                "dir_name": target_dir.name,
                "imported_at": datetime.utcnow().isoformat() + "Z",
            }
            (target_dir / "metadata.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

            stats["imported"] += 1

        except Exception as e:
            stats["errors"] += 1

    return stats


def main():
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent
    skills_dir = project_dir / "skills"
    clone_dir = project_dir / ".clone_cache"
    clone_dir.mkdir(exist_ok=True)

    logger.info("=" * 60)
    logger.info("DEEP IMPORT - Clone ALL referenced repos")
    logger.info("=" * 60)

    # Phase 1: Extract repos from READMEs
    logger.info("\n=== Phase 1: Extracting GitHub repos from READMEs ===")
    repos = extract_github_repos(clone_dir)
    logger.info(f"Found {len(repos)} unique repos")

    # Phase 2: Clone repos in parallel
    logger.info("\n=== Phase 2: Cloning repos (parallel) ===")

    cloned = 0
    cached = 0
    failed = 0
    total_skills_in_repos = 0

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(clone_repo, repo, clone_dir): repo for repo in repos}

        for i, future in enumerate(as_completed(futures), 1):
            repo, success, skill_count, status = future.result()

            if success:
                if status == "cached":
                    cached += 1
                else:
                    cloned += 1
                total_skills_in_repos += skill_count
                if skill_count > 0:
                    logger.info(f"  [{i}/{len(repos)}] ✓ {repo} ({skill_count} skills)")
            else:
                failed += 1

            if i % 20 == 0:
                logger.info(f"  Progress: {i}/{len(repos)} (cloned: {cloned}, cached: {cached}, failed: {failed})")

    logger.info(f"\nClone summary: {cloned} cloned, {cached} cached, {failed} failed")
    logger.info(f"Total SKILL.md in repos: {total_skills_in_repos}")

    # Phase 3: Import skills
    logger.info("\n=== Phase 3: Importing skills ===")
    stats = import_skills(clone_dir, skills_dir)

    # Final count
    total_skills = sum(1 for _ in skills_dir.rglob("SKILL.md"))

    logger.info("\n" + "=" * 60)
    logger.info("DEEP IMPORT COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Repos processed:  {len(repos)}")
    logger.info(f"  Skills imported:  {stats['imported']}")
    logger.info(f"  Skipped:          {stats['skipped']}")
    logger.info(f"  Errors:           {stats['errors']}")
    logger.info("-" * 60)
    logger.info(f"  TOTAL SKILLS:     {total_skills}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
