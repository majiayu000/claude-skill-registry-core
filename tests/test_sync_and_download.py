import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import types
from datetime import timedelta
from pathlib import Path

import pytest


def load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "sync_and_download.py"
    spec = importlib.util.spec_from_file_location("sync_and_download_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_support_module():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    module_path = scripts_dir / "sync_pipeline_support.py"
    spec = importlib.util.spec_from_file_location("sync_pipeline_support_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status, *, text="", json_payload=None, body=None, content_length=None):
        self.status = status
        self._text = text
        self._json_payload = json_payload
        self._body = text.encode("utf-8") if body is None else body
        self.content_length = len(self._body) if content_length is None else content_length
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def text(self):
        return self._text

    async def json(self):
        return self._json_payload

    async def read(self):
        return self._body

    async def iter_chunked(self, size):
        for offset in range(0, len(self._body), size):
            yield self._body[offset : offset + size]


def install_fake_aiohttp(monkeypatch, routes):
    class FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def get(self, url, timeout=None):
            response = routes.get(url)
            if isinstance(response, Exception):
                raise response
            if response is None:
                return FakeResponse(404)
            return response

    fake_aiohttp = types.SimpleNamespace(
        TCPConnector=lambda *args, **kwargs: object(),
        ClientTimeout=lambda *args, **kwargs: object(),
        ClientSession=FakeClientSession,
        ClientError=OSError,
    )
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp)


def git_blob_sha(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def git_blob_entry(path: str, content: bytes) -> dict:
    return {
        "path": path,
        "type": "blob",
        "mode": "100644",
        "size": len(content),
        "sha": git_blob_sha(content),
    }


def exact_repo_routes(repo: str, branch: str, sha: str, tree: list[dict]) -> dict:
    encoded_branch = branch.replace("/", "%2F")
    if len(branch) == 40 and set(branch) <= set("0123456789abcdefABCDEF"):
        ref_route = {
            f"https://api.github.com/repos/{repo}/commits/{branch.lower()}": FakeResponse(
                200, json_payload={"sha": sha}
            )
        }
    else:
        ref_route = {
            f"https://api.github.com/repos/{repo}/branches/{encoded_branch}": FakeResponse(
                200, json_payload={"name": branch, "commit": {"sha": sha}}
            )
        }
    return {
        f"https://api.github.com/repos/{repo}": FakeResponse(200, json_payload={"full_name": repo}),
        **ref_route,
        f"https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1": FakeResponse(
            200, json_payload={"truncated": False, "tree": tree}
        ),
    }


def test_should_fail_on_empty_download_only_when_all_attempts_fail():
    module = load_module()

    assert module.should_fail_on_empty_download({"downloaded": 0, "failed": 3}) is True
    assert module.should_fail_on_empty_download({"downloaded": 2, "failed": 3}) is False
    assert module.should_fail_on_empty_download({"downloaded": 0, "failed": 0}) is False
    assert (
        module.should_fail_on_empty_download({"downloaded": 0, "failed": 3, "skipped": 10}) is False
    )


def test_search_source_import_does_not_create_cli_log(tmp_path):
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(scripts_dir)!r}); import search_sources",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "sync_and_download.log").exists()


