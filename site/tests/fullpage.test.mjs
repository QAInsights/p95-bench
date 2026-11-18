import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { budgetScale, replayLayout, replayScore, replayToolLayout, replayToolCounts, replaySampleLabel } from '../src/lib/scores.mjs';
import { taskCoverage } from '../src/lib/scores.mjs';

const published = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));

test('stack and replay score cells reserve tabular single-line widths at every breakpoint', async () => {
  const css = await readFile(new URL('../src/styles/global.css', import.meta.url), 'utf8');
  for (const selector of ['.bar-value', '.lane-score']) {
    const rule = css.slice(css.indexOf(selector + ' {')).split('}')[0];
    assert.match(rule, /white-space: nowrap/);
    assert.match(rule, /font-variant-numeric: tabular-nums/);
    assert.match(rule, /(?:min-)?inline-size: 10ch/);
  }
  assert.match(css, /#flames\s*\{[^}]*grid-auto-rows: 1fr/);
  const columns = [...css.matchAll(/\.flame-row\s*\{[^}]*grid-template-columns: ([^;]+)/g)];
  assert.equal(columns.length, 3);
  for (const [, tracks] of columns) assert.match(tracks, /minmax\(0, 1fr\) max-content$/);
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.sample-head,\s*\.lane\s*\{\s*grid-template-columns: 24px 170px minmax\(0, 1fr\) 17rem/);
});

test('task coverage marks missing tasks as partial without counting duplicates or unknown ids', () => {
  const tasks = [{ id: 'a' }, { id: 'b' }];
  const model = { per_task_results: [{ task_id: 'a' }, { task_id: 'a' }, { task_id: 'unknown' }] };
  assert.deepEqual(taskCoverage(model, tasks), { run: 1, total: 2, partial: true, label: '1/2 tasks run' });
  assert.equal(taskCoverage({ per_task_results: [] }, tasks).partial, true);
  assert.equal(taskCoverage({ per_task_results: [{ task_id: 'a' }, { task_id: 'b' }] }, tasks).partial, false);
  assert.deepEqual(taskCoverage(model, []), { run: 0, total: 0, partial: false, label: '0/0 tasks run' });
});

test('tool blocks sort by metadata, retain task identity and repeated occurrences, and include future and unknown tools', () => {
  const tasks = [
    ['load', 'k6'], ['z', 'zebra'], ['plain', 'none'], ['plan', 'jmeter'],
    ['future', 'locust'], ['simulation', 'gatling'], ['a', 'alpha'], ['load', 'k6'],
    ['implicit'], ['empty', ''], ['special', 'constructor'],
  ].map(([task_id, tool]) => ({ task_id, tool, passed: true }));
  const models = [{ per_task_results: tasks }, { per_task_results: [tasks[3], tasks[0], tasks[2]] }];
  const layout = replayToolLayout(models);
  assert.deepEqual(layout.groups.map(group => [group.tool, group.label, group.count]), [
    ['none', 'General', 3], ['jmeter', 'JMeter', 1], ['k6', 'k6', 2],
    ['gatling', 'Gatling', 1], ['locust', 'Locust', 1],
    ['alpha', 'alpha', 1], ['constructor', 'constructor', 1], ['zebra', 'zebra', 1],
  ]);
  assert.deepEqual(layout.taskIds, ['plain', 'implicit', 'empty', 'plan', 'load', 'load',
    'simulation', 'future', 'a', 'special', 'z']);
  assert.deepEqual(layout.rows[1], [2, null, null, 0, 1, null, null, null, null, null, null]);
  assert.deepEqual(layout.groups.map(group => group.column), [1, 5, 7, 10, 12, 14, 16, 18]);
  layout.rows.forEach((row, i) => row.forEach((index, column) => {
    if (index !== null) assert.equal(models[i].per_task_results[index].task_id, layout.taskIds[column]);
  }));
  assert.deepEqual(layout.rows[0].filter(index => tasks[index].task_id === 'load'), [0, 7]);
});

test('tool pass counts distinguish failures, partial coverage and tools with no attempts', () => {
  const models = [
    { per_task_results: [
      { task_id: 'plain', passed: true }, { task_id: 'plan', tool: 'jmeter', passed: false },
      { task_id: 'load-a', tool: 'k6', passed: true }, { task_id: 'load-b', tool: 'k6', passed: false },
    ] },
    { per_task_results: [{ task_id: 'plain', tool: 'none', passed: true }] },
    { per_task_results: [{ task_id: 'load-b', tool: 'k6', passed: true }] },
    { per_task_results: [] },
  ];
  const { groups } = replayToolLayout(models);
  assert.deepEqual(replayToolCounts(models[0], groups).map(count => count.text),
    ['General 1/1', 'JMeter 0/1', 'k6 1/2']);
  assert.deepEqual(replayToolCounts(models[1], groups).map(count => count.text),
    ['General 1/1', 'JMeter Not run', 'k6 Not run']);
  assert.deepEqual(replayToolCounts(models[2], groups).at(-1),
    { tool: 'k6', label: 'k6', passed: 1, attempts: 1, total: 2, text: 'k6 1/2' });
  assert.ok(replayToolCounts(models[3], groups).every(count => count.text.endsWith('Not run')));
  assert.deepEqual(replayToolLayout([]), { taskIds: [], rows: [], groups: [] });
  assert.deepEqual(replayToolLayout([models[3]]), { taskIds: [], rows: [[]], groups: [] });
  assert.deepEqual(replayToolCounts(models[0], []), []);
});

test('published replay tools have nine General, three JMeter and three k6 aligned columns', () => {
  const { groups, rows } = replayToolLayout(published.models);
  assert.deepEqual(groups.map(group => [group.label, group.count]), [['General', 9], ['JMeter', 3], ['k6', 3]]);
  assert.equal(rows.flat().filter(index => index === null).length, 0);
});

test('sample descriptions expose task names, tools and all three result states without color', () => {
  assert.equal(replaySampleLabel({ task_name: 'Shop flow', passed: true }, 'shop', 'k6'), 'Shop flow · k6 · Pass');
  assert.equal(replaySampleLabel({ passed: false }, 'plan', 'JMeter'), 'plan · JMeter · Fail');
  assert.equal(replaySampleLabel(null, 'future', 'unknown'), 'future · unknown · Not run');
});

test('replay columns align interleaved, appended and partial task orders by identity, including repeats', () => {
  const models = [
    ['analysis', 'k6-shop', 'shop', 'k6-session', 'session', 'analysis'],
    ['analysis', 'shop', 'session', 'k6-shop', 'k6-session', 'analysis'],
    ['session', 'analysis', 'shop'],
  ].map(tasks => ({ per_task_results: tasks.map((task_id, index) => ({ task_id, passed: index % 2 === 0 })) }));
  const { taskIds, rows } = replayLayout(models);
  assert.deepEqual(taskIds, ['analysis', 'k6-shop', 'shop', 'k6-session', 'session', 'analysis']);
  assert.deepEqual(rows, [[0, 1, 2, 3, 4, 5], [0, 3, 1, 4, 2, 5], [1, null, 2, null, 0, null]]);
  rows.forEach((row, modelIndex) => row.forEach((index, column) => {
    if (index !== null) assert.equal(models[modelIndex].per_task_results[index].task_id, taskIds[column]);
  }));
});

test('replay columns include tasks absent from the first model, with empty slots rather than failures', () => {
  const models = [['a', 'b'], ['b', 'c'], []].map(tasks => ({
    per_task_results: tasks.map(task_id => ({ task_id })),
  }));
  assert.deepEqual(replayLayout(models), {
    taskIds: ['a', 'b', 'c'], rows: [[0, 1, null], [null, 0, 1], [null, null, null]],
  });
  assert.deepEqual(replayLayout([]), { taskIds: [], rows: [] });
  assert.deepEqual(replayLayout([{ per_task_results: [] }]), { taskIds: [], rows: [[]] });
});

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
  assert.match(page, /id="pack-data"[^>]*JSON\.stringify\(packCatalog\)/);
  assert.match(client, /JSON\.parse\(document\.getElementById\('pack-data'\)\.textContent\)/);
  assert.doesNotMatch(page + client, /fonts\.googleapis\.com|benchmark-data/);
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
