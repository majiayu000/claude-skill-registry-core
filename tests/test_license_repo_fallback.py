"""Regression tests for falling back to the repository license.

Records came from a 2026-10-22 spot check of THIRD_PARTY_NOTICES: these repos were
archived as NOASSERTION/restricted although GitHub detects a root license for them.
"""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import utils  # noqa: E402
from test_sync_and_download import FakeResponse, install_fake_aiohttp  # noqa: E402
from test_sync_and_download import load_module as load_sync_module  # noqa: E402


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# repo, skill path, license GitHub detects at the repo root, expected class
MISJUDGED_REPOS = [
    ("AlterLab-IEU/AlterLab-Academic-Skills", "skills/domain-specific/alterlab-adaptyv", "MIT"),
    ("Jinwe1/development-skills", "skills/demo", "MIT"),
    ("AI-Business-Tools/claude-code", ".claude/skills/demo", "MIT"),
    ("ArabelaTso/Skills-4-SE", "skills/demo", "Apache-2.0"),
    ("covoiturage-gouv-fr/mono", ".claude/skills/demo", "Apache-2.0"),
    ("Ashutosh0428/pi-agent", ".pi/skills/demo", "MIT"),
    ("example/bsd-repo", "skills/demo", "BSD-3-Clause"),
]

# Correctly restricted before the fix; they must stay restricted.
CORRECTLY_RESTRICTED = [
    # No license file: GitHub answers 404, which the backfill caches as NOASSERTION.
    ("ComposioHQ/awesome-claude-skills", "NOASSERTION", "NOASSERTION"),
    # A LICENSE file GitHub cannot match to an SPDX id.
    ("NulightJens/humanizer-stack", "NOASSERTION", "NOASSERTION"),
    # Copyleft keeps its real name instead of NOASSERTION.
    ("REW1L/munch-helper", "GPL-3.0", "GPL-3.0"),
    ("example/lgpl-repo", "LGPL-3.0", "LGPL-3.0"),
]


def archived_restricted_metadata(repo: str, path: str) -> dict:
    """Metadata exactly as the registry archived it before the fix."""
    legal = utils.build_legal_metadata(repo=repo, path=path, license_name="")
    return {
        "name": path.rsplit("/", 1)[-1],
        "repo": repo,
        "github_path": path,
        "github_branch": "main",
        "category": "development",
        **legal,
    }


def test_archived_fixture_matches_published_notice_row():
    metadata = archived_restricted_metadata("covoiturage-gouv-fr/mono", ".claude/skills/demo")

    assert metadata["license"] == "NOASSERTION"
    assert metadata["license_class"] == "restricted"
    assert metadata["distribution"] == "restricted"


@pytest.mark.parametrize(
    "value", ["", None, "NOASSERTION", "noassertion", "unknown", "  ", {"name": "MIT"}]
)
def test_unresolved_license_values(value):
    assert utils.is_unresolved_license(value)


@pytest.mark.parametrize("value", ["MIT", "GPL-3.0", "Proprietary", "See LICENSE.txt"])
def test_declared_license_values_are_resolved(value):
    assert not utils.is_unresolved_license(value)


def test_resolve_license_prefers_skill_declaration_over_repo():
    assert utils.resolve_license("NOASSERTION", "MIT") == "MIT"
    assert utils.resolve_license("", "apache 2.0") == "Apache-2.0"
    assert utils.resolve_license("Proprietary", "MIT") == "Proprietary"
    # A declared but unrecognized skill license is not overridden by a permissive repo.
    assert utils.resolve_license("Complete terms in LICENSE.txt", "MIT") == (
        "Complete terms in LICENSE.txt"
    )
    assert utils.resolve_license(None, "NOASSERTION") == "NOASSERTION"


@pytest.mark.parametrize("repo,path,repo_license", MISJUDGED_REPOS)
def test_backfill_upgrades_misjudged_repos_to_compatible(repo, path, repo_license):
    module = load_script("backfill_legal_metadata")
    metadata = archived_restricted_metadata(repo, path)

    assert module.needs_backfill(metadata)
    updated = module.backfill_metadata(
        metadata,
        {"license": repo_license, "copyright": "Copyright (c) 2026 Upstream Owner"},
    )

    assert updated["license"] == repo_license
    assert updated["license_class"] == "compatible"
    assert updated["distribution"] == "compatible"
    assert updated["permission_note"].startswith("Use according to upstream license terms")
    assert updated["copyright"] == "Copyright (c) 2026 Upstream Owner"


