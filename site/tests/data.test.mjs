import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { buildData } from '../scripts/build-data.mjs';
import { contributions, bestWithin, toolScore, toolScoreLabel, taskResult, costFrontier, modelSlug } from '../src/lib/scores.mjs';

const runs = ['deepseek-flash', 'deepseek-v4-pro', 'claude-sonnet-5-5', 'claude-opus-5-5',
  'inkling', 'inkling-small', 'inkling-2-small', 'gpt-6-2-sol', 'gpt-6-2-astra'].map((m) =>
  new URL(`../../results/published/${m}/`, import.meta.url).pathname);

test('site weights come from the same packaged JSON as the Python scorer', async () => {
  const shared = JSON.parse(await readFile(new URL('../../src/p95bench/category-weights.json', import.meta.url)));
  assert.deepEqual((await buildData(runs)).weights, shared);
  assert.equal(Object.values(shared).reduce((sum, value) => sum + value, 0), 100);
  const generator = await readFile(new URL('../scripts/build-data.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(generator, /harness\.match|tasks\.py/);
});

test('tool card labels distinguish unrun tools from failed and passing runs', () => {
  assert.equal(toolScoreLabel({ per_task_results: [] }, 'jmeter'), 'Not run');
  assert.equal(toolScoreLabel({ per_task_results: [{ tool: 'none', passed: true }] }, 'jmeter'), 'Not run');
  assert.equal(toolScoreLabel({ per_task_results: [{ tool: 'jmeter', passed: false }] }, 'jmeter'), '0.0');
  assert.equal(toolScoreLabel({ per_task_results: [{ tool: 'jmeter', passed: true }] }, 'jmeter'), '100.0');
  assert.equal(toolScoreLabel({ per_task_results: [
    { tool: 'jmeter', passed: true }, { tool: 'jmeter', passed: false },
  ] }, 'jmeter'), '50.0');
});

test('task matrix includes every repeat and fails a cell if any attempt failed', () => {
  const attempts = [
    { task_id: 'flow', passed: true, detail: 'Verified checkout' },
    { task_id: 'other', passed: false, detail: 'Unrelated failure' },
    { task_id: 'flow', passed: false, detail: 'Failed samples' },
    { task_id: 'flow', passed: true, detail: 'Verified again' },
  ];
  assert.deepEqual(taskResult({ per_task_results: attempts }, 'flow'), {
    passed: false,
    label: '2/3 pass',
    detail: 'Attempt 1: Pass · Verified checkout\nAttempt 2: Fail · Failed samples\nAttempt 3: Pass · Verified again',
  });
  assert.equal(taskResult({ per_task_results: [] }, 'flow'), null);
  assert.equal(taskResult({ per_task_results: attempts }, 'missing'), null);
  assert.deepEqual(taskResult({ per_task_results: [attempts[0]] }, 'flow'), {
    passed: true, label: 'Pass', detail: 'Verified checkout',
  });
  assert.deepEqual(taskResult({ per_task_results: [attempts[2]] }, 'flow'), {
    passed: false, label: 'Fail', detail: 'Failed samples',
  });
  const allPass = taskResult({ per_task_results: [attempts[0], attempts[3]] }, 'flow');
  assert.equal(allPass.passed, true);
  assert.equal(allPass.label, '2/2 pass');
  const allFail = taskResult({ per_task_results: [attempts[2], attempts[2]] }, 'flow');
  assert.equal(allFail.passed, false);
  assert.equal(allFail.label, '0/2 pass');
});

test('published data is reproducible, redacted and matches weighted scores', async () => {
  const result = await buildData(runs);
  const committed = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
  assert.deepEqual(result, committed);
  assert.deepEqual(await buildData(runs.toReversed()), result);
  assert.equal(result.models.length, 9);
  for (const model of result.models) {
    const sum = contributions(model, result.weights).reduce((n, c) => n + c.points, 0);
    assert.ok(Math.abs(sum - model.overall_score) < 1e-10);
    const metadata = JSON.parse(await readFile(path.join(runs.find(directory =>
      directory.endsWith('/' + modelSlug(model.model) + '/')), 'metadata.json')));
    assert.equal(metadata.k6_version, '1.0.0');
    assert.ok(metadata.k6_run_date);
    assert.match(metadata.k6_note, /separately/);
    assert.equal(model.per_task_results.length, metadata.k6_status === 'completed' ? 15 : 12);
    assert.equal(model.coverage.run, new Set(model.per_task_results.map(task => task.task_id)).size);
    assert.equal(model.coverage.partial, model.coverage.run < model.coverage.total);
    assert.equal(model.category_scores.repair, undefined);
    assert.equal(model.category_scores.migration, undefined);
    assert.ok(model.run_date);
    assert.match(model.harness_commit, /^[a-f0-9]{40}$/);
    const k6 = model.per_task_results.filter(t => t.tool === 'k6');
    assert.equal(k6.length, metadata.k6_status === 'completed' ? 3 : 0);
    assert.equal(toolScore(model, 'k6'), k6.length
      ? 100 * k6.filter(t => t.passed).length / k6.length : null);
    if (!k6.length) assert.equal(toolScoreLabel(model, 'k6'), 'Not run');
    const expectedToolScore = model.provider === 'thinkingmachines' ? 0
      : model.provider === 'deepseek' ? 200 / 3 : 100;
    assert.ok(Math.abs(toolScore(model, 'jmeter') - expectedToolScore) < 1e-10);
    for (const task of model.per_task_results) {
      assert.deepEqual(Object.keys(task), ['task_id', 'category', 'tool', 'passed', 'detail']);
    }
  }
  assert.doesNotMatch(JSON.stringify(result), /"(?:messages|prompt|response|text)":/);
});

test('budget picker covers no budget, Flash and Pro thresholds', async () => {
  const { models } = await buildData(runs);
  assert.equal(bestWithin(models, 0), null);
  assert.equal(bestWithin([], 1), null);
  assert.equal(bestWithin(models, models.find(m => m.model === 'deepseek-flash').cost).model, 'deepseek-flash');
  assert.equal(bestWithin(models, models.find(m => m.model === 'deepseek-v4-pro').cost).model, 'deepseek-v4-pro');
  assert.equal(bestWithin(models, 1).model, 'gpt-6.2-sol');
});

test('frontier excludes dominated runs and handles empty data and equal costs', async () => {
  const { models } = await buildData(runs);
  assert.deepEqual(costFrontier(models).map((m) => m.model),
    ['deepseek-flash', 'deepseek-v4-pro', 'gpt-6.2-sol']);
  assert.deepEqual(costFrontier([]), []);
  const best = { cost: 1, overall_score: 90 };
  assert.deepEqual(costFrontier([{ cost: 1, overall_score: 80 }, best,
    { cost: 2, overall_score: 90 }]), [best]);
});

test('Anthropic run metadata records the temperature exception', async () => {
  for (const directory of runs.slice(2, 4)) {
    const metadata = JSON.parse(await readFile(path.join(directory, 'metadata.json')));
    assert.equal(metadata.temperature, null);
    assert.match(metadata.temperature_note, /deprecated/);
    assert.equal(metadata.max_tokens, 8192);
  }
});

test('namespaced models resolve to published directories and preserve API identity', async () => {
  const { models } = await buildData(runs);
  for (const model of models) {
    const directory = new URL(`../../results/published/${modelSlug(model.model)}/`, import.meta.url);
    const first = JSON.parse((await readFile(new URL('attempts.jsonl', directory), 'utf8'))
      .split('\n')[0]);
    assert.equal(first.model, model.model);
    if (model.provider === 'openai') {
      const metadata = JSON.parse(await readFile(new URL('metadata.json', directory)));
      assert.equal(metadata.temperature, null);
      assert.match(metadata.temperature_note, /default temperature/);
      assert.equal(metadata.max_completion_tokens, 8192);
      assert.match(metadata.output_cap_note, /rejects max_tokens/);
    }
  }
});

test('generator rejects empty, duplicate and mismatched runs', async () => {
  await assert.rejects(buildData([]), /at least one/);
  await assert.rejects(buildData([runs[0], runs[0]]), /Duplicate/);
  const directory = await mkdtemp(path.join(os.tmpdir(), 'p95-site-test-'));
  try {
    await writeFile(path.join(directory, 'summary.json'), '{"attempts":1}');
    await writeFile(path.join(directory, 'attempts.jsonl'), '');
    await assert.rejects(buildData([directory]), /Empty/);
    await writeFile(path.join(directory, 'attempts.jsonl'),
      '{"model":"a","provider":"b"}\n{"model":"c","provider":"b"}\n');
    await assert.rejects(buildData([directory]), /Mixed/);
    await writeFile(path.join(directory, 'attempts.jsonl'), '{"model":"a","provider":"b"}\n');
    await writeFile(path.join(directory, 'summary.json'), '{"attempts":2}');
    await assert.rejects(buildData([directory]), /count mismatch/);
  } finally {
    await rm(directory, { recursive: true });
  }
});
