from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import discover_plugins as discovery  # noqa: E402


def _completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(["tool"], returncode, stdout=stdout, stderr="secret")


def _error(kind="api_failure", subject="demo"):
    return discovery.DiscoveryError(
        source="test",
        operation="operation",
        kind=kind,
        subject=subject,
        message="safe message",
    )


def _structure(**overrides):
    value = {
        "skills": ["skills/one/SKILL.md", "skills/two/SKILL.md"],
        "commands": ["plugin/commands/run.md"],
        "hooks": ["plugin/hooks/pre.sh"],
        "has_package_json": True,
        "description": "Demo plugin",
        "stars": 10,
        "default_branch": "main",
    }
    value.update(overrides)
    return value


def _report(status, *, allow_partial=False, candidates=None, errors=None):
    outcomes = []
    for index, error in enumerate(errors or []):
        outcomes.append(discovery.SourceOutcome(f"error-{index}", "error", error=error))
    if status in {"complete", "partial"}:
        outcomes.append(discovery.SourceOutcome("success", "success"))
    return discovery.DiscoveryReport(
        status=status,
        allow_partial=allow_partial,
        candidates=candidates or [],
        outcomes=outcomes,
        errors=errors or [],
    )


def test_run_command_success_uses_argument_list(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen.update(kwargs)
        return _completed("ok")

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)

    assert discovery._run_command(
        ["gh", "api", "repos/a/b"],
        source="github",
        operation="api",
        subject="repos/a/b",
        timeout=5,
    ) == "ok"
    assert seen["command"] == ["gh", "api", "repos/a/b"]
    assert seen["check"] is False


def test_run_command_nonzero_hides_stderr(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", lambda *a, **k: _completed("", 7))

    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery._run_command(
            ["npm", "search"],
            source="npm",
            operation="search",
            subject="query",
            timeout=1,
        )

    assert caught.value.kind == "nonzero_exit"
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("raised", "kind"),
    [
        (subprocess.TimeoutExpired(["npm"], 1), "timeout"),
        (OSError("token=secret"), "api_failure"),
    ],
)
def test_run_command_exceptions_are_typed_and_sanitized(monkeypatch, raised, kind):
    def fail(*args, **kwargs):
        raise raised

    monkeypatch.setattr(discovery.subprocess, "run", fail)
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery._run_command(
            ["npm"], source="npm", operation="search", subject="q", timeout=1
        )

    assert caught.value.kind == kind
    assert "token=secret" not in str(caught.value)


def test_json_loader_reports_location_without_payload():
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery._load_json("{secret", source="npm", operation="search", subject="query")

    assert caught.value.kind == "malformed_json"
    assert "secret" not in caught.value.message
    assert discovery._load_json("[]", source="npm", operation="search", subject="q") == []


@pytest.mark.parametrize(
    ("function", "payload", "expected"),
    [
        (discovery.npm_search, '[{"name":"demo"}]', [{"name": "demo"}]),
        (discovery.npm_view, '{"name":"demo"}', {"name": "demo"}),
    ],
)
def test_npm_adapters_accept_expected_shapes(monkeypatch, function, payload, expected):
    monkeypatch.setattr(discovery, "_run_command", lambda *args, **kwargs: payload)
    assert function("demo") == expected


@pytest.mark.parametrize(
    ("function", "payload"),
    [
        (discovery.npm_search, "{}"),
        (discovery.npm_search, "[null]"),
        (discovery.npm_view, "[]"),
    ],
)
def test_npm_adapters_reject_unknown_shapes(monkeypatch, function, payload):
    monkeypatch.setattr(discovery, "_run_command", lambda *args, **kwargs: payload)
    with pytest.raises(discovery.DiscoveryError) as caught:
        function("demo")
    assert caught.value.kind == "invalid_shape"


def test_extract_repo_slug_handles_supported_and_unknown_urls():
    assert (
        discovery.extract_repo_slug(
            {"repository": {"url": "git+https://github.com/owner/repo.git"}}
        )
        == "owner/repo"
    )
    assert discovery.extract_repo_slug({"repository": "http://github.com/a/b/extra"}) == "a/b"
    assert discovery.extract_repo_slug({"repository": "https://example.com/a/b"}) == ""


