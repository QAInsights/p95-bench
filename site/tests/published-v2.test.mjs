import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { loadTaskData } from '../scripts/build-data.mjs';
import { makePackCatalog, resolvePack } from '../src/lib/packs.mjs';
import { replayScore, selectModelVersions } from '../src/lib/scores.mjs';

const data = JSON.parse(await readFile(new URL('../src/data/leaderboard-p95-v2.0.0.json', import.meta.url)));
const v1 = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
const { tasks } = await loadTaskData({ packId: 'p95-v2.0.0' });
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test('published v2 Qwen results expose only public per-task records and aggregate the full 50', () => {
  assert.equal(data.pack_id, 'p95-v2.0.0');
  assert.equal(data.default_view, false);
  assert.equal(data.budget_file_sha256, '09ca8ed77cd7cb0eebea6a3618ae13c0fdb55496dd14d438745234a0b4a05bc7');
  assert.deepEqual(new Set(data.models.map(model => model.model)),
    new Set(['Qwen-Ambassador/Qwen3.7-Max']));
  for (const model of data.models) {
    assert.equal(model.repeats, 1);
    assert.equal(model.coverage.run, 50);
    assert.equal(model.coverage.total, 50);
    assert.equal(model.coverage.partial, false);
    assert.match(model.cost_note, /list-price estimate/);
    assert.ok(Number.isFinite(model.cost) && model.cost > 0);
    assert.equal(model.budget_file_sha256, data.budget_file_sha256);
    assert.equal(model.evaluator_segments.reduce((sum, segment) => {
      assert.match(segment.digest, /^[a-f0-9]{64}$/);
      return sum + segment.scored_attempts;
    }, 0), 50);
    assert.equal(model.per_task_results.length, 40);
    assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
    for (const task of model.per_task_results) {
      assert.deepEqual(Object.keys(task).sort(), ['task_id', 'category', 'tool', 'score', 'efficiency_score',
        'passed', 'repeat', 'output_tokens', 'latency_seconds', 'detail'].sort());
      assert.ok(task.score >= 0 && task.score <= 1);
      assert.ok(task.efficiency_score >= 0 && task.efficiency_score <= task.score);
    }
    const hidden = model.private_aggregates;
    assert.deepEqual(Object.keys(hidden).sort(), ['category_scores', 'task_count']);
    assert.equal(hidden.task_count, 10);
    assert.equal(Object.values(hidden.category_scores).reduce((sum, category) => sum + category.task_count, 0), 10);
    let quality = 0, efficient = 0;
    for (const [category, weight] of Object.entries(data.weights)) {
      const publicResults = model.per_task_results.filter(task => task.category === category);
      const aggregate = hidden.category_scores[category];
      assert.deepEqual(Object.keys(aggregate).sort(), ['task_count', 'quality_score', 'efficiency_score'].sort());
      const count = publicResults.length + aggregate.task_count;
      const credit = (publicResults.reduce((sum, task) => sum + task.score, 0) * 100
        + aggregate.quality_score * aggregate.task_count) / count;
      const efficiency = (publicResults.reduce((sum, task) => sum + task.efficiency_score, 0) * 100
        + aggregate.efficiency_score * aggregate.task_count) / count;
      near(model.category_scores[category], credit);
      quality += weight * credit / 100;
      efficient += weight * efficiency / 100;
    }
    near(model.quality_score, quality);
    near(model.efficiency_score, efficient);
    near(model.overall_score, .95 * quality + .05 * efficient);
    near(replayScore(model, new Set(model.per_task_results.map((_, index) => index)), data.weights), model.overall_score);
  }
});

test('v2 is opt-in and defaults to the latest family model with v2 results', () => {
  const catalog = makePackCatalog(v1, data, { v1: [], v2: tasks }, []);
  assert.equal(resolvePack(null, catalog), 'v1');
  assert.equal(resolvePack('v2', catalog), 'v2');
  const latest = selectModelVersions(catalog.v2.models);
  assert.ok(!latest.some(model => model.model === 'Qwen-Ambassador/Qwen3.8-Max'));
  const qwen = latest.find(model => model.model === 'Qwen-Ambassador/Qwen3.7-Max');
  assert.equal(qwen.version_status, 'Latest');
  assert.equal(qwen.not_run, undefined);
  assert.ok(selectModelVersions(catalog.v2.models, true).some(model => model.model === 'Qwen-Ambassador/Qwen3.8-Max' && model.not_run));
  assert.ok(!selectModelVersions(catalog.v1.models).some(model => model.model === 'Qwen-Ambassador/Qwen3.7-Max'));
});
