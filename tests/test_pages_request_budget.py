import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node_harness(script: str) -> dict:
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


def test_pages_request_budgets_and_exhaustive_search_behavior():
    result = run_node_harness(
        r"""
const fs = require('fs');
const assert = require('assert');
const app = fs.readFileSync('docs/js/app.js', 'utf8');
const render = fs.readFileSync('docs/js/app-render.js', 'utf8');

function extract(source, name) {
  const asyncStart = source.indexOf(`async function ${name}(`);
  const start = asyncStart >= 0 ? asyncStart : source.indexOf(`function ${name}(`);
  assert(start >= 0, `missing function ${name}`);
  const bodyStart = source.indexOf('{', start);
  let depth = 0;
  let quote = null;
  let escaped = false;
  for (let i = bodyStart; i < source.length; i += 1) {
    const char = source[i];
    if (quote) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === quote) quote = null;
      continue;
    }
    if (char === "'" || char === '"' || char === '`') {
      quote = char;
    } else if (char === '{') {
      depth += 1;
    } else if (char === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unterminated function ${name}`);
}

const responses = new Map();
const requests = [];
global.fetch = async url => {
  requests.push(url);
  if (!responses.has(url)) return { ok: false, status: 404, json: async () => ({}) };
  return { ok: true, status: 200, json: async () => structuredClone(responses.get(url)) };
};

const CONFIG = {
  INDEX_URL: 'search-index-lite.json',
  LEGACY_INDEX_URL: 'search-index.json',
  LEADERBOARD_SIZE: 50,
  FUSE_OPTIONS: {}
};
const CATEGORY_NAMES = { dev: 'Development', oth: 'Other' };
const CATEGORY_CODES_REVERSE = { development: 'dev', other: 'oth' };
let state = {
  index: null, fullIndex: null, categories: [], categoryCache: {}, featured: [],
  currentQuery: '', currentStarsFilter: '', currentSourceFilter: '',
  currentTagFilters: [], currentCategory: ''
};

eval(extract(app, 'fetchJson'));
eval(extract(app, 'normalizeCategoryCode'));
eval(extract(app, 'normalizeSkillRecord'));
eval(extract(app, 'normalizeSearchIndex'));
eval(extract(app, 'loadSearchIndex'));
eval(extract(app, 'findCategoryByCode'));
eval(extract(app, 'loadCategoryLeaderboardSkills'));
eval(extract(app, 'loadFullSearchIndex'));

responses.set('search-index-lite.json', {
  version: 'lite', total_count: 4, included_count: 2,
  skills: [{ name: 'Lite A', install: 'a/a' }, { name: 'Lite B', install: 'b/b' }]
});

(async () => {
  state.index = await loadSearchIndex();
  assert.deepStrictEqual(requests, ['search-index-lite.json']);
  assert.strictEqual(state.index.isLite, true);
  assert.strictEqual(state.index.includedCount, 2);

  const firstPart = Array.from({ length: 60 }, (_, i) => ({
    name: `Rank ${i}`, install: `owner/rank-${i}`, stars: 1000 - i, category: 'development'
  }));
  responses.set('categories/development/manifest.json', {
    count: 70,
    parts: [{ path: 'categories/development/part-000.json' }, { path: 'categories/development/part-001.json' }]
  });
  responses.set('categories/development/part-000.json', { skills: firstPart });
  responses.set('categories/development/part-001.json', { skills: [{ name: 'Must not fetch' }] });
  state.categories = [{ code: 'dev', manifest: 'categories/development/manifest.json' }];
  const categorySkills = await loadCategoryLeaderboardSkills('dev');
  assert.strictEqual(categorySkills.length, 60);
  assert.deepStrictEqual(requests.slice(1), [
    'categories/development/manifest.json', 'categories/development/part-000.json'
  ]);

  const beforeGlobal = requests.length;
  state.featured = [
    { name: 'Featured B', install: 'f/b', stars: 10 },
    { name: 'Featured A', install: 'f/a', stars: 20 }
  ];
  const globalElements = {
    leaderboardSection: { classList: { remove() {} } },
    leaderboardList: { innerHTML: '' },
    leaderboardStatus: { textContent: '' }
  };
  const globalRunner = new Function(
    'state', 'elements', 'CONFIG', 'normalizeSkillRecord', 'loadCategoryLeaderboardSkills',
    'createLeaderboardCard', `${extract(render, 'showLeaderboard')}; return showLeaderboard;`
  )(state, globalElements, CONFIG, normalizeSkillRecord,
    async () => { throw new Error('category loader should not run'); },
    skill => skill.n);
  await globalRunner('');
  assert.strictEqual(requests.length, beforeGlobal);
  assert.strictEqual(globalElements.leaderboardList.innerHTML, 'Featured AFeatured B');

  responses.set('search-index.json', { deprecated_full_payload: true, manifest: 'search-index-manifest.json' });
  responses.set('search-index-manifest.json', {
    v: 'full', total_count: 4,
    shards: [{ path: 'search-shards/part-000.json' }, { path: 'search-shards/part-001.json' }]
  });
  responses.set('search-shards/part-000.json', { s: [{ n: 'A' }, { n: 'B' }] });
  responses.set('search-shards/part-001.json', { s: [{ n: 'C' }, { n: 'Needle' }] });
  const full = await loadFullSearchIndex();
  assert.strictEqual(full.s.length, 4);
  assert.deepStrictEqual(requests.slice(beforeGlobal), [
    'search-index.json', 'search-index-manifest.json',
    'search-shards/part-000.json', 'search-shards/part-001.json'
  ]);

  const actionState = { index: state.index, currentQuery: 'needle' };
  const actionElements = {
    searchAllBtn: { disabled: false, textContent: '' },
    searchScope: { textContent: '' }
  };
  let fuseSize = 0;
  let rerunQuery = '';
  const actionRunner = new Function(
    'state', 'elements', 'loadFullSearchIndex', 'Fuse', 'CONFIG',
    'updateSearchScopeDisplay', 'search', 'hasActiveFilters', 'searchWithFiltersOnly',
    `${extract(app, 'activateFullSearch')}; return activateFullSearch;`
  )(
    actionState, actionElements, async () => full,
    class { constructor(skills) { fuseSize = skills.length; } }, CONFIG,
    () => {}, query => { rerunQuery = query; }, () => false, async () => {}
  );
  await actionRunner();
  assert.strictEqual(actionState.index.s.length, 4);
  assert.strictEqual(fuseSize, 4);
  assert.strictEqual(rerunQuery, 'needle');

  process.stdout.write(JSON.stringify({ requests, fullCount: full.s.length, fuseSize, rerunQuery }));
})().catch(error => { console.error(error); process.exit(1); });
"""
    )

    assert result["fullCount"] == 4
    assert result["fuseSize"] == 4
    assert result["rerunQuery"] == "needle"
    assert "categories/development/part-001.json" not in result["requests"]
