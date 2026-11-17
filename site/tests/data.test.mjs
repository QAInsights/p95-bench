import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { buildData } from '../scripts/build-data.mjs';
import { contributions, bestWithin, toolScore } from '../src/lib/scores.mjs';

const runs = ['deepseek-flash', 'deepseek-v4-pro'].map((m) =>
  new URL(`../../results/published/${m}/`, import.meta.url).pathname);

test('published data is reproducible, redacted and matches weighted scores', async () => {
  const result = await buildData(runs);
  const committed = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
  assert.deepEqual(result, committed);
  assert.equal(result.models.length, 2);
  for (const model of result.models) {
    const sum = contributions(model, result.weights).reduce((n, c) => n + c.points, 0);
    assert.ok(Math.abs(sum - model.overall_score) < 1e-10);
    assert.equal(model.per_task_results.length, 12);
    assert.equal(model.category_scores.repair, undefined);
    assert.equal(model.category_scores.migration, undefined);
    assert.ok(model.run_date);
    assert.match(model.harness_commit, /^[a-f0-9]{40}$/);
    assert.equal(toolScore(model, 'k6'), null);
    assert.ok(Math.abs(toolScore(model, 'jmeter') - 200 / 3) < 1e-10);
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
  assert.equal(bestWithin(models, .02).model, 'deepseek-flash');
  assert.equal(bestWithin(models, .04).model, 'deepseek-v4-pro');
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
