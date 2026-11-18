import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { makePackCatalog, resolvePack, creditGradient } from '../src/lib/packs.mjs';
import { selectModelVersions, toolScore, taskResult, replayScore, bestWithin, costFrontier, shortTaskLabel } from '../src/lib/scores.mjs';
import { unitRecords, unitSummary } from '../src/lib/unit-matrix-data.mjs';
import { heatmapData, heatValue, heatColor } from '../src/lib/heatmap-data.mjs';
import { loadTaskData } from '../scripts/build-data.mjs';
import { buildScenarios } from '../src/lib/scenarios.mjs';
import { fakeV2 } from './fake-v2.mjs';
import { renderToolRankings, toolRanking } from '../src/lib/tool-rankings.mjs';

const v1 = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
const v2 = fakeV2();
const tasks = { v1: (await loadTaskData()).tasks, v2: (await loadTaskData({ packId: v2.pack_id })).tasks };
const catalog = makePackCatalog(v1, v2, tasks, []);

test('reused Tools rows refresh version labels and newer tags for v1 → v2 → v1', () => {
  class Element {
    constructor(tag) {
      this.tag = tag;
      this.children = [];
      this.dataset = {};
      this.classList = { toggle() {} };
    }
    append(...nodes) {
      for (const node of nodes) {
        if (typeof node !== 'string') {
          if (node.parentNode) node.parentNode.children.splice(node.parentNode.children.indexOf(node), 1);
          node.parentNode = this;
        }
        this.children.push(node);
      }
    }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    set textContent(value) { this.replaceChildren(value); }
    get textContent() { return this.children.map(node => typeof node === 'string' ? node : node.textContent).join(''); }
    querySelector(selector) {
      for (const child of this.children.filter(node => typeof node !== 'string')) {
        if (selector === child.tag || selector === '.' + child.className) return child;
        const nested = child.querySelector(selector);
        if (nested) return nested;
      }
      return null;
    }
  }
  const previousDocument = globalThis.document;
  globalThis.document = { createElement: tag => new Element(tag) };
  try {
    for (const tool of ['jmeter', 'k6']) {
      const list = new Element('ol');
      list.dataset.toolRanking = tool;
      list.previousElementSibling = new Element('p');
      const root = { querySelectorAll: () => [list] };
      let original;
      for (const pack of ['v1', 'v2', 'v1']) {
        renderToolRankings(root, catalog[pack].models, pack, true);
        const row = list.children.find(row => row.dataset.toolModel === 'Qwen-Ambassador/Qwen3.7-Max');
        original ??= row;
        assert.equal(row, original);
        const version = row.querySelector('.model-version');
        assert.equal(version.textContent, pack === 'v2' ? 'Latest' : 'Earlier versionnewer: Qwen3.8 Max');
        assert.equal(version.querySelector('small') !== null, pack === 'v1');
        assert.equal(list.children.length, catalog[pack].models.length);
      }
    }
    assert.doesNotThrow(() => renderToolRankings({ querySelectorAll: () => [] }, [], 'v2', false));
  } finally {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
  }
});

test('Tools rankings follow the selected pack, including missing runs and earlier versions', () => {
  const before = structuredClone(catalog);
  for (const tool of ['jmeter', 'k6']) {
    for (const pack of ['v1', 'v2']) {
      const rows = toolRanking(catalog[pack].models, tool, pack);
      assert.equal(rows.length, catalog[pack].models.length);
      assert.deepEqual(new Set(rows.filter(row => !row.hidden).map(row => row.model)),
        new Set(selectModelVersions(catalog[pack].models).map(model => model.model)));
      assert.ok(toolRanking(catalog[pack].models, tool, pack, true).every(row => !row.hidden));
      const opus = rows.find(row => row.model === 'claude-opus-5-5');
      if (tool === 'jmeter') assert.equal(opus.tool_label, pack === 'v2' ? '62.5%' : '100%');
      const measured = rows.filter(row => row.tool_score !== null);
      assert.ok(measured.every((row, index) => !index || row.tool_score <= measured[index - 1].tool_score));
    }
    assert.equal(toolRanking(catalog.v2.models, tool, 'v2').find(row => row.model === 'gpt-6.2-sol').tool_label, 'Not run on v2');
  }
  const only = { ...v2.models[0], provider: 'test', model: 'v2-only-model' };
  const union = makePackCatalog(v1, { ...v2, models: [...v2.models, only] }, tasks, []);
  assert.equal(toolRanking(union.v2.models, 'jmeter', 'v2').find(row => row.model === only.model).tool_label, '62.5%');
  assert.equal(toolRanking(union.v1.models, 'jmeter', 'v1').find(row => row.model === only.model).tool_label, 'Not run on v1');
  assert.deepEqual(catalog, before);
  assert.ok(toolRanking([], 'jmeter', 'v2').length === 0);
  assert.equal(toolRanking([{ provider: 'test', model: 'empty', per_task_results: [{ tool: 'jmeter', score: NaN }] }], 'jmeter', 'v2')[0].tool_label, 'Not run on v2');
});

