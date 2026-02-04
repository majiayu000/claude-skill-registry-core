#!/usr/bin/env python3
"""
Migrate Skills - Reorganize from flat structure to category-based structure

From: skills/data/{skill-name}/SKILL.md
To:   skills/{category}/{skill-name}/SKILL.md

Handles conflicts with smart suffix (Option A):
- First skill gets simple name
- Conflicts get repo suffix: {name}-{owner}-{repo}
- Priority: official > higher stars > first-come
"""

import json
import re
import shutil
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# Official repos get priority
OFFICIAL_REPOS = {"anthropics/skills", "anthropics/claude-code"}


def normalize_name(name: str) -> str:
    """Normalize skill name."""
    if not name:
        return "unknown"
    name = re.sub(r'[^a-z0-9]+', '-', name.lower())
    name = re.sub(r'-+', '-', name).strip('-')
    return name[:64] if name else "unknown"


def get_repo_suffix(repo: str) -> str:
    """Get suffix from repo: owner-repo."""
    if not repo:
        return "unknown"
    parts = repo.replace("https://github.com/", "").split("/")
    if len(parts) >= 2:
        owner = normalize_name(parts[0])[:20]
        repo_name = normalize_name(parts[1])[:20]
        return f"{owner}-{repo_name}"
    return normalize_name(repo)[:40]


KNOWN_CATEGORIES = {
    "development", "design", "testing", "devops", "documents",
    "productivity", "product", "security", "marketing", "data", "other"
}


def scan_existing_skills(skills_dir: Path, mode: str = "legacy") -> List[dict]:
    """
    Scan skills based on mode:
    - legacy: Scan skills/data/ or skills/_legacy_data/
    - flat: Scan skills directly in skills/ root (excluding category dirs)
    """
    skills = []

    if mode == "legacy":
        # Support both old name and renamed directory
        data_dir = skills_dir / "_legacy_data"
        if not data_dir.exists():
            data_dir = skills_dir / "data"

        if not data_dir.exists():
            logger.warning(f"Data directory not found: {data_dir}")
            return skills

        dirs_to_scan = list(data_dir.iterdir())
    else:
        # Scan skills directly in skills/ root (excluding category directories)
        dirs_to_scan = [
            d for d in skills_dir.iterdir()
            if d.is_dir() and d.name not in KNOWN_CATEGORIES
            and not d.name.startswith('.')
        ]

    for skill_dir in dirs_to_scan:
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

        skills.append({
            "dir_name": dir_name,
            "path": skill_dir,
            "name": metadata.get("name", dir_name),
            "repo": metadata.get("repo", ""),
            "category": metadata.get("category", "other"),
            "stars": metadata.get("stars", 0),
            "metadata": metadata,
        })

    return skills


def plan_migration(skills: List[dict], skills_dir: Path) -> Dict[str, List[Tuple[Path, Path, str]]]:
    """
    Plan the migration, determining target paths and handling conflicts.

    Returns: {category: [(source_path, target_path, reason), ...]}
    """
    # Group by category and name
    # {category: {base_name: [skill_info, ...]}}
    grouped: Dict[str, Dict[str, List[dict]]] = defaultdict(lambda: defaultdict(list))

    for skill in skills:
        category = normalize_name(skill["category"]) or "other"
        base_name = normalize_name(skill["name"])
        grouped[category][base_name].append(skill)

    # Plan moves
    plan: Dict[str, List[Tuple[Path, Path, str]]] = defaultdict(list)

    for category, names in grouped.items():
        for base_name, skill_list in names.items():
            if len(skill_list) == 1:
                # No conflict - use simple name
                skill = skill_list[0]
                # Target is skills/{category}/{base_name}
                target_dir = skills_dir / category / base_name
                plan[category].append((skill["path"], target_dir, "no conflict"))
            else:
                # Conflict! Sort by priority
                # Priority: official > stars > first-come
                sorted_skills = sorted(
                    skill_list,
                    key=lambda s: (
                        s["repo"] in OFFICIAL_REPOS,  # Official first
                        s["stars"],  # Then by stars
                    ),
                    reverse=True
                )

                # First one gets simple name
                first = sorted_skills[0]
                target_dir = skills_dir / category / base_name
                plan[category].append((
                    first["path"],
                    target_dir,
                    f"winner: {first['stars']} stars, {first['repo']}"
                ))

                # Others get suffix
                for skill in sorted_skills[1:]:
                    suffix = get_repo_suffix(skill["repo"])
                    suffixed_name = f"{base_name}-{suffix}"
                    target_dir = skills_dir / category / suffixed_name
                    plan[category].append((
                        skill["path"],
                        target_dir,
                        f"conflict suffix: {skill['stars']} stars"
                    ))

    return plan


