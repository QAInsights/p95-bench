import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { loadTaskData } from '../scripts/build-data.mjs';
import { scoreAttempts } from '../src/lib/scores.mjs';
import { executionShapes, simulatorRows, setTaskState, attemptCost } from '../src/lib/explainer-data.mjs';
import { sectionMetadata, sectionShortcut, sectionNumber } from '../src/lib/section-navigation.mjs';

const published = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));

test('simulator uses shared scoring and reproduces every published overall and category score', async () => {
  const { tasks, weights } = await loadTaskData();
  for (const model of published.models) {
    const result = simulatorRows(tasks, model.per_task_results, weights);
    assert.ok(Math.abs(result.overall_score - model.overall_score) < 1e-10, model.model);
    assert.deepEqual(result.category_scores, model.category_scores);
    assert.ok(Math.abs(result.rows.reduce((sum, row) => sum + row.points, 0) - model.overall_score) < 1e-10);
    assert.equal(result.measured_weight, Object.keys(model.category_scores).reduce((sum, key) => sum + weights[key], 0));
  }
});

test('not run removes attempts and unmeasured weights, while failure remains measured', () => {
  const tasks = [{ id: 'a', category: 'first' }, { id: 'b', category: 'second' }];
  const weights = { first: 25, second: 75 };
  let values = setTaskState([], tasks[0], 'pass');
  assert.equal(scoreAttempts(values, weights).overall_score, 100);
  values = setTaskState(values, tasks[1], 'fail');
  assert.equal(scoreAttempts(values, weights).overall_score, 25);
  values = setTaskState(values, tasks[1], 'not_run');
  assert.equal(scoreAttempts(values, weights).overall_score, 100);
  const empty = simulatorRows(tasks, [], weights);
  assert.equal(empty.overall_score, 0);
  assert.equal(empty.measured_weight, 0);
  assert.ok(empty.rows.every(row => row.state === 'not_run' && row.points === 0));
  assert.deepEqual(simulatorRows([], [], {}).rows, []);
  assert.throws(() => setTaskState([], tasks[0], 'unknown'), /Unknown task state/);
  assert.throws(() => scoreAttempts([{ category: 'unknown', passed: true }], weights), /Unknown category/);
  assert.equal(scoreAttempts([{ category: 'unknown', passed: null }], weights).overall_score, 0);
});

test('repeated attempts retain their denominator and can be changed or excluded together', () => {
  const task = { id: 'a', category: 'first' };
  const values = [{ task_id: 'a', category: 'first', passed: true },
    { task_id: 'a', category: 'first', passed: false }];
  const rows = simulatorRows([task], values, { first: 25 }).rows;
  assert.deepEqual(rows, [{ id: 'a', state: 'mixed', points: 50, maximum: 100 }]);
  assert.equal(setTaskState(values, task, 'pass').length, 2);
  assert.ok(setTaskState(values, task, 'fail').every(value => value.passed === false));
  assert.equal(setTaskState(values, task, 'not_run').length, 0);
  assert.equal(values[0].passed, true);
});

test('execution diagrams derive their shapes from executable task parameters', async () => {
  const { tasks } = await loadTaskData();
  const shapes = executionShapes(tasks);
  assert.deepEqual(shapes.map(shape => [shape.vus, shape.iterations, shape.completions]).sort(),
    [[1, 1, 1], [1, 2, 2], [2, 1, 2]]);
  assert.deepEqual(executionShapes([]), []);
});

test('real attempt costs reproduce list-price estimates with bounded cached tokens', () => {
  for (const model of published.models) {
    const total = model.cost_attempts.reduce((sum, attempt) => {
      assert.ok(Math.abs(attemptCost(attempt).total - attempt.cost_usd) < 1e-10);
      return sum + attemptCost(attempt).total;
    }, 0);
    assert.ok(Math.abs(total - model.cost) < 1e-10);
  }
  const attempt = { input_tokens: 100, output_tokens: 20, cached_tokens: -20,
    price_per_million: { input: 2, cached: .5, output: 10 } };
  assert.equal(attemptCost(attempt).parts[0].tokens, 100);
  assert.equal(attemptCost({ ...attempt, cached_tokens: 200 }).parts[1].tokens, 100);
  assert.equal(attemptCost({ ...attempt, input_tokens: 0, output_tokens: 0 }).total, 0);
});

test('section rail metadata and shortcuts include every numbered section in DOM order', () => {
  const labels = ['Live run', 'Unit matrix', 'Heatmap', 'Flame stack', 'Value frontier', 'Model focus', 'Scenarios', 'How scoring works', 'Tools', 'Methodology'];
  const sections = labels.map((label, index) => ({ id: `section-${index}`, dataset: { sectionLabel: label } }));
  assert.deepEqual(sectionMetadata(sections).map(section => section.label), labels);
  assert.deepEqual(sectionMetadata(sections).map(section => section.number), ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10']);
  assert.equal(sectionShortcut('5', sections.length), 4);
  assert.equal(sectionShortcut('6', sections.length), 5);
  assert.equal(sectionShortcut('8', sections.length), 7);
  assert.equal(sectionShortcut('9', sections.length), 8);
  assert.equal(sectionShortcut('0', sections.length), 9);
  assert.equal(sectionShortcut('0', 9), null);
  for (const key of ['x', '16']) assert.equal(sectionShortcut(key, sections.length), null);
  assert.equal(sectionShortcut('1', 0), null);
  assert.deepEqual(sectionMetadata([]), []);
  assert.equal(sectionMetadata([{ id: 'future', dataset: {}, querySelector: () => ({ textContent: ' Future section ' }) }])[0].label, 'Future section');
});

test('footer and rail share two-digit section formatting without a third leading digit', async () => {
  assert.deepEqual([1, 9, 10, 11].map(sectionNumber), ['01', '09', '10', '11']);
  const source = await readFile(new URL('../src/lib/fullpage.mjs', import.meta.url), 'utf8');
  assert.match(source, /sectionNumber\(currentScene \+ 1\) \+ " \/ "/);
});

test('explainer is a semantic long section between scenarios and tools without the supplied audit stage', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const component = await readFile(new URL('../src/components/Explainer.astro', import.meta.url), 'utf8');
  assert.ok(page.indexOf('<PackExplainer pack=') > page.indexOf('<Scenarios pack='));
  assert.ok(page.indexOf('<PackExplainer pack=') < page.indexOf('id="tools"'));
  const packComponent = await readFile(new URL('../src/components/PackExplainer.astro', import.meta.url), 'utf8');
  assert.match(packComponent, /pack === 'v1' \? <Explainer tasks=/);
  assert.match(component, /class="reading-section explainer"/);
  assert.equal((component.match(/data-ex-panel/g) ?? []).length, 4);
  assert.match(component, /aria-pressed=/);
  assert.match(component, /Rules we hold/);
  assert.doesNotMatch(component, /audit findings|—/i);
});