def test_build_unified_registry_inherits_top_level_repo(tmp_path):
    module = load_module()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    output_path = tmp_path / "registry.json"
    (sources_dir / "anthropic.json").write_text(
        json.dumps(
            {
                "name": "Anthropic",
                "repo": "anthropics/skills",
                "skills": [
                    {
                        "name": "docx",
                        "path": "skills/docx",
                        "description": "Document editing skill.",
                        "category": "documents",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.build_unified_registry(sources_dir, output_path) == 1
    registry = json.loads(output_path.read_text(encoding="utf-8"))
    assert registry["skills"][0]["repo"] == "anthropics/skills"


def test_build_unified_registry_preserves_legal_metadata(tmp_path):
    module = load_module()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    output_path = tmp_path / "registry.json"
    (sources_dir / "community.json").write_text(
        json.dumps(
            {
                "name": "Community",
                "skills": [
                    {
                        "name": "product-manager-skills",
                        "repo": "Digidai/product-manager-skills",
                        "description": "Product management skill.",
                        "category": "product",
                        "author": "Gene Dai",
                        "source_url": "https://github.com/Digidai/product-manager-skills/blob/main/SKILL.md",
                        "license": "CC-BY-NC-SA-4.0",
                        "distribution": "restricted",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.build_unified_registry(sources_dir, output_path) == 1
    skill = json.loads(output_path.read_text(encoding="utf-8"))["skills"][0]
    assert skill["author"] == "Gene Dai"
    assert skill["source_url"].endswith("/Digidai/product-manager-skills/blob/main/SKILL.md")
    assert skill["license"] == "CC-BY-NC-SA-4.0"
    assert skill["distribution"] == "restricted"


def test_build_unified_registry_stringifies_legal_metadata(tmp_path):
    module = load_module()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    output_path = tmp_path / "registry.json"
    (sources_dir / "community.json").write_text(
        json.dumps(
            {
                "name": "Community",
                "skills": [
                    {
                        "name": "typed-legal-metadata",
                        "repo": "owner/repo",
                        "description": "Skill with non-string metadata.",
                        "category": "development",
                        "author": 123,
                        "license": 456,
                        "permission_note": ["verify upstream"],
                        "distribution": " restricted ",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.build_unified_registry(sources_dir, output_path) == 1
    skill = json.loads(output_path.read_text(encoding="utf-8"))["skills"][0]
    assert skill["author"] == "123"
    assert skill["license"] == "456"
    assert skill["permission_note"] == "['verify upstream']"
    assert skill["distribution"] == "restricted"


def test_build_unified_registry_dedupes_root_path_spellings(tmp_path):
    module = load_module()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    output_path = tmp_path / "registry.json"
    root_skill = {
        "name": "root-skill",
        "repo": "owner/root-skill",
        "description": "Repo-root skill.",
        "category": "productivity",
    }
    (sources_dir / "community.json").write_text(
        json.dumps({"name": "Community", "skills": [root_skill]}),
        encoding="utf-8",
    )
    (sources_dir / "custom.json").write_text(
        json.dumps(
            {
                "name": "Custom",
                "skills": [{**root_skill, "path": "."}],
            }
        ),
        encoding="utf-8",
    )

    assert module.build_unified_registry(sources_dir, output_path) == 1
    registry = json.loads(output_path.read_text(encoding="utf-8"))
    assert registry["skills"][0]["path"] == ""


def test_build_unified_registry_accepts_non_string_path_values(tmp_path):
    module = load_module()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    output_path = tmp_path / "registry.json"
    (sources_dir / "community.json").write_text(
        json.dumps(
            {
                "name": "Community",
                "skills": [
                    {"name": "numeric-path", "repo": "owner/repo", "path": 123},
                    {
                        "name": "object-path",
                        "repo": "owner/repo",
                        "path": {"bad": "path"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.build_unified_registry(sources_dir, output_path) == 2


def test_build_unified_registry_dedupes_boolean_root_path(tmp_path):
    module = load_module()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    output_path = tmp_path / "registry.json"
    root_skill = {
        "name": "root-skill",
        "repo": "owner/root-skill",
        "description": "Repo-root skill.",
        "category": "productivity",
    }
    (sources_dir / "community.json").write_text(
        json.dumps(
            {
                "name": "Community",
                "skills": [
                    root_skill,
                    {**root_skill, "path": False},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert module.build_unified_registry(sources_dir, output_path) == 1


def test_download_blocks_security_listed_source_repo(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "php-code-injection",
                        "repo": "blacklanternsecurity/red-run",
                        "path": "skills/web/php-code-injection/SKILL.md",
                        "category": "other",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(monkeypatch, {})

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert failure_report["failure_reasons"]["blocked_source"] == 1


def test_download_blocks_security_listed_source_path_alias(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "toprank",
                        "repo": "nowork-studio/toprank",
                        "path": "openclaw/skills/toprank/SKILL.md",
                        "category": "development",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(monkeypatch, {})

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert failure_report["failure_reasons"]["blocked_source"] == 1


@pytest.mark.parametrize("exact_paths_only", [False, True])
def test_download_rejects_drive_relative_source_path(tmp_path, monkeypatch, exact_paths_only):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "C:scripts/SKILL.md",
                        "category": "development",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(monkeypatch, {})

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=exact_paths_only,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert failure_report["failure_reasons"]["invalid_source_path"] == 1


def test_download_removes_skill_that_fails_security_scan(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "unsafe-demo",
                        "repo": "acme/unsafe-demo",
                        "path": "SKILL.md",
                        "category": "development",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/unsafe-demo/main/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: unsafe-demo\n"
                    "description: Demo skill with unsafe shell execution.\n---\n"
                    "# Unsafe Demo\n"
                    "```python\n"
                    "import subprocess\n"
                    "subprocess.run('echo unsafe', shell=True)\n"
                    "```\n"
                ),
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert failure_report["failure_reasons"]["security_scan_failed"] == 1


@pytest.mark.parametrize("source_ref", ["release/v1", "a" * 40])
@pytest.mark.parametrize("support_path", ["scripts/setup.py", "resources/scripts/setup.py"])
def test_exact_download_pins_skill_and_bundled_files_to_commit_sha(
    tmp_path, monkeypatch, source_ref, support_path
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo folder/skill.md",
                        "category": "development",
                        "github_branch": source_ref,
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sha = "a" * 40
    skill_body = (
        f"---\nname: demo\ndescription: A pinned demo skill.\n---\n# Demo\nRun {support_path}.\n".encode()
    )
    script_body = b"print('ok')\n"
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes(
                "acme/demo",
                source_ref,
                sha,
                [
                    git_blob_entry("skills/demo folder/skill.md", skill_body),
                    {
                        "path": f"skills/demo folder/{support_path}",
                        "type": "blob",
                        "mode": "100755",
                        "size": len(script_body),
                        "sha": git_blob_sha(script_body),
                    },
                ],
            ),
            f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo%20folder/skill.md": (
                FakeResponse(
                    200,
                    body=skill_body,
                )
            ),
            f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo%20folder/{support_path}": (
                FakeResponse(200, body=script_body)
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 1
    skill_dir = next(output_dir.glob("development/*"))
    setup_path = skill_dir / support_path
    assert setup_path.read_text() == "print('ok')\n"
    if os.name != "nt":
        assert setup_path.stat().st_mode & 0o111
    metadata = json.loads((skill_dir / "metadata.json").read_text())
    assert metadata["github_branch"] == source_ref
    assert metadata["github_commit_sha"] == sha
    assert metadata["assets_verified_at"].endswith("Z")
    assert metadata["bundled_file_blobs"] == {support_path: git_blob_sha(script_body)}


def test_exact_download_allows_standalone_skill_with_empty_support_listing(
    tmp_path, monkeypatch
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "standalone",
                        "repo": "acme/standalone",
                        "path": "skills/standalone/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sha = "a" * 40
    skill_body = (
        b"---\nname: standalone\ndescription: A complete standalone skill.\n---\n"
        b"# Standalone\nFollow these self-contained instructions.\n"
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes(
                "acme/standalone",
                "main",
                sha,
                [git_blob_entry("skills/standalone/SKILL.md", skill_body)],
            ),
            f"https://raw.githubusercontent.com/acme/standalone/{sha}/skills/standalone/SKILL.md": (  # noqa: E501
                FakeResponse(200, body=skill_body)
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["bundled_files"] == []
    assert "bundled_file_blobs" not in metadata


def test_exact_download_allows_standalone_skill_naming_target_repo_files(
    tmp_path, monkeypatch
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "test-gap-finder",
                        "repo": "acme/chores",
                        "path": "skills/test-gap-finder/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sha = "a" * 40
    skill_body = (
        b"---\nname: test-gap-finder\ndescription: Finds untested code paths.\n---\n"
        b"# Test gap finder\n"
        b"Read the target repository's package.json and pyproject.toml for its test "
        b"command, then report the untested branches in files such as auth.py.\n"
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes(
                "acme/chores",
                "main",
                sha,
                [git_blob_entry("skills/test-gap-finder/SKILL.md", skill_body)],
            ),
            f"https://raw.githubusercontent.com/acme/chores/{sha}/skills/test-gap-finder/SKILL.md": (  # noqa: E501
                FakeResponse(200, body=skill_body)
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["bundled_files"] == []


@pytest.mark.parametrize(
    ("upstream_description", "curated_description"),
    [
        ("x" * 501 + " See references/guide.md.", "A curated replacement description."),
        ("x" * 501, "Read references/guide.md before use."),
    ],
    ids=["raw-reference", "normalized-reference"],
)
def test_exact_download_checks_dependencies_before_and_after_frontmatter_repair(
    tmp_path, monkeypatch, upstream_description, curated_description
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    failure_report_path = tmp_path / "failures.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "description": curated_description,
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sha = "a" * 40
    skill_body = (
        f"---\nname: demo\ndescription: {upstream_description}\n---\n# Demo\n"
    ).encode()
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes(
                "acme/demo",
                "main",
                sha,
                [git_blob_entry("skills/demo/SKILL.md", skill_body)],
            ),
            f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md": (
                FakeResponse(200, body=skill_body)
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert report["failure_reasons"]["bundled_listing_incomplete"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


@pytest.mark.parametrize(
    ("commit_response", "error"),
    [
        (FakeResponse(404), "status 404"),
        (FakeResponse(200, json_payload={"sha": "b" * 40}), "different commit identity"),
    ],
)
def test_commit_pinned_source_ref_fails_closed(monkeypatch, commit_response, error):
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))
    from sync_download_support import resolve_exact_commit_sha

    pinned_ref = "a" * 40
    routes = {
        "https://api.github.com/repos/acme/demo": FakeResponse(
            200, json_payload={"full_name": "acme/demo"}
        ),
        f"https://api.github.com/repos/acme/demo/commits/{pinned_ref}": commit_response,
    }

    class FakeSession:
        def get(self, url, timeout=None):
            return routes[url]

    with pytest.raises(RuntimeError, match=error):
        asyncio.run(
            resolve_exact_commit_sha(
                FakeSession(),
                "acme/demo",
                pinned_ref,
                timeout=object(),
                security_blocklist={},
                repo_cache={},
                commit_cache={},
            )
        )


def test_exact_download_fails_closed_when_commit_cannot_be_resolved(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(monkeypatch, {})

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


def test_exact_download_rejects_redirected_repository_identity(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/old-name",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        )
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://api.github.com/repos/acme/old-name": FakeResponse(
                200, json_payload={"full_name": "acme/new-name"}
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1


def test_exact_download_does_not_probe_name_fallbacks(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "missing/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    sha = "b" * 40
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes("acme/demo", "main", sha, []),
            f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md": (
                FakeResponse(
                    200,
                    text="---\nname: demo\ndescription: fallback\n---\n# Demo\n",
                )
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert not list(output_dir.rglob("SKILL.md"))


def test_exact_download_fails_when_bundle_limits_truncate(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        )
    )
    sha = "c" * 40
    skill_body = (
        b"---\nname: demo\ndescription: A large bundled demo.\n---\n"
        b"# Demo\nRun scripts/tool_0.py.\n"
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes(
                "acme/demo",
                "main",
                sha,
                [
                    git_blob_entry("skills/demo/SKILL.md", skill_body),
                    *[
                        {
                            "path": f"skills/demo/scripts/tool_{index}.py",
                            "type": "blob",
                            "mode": "100644",
                            "size": 10,
                            "sha": "e" * 40,
                        }
                        for index in range(101)
                    ],
                ],
            ),
            f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md": FakeResponse(
                200,
                body=skill_body,
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["bundled_limits_exceeded"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


def test_exact_download_fails_when_an_eligible_asset_exceeds_per_file_limit(tmp_path, monkeypatch):
    module = load_module()
    support = load_support_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        )
    )
    sha = "d" * 40
    skill_body = (
        b"---\nname: demo\ndescription: Oversized asset demo.\n---\n"
        b"# Demo\nRun scripts/huge.py and read references/small.md.\n"
    )
    small_body = b"small\n"
    install_fake_aiohttp(
        monkeypatch,
        {
            **exact_repo_routes(
                "acme/demo",
                "main",
                sha,
                [
                    git_blob_entry("skills/demo/SKILL.md", skill_body),
                    {
                        "path": "skills/demo/scripts/huge.py",
                        "type": "blob",
                        "mode": "100644",
                        "size": support.MAX_BUNDLED_FILE_BYTES + 1,
                        "sha": "e" * 40,
                    },
                    git_blob_entry("skills/demo/references/small.md", small_body),
                ],
            ),
            f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md": (
                FakeResponse(200, body=skill_body)
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["bundled_limits_exceeded"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


def test_exact_download_requires_explicit_complete_git_tree(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                    }
                ]
            }
        )
    )
    sha = "9" * 40
    skill_body = b"---\nname: demo\ndescription: Exact demo.\n---\n# Demo\n"
    routes = exact_repo_routes(
        "acme/demo",
        "main",
        sha,
        [git_blob_entry("skills/demo/SKILL.md", skill_body)],
    )
    routes[f"https://api.github.com/repos/acme/demo/git/trees/{sha}?recursive=1"] = FakeResponse(
        200, json_payload={"tree": [git_blob_entry("skills/demo/SKILL.md", skill_body)]}
    )
    install_fake_aiohttp(monkeypatch, routes)

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["bundled_listing_failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


def test_exact_download_rejects_non_portable_file_in_support_scope(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                    }
                ]
            }
        )
    )
    sha = "7" * 40
    skill_body = b"---\nname: demo\ndescription: Exact demo.\n---\nRun scripts/good.py.\n"
    routes = exact_repo_routes(
        "acme/demo",
        "main",
        sha,
        [
            git_blob_entry("skills/demo/SKILL.md", skill_body),
            git_blob_entry("skills/demo/scripts/good.py", b"print('good')"),
            git_blob_entry("skills/demo/scripts/bad:name.py", b"print('bad')"),
        ],
    )
    install_fake_aiohttp(monkeypatch, routes)

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["bundled_listing_failed"] == 1
    assert "non-portable bundled path" in report["failures"]["bundled_listing_failed"][0]
    assert not list(output_dir.rglob("SKILL.md"))


@pytest.mark.parametrize("source_size", [None, 1_000_001])
def test_exact_download_rejects_invalid_skill_blob(tmp_path, monkeypatch, source_size):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                    }
                ]
            }
        )
    )
    sha = "8" * 40
    expected_body = b"---\nname: demo\ndescription: Expected demo.\n---\n# Demo\n"
    source_entry = git_blob_entry("skills/demo/SKILL.md", expected_body)
    if source_size is not None:
        source_entry["size"] = source_size
    routes = exact_repo_routes("acme/demo", "main", sha, [source_entry])
    routes[f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md"] = (
        FakeResponse(
            200,
            body=b"---\nname: demo\ndescription: Substituted demo.\n---\n# Demo\n",
        )
    )
    install_fake_aiohttp(monkeypatch, routes)

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


@pytest.mark.parametrize(
    ("asset_path", "asset_entry", "body"),
    [
        (
            "scripts/run.py",
            {"type": "blob", "mode": "120000", "size": 12, "sha": "f" * 40},
            b"target.py",
        ),
        ("scripts/run.py", {"type": "commit", "mode": "160000", "size": 0, "sha": "f" * 40}, b""),
        ("scripts", {"type": "blob", "mode": "120000", "size": 12, "sha": "f" * 40}, b"target.py"),
        ("scripts", {"type": "commit", "mode": "160000", "size": 0, "sha": "f" * 40}, b""),
    ],
)
def test_exact_download_rejects_non_regular_upstream_assets(
    tmp_path, monkeypatch, asset_path, asset_entry, body
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                    }
                ]
            }
        )
    )
    sha = "1" * 40
    skill_body = b"---\nname: demo\ndescription: Exact demo.\n---\n# Demo\nRun scripts/run.py.\n"
    routes = exact_repo_routes(
        "acme/demo",
        "main",
        sha,
        [
            git_blob_entry("skills/demo/SKILL.md", skill_body),
            {"path": f"skills/demo/{asset_path}", **asset_entry},
        ],
    )
    routes[f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md"] = (
        FakeResponse(
            200,
            body=skill_body,
        )
    )
    routes[f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/{asset_path}"] = (
        FakeResponse(200, body=body)
    )
    install_fake_aiohttp(monkeypatch, routes)

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["bundled_listing_failed"] == 1


def test_exact_download_rejects_truncated_asset_response(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo/SKILL.md",
                        "category": "development",
                        "github_branch": "main",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        )
    )
    sha = "3" * 40
    skill_body = b"---\nname: demo\ndescription: Exact demo.\n---\n# Demo\nRun scripts/run.py.\n"
    expected_body = b"x" * 100
    routes = exact_repo_routes(
        "acme/demo",
        "main",
        sha,
        [
            git_blob_entry("skills/demo/SKILL.md", skill_body),
            {
                "path": "skills/demo/scripts/run.py",
                "type": "blob",
                "mode": "100644",
                "size": len(expected_body),
                "sha": git_blob_sha(expected_body),
            },
        ],
    )
    routes[f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/SKILL.md"] = (
        FakeResponse(
            200,
            body=skill_body,
        )
    )
    routes[f"https://raw.githubusercontent.com/acme/demo/{sha}/skills/demo/scripts/run.py"] = (
        FakeResponse(200, body=b"x")
    )
    install_fake_aiohttp(monkeypatch, routes)

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
            exact_paths_only=True,
            pin_commit_sha=True,
        )
    )

    assert stats["downloaded"] == 0
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["bundled_download_failed"] == 1


@pytest.mark.parametrize("master_resolves", [False, True])
def test_failed_branch_probes_retain_earlier_resolved_attempts(tmp_path, monkeypatch, master_resolves):
    module = load_module()
    path = "skills/demo/SKILL.md"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"skills": [{"repo": "acme/demo", "path": path}]}))
    body = b"---\nname: demo\ndescription: Demo instructions.\n---\n# Demo\n"
    main_sha, master_sha = "9" * 40, "8" * 40
    routes = exact_repo_routes("acme/demo", "main", main_sha, [git_blob_entry(path, body)])
    if master_resolves:
        routes.update(exact_repo_routes("acme/demo", "master", master_sha, [git_blob_entry(path, body)]))
    install_fake_aiohttp(monkeypatch, routes)
    monkeypatch.setitem(module.download_skills.__globals__, "build_relative_candidates", lambda *_args: [path])
    monkeypatch.setitem(module.download_skills.__globals__, "BRANCHES", ["main", "master"])
    report_path = tmp_path / "failure.json"
    stats = asyncio.run(module.download_skills(
        registry_path, tmp_path / "skills", manifest_path=None,
        failure_report_path=report_path, cleanup_ci_untracked=False, pin_commit_sha=True,
    ))
    assert stats["failed"] == 1
    report = json.loads(report_path.read_text())
    reason = "not_found" if master_resolves else "commit_resolution_failed"
    assert report["failure_reasons"] == {reason: 1}
    failure = report["failures"][reason][0]
    assert f"main@{main_sha}" in failure
    assert "master" in failure
    assert path in failure
    if master_resolves:
        assert f"master@{master_sha}" in failure


@pytest.mark.parametrize("path", [None, ""])
def test_preprobe_missing_path_is_not_reported_as_root_skill(tmp_path, monkeypatch, path):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"skills": [{"repo": "acme/demo", "path": path}]}))
    report_path = tmp_path / "failure.json"
    install_fake_aiohttp(monkeypatch, {})
    stats = asyncio.run(module.download_skills(
        registry_path, tmp_path / "skills", manifest_path=None,
        failure_report_path=report_path, cleanup_ci_untracked=False, exact_paths_only=True,
    ))
    assert stats["failed"] == 1
    report = json.loads(report_path.read_text())
    failure = report["failures"]["invalid_exact_path"][0]
    assert "acme/demo:<unspecified path>" in failure
    assert "SKILL.md" not in failure


@pytest.mark.parametrize("stage", ["commit", "listing", "not_found", "internal", "scan_exception"])
def test_unnamed_download_failures_identify_source_and_attempted_revision(tmp_path, monkeypatch, stage):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    report_path = tmp_path / "failure.json"
    skill = {"repo": "acme/demo", "path": "skills/demo/SKILL.md", "github_branch": "release",
             "license": "MIT", "distribution": "compatible"}
    registry_path.write_text(json.dumps({"skills": [skill]}))
    sha = "9" * 40
    body = b"---\nname: demo\ndescription: Demo instructions.\n---\n# Demo\n"
    tree = [] if stage == "listing" else [git_blob_entry(skill["path"], body)]
    routes = exact_repo_routes(skill["repo"], "release", sha, tree)
    if stage == "scan_exception":
        routes[f"https://raw.githubusercontent.com/acme/demo/{sha}/{skill['path']}"] = FakeResponse(200, body=body)
        import security_scanner

        def fail_scan(*_args, **_kwargs):
            raise RuntimeError("unexpected scanner failure")
        monkeypatch.setattr(security_scanner.SecurityScanner, "scan_file", fail_scan)
    install_fake_aiohttp(monkeypatch, routes)
    if stage == "commit":
        async def fail_resolution(*_args, **_kwargs):
            raise RuntimeError("revision lookup unavailable")
        monkeypatch.setitem(module.download_skills.__globals__, "resolve_exact_commit_sha", fail_resolution)
    if stage == "internal":
        def fail_normalize(_repo):
            raise RuntimeError("unexpected normalize failure")
        monkeypatch.setitem(module.download_skills.__globals__, "normalize_download_repo", fail_normalize)
    stats = asyncio.run(module.download_skills(
        registry_path, tmp_path / "skills", manifest_path=None,
        failure_report_path=report_path, cleanup_ci_untracked=False,
        exact_paths_only=True, pin_commit_sha=True,
    ))
    assert stats["downloaded"] == 0 and stats["failed"] == 1
    report = json.loads(report_path.read_text())
    reason = {"commit": "commit_resolution_failed", "listing": "bundled_listing_failed",
              "not_found": "not_found", "internal": "internal_error", "scan_exception": "internal_error"}[stage]
    assert report["failure_reasons"] == {reason: 1}
    failure = report["failures"][reason][0]
    assert skill["repo"] in failure
    assert skill["path"] in failure
    assert "release" in failure
    if stage in {"listing", "not_found", "scan_exception"}:
        assert sha in failure
    elif stage == "commit":
        assert "revision lookup unavailable" in failure
    else:
        assert "unexpected normalize failure" in failure
    if stage == "scan_exception":
        assert "unexpected scanner failure" in failure
    assert not list((tmp_path / "skills").rglob("SKILL.md"))


def test_unexpected_skill_exception_is_recorded_in_failure_report(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                    }
                ]
            }
        )
    )
    install_fake_aiohttp(monkeypatch, {})

    def explode(_repo):
        raise RuntimeError("unexpected normalize failure")

    monkeypatch.setitem(module.download_skills.__globals__, "normalize_download_repo", explode)
    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
        )
    )

    assert stats["failed"] == 1
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["internal_error"] == 1
    assert "unexpected normalize failure" in report["failures"]["internal_error"][0]


def test_security_scanner_exception_is_internal_error_and_cleans_archive(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                    }
                ]
            }
        )
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/SKILL.md": FakeResponse(
                200,
                text="---\nname: demo\ndescription: Scanner failure demo.\n---\n# Demo\n",
            ),
        },
    )
    import security_scanner

    monkeypatch.setattr(
        security_scanner.SecurityScanner,
        "scan_file",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("scanner crashed")),
    )
    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
            cleanup_ci_untracked=False,
        )
    )

    assert stats["failed"] == 1
    assert stats["bundled_files"] == 0
    report = json.loads(failure_report_path.read_text())
    assert report["failure_reasons"]["internal_error"] == 1
    assert "scanner crashed" in report["failures"]["internal_error"][0]
    assert not list(output_dir.rglob("SKILL.md"))


