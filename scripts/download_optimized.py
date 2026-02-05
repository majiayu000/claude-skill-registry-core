#!/usr/bin/env python3
"""
Optimized Skill Downloader v3.0

Improvements over previous versions:
1. Extended URL patterns (20+ patterns covering various repo structures)
2. GitHub API tree lookup for unknown structures
3. Detailed failure logging with reasons
4. Smarter caching of repo structures
5. Parallel processing with adaptive rate limiting
"""

import asyncio
import aiohttp
import json
import os
import sys
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple
from datetime import datetime
from collections import defaultdict
import time
import logging

from utils import normalize_name, normalize_category, ensure_unique_dir, build_skill_key


# Configuration
MAX_CONCURRENT = 100
TIMEOUT = 15
RETRY_ATTEMPTS = 2
BATCH_SIZE = 300

GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
GITHUB_API_BASE = "https://api.github.com"

# GitHub token for higher rate limits (optional)
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('download_optimized.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class FailureTracker:
    """Track and categorize failures for analysis."""

    def __init__(self):
        self.failures: Dict[str, List[dict]] = defaultdict(list)
        self.stats = defaultdict(int)

    def add(self, skill_name: str, repo: str, reason: str, details: str = ""):
        self.failures[reason].append({
            "name": skill_name,
            "repo": repo,
            "details": details,
            "timestamp": datetime.now().isoformat()
        })
        self.stats[reason] += 1

    def save(self, path: str):
        output = {
            "summary": dict(self.stats),
            "total_failures": sum(self.stats.values()),
            "failures_by_reason": {k: v for k, v in self.failures.items()},
            "generated_at": datetime.now().isoformat()
        }
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False)
        logger.info(f"Failure report saved to {path}")

    def print_summary(self):
        print("\n" + "=" * 50)
        print("FAILURE SUMMARY")
        print("=" * 50)
        for reason, count in sorted(self.stats.items(), key=lambda x: -x[1]):
            print(f"  {reason}: {count}")
        print(f"  TOTAL: {sum(self.stats.values())}")
        print("=" * 50)


class RepoStructureCache:
    """Cache repo structures to avoid redundant API calls."""

    def __init__(self):
        self.cache: Dict[str, List[str]] = {}
        self.failed_repos: Set[str] = set()

    def get(self, repo: str) -> Optional[List[str]]:
        return self.cache.get(repo)

    def set(self, repo: str, files: List[str]):
        self.cache[repo] = files

    def mark_failed(self, repo: str):
        self.failed_repos.add(repo)

    def is_failed(self, repo: str) -> bool:
        return repo in self.failed_repos


# Extended URL patterns
def get_url_patterns(repo: str, skill_name: str, skill_path: str = "") -> List[str]:
    """Generate comprehensive URL patterns to try."""
    patterns = []
    branches = ["main", "master"]

    # If we have an explicit path from the source, try it first
    if skill_path:
        for branch in branches:
            # Direct path
            patterns.append(f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_path}/SKILL.md")
            patterns.append(f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_path}")
            # Maybe path is the skill name within a skills folder
            if not skill_path.endswith("SKILL.md"):
                patterns.append(f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/skills/{skill_path}/SKILL.md")

    for branch in branches:
        # Standard Claude Code locations
        patterns.extend([
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/skills/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/SKILL.md",
        ])

        # Common skill directory patterns
        patterns.extend([
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/skills/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/skill/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/claude-skills/{skill_name}/SKILL.md",
        ])

        # Direct in repo root
        patterns.extend([
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/SKILL.md",
        ])

        # Src patterns
        patterns.extend([
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/src/skills/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/src/{skill_name}/SKILL.md",
        ])

        # Package patterns (monorepo)
        patterns.extend([
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/packages/{skill_name}/SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/agents/{skill_name}/SKILL.md",
        ])

        # Alternative file names
        patterns.extend([
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_name}.SKILL.md",
            f"{GITHUB_RAW_BASE}/{repo}/{branch}/skills/{skill_name}.SKILL.md",
        ])

    # Remove duplicates while preserving order
    seen = set()
    unique_patterns = []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            unique_patterns.append(p)

    return unique_patterns


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    semaphore: asyncio.Semaphore,
) -> Tuple[Optional[str], int]:
    """Fetch URL with status code."""
    async with semaphore:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
                    if resp.status == 200:
                        return await resp.text(), 200
                    elif resp.status == 404:
                        return None, 404
                    elif resp.status == 403:
                        # Rate limited
                        return None, 403
                    elif resp.status == 429:
                        # Too many requests - wait and retry
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        return None, resp.status
            except asyncio.TimeoutError:
                if attempt < RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(0.5)
                continue
            except Exception as e:
                if attempt < RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(0.5)
                continue
    return None, -1  # -1 = network error


