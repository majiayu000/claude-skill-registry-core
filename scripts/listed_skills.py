"""Load upstream-only catalog entries without reading or downloading skill bodies."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import quote

from portable_paths import is_safe_portable_relative_path
from security_blocklist import blocked_metadata_source, load_security_blocklist
from security_rules import INJECTION_PATTERNS
from utils import is_registry_repo

logger = logging.getLogger(__name__)


def source_identity(skill: dict) -> tuple[str, str, str]:
    """Match repo-root and directory/SKILL.md spellings, preserving branch identity."""
    path = str(skill.get("path") or "").strip().replace("\\", "/").strip("/")
    while path.startswith("./"):
        path = path[2:]
    if path in {"", "."} or path.lower() == "skill.md":
        path = ""
    elif path.lower().endswith("/skill.md"):
        path = path[:-9]
    return str(skill.get("repo") or "").strip().lower(), path, skill.get("branch") or "main"


def load_listed_skills(sources_dir: Path) -> list[dict]:
    """Validate metadata at the source boundary; never manufacture body evidence."""
    # The CLI validator calls this loader too; reuse its row checks without a
    # module-level import cycle.
    from validate_sources import Report, validate_skill_entry

    path = sources_dir / "listed.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
        raise ValueError(f"{path}: expected an object with a skills array")
    blocklist = load_security_blocklist()
    records = []
    seen = set()
    for index, entry in enumerate(payload["skills"]):
        report = Report()
        validate_skill_entry(entry, file=path.name, location=f"skills[{index}]", report=report)
        if report.issues:
            raise ValueError("; ".join(issue.format_line() for issue in report.issues))
        branch = entry.get("branch", "main")
        if not isinstance(branch, str) or not branch.strip() or branch != branch.strip():
            raise ValueError(f"{path}: skills[{index}].branch must be a nonempty string")
        raw_path = entry.get("path", "")
        if raw_path not in {"", "."} and not is_safe_portable_relative_path(raw_path):
            raise ValueError(f"{path}: skills[{index}].path must be a portable relative path")
        blocked = blocked_metadata_source(entry, blocklist)
        if blocked or is_registry_repo(entry["repo"]):
            raise ValueError(f"{path}: skills[{index}] references a blocked catalog source")
        tags = entry.get("tags") or []
        stars = entry.get("stars") if entry.get("stars") is not None else 0
        if isinstance(stars, bool) or stars < 0:
            raise ValueError(f"{path}: skills[{index}].stars must be a nonnegative integer")
        text = "\n".join([entry["name"], entry["description"], *tags])
        if any(pattern.search(text) for pattern in INJECTION_PATTERNS):
            raise ValueError(f"{path}: skills[{index}] contains instruction-override metadata")
        repo, directory, branch = source_identity(entry)
        key = (repo, directory, branch)
        if key in seen:
            raise ValueError(f"{path}: duplicate listed source {repo}/{directory}@{branch}")
        seen.add(key)
        source_path = f"{directory}/SKILL.md" if directory else "SKILL.md"
        records.append(
            {
                "name": entry["name"],
                "description": entry["description"],
                "repo": repo,
                "path": directory,
                "branch": branch,
                "category": entry["category"],
                "tags": tags,
                "stars": stars,
                "license": entry.get("license", ""),
                "author": entry.get("author", ""),
                "source": "listed",
                "tier": "listed",
                "install": f"{repo}/{directory}" if directory else repo,
                "source_url": f"https://github.com/{repo}/blob/{quote(branch, safe='')}/{quote(source_path, safe='/')}",
                "security_status": "unknown",
                "install_status": "unknown",
            }
        )
    return records


def merge_listed_skills(archived: list[dict], listed: list[dict]) -> list[dict]:
    """Keep archives authoritative for an identical source, without losing copies."""
    result = list(archived)
    seen = {source_identity(skill) for skill in archived}
    for skill in listed:
        key = source_identity(skill)
        if key in seen:
            logger.info("Omitting listed source already present in catalog: %s", key)
        else:
            result.append(skill)
            seen.add(key)
    return result
