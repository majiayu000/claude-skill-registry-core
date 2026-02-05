#!/usr/bin/env python3
"""
Download skills from GitHub search results
"""

import os
import json
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime
import logging

from utils import normalize_name, ensure_unique_dir, build_skill_key

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

GITHUB_RAW = "https://raw.githubusercontent.com"
MAX_CONCURRENT = 30

CATEGORY_KEYWORDS = {
    "development": ["dev", "code", "programming", "api", "sdk", "framework", "typescript", "python", "rust", "go", "java"],
    "testing": ["test", "qa", "quality", "spec", "jest", "pytest", "playwright"],
    "devops": ["devops", "ci", "cd", "docker", "kubernetes", "deploy", "aws", "terraform"],
    "security": ["security", "auth", "crypto", "vulnerability", "audit", "pentest"],
    "documents": ["doc", "pdf", "word", "excel", "pptx", "xlsx", "docx", "markdown"],
    "data": ["data", "analytics", "sql", "database", "etl", "ml", "ai"],
    "design": ["design", "ui", "ux", "css", "frontend", "figma", "react"],
    "productivity": ["productivity", "automation", "workflow", "task"],
    "product": ["product", "prd", "roadmap", "feature"],
    "marketing": ["marketing", "seo", "content", "social"],
}


def guess_category(text):
    text = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text:
                return category
    return "other"


def parse_frontmatter(content):
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


async def download_skill(session, skill, skills_dir, semaphore, stats):
    async with semaphore:
        repo = skill.get("repo", "")
        path = skill.get("path", "")
        branch = skill.get("default_branch", "main")
        stars = skill.get("stars", 0)

        if not repo or not path:
            stats["skipped"] += 1
            return

        # Try to download
        for try_branch in [branch, "main", "master"]:
            url = f"{GITHUB_RAW}/{repo}/{try_branch}/{path}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        content = await resp.text()

                        # Validate
                        if len(content) < 50:
                            stats["skipped"] += 1
                            return

                        # Parse metadata
                        meta = parse_frontmatter(content)

                        # Determine name
                        skill_name = meta.get("name", "")
                        if not skill_name:
                            # Use directory name
                            parts = path.rsplit("/", 1)
                            if len(parts) == 2:
                                skill_name = parts[0].split("/")[-1]
                            else:
                                skill_name = repo.split("/")[-1]
                        skill_name = normalize_name(skill_name)

                        # Determine category
                        category = normalize_name(meta.get("category", ""))
                        if not category or category == "unknown":
                            category = guess_category(path + " " + content[:500])

                        # Target path (case-safe)
                        key = build_skill_key(repo, path, name=skill_name, category=category)
                        target_dir = ensure_unique_dir(skills_dir / category, skill_name, key)
                        target_file = target_dir / "SKILL.md"

                        if target_file.exists():
                            stats["skipped"] += 1
                            return

                        # Save
                        target_dir.mkdir(parents=True, exist_ok=True)
                        target_file.write_text(content, encoding="utf-8")

                        # Save metadata
                        meta_out = {
                            "name": skill_name,
                            "description": meta.get("description", "")[:200],
                            "category": category,
                            "repo": repo,
                            "path": path,
                            "stars": stars,
                            "source": f"github.com/{repo}",
                            "dir_name": target_dir.name,
                            "downloaded_at": datetime.utcnow().isoformat() + "Z",
                        }
                        (target_dir / "metadata.json").write_text(
                            json.dumps(meta_out, indent=2, ensure_ascii=False),
                            encoding="utf-8"
                        )

                        stats["downloaded"] += 1
                        return

            except Exception as e:
                pass

        stats["failed"] += 1


async def main():
    script_dir = Path(__file__).parent
    project_dir = script_dir.parent
    skills_dir = project_dir / "skills"
    search_file = project_dir / "sources" / "github_search.json"

    if not search_file.exists():
        logger.error(f"Search results not found: {search_file}")
        return

    # Load search results
    with open(search_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    skills = data.get("skills", [])
    logger.info(f"Loaded {len(skills)} skills from search results")

    # Filter out already downloaded
    existing = set()
    for skill_file in skills_dir.rglob("metadata.json"):
        try:
            meta = json.loads(skill_file.read_text())
            key = f"{meta.get('repo', '')}:{meta.get('path', '')}"
            existing.add(key)
        except:
            pass

    to_download = []
    for skill in skills:
        key = f"{skill.get('repo', '')}:{skill.get('path', '')}"
        if key not in existing:
            to_download.append(skill)

    logger.info(f"Already have: {len(existing)}")
    logger.info(f"To download: {len(to_download)}")

    if not to_download:
        logger.info("Nothing new to download")
        return

    # Download
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with aiohttp.ClientSession() as session:
        tasks = [download_skill(session, s, skills_dir, semaphore, stats) for s in to_download]

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            await coro
            if i % 100 == 0:
                logger.info(f"Progress: {i}/{len(to_download)} (✅ {stats['downloaded']} ⏭️ {stats['skipped']} ❌ {stats['failed']})")

    # Summary
    total = sum(1 for _ in skills_dir.rglob("SKILL.md"))

    print("\n" + "=" * 60)
    print("DOWNLOAD COMPLETE")
    print("=" * 60)
    print(f"  Downloaded: {stats['downloaded']}")
    print(f"  Skipped:    {stats['skipped']}")
    print(f"  Failed:     {stats['failed']}")
    print("-" * 60)
    print(f"  TOTAL SKILLS: {total}")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