@pytest.mark.parametrize("repo,repo_license,expected", CORRECTLY_RESTRICTED)
def test_backfill_keeps_correctly_restricted_repos(repo, repo_license, expected):
    module = load_script("backfill_legal_metadata")
    metadata = archived_restricted_metadata(repo, "skills/demo")

    updated = module.backfill_metadata(metadata, {"license": repo_license, "copyright": ""})

    assert updated["license"] == expected
    assert updated["license_class"] == "restricted"
    assert updated["distribution"] == "restricted"


def test_backfill_keeps_skill_level_declaration_and_hand_written_note():
    module = load_script("backfill_legal_metadata")
    declared = {
        **archived_restricted_metadata("acme/repo", "skills/demo"),
        "license": "Proprietary",
    }
    assert module.backfill_metadata(declared, {"license": "MIT"})["license"] == "Proprietary"

    custom_note = {
        **archived_restricted_metadata("acme/repo", "skills/demo"),
        "permission_note": "Author granted reuse by email on 2026-09-01.",
    }
    updated = module.backfill_metadata(custom_note, {"license": "MIT"})
    assert updated["license_class"] == "compatible"
    assert updated["permission_note"] == "Author granted reuse by email on 2026-09-01."


def test_fetch_repo_license_reports_copyleft_spdx(monkeypatch):
    module = load_script("backfill_legal_metadata")
    monkeypatch.setattr(
        module,
        "github_request",
        lambda *args, **kwargs: {
            "license": {"spdx_id": "GPL-3.0"},
            "html_url": "https://github.com/REW1L/munch-helper/blob/main/LICENSE",
            "content": "",
        },
    )

    assert (
        module.fetch_repo_license("REW1L/munch-helper", token="", timeout=1)["license"] == "GPL-3.0"
    )


def test_with_resolved_license_rederives_only_generated_restricted_fields():
    entry = {"name": "demo", **utils.build_legal_metadata(repo="acme/repo", license_name="")}
    resolved = utils.with_resolved_license(entry, "MIT")
    assert resolved["license"] == "MIT"
    assert resolved["distribution"] == "compatible"
    assert resolved["license_class"] == "compatible"
    assert resolved["permission_note"].startswith("Use according to upstream license terms")

    gpl = utils.with_resolved_license(entry, "GPL-3.0")
    assert (gpl["license"], gpl["distribution"]) == ("GPL-3.0", "restricted")

    custom = {**entry, "permission_note": "Reuse approved by the author."}
    assert utils.with_resolved_license(custom, "MIT")["permission_note"] == (
        "Reuse approved by the author."
    )


@pytest.mark.parametrize(
    "license_name,distribution", [("MIT", "compatible"), ("GPL-3.0", "restricted")]
)
def test_resolved_license_populates_absent_derived_fields(license_name, distribution):
    result = utils.with_resolved_license({"name": "demo"}, license_name)
    assert result["distribution"] == distribution
    assert result["license_class"] == distribution
    assert result["permission_note"]


def test_skill_license_replaces_fields_derived_from_catalog_license():
    entry = utils.build_legal_metadata(license_name="MIT")
    result = utils.with_resolved_license(entry, "Proprietary")
    assert result["distribution"] == "restricted"
    assert result["license_class"] == "restricted"


def write_registry(path: Path, entry: dict) -> None:
    path.write_text(json.dumps({"skills": [entry]}), encoding="utf-8")


SKILL_MD = "---\nname: demo\ndescription: Demo skill for license fallback.\n---\n# Demo\n"


