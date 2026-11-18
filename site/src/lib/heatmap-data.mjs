import { name, selectModelVersions, attemptFraction } from './scores.mjs';
import { providerName } from './provider-names.mjs';
import { creditGradient } from './packs.mjs';

export const heatMetrics = ['result', 'tokens', 'latency'];
export const heatTools = [['none', 'General'], ['jmeter', 'JMeter'], ['k6', 'k6']];
export const flameScale = ['#2a1a4a', '#6b2a7a', '#c4416a', '#ff7a3d', '#ffc94a'];
const mean = values => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
const valid = value => Number.isFinite(value) && value >= 0;

export function heatmapData(models, tasks, attempts) {
  const columns = heatTools.flatMap(([tool]) => tasks.filter(task => (task.tool || 'none') === tool)
    .map(task => ({ id: task.id, tool })));
  const grouped = new Map();
  for (const attempt of attempts) {
    const key = JSON.stringify([attempt.provider, attempt.model, attempt.task_id]);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(attempt);
  }
  const rows = selectModelVersions(models, true).map(model => ({
    id: model.model, model: name(model.model), provider: providerName(model.provider), latest: model.newer_model === null,
    cells: columns.map(task => {
      const records = grouped.get(JSON.stringify([model.provider, model.model, task.id])) ?? [];
      const measured = records.filter(record => attemptFraction(record.grade) !== null);
      return { task: task.id, attempts: records.length, measured: measured.length,
        result: mean(measured.map(record => attemptFraction(record.grade))),
        ...(model.pack_id === 'p95-v2.0.0' ? { partial_credit: true } : {}),
        tokens: mean(measured.map(record => record.response?.output_tokens).filter(valid)),
        latency: mean(measured.map(record => record.response?.latency_seconds).filter(valid)) };
    }),
  }));
  return { columns, rows };
}

export function metricExtent(rows, metric) {
  if (!heatMetrics.includes(metric)) throw new RangeError(`Unknown heatmap metric: ${metric}`);
  const values = rows.flatMap(row => row.cells.map(cell => cell[metric])).filter(valid);
  return values.length ? { min: Math.min(...values), max: Math.max(...values) } : { min: null, max: null };
}

export function heatColor(value, metric, extent, partial = false) {
  if (!heatMetrics.includes(metric)) throw new RangeError(`Unknown heatmap metric: ${metric}`);
  if (!valid(value)) return null;
  if (partial && metric === 'result' && value > 0 && value < 1) return creditGradient(value);
  if (metric === 'result') return value === 1 ? '#d4ff3a' : value === 0 ? '#c4416a'
    : `linear-gradient(to right, #d4ff3a ${value * 100}%, #c4416a ${value * 100}%)`;
  const ratio = extent.max === extent.min ? .5 : Math.max(0, Math.min(1, (value - extent.min) / (extent.max - extent.min)));
  return flameScale[Math.min(flameScale.length - 1, Math.floor(ratio * flameScale.length))];
}

export function heatValue(cell, metric) {
  if (!heatMetrics.includes(metric)) throw new RangeError(`Unknown heatmap metric: ${metric}`);
  const value = cell[metric];
  if (!valid(value)) return metric === 'result' ? 'Not run' : 'No measurement';
  return metric === 'result' ? value === 1 ? 'Pass' : value === 0 ? 'Fail' : `${(100 * value).toFixed(1)}% ${cell.partial_credit ? 'credit' : 'pass'}`
    : metric === 'tokens' ? `${Number(value.toFixed(1)).toLocaleString('en-US')} tokens` : `${value.toFixed(2)} s`;
}
