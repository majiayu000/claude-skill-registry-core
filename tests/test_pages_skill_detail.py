"""Exercise rendered card clicks against the actual app without a browser or network."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_loaded_skill_cards_open_details():
    result = subprocess.run(["node", "-e", r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('node:assert/strict');
const { test } = require('node:test');

function setup() {
    const nodes = new Map();
    function element() {
        const classes = new Set();
        return {
            innerHTML: '', textContent: '', value: '', dataset: {},
            classList: {
                add: name => classes.add(name), remove: name => classes.delete(name),
                contains: name => classes.has(name),
                toggle: (name, force) => force ? classes.add(name) : classes.delete(name)
            },
            addEventListener() {}, setAttribute() {}, removeAttribute() {},
            querySelector: element
        };
    }
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, element());
        return nodes.get(id);
    };
    let requests = 0;
    const sourceLoads = [];
    const context = {
        document: {
            getElementById: node, querySelector: () => element(), querySelectorAll: () => [],
            addEventListener() {}, createElement: element, documentElement: element()
        },
        window: {}, localStorage: { getItem: () => null }, console, setTimeout, clearTimeout,
        fetch: async () => { requests++; throw Error('Unexpected network request'); }
    };
    vm.createContext(context);
    for (const file of ['skill-source.js', 'app-render.js', 'app.js']) {
        const source = fs.readFileSync(`docs/js/${file}`, 'utf8');
        vm.runInContext(source.replace(/\ninit\(\);\s*$/, ''), context);
    }
    const state = vm.runInContext('state', context);
    state.index = { s: [], isLite: true };
    state.results = [];
    state.featured = [];
    state.categoryCache = {};
    context.loadSkillSourceCopies = skill => sourceLoads.push(skill);
    node('skill-modal').classList.add('hidden');
    return { context, state, node, sourceLoads, requests: () => requests };
}

function record(tier, branch) {
    return {
        name: 'Selected skill', description: 'Selected description', category: 'development',
        tags: ['selected-tag'], stars: 73, install: 'acme/tools/skills/selected', branch,
        tier, u: 'skills/selected/', canonical_id: 'canonical-selected', copies: 3
    };
}

function cardFrom(html) {
    const attributes = html.match(/data-install="([^"]+)" data-branch="([^"]+)"/);
    assert(attributes, 'the real renderer should produce a clickable card');
    return { dataset: { install: attributes[1], branch: attributes[2] } };
}

for (const collection of ['index', 'results', 'featured', 'featured-leaderboard', 'category-leaderboard']) {
    for (const tier of ['listed', 'archived']) {
        for (const branch of [undefined, 'release/v2']) {
            test(`${collection}: ${tier}, ${branch || 'default branch'}`, async () => {
                const env = setup();
                const { context, state, node } = env;
                const selected = record(tier, branch);
                const compact = context.normalizeSkillRecord(selected);
                const decoy = context.normalizeSkillRecord({
                    ...selected, name: 'Wrong branch', branch: 'unselected-branch'
                });
                state.index.s = [decoy];
                let html;
                if (collection === 'index') {
                    state.index.s.push(compact);
                    html = context.createSkillCard(compact);
                } else if (collection === 'results') {
                    state.results = [{ item: compact }];
                    context.displayResults();
                    html = node('search-results').innerHTML;
                } else if (collection === 'featured') {
                    state.featured = [selected];
                    context.showFeatured();
                    html = node('featured-list').innerHTML;
                } else {
                    if (collection === 'featured-leaderboard') state.featured = [selected];
                    else state.categoryCache.dev = [compact];
                    await context.showLeaderboard(collection === 'category-leaderboard' ? 'dev' : '');
                    html = node('leaderboard-list').innerHTML;
                }
                await context.showSkillDetail(cardFrom(html));
                assert(!node('skill-modal').classList.contains('hidden'), 'card click must open modal');
                const detail = node('modal-body').innerHTML;
                assert(detail.includes('Selected skill'));
                assert(detail.includes('Selected description'));
                assert(detail.includes('#selected-tag'));
                assert(detail.includes('skills/selected/'));
                assert(detail.includes(context.getGitHubUrl(selected.install, branch || 'main')));
                assert.match(detail, /<h2[^>]*>\s*Selected skill/);
                if (tier === 'listed') {
                    assert(detail.includes('Listed only'));
                    assert(!detail.includes('onclick="copyInstall(event)"'));
                    assert.equal(env.sourceLoads.length, 0);
                } else {
                    assert(detail.includes('onclick="copyInstall(event)"'));
                    assert.equal(env.sourceLoads.length, 1);
                    assert.equal(env.sourceLoads[0].canonical_id, selected.canonical_id);
                    assert.equal(env.sourceLoads[0].copies, selected.copies);
                }
                assert.equal(env.requests(), 0, 'lookup must use already loaded records');
            });
        }
    }
}

test('unknown source and wrong branch remain closed', async () => {
    const env = setup();
    const selected = record('listed', 'release/v2');
    env.state.featured = [selected];
    env.state.categoryCache.dev = [env.context.normalizeSkillRecord(selected)];
    for (const dataset of [
        { install: 'acme/missing', branch: 'release/v2' },
        { install: selected.install, branch: 'main' }
    ]) {
        await env.context.showSkillDetail({ dataset });
        assert(env.node('skill-modal').classList.contains('hidden'));
        assert.equal(env.node('modal-body').innerHTML, '');
    }
    assert.equal(env.requests(), 0);
});

test('existing index and result precedence is preserved', async () => {
    const env = setup();
    const { state, context, node } = env;
    const selected = record('listed', 'release/v2');
    const compact = context.normalizeSkillRecord(selected);
    state.index.s = [{ ...compact, n: 'Index winner' }];
    state.results = [{ item: { ...compact, n: 'Result winner' } }];
    state.featured = [{ ...selected, name: 'Featured winner' }];
    state.categoryCache.dev = [{ ...compact, n: 'Category winner' }];
    const card = cardFrom(context.createSkillCard(compact));
    for (const [expected, clear] of [
        ['Index winner', () => { state.index.s = []; }],
        ['Result winner', () => { state.results = []; }],
        ['Featured winner', () => { state.featured = []; }],
        ['Category winner', () => {}]
    ]) {
        await context.showSkillDetail(card);
        assert(node('modal-body').innerHTML.includes(expected));
        clear();
    }
    assert.equal(env.requests(), 0);
});
"""], cwd=ROOT, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
