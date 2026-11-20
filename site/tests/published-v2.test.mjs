import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { loadTaskData } from '../scripts/build-data.mjs';

const data = JSON.parse(await readFile(new URL('../src/data/leaderboard-p95-v2.0.0.json', import.meta.url)));
const { tasks } = await loadTaskData({ packId: 'p95-v2.0.0' });
const PUBLISHED = ['deepseek-flash', 'deepseek-v4-pro'];
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test('published v2 results cover the 40 public tasks with no held-out aggregates', () => {
  assert.equal(data.pack_id, 'p95-v2.0.0');
  assert.equal(data.budget_file_sha256, '2375e3bd11758aa5cd71964f291572ed47b034a08cb346558707ed874fe349b7');
  assert.deepEqual(new Set(data.models.map(model => model.model)), new Set(PUBLISHED));
  assert.equal(tasks.length, 40);
  for (const model of data.models) {
    assert.equal(model.repeats, 1);
    assert.equal(model.output_cap, 16384);
    assert.equal(model.private_aggregates, undefined);
    assert.equal(model.evaluator_segments, undefined);
    assert.deepEqual(model.coverage, { run: 40, total: 40, partial: false, label: '40/40 tasks run' });
    assert.match(model.cost_note, /list-price estimate/);
    assert.match(model.harness_commit, /^[0-9a-f]{40}$/);
    assert.ok(Number.isFinite(model.cost) && model.cost >= 0);
    assert.equal(model.per_task_results.length, 40);
    assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
    let quality = 0;
    for (const [category, weight] of Object.entries(data.weights)) {
      const results = model.per_task_results.filter(task => task.category === category);
      const credit = results.reduce((sum, task) => sum + task.score, 0) * 100 / results.length;
      near(model.category_scores[category], credit);
      quality += weight * credit / 100;
    }
    near(model.quality_score, quality);
    near(model.overall_score, .95 * model.quality_score + .05 * model.efficiency_score);
  }
});
