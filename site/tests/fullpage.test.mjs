import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { budgetScale, replayScore, replayTools, replayToolStats, replayCellLabel, replayToolDescription } from '../src/lib/scores.mjs';
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
  assert.match(css, /@media \(max-width: 1000px\)[\s\S]*?\.sample-head,\s*\.lane\s*\{\s*grid-template-columns: 24px 170px minmax\(0, 1fr\) 9rem/);
});

test('task coverage marks missing tasks as partial without counting duplicates or unknown ids', () => {
  const tasks = [{ id: 'a' }, { id: 'b' }];
  const model = { per_task_results: [{ task_id: 'a' }, { task_id: 'a' }, { task_id: 'unknown' }] };
  assert.deepEqual(taskCoverage(model, tasks), { run: 1, total: 2, partial: true, label: '1/2 tasks run' });
  assert.equal(taskCoverage({ per_task_results: [] }, tasks).partial, true);
  assert.equal(taskCoverage({ per_task_results: [{ task_id: 'a' }, { task_id: 'b' }] }, tasks).partial, false);
  assert.deepEqual(taskCoverage(model, []), { run: 0, total: 0, partial: false, label: '0/0 tasks run' });
});

test('replay tools union inventory and results in canonical order with unknown tools appended', () => {
  const tasks = [{ id: 't1', tool: 'k6' }, { id: 't2', tool: 'gatling' }];
  const models = [{ per_task_results: [
    { task_id: 'x', tool: 'JMeter ' }, { task_id: 'y', tool: 'zebra' },
    { task_id: 'z' }, { task_id: 'w', tool: 'locust' },
  ] }];
  assert.deepEqual(replayTools(models, tasks), ['none', 'jmeter', 'k6', 'gatling', 'locust', 'zebra']);
  assert.deepEqual(replayTools([], []), []);
  assert.deepEqual(replayTools([{ per_task_results: [] }], [{ id: 't', tool: 'locust' }]), ['locust']);
});

test('tool cells collate repeats per task, count partial credit and gate on landed attempts', () => {
  const tasks = [
    { id: 'g1', category: 'analysis', tool: 'none' },
    { id: 'g2', category: 'analysis', tool: 'none' },
    { id: 'j1', category: 'scripting', tool: 'jmeter' },
    { id: 'k1', category: 'workload', tool: 'k6' },
  ];
  const model = { per_task_results: [
    { task_id: 'g1', category: 'analysis', passed: true },
    { task_id: 'g1', category: 'analysis', passed: false },
    { task_id: 'g2', category: 'analysis', passed: false },
    { task_id: 'j1', category: 'scripting', tool: 'jmeter', passed: true },
    { task_id: 'k1', category: 'workload', tool: 'k6', score: 0.5 },
  ] };
  const general = replayToolStats(model, tasks, 'none');
  assert.deepEqual([general.pass, general.partial, general.fail, general.notrun, general.total], [0, 1, 1, 0, 2]);
  assert.deepEqual(general.cats, { analysis: { pass: 0, total: 2 } });
  assert.deepEqual(replayToolStats(model, [], 'none'), general);
  const gated = replayToolStats(model, tasks, 'none', new Set([0, 2, 3, 4]));
  assert.deepEqual([gated.pass, gated.partial, gated.fail, gated.notrun], [0, 0, 1, 1]);
  const landed = new Set(model.per_task_results.keys());
  assert.equal(replayCellLabel(replayToolStats(model, tasks, 'jmeter', landed)), '1/1 pass');
  const k6 = replayToolStats(model, tasks, 'k6');
  assert.deepEqual([k6.pass, k6.partial, k6.fail], [0, 1, 0]);
  assert.equal(replayCellLabel(replayToolStats(model, tasks, 'locust')), 'No tasks yet');
  assert.equal(replayCellLabel(replayToolStats({ per_task_results: [] }, tasks, 'jmeter')), 'Not run');
});

test('tool descriptions expose counts and category breakdowns without color', () => {
  const tasks = [
    { id: 'g1', category: 'analysis', tool: 'none' },
    { id: 'g2', category: 'analysis', tool: 'none' },
  ];
  const model = { per_task_results: [
    { task_id: 'g1', category: 'analysis', passed: true },
    { task_id: 'g1', category: 'analysis', passed: false },
    { task_id: 'g2', category: 'analysis', passed: false },
  ] };
  assert.equal(replayToolDescription(model, tasks, 'none'),
    'General 0/2 pass: 0 pass, 1 partial, 1 fail, 0 not run. Analysis 0/2');
});

test('published replay board collates every model into General, JMeter and k6 cells', () => {
  const tools = replayTools(published.models);
  assert.deepEqual(tools, ['none', 'jmeter', 'k6']);
  for (const model of published.models) {
    const stats = tools.map(tool => replayToolStats(model, [], tool));
    assert.deepEqual(stats.map(stat => stat.total), [9, 3, 3]);
    assert.ok(stats.every(stat => stat.notrun === 0));
  }
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
  assert.match(page, /class="wordmark" href="\/" aria-label="P95 Bench, home"/);
  assert.doesNotMatch(client, /querySelector\("\.wordmark"\)\.onclick/);
  assert.match(client, /if \(initialURL\.searchParams\.has\('pack'\)\)/);
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
