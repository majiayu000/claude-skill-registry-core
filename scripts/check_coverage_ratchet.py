#!/usr/bin/env python3
"""Fail closed on global, module, and critical-function coverage regressions."""

from __future__ import annotations

import argparse
import json
import string
import subprocess
from pathlib import Path
from typing import Any, Sequence


class CoverageRatchetError(ValueError):
    """Invalid baseline or coverage evidence."""


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CoverageRatchetError(f"unable to read JSON object {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CoverageRatchetError(f"expected JSON object: {path}")
    return payload


def _number(payload: dict[str, Any], key: str, context: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoverageRatchetError(f"{context}.{key} must be numeric")
    return float(value)


def validate_baseline(baseline: dict[str, Any]) -> None:
    if baseline.get("schema_version") != 1:
        raise CoverageRatchetError("coverage baseline schema_version must be 1")
    commit = baseline.get("recorded_commit")
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in string.hexdigits for character in commit)
    ):
        raise CoverageRatchetError("coverage baseline recorded_commit must be a full SHA")
    _number(baseline, "global_line_percent", "baseline")
    module_minimums = baseline.get("module_line_minimums")
    critical_functions = baseline.get("critical_functions")
    if not isinstance(module_minimums, dict) or not module_minimums:
        raise CoverageRatchetError("coverage baseline module_line_minimums must be an object")
    if not isinstance(critical_functions, dict) or not critical_functions:
        raise CoverageRatchetError("coverage baseline critical_functions must be an object")
    for path, minimum in module_minimums.items():
        if not isinstance(path, str) or isinstance(minimum, bool) or not isinstance(
            minimum, (int, float)
        ):
            raise CoverageRatchetError("invalid module coverage minimum")
        if float(minimum) < 80.0:
            raise CoverageRatchetError(f"module minimum cannot be below 80: {path}")
    for path, functions in critical_functions.items():
        if not isinstance(path, str) or not isinstance(functions, list) or not functions:
            raise CoverageRatchetError("invalid critical function mapping")
        if any(not isinstance(function, str) or not function for function in functions):
            raise CoverageRatchetError(f"invalid critical function name: {path}")


def validate_coverage(
    coverage: dict[str, Any],
    baseline: dict[str, Any],
    *,
    previous_baseline: dict[str, Any] | None = None,
) -> list[str]:
    validate_baseline(baseline)
    errors: list[str] = []
    totals = coverage.get("totals")
    files = coverage.get("files")
    if not isinstance(totals, dict) or not isinstance(files, dict):
        raise CoverageRatchetError("coverage JSON must contain totals and files objects")

    current_global = _number(totals, "percent_statements_covered", "coverage.totals")
    required_global = _number(baseline, "global_line_percent", "baseline")
    if current_global + 1e-9 < required_global:
        errors.append(
            f"global line coverage {current_global:.6f}% is below baseline "
            f"{required_global:.6f}%"
        )

    module_minimums = baseline["module_line_minimums"]
    for path, minimum in module_minimums.items():
        file_data = files.get(path)
        if not isinstance(file_data, dict) or not isinstance(file_data.get("summary"), dict):
            errors.append(f"coverage evidence is missing module: {path}")
            continue
        current = _number(file_data["summary"], "percent_statements_covered", path)
        if current + 1e-9 < float(minimum):
            errors.append(f"{path} line coverage {current:.6f}% is below {float(minimum):.6f}%")

    for path, function_names in baseline["critical_functions"].items():
        file_data = files.get(path)
        functions = file_data.get("functions") if isinstance(file_data, dict) else None
        if not isinstance(functions, dict):
            errors.append(f"coverage evidence is missing function data: {path}")
            continue
        for function_name in function_names:
            function = functions.get(function_name)
            if not isinstance(function, dict) or not isinstance(function.get("summary"), dict):
                errors.append(f"coverage evidence is missing critical function: {path}:{function_name}")
                continue
            summary = function["summary"]
            line_percent = _number(
                summary,
                "percent_statements_covered",
                f"{path}:{function_name}",
            )
            branch_percent = _number(
                summary,
                "percent_branches_covered",
                f"{path}:{function_name}",
            )
            if line_percent < 100.0 or branch_percent < 100.0:
                errors.append(
                    f"critical function {path}:{function_name} must have 100% line/branch "
                    f"coverage (line={line_percent:.6f}%, branch={branch_percent:.6f}%)"
                )

    if previous_baseline is not None:
        validate_baseline(previous_baseline)
        previous_global = _number(previous_baseline, "global_line_percent", "previous baseline")
        if required_global + 1e-9 < previous_global:
            errors.append(
                f"coverage baseline cannot decrease ({required_global:.6f}% < "
                f"{previous_global:.6f}%)"
            )
        for path, previous_minimum in previous_baseline["module_line_minimums"].items():
            current_minimum = module_minimums.get(path)
            if not isinstance(current_minimum, (int, float)) or float(current_minimum) < float(
                previous_minimum
            ):
                errors.append(f"module coverage minimum cannot decrease: {path}")
        for path, previous_functions in previous_baseline["critical_functions"].items():
            current_functions = baseline["critical_functions"].get(path)
            if not isinstance(current_functions, list) or not set(previous_functions).issubset(
                current_functions
            ):
                errors.append(f"critical function coverage set cannot shrink: {path}")
    return errors


def load_previous_baseline(compare_ref: str, baseline_path: Path) -> dict[str, Any] | None:
    result = subprocess.run(
        ["git", "show", f"{compare_ref}:{baseline_path.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CoverageRatchetError("previous coverage baseline is malformed") from exc
    if not isinstance(payload, dict):
        raise CoverageRatchetError("previous coverage baseline must be an object")
    return payload


def validate_recorded_commit(baseline: dict[str, Any], compare_ref: str) -> list[str]:
    commit = baseline["recorded_commit"]
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, compare_ref],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return []
    return [f"baseline recorded_commit {commit} is not an ancestor of {compare_ref}"]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, default=Path("coverage.json"))
    parser.add_argument("--baseline", type=Path, default=Path("coverage-baseline.json"))
    parser.add_argument("--compare-ref", default="origin/main")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        coverage = load_json_object(args.coverage)
        baseline = load_json_object(args.baseline)
        previous = load_previous_baseline(args.compare_ref, args.baseline)
        errors = validate_coverage(coverage, baseline, previous_baseline=previous)
        errors.extend(validate_recorded_commit(baseline, args.compare_ref))
    except CoverageRatchetError as exc:
        print(f"Coverage ratchet failed: {exc}")
        return 1
    if errors:
        print("Coverage ratchet failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Coverage ratchet passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