async def get_repo_tree(
    session: aiohttp.ClientSession,
    repo: str,
    semaphore: asyncio.Semaphore,
    cache: RepoStructureCache,
) -> Optional[List[str]]:
    """Get repo file tree via GitHub API."""

    if cache.is_failed(repo):
        return None

    cached = cache.get(repo)
    if cached is not None:
        return cached

    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    for branch in ["main", "master"]:
        url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{branch}?recursive=1"

        async with semaphore:
            try:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        files = [item["path"] for item in data.get("tree", []) if item["type"] == "blob"]
                        cache.set(repo, files)
                        return files
                    elif resp.status == 404:
                        continue
                    elif resp.status == 403:
                        # Rate limited - mark as failed to avoid retrying
                        cache.mark_failed(repo)
                        return None
            except Exception:
                continue

    cache.mark_failed(repo)
    return None


def find_skill_in_tree(files: List[str], skill_name: str) -> Optional[str]:
    """Find SKILL.md path in file tree."""
    skill_patterns = [
        f".claude/skills/{skill_name}/SKILL.md",
        f".claude/{skill_name}/SKILL.md",
        f"skills/{skill_name}/SKILL.md",
        f"{skill_name}/SKILL.md",
        f"SKILL.md",
        f"{skill_name}.SKILL.md",
    ]

    for pattern in skill_patterns:
        if pattern in files:
            return pattern

    # Fuzzy match - find any SKILL.md containing the skill name
    for f in files:
        if f.endswith("SKILL.md") and skill_name.lower() in f.lower():
            return f

    # Last resort - find any SKILL.md
    skill_files = [f for f in files if f.endswith("SKILL.md")]
    if len(skill_files) == 1:
        return skill_files[0]

    return None


def is_valid_skill_content(content: str) -> bool:
    """Validate that content is a proper SKILL.md file."""
    if not content or len(content) < 50:
        return False

    # Check for frontmatter or key indicators
    indicators = [
        content.strip().startswith("---"),
        "description:" in content[:500].lower(),
        "# " in content[:200],  # Has a header
        "skill" in content[:500].lower(),
    ]

    return any(indicators)


