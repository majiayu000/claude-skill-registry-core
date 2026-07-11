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
  leaderboardRequestToken: 0,
  currentQuery: '', currentStarsFilter: '', currentSourceFilter: '',
  currentTagFilters: [], currentCategory: ''
};

eval(extract(app, 'fetchJson'));
eval(extract(app, 'normalizeCategoryCode'));
eval(extract(app, 'normalizeSkillRecord'));
eval(extract(app, 'normalizeSearchIndex'));
eval(extract(app, 'requireSchemaOne'));
eval(extract(app, 'isSafeArtifactPath'));
eval(extract(app, 'requireNonNegativeInteger'));
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
    schema_version: 1, category: 'development', code: 'dev', count: 70, part_count: 2,
    part_strategy: 'bounded-sequential-stars-desc',
    parts: [
      { path: 'categories/development/part-000.json', count: 60 },
      { path: 'categories/development/part-001.json', count: 10 }
    ]
  });
  responses.set('categories/development/part-000.json', {
    schema_version: 1, category: 'development', code: 'dev', part: 0, part_count: 2,
    count: 60, skills: firstPart
  });
  responses.set('categories/development/part-001.json', { skills: [{ name: 'Must not fetch' }] });
  state.categories = [{ name: 'development', code: 'dev', manifest: 'categories/development/manifest.json' }];
  const categorySkills = await loadCategoryLeaderboardSkills('dev');
  assert.strictEqual(categorySkills.length, 60);
  assert.deepStrictEqual(requests.slice(1), [
    'categories/development/manifest.json', 'categories/development/part-000.json'
  ]);
  state.categoryCache = {};
  const categoryManifest = responses.get('categories/development/manifest.json');
  delete categoryManifest.part_strategy;
  const invalidCategoryStart = requests.length;
  await assert.rejects(loadCategoryLeaderboardSkills('dev'), /not stars-desc ranked/);
  assert.deepStrictEqual(requests.slice(invalidCategoryStart), ['categories/development/manifest.json']);
  categoryManifest.part_strategy = 'bounded-sequential-stars-desc';
  state.categories = [];
  const missingCategoryStart = requests.length;
  await assert.rejects(loadCategoryLeaderboardSkills('dev'), /Unknown leaderboard category/);
  assert.strictEqual(requests.length, missingCategoryStart);
  state.categories = [{ name: 'development', code: 'dev', manifest: 'categories/development/manifest.json' }];

  categoryManifest.part_strategy = 'invalid';
  state.categoryCache = {};
  const failedElements = {
    leaderboardSection: { classList: { remove() {} } },
    leaderboardList: { innerHTML: 'must be cleared' },
    leaderboardStatus: { textContent: '' }
  };
  const failedRunner = new Function(
    'state', 'elements', 'CONFIG', 'normalizeSkillRecord', 'loadCategoryLeaderboardSkills',
    'createLeaderboardCard', `${extract(render, 'showLeaderboard')}; return showLeaderboard;`
  )(state, failedElements, CONFIG, normalizeSkillRecord, loadCategoryLeaderboardSkills,
    () => { throw new Error('ranked cards must not render'); });
  const invalidManifestStart = requests.length;
  await failedRunner('dev');
  assert.strictEqual(failedElements.leaderboardList.innerHTML, '');
  assert.match(failedElements.leaderboardStatus.textContent, /load failed.*retry/i);
  assert.deepStrictEqual(requests.slice(invalidManifestStart), [
    'categories/development/manifest.json'
  ]);
  categoryManifest.part_strategy = 'bounded-sequential-stars-desc';
  state.categories = [{ name: 'development', code: 'dev' }];
  failedElements.leaderboardList.innerHTML = 'must be cleared again';
  const missingManifestStart = requests.length;
  await failedRunner('dev');
  assert.strictEqual(failedElements.leaderboardList.innerHTML, '');
  assert.match(failedElements.leaderboardStatus.textContent, /load failed.*retry/i);
  assert.strictEqual(requests.length, missingManifestStart);
  state.categories = [{ name: 'development', code: 'dev', manifest: 'categories/development/manifest.json' }];

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

  let releaseSlow;
  const delayedElements = {
    leaderboardSection: { classList: { remove() {} } },
    leaderboardList: { innerHTML: '' },
    leaderboardStatus: { textContent: '' }
  };
  const delayedRunner = new Function(
    'state', 'elements', 'CONFIG', 'normalizeSkillRecord', 'loadCategoryLeaderboardSkills',
    'createLeaderboardCard', `${extract(render, 'showLeaderboard')}; return showLeaderboard;`
  )(state, delayedElements, CONFIG, normalizeSkillRecord,
    category => category === 'dev'
      ? new Promise(resolve => { releaseSlow = resolve; })
      : Promise.resolve([{ n: 'Fast category', r: 20, i: 'fast/skill', b: 'main' }]),
    skill => skill.n);
  const slowRequest = delayedRunner('dev');
  await Promise.resolve();
  await delayedRunner('oth');
  const currentStatus = delayedElements.leaderboardStatus.textContent;
  releaseSlow([{ n: 'Stale category', r: 100, i: 'stale/skill', b: 'main' }]);
  await slowRequest;
  assert.strictEqual(delayedElements.leaderboardList.innerHTML, 'Fast category');
  assert.strictEqual(delayedElements.leaderboardStatus.textContent, currentStatus);

  responses.set('search-index.json', {
    schema_version: 1, t: 4, deprecated_full_payload: true, manifest: 'search-index-manifest.json'
  });
  responses.set('search-index-manifest.json', {
    schema_version: 1, v: 'full', total_count: 4, shard_count: 2,
    shards: [
      { path: 'search-shards/part-000.json', count: 2 },
      { path: 'search-shards/part-001.json', count: 2 }
    ]
  });
  responses.set('search-shards/part-000.json', {
    schema_version: 1, part: 0, part_count: 2, count: 2,
    s: [{ n: 'A', i: 'a/skill', b: 'main' }, { n: 'B', i: 'b/skill', b: 'main' }]
  });
  responses.set('search-shards/part-001.json', {
    schema_version: 1, part: 1, part_count: 2, count: 2,
    s: [{ n: 'C', i: 'c/skill', b: 'main' }, { n: 'Needle', i: 'n/skill', b: 'main' }]
  });
  const full = await loadFullSearchIndex();
  assert.strictEqual(full.s.length, 4);
  assert.deepStrictEqual(requests.slice(beforeGlobal), [
    'search-index.json', 'search-index-manifest.json',
    'search-shards/part-000.json', 'search-shards/part-001.json'
  ]);

  state.fullIndex = null;
  responses.get('search-shards/part-001.json').s[1] = { n: 'Duplicate', i: 'a/skill', b: 'main' };
  await assert.rejects(loadFullSearchIndex(), /duplicate stable records/);
  responses.get('search-shards/part-001.json').s[1] = { n: 'Needle', i: 'n/skill', b: 'main' };

  state.fullIndex = null;
  const searchManifest = responses.get('search-index-manifest.json');
  searchManifest.shards[0].path = '../escape.json';
  const unsafeStart = requests.length;
  await assert.rejects(loadFullSearchIndex(), /Invalid or duplicate search shard path/);
  assert.deepStrictEqual(requests.slice(unsafeStart), ['search-index.json', 'search-index-manifest.json']);
  searchManifest.shards[0].path = 'search-shards/part-000.json';

  state.fullIndex = null;
  responses.get('search-shards/part-000.json').part = 9;
  await assert.rejects(loadFullSearchIndex(), /identity\/count mismatch/);
  responses.get('search-shards/part-000.json').part = 0;

  state.fullIndex = null;
  delete responses.get('search-index.json').schema_version;
  await assert.rejects(loadFullSearchIndex(), /schema_version must be 1/);
  responses.get('search-index.json').schema_version = 1;

  state.fullIndex = null;
  searchManifest.shard_count = 3;
  await assert.rejects(loadFullSearchIndex(), /manifest count mismatch/);
  searchManifest.shard_count = 2;

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
