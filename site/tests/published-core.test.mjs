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

test('Core offline regrade covers every saved response without inventing repairs', async () => {
  assert.match(data.publication_note, /beda2d5/);
  for (const [directory, repairs] of Object.entries({
    'claude-sonnet-5-5': 24, 'Qwen-Ambassador-Qwen4-Max': 16,
    'deepseek-v4-pro': 29, 'deepseek-flash': 27,
  })) {
    const metadata = JSON.parse(await readFile(new URL(`${directory}/metadata.json`, runs)));
    const note = metadata.regraded.at(-1);
    assert.match(note.harness_commit, /^beda2d5/);
    assert.equal(note.grader, 'all_core_saved_outputs');
    assert.equal(note.initial_attempts_regraded, 104);
    assert.equal(note.recorded_repairs_regraded, repairs);
    assert.equal(note.repairs_not_attempted, 104 - repairs);
    const attempts = (await readFile(new URL(`${directory}/attempts.jsonl`, runs), 'utf8'))
      .trim().split('\n').map(JSON.parse);
    assert.ok(attempts.every(attempt => attempt.offline_regrades.at(-1).initial_regraded));
    assert.equal(attempts.filter(attempt => attempt.offline_regrades.at(-1).recorded_repair_regraded).length, repairs);
  }
});

test('Core publishes complete public Sonnet, DeepSeek and Qwen runs with current provenance', async () => {
  assert.equal(data.pack_id, 'p95-core');
  assert.equal(data.pack_digest, 'f07f902e5bf1542afa91818eb962abbdb4873c8999983f17ff7a983a685fc9de');
  assert.equal(data.budget_file_sha256, budgetHash);
  assert.match(data.publication_note, /45066297860/);
  assert.match(data.publication_note, /final answer after the one allowed repair/);
  assert.doesNotMatch(JSON.stringify(data), /5a5d088256624100/);
  assert.deepEqual((await readdir(runs)).sort(), ['Qwen-Ambassador-Qwen4-Max', 'claude-sonnet-5-5', 'deepseek-flash', 'deepseek-v4-pro']);
  assert.deepEqual(data.models.map(model => model.model).sort(), ['Qwen-Ambassador/Qwen4-Max', 'claude-sonnet-5-5', 'deepseek-flash', 'deepseek-v4-pro']);
  const model = data.models.find(model => model.provider === 'anthropic');
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
  near(model.overall_score, 67.03757251997128);
  near(model.after_repair.overall_score, 83.41449414223732);
  near(model.overall_score, .95 * model.quality_score + .05 * model.efficiency_score);
  const displayed = boardModels(data).find(model => model.key === 'anthropic/claude-sonnet-5-5');
  assert.equal(displayed.score.toFixed(1), '83.4');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  assert.equal(displayed.score, model.after_repair.overall_score);
});

for (const expected of [
  { model: 'deepseek-v4-pro', first: 44.08062990302476, after: 48.16969228667636,
    cost: 14.505640776, repairs: 29 },
  { model: 'deepseek-flash', first: 53.14515366802733, after: 62.993472060174604,
    cost: 5.054729706, repairs: 27 },
]) test(`Core ${expected.model} preserves matching manifest, public coverage, usage costs and after-repair display`, async () => {
  const model = data.models.find(model => model.model === expected.model);
  assert.equal(model.provider, 'deepseek');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.output_cap, 64000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.match(data.publication_note, /45082823742/);
  assert.match(model.cost_note, /45082823742/);
  assert.ok(model.cost_note.includes(`${expected.repairs} repair responses`));
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.harness_commit === model.harness_commit));
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, expected.first);
  near(model.after_repair.overall_score, expected.after);
  near(model.cost, expected.cost);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, expected.repairs);
  const displayed = boardModels(data).find(model => model.key === `deepseek/${expected.model}`);
  assert.equal(displayed.score.toFixed(1), expected.after.toFixed(1));
  assert.equal(displayed.score, model.after_repair.overall_score);
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
});

test('Flash provenance identifies the rerun Gatling shard without adding failed-run usage', () => {
  const model = data.models.find(model => model.model === 'deepseek-flash');
  assert.match(model.cost_note, /attempt 2 after an IncompleteRead transport failure in attempt 1/);
  assert.match(data.publication_note, /Flash's Gatling shard uses attempt 2/);
  assert.ok(model.derived_from.some(source => source.run === '20261123T020612Z-9edc7cba'));
});

test('Qwen Core keeps public list pricing, complete coverage and after-repair scoring', async () => {
  const model = data.models.find(model => model.model === 'Qwen-Ambassador/Qwen4-Max');
  assert.equal(model.provider, 'qwen');
  assert.equal(model.pack_digest, data.pack_digest);
  assert.equal(model.budget_file_sha256, budgetHash);
  assert.equal(model.output_cap, 64000);
  assert.equal(model.reasoning_mode, 'thinking-high');
  assert.equal(model.harness_commit, '2d6e43f309d0ef51698fee94e503439cdb4b09a6');
  assert.equal(model.derived_from.length, 4);
  assert.ok(model.derived_from.every(source => source.spend_cap_usd === null && source.harness_commit === model.harness_commit));
  assert.match(model.cost_note, /ModelScope free tier/);
  assert.match(model.cost_note, /45113245263/);
  assert.match(data.publication_note, /Qwen ran uncapped/);
  assert.deepEqual(model.coverage, { run: 104, total: 104, partial: false, label: '104/104 tasks run' });
  const { tasks } = await loadTaskData({ packId: 'p95-core' });
  assert.equal(model.per_task_results.length, 104);
  assert.deepEqual(new Set(model.per_task_results.map(task => task.task_id)), new Set(tasks.map(task => task.id)));
  near(model.overall_score, 69.11477483048039);
  near(model.after_repair.overall_score, 77.01952211965103);
  near(model.cost, 12.0328038);
  near(model.cost_attempts.reduce((sum, attempt) => sum + attempt.cost_usd, 0), model.cost);
  assert.equal(model.cost_attempts.filter(attempt => attempt.status === 'repair').length, 16);
  const displayed = boardModels(data).find(model => model.key === 'qwen/Qwen-Ambassador/Qwen4-Max');
  assert.equal(displayed.score.toFixed(1), '77.0');
  assert.deepEqual(displayed.categories, model.after_repair.category_scores);
  near(Object.values(displayed.toolCosts).reduce((sum, cost) => sum + cost, 0), model.cost);
  near(displayed.toolCosts.jmeter, 4.8591948);
  near(displayed.toolCosts.k6, 1.7839137);
  near(displayed.toolCosts.locust, 1.7608776);
  near(displayed.toolCosts.gatling, 3.6288177);
});

test('published Core data reproduces from stored runs apart from documented annotations', async () => {
  const rebuilt = await buildData((await readdir(runs)).map(model => new URL(`${model}/`, runs).pathname), { packId: 'p95-core' });
  const { budget_file_sha256, publication_note, models, ...rest } = data;
  const unannotated = { ...rest, models: models.map(({ budget_file_sha256, ...model }) => model) };
  assert.deepEqual(unannotated, rebuilt);
});
