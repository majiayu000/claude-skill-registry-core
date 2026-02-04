#!/usr/bin/env python3
"""
GitHub Code Search - Find all SKILL.md files across GitHub
Uses the GitHub Code Search API with authentication
"""

import os
import json
import time
import requests
from pathlib import Path
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

# Multiple search queries to maximize coverage
SEARCH_QUERIES = [
    "filename:SKILL.md",
    "filename:SKILL.md path:skills",
    "filename:SKILL.md path:.claude",
    "filename:SKILL.md path:.codex",
    "filename:SKILL.md claude",
    "filename:SKILL.md agent",
]


class GitHubCodeSearcher:
    def __init__(self, token):
        self.token = token
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28"
        })
        self.found_skills = {}  # repo:path -> skill_info
        self.repos_meta = {}    # repo -> metadata

    def check_rate_limit(self):
        """Check current rate limit status"""
        resp = self.session.get(f"{GITHUB_API}/rate_limit")
        if resp.status_code == 200:
            data = resp.json()
            search = data.get("resources", {}).get("search", {})
            core = data.get("resources", {}).get("core", {})
            logger.info(f"Rate limit - Search: {search.get('remaining')}/{search.get('limit')}, Core: {core.get('remaining')}/{core.get('limit')}")
            return search.get("remaining", 0), core.get("remaining", 0)
        return 0, 0

    def search_code(self, query, max_pages=10):
        """Search for code using GitHub Code Search API"""
        results = []

        for page in range(1, max_pages + 1):
            url = f"{GITHUB_API}/search/code"
            params = {
                "q": query,
                "per_page": 100,
                "page": page
            }

            logger.info(f"  Searching: '{query}' (page {page})")

            try:
                resp = self.session.get(url, params=params)

                if resp.status_code == 403:
                    # Rate limited
                    reset_time = int(resp.headers.get("X-RateLimit-Reset", 0))
                    wait = max(0, reset_time - time.time() + 5)
                    if wait < 300:  # Wait up to 5 minutes
                        logger.warning(f"  Rate limited, waiting {wait:.0f}s...")
                        time.sleep(wait)
                        continue
                    else:
                        logger.error("  Rate limit too long, stopping")
                        break

                if resp.status_code != 200:
                    logger.error(f"  Error {resp.status_code}: {resp.text[:200]}")
                    break

                data = resp.json()
                items = data.get("items", [])
                total = data.get("total_count", 0)

                if not items:
                    break

                results.extend(items)
                logger.info(f"  Found {len(items)} items (total: {total})")

                if len(items) < 100:
                    break

                # Be nice to the API
                time.sleep(2)

            except Exception as e:
                logger.error(f"  Request failed: {e}")
                break

        return results

    def get_repo_metadata(self, repo):
        """Get repository metadata"""
        if repo in self.repos_meta:
            return self.repos_meta[repo]

        try:
            resp = self.session.get(f"{GITHUB_API}/repos/{repo}")
            if resp.status_code == 200:
                data = resp.json()
                self.repos_meta[repo] = {
                    "stars": data.get("stargazers_count", 0),
                    "description": data.get("description", ""),
                    "default_branch": data.get("default_branch", "main"),
                    "updated_at": data.get("pushed_at", ""),
                }
                return self.repos_meta[repo]
        except Exception as e:
            logger.debug(f"Failed to get repo metadata for {repo}: {e}")

        return {"stars": 0, "description": "", "default_branch": "main", "updated_at": ""}

    def run_all_searches(self):
        """Run all search queries"""
        logger.info("=" * 60)
        logger.info("GITHUB CODE SEARCH FOR SKILL.MD")
        logger.info("=" * 60)

        # Check rate limit first
        search_remaining, core_remaining = self.check_rate_limit()
        if search_remaining < 10:
            logger.error("Search rate limit too low, please wait")
            return

        for query in SEARCH_QUERIES:
            logger.info(f"\n--- Query: {query} ---")
            results = self.search_code(query, max_pages=10)

            for item in results:
                repo = item.get("repository", {}).get("full_name", "")
                path = item.get("path", "")

                if not repo or not path:
                    continue

                key = f"{repo}:{path}"
                if key not in self.found_skills:
                    self.found_skills[key] = {
                        "repo": repo,
                        "path": path,
                        "html_url": item.get("html_url", ""),
                        "sha": item.get("sha", ""),
                    }

            logger.info(f"  Total unique skills so far: {len(self.found_skills)}")
            time.sleep(3)  # Wait between queries

        logger.info(f"\n=== SEARCH COMPLETE ===")
        logger.info(f"Total unique SKILL.md files found: {len(self.found_skills)}")

    def enrich_with_metadata(self):
        """Add repository metadata to found skills"""
        logger.info("\nFetching repository metadata...")

        repos = set(s["repo"] for s in self.found_skills.values())
        logger.info(f"Unique repositories: {len(repos)}")

        for i, repo in enumerate(repos, 1):
            if i % 50 == 0:
                logger.info(f"  Progress: {i}/{len(repos)}")
            self.get_repo_metadata(repo)
            time.sleep(0.1)  # Be nice

        # Enrich skills with metadata
        for key, skill in self.found_skills.items():
            meta = self.repos_meta.get(skill["repo"], {})
            skill["stars"] = meta.get("stars", 0)
            skill["description"] = meta.get("description", "")
            skill["default_branch"] = meta.get("default_branch", "main")

    def save_results(self, output_path):
        """Save results to JSON"""
        skills_list = sorted(
            self.found_skills.values(),
            key=lambda x: x.get("stars", 0),
            reverse=True
        )

        output = {
            "name": "GitHub Code Search Results",
            "searched_at": datetime.utcnow().isoformat() + "Z",
            "queries": SEARCH_QUERIES,
            "total_count": len(skills_list),
            "skills": skills_list,
        }

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved {len(skills_list)} skills to {output_path}")


def main():
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        logger.error("GITHUB_TOKEN not set")
        return

    output_path = "sources/github_search.json"

    searcher = GitHubCodeSearcher(token)
    searcher.run_all_searches()
    searcher.enrich_with_metadata()
    searcher.save_results(output_path)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Skills found: {len(searcher.found_skills)}")
    print(f"  Repos found:  {len(searcher.repos_meta)}")
    print(f"  Output:       {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
