#!/usr/bin/env python3
"""
Find All Skills v2 - Use Git Tree API to find SKILL.md files

Much more reliable than Search API. No token required for public repos.

Usage:
    python scripts/find_all_skills_v2.py [--dry-run] [--limit N] [--workers N]
"""

import asyncio
import aiohttp
import json
from pathlib import Path
from typing import Optional, Dict, List
import logging
import argparse
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


async def get_repo_tree(
    session: aiohttp.ClientSession,
    repo: str,
    semaphore: asyncio.Semaphore,
    token: Optional[str] = None
) -> tuple:
    """Get the full tree of a repo and find all SKILL.md files.
    Returns (branch, list of skill paths) or (None, [])
    """
    async with semaphore:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        # First get default branch
        try:
            async with session.get(
                f"{GITHUB_API}/repos/{repo}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None, []
                data = await resp.json()
                branch = data.get('default_branch', 'main')
        except:
            return None, []

        # Get full tree
        try:
            async with session.get(
                f"{GITHUB_API}/repos/{repo}/git/trees/{branch}?recursive=1",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    return branch, []
                data = await resp.json()

                # Find all SKILL.md files
                skill_paths = []
                for item in data.get('tree', []):
                    path = item.get('path', '')
                    if path.endswith('/SKILL.md') or path == 'SKILL.md':
                        # Get parent directory
                        parent = str(Path(path).parent)
                        if parent == '.':
                            parent = ''
                        skill_paths.append(parent)

                return branch, skill_paths
        except Exception as e:
            return branch, []


def match_skill_to_path(skill_name: str, skill_paths: List[str]) -> Optional[str]:
    """Try to match a skill name to one of the found paths."""
    skill_name_lower = skill_name.lower()

    for path in skill_paths:
        path_lower = path.lower()
        # Direct match on last component
        last_component = Path(path).name.lower() if path else ''
        if last_component == skill_name_lower:
            return path
        # Partial match
        if skill_name_lower in path_lower:
            return path

    return None


async def process_repo(
    session: aiohttp.ClientSession,
    repo: str,
    skills_info: List[tuple],  # [(name, metadata_path), ...]
    semaphore: asyncio.Semaphore,
    dry_run: bool,
    stats: dict,
    token: Optional[str] = None
) -> None:
    """Process a single repo."""
    branch, skill_paths = await get_repo_tree(session, repo, semaphore, token)

    if not skill_paths:
        stats['repos_no_skills'] += 1
        return

    stats['repos_with_skills'] += 1
    stats['total_skill_files'] += len(skill_paths)

    # Match each of our skills to found paths
    for skill_name, metadata_path in skills_info:
        matched_path = match_skill_to_path(skill_name, skill_paths)

        if matched_path is not None:
            if dry_run:
                logger.info(f"  Match: {skill_name} -> {repo}/{matched_path} ({branch})")
            else:
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
            # If only one skill from this repo and one SKILL.md found, use it
            if len(skills_info) == 1 and len(skill_paths) == 1:
                matched_path = skill_paths[0]
                if dry_run:
                    logger.info(f"  Match (single): {skill_name} -> {repo}/{matched_path} ({branch})")
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
    parser = argparse.ArgumentParser(description='Find SKILL.md files using Git Tree API')
    parser.add_argument('--skills-dir', '-s', default='skills', help='Skills directory')
    parser.add_argument('--token', '-t', help='GitHub token (optional, for higher rate limits)')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show changes without applying')
    parser.add_argument('--limit', '-l', type=int, default=0, help='Limit repos to process')
    parser.add_argument('--workers', '-w', type=int, default=20, help='Concurrent workers')

    args = parser.parse_args()
    skills_dir = Path(args.skills_dir)

    # Find all skills missing github_path, grouped by repo
    logger.info("Scanning for skills missing github_path...")
    repo_skills: Dict[str, List[tuple]] = defaultdict(list)

    for metadata_path in skills_dir.rglob("metadata.json"):
        try:
            m = json.loads(metadata_path.read_text())
            if m.get("github_path"):
                continue
            repo = m.get("repo", "")
            if not repo or "/" not in repo:
                continue
            name = m.get("name", metadata_path.parent.name)
            repo_skills[repo].append((name, metadata_path))
        except:
            pass

    total_skills = sum(len(v) for v in repo_skills.values())
    logger.info(f"Found {total_skills} skills in {len(repo_skills)} unique repos missing github_path")

    if args.limit > 0:
        limited_repos = dict(list(repo_skills.items())[:args.limit])
        repo_skills = limited_repos
        logger.info(f"Limited to {len(repo_skills)} repos")

    if not repo_skills:
        return

    stats = {
        'repos_with_skills': 0,
        'repos_no_skills': 0,
        'total_skill_files': 0,
        'matched': 0,
        'unmatched': 0,
    }

    semaphore = asyncio.Semaphore(args.workers)
    connector = aiohttp.TCPConnector(limit=args.workers * 2)

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = []
        for repo, skills_list in repo_skills.items():
            task = process_repo(
                session, repo, skills_list,
                semaphore, args.dry_run, stats, args.token
            )
            tasks.append(task)

        # Process with progress
        total = len(tasks)
        done = 0
        for coro in asyncio.as_completed(tasks):
            await coro
            done += 1
            if done % 100 == 0 or done == total:
                logger.info(f"Progress: {done}/{total} repos - "
                           f"Matched: {stats['matched']}, "
                           f"SKILL.md files found: {stats['total_skill_files']}")

    logger.info(f"\n{'='*50}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*50}")
    logger.info(f"Repos processed: {stats['repos_with_skills'] + stats['repos_no_skills']}")
    logger.info(f"Repos with SKILL.md: {stats['repos_with_skills']}")
    logger.info(f"Repos without SKILL.md: {stats['repos_no_skills']}")
    logger.info(f"Total SKILL.md files found: {stats['total_skill_files']}")
    logger.info(f"Skills matched: {stats['matched']}")
    logger.info(f"Skills unmatched: {stats['unmatched']}")


if __name__ == "__main__":
    asyncio.run(main())
