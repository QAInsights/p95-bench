import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtemp, mkdir, readFile, writeFile, rm, readdir } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildData, costBreakdown, loadTaskData, snapshotDigest } from '../scripts/build-data.mjs';

const packId = 'p95-v2.0.0';

test('pack weights are versioned and legacy leaderboard stays identical', async () => {
  const packs = JSON.parse(await readFile(new URL('../../src/p95bench/packs.json', import.meta.url)));
  assert.equal(packs[packId].default_repeats, 1);
  assert.equal(packs[packId].efficiency_weight, .05);
  assert.deepEqual((await loadTaskData({ packId })).weights, packs[packId].weights);
  await assert.rejects(loadTaskData({ packId: 'missing' }), /Unknown pack/);
  const base = new URL('../../results/published/', import.meta.url);
  const runs = (await readdir(base)).map(name => fileURLToPath(new URL(name + '/', base)));
  const saved = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
  assert.deepEqual(await buildData(runs), saved);
});

test('v2 data preserves points/repeats and rejects mixed, incomplete or private inputs', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'p95-pack-'));
  try {
    const taskFolder = path.join(directory, 'tasks', 'repair');
    await mkdir(taskFolder, { recursive: true });
    await writeFile(path.join(taskFolder, 'sample.yaml'),
      'id: sample\ncategory: repair\ntool: k6\nprompt: Synthetic\ngrader: test_checkpoint\n');
    const options = { packId, tasksRoot: path.join(directory, 'tasks') };
    const summary = { pack_id: packId, pack_digest: 'fixture-digest', complete: true,
      attempts: 2, overall_score: 79, quality_score: 80, efficiency_score: 60,
      category_scores: { repair: 80 }, repeats: 2, estimated_cost_usd: .02,
      input_tokens: 200, output_tokens: 40, latency_seconds: 4 };
    const metadata = { pack_id: packId, pack_digest: summary.pack_digest, visibility: 'public' };
    const manifest = { pack_id: packId, digest: summary.pack_digest, visibility: 'public',
      tasks: [{ id: 'sample', category: 'repair' }], repeats: 2 };
    const { digest: _, ...contents } = manifest;
    manifest.digest = snapshotDigest(contents);
    summary.pack_digest = manifest.digest;
    metadata.pack_digest = manifest.digest;
    const attempt = { pack_id: packId, pack_digest: summary.pack_digest, repeat: 0,
      model: 'synthetic', provider: 'test', task_id: 'sample', category: 'repair',
      response: { input_tokens: 100, output_tokens: 20, latency_seconds: 2 }, cost_usd: .01,
      price_per_million: { input: 1, cached: 1, output: 2 },
      grade: { schema_version: 2, score: .8, passed: false, detail: 'Partial' } };
    const save = async (file, value) => writeFile(path.join(directory, file), JSON.stringify(value));
    await save('summary.json', summary);
    await save('metadata.json', metadata);
    await save('pack.json', manifest);
    const graded = [attempt, { ...attempt, repeat: 1 }];
    const saveAttempts = () => writeFile(path.join(directory, 'attempts.jsonl'), graded.map(JSON.stringify).join('\n'));
    await saveAttempts();
    const data = await buildData([directory], options);
    assert.equal(data.pack_id, packId);
    assert.equal(data.models[0].quality_score, 80);
    assert.equal(data.models[0].per_task_results[0].score, .8);
    assert.equal(data.models[0].per_task_results[0].repeat, 0);
    assert.equal(data.models[0].per_task_results[0].output_tokens, 20);
    assert.equal(data.models[0].per_task_results[0].latency_seconds, 2);
    const repairedAttempt = { ...attempt, repair: { triggered: true, reason: 'invalid_model_artifact',
      feedback: 'invalid input', messages_appended: [], response: { input_tokens: 30,
        output_tokens: 15, cached_tokens: 0, latency_seconds: 1 },
      grade: { schema_version: 2, status: 'graded', score: 1, passed: true, detail: 'Fixed' },
      cost_usd: .01 } };
    const repairedSummary = { ...summary, estimated_cost_usd: .03, input_tokens: 230,
      output_tokens: 55, latency_seconds: 5, repairs_triggered: 1, repairs_succeeded: 1,
      after_repair: { overall_score: 95, quality_score: 96, efficiency_score: 76,
        category_scores: { repair: 96 } } };
    await save('metadata.json', { ...metadata, repair_rounds: 1, reasoning_mode: 'thinking' });
    await save('summary.json', repairedSummary);
    await writeFile(path.join(directory, 'attempts.jsonl'),
      [repairedAttempt, { ...attempt, repeat: 1 }].map(JSON.stringify).join('\n'));
    const repairedData = await buildData([directory], options);
    const repairedModel = repairedData.models[0];
    assert.deepEqual(repairedModel.after_repair, repairedSummary.after_repair);
    assert.equal(repairedModel.repair_rounds, 1);
    assert.equal(repairedModel.reasoning_mode, 'thinking');
    assert.equal(repairedModel.per_task_results[0].after_repair_score, 1);
    assert.equal(repairedModel.per_task_results[1].after_repair_score, .8);
    assert.equal(repairedModel.cost_attempts.length, 3);
    assert.equal(repairedModel.cost_attempts.find(item => item.status === 'repair').cost_usd, .01);
    const notRun = { ...attempt, repeat: 1, status: 'not_run', cost_usd: .02,
      grade: { status: 'not_run' } };
    const partial = { ...summary, attempts: 1, not_run: 1, complete: false, estimated_cost_usd: .03 };
    await save('attempts.jsonl', attempt);
    await save('not-run.jsonl', notRun);
    await save('summary.json', partial);
    const billed = costBreakdown([attempt], [notRun], { packId,
      taskMap: new Map([['sample', { category: 'repair' }]]), total: partial.estimated_cost_usd });
    assert.notEqual(attempt.repeat, notRun.repeat);
    assert.equal(billed.length, 2);
    assert.equal(billed[1].status, 'not_run');
    assert.equal(billed[1].cost_usd, .02);
    assert.equal(billed.reduce((sum, a) => sum + a.cost_usd, 0), partial.estimated_cost_usd);
    await assert.rejects(buildData([directory], options), /Incomplete/);
    await rm(path.join(directory, 'not-run.jsonl'));
    await save('summary.json', summary);
    await saveAttempts();
    await save('pack.json', { ...manifest, repeats: 3 });
    await assert.rejects(buildData([directory], options), /digest/);
    await save('pack.json', manifest);
    await writeFile(path.join(taskFolder, 'missing.yaml'),
      'id: missing\ncategory: repair\ntool: k6\nprompt: Synthetic\ngrader: test_checkpoint\n');
    await assert.rejects(buildData([directory], options), /inventory/);
    await rm(path.join(taskFolder, 'missing.yaml'));
    await assert.rejects(buildData([directory]), /Mixed pack/);
    await save('summary.json', { ...summary, complete: false });
    await assert.rejects(buildData([directory], options), /Incomplete/);
    await save('summary.json', summary);
    await save('pack.json', { ...manifest, visibility: 'private' });
    await assert.rejects(buildData([directory], options), /Private runs/);
    await save('pack.json', manifest);
    await save('metadata.json', { ...metadata, visibility: 'private' });
    await assert.rejects(buildData([directory], options), /Private runs/);
    await save('metadata.json', metadata);
    await save('attempts.jsonl', { ...attempt, pack_digest: 'different' });
    await assert.rejects(buildData([directory], options), /mismatched/);
  } finally {
    await rm(directory, { recursive: true });
  }
});

