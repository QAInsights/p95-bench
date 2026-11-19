import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile, readdir } from 'node:fs/promises';
import { selectModelVersions } from '../src/lib/scores.mjs';
import { heatmapData, metricExtent, heatColor, heatValue, flameScale } from '../src/lib/heatmap-data.mjs';
import { taskCategoryGroups, taskHeading, taskTitle } from '../src/lib/task-headings.mjs';
import { replayTools, replayToolStats } from '../src/lib/scores.mjs';
import { loadTaskData } from '../scripts/build-data.mjs';
const models = [{ model: 'test', provider: 'gemini', overall_score: 50, cost: 1 }];
const tasks = [{ id: 'k', tool: 'k6' }, { id: 'a', tool: 'none' }, { id: 'j', tool: 'jmeter' }];
const record = (passed, tokens, latency) => ({ provider: 'gemini', model: 'test', task_id: 'a', grade: { passed },
  response: { output_tokens: tokens, latency_seconds: latency } });

test('v2 replay collates every task into tool cells while the heatmap keeps all 40 ordinals', async () => {
  const data = JSON.parse(await readFile(new URL('../src/data/leaderboard-p95-v2.0.0.json', import.meta.url), 'utf8'));
  const { tasks } = await loadTaskData({ packId: 'p95-v2.0.0' });
  const { columns } = heatmapData([], tasks.toSorted((a, b) => a.id.localeCompare(b.id)), []);
  assert.equal(columns.length, 40);
  const tools = replayTools(data.models, tasks);
  const stats = tools.map(tool => replayToolStats(data.models[0], tasks, tool));
  assert.equal(stats.reduce((sum, stat) => sum + stat.total, 0), 40);
  const replay = await readFile(new URL('../src/lib/fullpage.mjs', import.meta.url), 'utf8');
  assert.ok(replay.includes('type="button" class="run-cell"'));
  assert.ok(replay.includes("cell.addEventListener('pointerenter', () => showRunTip(cell, i, tool))"));
  assert.ok(replay.includes("cell.addEventListener('focus', () => showRunTip(cell, i, tool))"));
  assert.ok(replay.includes('replayToolDescription(models[i], DATA.tasks, tool)'));
});

test('v2 task headings group all 40 columns by tool/category and retain full IDs and titles', async () => {
  const { tasks } = await loadTaskData({ packId: 'p95-v2.0.0' });
  const { columns } = heatmapData([], tasks, []);
  const groups = taskCategoryGroups(columns);
  assert.equal(columns.length, 40);
  assert.equal(groups.reduce((sum, group) => sum + group.count, 0), 40);
  assert.deepEqual(new Set(groups.map(group => group.label)), new Set(['Analysis', 'Correlation', 'Migration', 'Repair', 'Workload']));
  let offset = 0;
  for (const group of groups) {
    assert.ok(columns.slice(offset, offset + group.count).every(task => task.tool === group.tool && task.category === group.category));
    offset += group.count;
  }
  assert.ok(columns.every(task => taskHeading(task).includes(task.id) && task.title.length > 0));
  assert.equal(taskHeading({ id: 'v2-r01-extractor-scope' }), 'v2-r01-extractor-scope · extractor scope');
  assert.equal(taskTitle({ id: 'a', title: 'Endpoint report', name: 'Other' }), 'Endpoint report');
  assert.equal(taskTitle({ id: 'a', name: 'Endpoint report' }), 'Endpoint report');
  assert.deepEqual(taskCategoryGroups([]), []);
  assert.deepEqual(taskCategoryGroups([{ id: 'a' }]).map(group => group.label), ['Tasks']);
  const component = await readFile(new URL('../src/components/Heatmap.astro', import.meta.url), 'utf8');
  const client = await readFile(new URL('../src/lib/heatmap.mjs', import.meta.url), 'utf8');
  const replay = await readFile(new URL('../src/lib/fullpage.mjs', import.meta.url), 'utf8');
  assert.ok(component.includes('taskCategoryGroups(data.columns)'));
  assert.ok(component.includes('title={taskHeading(task)} aria-label={taskHeading(task)}'));
  assert.ok(component.includes('v2 ? index + 1 : shortTaskLabel(task.id)'));
  assert.ok(client.includes('taskHeading(data.columns[Number(node.dataset.column)])'));
  assert.ok(replay.includes('class="task-category-list"'));
  assert.ok(replay.includes('replayToolStats(models[i], DATA.tasks, tool, arrived[i])'));
});