def test_download_removes_existing_blocked_archive_before_existing_skip(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    skill_dir = output_dir / "other" / "php-code-injection"
    failure_report_path = tmp_path / "failure_report.json"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: php-code-injection
description: Existing blocked archive.
---

# Demo
""",
        encoding="utf-8",
    )
    (skill_dir / "metadata.json").write_text(
        json.dumps({"repo": "blacklanternsecurity/red-run"}),
        encoding="utf-8",
    )
    registry_path.write_text(json.dumps({"skills": []}), encoding="utf-8")
    install_fake_aiohttp(monkeypatch, {})

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["blocked_archives_removed"] == 1
    assert stats["downloaded"] == 0
    assert not skill_dir.exists()


def test_download_removes_ci_untracked_archive_leftovers(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    stale_dir = output_dir / "other" / "old-core-leftover"
    stale_dir.mkdir(parents=True)
    (stale_dir / "SKILL.md").write_text(
        """---
name: old-core-leftover
description: Stale file left by the core checkout.
---

# Demo
""",
        encoding="utf-8",
    )
    (stale_dir / "metadata.json").write_text("{}", encoding="utf-8")
    registry_path.write_text(json.dumps({"skills": []}), encoding="utf-8")
    install_fake_aiohttp(monkeypatch, {})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    subprocess_result = subprocess.run(
        ["git", "init"],
        cwd=output_dir,
        check=True,
        capture_output=True,
    )
    assert subprocess_result.returncode == 0

    stats = asyncio.run(
        module.download_skills(registry_path, output_dir, manifest_path=None)
    )

    assert stats["ci_untracked_files_removed"] == 2
    assert not stale_dir.exists()


def test_download_can_skip_ci_untracked_archive_cleanup(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    discovered_dir = output_dir / "other" / "new-discovery"
    discovered_dir.mkdir(parents=True)
    (discovered_dir / "SKILL.md").write_text(
        """---
name: new-discovery
description: Newly discovered in this workflow run.
---

# Demo
""",
        encoding="utf-8",
    )
    (discovered_dir / "metadata.json").write_text("{}", encoding="utf-8")
    registry_path.write_text(json.dumps({"skills": []}), encoding="utf-8")
    install_fake_aiohttp(monkeypatch, {})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    subprocess_result = subprocess.run(
        ["git", "init"],
        cwd=output_dir,
        check=True,
        capture_output=True,
    )
    assert subprocess_result.returncode == 0

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            cleanup_ci_untracked=False,
        )
    )

    assert stats["ci_untracked_files_removed"] == 0
    assert (discovered_dir / "SKILL.md").exists()


def test_existing_archive_blocks_security_listed_github_path(tmp_path):
    module = load_module()
    output_dir = tmp_path / "skills"
    skill_dir = output_dir / "other" / "primr-strategy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: primr-strategy
description: Existing archive with blocked github_path.
---

# Demo
""",
        encoding="utf-8",
    )
    (skill_dir / "metadata.json").write_text(
        json.dumps(
            {
                "repo": "blisspixel/primr",
                "github_path": "openclaw/skills/primr-strategy",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="Existing archive contains blocked source repos"):
        module.validate_existing_archive_sources(
            output_dir,
            module.load_security_blocklist(),
        )


def test_manifest_round_trip(tmp_path):
    module = load_module()
    manifest_path = tmp_path / "acquisition_manifest.json"
    entries = {
        "development:demo-skill": {
            "repo": "acme/demo",
            "branch": "main",
            "relative_path": "skills/demo/SKILL.md",
            "updated_at": "2026-04-10T00:00:00Z",
        }
    }

    module.save_acquisition_manifest(manifest_path, entries)
    loaded = module.load_acquisition_manifest(manifest_path)
    assert loaded == entries


def test_manifest_loader_tolerates_legacy_and_invalid_entries(tmp_path):
    module = load_module()
    manifest_path = tmp_path / "acquisition_manifest.json"
    manifest_path.write_text(
        """
        {
          "legacy_key": {"repo": "acme/demo", "branch": "main", "relative_path": "SKILL.md"},
          "bad_key": {"repo": "acme/demo", "branch": "", "relative_path": ""},
          "bad_type": "oops"
        }
        """,
        encoding="utf-8",
    )

    loaded = module.load_acquisition_manifest(manifest_path)
    assert loaded == {
        "legacy_key": {
            "repo": "acme/demo",
            "branch": "main",
            "relative_path": "SKILL.md",
            "updated_at": "",
        }
    }


def test_probe_order_prefers_manifest_hints():
    module = load_module()
    manifest_entry = {"branch": "release", "relative_path": "custom/path/SKILL.md"}
    preferred = {"acme/demo": "main"}

    branch_order = module.build_branch_probe_order(
        "acme/demo", preferred, manifest_entry, ("main", "master")
    )
    path_order = module.build_relative_probe_order(
        ["skills/demo/SKILL.md", "SKILL.md"], manifest_entry
    )

    assert branch_order == ["release", "main", "master"]
    assert path_order == ["custom/path/SKILL.md", "skills/demo/SKILL.md", "SKILL.md"]


def test_probe_order_removes_duplicates():
    module = load_module()
    manifest_entry = {"branch": "main", "relative_path": "skills/demo/SKILL.md"}
    preferred = {"acme/demo": "main"}

    branch_order = module.build_branch_probe_order(
        "acme/demo", preferred, manifest_entry, ("main", "master")
    )
    path_order = module.build_relative_probe_order(
        ["skills/demo/SKILL.md", "SKILL.md", "SKILL.md"], manifest_entry
    )

    assert branch_order == ["main", "master"]
    assert path_order == ["skills/demo/SKILL.md", "SKILL.md"]


def test_download_skills_can_disable_acquisition_manifest(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    default_manifest = tmp_path / "default_manifest.json"
    stale_manifest = {
        "entries": {
            "acme/demo:skills/demo": {
                "repo": "acme/demo",
                "branch": "release",
                "relative_path": "stale/SKILL.md",
                "updated_at": "2026-04-10T00:00:00Z",
            }
        }
    }
    default_manifest.write_text(json.dumps(stale_manifest), encoding="utf-8")
    monkeypatch.setitem(
        module.download_skills.__globals__,
        "DEFAULT_MANIFEST_PATH",
        default_manifest,
    )
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo",
                        "category": "development",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\n"
                    "description: Demo skill without manifest help.\n---\n"
                    "# Demo\nUse this skill directly.\n"
                ),
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=None,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["manifest_hits"] == 0
    assert stats["manifest_misses"] == 0
    assert json.loads(default_manifest.read_text(encoding="utf-8")) == stale_manifest


def test_skill_source_dir_resolves_skill_parent():
    module = load_module()

    assert module.skill_source_dir("skills/demo/SKILL.md") == "skills/demo"
    assert module.skill_source_dir(".claude/skills/demo/SKILL.md") == ".claude/skills/demo"
    assert module.skill_source_dir("SKILL.md") == ""
    assert module.skill_source_dir("") == ""


@pytest.mark.parametrize("path", [
    "~/.local/bin/agent-chain-doctor",
    "/usr/local/bin/agent-chain-doctor",
    "/opt/tool/scripts/check.py",
    r"C:\Tools\scripts\check.py",
    r'"C:\Program Files\Tool\scripts\check.py"',
    '"~/Library/Application Support/tool/scripts/check.py"',
    r"~/Library/Application\ Support/tool/scripts/check.py",
])
def test_external_absolute_prerequisite_is_not_a_bundled_asset(path):
    support = load_support_module()
    content = f"Install the prerequisite separately, then run `{path}`."
    assert support.requires_complete_bundled_archive(content) is False
    assert support.requires_complete_bundled_archive(content + " Read references/guide.md.") is True


def test_explicit_relative_support_path_still_requires_bundle():
    support = load_support_module()
    assert support.requires_complete_bundled_archive("Run ./scripts/check.py") is True
    assert support.requires_complete_bundled_archive("Run scripts/check.py") is True


@pytest.mark.parametrize("content", [
    "/usr/local/bin/tool&&scripts/setup.sh",
    "/usr/local/bin/tool|scripts/setup.sh",
    "/usr/local/bin/tool;scripts/setup.sh",
    "${CLAUDE_PLUGIN_ROOT}/scripts/check.py",
    "${SKILL_DIR}/references/guide.md",
    "$(pwd)/scripts/check.py",
])
def test_absolute_prerequisites_preserve_following_bundled_dependencies(content):
    support = load_support_module()
    assert support.requires_complete_bundled_archive(content) is True


def test_bounded_reader_uses_decoded_size_for_compressed_responses():
    load_support_module()
    from sync_download_support import read_response_bytes_limited

    response = FakeResponse(200, body=b'{"mode":"preview"}', content_length=38)
    response.headers = {"Content-Encoding": "gzip"}
    assert asyncio.run(read_response_bytes_limited(response, 18)) == b'{"mode":"preview"}'

    response = FakeResponse(200, body=b"x" * 19, content_length=10)
    response.headers = {"Content-Encoding": "gzip"}
    with pytest.raises(ValueError, match="response body exceeds"):
        asyncio.run(read_response_bytes_limited(response, 18))

    response = FakeResponse(200, body=b"x", content_length=19)
    with pytest.raises(ValueError, match="Content-Length"):
        asyncio.run(read_response_bytes_limited(response, 18))


def test_bundled_file_allowlist_is_scoped_and_size_limited():
    module = load_module()
    support = load_support_module()

    assert module.bundled_relative_path("", "package.json") == "package.json"
    assert support.is_safe_portable_relative_path("references/guide.md") is True
    for invalid_path in (
        "CON",
        "references/aux.txt",
        "references/name.",
        "references/name ",
        "references/a:b.md",
        "references/a?b.md",
        "references/a*b.md",
        "references/a|b.md",
        "references/a<b.md",
        "references/a>b.md",
        "references/COM¹.txt",
        "references/lpt³.log",
    ):
        assert support.is_safe_portable_relative_path(invalid_path) is False
    assert (
        module.bundled_relative_path("skills/demo", "skills/demo/scripts/run.sh")
        == "scripts/run.sh"
    )
    assert module.bundled_relative_path("skills/demo", "other/scripts/run.sh") == ""
    assert module.should_recurse_bundled_dir("scripts") is True
    assert module.should_recurse_bundled_dir("bin") is True
    assert module.should_recurse_bundled_dir("bin/nested") is False
    assert module.should_recurse_bundled_dir("references/nested") is True
    assert module.should_recurse_bundled_dir("reference") is True
    assert module.should_recurse_bundled_dir("connectors") is True
    assert module.should_recurse_bundled_dir("knowledge") is True
    assert module.should_recurse_bundled_dir("prompts") is True
    assert module.should_recurse_bundled_dir("src") is True
    assert module.should_recurse_bundled_dir("design-spatial") is True
    assert module.should_recurse_bundled_dir("docs") is False
    assert support.has_case_conflicting_paths(["references/Guide.md", "references/guide.md"])
    assert module.is_safe_bundled_file("references/helper.py", 1024) is True
    assert module.is_safe_bundled_file("reference/environment.md", 1024) is True
    assert module.is_safe_bundled_file("connectors/slack.md", 1024) is True
    assert module.is_safe_bundled_file("knowledge/finance-metrics.md", 1024) is True
    assert module.is_safe_bundled_file("prompts/audit-system-prompt.md", 1024) is True
    assert module.is_safe_bundled_file("src/polish.py", 1024) is True
    assert module.is_safe_bundled_file("src/events-log.swift", 1024) is True
    assert module.is_safe_bundled_file("webmedia.py", 1024) is True
    assert module.is_safe_bundled_file("sck-record.swift", 1024) is True
    assert module.is_safe_bundled_file("design-spatial/SKILL.md", 1024) is True
    assert module.is_safe_bundled_file("design-spatial/scripts/layout-audit.js", 1024) is True
    assert module.is_safe_bundled_file("scripts/listen.mjs", 1024) is True
    assert module.is_safe_bundled_file("bin/jq-linux-amd64", 2_319_424) is True
    assert module.is_safe_bundled_file("bin/jq-windows-amd64.exe", 985_088) is True
    assert module.is_safe_bundled_file("bin/jq.LICENSE", 6_026) is True
    assert module.is_safe_bundled_file("bin/random-tool", 1024) is False
    assert module.is_safe_bundled_file("bin/nested/jq-linux-amd64", 1024) is False
    assert module.is_safe_bundled_file("package.json", 1024) is True
    assert module.is_safe_bundled_file("setup.md", 1024) is True
    assert module.is_safe_bundled_file("audit.md", 1024) is True
    assert module.is_safe_bundled_file("references/SKILL.md", 1024) is False
    assert module.is_safe_bundled_file("examples/SKILL.md", 1024) is False
    assert module.is_safe_bundled_file("docs/helper.py", 1024) is False
    assert module.is_safe_bundled_file("references/.env", 10) is False
    for non_portable in (
        "references/a:b.md",
        "references/CON.txt",
        "/references/guide.md",
        "references/guide.md/",
    ):
        assert module.is_safe_bundled_file(non_portable, 10) is False
    assert (
        support.is_safe_bundled_file("scripts/unrelated:name.bin", 10, reject_nonportable=True)
        is False
    )
    with pytest.raises(support.BundledListingError, match="non-portable bundled path"):
        support.is_safe_bundled_file("scripts/bad:name.py", 10, reject_nonportable=True)
    assert (
        module.is_safe_bundled_file(
            "references/huge.py",
            module.MAX_BUNDLED_FILE_BYTES + 1,
        )
        is False
    )
    assert (
        module.is_safe_bundled_file(
            "bin/jq-linux-amd64",
            support.MAX_BUNDLED_BIN_FILE_BYTES + 1,
        )
        is False
    )
    assert support.requires_complete_bundled_archive("See references/guide.md") is True
    assert support.requires_complete_bundled_archive("Run src/polish.py") is True
    assert support.requires_complete_bundled_archive("Read design-spatial/SKILL.md") is True
    assert support.requires_complete_bundled_archive("Set user preference/theme.md") is False
    assert support.requires_complete_bundled_archive("Run webmedia.py") is False
    assert (
        support.requires_complete_bundled_archive(
            "Check package.json, pyproject.toml and requirements.txt for a test command."
        )
        is False
    )
    normalized = support.normalize_skill_frontmatter_description(
        f"---\nname: demo\ndescription: {'x' * 501}\n---\n# Demo\n",
        {"description": "Curated short source description."},
    )
    assert "Curated short source description." in normalized
    assert "x" * 501 not in normalized

    repaired = support.normalize_skill_frontmatter_description(
        "# Demo\n\nA body paragraph that remains after frontmatter repair.\n",
        {"name": "Demo Skill", "description": "Curated source description."},
    )
    assert repaired.startswith(
        "---\nname: demo-skill\ndescription: Curated source description.\n---\n\n"
    )
    assert "A body paragraph that remains" in repaired


def test_bundles_root_helpers_src_and_design_subskills(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "media-design",
                        "repo": "acme/media-design",
                        "path": "",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    skill_text = (
        "---\nname: media-design\n"
        "description: Demo skill with root helpers and design subskills.\n---\n"
        "# Demo\n"
        "Read config.json; run webmedia.py, sck-record.swift, src/polish.py, and design-spatial/SKILL.md.\n"
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/media-design/main/SKILL.md": FakeResponse(
                200,
                text=skill_text,
            ),
            "https://api.github.com/repos/acme/media-design/contents?ref=main": FakeResponse(
                200,
                json_payload=[
                    {"type": "file", "path": "SKILL.md", "size": len(skill_text)},
                    {
                        "type": "file",
                        "path": "config.json",
                        "download_url": "https://download.example/config.json",
                        "size": len('{"mode": "preview"}'),
                    },
                    {
                        "type": "file",
                        "path": "webmedia.py",
                        "download_url": "https://download.example/webmedia.py",
                        "size": 20,
                    },
                    {
                        "type": "file",
                        "path": "sck-record.swift",
                        "download_url": "https://download.example/sck-record.swift",
                        "size": 20,
                    },
                    {"type": "dir", "path": "src", "size": 0},
                    {"type": "dir", "path": "design-spatial", "size": 0},
                ],
            ),
            "https://api.github.com/repos/acme/media-design/contents/src?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "file",
                        "path": "src/polish.py",
                        "download_url": "https://download.example/polish.py",
                        "size": 20,
                    }
                ],
            ),
            "https://api.github.com/repos/acme/media-design/contents/design-spatial?ref=main": (
                FakeResponse(
                    200,
                    json_payload=[
                            {
                                "type": "file",
                                "path": "design-spatial/SKILL.md",
                                "download_url": "https://download.example/design-spatial-skill",
                                "size": 88,
                            },
                        {"type": "dir", "path": "design-spatial/scripts", "size": 0},
                    ],
                )
            ),
            "https://api.github.com/repos/acme/media-design/contents/design-spatial/scripts?ref=main": (
                FakeResponse(
                    200,
                    json_payload=[
                        {
                            "type": "file",
                            "path": "design-spatial/scripts/layout-audit.js",
                            "download_url": "https://download.example/layout-audit.js",
                            "size": 20,
                        }
                    ],
                )
            ),
            "https://download.example/config.json": FakeResponse(200, text='{"mode": "preview"}'),
            "https://download.example/webmedia.py": FakeResponse(200, text="print('media')\n"),
            "https://download.example/sck-record.swift": FakeResponse(200, text='print("rec")\n'),
            "https://download.example/polish.py": FakeResponse(200, text="print('polish')\n"),
            "https://download.example/design-spatial-skill": FakeResponse(
                200,
                text=(
                    "---\nname: design-spatial\n"
                    "description: Demo nested design subskill.\n---\n# Design Spatial\n"
                ),
            ),
            "https://download.example/layout-audit.js": FakeResponse(
                200, text="console.log('ok')\n"
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    assert stats["bundled_files"] == 6
    skill_dir = next(output_dir.glob("development/*"))
    assert json.loads((skill_dir / "config.json").read_text()) == {"mode": "preview"}
    assert (skill_dir / "webmedia.py").is_file()
    assert (skill_dir / "sck-record.swift").is_file()
    assert (skill_dir / "src" / "polish.py").is_file()
    assert (skill_dir / "design-spatial" / "SKILL.md").is_file()
    assert (skill_dir / "design-spatial" / "scripts" / "layout-audit.js").is_file()
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["archive_mode"] == "directory"
    assert metadata["bundled_files"] == [
        "config.json",
        "design-spatial/SKILL.md",
        "design-spatial/scripts/layout-audit.js",
        "sck-record.swift",
        "src/polish.py",
        "webmedia.py",
    ]


def test_bundled_download_failure_does_not_publish_partial_archive(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\n"
                    "description: Demo skill with a helper script.\n---\n# Demo\n"
                    "Run scripts/run.sh before using this skill.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "dir",
                        "path": "skills/demo/scripts",
                        "size": 0,
                    }
                ],
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo/scripts?ref=main": (
                FakeResponse(
                    200,
                    json_payload=[
                        {
                            "type": "file",
                            "path": "skills/demo/scripts/run.sh",
                            "download_url": "https://download.example/run.sh",
                            "size": 10,
                        }
                    ],
                )
            ),
            "https://download.example/run.sh": FakeResponse(503),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert stats["bundled_files"] == 0
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert failure_report["failure_reasons"]["bundled_download_failed"] == 1