@pytest.mark.parametrize(
    "license_route,expected_license,expected_class",
    [
        (FakeResponse(200, json_payload={"license": {"spdx_id": "MIT"}}), "MIT", "compatible"),
        (
            FakeResponse(200, json_payload={"license": {"spdx_id": "GPL-3.0"}}),
            "GPL-3.0",
            "restricted",
        ),
        (None, "NOASSERTION", "restricted"),
    ],
)
@pytest.mark.parametrize("derived_fields", [True, False])
def test_download_falls_back_to_repo_license(
    tmp_path, monkeypatch, license_route, expected_license, expected_class, derived_fields
):
    module = load_sync_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    # Registry entries rebuilt from old archives carry the generated restricted fields.
    write_registry(
        registry_path,
        {
            "name": "demo",
            "repo": "acme/demo",
            "path": "skills/demo",
            "category": "development",
            **{
                key: value
                for key, value in utils.build_legal_metadata(
                    repo="acme/demo", path="skills/demo", license_name=""
                ).items()
                if derived_fields and key in {"license", "distribution", "permission_note"}
            },
        },
    )
    routes = {
        "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
            200, text=SKILL_MD
        ),
        "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
            200, json_payload=[]
        ),
        # A GPL archive must carry the root license file.
        "https://api.github.com/repos/acme/demo/contents?ref=main": FakeResponse(
            200,
            json_payload=[
                {
                    "type": "file",
                    "path": "LICENSE",
                    "download_url": "https://download.example/LICENSE",
                    "size": 12,
                }
            ],
        ),
        "https://download.example/LICENSE": FakeResponse(200, text="GPL v3 text\n"),
    }
    if license_route is not None:
        routes["https://api.github.com/repos/acme/demo/license"] = license_route
    install_fake_aiohttp(monkeypatch, routes)

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=tmp_path / "failure_report.json",
        )
    )

    assert stats["downloaded"] == 1, json.loads((tmp_path / "failure_report.json").read_text())
    metadata = json.loads(next(output_dir.rglob("metadata.json")).read_text(encoding="utf-8"))
    assert metadata["license"] == expected_license
    assert metadata["license_class"] == expected_class
    if expected_class == "compatible":
        assert metadata["distribution"] == "compatible"


class FakeRequestsResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)


@pytest.mark.parametrize(
    "frontmatter_license,license_status,expected",
    [
        (None, 200, "Apache-2.0"),
        (None, 404, "NOASSERTION"),
        ("Proprietary", 200, "Proprietary"),
    ],
)
def test_discovery_uses_frontmatter_then_repo_license(
    tmp_path, monkeypatch, frontmatter_license, license_status, expected
):
    module = load_script("discover_by_topic")
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True
    license_line = f"license: {frontmatter_license}\n" if frontmatter_license else ""
    content = f"---\nname: demo\ndescription: Safe example.\n{license_line}---\n# Demo\n"
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        if url.endswith("/license"):
            return FakeRequestsResponse(license_status, {"license": {"spdx_id": "Apache-2.0"}})
        return FakeRequestsResponse(200, text=content)

    monkeypatch.setattr(discovery.session, "get", get)

    assert discovery.download_skill("acme/demo", "skills/demo/SKILL.md", tmp_path, branch="main")
    metadata = json.loads(next(tmp_path.rglob("metadata.json")).read_text(encoding="utf-8"))
    assert metadata["license"] == expected
    license_calls = [url for url in calls if url.endswith("/license")]
    assert len(license_calls) == (0 if frontmatter_license else 1)


@pytest.mark.parametrize("catalog_license", ["", "MIT"])
@pytest.mark.parametrize("declared", ["Proprietary", "GPL-3.0"])
def test_download_honors_skill_declaration_before_catalog_and_repo(
    tmp_path, monkeypatch, catalog_license, declared
):
    module = load_sync_module()
    registry = tmp_path / "registry.json"
    write_registry(
        registry,
        {
            "name": "demo",
            "repo": "acme/demo",
            "path": "skills/demo",
            "category": "development",
            **utils.build_legal_metadata(license_name=catalog_license),
        },
    )
    content = SKILL_MD.replace("name: demo\n", f"name: demo\nlicense: {declared}\n")
    calls = []

    class RepoLicense(FakeResponse):
        async def json(self):
            calls.append("license")
            return {"license": {"spdx_id": "MIT"}}

    routes = {
        "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
            200, text=content
        ),
        "https://api.github.com/repos/acme/demo/license": RepoLicense(200),
        "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
            200, json_payload=[]
        ),
        "https://api.github.com/repos/acme/demo/contents?ref=main": FakeResponse(
            200,
            json_payload=[
                {
                    "type": "file",
                    "path": "LICENSE",
                    "size": 12,
                    "download_url": "https://download.example/LICENSE",
                }
            ],
        ),
        "https://download.example/LICENSE": FakeResponse(200, text="GPL v3 text\n"),
    }
    install_fake_aiohttp(monkeypatch, routes)
    output = tmp_path / "skills"
    stats = asyncio.run(module.download_skills(registry, output, manifest_path=None))
    assert stats["downloaded"] == 1
    metadata = json.loads(next(output.rglob("metadata.json")).read_text())
    assert metadata["license"] == declared
    assert metadata["distribution"] == "restricted"
    assert not calls
    if declared == "GPL-3.0":
        assert next(output.rglob("LICENSE")).read_text() == "GPL v3 text\n"
        assert next(output.rglob("SKILL.md")).read_text() == content
    else:
        assert not list(output.rglob("LICENSE"))