test('heatmap groups task columns by tool, preserves ranking, averages repeated measured attempts and shares provider names', () => {
  const { columns, rows } = heatmapData(models, tasks, [record(true, 10, 2), record(false, 30, 6), record(null, 0, 0)]);
  assert.deepEqual(columns.map(task => task.id), ['a', 'j', 'k']);
  assert.equal(rows[0].provider, 'Google');
  assert.deepEqual(rows[0].cells[0], { task: 'a', attempts: 3, measured: 2, result: .5, tokens: 20, latency: 4 });
  assert.equal(rows[0].cells[1].result, null);
  assert.equal(heatValue(rows[0].cells[0], 'result'), '50.0% pass');
  assert.equal(heatValue(rows[0].cells[0], 'latency'), '4.00 s');
  const ranked = heatmapData([...models, { ...models[0], model: 'better', overall_score: 100 }], tasks, []).rows;
  assert.equal(ranked[0].id, 'better');
});

test('scales cover exact means, exclude invalid/missing measurements and handle zero, equal and empty domains', () => {
  const { rows } = heatmapData(models, tasks, [record(true, 0, 0), record(false, 20, 4), { ...record(true, 100, 10), task_id: 'j' }]);
  assert.deepEqual(metricExtent(rows, 'tokens'), { min: 10, max: 100 });
  assert.equal(heatColor(10, 'tokens', metricExtent(rows, 'tokens')), flameScale[0]);
  assert.equal(heatColor(100, 'tokens', metricExtent(rows, 'tokens')), flameScale.at(-1));
  assert.equal(heatColor(0, 'latency', { min: 0, max: 0 }), flameScale[2]);
  assert.equal(heatColor(null, 'result', {}), null);
  assert.deepEqual(metricExtent([], 'latency'), { min: null, max: null });
  assert.deepEqual(heatmapData([], [], []), { columns: [], rows: [] });
  const bad = heatmapData(models, tasks, [record(true, -1, NaN)]).rows;
  assert.equal(bad[0].cells[0].tokens, null);
  assert.equal(heatValue(bad[0].cells[1], 'result'), 'Not run');
  assert.throws(() => metricExtent([], 'invalid'), /Unknown heatmap metric/);
  assert.throws(() => heatColor(1, 'invalid', {}), /Unknown heatmap metric/);
  assert.throws(() => heatValue({}, 'invalid'), /Unknown heatmap metric/);
});

test('published heatmap reproduces every model output total, attempt count, ranking and earlier-version selection', async () => {
  const base = new URL('../../results/published/', import.meta.url);
  const directories = (await readdir(base, { withFileTypes: true })).filter(entry => entry.isDirectory());
  const attempts = (await Promise.all(directories.map(entry => readFile(new URL(`${entry.name}/attempts.jsonl`, base), 'utf8'))))
    .flatMap(source => source.split('\n').filter(line => line.trim()).map(line => JSON.parse(line)));
  const published = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url), 'utf8'));
  const catalog = [...new Map(published.models.flatMap(model => model.per_task_results.map(task => [task.task_id, { id: task.task_id, tool: task.tool }]))).values()];
  const { rows } = heatmapData(published.models, catalog, attempts);
  for (const earlier of [false, true]) assert.deepEqual(rows.filter(row => earlier || row.latest).map(row => row.id),
    selectModelVersions(published.models, earlier).map(model => model.model));
  for (const row of rows) {
    const model = published.models.find(model => model.model === row.id);
    assert.equal(row.cells.reduce((sum, cell) => sum + cell.attempts, 0), model.per_task_results.length);
    assert.ok(Math.abs(row.cells.reduce((sum, cell) => sum + (cell.tokens ?? 0) * cell.measured, 0) - model.tokens.output) < 1e-8);
  }
});