def test_bundled_listing_failure_does_not_publish_skill_md_only(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\n"
                    "description: Demo skill with references.\n---\n# Demo\n"
                    "Read references/guide.md before using this skill.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
                403
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert stats["bundled_files"] == 0
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert failure_report["failure_reasons"]["bundled_listing_failed"] == 1
    assert "status 403" in failure_report["failures"]["bundled_listing_failed"][0]


def test_non_portable_required_bundle_path_fails_instead_of_degrading(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\ndescription: Demo with required assets.\n---\n"
                    "# Demo\nRead references/a:b.md before use.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
                200,
                json_payload=[{"type": "dir", "path": "skills/demo/references", "size": 0}],
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo/references?ref=main": (
                FakeResponse(
                    200,
                    json_payload=[
                        {
                            "type": "file",
                            "path": "skills/demo/references/a:b.md",
                            "size": 10,
                        }
                    ],
                )
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert not list(output_dir.rglob("SKILL.md"))
    failure_report = json.loads(failure_report_path.read_text())
    assert failure_report["failure_reasons"]["bundled_listing_failed"] == 1
    assert "non-portable bundled path" in failure_report["failures"]["bundled_listing_failed"][0]


def test_case_conflicting_required_bundle_paths_fail_before_download(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\ndescription: Demo with required assets.\n---\n"
                    "# Demo\nRead references/Guide.md before use.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
                200,
                json_payload=[{"type": "dir", "path": "skills/demo/references", "size": 0}],
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo/references?ref=main": (
                FakeResponse(
                    200,
                    json_payload=[
                        {
                            "type": "file",
                            "path": "skills/demo/references/Guide.md",
                            "size": 10,
                        },
                        {
                            "type": "file",
                            "path": "skills/demo/references/guide.md",
                            "size": 10,
                        },
                    ],
                )
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 0
    failure_report = json.loads(failure_report_path.read_text())
    assert failure_report["failure_reasons"]["bundled_listing_failed"] == 1
    assert (
        "case-conflicting bundled paths" in failure_report["failures"]["bundled_listing_failed"][0]
    )
    assert not list(output_dir.rglob("SKILL.md"))


def test_bundled_listing_failure_degrades_when_skill_has_no_support_refs(
    tmp_path,
    monkeypatch,
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "skills/demo",
                        "category": "development",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/skills/demo/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\n"
                    "description: Demo skill without support files.\n---\n"
                    "# Demo\nUse this skill directly from the markdown instructions.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents/skills/demo?ref=main": FakeResponse(
                403
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    assert stats["bundled_files"] == 0
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["archive_mode"] == "skill-md"
    assert metadata["bundled_files"] == []
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert "bundled_listing_failed" not in failure_report["failure_reasons"]


def test_optional_bundled_download_failure_degrades_to_skill_md(
    tmp_path,
    monkeypatch,
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\n"
                    "description: Demo skill with optional repo files.\n---\n"
                    "# Demo\nUse this skill directly from the markdown instructions.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents?ref=main": FakeResponse(
                200,
                json_payload=[
                    {"type": "file", "path": "SKILL.md", "size": 80},
                    {"type": "dir", "path": "scripts", "size": 0},
                ],
            ),
            "https://api.github.com/repos/acme/demo/contents/scripts?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "file",
                        "path": "scripts/optional.py",
                        "download_url": "https://download.example/optional.py",
                        "size": 12,
                    }
                ],
            ),
            "https://download.example/optional.py": FakeResponse(503),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    assert stats["bundled_files"] == 0
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["archive_mode"] == "skill-md"
    assert metadata["bundled_files"] == []
    assert not (skill_dir / "scripts" / "optional.py").exists()
    failure_report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert "bundled_download_failed" not in failure_report["failure_reasons"]


def test_bundled_references_rules_and_knowledge_are_archived_with_directory_mode(
    tmp_path, monkeypatch
):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                        "license": "MIT",
                        "distribution": "compatible",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\n"
                    "description: Demo skill with references.\n---\n"
                    "# Demo\nSee references/guide.md, rules/rule.md, and knowledge/framework.md.\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents?ref=main": FakeResponse(
                200,
                json_payload=[
                    {"type": "file", "path": "SKILL.md", "size": 80},
                    {"type": "dir", "path": "references", "size": 0},
                    {"type": "dir", "path": "rules", "size": 0},
                    {"type": "dir", "path": "knowledge", "size": 0},
                ],
            ),
            "https://api.github.com/repos/acme/demo/contents/references?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "file",
                        "path": "references/guide.md",
                        "download_url": "https://download.example/guide.md",
                        "size": 12,
                    }
                ],
            ),
            "https://download.example/guide.md": FakeResponse(200, body=b"# Guide\n"),
            "https://api.github.com/repos/acme/demo/contents/rules?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "file",
                        "path": "rules/rule.md",
                        "download_url": "https://download.example/rule.md",
                        "size": 12,
                    }
                ],
            ),
            "https://download.example/rule.md": FakeResponse(200, body=b"# Rule\n"),
            "https://api.github.com/repos/acme/demo/contents/knowledge?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "file",
                        "path": "knowledge/framework.md",
                        "download_url": "https://download.example/framework.md",
                        "size": 12,
                    }
                ],
            ),
            "https://download.example/framework.md": FakeResponse(200, body=b"# Framework\n"),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    assert stats["bundled_files"] == 3
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["archive_mode"] == "directory"
    assert metadata["bundled_files"] == [
        "knowledge/framework.md",
        "references/guide.md",
        "rules/rule.md",
    ]
    assert (skill_dir / "knowledge" / "framework.md").read_text(encoding="utf-8") == "# Framework\n"
    assert (skill_dir / "references" / "guide.md").read_text(encoding="utf-8") == "# Guide\n"
    assert (skill_dir / "rules" / "rule.md").read_text(encoding="utf-8") == "# Rule\n"


