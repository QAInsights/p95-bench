import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { budgetScale, replayScore } from '../src/lib/scores.mjs';

const published = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));

test('replay scores only landed passing attempts and finishes at published weighted scores', () => {
  for (const model of published.models) {
    assert.equal(replayScore(model, new Set(), published.weights), 0);
    assert.ok(Math.abs(replayScore(model, new Set(model.per_task_results.keys()), published.weights)
      - model.overall_score) < 1e-10);
    const failures = new Set(model.per_task_results.flatMap((task, i) => task.passed ? [] : [i]));
    assert.equal(replayScore(model, failures, published.weights), 0);
  }
  const model = {
    category_scores: { analysis: 50, workload: 100 },
    per_task_results: [
      { task_id: 'repeat', category: 'analysis', passed: true },
      { task_id: 'repeat', category: 'analysis', passed: false },
      { task_id: 'load', category: 'workload', passed: true },
    ],
  };
  const weights = { analysis: 15, workload: 15, repair: 15 };
  assert.equal(replayScore(model, new Set([0]), weights), 25);
  assert.equal(replayScore(model, new Set([2]), weights), 50);
  assert.equal(replayScore(model, new Set([0, 1, 2]), weights), 75);
  assert.equal(replayScore(model, new Set([-1, 10]), weights), 0);
  assert.equal(replayScore({ category_scores: {}, per_task_results: [] }, new Set(), weights), 0);
});

test('log budget scale is invertible, bounded and has a safe empty or invalid-cost domain', () => {
  const scale = budgetScale(published.models);
  assert.ok(scale.min < Math.min(...published.models.map(m => m.cost)));
  assert.ok(scale.max > Math.max(...published.models.map(m => m.cost)));
  for (const position of [0, 100, 500, 750, 1000]) {
    assert.ok(Math.abs(scale.toPosition(scale.toCost(position)) - position) < 1e-9);
  }
  assert.ok(Math.abs(scale.toCost(500) - Math.sqrt(scale.min * scale.max)) < 1e-10);
  assert.equal(scale.toCost(-1), scale.toCost(0));
  assert.equal(scale.toCost(1001), scale.toCost(1000));
  assert.equal(scale.toPosition(0), 0);
  assert.equal(scale.toPosition(-2), 0);
  assert.equal(scale.toPosition(Infinity), 1000);
  assert.equal(scale.toPosition(NaN), 0);
  for (const models of [[], [{ cost: 0 }, { cost: -1 }, { cost: NaN }, { cost: Infinity }]]) {
    const empty = budgetScale(models);
    assert.equal(empty.min, .001);
    assert.equal(empty.max, 1);
    assert.ok(Number.isFinite(empty.toCost(500)));
  }
  const single = budgetScale([{ cost: 1 }]);
  assert.ok(single.toCost(0) < 1 && single.toCost(1000) > 1);
});

test('home uses build-time data, separate client code and preserved methodology and head metadata', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const client = await readFile(new URL('../src/lib/fullpage.mjs', import.meta.url), 'utf8');
  assert.match(page, /import data from ['"]\.\.\/data\/leaderboard\.json['"]/);
  assert.match(client, /import DATA from ['"]\.\.\/data\/leaderboard\.json['"]/);
  assert.doesNotMatch(page + client, /fonts\.googleapis\.com|benchmark-data|—/);
  for (const attribute of ['rel="icon"', 'rel="apple-touch-icon"', 'rel="manifest"', 'name="theme-color"']) {
    assert.ok(page.includes(attribute));
  }
  assert.ok(page.indexOf('id="tools"') > page.indexOf('id="scene-4"'));
  assert.ok(page.indexOf('id="method"') > page.indexOf('id="tools"'));
  assert.match(page, /Claude Sonnet 5\.5, Opus 5\.5, GPT-6\.2 Sol and Astra/);
  assert.match(page.replace(/\s+/g, ' '), /max_completion_tokens instead of max_tokens/);
  assert.match(page, /toolScoreLabel\(m, ['"]jmeter['"]\)/);
  assert.match(page, /toolScoreLabel\(m, ['"]k6['"]\)/);
  assert.match(page, /\["Gatling", "Locust"\]/);
  assert.match(page, /k6 v1\.0\.0 scripts/);
});
