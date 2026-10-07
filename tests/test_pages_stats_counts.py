import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STALE_DISPLAY_COUNT = "67" + ",000"
STALE_PLAIN_COUNT = "67" + "000"


def test_pages_shell_does_not_hardcode_stale_skill_count():
    pages_files = [
        ROOT / "docs" / "index.html",
        ROOT / "docs" / "js" / "app.js",
    ]

    for path in pages_files:
        text = path.read_text(encoding="utf-8")
        assert STALE_DISPLAY_COUNT not in text
        assert STALE_PLAIN_COUNT not in text


def test_homepage_uses_neutral_count_fallback_until_stats_loads():
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")

    assert "Search <span id=\"total-count\">skills</span> for Claude Code" in html
    assert "Search and discover Claude Code skills" in html


def test_pages_app_renders_visible_count_from_stats_json():
    app_js = (ROOT / "docs" / "js" / "app.js").read_text(encoding="utf-8")

    assert "independent_skill_count" in app_js
    assert "archive_skill_md_count_raw" in app_js
    assert "updateRegistryCountDisplay" in app_js
    assert "document.title" in app_js
    assert "metaDescription" in app_js


def test_independent_totals_source_percentages_and_copy_detail_behavior():
    result = subprocess.run(["node", "-e", r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const app = fs.readFileSync('docs/js/app.js', 'utf8');
const render = fs.readFileSync('docs/js/app-render.js', 'utf8');
const containers = { 'category-chart': {}, 'skill-source-copies': {} };
const context = {
    state: { stats: { independent_skill_count: 2, registry_skill_count_dedup: 30 },
             index: { t: 30, s: [], isLite: false },
             categories: [{ code: 'dev', count: 20 }, { code: 'dat', count: 10 }] },
    elements: { totalCount: {}, searchScope: {}, searchAllBtn: {} },
    document: { getElementById: id => containers[id], addEventListener: () => {} },
    CATEGORY_COLORS: {}, categoryReportingLabel: code => code, normalizeCategoryCode: code => code,
    console: { error: () => {} },
};
vm.createContext(context);
vm.runInContext(render, context);
vm.runInContext(app.slice(app.indexOf('function normalizeSkillRecord('),
                          app.indexOf('async function loadSearchIndex(')), context);
vm.runInContext(app.slice(app.indexOf('function readNumericStat('),
                          app.indexOf('async function init(')), context);
assert.strictEqual(context.getDisplaySkillCount(), 2);
assert.strictEqual(context.getTotalSkillCount(), 2);
context.updateRegistryCountDisplay();
assert.strictEqual(context.elements.totalCount.textContent, '2');
assert(context.elements.totalCount.title.includes('independent skills'));
assert(context.elements.searchScope.textContent.includes('30 source entries'));
context.renderCategoryChart();
assert(containers['category-chart'].innerHTML.includes('66.7%'));
assert(containers['category-chart'].innerHTML.includes('33.3%'));
context.state.stats = {};
assert.strictEqual(context.getDisplaySkillCount(), 0);
assert.strictEqual(context.getTotalSkillCount(), null);
const normalized = context.normalizeSkillRecord({ name: 'review', canonical_id: 'abc', copies: 12, is_canonical: false });
assert.strictEqual(normalized.canonical_id, 'abc');
assert.strictEqual(normalized.copies, 12);
assert.strictEqual(normalized.is_canonical, false);

(async () => {
    let fetched = [];
    context.fetchJson = async url => {
        fetched.push(url);
        return { skills: [{ id: 'abc', copies: 12, source_copies: Array.from({ length: 12 }, (_, i) => ({
            install: `acme/repo-${i}/review`, branch: 'feature/next'
        })).concat([{ install: 'acme/repo/<img>', branch: '<script>' }]) }] };
    };
    await context.loadSkillSourceCopies(normalized);
    assert.deepStrictEqual(fetched, ['skill-detail-shards/a.json']);
    const content = containers['skill-source-copies'].innerHTML;
    assert(content.includes('Found in 12 repositories'));
    assert(content.includes('acme/repo-11/review'));
    assert(content.includes('feature%2Fnext'));
    assert(!content.includes('<img>'));
    assert(!content.includes('<script>'));
    context.fetchJson = async () => { throw Error('offline'); };
    await context.loadSkillSourceCopies(normalized);
    assert(containers['skill-source-copies'].innerHTML.includes('could not be loaded'));
    context.fetchJson = async () => ({ skills: [] });
    await context.loadSkillSourceCopies(normalized);
    assert(containers['skill-source-copies'].innerHTML.includes('could not be loaded'));
    context.fetchJson = async () => {
        containers['skill-source-copies'] = { innerHTML: 'new selection' };
        return { skills: [{ id: 'abc', copies: 1, source_copies: [] }] };
    };
    await context.loadSkillSourceCopies(normalized);
    assert.strictEqual(containers['skill-source-copies'].innerHTML, 'new selection');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_copy_modal_selects_the_clicked_source_branch():
    result = subprocess.run(["node", "-e", r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const main = { n: 'main skill', d: 'main body', c: 'dev', i: 'acme/repo/review', b: 'main', r: 10, canonical_id: 'main-group' };
const master = { ...main, n: 'master skill', d: 'different body', b: 'master', canonical_id: 'master-group' };
const context = {
    state: { index: { s: [main] }, results: [{ item: master }], favorites: [] },
    document: { addEventListener: () => {}, createElement: () => ({ dataset: {} }) },
    elements: { modalBody: {}, modal: { classList: { remove: () => {} } } },
    categoryDisplayName: code => code,
    findSimilarSkills: () => [],
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('docs/js/app-render.js', 'utf8'), context);
let opened;
context.loadSkillSourceCopies = skill => { opened = skill; };
context.loadCommunityData = () => {};
(async () => {
    assert(context.createSkillCard(master).includes('data-branch="master"'));
    assert(context.createLeaderboardCard(master, 1).includes('data-branch="master"'));
    assert(context.createSkillCard({ name: 'master', install: master.i, branch: 'master', stars: 10 }, true).includes('data-branch="master"'));
    await context.showSkillDetail({ dataset: { install: master.i, branch: 'master' } });
    assert.strictEqual(opened.canonical_id, 'master-group');
    assert(context.elements.modalBody.innerHTML.includes('master skill'));
    assert(context.elements.modalBody.innerHTML.includes('/blob/master/'));
    context.state.index.s = [main, master];
    context.state.results = [];
    await context.showSkillDetail({ dataset: { install: master.i, branch: 'master' } });
    assert.strictEqual(opened.canonical_id, 'master-group');
    context.findSimilarSkills = () => [master];
    await context.showSkillDetail({ dataset: { install: main.i, branch: 'main' } });
    assert(context.elements.modalBody.innerHTML.includes('class="similar-card" data-install="acme/repo/review" data-branch="master"'));
    assert.strictEqual(opened.canonical_id, 'main-group');
    vm.runInContext('Math.random = () => 0.99', context);
    context.showRandomSkill();
    assert.strictEqual(opened.canonical_id, 'master-group');
})().catch(error => { console.error(error); process.exitCode = 1; });
"""], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