def execute_migration(plan: Dict[str, List[Tuple[Path, Path, str]]], dry_run: bool = True):
    """Execute the migration plan."""
    total_moves = sum(len(moves) for moves in plan.values())
    logger.info(f"Migration plan: {total_moves} skills to move")

    moved = 0
    errors = 0

    for category, moves in sorted(plan.items()):
        logger.info(f"\n[{category}] {len(moves)} skills")

        for source, target, reason in moves:
            if dry_run:
                logger.info(f"  {source.name} -> {target.relative_to(target.parent.parent)} ({reason})")
            else:
                try:
                    # Create target directory
                    target.parent.mkdir(parents=True, exist_ok=True)

                    # Check if target already exists
                    if target.exists():
                        logger.warning(f"  Target exists, skipping: {target}")
                        continue

                    # Move
                    shutil.move(str(source), str(target))

                    # Update metadata with new dir_name
                    metadata_file = target / "metadata.json"
                    if metadata_file.exists():
                        try:
                            metadata = json.loads(metadata_file.read_text())
                            metadata["dir_name"] = target.name
                            metadata["category"] = category
                            metadata_file.write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
                        except Exception:
                            pass

                    logger.info(f"  ✓ {source.name} -> {target.name}")
                    moved += 1

                except Exception as e:
                    logger.error(f"  ✗ Failed to move {source.name}: {e}")
                    errors += 1

    if not dry_run:
        logger.info(f"\nMigration complete: {moved} moved, {errors} errors")

        # Clean up empty data directory
        try:
            data_dir = list(plan.values())[0][0][0].parent.parent / "data" if plan else None
            if data_dir and data_dir.exists():
                remaining = list(data_dir.iterdir())
                if not remaining:
                    data_dir.rmdir()
                    logger.info("Removed empty data directory")
                else:
                    logger.info(f"Data directory still has {len(remaining)} items")
        except Exception:
            pass


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Migrate skills to category-based structure')
    parser.add_argument('--skills-dir', '-s', default='skills', help='Skills directory')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show plan without executing')
    parser.add_argument('--execute', '-e', action='store_true', help='Execute the migration')
    parser.add_argument('--flat', '-f', action='store_true', help='Migrate skills from root (not data/)')

    args = parser.parse_args()

    skills_dir = Path(args.skills_dir)

    if not skills_dir.exists():
        logger.error(f"Skills directory not found: {skills_dir}")
        return

    # Scan existing skills
    mode = "flat" if args.flat else "legacy"
    if mode == "flat":
        logger.info(f"Scanning skills in {skills_dir}/ root...")
    else:
        logger.info(f"Scanning skills in {skills_dir}/data...")
    skills = scan_existing_skills(skills_dir, mode=mode)
    logger.info(f"Found {len(skills)} skills")

    if not skills:
        logger.info("No skills to migrate")
        return

    # Plan migration
    logger.info("\nPlanning migration...")
    plan = plan_migration(skills, skills_dir)

    # Count conflicts
    conflicts = sum(1 for moves in plan.values() for _, _, reason in moves if "conflict" in reason)
    logger.info(f"Conflicts to resolve: {conflicts}")

    # Show summary by category
    logger.info("\nCategory summary:")
    for category, moves in sorted(plan.items()):
        logger.info(f"  {category}: {len(moves)} skills")

    if args.execute:
        logger.info("\n" + "=" * 50)
        logger.info("EXECUTING MIGRATION")
        logger.info("=" * 50)
        execute_migration(plan, dry_run=False)
    else:
        logger.info("\n" + "=" * 50)
        logger.info("DRY RUN - No changes made")
        logger.info("=" * 50)
        execute_migration(plan, dry_run=True)
        logger.info("\nRun with --execute to apply changes")


if __name__ == "__main__":
    main()
