#!/usr/bin/env python3
"""
Find All Skills - Search for SKILL.md files in GitHub repos

Uses GitHub Search API to find actual locations of SKILL.md files
in repos where we couldn't find them with fixed path patterns.

Usage:
    python scripts/find_all_skills.py --token YOUR_GITHUB_TOKEN [--limit N]
"""

import asyncio
import aiohttp
import json
from pathlib import Path
from typing import Optional, Dict, List, Set
import logging
import argparse
import time
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# GitHub API
GITHUB_API = "https://api.github.com"

# Rate limit tracking
rate_limit_remaining = 30
rate_limit_reset = 0


async def search_skill_in_repo(
    session: aiohttp.ClientSession,
    repo: str,
    token: str,
    semaphore: asyncio.Semaphore
) -> List[str]:
    """Search for SKILL.md files in a repo using GitHub Search API."""
    global rate_limit_remaining, rate_limit_reset

    async with semaphore:
        # Check rate limit
        if rate_limit_remaining < 5:
            wait_time = max(0, rate_limit_reset - time.time()) + 1
            if wait_time > 0:
                logger.info(f"Rate limited, waiting {wait_time:.0f}s...")
                await asyncio.sleep(wait_time)

        url = f"{GITHUB_API}/search/code"
        params = {
            "q": f"filename:SKILL.md repo:{repo}",
            "per_page": 100
        }
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        try:
            async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                # Update rate limit info
                rate_limit_remaining = int(resp.headers.get('X-RateLimit-Remaining', 30))
                rate_limit_reset = int(resp.headers.get('X-RateLimit-Reset', 0))

                if resp.status == 403:
                    # Rate limited
                    wait_time = max(0, rate_limit_reset - time.time()) + 1
                    logger.warning(f"Rate limited for {repo}, waiting {wait_time:.0f}s...")
                    await asyncio.sleep(wait_time)
                    return []

                if resp.status != 200:
                    return []

                data = await resp.json()
                paths = []
                for item in data.get('items', []):
                    path = item.get('path', '')
                    if path.endswith('SKILL.md'):
                        # Get the directory containing SKILL.md
                        skill_dir = str(Path(path).parent)
                        if skill_dir == '.':
                            skill_dir = ''
                        paths.append(skill_dir)

                return paths
        except Exception as e:
            return []


async def get_repo_default_branch(
    session: aiohttp.ClientSession,
    repo: str,
    token: str,
    semaphore: asyncio.Semaphore
) -> Optional[str]:
    """Get the default branch of a repo."""
    async with semaphore:
        url = f"{GITHUB_API}/repos/{repo}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get('default_branch', 'main')
        except:
            return None


async def process_repo(
    session: aiohttp.ClientSession,
    repo: str,
    skill_names: List[str],
    metadata_files: List[Path],
    token: str,
    semaphore: asyncio.Semaphore,
    dry_run: bool,
    stats: dict
) -> None:
    """Process a single repo - find all SKILL.md files and match to our skills."""

    # Search for SKILL.md files in the repo
    skill_paths = await search_skill_in_repo(session, repo, token, semaphore)

    if not skill_paths:
        stats['repos_no_skills'] += 1
        return

    # Get default branch
    branch = await get_repo_default_branch(session, repo, token, semaphore)
    if not branch:
        branch = 'main'

    stats['repos_with_skills'] += 1

    # Try to match our skills to found paths
    for i, skill_name in enumerate(skill_names):
        metadata_path = metadata_files[i]
        skill_name_lower = skill_name.lower()

        # Find matching path
        matched_path = None
        for path in skill_paths:
            path_lower = path.lower()
            # Check if skill name is in the path
            if skill_name_lower in path_lower or path_lower.endswith(f"/{skill_name_lower}"):
                matched_path = path
                break
            # Also check the last component
            last_component = Path(path).name.lower() if path else ''
            if last_component == skill_name_lower:
                matched_path = path
                break

        if matched_path is not None:
            if dry_run:
                logger.info(f"  Found: {skill_name} -> {repo}/{matched_path} ({branch})")
            else:
                # Update metadata
                try:
                    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                    metadata['github_path'] = matched_path
                    metadata['github_branch'] = branch
                    metadata_path.write_text(
                        json.dumps(metadata, indent=2, ensure_ascii=False),
                        encoding='utf-8'
                    )
                except Exception as e:
                    logger.error(f"  Error updating {metadata_path}: {e}")
                    continue

            stats['matched'] += 1
        else:
            # If we found SKILL.md files but couldn't match by name,
            # and there's only one skill from this repo, use the first found path
            if len(skill_names) == 1 and len(skill_paths) == 1:
                matched_path = skill_paths[0]
                if dry_run:
                    logger.info(f"  Found (single): {skill_name} -> {repo}/{matched_path} ({branch})")
                else:
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                        metadata['github_path'] = matched_path
                        metadata['github_branch'] = branch
                        metadata_path.write_text(
                            json.dumps(metadata, indent=2, ensure_ascii=False),
                            encoding='utf-8'
                        )
                    except:
                        continue
                stats['matched'] += 1
            else:
                stats['unmatched'] += 1


