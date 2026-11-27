import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import {
  boardModels, boardTasks, formatCost, palette,
  scatter, taskCode, taskLabel, taskScore, reasoningLabel, sharedReasoningLabel,
} from '../src/lib/board.mjs';
import { fakeV2 } from './fake-v2.mjs';

test('models sort by overall score and receive palette colors by rank', () => {
  const models = boardModels(fakeV2());

  assert.deepEqual(models.map(model => model.score), [...models.map(model => model.score)].sort((a, b) => b - a));
  assert.deepEqual(models.map(model => model.color), models.map((_, rank) => palette[rank % palette.length]));
});

test('tasks follow category weights, sort by id and deduplicate', () => {
  const data = fakeV2();
  const firstTask = data.models[0].per_task_results[0];
  data.models[0].per_task_results.push(
    { ...firstTask, task_id: 'v2-r02-stale-cookie' },
    { ...firstTask, task_id: 'v2-r00-first' },
  );
  const tasks = boardTasks(data);

  assert.deepEqual(tasks.map(task => task.id), [
    'v2-r00-first', 'v2-r01-extractor-scope', 'v2-r02-stale-cookie', 'v2-m01-basic-flow', 'v2-c01-html-csrf',
    'v2-w01-steady-starts', 'v2-a01-jtl-endpoints',
  ]);
  assert.equal(new Set(tasks.map(task => task.id)).size, tasks.length);
});

test('task labels and codes omit the v2 category prefix', () => {
  assert.equal(taskLabel('v2-a01-jtl-endpoints'), 'jtl endpoints');
  assert.equal(taskCode('v2-a01-jtl-endpoints'), 'A01');
});

test('task scores average repetitions and use pass booleans when scores are absent', () => {
  const model = { tasks: [
    { task_id: 'repeated', score: 0 }, { task_id: 'repeated', score: 1 },
    { task_id: 'passed', passed: false }, { task_id: 'passed', passed: true },
  ] };

  assert.equal(taskScore(model, 'repeated'), 50);
  assert.equal(taskScore(model, 'passed'), 50);
  assert.equal(taskScore(model, 'unknown'), null);
});

test('board score is the final scored answer, including the one allowed fix', () => {
  const data = fakeV2();
  const source = data.models[0];
  source.after_repair = { overall_score: 91, quality_score: 92, efficiency_score: 72,
    category_scores: { repair: 92 } };
  source.repair_rounds = 1;
  source.reasoning_mode = 'thinking';
  source.derived_from = [{ pack_id: 'p95-matrix', run: 'matrix-run' }];
  source.per_task_results[0].after_repair_score = .9;
  const model = boardModels(data).find(item => item.key === `${source.provider}/${source.model}`);
  const taskId = source.per_task_results[0].task_id;

  assert.equal(model.score, 91);
  assert.deepEqual(model.categories, { repair: 92 });
  assert.equal(model.reasoningMode, 'thinking');
  assert.deepEqual(model.derivedFrom, source.derived_from);
  assert.equal(taskScore(model, taskId, null), 90);
  assert.equal('afterRepair' in model, false);
});

test('models without a fix round keep their recorded score', () => {
  const data = fakeV2();
  for (const model of boardModels(data)) {
    const source = data.models.find(item => `${item.provider}/${item.model}` === model.key);
    assert.equal(model.score, source.overall_score);
  }
});

test('scatter plots priced models with x increasing by cost and y tracking score', () => {
  const models = boardModels(fakeV2());
  const points = scatter(models).points;

  assert.equal(points.length, models.filter(model => model.cost > 0).length);
  for (let index = 1; index < points.length; index++) {
    assert.ok(points[index].x > points[index - 1].x);
    assert.ok(points[index].cost > points[index - 1].cost);
    assert.ok(points[index].score < points[index - 1].score);
    assert.ok(points[index].y > points[index - 1].y);
  }
  assert.deepEqual(scatter(models.map(model => ({ ...model, cost: 0 }))).points, []);
});

test('scatter labels every model without rectangular label collisions', () => {
  const crowded = Array.from({ length: 8 }, (_, index) => ({
    key: `m${index}`, name: `Long Model Name ${index}`, cost: 5 + index * .01,
    score: 80 - index, color: '#fff',
  }));
  const plot = scatter(crowded);

  assert.equal(plot.points.length, crowded.length);
  for (const point of plot.points) {
    assert.ok(Number.isFinite(point.labelX) && Number.isFinite(point.labelY));
    assert.ok(point.labelX + point.labelWidth <= plot.width);
    assert.ok(point.labelY >= plot.top && point.labelY <= plot.height - plot.bottom);
  }
  for (let a = 0; a < plot.points.length; a++) {
    for (let b = a + 1; b < plot.points.length; b++) {
      const first = plot.points[a], second = plot.points[b];
      const horizontal = Math.min(first.labelX + first.labelWidth, second.labelX + second.labelWidth)
        - Math.max(first.labelX, second.labelX);
      const vertical = Math.min(first.labelY + 4, second.labelY + 4)
        - Math.max(first.labelY - 11, second.labelY - 11);
      assert.ok(horizontal <= 0 || vertical <= 0,
        `${first.name} overlaps ${second.name}`);
    }
  }
});