async def download_skill(
    session: aiohttp.ClientSession,
    skill: dict,
    output_dir: Path,
    semaphore: asyncio.Semaphore,
    api_semaphore: asyncio.Semaphore,
    downloaded_set: Set[str],
    failure_tracker: FailureTracker,
    repo_cache: RepoStructureCache,
    use_api: bool = True,
) -> bool:
    """Download a single skill with comprehensive fallback."""

    name = skill["name"]
    category = normalize_category(skill.get("category", "other")) or "other"
    repo = skill.get("repo", skill.get("install", ""))
    path = skill.get("path", "")

    skill_key = f"{category}/{name}"

    if skill_key in downloaded_set:
        return False

    if not repo:
        failure_tracker.add(name, "", "no_repo", "Missing repo field")
        return False

    # Clean repo path
    repo = repo.split("/tree/")[0]  # Remove branch info if present
    if repo.startswith("https://github.com/"):
        repo = repo.replace("https://github.com/", "")
    repo = repo.rstrip("/")

    key = build_skill_key(repo, path, name=name, category=category)
    skill_dir = ensure_unique_dir(output_dir / category, name, key, repo=repo)
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        downloaded_set.add(skill_key)
        return False

    # Try URL patterns first (fast)
    patterns = get_url_patterns(repo, name, path)

    for url in patterns[:10]:  # Try top 10 patterns
        content, status = await fetch_url(session, url, semaphore)
        if content and is_valid_skill_content(content):
            # Success! Extract the GitHub path from the URL
            # URL format: https://raw.githubusercontent.com/{repo}/{branch}/{path}
            github_path = ""
            try:
                url_parts = url.replace(GITHUB_RAW_BASE + "/", "").split("/")
                if len(url_parts) > 3:
                    # Skip repo (2 parts) and branch (1 part), get the rest
                    github_path = "/".join(url_parts[3:])
                    # Remove SKILL.md from path to get directory
                    if github_path.endswith("/SKILL.md"):
                        github_path = github_path[:-9]
                    elif github_path == "SKILL.md":
                        github_path = ""
            except Exception:
                pass

            skill_dir.mkdir(parents=True, exist_ok=True)
            skill_file.write_text(content, encoding="utf-8")

            metadata = {
                "name": name,
                "description": skill.get("description", ""),
                "repo": repo,
                "category": category,
                "tags": skill.get("tags", []),
                "stars": skill.get("stars", 0),
                "source": skill.get("source", ""),
                "github_path": github_path,  # Original GitHub path
                "dir_name": skill_dir.name,
            }
            (skill_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )

            downloaded_set.add(skill_key)
            return True

        if status == 403:
            failure_tracker.add(name, repo, "rate_limited", url)
            return False

    # If fast patterns failed and API is enabled, try GitHub API
    if use_api and GITHUB_TOKEN:
        files = await get_repo_tree(session, repo, api_semaphore, repo_cache)

        if files:
            skill_path = find_skill_in_tree(files, name)
            if skill_path:
                for branch in ["main", "master"]:
                    url = f"{GITHUB_RAW_BASE}/{repo}/{branch}/{skill_path}"
                    content, status = await fetch_url(session, url, semaphore)
                    if content and is_valid_skill_content(content):
                        skill_dir.mkdir(parents=True, exist_ok=True)
                        skill_file.write_text(content, encoding="utf-8")

                        # Remove SKILL.md from path to get directory
                        github_path = skill_path
                        if github_path.endswith("/SKILL.md"):
                            github_path = github_path[:-9]
                        elif github_path == "SKILL.md":
                            github_path = ""

                        metadata = {
                            "name": name,
                            "description": skill.get("description", ""),
                            "repo": repo,
                            "category": category,
                            "tags": skill.get("tags", []),
                            "stars": skill.get("stars", 0),
                            "source": skill.get("source", ""),
                            "github_path": github_path,  # Original GitHub path
                            "dir_name": skill_dir.name,
                        }
                        (skill_dir / "metadata.json").write_text(
                            json.dumps(metadata, indent=2, ensure_ascii=False),
                            encoding="utf-8"
                        )

                        downloaded_set.add(skill_key)
                        return True
            else:
                failure_tracker.add(name, repo, "no_skill_in_tree", f"Files found: {len(files)}")
        else:
            failure_tracker.add(name, repo, "repo_not_found", "API tree fetch failed")
    else:
        failure_tracker.add(name, repo, "patterns_exhausted", f"Tried {len(patterns[:10])} patterns")

    return False


