import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  CHAMBER_CAMERAS,
  chamberGeometry,
  chamberModels,
  computePareto,
  formatTaskCost,
  formatTaskLatency,
  project3D,
} from '../src/lib/pareto3d-math.mjs';

const sample = [
  { key: 'a/fast-cheap', name: 'Fast Cheap', providerLabel: 'A', score: 80, quality: 80, cost: 2, latency: 200, taskCount: 100 },
  { key: 'b/high-q', name: 'High Quality', providerLabel: 'B', score: 94, quality: 94, cost: 20, latency: 1200, taskCount: 100 },
  { key: 'c/dominated', name: 'Dominated', providerLabel: 'C', score: 75, quality: 75, cost: 15, latency: 1500, taskCount: 100 },
];

test('chamberModels computes per-task speed and cost cleanly', () => {
  const items = chamberModels(sample);
  assert.equal(items.length, 3);
  assert.equal(items[0].costPerTask, 0.02);
  assert.equal(items[0].latencyPerTask, 2);
});

test('computePareto marks non-dominated frontier models and excludes dominated ones', () => {
  const items = chamberModels(sample);
  assert.deepEqual(computePareto(items), [true, true, false]);
});

test('chamberGeometry handles a single model without NaN or collapse', () => {
  const geo = chamberGeometry(chamberModels([sample[0]]));
  assert.equal(geo.length, 1);
  assert.equal(geo[0].pareto, true);
  assert.ok(Number.isFinite(geo[0].pos.x) && Number.isFinite(geo[0].pos.y) && Number.isFinite(geo[0].pos.z));
});

test('chamberGeometry keeps 3D coordinates fixed across focus modes while updating utility', () => {
  const items = chamberModels(sample);
  const balanced = chamberGeometry(items, 'balanced');
  const speed = chamberGeometry(items, 'speed');
  assert.deepEqual(balanced[0].pos, speed[0].pos);
  assert.ok(balanced[0].pos.x > balanced[1].pos.x, 'faster model sits further right (+X)');
  assert.ok(balanced[0].pos.z > balanced[1].pos.z, 'cheaper model sits further front (+Z)');
  assert.ok(balanced[1].pos.y > balanced[0].pos.y, 'higher quality sits higher (+Y)');
  assert.ok(speed[0].util > speed[1].util, 'speed focus elevates the faster model');
});

test('project3D returns finite screen coordinates for all camera presets', () => {
  for (const cam of CHAMBER_CAMERAS) {
    const p = project3D({ x: 0.4, y: 1.1, z: -0.3 }, cam, 800, 460);
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y) && Number.isFinite(p.z) && p.s > 0);
  }
});

test('formatters handle small costs, latencies, and missing values', () => {
  assert.equal(formatTaskCost(0.008), '$0.008');
  assert.equal(formatTaskCost(0.192), '$0.19');
  assert.equal(formatTaskCost(NaN), '—');
  assert.equal(formatTaskLatency(4.26), '4.3s');
  assert.equal(formatTaskLatency(118.5), '119s');
  assert.equal(formatTaskLatency(null), '—');
});
