import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str) -> dict:
    completed = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr)
    return json.loads(completed.stdout)


def test_study_recorder_requires_consent_and_excludes_raw_search_text():
    result = run_node(
        r"""
const assert = require('assert');
const study = require('./docs/js/study-mode.js');
const values = new Map();
const storage = {
  getItem: key => values.get(key) || null,
  setItem: (key, value) => values.set(key, value),
  removeItem: key => values.delete(key)
};
let currentTime = 1000;
const recorder = study.createRecorder({
  storage,
  now: () => currentTime,
  isoNow: () => `time-${currentTime}`,
  makeId: () => 'participant-session'
});

assert.strictEqual(recorder.track('search_submitted', {}), false);
recorder.start('q3 cohort with spaces');
assert.strictEqual(recorder.isActive(), true);
assert.strictEqual(recorder.track('unknown_event', {}), false);
assert.strictEqual(recorder.track('search_submitted', {
  query_length_bucket: study.bucketQueryLength('private search text'),
  result_count_bucket: study.bucketResultCount(42),
  source: 'enter',
  query_text: 'private search text'
}), true);
currentTime = 2500;
assert.strictEqual(recorder.track('skill_detail_opened', {
  skill_install: 'owner/repo/path',
  source_view: 'search',
  referrer: 'https://private.example'
}), true);

const summary = recorder.finish();
assert.strictEqual(summary.active, false);
assert.strictEqual(summary.started_ms, undefined);
assert.strictEqual(summary.cohort, 'q3cohortwithspaces');
assert.strictEqual(summary.events[0].elapsed_ms, 0);
assert.strictEqual(summary.events[1].elapsed_ms, 1500);
assert.strictEqual(summary.events[0].details.query_text, undefined);
assert.strictEqual(summary.events[1].details.referrer, undefined);
assert.strictEqual(JSON.stringify(summary).includes('private search text'), false);
assert.strictEqual(recorder.track('github_opened', {}), false);
console.log(JSON.stringify(summary));
"""
    )

    assert result["schema_version"] == 1
    assert [event["name"] for event in result["events"]] == [
        "search_submitted",
        "skill_detail_opened",
    ]


def test_study_mode_is_query_gated_and_has_no_network_sender():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "docs" / "js" / "study-mode.js").read_text(encoding="utf-8")

    assert '<script src="js/study-mode.js"></script>' in html
    assert 'id="study-panel"' in html
    assert "params.get('study') !== '1'" in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source
    assert "query_text" not in source
    assert "userAgent" not in source
    assert "document.referrer" not in source
