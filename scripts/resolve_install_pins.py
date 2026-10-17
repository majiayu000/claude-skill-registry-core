#!/usr/bin/env python3
"""Pin skill guides to the upstream commit whose SKILL.md matches the scanned archive.

A guide is pinned only when the archived SKILL.md is byte-identical (same git
blob) to a SKILL.md in the upstream repository at its branch head, and that
skill directory contains no other files. The pin records the upstream commit,
path and the SHA-256 of those bytes, so a well-known discovery index can point
installers at the author's repository while guaranteeing the scanned content.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml
from utils import split_frontmatter_content
from verify_upstream_assets import GitHubApiError, GitHubClient

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DEFAULT_MAX_AGE_DAYS = 7


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def frontmatter_identity(data: bytes) -> tuple[str, str] | None:
    """Return (name, description) when they satisfy the discovery index rules."""
    try:
        raw, _ = split_frontmatter_content(data.decode("utf-8"))
        meta = yaml.safe_load(raw) if raw is not None else None
    except (UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(meta, dict):
        return None
    name, description = meta.get("name"), meta.get("description")
    if not isinstance(name, str) or not SKILL_NAME_RE.fullmatch(name) or len(name) > 64:
        return None
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        return None
    return name, description.strip()


def load_archive_paths(docs_dir: Path) -> dict[str, str]:
    paths = {}
    for shard in sorted((docs_dir / "skill-detail-shards").glob("*.json")):
        for record in json.loads(shard.read_text(encoding="utf-8")).get("skills", []):
            if record.get("id") and record.get("archive_path"):
                paths[record["id"]] = record["archive_path"]
    return paths


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def current_pin(existing: dict[str, dict], record_id: str, archive_path: str, data: bytes) -> dict | None:
    """A previous pin, only while it still describes the archive bytes behind the guide."""
    previous = existing.get(record_id)
    if previous and previous.get("archive_path") == archive_path and previous.get("digest") == sha256_digest(data):
        return previous
    return None


def match_upstream(entries: list[dict], blob: str) -> dict[str, Any]:
    """Locate the archived blob in an upstream tree and check the skill is SKILL.md only."""
    blobs = [e for e in entries if e.get("type") == "blob" and isinstance(e.get("path"), str)]
    hits = sorted(e["path"] for e in blobs if e["path"].rsplit("/", 1)[-1] == "SKILL.md" and e.get("sha") == blob)
    if not hits:
        return {"status": "upstream_changed"}
    if len(hits) > 1:
        return {"status": "ambiguous"}
    path = hits[0]
    if "/" not in path:
        return {"status": "repo_root"}
    prefix = path.rsplit("/", 1)[0] + "/"
    if any(e["path"].startswith(prefix) and e["path"] != path for e in blobs):
        return {"status": "bundled_files"}
    return {"status": "pinned", "path": path}


def resolve_pins(
    page_records: list[dict],
    archive_paths: dict[str, str],
    archive_dir: Path,
    existing: dict[str, dict],
    client: GitHubClient,
    *,
    budget: int,
    now: datetime,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Return pins keyed by page record id, refreshing at most `budget` API calls."""
    pins: dict[str, dict] = {}
    due: dict[tuple[str, str], list[tuple[str, str, bytes]]] = defaultdict(list)
    stats = {"api_calls": 0, "reused": 0, "deferred": 0, "unavailable": 0}
    cutoff = (now - timedelta(days=max_age_days)).isoformat()
    for record in page_records:
        record_id = record.get("id", "")
        archive_path = archive_paths.get(record_id)
        skill_file = archive_dir / archive_path if archive_path else None
        if not skill_file or not skill_file.is_file():
            stats["unavailable"] += 1
            continue
        data = skill_file.read_bytes()
        previous = current_pin(existing, record_id, archive_path, data)
        if previous and previous.get("checked_at", "") >= cutoff:
            pins[record_id] = previous
            stats["reused"] += 1
            continue
        due[(record["repo"], record.get("branch") or "main")].append((record_id, archive_path, data))

    rate_limited = False
    for (repo, branch), skills in sorted(due.items()):
        if rate_limited or stats["api_calls"] + 2 > budget:
            stats["deferred"] += len(skills)
            for record_id, archive_path, data in skills:
                if previous := current_pin(existing, record_id, archive_path, data):
                    pins[record_id] = previous
            continue
        try:
            stats["api_calls"] += 1
            commit = client.branch_sha(repo, branch)
            stats["api_calls"] += 1
            tree = client.get_json(f"/repos/{repo}/git/trees/{commit}?recursive=1")
        except GitHubApiError as exc:
            if exc.status in (403, 429) and "rate limit" in str(exc).lower():
                # Leave this and every later repository for the next run.
                rate_limited = True
                stats["deferred"] += len(skills)
                for record_id, archive_path, data in skills:
                    if previous := current_pin(existing, record_id, archive_path, data):
                        pins[record_id] = previous
                continue
            for record_id, archive_path, data in skills:
                pins[record_id] = {"status": "upstream_unavailable", "error": str(exc)[:200],
                                   "archive_path": archive_path,
                                      "digest": sha256_digest(data),
                                      "checked_at": now.isoformat()}
            continue
        entries = tree.get("tree") if tree.get("truncated") is False else None
        for record_id, archive_path, data in skills:
            digest = sha256_digest(data)
            result = match_upstream(entries, git_blob_sha(data)) if entries is not None else {"status": "tree_truncated"}
            identity = frontmatter_identity(data)
            if result["status"] == "pinned" and identity is None:
                result = {"status": "invalid_frontmatter"}
            pin = {**result, "archive_path": archive_path, "repo": repo, "branch": branch,
                   "digest": digest, "checked_at": now.isoformat()}
            if result["status"] == "pinned":
                pin.update({"commit": commit, "name": identity[0], "description": identity[1]})
            pins[record_id] = pin
    return pins, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True, help="docs/page-catalog.json")
    parser.add_argument("--docs-dir", type=Path, required=True, help="Directory holding skill-detail-shards/")
    parser.add_argument("--archive", type=Path, required=True, help="Checked-out data repository")
    parser.add_argument("--pins", type=Path, required=True, help="Pin cache to read and rewrite")
    parser.add_argument("--budget", type=int, default=1500, help="Maximum GitHub API calls")
    args = parser.parse_args()

    records = json.loads(args.catalog.read_text(encoding="utf-8")).get("skills")
    if not isinstance(records, list):
        raise ValueError(f"Catalog artifact has no valid skills list: {args.catalog}")
    existing = json.loads(args.pins.read_text(encoding="utf-8")).get("pins", {}) if args.pins.exists() else {}
    pins, stats = resolve_pins(
        records, load_archive_paths(args.docs_dir), args.archive, existing,
        GitHubClient(os.environ.get("GITHUB_TOKEN", "")),
        budget=args.budget, now=datetime.now(timezone.utc).replace(microsecond=0),
    )
    args.pins.write_text(json.dumps({"schema_version": 1, "pins": dict(sorted(pins.items()))},
                                    ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    pinned = sum(1 for pin in pins.values() if pin.get("status") == "pinned")
    print(f"Install pins: {pinned} pinned of {len(pins)} checked; {json.dumps(stats)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