def test_bundled_collection_skips_github_submodule_entries(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure_report.json"
    manifest_path = tmp_path / "manifest.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\ndescription: Demo skill with a submodule path.\n---\n# Demo\n"
                ),
            ),
            "https://api.github.com/repos/acme/demo/contents?ref=main": FakeResponse(
                200,
                json_payload=[
                    {"type": "file", "path": "SKILL.md", "size": 80},
                    {"type": "dir", "path": "scripts", "size": 0},
                ],
            ),
            "https://api.github.com/repos/acme/demo/contents/scripts?ref=main": FakeResponse(
                200,
                json_payload=[
                    {
                        "type": "file",
                        "path": "scripts/tool.py",
                        "size": 0,
                        "download_url": None,
                        "submodule_git_url": "https://github.com/acme/tool.git",
                    }
                ],
            ),
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            manifest_path=manifest_path,
            failure_report_path=failure_report_path,
        )
    )

    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["archive_mode"] == "skill-md"
    assert metadata["bundled_files"] == []
    assert not (skill_dir / "scripts" / "tool.py").exists()


def test_required_bundle_rejects_unapproved_redistribution_before_listing(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    failure_report_path = tmp_path / "failure.json"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                        "license": "NOASSERTION",
                        "distribution": "restricted",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/SKILL.md": FakeResponse(
                200,
                text=(
                    "---\nname: demo\ndescription: Restricted required bundle.\n---\n"
                    "# Demo\nRun scripts/tool.py.\n"
                ),
            )
        },
    )

    stats = asyncio.run(
        module.download_skills(
            registry_path,
            output_dir,
            failure_report_path=failure_report_path,
            manifest_path=None,
        )
    )

    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    report = json.loads(failure_report_path.read_text(encoding="utf-8"))
    assert report["failure_reasons"]["asset_redistribution_not_approved"] == 1
    assert not list(output_dir.rglob("SKILL.md"))


