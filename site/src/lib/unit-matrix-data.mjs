import { name, selectModelVersions, attemptFraction } from './scores.mjs';
import { providerName } from './provider-names.mjs';

export const matrixModes = ['all', 'tool', 'category', 'provider', 'result'];
export const resultLabels = { pass: 'Pass', fail: 'Fail', partial: 'Partial credit', not_run: 'Not run' };
const toolLabels = new Map([['none', 'General'], ['jmeter', 'JMeter'], ['k6', 'k6']]);
export const toolLabel = tool => toolLabels.get(tool) ?? tool;
const label = value => value.replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());

export function unitRecords(models, tasks = []) {
  return selectModelVersions(models, true).flatMap(model => {
    const seen = new Set();
    const record = (attempt, key) => ({ id: `${model.model}:${key}`, model_id: model.model,
      model: name(model.model), provider: model.provider, task: attempt.task_id,
      category: attempt.category, tool: attempt.tool || 'none', latest: model.newer_model === null,
      ...(Number.isFinite(attempt.score) ? { score: attempt.score } : {}),
      result: attemptFraction(attempt) === null ? 'not_run' : attemptFraction(attempt) === 1 ? 'pass'
        : attemptFraction(attempt) === 0 ? 'fail' : 'partial' });
    const records = model.per_task_results.map((attempt, index) => {
      seen.add(attempt.task_id);
      return record(attempt, index);
    });
    for (const task of tasks) {
      if (!seen.has(task.id)) records.push(record({ task_id: task.id, category: task.category,
        tool: task.tool, passed: null }, `missing:${task.id}`));
    }
    return records;
  });
}

export function unitSummary(records) {
  const passed = records.filter(record => record.result === 'pass').length;
  const failed = records.filter(record => record.result === 'fail').length;
  const partial = records.filter(record => record.result === 'partial').length;
  const measured = passed + failed + partial;
  return { count: records.length, passed, failed, notRun: records.length - measured,
    ...(partial ? { partial } : {}),
    measured, passRate: measured ? 100 * passed / measured : null };
}

export const passRateLabel = rate => rate === null ? '— pass (unmeasured)' : `${Number(rate.toFixed(1))}% pass`;

export function unitGroups(records, mode = 'all') {
  if (!matrixModes.includes(mode)) throw new RangeError(`Unknown matrix grouping: ${mode}`);
  const groups = new Map();
  for (const record of records) {
    const key = mode === 'all' ? 'all' : record[mode];
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(record);
  }
  const order = mode === 'result' ? ['pass', 'partial', 'fail', 'not_run'] : mode === 'tool' ? ['none', 'jmeter', 'k6'] : [];
  return [...groups].sort(([a], [b]) => {
    const ai = order.indexOf(a), bi = order.indexOf(b);
    return (ai < 0 ? order.length : ai) - (bi < 0 ? order.length : bi) || a.localeCompare(b);
  }).map(([key, items]) => ({ key, records: items, ...unitSummary(items),
    label: mode === 'all' ? 'All attempts' : mode === 'tool' ? toolLabel(key)
      : mode === 'result' ? resultLabels[key] : mode === 'provider' ? providerName(key) : label(key) }));
}

export const unitDescription = record =>
  `${record.model} · ${record.task} · ${toolLabel(record.tool)} · ${Number.isFinite(record.score) ? `${(record.score * 100).toFixed(1)}% credit` : resultLabels[record.result]}`;
