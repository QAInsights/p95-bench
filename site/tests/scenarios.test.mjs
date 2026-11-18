import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { test } from 'node:test';
import { loadTaskData } from '../scripts/build-data.mjs';
import { buildScenarios, scenarioFilters, scenarioGrading } from '../src/lib/scenarios.mjs';

const published = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));

test('scenario catalog loads every YAML at build time with summaries, grading and latest-model pass rates', async () => {
  const { tasks } = await loadTaskData();
  const scenarios = buildScenarios(tasks, published.models);
  const files = await readdir(new URL('../../tasks/', import.meta.url), { recursive: true });
  assert.equal(scenarios.length, files.filter(file => file.endsWith('.yaml')).length);
  assert.equal(new Set(scenarios.map(scenario => scenario.id)).size, tasks.length);
  for (const scenario of scenarios) {
    const task = tasks.find(task => task.id === scenario.id);
    assert.equal(scenario.summary, task.summary);
    assert.ok(scenario.summary.length > 40);
    assert.doesNotMatch(scenario.summary + scenario.grading, /—|undefined/);
    assert.equal(scenario.category, task.category);
    assert.equal(scenario.tool, task.tool);
    assert.equal(scenario.total, 8);
    assert.equal(scenario.attempted + scenario.notRun, 8);
    assert.ok(scenario.passed <= scenario.attempted);
  }
  const refresh = scenarios.find(scenario => scenario.id === 'k6-token-refresh');
  assert.equal(refresh.passLabel, '6/8 models pass');
  assert.equal(refresh.attempted, 7);
  assert.equal(refresh.notRun, 1);
  assert.deepEqual(scenarioFilters(scenarios), [
    { tool: 'all', label: 'All' }, { tool: 'none', label: 'General' },
    { tool: 'jmeter', label: 'JMeter' }, { tool: 'k6', label: 'k6' },
  ]);
});

test('one vote per latest model, all repeated attempts must pass, and missing attempts stay distinct', () => {
  const tasks = [
    { id: 'mean', category: 'analysis', grader: 'numeric', params: { answer: 10 }, prompt: 'Compute the mean.' },
    { id: 'flow', title: 'Shop flow', category: 'authoring', tool: 'k6', grader: 'k6_exec', summary: 'Complete a checkout.' },
    { id: 'future', category: 'migration', tool: 'vegeta', grader: 'future_exec', summary: 'Run a future tool.' },
  ];
  const models = [
    { model: 'thinkingmachines/Inkling-Small', overall_score: 100, cost: 1, per_task_results: [
      { task_id: 'mean', passed: true }, { task_id: 'flow', passed: true },
    ] },
    { model: 'thinkingmachines/Inkling-2-Small', overall_score: 90, cost: 1, per_task_results: [
      { task_id: 'mean', passed: true }, { task_id: 'mean', passed: false },
    ] },
    { model: 'unknown/latest', overall_score: 80, cost: 2, per_task_results: [
      { task_id: 'mean', passed: true }, { task_id: 'flow', passed: true }, { task_id: 'flow', passed: true },
    ] },
    { model: 'empty', overall_score: 0, cost: 0, per_task_results: [] },
  ];
  const original = JSON.stringify({ tasks, models });
  const [mean, flow, future] = buildScenarios(tasks.toReversed(), models);
  assert.equal(mean.summary, 'Compute the mean.');
  assert.equal(mean.toolLabel, 'General');
  assert.equal(mean.passLabel, '1/3 models pass');
  assert.equal(mean.attempted, 2);
  assert.equal(mean.notRun, 1);
  assert.equal(flow.title, 'Shop flow');
  assert.equal(flow.passLabel, '1/3 models pass');
  assert.equal(flow.attempted, 1);
  assert.equal(flow.notRun, 2);
  assert.equal(future.toolLabel, 'vegeta');
  assert.equal(future.passLabel, '0/3 models pass');
  assert.equal(future.notRun, 3);
  assert.match(future.grading, /Custom grader: future_exec/);
  assert.equal(JSON.stringify({ tasks, models }), original);
});

test('empty catalogs and no published models have safe descriptions and filters', () => {
  assert.deepEqual(buildScenarios([], []), []);
  assert.deepEqual(scenarioFilters([]), [{ tool: 'all', label: 'All' }]);
  const [scenario] = buildScenarios([{ id: 'new', category: 'analysis', grader: 'numeric',
    params: { answer: 1 }, prompt: 'Compute a value.' }], []);
  assert.equal(scenario.passLabel, 'No published latest models');
  assert.equal(scenario.total, 0);
  assert.equal(scenario.notRun, 0);
});