def test_gh_api_builds_jq_command(monkeypatch):
    seen = []

    def fake(command, **kwargs):
        seen.append(command)
        return " value \n"

    monkeypatch.setattr(discovery, "_run_command", fake)
    assert discovery.gh_api("repos/a/b", ".name") == "value"
    assert seen == [["gh", "api", "repos/a/b", "--jq", ".name"]]


def test_inspect_repo_structure_parses_signals(monkeypatch):
    replies = iter(
        [
            json.dumps(
                {"description": "demo", "stargazers_count": 12, "default_branch": "trunk"}
            ),
            "skills/a/SKILL.md\nplugin/commands/run.md\nplugin/hooks/pre.js\npackage.json\n",
        ]
    )
    monkeypatch.setattr(discovery, "gh_api", lambda *args, **kwargs: next(replies))

    result = discovery.inspect_repo_structure("owner/repo")

    assert result["skills"] == ["skills/a/SKILL.md"]
    assert result["commands"] == ["plugin/commands/run.md"]
    assert result["hooks"] == ["plugin/hooks/pre.js"]
    assert result["has_package_json"] is True
    assert result["default_branch"] == "trunk"


@pytest.mark.parametrize(
    ("metadata", "kind"),
    [
        ("", "api_failure"),
        ("[]", "invalid_shape"),
        ('{"description":"x"}', "invalid_shape"),
    ],
)
def test_inspect_repo_structure_rejects_bad_metadata(monkeypatch, metadata, kind):
    monkeypatch.setattr(discovery, "gh_api", lambda *args, **kwargs: metadata)
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery.inspect_repo_structure("owner/repo")
    assert caught.value.kind == kind


def test_get_install_command_parses_package(monkeypatch):
    encoded = base64.b64encode(b'{"name":"demo-cli","bin":{"demo":"cli.js"}}').decode()
    monkeypatch.setattr(discovery, "gh_api", lambda *args, **kwargs: encoded)
    assert discovery.get_install_command("owner/repo", "main") == "npx demo-cli@latest"


@pytest.mark.parametrize("content", ["", "not-base64"])
def test_get_install_command_rejects_bad_content(monkeypatch, content):
    monkeypatch.setattr(discovery, "gh_api", lambda *args, **kwargs: content)
    with pytest.raises(discovery.DiscoveryError):
        discovery.get_install_command("owner/repo", "main")


def test_score_candidate_preserves_policy(monkeypatch):
    monkeypatch.setattr(discovery, "get_install_command", lambda *a: "npx demo@latest")
    assert discovery.score_candidate("a/b", _structure(skills=[]))["score"] == 0
    assert discovery.score_candidate("a/b", _structure(), npm_name="demo")["score"] >= 4
    assert discovery.score_candidate("a/b", _structure(stars=1000))["install"] == "npx demo@latest"
    assert discovery.score_candidate("a/b", _structure(skills=[f"s/{n}/SKILL.md" for n in range(6)]))[
        "score"
    ] >= 2
    assert discovery.score_candidate("a/b", _structure(skills=[f"s/{n}/SKILL.md" for n in range(10)]))[
        "score"
    ] >= 3


def test_load_existing_plugins_missing_valid_and_invalid(tmp_path):
    missing, outcome = discovery.load_existing_plugins(tmp_path / "missing.json")
    assert missing == set()
    assert outcome.status == "optional_missing"

    path = tmp_path / "plugins.json"
    path.write_text(
        '{"plugins":[{"name":"demo","repo":"owner/repo"}]}',
        encoding="utf-8",
    )
    repos, outcome = discovery.load_existing_plugins(path)
    assert repos == {"owner/repo"}
    assert outcome.status == "success"

    path.write_text('{"plugins":[null]}', encoding="utf-8")
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery.load_existing_plugins(path)
    assert caught.value.kind == "invalid_shape"


