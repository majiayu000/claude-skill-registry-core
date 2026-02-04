#!/usr/bin/env python3
"""
Find Skills by Cloning - Clone repos and search for SKILL.md locally

No GitHub API needed. Uses git clone --depth 1 for speed.

Usage:
    python scripts/find_skills_by_clone.py [--limit N] [--workers N]
"""

import subprocess
import json
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional
import logging
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import os

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def clone_and_search(repo: str, temp_dir: str) -> tuple:
    """Clone a repo and find all SKILL.md files.
    Returns (branch, list of skill paths) or (None, [])
    """
    repo_dir = os.path.join(temp_dir, repo.replace('/', '_'))

    try:
        # Try main branch first
        result = subprocess.run(
            ['git', 'clone', '--depth', '1', '--single-branch',
             f'https://github.com/{repo}.git', repo_dir],
            capture_output=True, timeout=60, text=True
        )

        if result.returncode != 0:
            # Try master branch
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', '--single-branch', '-b', 'master',
                 f'https://github.com/{repo}.git', repo_dir],
                capture_output=True, timeout=60, text=True
            )
            if result.returncode != 0:
                return None, []
            branch = 'master'
        else:
            branch = 'main'

        # Find all SKILL.md files
        skill_paths = []
        repo_path = Path(repo_dir)
        for skill_file in repo_path.rglob('SKILL.md'):
            rel_path = skill_file.relative_to(repo_path)
            parent = str(rel_path.parent)
            if parent == '.':
                parent = ''
            skill_paths.append(parent)

        return branch, skill_paths

    except subprocess.TimeoutExpired:
        return None, []
    except Exception as e:
        return None, []
    finally:
        # Cleanup
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)


def match_skill_to_path(skill_name: str, skill_paths: List[str]) -> Optional[str]:
    """Match a skill name to one of the found paths."""
    skill_name_lower = skill_name.lower()

    for path in skill_paths:
        # Get last component of path
        last_component = Path(path).name.lower() if path else ''
        if last_component == skill_name_lower:
            return path
        # Also check if skill name is anywhere in path
        if skill_name_lower in path.lower():
            return path

    return None


def process_repo(repo: str, skills_info: List[tuple], temp_base: str, stats: dict) -> List[tuple]:
    """Process a single repo. Returns list of (metadata_path, github_path, branch) to update."""
    updates = []

    # Create temp dir for this repo
    temp_dir = tempfile.mkdtemp(dir=temp_base)

    try:
        branch, skill_paths = clone_and_search(repo, temp_dir)

        if not skill_paths:
            stats['repos_no_skills'] += 1
            return updates

        stats['repos_with_skills'] += 1
        stats['total_skill_files'] += len(skill_paths)

        # Match skills
        for skill_name, metadata_path in skills_info:
            matched_path = match_skill_to_path(skill_name, skill_paths)

            if matched_path is not None:
                updates.append((metadata_path, matched_path, branch))
                stats['matched'] += 1
            elif len(skills_info) == 1 and len(skill_paths) == 1:
                # Single skill, single path - use it
                updates.append((metadata_path, skill_paths[0], branch))
                stats['matched'] += 1
            else:
                stats['unmatched'] += 1

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    return updates


def main():
    parser = argparse.ArgumentParser(description='Find SKILL.md by cloning repos')
    parser.add_argument('--skills-dir', '-s', default='skills', help='Skills directory')
    parser.add_argument('--limit', '-l', type=int, default=0, help='Limit repos to process')
    parser.add_argument('--workers', '-w', type=int, default=10, help='Concurrent workers')
    parser.add_argument('--dry-run', '-n', action='store_true', help='Show without applying')

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
    logger.info(f"Found {total_skills} skills in {len(repo_skills)} unique repos")

    if args.limit > 0:
        # Sort by skill count (more skills = more likely to have SKILL.md)
        sorted_repos = sorted(repo_skills.items(), key=lambda x: -len(x[1]))
        repo_skills = dict(sorted_repos[:args.limit])
        logger.info(f"Limited to top {len(repo_skills)} repos by skill count")

    if not repo_skills:
        return

    stats = {
        'repos_with_skills': 0,
        'repos_no_skills': 0,
        'total_skill_files': 0,
        'matched': 0,
        'unmatched': 0,
    }

    all_updates = []

    # Create temp base directory
    temp_base = tempfile.mkdtemp(prefix='skill_clone_')

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {}
            for repo, skills_list in repo_skills.items():
                future = executor.submit(process_repo, repo, skills_list, temp_base, stats)
                futures[future] = repo

            done = 0
            total = len(futures)

            for future in as_completed(futures):
                repo = futures[future]
                try:
                    updates = future.result()
                    all_updates.extend(updates)
                except Exception as e:
                    logger.error(f"Error processing {repo}: {e}")

                done += 1
                if done % 50 == 0 or done == total:
                    logger.info(f"Progress: {done}/{total} repos - "
                               f"Matched: {stats['matched']}, "
                               f"SKILL.md found: {stats['total_skill_files']}")

    finally:
        shutil.rmtree(temp_base, ignore_errors=True)

    # Apply updates
    if not args.dry_run and all_updates:
        logger.info(f"\nApplying {len(all_updates)} updates...")
        for metadata_path, github_path, branch in all_updates:
            try:
                metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                metadata['github_path'] = github_path
                metadata['github_branch'] = branch
                metadata_path.write_text(
                    json.dumps(metadata, indent=2, ensure_ascii=False),
                    encoding='utf-8'
                )
            except Exception as e:
                logger.error(f"Error updating {metadata_path}: {e}")
    elif args.dry_run and all_updates:
        logger.info(f"\nWould update {len(all_updates)} skills:")
        for metadata_path, github_path, branch in all_updates[:20]:
            logger.info(f"  {metadata_path.parent.name} -> {github_path} ({branch})")
        if len(all_updates) > 20:
            logger.info(f"  ... and {len(all_updates) - 20} more")

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
    main()