test('future tool filters appear automatically in stable order without duplicate buttons', () => {
  const scenarios = ['zebra', 'k6', 'locust', 'none', 'gatling', 'jmeter', 'alpha', 'k6']
    .map(tool => ({ tool, toolLabel: tool }));
  assert.deepEqual(scenarioFilters(scenarios).map(filter => filter.tool),
    ['all', 'none', 'jmeter', 'k6', 'gatling', 'locust', 'alpha', 'zebra']);
});

test('grading descriptions follow numeric and choice comparisons, not generic correctness claims', () => {
  assert.equal(scenarioGrading({ grader: 'numeric', params: { answer: 250, tolerance: .001 } }),
    'Numeric answer: 250, with absolute tolerance 0.001. Reply with only the bare finite number; markdown or prose fails.');
  assert.match(scenarioGrading({ grader: 'numeric', params: { answer: 1 } }), /absolute tolerance 0/);
  assert.equal(scenarioGrading({ grader: 'choice', params: { answer: 'C' } }),
    'Exact choice: C. Reply with only the bare letter; markdown or prose fails. Letter case and surrounding whitespace are ignored.');
  assert.match(scenarioGrading({ grader: 'new_grader' }), /Custom grader: new_grader/);
});

test('execution descriptions expose the actual JMeter and k6 checks, including their shape-check difference', () => {
  const jmeter = scenarioGrading({ grader: 'jmeter_exec', params: { completions: 2, min_checkout_threads: 2, timeout: 45 } });
  assert.match(jmeter, /^Return one complete <jmeterTestPlan> XML block before execution/);
  assert.match(jmeter, /exit code 0, a nonempty sample log and all samples successful/);
  assert.match(jmeter, /Exactly 2 completed checkouts/);
  assert.match(jmeter, /At least 2 distinct checkout threads/);
  assert.match(jmeter, /Thread and iteration counts are not otherwise checked/);
  assert.match(jmeter, /Timeout: 45 seconds/);
  const isolation = scenarioGrading({ grader: 'k6_exec', params: { completions: 2, min_checkout_vus: 2 } });
  assert.match(isolation, /^Return the script in one js\/javascript code block before execution/);
  assert.match(isolation, /expected responses and positive HTTP statuses below 400/);
  assert.match(isolation, /At least 2 distinct checkout VUs/);
  const refresh = scenarioGrading({ grader: 'k6_exec', params: { completions: 2, expected_checkout_vus: 1, min_checkout_iterations: 2 } });
  assert.match(refresh, /Exactly 1 checkout VU\./);
  assert.match(refresh, /At least 2 distinct checkout iterations/);
  assert.match(refresh, /Timeout: 60 seconds/);
  assert.doesNotMatch(refresh, /threads|otherwise checked/);
  assert.match(scenarioGrading({ grader: 'k6_exec' }), /Exactly 1 completed checkout\./);
});

test('scenario content is build-time semantic markup between Model focus and Tools', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const client = await readFile(new URL('../src/lib/fullpage.mjs', import.meta.url), 'utf8');
  assert.ok(page.indexOf('id="scenarios"') > page.indexOf('id="scene-4"'));
  assert.ok(page.indexOf('id="scenarios"') < page.indexOf('id="tools"'));
  assert.match(page, /import\.meta\.glob\("\.\.\/\.\.\/\.\.\/tasks\/\*\*\/\*\.yaml"/);
  assert.match(page, /<ul class="scenario-list"/);
  assert.match(page, /<ul class="scenario-list" id="scenario-list" role="list">/);
  assert.match(page, /data-scenario-filter=/);
  assert.match(page, /aria-pressed=/);
  assert.match(page, /id="status" class="sr" role="status" aria-live="polite"/);
  assert.match(client, /"MODEL FOCUS",\s*"SCENARIOS",\s*"TOOLS",\s*"METHODOLOGY"/);
  assert.match(client, /scenes\.findIndex/);
  assert.match(client, /e\.button !== 0 \|\| e\.ctrlKey \|\| e\.metaKey \|\| e\.shiftKey \|\| e\.altKey/);
  assert.match(client, /history\.pushState\(null, "", a\.hash\)/);
});