@pytest.mark.parametrize(
    "plugin",
    [
        {"repo": "owner/repo"},
        {"name": "", "repo": "owner/repo"},
        {"name": "demo", "repo": ""},
    ],
    ids=["missing-name", "empty-name", "empty-repo"],
)
def test_existing_plugin_invalid_item_is_authoritative_and_preserves_output(
    tmp_path, plugin
):
    path = tmp_path / "plugins.json"
    path.write_text(json.dumps({"plugins": [plugin]}), encoding="utf-8")

    report = discovery.run_discovery(
        plugins_path=path,
        registry_path=tmp_path / "registry.json",
        npm_only=True,
        allow_partial=True,
    )

    assert report.status == "failed"
    assert len(report.errors) == 1
    error = report.errors[0]
    assert error.source == "plugin_source"
    assert error.operation == "read_existing"
    assert error.kind == "invalid_shape"
    assert error.subject == str(path)

    output = tmp_path / "report.json"
    output.write_bytes(b"trusted\n")
    assert discovery.main(
        ["--plugins", str(path), "--output", str(output), "--npm-only", "--allow-partial"]
    ) == 1
    assert output.read_bytes() == b"trusted\n"


def test_load_existing_plugins_non_utf8_is_malformed(tmp_path):
    path = tmp_path / "plugins.json"
    path.write_bytes(b"\xff")
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery.load_existing_plugins(path)
    assert caught.value.kind == "malformed_json"


def test_discover_from_npm_retains_partial_errors(monkeypatch):
    monkeypatch.setattr(discovery, "NPM_QUERIES", ["good", "bad"])

    def fake_search(query):
        if query == "bad":
            raise _error("timeout", query)
        return [{"name": "demo"}]

    monkeypatch.setattr(discovery, "npm_search", fake_search)
    monkeypatch.setattr(
        discovery,
        "npm_view",
        lambda name: {
            "name": name,
            "bin": {"demo": "cli.js"},
            "repository": {"url": "https://github.com/owner/repo"},
        },
    )
    monkeypatch.setattr(discovery, "inspect_repo_structure", lambda repo: _structure())
    outcomes = []

    candidates = discovery.discover_from_npm(set(), outcomes)

    assert len(candidates) == 1
    assert {outcome.status for outcome in outcomes} == {"success", "error"}
    assert discovery.derive_status(outcomes) == "partial"


def test_discover_from_npm_filters_known_and_records_view_error(monkeypatch):
    monkeypatch.setattr(discovery, "NPM_QUERIES", ["query"])
    monkeypatch.setattr(
        discovery,
        "npm_search",
        lambda query: [{"name": "known"}, {"name": "broken"}, {"other": "ignored"}],
    )

    def fake_view(name):
        if name == "broken":
            raise _error("nonzero_exit", name)
        return {
            "bin": {"known": "cli.js"},
            "repository": {"url": "https://github.com/owner/known"},
        }

    monkeypatch.setattr(discovery, "npm_view", fake_view)
    outcomes = []
    assert discovery.discover_from_npm({"owner/known"}, outcomes) == []
    assert any(outcome.status == "error" for outcome in outcomes)


def test_registry_loader_missing_valid_and_malformed(tmp_path):
    repos, outcome = discovery._load_registry_repos(tmp_path / "missing.json")
    assert repos == []
    assert outcome.status == "optional_missing"

    path = tmp_path / "registry.json"
    path.write_text(
        json.dumps({"skills": [{"repo": "owner/repo"}] * 10 + [{"repo": "small/repo"}]}),
        encoding="utf-8",
    )
    repos, outcome = discovery._load_registry_repos(path)
    assert repos == ["owner/repo"]
    assert outcome.status == "success"

    path.write_text("{}", encoding="utf-8")
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery._load_registry_repos(path)
    assert caught.value.kind == "invalid_shape"

    path.write_bytes(b"\xff")
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery._load_registry_repos(path)
    assert caught.value.kind == "malformed_json"


def test_discover_from_registry_records_repo_error(monkeypatch, tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"skills": [{"repo": "owner/repo"}] * 10}), encoding="utf-8")
    monkeypatch.setattr(
        discovery,
        "inspect_repo_structure",
        lambda repo: (_ for _ in ()).throw(_error("api_failure", repo)),
    )
    outcomes = []
    assert discovery.discover_from_registry(path, set(), set(), outcomes) == []
    assert outcomes[-1].status == "error"


