"""Offline markup/state checks; these do not test screen-reader announcements."""

import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SearchStatusTests(unittest.TestCase):
    def test_status_containers_exist_before_updates(self):
        class Elements(HTMLParser):
            def __init__(self):
                super().__init__()
                self.elements = []

            def handle_starttag(self, tag, attrs):
                self.elements.append((tag, dict(attrs)))

        parsed = Elements()
        parsed.feed((ROOT / "docs/index.html").read_text(encoding="utf-8"))
        count = next(attrs for _, attrs in parsed.elements if attrs.get("id") == "result-count")
        self.assertEqual(count.get("role"), "status")
        self.assertEqual(count.get("aria-atomic"), "true")
        scope = next(
            attrs for _, attrs in parsed.elements if attrs.get("class") == "search-scope-row"
        )
        self.assertEqual(scope.get("aria-live"), "polite")
        # Announce the summary, not every result card or the elapsed milliseconds.
        for element_id in ("search-results", "search-time", "empty-state", "loading"):
            attrs = next(a for _, a in parsed.elements if a.get("id") == element_id)
            self.assertNotIn("aria-live", attrs)
            self.assertNotIn("role", attrs)

    def test_search_and_loading_status_transitions(self):
        result = subprocess.run(
            ["node", "-e", r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const nodes = new Map();
function element() {
    const classes = new Set();
    return {
        textContent: '', innerHTML: '', value: '', dataset: {},
        classList: {
            add: name => classes.add(name), remove: name => classes.delete(name),
            contains: name => classes.has(name),
            toggle: (name, force) => force ? classes.add(name) : classes.delete(name)
        },
        addEventListener() {}, setAttribute() {}, removeAttribute() {},
        appendChild() {}, querySelector: () => element(),
        insertAdjacentHTML(_, html) { this.innerHTML += html; }
    };
}
const node = id => {
    if (!nodes.has(id)) nodes.set(id, element());
    return nodes.get(id);
};
const context = {
    document: {
        getElementById: node, querySelector: () => element(), querySelectorAll: () => [],
        addEventListener() {}, createElement: () => element(), documentElement: element()
    },
    localStorage: { getItem: () => null },
    performance: { now: () => 0 }, console: { error() {} }, setTimeout, clearTimeout,
    fetch: async () => ({ ok: true, json: async () => ({}) })
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/js/app-render.js', 'utf8'), context);
const app = fs.readFileSync('docs/js/app.js', 'utf8');
// Load the real functions/listeners, but control when initialization completes.
vm.runInContext(app.replace(/\ninit\(\);\s*$/, ''), context);
vm.runInContext(`
    state.index = { s: [], isLite: true, includedCount: 2, t: 10 };
    state.stats = {};
    state.fuse = { search: () => [{ item: {
        n: 'Review', d: 'Review code', i: 'acme/review', c: 'dev', g: [], r: 1,
        u: 'skills/review-example/'
    }}] };
`, context);
context.updateRegistryCountDisplay();
assert.strictEqual(node('search-scope').textContent,
    'Searching 2 highlighted of 10 source entries');
context.search('review');
assert.strictEqual(node('result-count').textContent, '1 results in highlighted index');
assert(node('search-results').innerHTML.includes('href="skills/review-example/"'));
assert(node('empty-state').classList.contains('hidden'));
vm.runInContext('state.fuse.search = () => []', context);
context.search('missing');
assert.strictEqual(node('result-count').textContent, '0 results in highlighted index');
assert(!node('empty-state').classList.contains('hidden'));

(async () => {
    context.fetchJson = async () => { throw Error('offline'); };
    await context.init();
    assert.strictEqual(node('search-scope').textContent,
        'Failed to load skills index. Reload the page to try again.');

    context.loadFullSearchIndex = async () => { throw Error('offline'); };
    const failed = context.activateFullSearch();
    assert.strictEqual(node('search-scope').textContent,
        'Loading the complete offline search index…');
    await failed;
    assert.strictEqual(node('search-scope').textContent, 'Full search failed: offline');
    assert.strictEqual(node('search-all-btn').disabled, false);

    context.loadFullSearchIndex = async () => ({ s: [], isLite: false, t: 10 });
    context.Fuse = function () { this.search = () => []; };
    await context.activateFullSearch();
    assert.strictEqual(node('search-scope').textContent, 'Searching all 10 source entries');
    assert.strictEqual(node('result-count').textContent, '0 results');
    assert.strictEqual(node('search-all-btn').disabled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