test('only a nonempty v2 leaderboard enables v2; URLs resolve without prototype lookups', () => {
  for (const absent of [undefined, {}, { ...v2, models: [] }, { ...v2, models: 'bad' }, { ...v2, pack_id: 'unknown' }]) {
    const packs = makePackCatalog(v1, absent, tasks, []);
    assert.deepEqual(Object.keys(packs), ['v1']);
    assert.equal(resolvePack('v2', packs), 'v1');
    assert.equal(resolvePack(null, packs), 'v1');
  }
  assert.equal(resolvePack('v1', catalog), 'v1');
  assert.equal(resolvePack('v2', catalog), 'v2');
  assert.equal(resolvePack(null, catalog), 'v2');
  assert.equal(resolvePack('__proto__', catalog), 'v2');
  assert.equal(resolvePack(undefined, catalog), 'v2');
  assert.equal(resolvePack('', catalog), 'v2');
  assert.equal(resolvePack('unknown', catalog), 'v2');
});

test('pack changes retain unrun model identities, preserve latest/earlier selection, and do not mutate v1', () => {
  const before = structuredClone(v1);
  for (const earlier of [false, true]) {
    const selected = selectModelVersions(catalog.v2.models, earlier);
    const expected = selectModelVersions(v1.models, earlier).map(model =>
      !earlier && model.model === 'Qwen-Ambassador/Qwen3.8-Max' ? 'Qwen-Ambassador/Qwen3.7-Max' : model.model);
    assert.deepEqual(new Set(selected.map(model => model.model)), new Set(expected));
    const missing = selected.find(model => model.model === 'gpt-6.2-sol');
    assert.equal(missing.not_run, true);
    assert.equal(missing.overall_score, null);
    assert.deepEqual(missing.per_task_results, []);
    assert.equal(selected.at(-1).not_run, true);
  }
  for (const model of v1.models) assert.deepEqual(catalog.v1.models.find(item => item.model === model.model), model);
  assert.deepEqual(v1, before);
  assert.equal(catalog.v2.models.filter(model => !model.not_run).length, 3);
  assert.ok(selectModelVersions(catalog.v2.models).some(model => model.model === 'Qwen-Ambassador/Qwen3.7-Max'));
  assert.ok(selectModelVersions(catalog.v2.models, true).some(model => model.model === 'Qwen-Ambassador/Qwen3.7-Max'));
});

test('partial task, tool and replay scores preserve credit and finish at the published composite', () => {
  const model = v2.models[0], all = new Set(model.per_task_results.map((_, index) => index));
  assert.equal(taskResult(model, model.per_task_results[0].task_id).score, .6);
  assert.equal(taskResult(model, 'absent'), null);
  assert.equal(toolScore(model, 'jmeter'), 62.5);
  assert.equal(toolScore(model, 'absent'), null);
  assert.equal(replayScore(model, new Set(), v2.weights), 0);
  assert.ok(Math.abs(replayScore(model, all, v2.weights) - model.overall_score) < 1e-9);
  assert.ok(replayScore(model, new Set([0]), v2.weights) > 0);
  assert.equal(bestWithin(catalog.v2.models, 0), null);
  assert.ok(costFrontier(catalog.v2.models).every(model => !model.not_run));
  assert.equal(shortTaskLabel('v2-r01-extractor-scope'), 'R01');
  assert.equal(shortTaskLabel('v2-r02-stale-cookie'), 'R02');
  assert.equal(shortTaskLabel('v2-a01-jtl-endpoints'), 'A01');
  assert.equal(shortTaskLabel('k6-token-refresh'), 'K6TO');
});

test('matrix and heatmap distinguish partial credit, full credit, missing runs and genuine telemetry', () => {
  const records = unitRecords(catalog.v2.models, tasks.v2), summary = unitSummary(records);
  assert.equal(summary.partial, 14);
  assert.equal(summary.measured, 15);
  assert.ok(summary.notRun > 0);
  assert.ok(records.some(record => record.result === 'partial' && record.score === .6));
  const data = heatmapData(catalog.v2.models, tasks.v2, catalog.v2.attempts);
  const cell = data.rows.find(row => row.id === v2.models[0].model).cells.find(cell => cell.task === 'v2-r01-extractor-scope');
  assert.equal(cell.result, .6);
  assert.equal(cell.tokens, 80);
  assert.equal(cell.latency, 2);
  assert.equal(heatValue(cell, 'result'), '60.0% credit');
  assert.match(heatColor(cell.result, 'result', {}, true), /linear-gradient.*60%/);
  assert.ok(data.rows.find(row => row.id === 'gpt-6.2-sol').cells.every(cell => cell.result === null));
  assert.equal(creditGradient(NaN), null);
  assert.match(creditGradient(.6), /60%/);
  const failed = structuredClone(v2.models[0]);
  failed.per_task_results[0].score = 0;
  assert.equal(unitRecords([failed], tasks.v2)[0].result, 'fail');
});

test('v2 scenarios expose only public task rubrics and report fractional means with version filtering', () => {
  const latest = buildScenarios(tasks.v2, catalog.v2.models);
  const all = buildScenarios(tasks.v2, catalog.v2.models, true);
  assert.equal(latest.length, 40);
  const repair = latest.find(task => task.id === 'v2-r01-extractor-scope');
  assert.match(repair.passLabel, /48.0% mean quality/);
  assert.match(repair.grading, /prerequisites:/);
  assert.equal(all.find(task => task.id === repair.id).attempted, repair.attempted);
  assert.match(latest.find(task => task.id === 'v2-r02-cookie-lifetime').passLabel, /Not run on v2/);
});