def test_optional_unapproved_bundle_is_not_redistributed(tmp_path, monkeypatch):
    module = load_module()
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "skills"
    registry_path.write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "name": "demo",
                        "repo": "acme/demo",
                        "path": "SKILL.md",
                        "category": "development",
                        "license": "NOASSERTION",
                        "distribution": "restricted",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    install_fake_aiohttp(
        monkeypatch,
        {
            "https://raw.githubusercontent.com/acme/demo/main/SKILL.md": FakeResponse(
                200,
                text="---\nname: demo\ndescription: Restricted standalone skill.\n---\n# Demo\n",
            ),
            "https://api.github.com/repos/acme/demo/contents?ref=main": FakeResponse(
                200,
                json_payload=[
                    {"type": "file", "path": "SKILL.md", "size": 80},
                    {
                        "type": "file",
                        "path": "setup.py",
                        "size": 8,
                        "download_url": "https://download.example/setup.py",
                    },
                ],
            ),
            "https://download.example/setup.py": FakeResponse(200, body=b"print(1)"),
        },
    )

    stats = asyncio.run(
        module.download_skills(registry_path, output_dir, manifest_path=None)
    )

    assert stats["downloaded"] == 1
    skill_dir = next(output_dir.glob("development/*"))
    metadata = json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["archive_mode"] == "skill-md"
    assert metadata["bundled_files"] == []
    assert not (skill_dir / "setup.py").exists()


def test_select_shard_skills_is_deterministic():
    module = load_module()
    skills = [
        {"repo": "acme/repo1", "path": "skills/a", "name": "a", "category": "dev"},
        {"repo": "acme/repo2", "path": "skills/b", "name": "b", "category": "dev"},
        {"repo": "acme/repo3", "path": "skills/c", "name": "c", "category": "dev"},
        {"repo": "acme/repo4", "path": "skills/d", "name": "d", "category": "dev"},
    ]
    first = module.select_shard_skills(skills, shard_count=3, shard_index=1)
    second = module.select_shard_skills(skills, shard_count=3, shard_index=1)
    assert first == second