@pytest.mark.parametrize("failure", ["timeout", "connection", "rate_limit", "server"])
def test_optional_discovery_license_errors_do_not_abort_batch(monkeypatch, failure):
    import requests

    module = load_script("discover_by_topic")
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True
    errors = {
        "timeout": requests.Timeout(),
        "connection": requests.ConnectionError(),
        "rate_limit": requests.HTTPError(response=FakeRequestsResponse(403)),
        "server": requests.HTTPError(response=FakeRequestsResponse(503)),
    }

    def request(*args, **kwargs):
        raise errors[failure]

    monkeypatch.setattr(discovery, "_request", request)
    assert discovery._repo_license("acme/demo") == "NOASSERTION"


def test_discovery_budget_expiry_is_not_swallowed(monkeypatch):
    module = load_script("discover_by_topic")
    discovery = module.GitHubTopicDiscovery(request_delay=0)

    def request(*args, **kwargs):
        raise module.DiscoveryBudgetExpired()

    monkeypatch.setattr(discovery, "_request", request)
    with pytest.raises(module.DiscoveryBudgetExpired):
        discovery._repo_license("acme/demo")


def test_license_requests_share_downloader_concurrency_limit(tmp_path, monkeypatch):
    module = load_sync_module()
    active = peak = 0

    class DelayedLicense(FakeResponse):
        async def json(self):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return {"license": {"spdx_id": "MIT"}}

    entries = []
    routes = {}
    for i in range(101):
        repo = f"acme/demo{i}"
        entries.append(
            {"name": f"demo{i}", "repo": repo, "path": "skills/demo", "category": "development"}
        )
        routes[f"https://raw.githubusercontent.com/{repo}/main/skills/demo/SKILL.md"] = (
            FakeResponse(200, text=SKILL_MD)
        )
        routes[f"https://api.github.com/repos/{repo}/license"] = DelayedLicense(200)
        routes[f"https://api.github.com/repos/{repo}/contents/skills/demo?ref=main"] = FakeResponse(
            200, json_payload=[]
        )
    install_fake_aiohttp(monkeypatch, routes)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"skills": entries}))
    stats = asyncio.run(module.download_skills(registry, tmp_path / "skills", manifest_path=None))
    assert stats["downloaded"] == 101
    assert 1 < peak <= 100


def test_pinned_download_applies_gpl_frontmatter_before_bundling(tmp_path, monkeypatch):
    from test_sync_and_download import exact_repo_routes, git_blob_entry

    module = load_sync_module()
    repo, sha = "acme/demo", "a" * 40
    content = SKILL_MD.replace("name: demo\n", "name: demo\nlicense: GPL-3.0\n")
    license_text = "GPL v3 text\n"
    tree = [
        git_blob_entry("skills/demo/SKILL.md", content.encode()),
        git_blob_entry("LICENSE", license_text.encode()),
    ]
    routes = exact_repo_routes(repo, "main", sha, tree)
    routes[f"https://raw.githubusercontent.com/{repo}/{sha}/skills/demo/SKILL.md"] = FakeResponse(
        200, text=content
    )
    routes[f"https://raw.githubusercontent.com/{repo}/{sha}/LICENSE"] = FakeResponse(
        200, text=license_text
    )
    install_fake_aiohttp(monkeypatch, routes)
    registry = tmp_path / "registry.json"
    write_registry(
        registry,
        {
            "name": "demo",
            "repo": repo,
            "path": "skills/demo/SKILL.md",
            "branch": "main",
            "category": "development",
        },
    )
    output = tmp_path / "skills"
    stats = asyncio.run(
        module.download_skills(
            registry, output, manifest_path=None, exact_paths_only=True, pin_commit_sha=True
        )
    )
    assert stats["downloaded"] == 1
    metadata = json.loads(next(output.rglob("metadata.json")).read_text())
    assert metadata["license"] == "GPL-3.0"
    assert metadata["github_commit_sha"] == sha
    assert next(output.rglob("LICENSE")).read_text() == license_text
    assert next(output.rglob("SKILL.md")).read_text() == content
