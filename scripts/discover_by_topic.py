#!/usr/bin/env python3
"""
Discover skills by GitHub Topics
Uses GitHub Search API to find repositories with claude-code-skills or claude-skills topics
"""

import os
import json
import time
import logging
import requests
from datetime import datetime
from pathlib import Path

from utils import normalize_name

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"

# Topics to search for
SKILL_TOPICS = [
    "claude-code-skills",
    "claude-skills",
    "agent-skills",
    "codex-skills",
]

# Search queries for finding SKILL.md files
CODE_SEARCH_QUERIES = [
    "filename:SKILL.md",
    "filename:SKILL.md path:.claude/skills",
    "filename:SKILL.md path:skills",
]


class GitHubTopicDiscovery:
    """Discover skills using GitHub Topics and Code Search"""

    def __init__(self, token=None):
        self.token = token or os.environ.get('GITHUB_TOKEN')
        self.session = requests.Session()
        self.session.headers['Accept'] = 'application/vnd.github.v3+json'
        if self.token:
            self.session.headers['Authorization'] = f'token {self.token}'
            logger.info("Using authenticated GitHub API")
        else:
            logger.warning("No token - rate limits will be strict (10 req/min)")

        self.discovered_repos = set()
        self.skills = []

    def _request(self, url, params=None):
        """Make rate-limited request"""
        time.sleep(2)  # Be nice to API
        try:
            resp = self.session.get(url, params=params, timeout=30)

            # Handle rate limiting
            if resp.status_code == 403:
                reset = int(resp.headers.get('X-RateLimit-Reset', 0))
                if reset:
                    wait = max(0, reset - time.time() + 1)
                    if wait < 3600:
                        logger.warning(f"Rate limited, waiting {wait:.0f}s")
                        time.sleep(wait)
                        return self._request(url, params)
                return None

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Request failed: {e}")
            return None

    def discover_by_topics(self, topics=None):
        """Discover repositories by GitHub topics"""
        topics = topics or SKILL_TOPICS

        for topic in topics:
            logger.info(f"Searching topic: {topic}")

            page = 1
            while page <= 10:  # Max 1000 results
                url = f"{GITHUB_API}/search/repositories"
                params = {
                    'q': f'topic:{topic}',
                    'sort': 'stars',
                    'order': 'desc',
                    'per_page': 100,
                    'page': page,
                }

                result = self._request(url, params)
                if not result:
                    break

                items = result.get('items', [])
                if not items:
                    break

                for repo in items:
                    full_name = repo['full_name']
                    if full_name not in self.discovered_repos:
                        self.discovered_repos.add(full_name)
                        logger.info(f"  Found: {full_name} ({repo.get('stargazers_count', 0)} stars)")

                total = result.get('total_count', 0)
                if page * 100 >= total:
                    break
                page += 1

        logger.info(f"Discovered {len(self.discovered_repos)} repositories from topics")
        return list(self.discovered_repos)

    def discover_by_code_search(self, queries=None):
        """Discover SKILL.md files using GitHub Code Search"""
        queries = queries or CODE_SEARCH_QUERIES

        for query in queries:
            logger.info(f"Code search: {query}")

            page = 1
            while page <= 10:
                url = f"{GITHUB_API}/search/code"
                params = {
                    'q': query,
                    'per_page': 100,
                    'page': page,
                }

                result = self._request(url, params)
                if not result:
                    break

                items = result.get('items', [])
                if not items:
                    break

                for item in items:
                    repo = item['repository']['full_name']
                    path = item['path']

                    if repo not in self.discovered_repos:
                        self.discovered_repos.add(repo)
                        logger.info(f"  Found: {repo} - {path}")

                total = result.get('total_count', 0)
                if page * 100 >= total:
                    break
                page += 1

        logger.info(f"Total discovered: {len(self.discovered_repos)} repositories")
        return list(self.discovered_repos)

    def get_skill_files_from_repo(self, repo):
        """Find all SKILL.md files in a repository"""
        skills = []

        # Common skill locations
        paths_to_check = [
            '',
            'skills',
            '.claude/skills',
            '.codex/skills',
        ]

        # First try to search the repo for SKILL.md files
        url = f"{GITHUB_API}/search/code"
        params = {
            'q': f'filename:SKILL.md repo:{repo}',
            'per_page': 100,
        }

        result = self._request(url, params)
        if result and result.get('items'):
            for item in result['items']:
                skills.append({
                    'repo': repo,
                    'path': item['path'],
                    'html_url': item['html_url'],
                })

        return skills

    def download_skill(self, repo, path, output_dir):
        """Download a SKILL.md file"""
        # Extract skill name from path
        parts = path.rsplit('/', 1)
        if len(parts) == 2:
            skill_dir = parts[0].split('/')[-1] if '/' in parts[0] else parts[0]
        else:
            skill_dir = repo.split('/')[-1]

        # Normalize to lowercase to prevent case conflicts on macOS/Windows
        skill_dir = normalize_name(skill_dir)

        # Try to fetch content
        for branch in ['main', 'master']:
            url = f"{GITHUB_RAW}/{repo}/{branch}/{path}"
            try:
                resp = self.session.get(url, timeout=15)
                if resp.status_code == 200:
                    content = resp.text

                    # Validate it's a skill file
                    if '---' not in content[:100] and 'name:' not in content[:500]:
                        continue

                    # Save skill
                    skill_path = output_dir / skill_dir
                    skill_path.mkdir(parents=True, exist_ok=True)

                    (skill_path / 'SKILL.md').write_text(content, encoding='utf-8')

                    # Save metadata
                    metadata = {
                        'name': skill_dir,
                        'repo': repo,
                        'path': path,
                        'source': f'github.com/{repo}',
                        'downloaded_at': datetime.utcnow().isoformat() + 'Z',
                    }
                    (skill_path / 'metadata.json').write_text(
                        json.dumps(metadata, indent=2), encoding='utf-8'
                    )

                    return True
            except Exception as e:
                logger.debug(f"Failed to fetch {url}: {e}")

        return False

    def run(self, output_dir='skills', output_json='sources/discovered.json'):
        """Run full discovery pipeline"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Phase 1: Discover by topics
        logger.info("=== Phase 1: Topic Discovery ===")
        self.discover_by_topics()

        # Phase 2: Discover by code search
        logger.info("\n=== Phase 2: Code Search ===")
        self.discover_by_code_search()

        # Phase 3: Download skills from discovered repos
        logger.info("\n=== Phase 3: Download Skills ===")
        downloaded = 0

        for repo in self.discovered_repos:
            logger.info(f"Scanning {repo}...")
            skill_files = self.get_skill_files_from_repo(repo)

            for skill in skill_files:
                if self.download_skill(repo, skill['path'], output_dir):
                    downloaded += 1
                    self.skills.append({
                        'repo': repo,
                        'path': skill['path'],
                    })
                    logger.info(f"  ✓ Downloaded: {skill['path']}")

        # Save discovery results
        Path(output_json).parent.mkdir(parents=True, exist_ok=True)
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump({
                'discovered_at': datetime.utcnow().isoformat() + 'Z',
                'total_repos': len(self.discovered_repos),
                'total_skills': len(self.skills),
                'repos': list(self.discovered_repos),
                'skills': self.skills,
            }, f, indent=2, ensure_ascii=False)

        logger.info(f"\n=== Summary ===")
        logger.info(f"Repositories discovered: {len(self.discovered_repos)}")
        logger.info(f"Skills downloaded: {downloaded}")
        logger.info(f"Results saved to: {output_json}")

        return self.skills


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Discover skills from GitHub')
    parser.add_argument('--token', help='GitHub token (or set GITHUB_TOKEN env)')
    parser.add_argument('--output', default='skills', help='Output directory')
    parser.add_argument('--json', default='sources/discovered.json', help='JSON output')

    args = parser.parse_args()

    discoverer = GitHubTopicDiscovery(token=args.token)
    discoverer.run(output_dir=args.output, output_json=args.json)


if __name__ == '__main__':
    main()