@pytest.mark.parametrize("pinned", [False, True])
@pytest.mark.parametrize("license_case", [
    "root", "local", "missing", "empty", "unavailable", "symlink", "empty_response", "omitted_source",
])
def test_gpl_archive_keeps_reference_and_license_or_fails_closed(tmp_path, monkeypatch, pinned, license_case):
    module = load_module()
    repo = "acme/gpl-skill"
    source_dir = ".claude/skills/demo"
    source_path = f"{source_dir}/SKILL.md"
    reference_path = f"{source_dir}/references/guide.md"
    license_path = f"{source_dir}/LICENSE.md" if license_case == "local" else "LICENSE"
    sha = "a" * 40
    ref = sha if pinned else "main"
    skill_body = (
        b"---\nname: demo\ndescription: " + b"GPL source description. " * 30
        + b"\n---\n# Demo\nRead references/guide.md.\n"
    )
    reference_body = b"# Guide\nKeep the original source and its attribution.\n"
    license_body = b"" if license_case == "empty" else b"GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
    files = {source_path: skill_body, reference_path: reference_body}
    if license_case != "missing":
        files[license_path] = license_body
    if license_case == "local":
        files["LICENSE"] = b"Different repository-level terms; do not override the skill license.\n"
    if license_case == "omitted_source":
        files[f"{source_dir}/lib/helper.py"] = b"print('Required GPL source')\n"
    tree = [git_blob_entry(path, body) for path, body in files.items()]
    if license_case == "symlink":
        next(entry for entry in tree if entry["path"] == license_path)["mode"] = "120000"
    routes = exact_repo_routes(repo, "main", sha, tree) if pinned else {}
    raw_base = f"https://raw.githubusercontent.com/{repo}/{ref}"
    for path, body in files.items():
        routes[f"{raw_base}/{path}"] = FakeResponse(200, body=body, text=body.decode())
    if license_case == "unavailable":
        routes[f"{raw_base}/{license_path}"] = FakeResponse(404)
    if license_case == "empty_response":
        routes[f"{raw_base}/{license_path}"] = FakeResponse(200, body=b"")
    if not pinned:
        for directory in ("", source_dir, f"{source_dir}/references"):
            contents = [
                {"type": "symlink" if license_case == "symlink" and path == license_path else "file",
                 "path": path, "size": len(body), "download_url": f"{raw_base}/{path}"}
                for path, body in files.items()
                if path.rpartition("/")[0] == directory
            ]
            if directory == source_dir:
                contents.append({"type": "dir", "path": f"{source_dir}/references"})
                if license_case == "omitted_source":
                    contents.append({"type": "dir", "path": f"{source_dir}/lib"})
            suffix = f"/{directory}" if directory else ""
            routes[f"https://api.github.com/repos/{repo}/contents{suffix}?ref=main"] = FakeResponse(200, json_payload=contents)
    install_fake_aiohttp(monkeypatch, routes)
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"skills": [{
        "name": "demo", "repo": repo, "path": source_path, "category": "development",
        "description": "Short catalog summary must not replace upstream GPL source text.",
        "github_branch": "main", "license": "GPL-3.0", "distribution": "restricted",
        "permission_note": "Separate source work, redistributed under upstream GPL-3.0 terms.",
    }]}))
    output_dir = tmp_path / "skills"
    stats = asyncio.run(module.download_skills(
        registry_path, output_dir, manifest_path=None, cleanup_ci_untracked=False,
        exact_paths_only=pinned, pin_commit_sha=pinned,
    ))
    if license_case not in {"root", "local"}:
        assert stats["downloaded"] == 0
        assert stats["failed"] == 1
        assert not list(output_dir.rglob("SKILL.md"))
        assert not list(output_dir.rglob("metadata.json"))
        return
    assert stats["downloaded"] == 1
    assert stats["failed"] == 0
    archive = next(output_dir.glob("development/*"))
    assert (archive / "SKILL.md").read_bytes() == skill_body
    assert (archive / "references/guide.md").read_bytes() == reference_body
    license_filename = license_path.rsplit("/", 1)[-1]
    assert (archive / license_filename).read_bytes() == license_body
    if license_case == "local":
        assert not (archive / "LICENSE").exists()
    metadata = json.loads((archive / "metadata.json").read_text())
    assert metadata["license"] == "GPL-3.0"
    assert metadata["distribution"] == "restricted"
    assert metadata["bundled_files"] == [license_filename, "references/guide.md"]
    assert metadata["license_source_paths"] == {license_filename: license_path}
    if pinned:
        assert metadata["github_commit_sha"] == sha
        assert metadata["bundled_file_blobs"] == {
            license_filename: git_blob_sha(license_body), "references/guide.md": git_blob_sha(reference_body),
        }


def test_select_shard_skills_partition_has_no_overlap():
    module = load_module()
    skills = [
        {"repo": f"acme/repo{i}", "path": f"skills/{i}", "name": f"s{i}", "category": "dev"}
        for i in range(15)
    ]
    shard_count = 4
    buckets = []
    for idx in range(shard_count):
        bucket = module.select_shard_skills(skills, shard_count=shard_count, shard_index=idx)
        keys = {module.skill_key(item) for item in bucket}
        buckets.append(keys)

    combined = set().union(*buckets)
    original = {module.skill_key(item) for item in skills}

    assert combined == original
    for i in range(shard_count):
        for j in range(i + 1, shard_count):
            assert buckets[i].isdisjoint(buckets[j])


def test_filter_pending_skills_prefilters_no_repo_and_cooldown():
    module = load_module()
    now = module.utc_now()
    valid = {"repo": "acme/ok", "path": "skills/ok", "name": "ok", "category": "dev"}
    missing_repo = {"repo": "", "path": "skills/missing", "name": "missing", "category": "dev"}
    cooldown = {"repo": "acme/cool", "path": "skills/cool", "name": "cool", "category": "dev"}

    negative_cache = {
        module.skill_key(cooldown): {
            "reason": "not_found",
            "cooldown_until": module.to_utc_iso(now + timedelta(hours=24)),
        }
    }

    filtered, skipped, skipped_rows = module.filter_pending_skills(
        [valid, missing_repo, cooldown],
        existing=set(),
        negative_cache=negative_cache,
        now_utc=now,
    )

    assert filtered == [valid]
    assert skipped["no_repo"] == 1
    assert skipped["cooldown_not_found"] == 1
    reasons = [reason for _, reason in skipped_rows]
    assert "no_repo_prefilter" in reasons
    assert "cooldown_not_found" in reasons


def test_filter_pending_skills_skips_existing_root_skill():
    module = load_module()
    source = {
        "repo": "acme/root-skill",
        "path": "SKILL.md",
        "name": "root-skill",
        "category": "development",
    }
    archived = {**source, "path": "SKILL.md"}

    filtered, skipped, skipped_rows = module.filter_pending_skills(
        [source],
        existing={module.skill_key(archived)},
        negative_cache={},
        now_utc=module.utc_now(),
    )

    assert filtered == []
    assert skipped == {"existing": 1, "no_repo": 0, "cooldown_not_found": 0}
    assert skipped_rows == []


@pytest.mark.parametrize("path", ["SKILL.md", "skill.md", "."])
@pytest.mark.parametrize("source_name", ["", "renamed"])
def test_download_skips_existing_root_archive_when_display_name_changes(
    tmp_path, monkeypatch, path, source_name
):
    module = load_module()
    output_dir = tmp_path / "skills"
    archive = output_dir / "development" / "unknown-acme-demo"
    archive.mkdir(parents=True)
    metadata = {
        "repo": "acme/demo",
        "path": "SKILL.md",
        "name": "unknown",
        "category": "development",
        "archive_mode": "directory",
        "bundled_files": ["LICENSE", "README.md"],
        "github_commit_sha": "a" * 40,
    }
    (archive / "metadata.json").write_text(json.dumps(metadata))
    (archive / "SKILL.md").write_text("---\nname: demo\ndescription: Demo.\n---\n# Demo\n")
    (archive / "LICENSE").write_text("MIT license notice")
    (archive / "README.md").write_text("Supporting documentation")
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"skills": [{
        "repo": "acme/demo", "path": path, "name": source_name,
        "category": "development",
    }]}))

    def unexpected_session(*args, **kwargs):
        pytest.fail("Existing root archive must be skipped before any network session")

    monkeypatch.setitem(sys.modules, "aiohttp", types.SimpleNamespace(
        TCPConnector=lambda *args, **kwargs: object(),
        ClientTimeout=lambda *args, **kwargs: object(),
        ClientSession=unexpected_session,
        ClientError=OSError,
    ))
    stats = asyncio.run(module.download_skills(
        registry_path, output_dir, github_token="", pin_commit_sha=True,
    ))

    assert stats["pending_before_shard"] == 0
    assert stats["total"] == 1
    assert stats["downloaded"] == stats["failed"] == 0
    assert {p.name: p.read_bytes() for p in archive.iterdir()} == before


def test_skill_key_preserves_pathless_names_and_nonroot_source_paths():
    module = load_module()
    assert module.skill_key({"repo": "acme/demo", "name": "nested"}) == "acme/demo:name:nested"
    assert module.skill_key({"repo": "acme/demo", "name": "other"}) == "acme/demo:name:other"
    assert module.skill_key({"repo": "acme/demo", "path": "skills/one/SKILL.md"}) != (
        module.skill_key({"repo": "acme/demo", "path": "skills/two/SKILL.md"})
    )
    assert module.skill_key({"name": "nested", "category": "development"}) == (
        "development:nested"
    )


