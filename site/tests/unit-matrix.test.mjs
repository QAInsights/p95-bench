import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { selectModelVersions } from '../src/lib/scores.mjs';
import { unitRecords, unitGroups, unitSummary, matrixModes, unitDescription, passRateLabel } from '../src/lib/unit-matrix-data.mjs';

const data = JSON.parse(await readFile(new URL('../src/data/leaderboard.json', import.meta.url)));
const tasks = [{ id: 'a', category: 'analysis', tool: 'none' }, { id: 'b', category: 'authoring', tool: 'k6' }];
const model = { model: 'test', provider: 'provider', overall_score: 50, cost: 1,
  per_task_results: [{ task_id: 'a', category: 'analysis', passed: true },
    { task_id: 'a', category: 'analysis', passed: false }] };

test('matrix preserves every repeated attempt and adds only genuinely missing task slots', () => {
  const records = unitRecords([model], tasks);
  assert.equal(records.length, 3);
  assert.equal(new Set(records.map(record => record.id)).size, 3);
  assert.deepEqual(records.map(record => record.result), ['pass', 'fail', 'not_run']);
  assert.equal(records[2].task, 'b');
  assert.deepEqual(unitSummary(records), { count: 3, passed: 1, failed: 1, notRun: 1, measured: 2, passRate: 50 });
  assert.match(unitDescription(records[0]), /test · a · General · Pass/);
  assert.match(unitDescription(records[2]), /k6 · Not run/);
});

test('every grouping conserves published counts and pass-rate denominators', () => {
  const all = unitRecords(data.models);
  for (const earlier of [false, true]) {
    const visible = all.filter(record => earlier || record.latest);
    const models = selectModelVersions(data.models, earlier);
    assert.equal(visible.length, models.reduce((sum, model) => sum + model.per_task_results.length, 0));
    for (const mode of matrixModes) {
      const groups = unitGroups(visible, mode);
      assert.equal(groups.reduce((sum, group) => sum + group.count, 0), visible.length);
      assert.equal(groups.reduce((sum, group) => sum + group.passed, 0), unitSummary(visible).passed);
      assert.equal(new Set(groups.flatMap(group => group.records.map(record => record.id))).size, visible.length);
      for (const group of groups) assert.equal(group.passRate, 100 * group.passed / group.measured);
    }
  }
});

test('tool and result groups have meaningful labels and stable ordering', () => {
  const records = unitRecords([model], tasks);
  assert.deepEqual(unitGroups(records, 'tool').map(group => group.label), ['General', 'k6']);
  assert.deepEqual(unitGroups(records, 'result').map(group => group.label), ['Pass', 'Fail', 'Not run']);
  assert.equal(unitGroups(records, 'result')[2].passRate, null);
  const unusual = [{ ...records[0], provider: '__proto__', tool: 'constructor' }];
  assert.equal(unitGroups(unusual, 'provider')[0].key, '__proto__');
  assert.equal(unitGroups(unusual, 'tool')[0].label, 'constructor');
});

test('empty and invalid groupings do not invent observations', () => {
  assert.deepEqual(unitRecords([], tasks), []);
  for (const mode of matrixModes) assert.deepEqual(unitGroups([], mode), []);
  assert.equal(unitSummary([]).passRate, null);
  assert.equal(passRateLabel(null), '— pass (unmeasured)');
  assert.equal(passRateLabel(2 / 3 * 100), '66.7% pass');
  assert.throws(() => unitGroups([], 'unknown'), /Unknown matrix grouping/);
});

test('matrix is build-time markup with native controls, a table alternative, and no individual tab stops', async () => {
  const page = await readFile(new URL('../src/pages/index.astro', import.meta.url), 'utf8');
  const component = await readFile(new URL('../src/components/UnitMatrix.astro', import.meta.url), 'utf8');
  assert.ok(page.indexOf('<UnitMatrix models=') > page.indexOf('id="scene-1"'));
  assert.ok(page.indexOf('<UnitMatrix models=') < page.indexOf('id="scene-2"'));
  assert.match(component, /aria-pressed=/);
  assert.match(component, /tabindex="-1" data-unit-id/);
  assert.match(component, /Show data table/);
  assert.match(component, /Source: published attempts, n =/);
  assert.doesNotMatch(component, /\stitle=/);
});
