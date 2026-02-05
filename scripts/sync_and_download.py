#!/usr/bin/env python3
"""
Complete sync and download pipeline.

1. Sync skills index from SkillsMP.com (32,000+ skills)
2. Download SKILL.md files with optimized patterns
3. Generate reports

Usage:
    # Full pipeline
    python scripts/sync_and_download.py

    # Only sync index (no download)
    python scripts/sync_and_download.py --sync-only

    # Only download (use existing index)
    python scripts/sync_and_download.py --download-only

Environment:
    GITHUB_TOKEN - GitHub personal access token for higher rate limits
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from crawler.skillsmp_sync import SkillsMPSync
from scripts.utils import normalize_name, ensure_unique_dir, build_skill_key


def sanitize_category(category: str) -> str:
    category = (category or "data").strip()
    if not category:
        category = "data"
    return category.replace("/", "-").replace("\\", "-").replace(":", "-")


def skill_key(skill: dict) -> str:
    repo = (skill.get("repo") or "").strip()
    path = (skill.get("path") or skill.get("github_path") or "").strip()
    if repo and path:
        return f"{repo}:{path}"
    if repo:
        return repo
    name = skill.get("name") or ""
    category = skill.get("category") or "data"
    return f"{category}:{name}"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('sync_and_download.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def sync_skillsmp(output_path: str, max_skills: int = 50000) -> int:
    """Sync skills from SkillsMP."""
    logger.info("=" * 60)
    logger.info("STEP 1: Syncing from SkillsMP.com")
    logger.info("=" * 60)

    syncer = SkillsMPSync()
    skills = syncer.sync(max_skills=max_skills)
    syncer.save(output_path)

    logger.info(f"Synced {len(skills)} skills to {output_path}")
    return len(skills)


def build_unified_registry(sources_dir: Path, output_path: Path) -> int:
    """Build unified registry from all sources."""
    logger.info("=" * 60)
    logger.info("STEP 2: Building unified registry")
    logger.info("=" * 60)

    all_skills = []
    seen = set()

    for source_file in sources_dir.glob("*.json"):
        logger.info(f"Loading {source_file.name}...")
        with open(source_file) as f:
            source = json.load(f)

        source_name = source.get("name", source_file.stem)

        for skill in source.get("skills", []):
            # Create unique key
            repo = skill.get("repo", "")
            name = skill.get("name", "")
            path = skill.get("path", "")
            key = f"{repo}/{path}/{name}"

            if key in seen:
                continue
            seen.add(key)

            all_skills.append({
                "name": name,
                "description": skill.get("description", ""),
                "repo": repo,
                "path": path,
                "category": skill.get("category", "development"),
                "tags": skill.get("tags", []),
                "stars": skill.get("stars", 0),
                "source": source_name,
                "featured": skill.get("featured", False),
            })

    # Sort by stars (descending) then name
    all_skills.sort(key=lambda x: (-x.get("stars", 0), x["name"].lower()))

    registry = {
        "version": "2.0.0",
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "total_count": len(all_skills),
        "skills": all_skills,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)

    logger.info(f"Built registry with {len(all_skills)} unique skills")
    return len(all_skills)


async def download_skills(registry_path: Path, output_dir: Path, github_token: str = "") -> dict:
    """Download skills using optimized downloader."""
    logger.info("=" * 60)
    logger.info("STEP 3: Downloading SKILL.md files")
    logger.info("=" * 60)

    # Import here to avoid circular imports
    import aiohttp
    from collections import defaultdict

    GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
    MAX_CONCURRENT = 100
    TIMEOUT = 15
    BATCH_SIZE = 300

    # Load registry
    with open(registry_path) as f:
        registry = json.load(f)

    skills = registry.get("skills", [])
    logger.info(f"Total skills in registry: {len(skills)}")

    # Check existing (across all categories)
    exclude = {".git", ".github-skills", ".template", ".templates", ".attic"}
    existing = set()
    for dirpath, dirnames, filenames in os.walk(output_dir):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        if "metadata.json" in filenames and "SKILL.md" in filenames:
            meta_path = Path(dirpath) / "metadata.json"
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
            existing.add(skill_key(meta))

    logger.info(f"Already downloaded: {len(existing)}")

    # Filter pending by repo/path key
    pending = [s for s in skills if skill_key(s) not in existing]
    logger.info(f"To download: {len(pending)}")

    if not pending:
        logger.info("Nothing to download!")
        return {"downloaded": 0, "failed": 0, "total": len(existing)}

    stats = {"downloaded": 0, "failed": 0, "skipped": len(existing)}
    failures = defaultdict(list)

    headers = {"User-Agent": "Claude-Skills-Registry/3.0"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT * 2)

    async def try_download(session: aiohttp.ClientSession, skill: dict) -> bool:
        name = skill["name"]
        # Normalize name to prevent case conflicts on macOS/Windows
        normalized_name = normalize_name(name)
        repo = skill.get("repo", "")
        path = skill.get("path", "")

        if not repo:
            failures["no_repo"].append(name)
            return False

        # Clean repo
        repo = repo.replace("https://github.com/", "").split("/tree/")[0].rstrip("/")

        # URL patterns
        patterns = []
        for branch in ["main", "master"]:
            if path:
                patterns.append(f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}/SKILL.md")
                patterns.append(f"{GITHUB_RAW_BASE}/{repo}/{branch}/{path}")

            patterns.extend([
                f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/skills/{name}/SKILL.md",
                f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/{name}/SKILL.md",
                f"{GITHUB_RAW_BASE}/{repo}/{branch}/skills/{name}/SKILL.md",
                f"{GITHUB_RAW_BASE}/{repo}/{branch}/{name}/SKILL.md",
                f"{GITHUB_RAW_BASE}/{repo}/{branch}/SKILL.md",
                f"{GITHUB_RAW_BASE}/{repo}/{branch}/.claude/SKILL.md",
            ])

        async with semaphore:
            for url in patterns[:12]:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=TIMEOUT)) as resp:
                        if resp.status == 200:
                            content = await resp.text()
                            if content and len(content) > 50 and ("---" in content[:50] or "#" in content[:100]):
                                # Valid content - save under category with normalized name
                                category = sanitize_category(skill.get("category", "data"))
                                category_dir = output_dir / category
                                category_dir.mkdir(parents=True, exist_ok=True)
                                key = build_skill_key(repo, path, name=name, category=category)
                                skill_dir = ensure_unique_dir(category_dir, normalized_name, key)
                                skill_dir.mkdir(parents=True, exist_ok=True)
                                (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
                                (skill_dir / "metadata.json").write_text(
                                    json.dumps({
                                        "name": name,
                                        "description": skill.get("description", ""),
                                        "repo": repo,
                                        "path": path,
                                        "category": skill.get("category", ""),
                                        "tags": skill.get("tags", []),
                                        "stars": skill.get("stars", 0),
                                        "source": skill.get("source", ""),
                                        "dir_name": skill_dir.name,
                                    }, indent=2, ensure_ascii=False),
                                    encoding="utf-8"
                                )
                                return True
                        elif resp.status == 403:
                            failures["rate_limited"].append(name)
                            return False
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    continue

            failures["not_found"].append(name)
            return False

    start_time = time.time()

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        for i in range(0, len(pending), BATCH_SIZE):
            batch = pending[i:i + BATCH_SIZE]
            batch_num = i // BATCH_SIZE + 1
            total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE

            tasks = [try_download(session, s) for s in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for r in results:
                if r is True:
                    stats["downloaded"] += 1
                else:
                    stats["failed"] += 1

            elapsed = time.time() - start_time
            rate = stats["downloaded"] / elapsed if elapsed > 0 else 0

            logger.info(
                f"Batch {batch_num}/{total_batches}: "
                f"✅ {stats['downloaded']} | ❌ {stats['failed']} | ⚡ {rate:.1f}/s"
            )

            await asyncio.sleep(0.2)

    # Final count
    final_count = sum(1 for _ in output_dir.rglob("SKILL.md"))

    logger.info("=" * 60)
    logger.info("DOWNLOAD COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Downloaded: {stats['downloaded']}")
    logger.info(f"Failed: {stats['failed']}")
    logger.info(f"Total skills: {final_count}")

    # Save failure report
    failure_report = {
        "timestamp": datetime.now().isoformat(),
        "stats": dict(stats),
        "failure_reasons": {k: len(v) for k, v in failures.items()},
        "failures": dict(failures),
    }
    report_path = output_dir.parent / "failure_report.json"
    with open(report_path, "w") as f:
        json.dump(failure_report, f, indent=2)
    logger.info(f"Failure report saved to {report_path}")

    stats["total"] = final_count
    return stats


def main():
    parser = argparse.ArgumentParser(description="Sync and download Claude skills")
    parser.add_argument("--sync-only", action="store_true", help="Only sync index, don't download")
    parser.add_argument("--download-only", action="store_true", help="Only download, use existing index")
    parser.add_argument("--max-skills", type=int, default=50000, help="Max skills to sync from SkillsMP")
    args = parser.parse_args()

    # Paths
    script_dir = Path(__file__).parent
    registry_dir = script_dir.parent
    sources_dir = registry_dir / "sources"
    registry_path = registry_dir / "registry.json"
    output_dir = registry_dir / "skills"
    skillsmp_path = sources_dir / "skillsmp.json"

    github_token = os.environ.get("GITHUB_TOKEN", "")

    start_time = time.time()

    # Step 1: Sync from SkillsMP
    if not args.download_only:
        sync_skillsmp(str(skillsmp_path), max_skills=args.max_skills)

    # Step 2: Build unified registry
    if not args.download_only:
        build_unified_registry(sources_dir, registry_path)

    # Step 3: Download skills
    if not args.sync_only:
        stats = asyncio.run(download_skills(registry_path, output_dir, github_token))

    elapsed = time.time() - start_time

    logger.info("=" * 60)
    logger.info("PIPELINE COMPLETE")
    logger.info(f"Total time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