test('scatter separates same-height labels clamped at the top edge', () => {
  const crowded = Array.from({ length: 9 }, (_, index) => ({
    key: `top${index}`, name: `Long Model Name ${index}`, cost: 5 + index * .001,
    score: 99, color: '#fff',
  }));
  const points = scatter(crowded).points;

  for (let a = 0; a < points.length; a++) {
    for (let b = a + 1; b < points.length; b++) {
      const first = points[a], second = points[b];
      const horizontal = Math.min(first.labelX + first.labelWidth, second.labelX + second.labelWidth)
        - Math.max(first.labelX, second.labelX);
      const vertical = Math.min(first.labelY + 4, second.labelY + 4)
        - Math.max(first.labelY - 11, second.labelY - 11);
      assert.ok(horizontal <= 0 || vertical <= 0,
        `${first.name} overlaps ${second.name}`);
    }
  }
});


test('reasoning labels normalize high mode and only summarize uniform charts', () => {
  assert.equal(reasoningLabel('thinking-high'), 'High reasoning');
  assert.equal(reasoningLabel('thinking-medium'), 'thinking medium');
  assert.equal(reasoningLabel(null), null);
  assert.equal(sharedReasoningLabel([
    { reasoningMode: 'thinking-high' }, { reasoningMode: 'thinking-high' },
  ]), 'High reasoning');
  assert.equal(sharedReasoningLabel([
    { reasoningMode: 'thinking-high' }, { reasoningMode: 'thinking-medium' },
  ]), null);
  assert.equal(sharedReasoningLabel([
    { reasoningMode: 'thinking-high' }, { reasoningMode: null },
  ]), null);
});


test('shared chart reasoning uses plotted models only', () => {
  const models = boardModels(fakeV2()).map((model, index) => ({
    ...model,
    reasoningMode: index === 0 ? 'thinking-medium' : 'thinking-high',
  }));
  models[0].cost = 0;
  for (const model of models.slice(1)) model.reasoningMode = 'thinking-high';

  assert.equal(sharedReasoningLabel(models), null);
  assert.equal(sharedReasoningLabel(scatter(models).points), 'High reasoning');
});

test('cost formatter handles basic and non-finite values', () => {
  assert.equal(formatCost(1.234), '$1.23');
  assert.equal(formatCost(Infinity), '—');
});

test('tool costs include first tries, repairs and charged not-run responses', () => {
  const data = fakeV2();
  const source = data.models[0];
  const jmeter = source.per_task_results.find(task => task.tool === 'jmeter').task_id;
  const k6 = source.per_task_results.find(task => task.tool === 'k6').task_id;
  source.cost_attempts = [
    { task_id: jmeter, cost_usd: 1 },
    { task_id: jmeter, cost_usd: .5, status: 'repair' },
    { task_id: jmeter, cost_usd: .25, status: 'not_run' },
    { task_id: k6, cost_usd: 0 },
  ];
  const model = boardModels(data).find(item => item.key === `${source.provider}/${source.model}`);
  assert.deepEqual(model.toolCosts, { jmeter: 1.75, k6: 0 });
  assert.equal(formatCost(model.toolCosts.locust), '—');
  source.cost_attempts = [];
  assert.deepEqual(boardModels(data).find(item => item.key === model.key).toolCosts, {});
});

test('pages use the Core leaderboard and task pack without Matrix-only copy', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const jsonImports = [...page.matchAll(/import\s+[^;]*from\s+["']([^"']+\.json)["'];?/g)]
    .map(([, source]) => source);

  assert.deepEqual(jsonImports, ['../data/leaderboard-p95-core.json']);
  assert.doesNotMatch(page, /leaderboard\.json|pack-switch|show-versions/);
  assert.match(page, /Core · \{tasks\.length\} scenarios × \{tools\.length\} tools = \{tasks\.length \* tools\.length\} tasks/);
  assert.doesNotMatch(page, /40 scenarios/);
  assert.doesNotMatch(page, /JMeter three-phase workload reference/);
  assert.match(page, /href="\/tasks"/);
  assert.match(page, /class="reasoning-context" id="reasoning-context"/);
  assert.match(page, /used for every model in this chart/);
  assert.match(page, /aria-describedby=\{sharedReasoning \? "reasoning-context chart-note" : "chart-note"\}/);
  assert.match(page, /sharedReasoningLabel\(plot\.points\)/);
  assert.match(page, /reasoningLabel\(p\.reasoningMode\)/);
  assert.doesNotMatch(page, /model\.tasks\.find\(/);
  assert.doesNotMatch(page, /First try|After 1 repair|score-toggle|data-score/);
  assert.doesNotMatch(page, />Every task <span|class="grid"|Output tokens/);
  const tasksPage = await readFile(new URL('../src/pages/tasks.astro', import.meta.url), 'utf8');
  assert.match(tasksPage, /leaderboard-p95-core\.json/);
  assert.match(tasksPage, /packs\/p95-core\/tasks/);
  assert.match(tasksPage, /<span class="lime">\{tasks\.length\}<\/span> scenarios × \{tools\.length\} tools = \{tasks\.length \* tools\.length\} tasks/);
  assert.doesNotMatch(tasksPage, /First try|After 1 repair|data-score|Solved|boardModels/);
});
