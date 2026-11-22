import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile, readdir } from 'node:fs/promises';
import { test } from 'node:test';
import { buildData, loadTaskData } from '../scripts/build-data.mjs';
import { boardModels } from '../src/lib/board.mjs';

const data = JSON.parse(await readFile(new URL('../src/data/leaderboard-p95-core.json', import.meta.url)));
const runs = new URL('../../results/p95-core/', import.meta.url);
const budget = await readFile(new URL('../../packs/p95-core/efficiency-budgets.json', import.meta.url));
const budgetHash = createHash('sha256').update(budget).digest('hex');
const near = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test('Core publishes only the complete public Sonnet run with current provenance', async () => {
  assert.equal(data.pack_id, 'p95-core');
  assert.equal(data.pack_digest, 'f07f902e5bf1542afa91818eb962abbdb4873c8999983f17ff7a983a685fc9de');
  assert.equal(data.budget_file_sha256, budgetHash);
  assert.match(data.publication_note, /45066297860/);
  assert.match(data.publication_note, /first answer/);
  assert.doesNotMatch(JSON.stringify(data), /deepseek|5a5d088256624100/);
  assert.deepEqual(await readdir(runs), ['claude-sonnet-5-5']);
  assert.deepEqual(data.models.map(model => model.model), ['claude-sonnet-5-5']);
  const [model] = data.models;
  assert.equal(model.provider, 'anthropic');
  assert.equal(model.output_cap, 64000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /list-price estimate/);
  assert.match(model.cost_note, /45066297860/);
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.cost, 19.925888);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 24);
  near(model.overall_score, 62.6305997442996);
  near(model.overall_score, .95 * model.quality_score + .05 * model.efficiency_score);
  const [displayed] = boardModels(data);
  assert.equal(displayed.score.toFixed(1), '62.6');
  assert.deepEqual(displayed.categories, model.category_scores);
  assert.notEqual(displayed.score, model.after_repair.overall_score);
});

test('published Core data reproduces from stored runs apart from documented annotations', async () => {
  const rebuilt = await buildData([new URL('claude-sonnet-5-5/', runs).pathname], { packId: 'p95-core' });
  const { budget_file_sha256, publication_note, models, ...rest } = data;
  const unannotated = { ...rest, models: models.map(({ budget_file_sha256, ...model }) => model) };
  assert.deepEqual(unannotated, rebuilt);
});
