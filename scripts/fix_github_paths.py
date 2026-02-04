#!/usr/bin/env python3
"""
Fix GitHub Paths - Update metadata.json with correct github_path

For skills downloaded from SkillsMP that only have repo name,
this script tries to discover the actual GitHub path by directly
checking common skill locations via HTTP (no API needed).

Usage:
    python scripts/fix_github_paths.py [--dry-run] [--limit N]
    python scripts/fix_github_paths.py --workers 100  # More parallel
"""

import asyncio
import aiohttp
import json
from pathlib import Path
from typing import Optional, Dict
import logging
import argparse

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

# Cache: repo -> (branch, base_path) e.g., "owner/repo" -> ("main", ".claude/skills")
REPO_STRUCTURE_CACHE: Dict[str, tuple] = {}

# Common skill base paths (without skill name)
SKILL_BASE_PATHS = [
    ".claude/skills",
    ".claude",
    "skills",
    "",  # root level
]


async def check_url(session: aiohttp.ClientSession, url: str, semaphore: asyncio.Semaphore) -> bool:
    """Check if URL exists with rate limiting."""
    async with semaphore:
        try:
            async with session.head(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                return resp.status == 200
        except:
            return False


async def find_repo_structure(
    session: aiohttp.ClientSession,
    repo: str,
    skill_name: str,
    semaphore: asyncio.Semaphore
) -> Optional[tuple]:
    """Find the skill structure for a repo (branch, base_path)."""
    # Check cache first
    if repo in REPO_STRUCTURE_CACHE:
        return REPO_STRUCTURE_CACHE[repo]

    branches = ["main", "master"]

    for branch in branches:
        for base_path in SKILL_BASE_PATHS:
            if base_path:
                path = f"{base_path}/{skill_name}/SKILL.md"
            else:
                path = f"{skill_name}/SKILL.md"

            url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}"

            if await check_url(session, url, semaphore):
                # Cache this repo's structure
                REPO_STRUCTURE_CACHE[repo] = (branch, base_path)
                return (branch, base_path)

    # Also try root-level SKILL.md
    for branch in branches:
        url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/SKILL.md"
        if await check_url(session, url, semaphore):
            REPO_STRUCTURE_CACHE[repo] = (branch, "")
            return (branch, "")

    REPO_STRUCTURE_CACHE[repo] = None
    return None


async def find_skill_path(
    session: aiohttp.ClientSession,
    repo: str,
    skill_name: str,
    semaphore: asyncio.Semaphore
) -> Optional[tuple]:
    """Try to find the actual skill path in a repo. Returns (path, branch) or None."""
    # Get repo structure (cached)
    structure = await find_repo_structure(session, repo, skill_name, semaphore)

    if structure is None:
        return None

    branch, base_path = structure

    # Now check if this specific skill exists
    if base_path:
        skill_path = f"{base_path}/{skill_name}"
    else:
        skill_path = skill_name

    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_path}/SKILL.md"

    if await check_url(session, url, semaphore):
        return (skill_path, branch)

    # Try root SKILL.md for single-skill repos
    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/SKILL.md"
    if await check_url(session, url, semaphore):
        return ("", branch)

    return None


async def process_skill(
    session: aiohttp.ClientSession,
    metadata_path: Path,
    semaphore: asyncio.Semaphore,
    dry_run: bool,
    stats: dict
) -> bool:
    """Process a single skill's metadata."""
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except:
        return False

    # Skip if already has github_path
    if metadata.get("github_path"):
        stats["already_has_path"] += 1
        return False

    repo = metadata.get("repo", "")
    if not repo:
        stats["no_repo"] += 1
        return False

    # Get skill name from metadata or directory
    skill_name = metadata.get("name", metadata_path.parent.name)

    # Try to find the path
    result = await find_skill_path(session, repo, skill_name, semaphore)

    if result is not None:
        github_path, branch = result
        if dry_run:
            logger.info(f"  Found: {skill_name} -> {repo}/{github_path} ({branch})")
        else:
            metadata["github_path"] = github_path
            metadata["github_branch"] = branch
            metadata_path.write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
        stats["updated"] += 1
        return True
    else:
        stats["not_found"] += 1
        return False


async def main():
    parser = argparse.ArgumentParser(description='Fix github_path in metadata (no API needed)')
    parser.add_argument('--skills-dir', '-s', default='skills', help='Skills directory')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show changes without applying')
    parser.add_argument('--limit', '-l', type=int, default=0, help='Limit number to process')
    parser.add_argument('--category', '-c', help='Only process specific category')
    parser.add_argument('--workers', '-w', type=int, default=100, help='Concurrent workers')

    args = parser.parse_args()
    skills_dir = Path(args.skills_dir)

    # Find all metadata files missing github_path
    logger.info("Scanning for skills missing github_path...")
    metadata_files = []
    for metadata_path in skills_dir.rglob("metadata.json"):
        if args.category and args.category not in str(metadata_path):
            continue
        try:
            m = json.loads(metadata_path.read_text())
            if not m.get("github_path") and m.get("repo"):
                metadata_files.append((metadata_path, m.get("stars", 0)))
        except:
            pass

    # Sort by stars (high stars first - more likely to have standard structure)
    metadata_files.sort(key=lambda x: x[1], reverse=True)
    metadata_files = [p for p, _ in metadata_files]

    if args.limit > 0:
        metadata_files = metadata_files[:args.limit]

    logger.info(f"Found {len(metadata_files)} skills missing github_path")
    logger.info(f"Using {args.workers} concurrent workers")

    if not metadata_files:
        return

    stats = {
        "updated": 0,
        "not_found": 0,
        "already_has_path": 0,
        "no_repo": 0,
    }

    # High concurrency - no API rate limits for raw.githubusercontent.com
    semaphore = asyncio.Semaphore(args.workers)
    connector = aiohttp.TCPConnector(limit=args.workers * 2)
    headers = {"User-Agent": "Claude-Skills-Path-Fixer/1.0"}

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        # Process all at once with semaphore controlling concurrency
        tasks = [
            process_skill(session, path, semaphore, args.dry_run, stats)
            for path in metadata_files
        ]

        # Show progress
        total = len(tasks)
        done = 0

        for coro in asyncio.as_completed(tasks):
            await coro
            done += 1
            if done % 500 == 0 or done == total:
                logger.info(f"Progress: {done}/{total} ({100*done//total}%) - "
                           f"Found: {stats['updated']}, Not found: {stats['not_found']}, "
                           f"Cache hits: {len(REPO_STRUCTURE_CACHE)} repos")

    logger.info(f"\n=== Summary ===")
    logger.info(f"Updated: {stats['updated']}")
    logger.info(f"Not found: {stats['not_found']}")
    logger.info(f"Already has path: {stats['already_has_path']}")
    logger.info(f"Repos cached: {len(REPO_STRUCTURE_CACHE)}")


if __name__ == "__main__":
    asyncio.run(main())
