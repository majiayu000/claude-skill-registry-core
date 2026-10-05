import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_alert_decision as alert  # noqa: E402

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
OK = {"preflight": "success", "sync": "success", "publish": "success"}


def progress(next_repo, total=4, started_days_ago=1.0, completed_at=None):
    return {
        "started_at": (NOW - timedelta(days=started_days_ago)).isoformat().replace("+00:00", "Z"),
        "repos": [f"owner/repo{i}" for i in range(total)],
        "next_repo": next_repo,
        "completed_at": completed_at,
    }


def decide(**overrides):
    kwargs = dict(event_name="schedule", is_full=True, results=OK, run_attempt=1,
                  progress=progress(2), weekly_issue_last_alert_at=None, now=NOW)
    kwargs.update(overrides)
    return alert.decide(**kwargs)


def test_advancing_cycle_is_not_reported():
    assert decide()["action"] == "none"


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_completion_after_last_alert_closes_weekly_issue_from_any_trigger(event):
    result = decide(
        event_name=event,
        is_full=False,
        progress=progress(4, completed_at="2026-10-05T04:36:22Z"),
        weekly_issue_last_alert_at=datetime(2026, 10, 4, 2, 40, tzinfo=timezone.utc),
    )
    assert result["action"] == "close"
    assert result["title"] == alert.WEEKLY_TITLE
    assert "2026-10-05T04:36:22Z" in result["message"]


def test_completion_before_last_alert_does_not_close_weekly_issue():
    result = decide(
        event_name="workflow_dispatch",
        is_full=False,
        progress=progress(4, completed_at="2026-10-01T00:00:00Z"),
        weekly_issue_last_alert_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    assert result["action"] == "none"


def test_completion_does_not_close_when_this_run_did_not_succeed():
    result = decide(
        results={"preflight": "success", "sync": "success", "publish": "cancelled"},
        progress=progress(4, completed_at="2026-10-05T04:36:22Z"),
        weekly_issue_last_alert_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
    )
    assert result["action"] == "none"


@pytest.mark.parametrize("is_full,title", [(True, alert.WEEKLY_TITLE), (False, alert.DAILY_TITLE)])
def test_scheduled_job_failure_still_reports(is_full, title):
    result = decide(is_full=is_full, results=dict(OK, sync="failure"))
    assert result == {"action": "report", "title": title,
                      "scope": "weekly full sync" if is_full else "daily sync"}


def test_manual_failure_is_not_reported():
    assert decide(event_name="workflow_dispatch", results=dict(OK, sync="failure"))["action"] == "none"


def test_overdue_cycle_is_flagged_on_schedule_only():
    stale = progress(2, started_days_ago=alert.DEFAULT_MAX_CYCLE_DAYS + 1)
    result = decide(progress=stale)
    assert result["action"] == "flag"
    assert result["title"] == alert.WEEKLY_TITLE
    assert "2/4" in result["message"]
    assert decide(event_name="workflow_dispatch", progress=stale)["action"] == "none"


def test_max_cycle_days_is_configurable():
    four_days = progress(2, started_days_ago=4)
    assert decide(progress=four_days)["action"] == "none"
    assert decide(progress=four_days, max_cycle_days=3)["action"] == "flag"


def test_successful_scheduled_daily_run_closes_daily_issue():
    result = decide(is_full=False, progress=progress(4, completed_at="2026-09-01T00:00:00Z"))
    assert result["action"] == "close"
    assert result["title"] == alert.DAILY_TITLE


def test_rerun_with_skipped_sync_counts_as_success():
    results = dict(OK, sync="skipped")
    assert decide(is_full=False, results=results, run_attempt=2)["action"] == "close"
    assert decide(is_full=False, results=results, run_attempt=1)["action"] == "none"


def test_full_run_without_progress_file_is_left_alone():
    assert decide(progress=None)["action"] == "none"


def test_cli_reads_progress_file(tmp_path):
    path = tmp_path / "progress.json"
    path.write_text(json.dumps(progress(4, completed_at="2026-10-05T04:36:22Z")), encoding="utf-8")
    output = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_alert_decision.py"),
         "--event-name", "workflow_dispatch", "--is-full", "true",
         "--preflight-result", "success", "--sync-result", "success", "--publish-result", "success",
         "--progress", str(path), "--weekly-issue-last-alert-at", "2026-10-04T02:40:00Z",
         "--max-cycle-days", "", "--now", NOW.isoformat()],
        capture_output=True, text=True, check=True,
    ).stdout
    assert json.loads(output)["action"] == "close"


def test_cli_rejects_non_positive_max_cycle_days():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "sync_alert_decision.py"),
         "--event-name", "schedule", "--is-full", "true", "--max-cycle-days", "0"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
