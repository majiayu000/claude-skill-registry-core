#!/usr/bin/env python3
"""Keep an append-only content history for skill guides and summarize upstream drift.

Each run records, per guide, the SHA-256 of every archived file the registry
scanned and the install pin status for those bytes. A new entry is appended
only when the scanned content or the pin status changes, so the history shows
when an author's upstream copy stopped matching the version the registry checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from resolve_install_pins import sha256_digest

SCHEMA_VERSION = 1
# Written by the registry next to every archived skill, so it is not author content.
ARCHIVE_METADATA = "metadata.json"


def file_manifest(archive_dir: Path, archive_path: str) -> dict[str, str] | None:
    """Per-file SHA-256 of the archived skill directory, or None when it is not on disk."""
    root = archive_dir.resolve()
    skill_dir = (root / archive_path).resolve().parent
    if not skill_dir.is_relative_to(root) or skill_dir == root or not skill_dir.is_dir():
        return None
    files = {}
    for path in sorted(skill_dir.rglob("*")):
        rel = path.relative_to(skill_dir).as_posix()
        if path.is_symlink() or not path.is_file() or rel == ARCHIVE_METADATA:
            continue
        files[rel] = sha256_digest(path.read_bytes())
    return files


def tree_digest(files: dict[str, str]) -> str:
    listing = "".join(f"{digest}  {rel}\n" for rel, digest in sorted(files.items()))
    return "sha256:" + hashlib.sha256(listing.encode("utf-8")).hexdigest()


def observation(pin: dict[str, Any], files: dict[str, str] | None) -> dict[str, Any]:
    entry = {key: pin[key] for key in ("status", "digest", "repo", "path", "commit") if pin.get(key)}
    if pin.get("status") == "upstream_unavailable" and pin.get("error"):
        entry["error"] = str(pin["error"])[:80]
    if files is not None:
        entry["tree_digest"], entry["files"] = tree_digest(files), files
    return entry


def same_content(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    """Equal scanned bytes and pin outcome; a moved upstream branch head alone is not a change."""
    if any(previous.get(key) != current.get(key) for key in ("status", "digest", "repo", "path")):
        return False
    return "tree_digest" not in previous or "tree_digest" not in current or previous["tree_digest"] == current["tree_digest"]


def update_history(
    history: dict[str, list[dict[str, Any]]],
    pins: dict[str, dict[str, Any]],
    archive_dir: Path | None,
    observed_at: str,
) -> dict[str, int]:
    stats = {"appended": 0, "unchanged": 0}
    for record_id, pin in sorted(pins.items()):
        archive_path = pin.get("archive_path")
        files = file_manifest(archive_dir, archive_path) if archive_dir and archive_path else None
        entry = observation(pin, files)
        entries = history.setdefault(record_id, [])
        last = entries[-1] if entries else None
        if last and observed_at < last["last_seen"]:
            raise ValueError(f"Observation {observed_at} is older than recorded history for {record_id}")
        if last and same_content(last, entry):
            last["last_seen"] = observed_at
            if "tree_digest" in entry and "tree_digest" not in last:
                last["tree_digest"], last["files"] = entry["tree_digest"], entry["files"]
            stats["unchanged"] += 1
            continue
        entries.append({"first_seen": observed_at, "last_seen": observed_at, **entry})
        stats["appended"] += 1
    return stats


def summarize(history: dict[str, list[dict[str, Any]]], pins: dict[str, dict[str, Any]], generated_at: str) -> dict[str, Any]:
    """Counts over the guides in the current pin cache."""
    tracked = {record_id: history[record_id] for record_id in pins if history.get(record_id)}
    statuses = Counter(entries[-1]["status"] for entries in tracked.values())
    verified_then_changed = sum(
        1 for entries in tracked.values()
        if entries[-1]["status"] == "upstream_changed" and any(e["status"] == "pinned" for e in entries[:-1])
    )
    source_gone = sum(
        1 for entries in tracked.values()
        if entries[-1]["status"] == "upstream_unavailable" and "404" in entries[-1].get("error", "")
    )
    rescanned = sum(1 for entries in tracked.values() if len({e.get("digest") for e in entries}) > 1)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "history_since": min((entries[0]["first_seen"] for entries in tracked.values()), default=None),
        "tracked": len(tracked),
        "files_tracked": sum(len(entries[-1].get("files", {})) for entries in tracked.values()),
        "status_counts": dict(sorted(statuses.items())),
        "upstream_changed": statuses["upstream_changed"],
        "verified_then_changed": verified_then_changed,
        "source_gone": source_gone,
        "content_rescanned": rescanned,
    }


def render_summary(summary: dict[str, Any]) -> str:
    tracked = summary["tracked"] or 1
    rows = [
        ("Guides tracked", summary["tracked"]),
        ("Author's current SKILL.md differs from the scanned version", summary["upstream_changed"]),
        ("…of which the registry had verified an exact match earlier", summary["verified_then_changed"]),
        ("Source repository or branch returns 404", summary["source_gone"]),
        ("Archived content changed between scans", summary["content_rescanned"]),
    ]
    lines = [f"Skill content history since {summary['history_since']} ({summary['files_tracked']} files hashed)", "",
             "| Measure | Guides | Share |", "|---|---:|---:|"]
    lines += [f"| {label} | {count} | {count / tracked:.1%} |" for label, count in rows]
    lines += ["", "| Pin status | Guides |", "|---|---:|"]
    lines += [f"| {status} | {count} |" for status, count in summary["status_counts"].items()]
    return "\n".join(lines)


def load_history(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION or not isinstance(payload.get("skills"), dict):
        raise ValueError(f"{path} is not a schema_version {SCHEMA_VERSION} content history")
    return payload["skills"]


def load_pins(text: str, source: str) -> dict[str, dict[str, Any]]:
    pins = json.loads(text).get("pins")
    if not isinstance(pins, dict):
        raise ValueError(f"Install pin artifact has no valid pins map: {source}")
    return pins


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def git_snapshots(repo: Path, rel_path: str) -> list[tuple[str, str]]:
    """(commit time, file text) for every commit that touched rel_path, oldest first."""
    log = subprocess.run(["git", "-C", str(repo), "log", "--reverse", "--format=%H %cI", "--", rel_path],
                         check=True, capture_output=True, text=True).stdout.split()
    snapshots = []
    for commit, committed_at in zip(log[::2], log[1::2], strict=True):
        text = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{rel_path}"],
                              check=True, capture_output=True, text=True).stdout
        utc = datetime.fromisoformat(committed_at).astimezone(timezone.utc).replace(microsecond=0)
        snapshots.append((utc.isoformat(), text))
    return snapshots


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    update = sub.add_parser("update", help="Record the current pin cache and archive")
    update.add_argument("--pins", type=Path, required=True, help="install-pins.json")
    update.add_argument("--archive", type=Path, help="Checked-out data repository")
    update.add_argument("--observed-at", help="ISO timestamp; defaults to now (UTC)")
    backfill = sub.add_parser("backfill", help="Replay install-pins.json from git history")
    backfill.add_argument("--repo", type=Path, default=Path("."))
    backfill.add_argument("--pins-path", default="install-pins.json")
    report = sub.add_parser("summary", help="Print the drift table for the current pin cache")
    report.add_argument("--pins", type=Path, required=True)
    for command in (update, backfill, report):
        command.add_argument("--history", type=Path, required=True, help="skill-content-history.json")
        command.add_argument("--summary", type=Path, help="Write summary JSON here")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    history = load_history(args.history)
    if args.command == "backfill":
        snapshots = git_snapshots(args.repo, args.pins_path)
        if not snapshots:
            raise ValueError(f"No git history for {args.pins_path} in {args.repo}")
        for observed_at, text in snapshots:
            pins = load_pins(text, f"{args.pins_path}@{observed_at}")
            stats = update_history(history, pins, None, observed_at)
            print(f"{observed_at}: {json.dumps(stats)}")
    else:
        pins = load_pins(args.pins.read_text(encoding="utf-8"), str(args.pins))
        if args.command == "update":
            stats = update_history(history, pins, args.archive, args.observed_at or now)
            print(f"Content history: {json.dumps(stats)}")
    if args.command != "summary":
        write_json(args.history, {"schema_version": SCHEMA_VERSION, "skills": history})
    summary = summarize(history, pins, now)
    if args.summary:
        write_json(args.summary, summary)
    print(render_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
