from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_coverage_ratchet as ratchet  # noqa: E402

MODULES = ["scripts/discover_plugins.py", "scripts/plugin_index.py"]
CRITICAL = {
    "scripts/discover_plugins.py": ["_run_command", "_load_json"],
    "scripts/plugin_index.py": ["_validate_plugins"],
}


def _summary(line=100.0, branch=100.0):
    return {
        "percent_statements_covered": line,
        "percent_branches_covered": branch,
    }


def _baseline(global_line=66.0):
    return {
        "schema_version": 1,
        "recorded_commit": "a" * 40,
        "global_line_percent": global_line,
        "module_line_minimums": dict.fromkeys(MODULES, 80.0),
        "critical_functions": deepcopy(CRITICAL),
    }


def _coverage(global_line=90.0):
    files = {}
    for path in MODULES:
        files[path] = {
            "summary": _summary(90.0, 90.0),
            "functions": {name: {"summary": _summary()} for name in CRITICAL[path]},
        }
    return {
        "totals": {"percent_statements_covered": global_line},
        "files": files,
    }


def test_validate_coverage_accepts_all_gates():
    assert ratchet.validate_coverage(_coverage(), _baseline()) == []


def test_validate_coverage_reports_global_regression():
    errors = ratchet.validate_coverage(_coverage(global_line=65.9), _baseline(global_line=66.0))
    assert any("global line coverage" in error for error in errors)


def test_validate_coverage_reports_module_below_target():
    coverage = _coverage()
    coverage["files"][MODULES[0]]["summary"]["percent_statements_covered"] = 79.9
    errors = ratchet.validate_coverage(coverage, _baseline())
    assert any(MODULES[0] in error and "below" in error for error in errors)


@pytest.mark.parametrize(("key", "value"), [("percent_statements_covered", 99.0), ("percent_branches_covered", 99.0)])
def test_validate_coverage_reports_critical_function_gap(key, value):
    coverage = _coverage()
    function = coverage["files"][MODULES[0]]["functions"][CRITICAL[MODULES[0]][0]]
    function["summary"][key] = value
    errors = ratchet.validate_coverage(coverage, _baseline())
    assert any("critical function" in error for error in errors)


def test_validate_coverage_reports_missing_module_and_function():
    coverage = _coverage()
    del coverage["files"][MODULES[0]]
    del coverage["files"][MODULES[1]]["functions"][CRITICAL[MODULES[1]][0]]
    errors = ratchet.validate_coverage(coverage, _baseline())
    assert any("missing module" in error for error in errors)
    assert any("missing critical function" in error for error in errors)


def test_validate_coverage_rejects_baseline_lowering():
    current = _baseline(global_line=65.0)
    current["module_line_minimums"][MODULES[0]] = 80.0
    current["critical_functions"][MODULES[0]] = current["critical_functions"][MODULES[0]][:1]
    previous = _baseline(global_line=66.0)
    previous["module_line_minimums"][MODULES[0]] = 85.0
    errors = ratchet.validate_coverage(
        _coverage(), current, previous_baseline=previous
    )
    assert any("baseline cannot decrease" in error for error in errors)
    assert any("module coverage minimum cannot decrease" in error for error in errors)
    assert any("critical function coverage set cannot shrink" in error for error in errors)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda baseline: baseline.update(schema_version=2),
        lambda baseline: baseline.update(recorded_commit="short"),
        lambda baseline: baseline.update(recorded_commit="z" * 40),
        lambda baseline: baseline.update(global_line_percent="high"),
        lambda baseline: baseline.update(module_line_minimums={}),
        lambda baseline: baseline.update(critical_functions={}),
        lambda baseline: baseline["module_line_minimums"].update({MODULES[0]: 79.0}),
    ],
)
def test_validate_baseline_rejects_invalid_contract(mutation):
    baseline = _baseline()
    mutation(baseline)
    with pytest.raises(ratchet.CoverageRatchetError):
        ratchet.validate_baseline(baseline)


def test_load_json_object_rejects_malformed_and_nonobject(tmp_path):
    path = tmp_path / "data.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ratchet.CoverageRatchetError):
        ratchet.load_json_object(path)
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ratchet.CoverageRatchetError):
        ratchet.load_json_object(path)


def test_load_previous_baseline_missing_and_valid(monkeypatch):
    monkeypatch.setattr(
        ratchet.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "missing"),
    )
    assert ratchet.load_previous_baseline("origin/main", Path("coverage-baseline.json")) is None

    payload = _baseline()
    monkeypatch.setattr(
        ratchet.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(payload), ""),
    )
    assert ratchet.load_previous_baseline("origin/main", Path("coverage-baseline.json")) == payload


def test_validate_recorded_commit_pass_and_fail(monkeypatch):
    monkeypatch.setattr(
        ratchet.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
    )
    assert ratchet.validate_recorded_commit(_baseline(), "origin/main") == []
    monkeypatch.setattr(
        ratchet.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", ""),
    )
    assert "not an ancestor" in ratchet.validate_recorded_commit(
        _baseline(), "origin/main"
    )[0]
