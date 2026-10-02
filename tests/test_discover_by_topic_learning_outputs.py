import importlib.util
import sys
from pathlib import Path

import pytest


def load_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "discover_by_topic.py"
    scripts_dir = str(module_path.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("discover_by_topic_module", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.headers = {}
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        return self.response


def test_write_candidates_jsonl_emits_repo_and_path_rows(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)

    discovery.repo_candidates = {
        "acme/demo": {
            "candidate_level": "repo",
            "repo": "acme/demo",
            "topics": ["claude-skills"],
            "code_queries": ["filename:SKILL.md"],
            "topic_hits": 1,
            "code_hits": 1,
            "max_stars": 42,
            "selected_for_scan": False,
            "downloaded_skills": 2,
        }
    }
    discovery.path_candidates = {
        "acme/demo:skills/demo/SKILL.md": {
            "candidate_level": "path",
            "repo": "acme/demo",
            "path": "skills/demo/SKILL.md",
            "code_queries": ["filename:SKILL.md"],
            "discovered_via_code_search": True,
            "discovered_via_repo_scan": True,
            "downloaded": True,
        }
    }

    output = tmp_path / "discovery_candidates.jsonl"
    written = discovery._write_candidates_jsonl(
        str(output),
        "2026-04-12T00:00:00Z",
        ["acme/demo"],
    )

    lines = output.read_text(encoding="utf-8").strip().splitlines()
    assert written == 2
    assert len(lines) == 2
    assert '"candidate_key": "repo:acme/demo"' in lines[0]
    assert '"selected_for_scan": true' in lines[0]
    assert '"candidate_key": "path:acme/demo:skills/demo/SKILL.md"' in lines[1]


def test_update_priors_accumulates_stats_across_runs(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)

    discovery.repo_candidates = {
        "acme/demo": {
            "candidate_level": "repo",
            "repo": "acme/demo",
            "topics": ["claude-skills"],
            "code_queries": ["filename:SKILL.md"],
            "topic_hits": 2,
            "code_hits": 1,
            "max_stars": 10,
            "selected_for_scan": True,
            "downloaded_skills": 1,
        }
    }
    discovery.topic_stats["claude-skills"]["repo_hits"] = 1
    discovery.topic_stats["claude-skills"]["repo_selected"] = 1
    discovery.topic_stats["claude-skills"]["downloaded_skills"] = 1
    discovery.code_query_stats["filename:SKILL.md"]["repo_hits"] = 1
    discovery.code_query_stats["filename:SKILL.md"]["path_hits"] = 1
    discovery.code_query_stats["filename:SKILL.md"]["downloaded_skills"] = 1

    priors_path = tmp_path / "discovery_priors.json"
    discovery._update_priors(
        str(priors_path),
        "2026-04-12T00:00:00Z",
        ["acme/demo"],
    )
    discovery._update_priors(
        str(priors_path),
        "2026-04-13T00:00:00Z",
        ["acme/demo"],
    )

    priors = module.json.loads(priors_path.read_text(encoding="utf-8"))
    repo = priors["repo_priors"]["acme/demo"]
    assert priors["runs"] == 2
    assert repo["seen_runs"] == 2
    assert repo["selected_runs"] == 2
    assert repo["downloaded_skills"] == 2
    assert priors["topic_yield"]["claude-skills"]["downloaded_skills"] == 2


def test_download_skill_rejects_security_listed_path(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(FakeResponse(200, "---\nname: demo\n---\n# Demo\n"))

    downloaded = discovery.download_skill(
        "nowork-studio/toprank",
        "openclaw/skills/toprank/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is False
    assert not list(tmp_path.rglob("SKILL.md"))


def test_download_skill_removes_security_scan_failure(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(
        FakeResponse(
            200,
            (
                "---\nname: unsafe-demo\n"
                "description: Demo skill with unsafe shell execution.\n---\n"
                "# Unsafe Demo\n"
                "```python\n"
                "import subprocess\n"
                "subprocess.run('echo unsafe', shell=True)\n"
                "```\n"
            ),
        )
    )

    downloaded = discovery.download_skill(
        "acme/unsafe-demo",
        "skills/unsafe-demo/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is False
    assert not list(tmp_path.rglob("SKILL.md"))


def test_download_skill_preserves_existing_archive_on_security_scan_failure(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(
        FakeResponse(
            200,
            (
                "---\nname: unsafe-demo\n"
                "description: Demo skill with unsafe shell execution.\n---\n"
                "# Unsafe Demo\n"
                "```python\n"
                "import subprocess\n"
                "subprocess.run('echo unsafe', shell=True)\n"
                "```\n"
            ),
        )
    )

    existing_dir = tmp_path / "skills" / "other" / "unsafe-demo"
    existing_dir.mkdir(parents=True)
    existing_skill = (
        "---\nname: unsafe-demo\n"
        "description: Previously archived safe version.\n---\n"
        "# Existing Demo\n"
    )
    existing_metadata = module.json.dumps(
        {
            "name": "unsafe-demo",
            "repo": "acme/unsafe-demo",
            "path": "skills/unsafe-demo/SKILL.md",
            "category": "other",
            "source": "github.com/acme/unsafe-demo",
            "dir_name": "unsafe-demo",
        },
        indent=2,
    )
    (existing_dir / "SKILL.md").write_text(existing_skill, encoding="utf-8")
    (existing_dir / "metadata.json").write_text(existing_metadata, encoding="utf-8")

    downloaded = discovery.download_skill(
        "acme/unsafe-demo",
        "skills/unsafe-demo/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is False
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8") == existing_skill
    assert (existing_dir / "metadata.json").read_text(encoding="utf-8") == existing_metadata


def test_download_skill_classifies_from_skill_md_first(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(
        FakeResponse(
            200,
            (
                "---\n"
                "name: docker-deployer\n"
                "description: Deploy Docker Kubernetes CI CD infrastructure automation.\n"
                "tags: [docker, kubernetes, ci]\n"
                "---\n"
                "# Docker Deployer\n\n"
                "Builds release images and deploys services to Kubernetes clusters.\n"
            ),
        )
    )

    downloaded = discovery.download_skill(
        "acme/docker-deployer",
        "skills/docker-deployer/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is True
    skill_dir = tmp_path / "skills" / "devops" / "docker-deployer"
    metadata = module.json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert (skill_dir / "SKILL.md").exists()
    assert not (tmp_path / "skills" / "other" / "docker-deployer").exists()
    assert metadata["category"] == "devops"
    assert metadata["description"] == "Deploy Docker Kubernetes CI CD infrastructure automation."
    assert metadata["classification"]["status"] == "classified"
    assert metadata["classification"]["confidence"] == "high"
    assert metadata["classification"]["method"] == "taxonomy_keyword_v1"
    assert metadata["classification"]["semantic_sources"]["description"] == "frontmatter"


def test_download_skill_skips_source_already_archived_in_another_category(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(
        FakeResponse(
            200,
            (
                "---\n"
                "name: docker-deployer\n"
                "description: Deploy Docker Kubernetes CI CD infrastructure automation.\n"
                "---\n"
                "# Docker Deployer\n"
            ),
        )
    )

    existing_dir = tmp_path / "skills" / "development" / "docker-deployer"
    existing_dir.mkdir(parents=True)
    existing_skill = "---\nname: docker-deployer\n---\n# Existing Docker Deployer\n"
    existing_metadata = module.json.dumps(
        {
            "name": "docker-deployer",
            "repo": "acme/docker-deployer",
            "path": "skills/docker-deployer",
            "category": "development",
            "source": "github.com/acme/docker-deployer",
            "dir_name": "docker-deployer",
        },
        indent=2,
    )
    (existing_dir / "SKILL.md").write_text(existing_skill, encoding="utf-8")
    (existing_dir / "metadata.json").write_text(existing_metadata, encoding="utf-8")

    downloaded = discovery.download_skill(
        "acme/docker-deployer",
        "skills/docker-deployer/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is False
    assert discovery.session.calls == 0
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8") == existing_skill
    assert not (tmp_path / "skills" / "other" / "docker-deployer").exists()
    assert not (tmp_path / "skills" / "devops" / "docker-deployer").exists()


def test_download_skill_falls_back_to_other_with_audit_reason(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(
        FakeResponse(
            200,
            "---\nname: quiet-helper\n---\n# Quiet Helper\n\nShort note.\n",
        )
    )

    downloaded = discovery.download_skill(
        "acme/quiet-helper",
        "skills/quiet-helper/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is True
    skill_dir = tmp_path / "skills" / "other" / "quiet-helper"
    metadata = module.json.loads((skill_dir / "metadata.json").read_text(encoding="utf-8"))
    assert (skill_dir / "SKILL.md").exists()
    assert metadata["category"] == "other"
    assert metadata["classification"]["status"] == "unclassified"
    assert metadata["classification"]["confidence"] == "low"
    assert metadata["classification"]["reason"] == (
        "no taxonomy keywords matched SKILL.md semantic text"
    )


def test_download_skill_scans_existing_bundled_files_before_refresh(tmp_path):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    discovery.session = FakeSession(
        FakeResponse(
            200,
            (
                "---\nname: bundled-demo\n"
                "description: Safe refreshed skill content.\n---\n"
                "# Refreshed Demo\n"
            ),
        )
    )

    existing_dir = tmp_path / "skills" / "other" / "bundled-demo"
    existing_dir.mkdir(parents=True)
    (existing_dir / "scripts").mkdir()
    existing_skill = (
        "---\nname: bundled-demo\n"
        "description: Previously archived version.\n---\n"
        "# Existing Demo\n"
    )
    existing_metadata = module.json.dumps(
        {
            "name": "bundled-demo",
            "repo": "acme/bundled-demo",
            "path": "skills/bundled-demo/SKILL.md",
            "category": "other",
            "source": "github.com/acme/bundled-demo",
            "dir_name": "bundled-demo",
        },
        indent=2,
    )
    (existing_dir / "SKILL.md").write_text(existing_skill, encoding="utf-8")
    (existing_dir / "metadata.json").write_text(existing_metadata, encoding="utf-8")
    (existing_dir / "scripts" / "tool.py").write_text("eval('unsafe')\n", encoding="utf-8")

    downloaded = discovery.download_skill(
        "acme/bundled-demo",
        "skills/bundled-demo/SKILL.md",
        tmp_path / "skills",
    )

    assert downloaded is False
    assert (existing_dir / "SKILL.md").read_text(encoding="utf-8") == existing_skill
    assert (existing_dir / "metadata.json").read_text(encoding="utf-8") == existing_metadata
    assert (existing_dir / "scripts" / "tool.py").exists()


def batch_paths(tmp_path):
    return {
        "output_dir": tmp_path / "skills",
        "output_json": tmp_path / "discovered.json",
        "candidates_output": tmp_path / "candidates.jsonl",
        "priors_output": tmp_path / "priors.json",
        "progress_path": tmp_path / "progress.json",
        "time_budget_seconds": 2,
    }


def fake_inventory(monkeypatch, module, repos):
    calls = []

    def topics(discovery):
        calls.append("topics")
        discovery.discovered_repos.update(repos)

    def code(discovery):
        calls.append("code")

    monkeypatch.setattr(module.GitHubTopicDiscovery, "discover_by_topics", topics)
    monkeypatch.setattr(module.GitHubTopicDiscovery, "discover_by_code_search", code)
    return calls


def test_full_cycle_resumes_snapshot_and_only_completes_after_last_repo(tmp_path, monkeypatch):
    module = load_module()
    clock = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    repos = [f"acme/repo-{i}" for i in range(5)]
    searches = fake_inventory(monkeypatch, module, repos)
    scanned = []

    def scan(discovery, repo):
        scanned.append(repo)
        clock[0] += 1
        return []

    monkeypatch.setattr(module.GitHubTopicDiscovery, "get_skill_files_from_repo", scan)
    paths = batch_paths(tmp_path)
    for expected in (2, 4, 5):
        module.GitHubTopicDiscovery(request_delay=0).run(**paths)
        state = module.json.loads(paths["progress_path"].read_text())
        assert state["next_repo"] == expected
        assert bool(state["completed_at"]) == (expected == 5)
        assert state["repos"] == repos
    assert scanned == repos
    assert searches == ["topics", "code"]

    # A new cycle is allowed only after the previous inventory finished.
    module.GitHubTopicDiscovery(request_delay=0).run(**paths)
    assert searches == ["topics", "code", "topics", "code"]
    assert scanned[5:] == repos[:2]


def test_budget_mid_repo_keeps_cursor_and_resumes_unarchived_files(tmp_path, monkeypatch):
    module = load_module()
    clock = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    fake_inventory(monkeypatch, module, ["acme/demo"])
    monkeypatch.setattr(module.GitHubTopicDiscovery, "get_skill_files_from_repo",
                        lambda self, repo: [{"path": f"skill-{i}/SKILL.md"} for i in range(3)])
    downloaded = []

    def download(session, url, **kwargs):
        downloaded.append(url)
        clock[0] += 1
        return FakeResponse(200, "---\nname: demo\ndescription: A safe demo skill.\n---\n# Demo\n")

    monkeypatch.setattr(module.requests.Session, "get", download)
    paths = batch_paths(tmp_path)
    module.GitHubTopicDiscovery(request_delay=0).run(**paths)
    assert module.json.loads(paths["progress_path"].read_text())["next_repo"] == 0
    module.GitHubTopicDiscovery(request_delay=0).run(**paths)
    state = module.json.loads(paths["progress_path"].read_text())
    assert state["next_repo"] == 1
    assert state["completed_at"]
    assert downloaded == [f"{module.GITHUB_RAW}/acme/demo/main/skill-{i}/SKILL.md" for i in range(3)]
    assert len(list(paths["output_dir"].rglob("SKILL.md"))) == 3


def test_failed_batch_does_not_advance_durable_progress(tmp_path, monkeypatch):
    module = load_module()
    paths = batch_paths(tmp_path)
    original = '{"repos": ["acme/first", "acme/failing"], "next_repo": 0}'
    paths["progress_path"].write_text(original)

    def scan(discovery, repo):
        if repo == "acme/failing":
            raise module.requests.ConnectionError("upstream failed")
        return []

    monkeypatch.setattr(module.GitHubTopicDiscovery, "get_skill_files_from_repo", scan)
    with pytest.raises(module.requests.ConnectionError):
        module.GitHubTopicDiscovery(request_delay=0).run(**paths)
    assert paths["progress_path"].read_text() == original
    assert not Path(paths["output_json"]).exists()


def test_interrupted_inventory_is_not_saved_as_full_snapshot(tmp_path, monkeypatch):
    module = load_module()
    fake_inventory(monkeypatch, module, ["acme/demo"])

    def expire(discovery):
        raise module.DiscoveryBudgetExpired()

    monkeypatch.setattr(module.GitHubTopicDiscovery, "discover_by_code_search", expire)
    paths = batch_paths(tmp_path)
    with pytest.raises(module.DiscoveryBudgetExpired):
        module.GitHubTopicDiscovery(request_delay=0).run(**paths)
    assert not paths["progress_path"].exists()


def test_resumed_batch_without_progress_fails(tmp_path, monkeypatch):
    module = load_module()
    paths = batch_paths(tmp_path)
    original = '{"repos": ["acme/demo"], "next_repo": 0}'
    paths["progress_path"].write_text(original)

    def expire(discovery, repo):
        raise module.DiscoveryBudgetExpired()

    monkeypatch.setattr(module.GitHubTopicDiscovery, "get_skill_files_from_repo", expire)
    with pytest.raises(RuntimeError, match="no progress"):
        module.GitHubTopicDiscovery(request_delay=0).run(**paths)
    assert paths["progress_path"].read_text() == original


def test_rate_limit_wait_cannot_overrun_batch_budget(monkeypatch):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.deadline = 100
    discovery.checkpointed = True
    response = FakeResponse(403)
    response.headers = {"X-RateLimit-Reset": "200"}
    monkeypatch.setattr(discovery.session, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(module.time, "monotonic", lambda: 90)
    monkeypatch.setattr(module.time, "time", lambda: 100)
    monkeypatch.setattr(module.time, "sleep", lambda _: pytest.fail("must not sleep past budget"))

    with pytest.raises(module.DiscoveryBudgetExpired):
        discovery._request("https://api.github.com/search/code")


@pytest.mark.parametrize("stage", ["request", "download"])
def test_checkpointed_network_failure_is_not_treated_as_completed(stage, tmp_path, monkeypatch):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True

    def timeout(*args, **kwargs):
        raise module.requests.Timeout("upstream timed out")

    monkeypatch.setattr(discovery.session, "get", timeout)
    with pytest.raises(module.requests.Timeout):
        if stage == "request":
            discovery.get_skill_files_from_repo("acme/demo")
        else:
            discovery.download_skill("acme/demo", "SKILL.md", tmp_path)


def test_incomplete_search_results_cannot_advance_cycle(monkeypatch):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True
    response = module.requests.Response()
    response.status_code = 200
    response._content = b'{"incomplete_results": true, "items": []}'
    monkeypatch.setattr(discovery.session, "get", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError, match="incomplete discovery results"):
        discovery._request("https://api.github.com/search/code")


@pytest.mark.parametrize("status", [404, 422, 401, 500])
def test_repo_removed_between_batches_is_explicitly_recorded(status, monkeypatch):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True
    response = module.requests.Response()
    response.status_code = status
    missing = module.requests.Response()
    missing.status_code = 404
    monkeypatch.setattr(discovery.session, "get",
                        lambda url, **kwargs: missing if "/repos/" in url else response)
    if status in (404, 422):
        assert discovery.get_skill_files_from_repo("acme/demo") == []
        assert discovery.repo_candidates["acme/demo"]["unavailable"] is True
    else:
        with pytest.raises(module.requests.HTTPError):
            discovery.get_skill_files_from_repo("acme/demo")


def test_search_validation_failure_on_existing_repo_still_fails(monkeypatch):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True
    response = module.requests.Response()
    response.status_code = 422
    existing = module.requests.Response()
    existing.status_code = 200
    existing._content = b'{"full_name": "acme/demo"}'
    monkeypatch.setattr(discovery.session, "get",
                        lambda url, **kwargs: existing if "/repos/" in url else response)
    with pytest.raises(module.requests.HTTPError):
        discovery.get_skill_files_from_repo("acme/demo")


def test_registry_repo_from_existing_snapshot_never_fetches_skill_inventory(monkeypatch):
    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0.0)
    def unexpected_request(*args, **kwargs):
        pytest.fail("Persisted registry candidates must not make inventory requests")
    monkeypatch.setattr(discovery, "_request", unexpected_request)
    assert discovery.get_skill_files_from_repo("majiayu000/claude-skill-registry") == []
    assert discovery.get_skill_files_from_repo("majiayu000/claude-skill-registry-core") == []
    assert discovery.get_skill_files_from_repo("majiayu000/claude-skill-registry-data") == []


@pytest.mark.parametrize("branch", ["main", "feature/skills"])
def test_incomplete_repo_search_reads_complete_default_branch_tree(monkeypatch, branch):
    import json

    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs.get("params")))
        response = module.requests.Response()
        response.status_code = 200
        if url.endswith("/search/code"):
            payload = {"incomplete_results": True, "items": []}
        elif "/git/trees/" in url:
            payload = {"truncated": False, "tree": [
                {"type": "blob", "path": "skills/solana-token-research/SKILL.md"},
                {"type": "blob", "path": "templates/skill/SKILL.md"},
                {"type": "blob", "path": "README.md"},
                {"type": "blob", "path": "SKILL.md.bak"},
                {"type": "tree", "path": "directory/SKILL.md"},
            ]}
        else:
            payload = {"default_branch": branch}
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(discovery.session, "get", get)
    skills = discovery.get_skill_files_from_repo("acme/demo")
    encoded_branch = branch.replace("/", "%2F")
    assert skills == [
        {"repo": "acme/demo", "path": path,
         "html_url": f"https://github.com/acme/demo/blob/{encoded_branch}/{path}", "branch": branch}
        for path in ["skills/solana-token-research/SKILL.md", "templates/skill/SKILL.md"]
    ]
    assert calls == [
        ("https://api.github.com/search/code", {"q": "filename:SKILL.md repo:acme/demo", "per_page": 100}),
        ("https://api.github.com/repos/acme/demo", None),
        (f"https://api.github.com/repos/acme/demo/git/trees/{encoded_branch}", {"recursive": "1"}),
    ]


@pytest.mark.parametrize("failure", ["truncated", "missing_flag", "missing_tree", "metadata_404", "tree_404", "tree_409", "tree_500", "tree_timeout"])
def test_incomplete_repo_search_tree_failure_cannot_complete_inventory(monkeypatch, failure):
    import json

    module = load_module()
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    discovery.checkpointed = True

    def get(url, **kwargs):
        is_tree = "/git/trees/" in url
        response = module.requests.Response()
        response.status_code = 200
        if url.endswith("/search/code"):
            payload = {"incomplete_results": True, "items": []}
        elif is_tree:
            if failure == "tree_timeout":
                raise module.requests.Timeout("tree unavailable")
            if failure.startswith("tree_"):
                response.status_code = int(failure.rsplit("_", 1)[1])
            payload = {"truncated": failure == "truncated", "tree": []}
            if failure == "missing_flag":
                payload.pop("truncated")
            elif failure == "missing_tree":
                payload.pop("tree")
        else:
            if failure == "metadata_404":
                response.status_code = 404
            payload = {"default_branch": "main"}
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(discovery.session, "get", get)
    if failure == "truncated":
        error, match = RuntimeError, "truncated tree"
    elif failure in {"missing_flag", "missing_tree"}:
        error, match = KeyError, "truncated" if failure == "missing_flag" else "tree"
    elif failure == "tree_timeout":
        error, match = module.requests.Timeout, "tree unavailable"
    else:
        error, match = module.requests.HTTPError, None
    with pytest.raises(error, match=match):
        discovery.get_skill_files_from_repo("acme/demo")


@pytest.mark.parametrize("outcome", ["success", "403", "500", "timeout", "security_rejection"])
def test_incomplete_tree_default_branch_downloads_before_completing_cycle(tmp_path, monkeypatch, outcome):
    import json

    module = load_module()
    fake_inventory(monkeypatch, module, ["acme/demo"])
    paths = batch_paths(tmp_path)
    branch = "feature/skills"
    skill_paths = ["skills/first/SKILL.md", "skills/second skill/SKILL.md"]
    calls = []
    safe = "---\nname: demo\ndescription: Safe example.\n---\n# Demo\n"
    unsafe = safe + "```python\nimport subprocess\nsubprocess.run('echo unsafe', shell=True)\n```\n"

    def get(session, url, **kwargs):
        calls.append(url)
        response = module.requests.Response()
        response.status_code = 200
        if url.endswith("/search/code"):
            payload = {"incomplete_results": True, "items": []}
        elif "/git/trees/" in url:
            payload = {"truncated": False, "tree": [{"type": "blob", "path": p} for p in skill_paths]}
        elif url.startswith(module.GITHUB_RAW):
            if "/feature%2Fskills/" not in url:
                response.status_code = 404
                response._content = b"not found"
                return response
            if outcome == "timeout":
                raise module.requests.Timeout("raw content unavailable")
            if outcome in {"403", "500"}:
                response.status_code = int(outcome)
            response._content = (unsafe if outcome == "security_rejection" else safe).encode()
            return response
        else:
            payload = {"default_branch": branch}
        response._content = json.dumps(payload).encode()
        return response

    monkeypatch.setattr(module.requests.Session, "get", get)
    discovery = module.GitHubTopicDiscovery(request_delay=0)
    if outcome in {"403", "500", "timeout"}:
        error = module.requests.Timeout if outcome == "timeout" else module.requests.HTTPError
        with pytest.raises(error):
            discovery.run(**paths)
        assert not paths["progress_path"].exists()
        assert not paths["output_json"].exists()
        assert not list(paths["output_dir"].rglob("SKILL.md"))
    else:
        discovery.run(**paths)
        state = json.loads(paths["progress_path"].read_text())
        assert state["next_repo"] == 1 and state["completed_at"]
        archived = list(paths["output_dir"].rglob("SKILL.md"))
        assert len(archived) == (2 if outcome == "success" else 0)
        if outcome == "success":
            assert all(p.read_text() == safe for p in archived)
            metadata = [json.loads(p.read_text()) for p in paths["output_dir"].rglob("metadata.json")]
            assert {m["path"] for m in metadata} == set(skill_paths)
            assert all(m["github_branch"] == branch for m in metadata)
    raw_calls = [u for u in calls if u.startswith(module.GITHUB_RAW)]
    expected = [f"{module.GITHUB_RAW}/acme/demo/feature%2Fskills/{module.quote(p)}" for p in skill_paths]
    assert raw_calls == (expected[:1] if outcome in {"403", "500", "timeout"} else expected)