test('Matrix scenario identity survives into grouped task results', async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'p95-matrix-scenario-'));
  try {
    const taskId = 'mx-c02-html-csrf-k6';
    const scenario = 'mx-c02-html-csrf';
    const taskFolder = path.join(directory, 'tasks', 'correlation');
    await mkdir(taskFolder, { recursive: true });
    await writeFile(path.join(taskFolder, 'sample.yaml'),
      `id: ${taskId}\nscenario: ${scenario}\ncategory: correlation\ntool: k6\nprompt: Synthetic\ngrader: v2_exec\n`);
    const options = { packId: 'p95-matrix', tasksRoot: path.join(directory, 'tasks') };
    const { weights } = await loadTaskData(options);
    const attempt = { pack_id: options.packId, repeat: 0, model: 'synthetic', provider: 'test',
      task_id: taskId, scenario, category: 'correlation',
      response: { input_tokens: 100, output_tokens: 20, latency_seconds: 2 }, cost_usd: .01,
      price_per_million: { input: 1, cached: 1, output: 2 },
      grade: { schema_version: 2, score: 1, passed: true, detail: 'Complete' } };
    const manifest = { pack_id: options.packId, grade_schema_version: 2, default_repeats: 1,
      efficiency_weight: .05, weights, visibility: 'public',
      tasks: [{ id: taskId, category: 'correlation', scenario, task_digest: 'fixture' }],
      repeats: 1, efficiency_budgets: { [taskId]: { tokens: 1000, latency_seconds: 10 } } };
    manifest.digest = snapshotDigest(manifest);
    attempt.pack_digest = manifest.digest;
    const summary = { pack_id: options.packId, pack_digest: manifest.digest, complete: true,
      attempts: 1, overall_score: 100, quality_score: 100, efficiency_score: 100,
      category_scores: { correlation: 100 }, repeats: 1, estimated_cost_usd: .01,
      input_tokens: 100, output_tokens: 20, latency_seconds: 2 };
    const metadata = { pack_id: options.packId, pack_digest: manifest.digest, visibility: 'public',
      task_scenarios: { [taskId]: scenario } };
    await writeFile(path.join(directory, 'pack.json'), JSON.stringify(manifest));
    await writeFile(path.join(directory, 'summary.json'), JSON.stringify(summary));
    await writeFile(path.join(directory, 'metadata.json'), JSON.stringify(metadata));
    await writeFile(path.join(directory, 'attempts.jsonl'), JSON.stringify(attempt));

    const data = await buildData([directory], options);
    assert.equal(data.models[0].per_task_results[0].scenario, scenario);
  } finally {
    await rm(directory, { recursive: true });
  }
});
