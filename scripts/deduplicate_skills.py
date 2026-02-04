#!/usr/bin/env python3
"""
Deduplicate skills by content (MD5 hash)
Keeps the one with highest stars or first alphabetically
"""

import os
import json
import hashlib
from pathlib import Path
from collections import defaultdict
import shutil
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)


def get_file_hash(filepath):
    """Calculate MD5 hash of file content"""
    with open(filepath, 'rb') as f:
        return hashlib.md5(f.read()).hexdigest()


def get_skill_stars(skill_dir):
    """Get stars count from metadata.json"""
    metadata_file = skill_dir / "metadata.json"
    if metadata_file.exists():
        try:
            meta = json.loads(metadata_file.read_text())
            return meta.get("stars", 0)
        except:
            pass
    return 0


def main():
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent
    skills_dir = project_dir / "skills"

    logger.info("=" * 60)
    logger.info("DEDUPLICATING SKILLS BY CONTENT")
    logger.info("=" * 60)

    # Group skills by content hash
    hash_to_skills = defaultdict(list)
    total_files = 0

    logger.info("Scanning skills and calculating hashes...")

    for skill_file in skills_dir.rglob("SKILL.md"):
        total_files += 1
        file_hash = get_file_hash(skill_file)
        skill_dir = skill_file.parent
        stars = get_skill_stars(skill_dir)

        hash_to_skills[file_hash].append({
            "path": skill_dir,
            "stars": stars,
            "name": skill_dir.name,
        })

        if total_files % 5000 == 0:
            logger.info(f"  Scanned {total_files} files...")

    logger.info(f"Total skills scanned: {total_files}")
    logger.info(f"Unique content hashes: {len(hash_to_skills)}")

    # Find duplicates (hash with more than 1 file)
    duplicates = {h: skills for h, skills in hash_to_skills.items() if len(skills) > 1}
    total_duplicates = sum(len(s) - 1 for s in duplicates.values())

    logger.info(f"Duplicate groups: {len(duplicates)}")
    logger.info(f"Total files to remove: {total_duplicates}")

    if total_duplicates == 0:
        logger.info("No duplicates found!")
        return

    # Remove duplicates, keep one with highest stars
    removed = 0
    kept = 0

    for file_hash, skills in duplicates.items():
        # Sort by stars (descending), then by name (for consistency)
        skills.sort(key=lambda x: (-x["stars"], x["name"]))

        # Keep the first one (highest stars)
        keeper = skills[0]
        kept += 1

        # Remove the rest
        for skill in skills[1:]:
            try:
                shutil.rmtree(skill["path"])
                removed += 1
                if removed <= 20:  # Show first 20
                    logger.info(f"  Removed: {skill['path'].relative_to(skills_dir)} (kept: {keeper['name']})")
            except Exception as e:
                logger.error(f"  Failed to remove {skill['path']}: {e}")

        if removed % 100 == 0 and removed > 0:
            logger.info(f"  Progress: {removed} removed...")

    # Final count
    final_count = sum(1 for _ in skills_dir.rglob("SKILL.md"))

    logger.info("")
    logger.info("=" * 60)
    logger.info("DEDUPLICATION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"  Before:  {total_files}")
    logger.info(f"  Removed: {removed}")
    logger.info(f"  After:   {final_count}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
