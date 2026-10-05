#!/usr/bin/env python3
"""Decide what the sync-data alert job does with its tracking issues.

The weekly full discovery cycle spans several 100-minute batches, so a single run
rarely finishes it. This script keeps that normal multi-run progress from being
reported as a failure while still alerting on real failures and on cycles that
stop finishing in time.

It prints one JSON object; the workflow performs the GitHub issue calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

WEEKLY_TITLE = "[sync-data] Weekly full sync failed"
DAILY_TITLE = "[sync-data] Daily sync failed"
# A cycle restarts every Sunday and each daily run resumes it with one batch, so a
# healthy cycle finishes within the week. Running past that means it cannot keep up.
DEFAULT_MAX_CYCLE_DAYS = 7


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def load_progress(path: Optional[str]) -> Optional[dict]:
    if not path or not Path(path).is_file():
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def decide(
    *,
    event_name: str,
    is_full: bool,
    results: dict,
    run_attempt: int = 1,
    progress: Optional[dict],
    weekly_issue_last_alert_at: Optional[datetime],
    now: datetime,
    max_cycle_days: int = DEFAULT_MAX_CYCLE_DAYS,
) -> dict:
    """Return the issue action for this run.

    action is one of:
      report      - open the issue, or comment on it if already open (schedule only)
      flag        - open the issue only if it is not already open (schedule only)
      close       - close the open issue for `title`
      none        - leave issues alone
    """
    title = WEEKLY_TITLE if is_full else DAILY_TITLE
    scope = "weekly full sync" if is_full else "daily sync"
    scheduled = event_name == "schedule"
    failed = any(result == "failure" for result in results.values())
    # A rerun replays publish from the original handoff, so its sync job is skipped.
    succeeded = (
        results.get("preflight") == "success"
        and results.get("publish") == "success"
        and (results.get("sync") == "success" or (run_attempt > 1 and results.get("sync") == "skipped"))
    )

    if failed:
        if not scheduled:
            return {"action": "none", "reason": "Manual run failures are watched by whoever started them."}
        return {"action": "report", "title": title, "scope": scope}

    completed_at = parse_time(progress.get("completed_at")) if progress else None
    cycle_complete = bool(progress) and progress["next_repo"] == len(progress["repos"])

    # A cycle that finished after the weekly alert was last raised resolves it, whichever
    # trigger (schedule, manual full_scan, or a daily resume) carried the final batch.
    if (
        succeeded
        and cycle_complete
        and completed_at is not None
        and weekly_issue_last_alert_at is not None
        and completed_at > weekly_issue_last_alert_at
    ):
        return {
            "action": "close",
            "title": WEEKLY_TITLE,
            "scope": "weekly full sync",
            "message": (
                f"The full discovery cycle completed at {progress['completed_at']} "
                f"({progress['next_repo']}/{len(progress['repos'])} repositories); closing this alert."
            ),
        }

    if is_full and progress is None:
        return {"action": "none", "reason": "No full discovery progress file to judge the cycle by."}

    if is_full and not cycle_complete:
        started_at = parse_time(progress.get("started_at"))
        status = f"{progress['next_repo']}/{len(progress['repos'])} repositories"
        if started_at is not None and now - started_at > timedelta(days=max_cycle_days):
            if not scheduled:
                return {"action": "none", "reason": f"Cycle overdue at {status}; schedules raise the alert."}
            return {
                "action": "flag",
                "title": WEEKLY_TITLE,
                "scope": "weekly full sync",
                "message": (
                    f"The full discovery cycle started {progress['started_at']} is still unfinished at "
                    f"{status} after more than {max_cycle_days} days."
                ),
            }
        # Each batch either advances the cursor or fails the sync job (discover_by_topic.py
        # raises when a resumed batch makes no progress), so an unfinished cycle here is moving.
        return {"action": "none", "reason": f"Full discovery cycle advancing: {status}."}

    if not scheduled:
        return {"action": "none", "reason": "Manual runs only resolve a completed weekly cycle."}
    if not succeeded:
        return {"action": "none", "reason": "Run neither failed nor fully succeeded; leaving alerts unchanged."}
    return {
        "action": "close",
        "title": title,
        "scope": scope,
        "message": f"The latest {scope} completed successfully; closing this alert.",
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--is-full", required=True, choices=["true", "false"])
    parser.add_argument("--preflight-result", default="")
    parser.add_argument("--sync-result", default="")
    parser.add_argument("--publish-result", default="")
    parser.add_argument("--run-attempt", type=int, default=1)
    parser.add_argument("--progress", help="Path to full-discovery-progress.json")
    parser.add_argument("--weekly-issue-last-alert-at", default="",
                        help="Newest alert timestamp on the open weekly issue, if any")
    parser.add_argument("--max-cycle-days", default="",
                        help=f"Days before an unfinished cycle alerts (default {DEFAULT_MAX_CYCLE_DAYS})")
    parser.add_argument("--now", default="", help="Override the current time (tests)")
    args = parser.parse_args(argv)

    max_cycle_days = int(args.max_cycle_days) if args.max_cycle_days else DEFAULT_MAX_CYCLE_DAYS
    if max_cycle_days < 1:
        parser.error("--max-cycle-days must be at least 1")
    decision = decide(
        event_name=args.event_name,
        is_full=args.is_full == "true",
        results={
            "preflight": args.preflight_result,
            "sync": args.sync_result,
            "publish": args.publish_result,
        },
        run_attempt=args.run_attempt,
        progress=load_progress(args.progress),
        weekly_issue_last_alert_at=parse_time(args.weekly_issue_last_alert_at),
        now=parse_time(args.now) or datetime.now(timezone.utc),
        max_cycle_days=max_cycle_days,
    )
    json.dump(decision, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
