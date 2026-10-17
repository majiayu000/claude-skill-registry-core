"""Execute pin handoff shell steps with disposable Git repositories and API stubs."""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = yaml.safe_load((Path(__file__).resolve().parents[1] /
                          '.github/workflows/commit-install-pins.yml').read_text())
STEPS = {s['name']: s for s in WORKFLOW['jobs']['commit-install-pins']['steps']}


def run_step(name, directory, env):
    return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', STEPS[name]['run']],
                          cwd=directory, env={**os.environ, **env}, text=True, capture_output=True)


@pytest.fixture
def sandbox(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    gh = bin_dir / 'gh'
    gh.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
if 'actions/workflows/build-index.yml/runs?' in ' '.join(args):
    if os.environ.get('FAIL_LOOKUP'): sys.exit(1)
    print(os.environ['LATEST_RUN'])
elif 'repos/majiayu000/claude-skill-registry-data/commits/main' in args:
    if os.environ.get('FAIL_LOOKUP'): sys.exit(1)
    print('d' * 40)
elif 'repos/majiayu000/claude-skill-registry/dispatches' in args:
    payload = pathlib.Path(args[args.index('--input') + 1]).read_text()
    pathlib.Path(os.environ['RECEIPT']).write_text(payload)
    if os.environ.get('FAIL_DISPATCH'): sys.exit(1)
else:
    raise RuntimeError(args)
''')
    gh.chmod(0o755)
    return tmp_path, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                      'GITHUB_OUTPUT': str(tmp_path / 'output'),
                      'GITHUB_REPOSITORY': 'majiayu000/claude-skill-registry-core',
                      'RUNNER_TEMP': str(tmp_path), 'RECEIPT': str(tmp_path / 'receipt')}


@pytest.mark.parametrize(('latest', 'source', 'expected'), [('101', '101', 'true'), ('102', '101', 'false')])
def test_superseded_build_skips_all_artifact_and_write_steps(sandbox, latest, source, expected):
    directory, env = sandbox
    result = run_step('Reject superseded build artifacts', directory,
                      {**env, 'LATEST_RUN': latest, 'SOURCE_RUN_ID': source})
    assert result.returncode == 0, result.stderr
    assert (directory / 'output').read_text().strip() == f'current={expected}'
    assert WORKFLOW['concurrency'] == {'group': 'commit-install-pins', 'cancel-in-progress': False}
    for name, step in STEPS.items():
        if name != 'Reject superseded build artifacts':
            assert step['if'] == "steps.source.outputs.current == 'true'"


def test_freshness_lookup_failure_stops_before_mutations(sandbox):
    directory, env = sandbox
    result = run_step('Reject superseded build artifacts', directory,
                      {**env, 'SOURCE_RUN_ID': '101', 'FAIL_LOOKUP': '1'})
    assert result.returncode != 0
    assert not (directory / 'output').exists()


def test_commit_and_unchanged_cache_both_emit_actual_remote_core_sha(sandbox):
    directory, env = sandbox
    remote = directory / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True, capture_output=True)
    work = directory / 'work'
    subprocess.run(['git', 'clone', str(remote), str(work)], check=True, capture_output=True)
    def git(*args):
        return subprocess.check_output(['git', '-C', str(work), *args], text=True).strip()
    git('config', 'user.name', 'Test')
    git('config', 'user.email', 'test@example.invalid')
    git('checkout', '-b', 'main')
    (work / 'install-pins.json').write_text('{"pins": {}}')
    git('add', '.')
    git('commit', '-m', 'initial')
    git('push', '-u', 'origin', 'main')
    (work / 'install-pins.json').write_text('{"pins": {"id": {"status": "pinned"}}}')
    for _ in range(2):
        (directory / 'output').write_text('')
        result = run_step('Commit & push install pins', work, env)
        assert result.returncode == 0, result.stderr
        sha = git('rev-parse', 'HEAD')
        assert (directory / 'output').read_text().strip() == f'core_sha={sha}'
        assert subprocess.check_output(['git', '--git-dir', str(remote), 'rev-parse', 'main'], text=True).strip() == sha


@pytest.mark.parametrize('failure', ['', 'FAIL_LOOKUP', 'FAIL_DISPATCH'])
def test_dispatch_uses_exact_committed_core_and_resolved_data_and_surfaces_errors(sandbox, failure):
    directory, env = sandbox
    result = run_step('Publish refreshed install pins', directory,
                      {**env, 'CORE_SHA': 'c' * 40, **({failure: '1'} if failure else {})})
    if failure:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
    if failure == 'FAIL_LOOKUP':
        assert not (directory / 'receipt').exists()
    else:
        assert json.loads((directory / 'receipt').read_text()) == {
            'event_type': 'publish_from_core', 'client_payload': {
                'core_repo': 'majiayu000/claude-skill-registry-core', 'core_sha': 'c' * 40,
                'data_repo': 'majiayu000/claude-skill-registry-data', 'data_sha': 'd' * 40}}