@pytest.mark.parametrize(
    ("outcomes", "authoritative", "expected"),
    [
        ([], False, "complete"),
        ([discovery.SourceOutcome("a", "optional_missing")], False, "complete"),
        ([discovery.SourceOutcome("a", "success")], False, "complete"),
        (
            [
                discovery.SourceOutcome("a", "success"),
                discovery.SourceOutcome("b", "error", error=_error()),
            ],
            False,
            "partial",
        ),
        ([discovery.SourceOutcome("b", "error", error=_error())], False, "failed"),
        ([discovery.SourceOutcome("a", "success")], True, "failed"),
    ],
)
def test_derive_status(outcomes, authoritative, expected):
    assert discovery.derive_status(outcomes, authoritative_error=authoritative) == expected


def test_existing_plugin_context_does_not_turn_all_remote_errors_partial():
    outcomes = [
        discovery.SourceOutcome("existing_plugins", "success"),
        discovery.SourceOutcome("npm_search:q", "error", error=_error()),
    ]
    assert discovery.derive_status(outcomes) == "failed"


def test_run_discovery_authoritative_input_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        discovery,
        "load_existing_plugins",
        lambda path: (_ for _ in ()).throw(_error("malformed_json")),
    )
    report = discovery.run_discovery(
        plugins_path=tmp_path / "plugins.json",
        registry_path=tmp_path / "registry.json",
        npm_only=False,
        allow_partial=True,
    )
    assert report.status == "failed"
    assert len(report.errors) == 1


def test_run_discovery_zero_candidates_can_be_complete(monkeypatch, tmp_path):
    monkeypatch.setattr(
        discovery,
        "load_existing_plugins",
        lambda path: (set(), discovery.SourceOutcome("existing", "success")),
    )
    monkeypatch.setattr(discovery, "discover_from_npm", lambda existing, outcomes: [])
    report = discovery.run_discovery(
        plugins_path=tmp_path / "plugins.json",
        registry_path=tmp_path / "registry.json",
        npm_only=True,
        allow_partial=False,
    )
    assert report.status == "complete"
    assert report.candidates == []


def test_write_discovery_report_is_atomic(tmp_path):
    path = tmp_path / "report.json"
    report = _report("complete")
    discovery.write_discovery_report(path, report)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["status"] == "complete"
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_write_discovery_report_serialization_failure_preserves_existing(tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"trusted\n")
    report = _report("complete", candidates=[{"score": 1, "stars": 0, "bad": {"set"}}])

    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery.write_discovery_report(path, report)

    assert caught.value.kind == "write_error"
    assert path.read_bytes() == b"trusted\n"
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_write_discovery_report_failure_preserves_existing(monkeypatch, tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"trusted\n")
    original_replace = Path.replace

    def fail_replace(self, target):
        if Path(target) == path:
            raise OSError("failure")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery.write_discovery_report(path, _report("complete"))
    assert caught.value.kind == "write_error"
    assert path.read_bytes() == b"trusted\n"
    assert list(tmp_path.glob(".report.json.*.tmp")) == []


def test_write_discovery_report_temp_creation_failure_is_typed(monkeypatch, tmp_path):
    path = tmp_path / "report.json"
    path.write_bytes(b"trusted\n")

    def fail_temp(*args, **kwargs):
        raise OSError("failure")

    monkeypatch.setattr(discovery.tempfile, "NamedTemporaryFile", fail_temp)
    with pytest.raises(discovery.DiscoveryError) as caught:
        discovery.write_discovery_report(path, _report("complete"))
    assert caught.value.kind == "write_error"
    assert path.read_bytes() == b"trusted\n"


@pytest.mark.parametrize(
    ("status", "allow_partial", "expected_code", "should_change"),
    [
        ("complete", False, 0, True),
        ("partial", False, 2, False),
        ("partial", True, 0, True),
        ("failed", True, 1, False),
    ],
)
def test_main_exit_and_output_contract(
    monkeypatch, tmp_path, status, allow_partial, expected_code, should_change
):
    output = tmp_path / "report.json"
    output.write_bytes(b"trusted\n")
    errors = [_error()] if status != "complete" else []
    monkeypatch.setattr(
        discovery,
        "run_discovery",
        lambda **kwargs: _report(status, allow_partial=allow_partial, errors=errors),
    )
    argv = ["--output", str(output), "--npm-only"]
    if allow_partial:
        argv.append("--allow-partial")

    assert discovery.main(argv) == expected_code
    if should_change:
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert payload["status"] == status
        assert payload["allow_partial"] is allow_partial
    else:
        assert output.read_bytes() == b"trusted\n"
