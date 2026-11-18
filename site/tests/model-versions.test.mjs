import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { selectModelVersions } from '../src/lib/scores.mjs';

test('current registry shows the latest published model in every family and restores earlier ranks', async () => {
  const data = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
  const registry = JSON.parse(await readFile(new URL('../src/data/model-registry.json', import.meta.url)));
  const latest = selectModelVersions(data.models), all = selectModelVersions(data.models, true);
  assert.equal(latest.length, new Set(data.models.map(m => registry[m.model]?.family ?? m.model)).size);
  assert.equal(all.length, data.models.length);
  assert.ok(!latest.some(m => m.model === 'thinkingmachines/Inkling-Small'));
  assert.equal(all.find(m => m.model === 'thinkingmachines/Inkling-Small').newer_model, 'thinkingmachines/Inkling-2-Small');
  assert.deepEqual(all.map(m => m.model), data.models.map(m => m.model));
  assert.deepEqual(latest.map(m => m.model), all.filter(m => !m.newer_model).map(m => m.model));
  assert.ok(latest.every(m => m.version_status === 'Latest'));
  assert.ok(data.models.every(m => !Object.hasOwn(m, 'version_status')));
});

test('Qwen Max groups the earlier 3.7 release under 3.8 and Flash stays independent', () => {
  const models = [
    { model: 'Qwen-Ambassador/Qwen3.7-Max', overall_score: 100, cost: 1 },
    { model: 'Qwen-Ambassador/Qwen3.8-Max', overall_score: 90, cost: 1 },
    { model: 'Qwen-Ambassador/Qwen3.8-Flash-Next', overall_score: 80, cost: 1 },
  ];
  assert.deepEqual(selectModelVersions(models).map(m => m.model), models.slice(1).map(m => m.model));
  assert.equal(selectModelVersions(models, true)[0].newer_model, models[1].model);
});

test('Gemini Carbon, Argon, Hydrogen and Flash have independent latest families', () => {
  const models = ['gemini-4-carbon', 'gemini-4-argon', 'gemini-4-hydrogen', 'gemini-3.8-flash']
    .map(model => ({ model, overall_score: 100, cost: 1 }));
  const latest = selectModelVersions(models);
  assert.equal(latest.length, 4);
  assert.equal(new Set(latest.map(model => model.family)).size, 4);
  assert.ok(latest.every(model => model.version_status === 'Latest'));
});

test('release order, not score, chooses latest; earlier and discontinued runs stay fully ranked when included', () => {
  const models = [{ model: 'old', overall_score: 100, cost: 1 }, { model: 'new', overall_score: 50, cost: 1 }];
  const registry = { old: { family: 'a', release_order: 1 }, new: { family: 'a', release_order: 2, discontinued: true } };
  const latest = selectModelVersions(models, false, registry);
  assert.deepEqual(latest.map(m => m.model), ['new']);
  assert.equal(latest[0].discontinued, true);
  const all = selectModelVersions(models, true, registry);
  assert.equal(all[0].model, 'old');
  assert.equal(all[0].version_status, 'Earlier version');
  assert.equal(all[0].newer_model, 'new');
  assert.equal(all[0].overall_score, 100);
});

test('unknown models get independent latest families and unbenchmarked releases do not hide published results', () => {
  const registry = { old: { family: 'unknown', release_order: 1 }, absent: { family: 'unknown', release_order: 2 } };
  const models = ['old', 'unknown', 'brand-new'].map(model => ({ model, overall_score: 50, cost: 1 }));
  const selected = selectModelVersions(models, false, registry);
  assert.equal(selected.length, 3);
  assert.ok(selected.every(m => m.version_status === 'Latest' && !m.discontinued));
  assert.equal(selected.find(m => m.model === 'brand-new').family, 'brand-new');
  assert.deepEqual(selectModelVersions([], false, registry), []);
});