def test_filter_pending_skills_keeps_pathless_skill_with_different_root_name():
    module = load_module()
    archived = {
        "repo": "acme/multi",
        "name": "root-skill",
        "path": "SKILL.md",
        "category": "development",
    }
    pending = {
        "repo": "acme/multi",
        "name": "nested-skill",
        "category": "development",
    }

    filtered, skipped, skipped_rows = module.filter_pending_skills(
        [pending],
        existing={module.skill_key(archived)},
        negative_cache={},
        now_utc=module.utc_now(),
    )

    assert filtered == [pending]
    assert skipped == {"existing": 0, "no_repo": 0, "cooldown_not_found": 0}
    assert skipped_rows == []


def test_sync_pipeline_category_sanitization_does_not_use_legacy_aliases():
    module = load_support_module()
    assert module.sanitize_category("dev") == "dev"
    assert module.sanitize_category("Engineering") == "engineering"
    assert module.skill_key({"name": "demo", "category": "dev"}) == "dev:demo"


def test_negative_cache_helpers_prune_and_cooldown():
    module = load_module()
    now = module.utc_now()
    stale = module.to_utc_iso(now - timedelta(days=40))
    future = module.to_utc_iso(now + timedelta(days=1))
    cache = {
        "bad": "x",
        "stale": {"reason": "not_found", "cooldown_until": stale},
        "active": {"reason": "not_found", "cooldown_until": future},
    }

    removed = module.prune_negative_cache(cache, now)
    assert removed == 2
    assert "active" in cache
    assert module.is_negative_cache_active(cache["active"], now) is True
    assert module.not_found_cooldown_hours(1) == 24
    assert module.not_found_cooldown_hours(2) == 72
    assert module.not_found_cooldown_hours(5) == 168


def test_main_exits_when_fail_on_empty_download_is_enabled(monkeypatch):
    module = load_module()

    async def fake_download_skills(*args, **kwargs):
        return {"downloaded": 0, "failed": 2, "skipped": 0, "total": 0}

    monkeypatch.setattr(module, "download_skills", fake_download_skills)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_and_download.py", "--download-only", "--fail-on-empty-download"],
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code == 1


def test_main_allows_partial_success_with_fail_on_empty_download(monkeypatch):
    module = load_module()

    async def fake_download_skills(*args, **kwargs):
        return {"downloaded": 1, "failed": 2, "total": 1}

    monkeypatch.setattr(module, "download_skills", fake_download_skills)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_and_download.py", "--download-only", "--fail-on-empty-download"],
    )

    module.main()


def test_main_allows_existing_archive_when_all_pending_fail(monkeypatch):
    module = load_module()

    async def fake_download_skills(*args, **kwargs):
        return {"downloaded": 0, "failed": 2, "skipped": 100, "total": 100}

    monkeypatch.setattr(module, "download_skills", fake_download_skills)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_and_download.py", "--download-only", "--fail-on-empty-download"],
    )

    module.main()


def test_main_passes_skip_ci_untracked_cleanup(monkeypatch):
    module = load_module()
    captured = {}

    async def fake_download_skills(*args, **kwargs):
        captured.update(kwargs)
        return {"downloaded": 0, "failed": 0, "skipped": 0, "total": 0}

    monkeypatch.setattr(module, "download_skills", fake_download_skills)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_and_download.py", "--download-only", "--skip-ci-untracked-cleanup"],
    )

    module.main()

    assert captured["cleanup_ci_untracked"] is False


def test_main_enables_pin_commit_sha_on_download(monkeypatch):
    module = load_module()
    captured = {}

    async def fake_download_skills(*args, **kwargs):
        captured.update(kwargs)
        return {"downloaded": 0, "failed": 0, "skipped": 0, "total": 0}

    monkeypatch.setattr(module, "download_skills", fake_download_skills)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_and_download.py", "--download-only"],
    )

    module.main()

    assert captured["pin_commit_sha"] is True


def test_main_cleanup_only_runs_ci_archive_cleanup(monkeypatch):
    module = load_module()
    captured = {}

    def fake_cleanup(output_dir):
        captured["output_dir"] = output_dir
        return 2

    monkeypatch.setattr(module, "remove_ci_untracked_archive_files", fake_cleanup)
    monkeypatch.setattr(
        sys,
        "argv",
        ["sync_and_download.py", "--cleanup-ci-untracked-archive-files-only"],
    )

    module.main()

    assert captured["output_dir"].name == "skills"


@pytest.mark.parametrize(
    ("resolved_path", "existing_repo", "existing_path", "pin_commit_sha", "expected_skipped"),
    [
        ("SKILL.md", "acme/demo", "SKILL.md", True, True),
        ("skills/new/SKILL.md", "acme/demo", "SKILL.md", True, False),
        ("SKILL.md", "acme/other", "SKILL.md", True, False),
        ("SKILL.md", "acme/demo", "SKILL.md", False, False),
    ],
)
def test_pathless_download_reuses_only_verified_resolved_archive(
    tmp_path, monkeypatch, resolved_path, existing_repo, existing_path,
    pin_commit_sha, expected_skipped,
):
    module = load_module()
    repo, sha = "acme/demo", "c" * 40
    body = b"---\nname: current-display-name\ndescription: Current upstream skill.\n---\n# Demo\n"
    archive = tmp_path / "skills/development/existing"
    archive.mkdir(parents=True)
    (archive / "SKILL.md").write_bytes(b"# Existing archived body\n")
    (archive / "LICENSE").write_bytes(b"Existing support bytes\n")
    (archive / "metadata.json").write_text(json.dumps({
        "repo": existing_repo, "path": existing_path, "name": "different-old-name",
        "archive_mode": "directory", "bundled_files": ["LICENSE"],
    }))
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    skill = {"repo": repo, "path": "", "name": "current-display-name", "category": "development"}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"skills": [skill]}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {load_support_module().build_manifest_key(
        repo, "", skill["name"], "development"
    ): {"repo": repo, "branch": "main", "relative_path": resolved_path}}}))
    routes = exact_repo_routes(repo, "main", sha, [git_blob_entry(resolved_path, body)])
    routes[f"https://raw.githubusercontent.com/{repo}/{sha if pin_commit_sha else 'main'}/{resolved_path}"] = FakeResponse(200, body=body)
    install_fake_aiohttp(monkeypatch, routes)
    observations = tmp_path / "observations.jsonl"
    stats = asyncio.run(module.download_skills(
        registry, tmp_path / "skills", manifest_path=manifest,
        pin_commit_sha=pin_commit_sha, observations_output_path=observations,
    ))
    rows = [json.loads(line) for line in observations.read_text().splitlines()]
    assert stats["downloaded"] == int(not expected_skipped)
    assert stats["failed"] == 0
    assert stats["skipped"] == 1 + int(expected_skipped)
    assert stats["url_attempts"] == 1
    if expected_skipped:
        assert {p.name: p.read_bytes() for p in archive.iterdir()} == before
        assert rows[0]["outcome"] == "skipped"
        assert rows[0]["failure_reason"] == "existing"
        assert rows[0]["resolved_relative_path"] == resolved_path
        assert rows[0]["resolved_commit_sha"] == sha
        assert rows[0]["attempts"] == 1
    else:
        assert rows[0]["outcome"] == "downloaded"


@pytest.mark.parametrize("failure", ["403", "404", "truncated", "invalid_content", "blob_mismatch"])
def test_pathless_resolved_existing_skip_preserves_verification_failures(
    tmp_path, monkeypatch, failure,
):
    module = load_module()
    repo, sha = "acme/demo", "d" * 40
    body = b"---\nname: demo\ndescription: Current upstream skill.\n---\n# Demo\n"
    if failure == "invalid_content":
        body = b"---\nname: demo\n---\n"
    archive = tmp_path / "skills/development/existing"
    archive.mkdir(parents=True)
    (archive / "SKILL.md").write_bytes(b"# Original archive\n")
    (archive / "metadata.json").write_text(json.dumps({"repo": repo, "path": "SKILL.md"}))
    before = {p.name: p.read_bytes() for p in archive.iterdir()}
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"skills": [{
        "repo": repo, "path": "", "name": "demo", "category": "development",
    }]}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"entries": {load_support_module().build_manifest_key(
        repo, "", "demo", "development"
    ): {"repo": repo, "branch": "main", "relative_path": "SKILL.md"}}}))
    routes = exact_repo_routes(repo, "main", sha, [git_blob_entry("SKILL.md", body)])
    if failure == "truncated":
        routes[f"https://api.github.com/repos/{repo}/git/trees/{sha}?recursive=1"] = FakeResponse(
            200, json_payload={"truncated": True, "tree": [git_blob_entry("SKILL.md", body)]}
        )
    routes[f"https://raw.githubusercontent.com/{repo}/{sha}/SKILL.md"] = FakeResponse(
        int(failure) if failure in {"403", "404"} else 200,
        body=b"changed unverified bytes" if failure == "blob_mismatch" else body,
    )
    install_fake_aiohttp(monkeypatch, routes)
    observations = tmp_path / "observations.jsonl"
    stats = asyncio.run(module.download_skills(
        registry, tmp_path / "skills", manifest_path=manifest,
        pin_commit_sha=True, observations_output_path=observations,
    ))
    assert stats["downloaded"] == 0
    assert stats["failed"] == 1
    assert stats["skipped"] == 1
    assert {p.name: p.read_bytes() for p in archive.iterdir()} == before
    assert all(json.loads(line)["outcome"] != "skipped" for line in observations.read_text().splitlines())