async def main():
    # Paths
    script_dir = Path(__file__).parent
    registry_dir = script_dir.parent
    registry_path = registry_dir / "registry.json"
    output_dir = registry_dir / "skills"

    # Load registry
    with open(registry_path, "r", encoding="utf-8") as f:
        registry = json.load(f)

    skills = registry.get("skills", [])

    # Also load from skillsmp if available
    skillsmp_path = registry_dir / "sources" / "skillsmp.json"
    if skillsmp_path.exists():
        with open(skillsmp_path, "r", encoding="utf-8") as f:
            skillsmp = json.load(f)
            skills.extend(skillsmp.get("skills", []))

    # Deduplicate by name
    seen_names = set()
    unique_skills = []
    for s in skills:
        if s["name"] not in seen_names:
            seen_names.add(s["name"])
            unique_skills.append(s)
    skills = unique_skills

    logger.info(f"Total skills to process: {len(skills)}")

    # Check existing
    output_dir.mkdir(exist_ok=True)
    existing = sum(1 for _ in output_dir.rglob("SKILL.md")) if output_dir.exists() else 0
    logger.info(f"Already downloaded: {existing}")

    # Initialize trackers
    downloaded_set: Set[str] = set()
    failure_tracker = FailureTracker()
    repo_cache = RepoStructureCache()

    # Pre-populate downloaded set
    if output_dir.exists():
        for skill_md in output_dir.rglob("SKILL.md"):
            skill_dir = skill_md.parent
            category = skill_dir.parent.name
            name = skill_dir.name
            downloaded_set.add(f"{category}/{name}")

    # Semaphores
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    api_semaphore = asyncio.Semaphore(10)  # Lower limit for API calls

    # Headers
    headers = {
        "User-Agent": "Claude-Skills-Registry-Downloader/3.0",
        "Accept": "text/plain,application/json",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
        logger.info("Using GitHub token for higher rate limits")
    else:
        logger.warning("No GITHUB_TOKEN set - API features limited")

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT * 2, limit_per_host=30)

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    start_time = time.time()

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        for i in range(0, len(skills), BATCH_SIZE):
            batch = skills[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(skills) + BATCH_SIZE - 1) // BATCH_SIZE

            logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} skills)")

            tasks = [
                download_skill(
                    session, skill, output_dir, semaphore, api_semaphore,
                    downloaded_set, failure_tracker, repo_cache,
                    use_api=bool(GITHUB_TOKEN)
                )
                for skill in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if r is True:
                    stats["downloaded"] += 1
                elif r is False:
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1

            elapsed = time.time() - start_time
            rate = (stats["downloaded"] + stats["skipped"]) / elapsed if elapsed > 0 else 0

            logger.info(
                f"Progress: ✅ {stats['downloaded']} downloaded | "
                f"⏭️ {stats['skipped']} skipped | "
                f"❌ {stats['failed']} errors | "
                f"⚡ {rate:.1f}/s"
            )

            # Small delay between batches
            await asyncio.sleep(0.3)

    # Final stats
    elapsed = time.time() - start_time
    final_count = sum(1 for _ in output_dir.rglob("SKILL.md"))

    print()
    print("=" * 60)
    print("DOWNLOAD COMPLETE")
    print("=" * 60)
    print(f"  Total skills in index: {len(skills)}")
    print(f"  Downloaded this run:   {stats['downloaded']}")
    print(f"  Already existed:       {existing}")
    print(f"  Final total:           {final_count}")
    print(f"  Failed/Not found:      {sum(failure_tracker.stats.values())}")
    print(f"  Time:                  {elapsed:.1f}s")
    print(f"  Rate:                  {stats['downloaded'] / elapsed:.1f} skills/s" if elapsed > 0 else "")
    print("=" * 60)

    # Save failure report
    failure_tracker.print_summary()
    failure_tracker.save(str(registry_dir / "failure_report.json"))

    # Save repo cache for debugging
    cache_path = registry_dir / "repo_cache.json"
    with open(cache_path, "w") as f:
        json.dump({
            "cached_repos": len(repo_cache.cache),
            "failed_repos": list(repo_cache.failed_repos),
        }, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