async def main():
    parser = argparse.ArgumentParser(description='Find SKILL.md files using GitHub Search API')
    parser.add_argument('--skills-dir', '-s', default='skills', help='Skills directory')
    parser.add_argument('--token', '-t', required=True, help='GitHub token')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show changes without applying')
    parser.add_argument('--limit', '-l', type=int, default=0, help='Limit repos to process')
    parser.add_argument('--workers', '-w', type=int, default=5, help='Concurrent workers (keep low for API)')

    args = parser.parse_args()
    skills_dir = Path(args.skills_dir)

    # Find all skills missing github_path, grouped by repo
    logger.info("Scanning for skills missing github_path...")
    repo_skills: Dict[str, List[tuple]] = defaultdict(list)  # repo -> [(name, metadata_path), ...]

    for metadata_path in skills_dir.rglob("metadata.json"):
        try:
            m = json.loads(metadata_path.read_text())
            if m.get("github_path"):
                continue  # Already has path
            repo = m.get("repo", "")
            if not repo or "/" not in repo:
                continue  # Invalid repo
            name = m.get("name", metadata_path.parent.name)
            repo_skills[repo].append((name, metadata_path))
        except:
            pass

    total_skills = sum(len(v) for v in repo_skills.values())
    logger.info(f"Found {total_skills} skills in {len(repo_skills)} unique repos")

    if args.limit > 0:
        # Limit to first N repos
        limited_repos = dict(list(repo_skills.items())[:args.limit])
        repo_skills = limited_repos
        logger.info(f"Limited to {len(repo_skills)} repos")

    if not repo_skills:
        logger.info("No skills to process")
        return

    stats = {
        'repos_with_skills': 0,
        'repos_no_skills': 0,
        'matched': 0,
        'unmatched': 0,
    }

    # Low concurrency for Search API (30 requests/minute)
    semaphore = asyncio.Semaphore(args.workers)
    connector = aiohttp.TCPConnector(limit=args.workers * 2)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Process repos one by one due to API limits
        total_repos = len(repo_skills)
        processed = 0

        for repo, skills_list in repo_skills.items():
            skill_names = [s[0] for s in skills_list]
            metadata_files = [s[1] for s in skills_list]

            await process_repo(
                session, repo, skill_names, metadata_files,
                args.token, semaphore, args.dry_run, stats
            )

            processed += 1
            if processed % 100 == 0 or processed == total_repos:
                logger.info(f"Progress: {processed}/{total_repos} repos - "
                           f"Matched: {stats['matched']}, Unmatched: {stats['unmatched']}, "
                           f"Repos with skills: {stats['repos_with_skills']}")

    logger.info(f"\n=== Summary ===")
    logger.info(f"Repos processed: {stats['repos_with_skills'] + stats['repos_no_skills']}")
    logger.info(f"Repos with SKILL.md: {stats['repos_with_skills']}")
    logger.info(f"Repos without SKILL.md: {stats['repos_no_skills']}")
    logger.info(f"Skills matched: {stats['matched']}")
    logger.info(f"Skills unmatched: {stats['unmatched']}")


if __name__ == "__main__":
    asyncio.run(main())
