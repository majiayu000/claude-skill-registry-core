#!/usr/bin/env python3
"""
Download skills from known GitHub sources directly.
Uses raw.githubusercontent.com to avoid API rate limits.
"""

import urllib.request
import json
import re
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from utils import normalize_name, ensure_unique_dir, build_skill_key

GITHUB_RAW = "https://raw.githubusercontent.com"

# Known skill sources with their structure
SOURCES = [
    # Official Anthropic skills
    {"repo": "anthropics/skills", "path": "skills", "branch": "main"},
    # Community collections
    {"repo": "obra/superpowers", "path": ".claude/skills", "branch": "main"},
    {"repo": "alirezarezvani/claude-skills", "path": "skills", "branch": "main"},
    {"repo": "daymade/claude-code-skills", "path": "skills", "branch": "main"},
    {"repo": "mhattingpete/claude-skills-marketplace", "path": "skills", "branch": "main"},
    {"repo": "Tony363/SuperClaude", "path": ".claude/skills", "branch": "main"},
    {"repo": "jackspace/ClaudeSkillz", "path": ".claude/skills", "branch": "main"},
]

def fetch_url(url, timeout=15):
    """Fetch URL content."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Claude-Skills-Downloader/2.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        return None

def find_skill_dirs_from_readme(repo, branch="main"):
    """Parse README to find skill directories."""
    readme_url = f"{GITHUB_RAW}/{repo}/{branch}/README.md"
    content = fetch_url(readme_url)
    if not content:
        return []

    # Find links to skill directories
    skill_dirs = []
    patterns = [
        r'\[([^\]]+)\]\(\./skills/([^)]+)\)',
        r'\[([^\]]+)\]\(skills/([^)]+)\)',
        r'`skills/([^`]+)`',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content)
        for match in matches:
            if isinstance(match, tuple):
                skill_dirs.append(match[-1].rstrip('/'))
            else:
                skill_dirs.append(match.rstrip('/'))

    return list(set(skill_dirs))

def download_skill(repo, skill_path, skill_name, output_dir, category="community"):
    """Download a single skill."""
    # Normalize names to prevent case conflicts on macOS/Windows
    skill_name = normalize_name(skill_name)
    category = normalize_name(category)

    # Try different URL patterns
    urls = [
        f"{GITHUB_RAW}/{repo}/main/{skill_path}/SKILL.md",
        f"{GITHUB_RAW}/{repo}/master/{skill_path}/SKILL.md",
        f"{GITHUB_RAW}/{repo}/main/{skill_path}/{skill_name}/SKILL.md",
    ]

    content = None
    for url in urls:
        content = fetch_url(url)
        if content and ("---" in content[:100] or "description:" in content[:500]):
            break
        content = None

    if not content:
        return False

    # Save skill (case-safe)
    key = build_skill_key(repo, skill_path, name=skill_name, category=category)
    skill_dir = ensure_unique_dir(output_dir / category, skill_name, key)
    skill_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")

    metadata = {
        "name": skill_name,
        "repo": repo,
        "category": category,
        "source": f"github.com/{repo}",
        "path": skill_path,
        "dir_name": skill_dir.name,
    }
    (skill_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    return True

def scan_repo_for_skills(repo, base_path, branch="main"):
    """Scan a repo for skill directories by trying common patterns."""
    skills = []

    # Try to find skill directories
    test_names = [
        "pdf", "docx", "xlsx", "pptx",  # Document skills
        "commit", "review", "test", "debug",  # Dev skills
        "brainstorming", "planning", "writing",  # Workflow skills
    ]

    for name in test_names:
        url = f"{GITHUB_RAW}/{repo}/{branch}/{base_path}/{name}/SKILL.md"
        content = fetch_url(url)
        if content and "---" in content[:100]:
            skills.append(name)

    return skills

def main():
    output_dir = Path(__file__).parent.parent / "skills"

    # Count existing
    existing = sum(1 for _ in output_dir.rglob("SKILL.md"))
    print(f"📁 Existing skills: {existing}")

    total_new = 0

    for source in SOURCES:
        repo = source["repo"]
        base_path = source["path"]
        branch = source.get("branch", "main")

        print(f"\n🔍 Scanning {repo}...")

        # First try to get skills from README
        skill_names = find_skill_dirs_from_readme(repo, branch)

        # Also scan for common skill names
        scanned = scan_repo_for_skills(repo, base_path, branch)
        skill_names.extend(scanned)
        skill_names = list(set(skill_names))

        if not skill_names:
            # Try listing known skill types
            skill_names = ["pdf", "docx", "xlsx", "pptx", "commit", "review", "test"]

        print(f"   Found {len(skill_names)} potential skills")

        downloaded = 0
        for skill_name in skill_names:
            # Clean skill name
            skill_name = skill_name.split("/")[0].strip()
            if not skill_name or skill_name.startswith("."):
                continue

            skill_path = f"{base_path}/{skill_name}"
            category = "official" if "anthropics" in repo else "community"

            if download_skill(repo, skill_path, skill_name, output_dir, category):
                downloaded += 1
                print(f"   ✅ {skill_name}")

        total_new += downloaded
        print(f"   Downloaded: {downloaded}")

        time.sleep(1)  # Be nice to GitHub

    final_count = sum(1 for _ in output_dir.rglob("SKILL.md"))
    print(f"\n✨ Done! Total skills: {final_count} (new: {total_new})")

if __name__ == "__main__":
    main()
